"""Score chunking alone (no chooser, no credits): do gold values land in good units?

    uv run python eval/tagging/chunks.py [--ocr app|tesseract|ppocr4|v6]

  whole   the gold value's text sits inside a single unit (not cut across units)
  alone   ...and that unit holds no other gold value of a different field
  units   how many units the tagger would have to label
  read    the gold value's text appears anywhere on the page (an OCR ceiling)

With --ocr other than app, every page is read from its image (see run.py), so
"whole" and "read" say how much of the schema an OCR engine leaves fillable.

Region-type gold (spans of prose) is skipped: it's meant to cover many units.
"""

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from run import CachedOcr  # noqa: E402

from jove import layout  # noqa: E402
from jove.ingest import ingest  # noqa: E402

ROOT = Path(__file__).parent
REPO = ROOT.parent.parent


def norm(text: str) -> str:
    return re.sub(r"[^0-9a-z]", "", text.lower())


def units_for(src: Path, ocr) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        _, pages = ingest([(src.name, src.read_bytes())], Path(tmp))
        blocks = [b for p in pages for b in layout.build(p.page_id, ocr.read(p))]
    return [l.text for b in blocks for l in b.children]  # in reading order


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ocr", choices=["app", "tesseract", "ppocr4", "v6"], default="app")
    args = ap.parse_args()
    ocr = CachedOcr(args.ocr)
    tot = {"whole": 0, "alone": 0, "read": 0, "n": 0, "units": 0}
    for gold_path in sorted((ROOT / "gold").glob("*.json")):
        gold = json.loads(gold_path.read_text())
        units = [norm(u) for u in units_for(REPO / gold["file"], ocr)]
        tags = [t for t in gold["tags"] if not t.get("region") and norm(t["text"])]
        whole = alone = read = 0
        page = "".join(units)
        for t in tags:
            g = norm(t["text"])
            read += g in page
            holders = [u for u in units if g in u]
            if holders:
                whole += 1
                others = [o for o in tags if o is not t and o["path"] != t["path"] and norm(o["text"]) not in g]
                if any(not any(norm(o["text"]) in u for o in others) for u in holders):
                    alone += 1
        tot["whole"] += whole; tot["alone"] += alone; tot["read"] += read; tot["n"] += len(tags); tot["units"] += len(units)
        print(f"{gold_path.stem:<20} whole {whole:2d}/{len(tags):2d}  alone {alone:2d}/{len(tags):2d}  read {read:2d}/{len(tags):2d}  units {len(units):3d}", flush=True)
    print(f"{'TOTAL':<20} whole {tot['whole'] / tot['n']:.3f}  alone {tot['alone'] / tot['n']:.3f}  read {tot['read'] / tot['n']:.3f}  units {tot['units']}")


if __name__ == "__main__":
    main()
