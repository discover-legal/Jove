"""ingest → ocr → layout → tag → emit a JSONL graph."""

import json
import math
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from . import layout
from .choose import Chooser
from .ingest import ingest, sha256
from .ocr import Ocr
from .schema import build_tree
from .tagger import Decision, Tagger


def run(job_id: str, job_dir: Path, files: list[tuple[str, bytes]], schema: dict,
        ocr: Ocr, chooser: Chooser, separation: float = 1.5) -> Path:
    tree = build_tree(schema)
    inputs, pages = ingest(files, job_dir / "pages")
    blocks = {p.page_id: layout.build(p.page_id, ocr.read(p)) for p in pages}

    tagger = Tagger(tree, chooser, separation=separation)
    decisions = tagger.tag(blocks)

    records: list[dict] = [{
        "type": "job",
        "job_id": job_id,
        "created_at": datetime.now(UTC).isoformat(),
        "schema_sha256": sha256(json.dumps(schema, sort_keys=True).encode()),
        "inputs": [asdict(i) for i in inputs],
        "ocr": ocr.describe(),
        "chooser": chooser.describe(),
        "separation_threshold": separation,
    }]
    nodes, edges = _graph(blocks, decisions)

    page_tags: dict[str, set[str]] = {}
    for e in edges:
        if e["rel"] == "tagged" and e["rank"] == 1 and "#none" not in e["to"]:
            page_tags.setdefault(e["from"].split("/")[0], set()).add(e["to"])
    for p in pages:
        tags = sorted(page_tags.get(p.page_id, ()))
        records.append({
            "type": "page", "page_id": p.page_id, "source": p.source, "source_page": p.source_page,
            "sha256": p.sha256, "width": p.width, "height": p.height, "tags": tags, "placed": bool(tags),
        })
    records.extend(nodes)
    records.extend(edges)
    records.extend({"type": "warning", **w} for w in tagger.warnings)

    out = job_dir / "result.jsonl"
    tmp = out.with_suffix(".tmp")
    with tmp.open("w") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(out)
    return out


def _graph(blocks: dict[str, list[layout.DocNode]], decisions: list[Decision]) -> tuple[list[dict], list[dict]]:
    by_id = {d.doc.id: d for d in decisions}
    nodes: list[dict] = []
    edges: list[dict] = []

    def node(n: layout.DocNode, parent: str) -> None:
        nodes.append({"type": "node", "id": n.id, "kind": n.kind, "page_id": n.page_id,
                      "bbox": [round(float(v), 1) for v in n.bbox], "text": n.text})
        edges.append({"type": "edge", "rel": "contains", "from": parent, "to": n.id})
        if n.id in by_id:
            edges.extend(_decision_edges(by_id[n.id]))

    for page_id, page_blocks in blocks.items():
        for b in page_blocks:
            node(b, page_id)
            for line in b.children:
                node(line, b.id)
                for span in _spans(line, by_id):
                    nodes.append({"type": "node", "id": span["id"], "kind": "span", "page_id": line.page_id,
                                  "bbox": span["bbox"], "text": span["text"]})
                    edges.append({"type": "edge", "rel": "contains", "from": line.id, "to": span["id"]})
                    edges.append(span["edge"])
    return nodes, edges


def _decision_edges(d: Decision) -> list[dict]:
    if d.split:
        top = d.top[0]
        idx = [n.path for n in top.nodes].index(d.within)
        score = _geomean(top.probs[:idx]) if idx else None
        return [{"type": "edge", "rel": "within", "from": d.doc.id, "to": d.within or "<root>",
                 "score": score, "separation": _sep(d.separation), "asked_from": d.asked_from}]
    out = []
    for rank, path in enumerate(d.top, start=1):
        edge = {"type": "edge", "rel": "tagged", "from": d.doc.id, "to": path.target, "rank": rank,
                "score": round(path.score, 4), "asked_from": d.asked_from}
        if rank == 1:
            edge["separation"] = _sep(d.separation)
            edge["instances"] = d.instances
        out.append(edge)
    return out


def _spans(line: layout.DocNode, by_id: dict[str, Decision]) -> list[dict]:
    """Merge runs of consecutive words that got the same tag into spans."""
    runs: list[list[Decision]] = []
    for w in line.children:
        d = by_id.get(w.id)
        if d is None:
            continue
        if runs and _same(runs[-1][-1], d) and _adjacent(line, runs[-1][-1].doc, w):
            runs[-1].append(d)
        else:
            runs.append([d])
    spans = []
    for i, run in enumerate(runs, start=1):
        span_id = f"{line.id}/s{i:02d}"
        boxes = [d.doc.bbox for d in run]
        spans.append({
            "id": span_id,
            "text": " ".join(d.doc.text for d in run),
            "bbox": [round(float(f(b[i] for b in boxes)), 1) for f, i in ((min, 0), (min, 1), (max, 2), (max, 3))],
            "edge": {"type": "edge", "rel": "tagged", "from": span_id, "to": run[0].top[0].target, "rank": 1,
                     "score": round(sum(d.top[0].score for d in run) / len(run), 4),
                     "asked_from": run[0].asked_from,
                     "separation": _sep(min(d.separation for d in run)),
                     "instances": run[0].instances},
        })
    return spans


def _same(a: Decision, b: Decision) -> bool:
    return a.top[0].target == b.top[0].target and a.instances == b.instances


def _adjacent(line: layout.DocNode, a: layout.DocNode, b: layout.DocNode) -> bool:
    ids = [w.id for w in line.children]
    return ids.index(b.id) == ids.index(a.id) + 1


def _geomean(probs: list[float]) -> float:
    return round(math.exp(sum(math.log(max(p, 1e-9)) for p in probs) / len(probs)), 4)


def _sep(value: float) -> float | None:
    return None if math.isinf(value) else round(value, 3)
