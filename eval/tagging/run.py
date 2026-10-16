"""Run a tagger over every gold document and save its JSONL for score.py.

    uv run python eval/tagging/run.py tree|flat [--only NAME]

OCR is cached per page (by page hash) under eval/tagging/cache, so every
tagger sees identical text and OCR runs once. The default chooser, jev, needs
TYPESAFE_API_KEY; clef, llm and laya are local (see README).
"""

import argparse
import json
import shutil
import tempfile
import time
from pathlib import Path

from jove.config import make_chooser, make_ocr
from jove.ocr import OcrLine, release
from jove.pipeline import run

ROOT = Path(__file__).parent
REPO = ROOT.parent.parent


class CachedOcr:
    """engine "app" is what the app ships (config.make_ocr, text layer first);
    the others are eval/tagging/ocr_engines.py, which always read the image."""

    def __init__(self, engine: str = "app", cache: str | None = None):
        self.engine = engine
        self._inner = None
        self.dir = ROOT / "cache" / (cache or ("" if engine == "app" else engine))
        self.dir.mkdir(parents=True, exist_ok=True)

    def describe(self):
        if self.engine != "app":
            return {"provider": "cached", "of": f"eval/tagging/ocr_engines.py:{self.engine}"}
        return {"provider": "cached", "of": "jove.config.make_ocr()"}

    def release(self):
        release(self._inner)  # free the OCR models' GPU memory before the chooser runs

    def _make(self):
        if self.engine == "app":
            return make_ocr()
        from ocr_engines import ENGINES

        return Reader(ENGINES[self.engine]())

    def read(self, page):
        if page.text_layer and self.engine == "app":  # born-digital: instant, and the cache would drop newer OcrLine fields
            self._inner = self._inner or make_ocr()
            return self._inner.read(page)
        path = self.dir / f"{page.sha256}.json"
        if path.exists():
            return [OcrLine(d["text"], tuple(d["bbox"]), d["confidence"], tuple(map(tuple, d["quad"])) if d["quad"] else None)
                    for d in json.loads(path.read_text())]
        self._inner = self._inner or self._make()
        lines = self._inner.read(page)
        path.write_text(json.dumps([{"text": l.text, "bbox": l.bbox, "confidence": l.confidence, "quad": l.quad}
                                    for l in lines]))
        return lines


class Reader:
    def __init__(self, read):
        self.read = read


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tagger", choices=["tree", "flat"])
    ap.add_argument("--only")
    ap.add_argument("--no-owners", action="store_true", help="skip the ownership pass")
    ap.add_argument("--layout", choices=["rules", "docling"], default="rules")
    ap.add_argument("--no-paragraphs", action="store_true", help="flat tagger: ask line by line, prose too")
    ap.add_argument("--chooser", choices=["jev", "clef", "llm", "laya"], default="jev")
    ap.add_argument("--ocr", choices=["app", "tesseract", "ppocr4", "v6"], default="app",
                    help="OCR engine; all but app read the page image even when there is a text layer")
    ap.add_argument("--out", help="results subdirectory (default: the tagger name)")
    ap.add_argument("--ocr-cache", help="OCR cache subdirectory, e.g. to re-read pages with a changed OCR stack")
    args = ap.parse_args()
    out = ROOT / "results" / (args.out or args.tagger)
    out.mkdir(parents=True, exist_ok=True)
    ocr = CachedOcr(args.ocr, args.ocr_cache)
    chooser = None
    total = {"secs": 0.0, "requests": 0, "tokens": 0}
    for gold_path in sorted((ROOT / "gold").glob("*.json")):
        if args.only and gold_path.stem != args.only:
            continue
        gold = json.loads(gold_path.read_text())
        src = REPO / gold["file"]
        schema = json.loads((REPO / gold["schema"]).read_text())
        chooser = chooser or make_chooser(args.chooser)
        before = chooser.requests, chooser.input_tokens
        with tempfile.TemporaryDirectory() as tmp:
            t = time.time()
            result = run(gold_path.stem, Path(tmp), [(src.name, src.read_bytes())], schema, ocr, chooser,
                         context=gold.get("context"), tagger=args.tagger, owners=not args.no_owners,
                         layout_engine=args.layout, paragraphs=not args.no_paragraphs)
            secs = time.time() - t
            shutil.copy(result, out / f"{gold_path.stem}.jsonl")
        total["secs"] += secs
        requests, tokens = chooser.requests - before[0], chooser.input_tokens - before[1]
        total["requests"] += requests
        total["tokens"] += tokens
        print(f"{gold_path.stem:<20} {secs:5.1f}s  {requests:5d} requests  {tokens:8d} tokens", flush=True)
    cost = total["tokens"] * 0.042 / 1e6 if args.chooser == "jev" else 0.0
    print(f"{'TOTAL':<20} {total['secs']:5.1f}s  {total['requests']:3d} requests  {total['tokens']:8d} tokens  ${cost:.4f}")


if __name__ == "__main__":
    main()
