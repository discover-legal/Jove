"""Older / plainer OCR engines, to measure how much the OCR stage matters to
how well the schema gets filled. Every engine here reads the page image, even
when the PDF has a text layer: it shows what a scan of the same page would give.
"""

import csv
import io
import subprocess
from collections import defaultdict

from legaleyes.ocr import OcrLine


def tesseract():
    """Tesseract 5 (LSTM), the classic open-source OCR. Needs the tesseract binary."""

    def read(page) -> list[OcrLine]:
        tsv = subprocess.run(["tesseract", str(page.image_path), "-", "--psm", "3", "tsv"],
                             check=True, capture_output=True, text=True).stdout
        lines: dict[tuple, list[dict]] = defaultdict(list)
        for row in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
            if row["level"] == "5" and row["text"].strip():
                lines[(row["block_num"], row["par_num"], row["line_num"])].append(row)
        out = []
        for words in lines.values():
            x0 = min(int(w["left"]) for w in words)
            y0 = min(int(w["top"]) for w in words)
            x1 = max(int(w["left"]) + int(w["width"]) for w in words)
            y1 = max(int(w["top"]) + int(w["height"]) for w in words)
            conf = sum(float(w["conf"]) for w in words) / len(words) / 100
            out.append(OcrLine(" ".join(w["text"] for w in words), (x0, y0, x1, y1), conf))
        return out

    return read


def _rapid(version: str, model_type: str, lang: str):
    from rapidocr import LangDet, LangRec, ModelType, OCRVersion, RapidOCR

    det_lang = "CH" if version == "PPOCRV4" else lang  # v4 ships only a Chinese/multilingual detector
    engine = RapidOCR(params={
        "Global.log_level": "error",
        "Det.ocr_version": OCRVersion[version], "Det.model_type": ModelType[model_type], "Det.lang_type": LangDet[det_lang],
        "Rec.ocr_version": OCRVersion[version], "Rec.model_type": ModelType[model_type], "Rec.lang_type": LangRec[lang],
    })

    def read(page) -> list[OcrLine]:
        r = engine(str(page.image_path))
        out = []
        for box, text, score in zip(r.boxes if r.boxes is not None else [], r.txts or [], r.scores or []):
            xs, ys = [p[0] for p in box], [p[1] for p in box]
            out.append(OcrLine(text, (float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys))), float(score),
                               tuple((float(x), float(y)) for x, y in box)))
        return out

    return read


ENGINES = {
    "tesseract": tesseract,
    "ppocr4": lambda: _rapid("PPOCRV4", "MOBILE", "EN"),
    "v6": lambda: _rapid("PPOCRV6", "SMALL", "EN"),
}
