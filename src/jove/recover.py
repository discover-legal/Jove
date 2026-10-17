"""Find text lines the OCR detector never boxed.

The line vote (vote.py) can only re-read lines the detector found. On photos the
detector misses things: slanted or curved lettering, logo text, handwriting.
This pass reads the whole page with the OCR model, keeps the rows that no
detected line covers, asks a grounding vision model (qwen3-vl) where each one
is, then crops that box and reads it again. A row is added only when the crop
reads back the same text, numbers included: page-level OCR models loop and
state wrong numbers confidently (see eval/ocr), so nothing is taken on the
page read's word alone.
"""

import json
import logging
import re
import statistics

import httpx

from .ingest import Page
from .ocr import Ocr, OcrLine, release
from .ollama import chat, unload
from .vote import first_line, norm, similarity, unbacked

log = logging.getLogger(__name__)

LOCATOR_SIDE = 1280  # px; full pages overflow the locator's context

LOCATE = (
    "Find each of these text lines in the image and give its bounding box. "
    'Reply with JSON only: [{{"text": "...", "bbox_2d": [x1, y1, x2, y2]}}, ...]. '
    "Lines: {rows}"
)


class MissedLines:
    def __init__(self, inner: Ocr, base_url: str = "http://localhost:11434", reader: str = "glm-ocr",
                 locator: str = "qwen3-vl:4b-instruct", agree: float = 0.7,
                 max_rows: int = 8, max_words: int = 12, timeout: float = 120):
        self.inner = inner
        self.base_url = base_url.rstrip("/").removesuffix("/v1")
        self.reader = reader
        self.locator = locator
        self.agree = agree  # the crop must read back at least this similar to the page row
        self.max_rows = max_rows
        self.max_words = max_words  # longer page rows are paragraphs: a misread word there is not a missed line
        self.timeout = timeout
        self._offline = False

    def describe(self) -> dict:
        return {**self.inner.describe(), "recover": {"reader": self.reader, "locator": self.locator}}

    def read(self, page: Page) -> list[OcrLine]:
        lines = self.inner.read(page)
        if self._offline or not lines:
            return lines
        import cv2

        img = cv2.imread(str(page.image_path))
        try:
            rows = [r for r in missing(page_rows(self._chat(img, self.reader, "Text Recognition:", 2000)), lines)
                    if len(r.split()) <= self.max_words]
            if not rows:
                return lines
            added = []
            line_h = statistics.median(l.bbox[3] - l.bbox[1] for l in lines)
            for row, box in self._locate(img, rows[: self.max_rows]):
                found = gap(row, box, lines + added, line_h)
                if found is None:
                    continue
                expected, region = found
                crop = _crop(img, region)
                if crop is None:
                    continue
                text = first_line(self._chat(crop, self.reader, "Text Recognition:", 20 + len(expected)))
                if similarity(norm(text), norm(expected)) >= self.agree and not unbacked(text, [row]):
                    x0, y0, x1, y1 = region
                    added.append(OcrLine(text, region, None, ((x0, y0), (x1, y0), (x1, y1), (x0, y1))))
        except (httpx.TimeoutException, httpx.HTTPStatusError) as e:
            log.warning("missed-line recovery skipped %s: %s", page.page_id, e)
            return lines
        except httpx.TransportError as e:
            log.warning("missed-line recovery disabled, %s unreachable: %s", self.base_url, e)
            self._offline = True
            return lines
        return lines + added

    def _locate(self, img, rows: list[str]) -> list[tuple[str, tuple[float, float, float, float]]]:
        import cv2

        h, w = img.shape[:2]
        scale = min(1.0, LOCATOR_SIDE / max(h, w))
        small = cv2.resize(img, (round(w * scale), round(h * scale))) if scale < 1 else img
        reply = self._chat(small, self.locator, LOCATE.format(rows=json.dumps(rows)), 100 + 60 * len(rows))
        out = []
        for item in parse_boxes(reply):
            score, row = max((similarity(norm(r), norm(item["text"])), r) for r in rows)
            if score < self.agree:
                continue
            x0, y0, x1, y1 = item["bbox_2d"]  # qwen3-vl: 0..1000, relative to the image
            box = (x0 * w / 1000, y0 * h / 1000, x1 * w / 1000, y1 * h / 1000)
            if box[2] > box[0] and box[3] > box[1]:
                out.append((row, box))
        return out

    def release(self) -> None:
        unload(self.base_url, [self.reader, self.locator])
        release(self.inner)

    def _chat(self, img, model: str, prompt: str, max_tokens: int) -> str:
        return chat(self.base_url, img, model, prompt, max_tokens, self.timeout)


