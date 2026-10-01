"""Run a tagger over every gold document and save its JSONL for score.py.

    uv run python eval/tagging/run.py tree|flat [--only NAME]

OCR is cached per page (by page hash) under eval/tagging/cache, so every
tagger sees identical text and OCR runs once. Needs TYPESAFE_API_KEY.
"""

import argparse
import json
import shutil
import tempfile
import time
from pathlib import Path

from legaleyes.config import make_ocr
from legaleyes.jev import JevChooser
from legaleyes.ocr import OcrLine
from legaleyes.pipeline import run

ROOT = Path(__file__).parent
REPO = ROOT.parent.parent


class CachedOcr:
    def __init__(self):
        self._inner = None
        self.dir = ROOT / "cache"
        self.dir.mkdir(exist_ok=True)

    def describe(self):
        return {"provider": "cached", "of": "legaleyes.config.make_ocr()"}

    def read(self, page):
        path = self.dir / f"{page.sha256}.json"
        if path.exists():
            return [OcrLine(d["text"], tuple(d["bbox"]), d["confidence"], tuple(map(tuple, d["quad"])) if d["quad"] else None)
                    for d in json.loads(path.read_text())]
        self._inner = self._inner or make_ocr()
        lines = self._inner.read(page)
        path.write_text(json.dumps([{"text": l.text, "bbox": l.bbox, "confidence": l.confidence, "quad": l.quad}
                                    for l in lines]))
        return lines


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tagger", choices=["tree", "flat"])
    ap.add_argument("--only")
    ap.add_argument("--no-owners", action="store_true", help="skip the ownership pass")
    ap.add_argument("--out", help="results subdirectory (default: the tagger name)")
    args = ap.parse_args()
    out = ROOT / "results" / (args.out or args.tagger)
    out.mkdir(parents=True, exist_ok=True)
    ocr = CachedOcr()
    total = {"secs": 0.0, "requests": 0, "tokens": 0}
    for gold_path in sorted((ROOT / "gold").glob("*.json")):
        if args.only and gold_path.stem != args.only:
            continue
        gold = json.loads(gold_path.read_text())
        src = REPO / gold["file"]
        schema = json.loads((REPO / gold["schema"]).read_text())
        chooser = JevChooser()
        with tempfile.TemporaryDirectory() as tmp:
            t = time.time()
            result = run(gold_path.stem, Path(tmp), [(src.name, src.read_bytes())], schema, ocr, chooser,
                         context=gold.get("context"), tagger=args.tagger, owners=not args.no_owners)
            secs = time.time() - t
            shutil.copy(result, out / f"{gold_path.stem}.jsonl")
        total["secs"] += secs
        total["requests"] += chooser.requests
        total["tokens"] += chooser.input_tokens
        print(f"{gold_path.stem:<20} {secs:5.1f}s  {chooser.requests:3d} requests  {chooser.input_tokens:8d} tokens", flush=True)
    cost = total["tokens"] * 0.042 / 1e6
    print(f"{'TOTAL':<20} {total['secs']:5.1f}s  {total['requests']:3d} requests  {total['tokens']:8d} tokens  ${cost:.4f}")


if __name__ == "__main__":
    main()
