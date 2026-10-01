"""Score chunking alone (no chooser, no credits): do gold values land in good units?

    uv run python eval/tagging/chunks.py rules|docling

  whole   the gold value's text sits inside a single unit (not cut across units)
  alone   ...and that unit holds no other gold value of a different field
  units   how many units the tagger would have to label

Region-type gold (spans of prose) is skipped: it's meant to cover many units.
"""

import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from run import CachedOcr  # noqa: E402

from legaleyes import layout  # noqa: E402
from legaleyes.ingest import ingest  # noqa: E402

ROOT = Path(__file__).parent
REPO = ROOT.parent.parent


def norm(text: str) -> str:
    return re.sub(r"[^0-9a-z]", "", text.lower())


def units_for(engine: str, src: Path, ocr) -> list[str]:
    data = src.read_bytes()
    with tempfile.TemporaryDirectory() as tmp:
        _, pages = ingest([(src.name, data)], Path(tmp))
        if engine == "docling":
            from legaleyes import docling_layout

            regions = docling_layout.DoclingLayout().regions(src.name, data, [(p.width, p.height) for p in pages])
            blocks = [b for p, r in zip(pages, regions) for b in docling_layout.build(p.page_id, ocr.read(p), r)]
        else:
            blocks = [b for p in pages for b in layout.build(p.page_id, ocr.read(p))]
    return [l.text for b in blocks for l in b.children]


def main() -> None:
    engine = sys.argv[1]
    ocr = CachedOcr()
    tot = {"whole": 0, "alone": 0, "n": 0, "units": 0}
    for gold_path in sorted((ROOT / "gold").glob("*.json")):
        gold = json.loads(gold_path.read_text())
        units = [norm(u) for u in units_for(engine, REPO / gold["file"], ocr)]
        tags = [t for t in gold["tags"] if not t.get("region") and norm(t["text"])]
        whole = alone = 0
        for t in tags:
            g = norm(t["text"])
            holders = [u for u in units if g in u]
            if holders:
                whole += 1
                others = [o for o in tags if o is not t and o["path"] != t["path"] and norm(o["text"]) not in g]
                if any(not any(norm(o["text"]) in u for o in others) for u in holders):
                    alone += 1
        tot["whole"] += whole; tot["alone"] += alone; tot["n"] += len(tags); tot["units"] += len(units)
        print(f"{gold_path.stem:<20} whole {whole:2d}/{len(tags):2d}  alone {alone:2d}/{len(tags):2d}  units {len(units):3d}", flush=True)
    print(f"{'TOTAL':<20} whole {tot['whole'] / tot['n']:.3f}  alone {tot['alone'] / tot['n']:.3f}  units {tot['units']}")


if __name__ == "__main__":
    main()