def page_rows(text: str) -> list[str]:
    """Rows of a whole-page reading, deduplicated (page OCR models can loop)."""
    seen, out = set(), []
    for line in text.splitlines():
        row = re.sub(r"^#+\s*|\*\*|__|^[-*]\s+", "", line.strip()).strip()
        if row and not row.startswith("```") and norm(row) not in seen and any(c.isalnum() for c in row):
            seen.add(norm(row))
            out.append(row)
    return out


def missing(rows: list[str], lines: list[OcrLine]) -> list[str]:
    """Rows with a word that no detected line has anywhere (tolerant of small OCR slips).

    Cheap and lenient: the logo's "Weed" hides nothing else, so rows are checked
    again by position (``gap``) once located.
    """
    have = _tokens(" ".join(l.text for l in lines))
    return [row for row in rows if _uncovered(row, have)]


def gap(row: str, box, lines: list[OcrLine], line_h: float):
    """(words of ``row`` the lines at ``box`` lack, the stretch of ``box`` no line covers), or None.

    The locator's boxes are loose and often thin, so the region is widened to
    the page's usual line height.
    """
    cy0, cy1 = box[1] - line_h / 2, box[3] + line_h / 2
    # Lines at the row's place that print some of its words. Neighbours sharing no word
    # (the line below, inside the widened band) are not part of the row.
    near = [l for l in lines if cy0 <= (l.bbox[1] + l.bbox[3]) / 2 <= cy1
            and min(l.bbox[2], box[2]) > max(l.bbox[0], box[0]) and _shares(l.text, row)]
    words = _uncovered(row, _tokens(" ".join(l.text for l in near)))
    if not words:
        return None
    spans, x = [], box[0]
    for x0, x1 in sorted((l.bbox[0], l.bbox[2]) for l in near):
        if x0 > x:
            spans.append((x, x0))
        x = max(x, x1)
    if box[2] > x:
        spans.append((x, box[2]))
    if not spans:
        return None
    x0, x1 = max(spans, key=lambda s: s[1] - s[0])
    if x1 - x0 < line_h:
        return None
    cy, half = (box[1] + box[3]) / 2, max(box[3] - box[1], 1.5 * line_h) / 2
    return " ".join(words), (x0, cy - half, x1, cy + half)


def _tokens(text: str) -> list[str]:
    return norm(text).split()


def _shares(a: str, b: str) -> bool:
    return any(similarity(x, y) >= 0.8 for x in _tokens(a) for y in _tokens(b))


def _uncovered(row: str, have: list[str]) -> list[str]:
    """Words of ``row`` (as printed) with no close match in ``have``."""
    return [w for w in row.split() for t in _tokens(w)
            if (len(t) >= 2 or t.isdigit()) and not any(similarity(t, h) >= 0.8 for h in have)]


def parse_boxes(reply: str) -> list[dict]:
    text = reply.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = [data]
    return [d for d in data if isinstance(d, dict) and isinstance(d.get("text"), str)
            and isinstance(d.get("bbox_2d"), list) and len(d["bbox_2d"]) == 4
            and all(isinstance(v, (int, float)) for v in d["bbox_2d"])]


def _crop(img, box, pad: float = 0.15):
    h, w = img.shape[:2]
    x0, y0, x1, y1 = box
    p = pad * (y1 - y0)  # by line height on every side
    x0, y0, x1, y1 = int(max(0, x0 - p)), int(max(0, y0 - p)), int(min(w, x1 + p)), int(min(h, y1 + p))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    return img[y0:y1, x0:x1]
