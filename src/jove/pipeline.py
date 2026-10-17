"""ingest → ocr → layout → tag → emit a JSONL graph."""

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from . import layout
from .choose import Chooser, is_value
from .ingest import ingest, sha256
from .ocr import Ocr, release
from .schema import build_tree
from .flat import FlatTagger
from .questions import standing


def run(job_id: str, job_dir: Path, files: list[tuple[str, bytes]], schema: dict,
        ocr: Ocr, chooser: Chooser, context: str | None = None, gate: float = 0.5,
        owners: bool = True, layout_engine: str = "rules", paragraphs: bool = True) -> Path:
    tree = build_tree(schema)
    inputs, pages = ingest(files, job_dir / "pages")
    if layout_engine == "docling":
        from . import docling_layout

        parser = docling_layout.DoclingLayout()
        by_id = {p.page_id: p for p in pages}
        blocks = {}
        for (name, data), info in zip(files, inputs):
            own = [by_id[pid] for pid in info.pages]
            regions = parser.regions(name, data, [(p.width, p.height) for p in own])
            for p, regs in zip(own, regions):
                blocks[p.page_id] = docling_layout.build(p.page_id, ocr.read(p), regs)
    else:
        blocks = {p.page_id: layout.build(p.page_id, ocr.read(p)) for p in pages}
    # Every page is read: free the OCR models' GPU memory before the chooser needs it.
    release(ocr)

    tagger = FlatTagger(tree, chooser, context=context, threshold=gate, paragraphs=paragraphs)
    nodes, edges = tagger.tag(blocks)
    warnings = list(tagger.warnings)
    if owners and schema.get("x-owners"):
        from .owners import Owners

        pass_ = Owners(schema, tree, chooser, standing(tree, context))
        edges = pass_.apply(nodes, edges)
        warnings += pass_.warnings

    records: list[dict] = [{
        "type": "job",
        "job_id": job_id,
        "created_at": datetime.now(UTC).isoformat(),
        "schema_sha256": sha256(json.dumps(schema, sort_keys=True).encode()),
        "inputs": [asdict(i) for i in inputs],
        "ocr": ocr.describe(),
        "chooser": chooser.describe(),
        "layout": layout_engine,
        "split_gate_threshold": gate,
        "paragraphs": paragraphs,
        "context": context,
    }]

    page_tags: dict[str, set[str]] = {}
    for e in edges:
        if e["rel"] == "tagged" and e["rank"] == 1 and is_value(e["to"]):
            page_tags.setdefault(e["from"].split("/")[0], set()).add(e["to"])
    for p in pages:
        tags = sorted(page_tags.get(p.page_id, ()))
        records.append({
            "type": "page", "page_id": p.page_id, "source": p.source, "source_page": p.source_page,
            "sha256": p.sha256, "width": p.width, "height": p.height, "tags": tags, "placed": bool(tags),
        })
    records.extend(nodes)
    records.extend(edges)
    records.extend({"type": "warning", **w} for w in warnings)

    out = job_dir / "result.jsonl"
    tmp = out.with_suffix(".tmp")
    with tmp.open("w") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(out)
    return out
