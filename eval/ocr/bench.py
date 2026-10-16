"""OCR benchmark: run an OCR engine over eval/ocr/{real,synthetic} and score it.

    uv run python eval/ocr/bench.py rapidocr [--only real]

Text goes through the real layout step, so reading-order mistakes count.
Metrics:
  cer      character error rate on whitespace-normalised text (lower is better)
  word_f1  bag-of-words F1, case-insensitive, edge punctuation stripped
  num_rec  share of ground-truth tokens containing a digit that come back exactly
           (dates, amounts, file numbers: what legal work can't afford to lose)
"""

import argparse
import json
import re
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

from rapidfuzz.distance import Levenshtein

sys.path.insert(0, str(Path(__file__).parent))
from engines import ENGINES  # noqa: E402

from jove.layout import build  # noqa: E402

ROOT = Path(__file__).parent


def words(text: str) -> list[str]:
    return [w for w in (t.strip(".,;:!?()\"'").lower() for t in text.split()) if w]


def score(truth: str, got: str) -> dict:
    t, g = " ".join(truth.split()), " ".join(got.split())
    cer = Levenshtein.distance(t, g) / max(len(t), 1)
    tw, gw = Counter(words(truth)), Counter(words(got))
    hit = sum((tw & gw).values())
    p, r = hit / max(sum(gw.values()), 1), hit / max(sum(tw.values()), 1)
    nums = [w for w in truth.split() if re.search(r"\d", w)]
    gtoks = Counter(got.split())
    num_hit = sum(min(c, gtoks[w]) for w, c in Counter(nums).items())
    return {"cer": cer, "word_f1": 2 * p * r / max(p + r, 1e-9), "num_rec": num_hit / max(len(nums), 1)}


def cases(only: str | None):
    for sub in ("real", "synthetic"):
        if only and only != sub:
            continue
        for txt in sorted((ROOT / sub).glob("*.txt")):
            img = next(p for p in txt.parent.glob(txt.stem + ".*") if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".pdf"))
            variant = "real" if sub == "real" else txt.stem.split(".")[-1]
            yield txt.stem, variant, img, txt.read_text()


def _pages(path: Path) -> list[Path]:
    """Images as-is; PDFs rasterised (text layer ignored) into a cache next to results."""
    if path.suffix.lower() != ".pdf":
        return [path]
    from jove.ingest import ingest

    out = ROOT / "results" / "pages" / path.stem
    _, pages = ingest([(path.name, path.read_bytes())], out)
    return pages  # Page objects keep the PDF text layer for engines that use it


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("engine", choices=sorted(ENGINES))
    ap.add_argument("--only", choices=["real", "synthetic"])
    ap.add_argument("--variant", help="only this capture variant, e.g. shake")
    ap.add_argument("--show", action="store_true", help="print recognised text")
    args = ap.parse_args()

    engine = ENGINES[args.engine]()
    rows = []
    for name, variant, img, truth in cases(args.only):
        if args.variant and variant != args.variant:
            continue
        t = time.time()
        pages = _pages(img)
        got = "\n".join(l.text for p in pages for b in build("p", engine(p)) for l in b.children)
        secs = (time.time() - t) / len(pages)
        s = score(truth, got) | {"name": name, "variant": variant, "secs": secs}
        rows.append(s)
        print(f"{name:<34} cer={s['cer']:.3f} f1={s['word_f1']:.3f} num={s['num_rec']:.3f} {secs:5.1f}s", flush=True)
        if args.show:
            print(got, "\n")

    print()
    groups = {}
    for r in rows:
        groups.setdefault(r["variant"], []).append(r)
    summary = {}
    for g, rs in sorted(groups.items()) + [("ALL", rows)]:
        m = {k: statistics.mean(r[k] for r in rs) for k in ("cer", "word_f1", "num_rec", "secs")}
        summary[g] = m
        print(f"{g:<6} n={len(rs):2d}  cer={m['cer']:.3f}  f1={m['word_f1']:.3f}  num={m['num_rec']:.3f}  {m['secs']:.1f}s/page")
    out = ROOT / "results"
    out.mkdir(exist_ok=True)
    suffix = f".{args.variant}" if args.variant else ""
    (out / f"{args.engine}{suffix}.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=1))


if __name__ == "__main__":
    main()
