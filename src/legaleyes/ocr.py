"""OCR backends. Each returns text lines with pixel bounding boxes."""

import re
import statistics
from dataclasses import dataclass
from typing import Protocol

from .ingest import Page


@dataclass
class OcrLine:
    text: str
    bbox: tuple[float, float, float, float]  # x0, y0, x1, y1
    confidence: float | None = None
    quad: tuple[tuple[float, float], ...] | None = None  # corners TL, TR, BR, BL when the engine gives them
    form: bool = False  # typed into a fillable PDF form field: certainly a value


class Ocr(Protocol):
    def describe(self) -> dict: ...
    def read(self, page: Page) -> list[OcrLine]: ...


class RapidOcr:
    """RapidOCR 3 with PaddleOCR's PP-OCRv6 small models, run locally on CPU.

    Picked on eval/ocr: against PP-OCRv3/v5 and v6 medium it was the best on
    photos and faxes, and as fast as the old baseline. RapidOCR downloads its
    models on first use and checks them against pinned SHA-256s.
    """

    def __init__(self):
        from rapidocr import LangDet, LangRec, ModelType, OCRVersion, RapidOCR

        self._engine = RapidOCR(params={
            "Global.log_level": "error",
            # Keep low-scoring lines (default drops < 0.5): on shaky photos the detector
            # finds them but recognition is poor, and the re-read stage can repair them.
            "Global.text_score": 0.0,
            "Det.ocr_version": OCRVersion.PPOCRV6, "Det.model_type": ModelType.SMALL, "Det.lang_type": LangDet.EN,
            "Rec.ocr_version": OCRVersion.PPOCRV6, "Rec.model_type": ModelType.SMALL, "Rec.lang_type": LangRec.EN,
        })

    def describe(self) -> dict:
        return {"provider": "rapidocr", "models": "PP-OCRv6 small (en)"}

    def read(self, page: Page) -> list[OcrLine]:
        r = self._engine(str(page.image_path), return_word_box=True, return_single_char_box=True)
        lines = []
        chars_per_line = r.word_results or [None] * len(r.txts or [])
        for box, text, score, chars in zip(r.boxes if r.boxes is not None else [], r.txts or [], r.scores or [], chars_per_line):
            xs, ys = [p[0] for p in box], [p[1] for p in box]
            bbox = (float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys)))  # numpy -> float for JSON
            lines.append(OcrLine(respace(text, chars), bbox, float(score),
                                 tuple((float(x), float(y)) for x, y in box)))
        return lines


def respace(text: str, chars) -> str:
    """Put back spaces the recogniser dropped.

    Uses per-character boxes: a gap wider than 0.8x the median character width
    where the character kind changes (letter/digit/punctuation) is a space
    ("expires2027-04-03" -> "expires 2027-04-03"). Box positions are coarse
    estimates, so gaps inside words ("Acm e") are ignored. A colon glued to
    a following capital ("ID:Ontario") is split too; times like 10:00 are not.
    """
    text = re.sub(r"(?<=[A-Za-z]):(?=[A-Z])", ": ", text)
    if not chars:
        return text
    boxes = [(c[0], min(p[0] for p in c[2]), max(p[0] for p in c[2])) for c in chars if c and len(c) >= 3]
    widths = [b[2] - b[1] for b in boxes if b[2] > b[1]]
    if len(boxes) < 3 or not widths:
        return text
    threshold = 0.8 * statistics.median(widths)
    out, k = [], 0
    for i, ch in enumerate(text):
        if ch.isspace():
            out.append(ch)
            continue
        if k < len(boxes) and boxes[k][0] == ch:
            wide = k and boxes[k][1] - boxes[k - 1][2] >= threshold
            if wide and out and not out[-1].isspace() and _kind(out[-1]) != _kind(ch):
                out.append(" ")
            k += 1
        out.append(ch)
    return "".join(out) if k == len(boxes) else text  # boxes didn't align: leave text alone


def _kind(ch: str) -> str:
    return "a" if ch.isalpha() else "9" if ch.isdigit() else "."


class TextLayerFirst:
    """Use a born-digital PDF page's own text when it has one; OCR everything else.

    Exact text, no GPU, and it keeps typography OCR can't see ("B E T W E E N :").
    Pages with fewer than ``min_chars`` characters (scans with a stray text
    layer, image-only pages) still go to OCR.
    """

    def __init__(self, inner: "Ocr", min_chars: int = 40):
        self.inner = inner
        self.min_chars = min_chars

    def describe(self) -> dict:
        return {**self.inner.describe(), "text_layer": "used when present"}

    def read(self, page: Page) -> list[OcrLine]:
        layer = page.text_layer or []
        if sum(len(item[0]) for item in layer) >= self.min_chars:
            return [OcrLine(t, box, 1.0, ((box[0], box[1]), (box[2], box[1]), (box[2], box[3]), (box[0], box[3])),
                            form=bool(rest and rest[0]))
                    for t, box, *rest in layer]
        return self.inner.read(page)


class FakeOcr:
    """Returns canned lines per page id; for tests. An empty string leaves a
    blank line, which the layout reads as a block break."""

    def __init__(self, lines: dict[str, list[str]]):
        self._lines = lines

    def describe(self) -> dict:
        return {"provider": "fake"}

    def read(self, page: Page) -> list[OcrLine]:
        return [
            OcrLine(text, (0, i * 20, 10 * len(text), i * 20 + 18), 1.0)
            for i, text in enumerate(self._lines.get(page.page_id, []))
            if text
        ]
