"""Recursive OCR by majority vote of independent readers, line by line.

Every OCR line is cut out of the page (straightened) and read again by a
dedicated OCR model (glm-ocr), which is trusted for words: on blurred photos
it is far stronger than the detector's own recogniser. Other readers are only
called on dispute: a cheap tiebreaker (granite3.2-vision) when the two
readings look nothing alike (is the OCR model hallucinating?), and a strong
general vision model (qwen3.5) when a number lacks a second witness.

Numbers get stricter treatment: a number in the chosen reading must be
backed by at least one other reader (allowing classic glyph confusions such
as O/0). An unbacked number is replaced by the OCR engine's own reading,
which stays visibly uncertain rather than becoming a confident wrong one:
page-level OCR models were seen turning a blurred 1986 into "1996".

Measured on eval/ocr (see its README).
"""

import difflib
import logging
import re

import httpx

from .ingest import Page
from .ocr import Ocr, OcrLine, release
from .ollama import chat, unload
from .reread import _CONFUSABLE, line_crop, similarity

log = logging.getLogger(__name__)
TRANSCRIBE = "Transcribe the single line of text in this image exactly. Output only the text."
_EDGE = re.compile(r"^\W+|\W+$")


def norm(text: str) -> str:
    return " ".join(_EDGE.sub("", t).lower() for t in text.split())


def _num_key(tok: str) -> str:
    return _EDGE.sub("", tok).translate(_CONFUSABLE)


def medoid(readings: list[str]) -> int:
    """Index of the reading most similar to all the others (ties: earliest wins)."""
    scores = [sum(similarity(norm(r), norm(o)) for j, o in enumerate(readings) if j != i) for i, r in enumerate(readings)]
    return max(range(len(readings)), key=lambda i: scores[i])


def back_numbers(chosen: str, others: list[str], ocr: str) -> str:
    """Replace numbers in ``chosen`` that no other reading supports with the OCR's tokens."""
    keys = {_num_key(t) for r in others for t in r.split()}

    a, b = ocr.split(), chosen.split()
    out: list[str] = []
    def bad(t: str) -> bool:
        return bool(re.search(r"\d", t)) and _num_key(t) not in keys

    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        old, new = a[i1:i2], b[j1:j2]
        if op == "equal" or not any(bad(t) for t in new):
            out += new
        elif len(old) == len(new):
            out += [o if bad(n) else n for o, n in zip(old, new)]  # revert only the unbacked numbers
        else:
            out += old
    return " ".join(out)


def unbacked(chosen: str, others: list[str]) -> bool:
    keys = {_num_key(t) for r in others for t in r.split()}
    return any(re.search(r"\d", t) and _num_key(t) not in keys for t in chosen.split())


def decide(ocr: str, second: str, tiebreak=None, escalate=None, outlier: float = 0.35) -> str:
    """Choose a line from the OCR reading and the OCR model's reading; call more readers only on dispute.

    - The OCR model (second) is trusted for words: it is far stronger on blur.
    - If it looks nothing like the OCR (similarity < ``outlier``), a cheap tiebreaker
      checks whether it is the odd one out (a hallucination); the medoid wins then.
    - Every number must be backed by another reader. If not, ``escalate`` (a
      stronger reader) is asked; numbers still unbacked revert to the OCR's tokens.
    """
    if not second:
        return ocr
    if norm(ocr) == norm(second):
        return second  # same words; the OCR model's punctuation and case are better
    readings = [ocr, second]
    k = 1
    if tiebreak and similarity(norm(ocr), norm(second)) < outlier:
        third = tiebreak()
        if third:
            readings.append(third)
            k = medoid(readings)
    chosen, others = readings[k], readings[:k] + readings[k + 1:]
    if escalate and unbacked(chosen, others):
        extra = escalate()
        if extra:
            others.append(extra)
    return back_numbers(chosen, others, ocr)


class LineVote:
    def __init__(self, inner: Ocr, base_url: str = "http://localhost:11434", reader: str = "glm-ocr",
                 tiebreaker: str = "granite3.2-vision", escalation: str = "qwen3.5:9b-16k",
                 skip_above: float = 0.995, min_lines: int = 3, timeout: float = 120):
        self.inner = inner
        self.base_url = base_url.rstrip("/").removesuffix("/v1")
        self.reader = reader
        self.tiebreaker = tiebreaker
        self.escalation = escalation
        self.skip_above = skip_above
        self.min_lines = min_lines
        self.timeout = timeout
        self._offline = False

    def describe(self) -> dict:
        return {**self.inner.describe(),
                "vote": {"reader": self.reader, "tiebreaker": self.tiebreaker, "escalation": self.escalation}}

    def read(self, page: Page) -> list[OcrLine]:
        lines = self.inner.read(page)
        if self._offline:
            return lines
        import cv2

        img = cv2.imread(str(page.image_path))
        if len(lines) < self.min_lines:
            return self._whole_page(img, lines)
        todo = [i for i, l in enumerate(lines) if l.quad and (l.confidence or 0) < self.skip_above]
        for i in todo:
            line = lines[i]
            crop = line_crop(img, line.quad)
            try:
                second = self._read(crop, self.reader, "Text Recognition:", len(line.text))
                text = decide(
                    line.text, second,
                    tiebreak=lambda: self._read(crop, self.tiebreaker, TRANSCRIBE, len(line.text)),
                    escalate=lambda: self._read(crop, self.escalation, TRANSCRIBE, len(line.text)),
                )
            except httpx.TransportError as e:
                log.warning("line vote disabled, %s unreachable: %s", self.base_url, e)
                self._offline = True
                break
            lines[i] = OcrLine(text, line.bbox, line.confidence, line.quad)
        return lines

    def _whole_page(self, img, lines: list[OcrLine]) -> list[OcrLine]:
        """Detection found (almost) nothing: take the page model's reading, lines stacked top to bottom.

        Boxes are unknown, so they are spread evenly down the page and carry no quad.
        """
        try:
            text = self._chat(img, self.reader, "Text Recognition:", 4000)
        except httpx.TransportError as e:
            log.warning("line vote disabled, %s unreachable: %s", self.base_url, e)
            self._offline = True
            return lines
        rows = [first_line(l) for l in text.splitlines()]
        rows = [r for r in rows if r]
        if len(rows) <= len(lines):
            return lines
        h, w = img.shape[:2]
        step = h / (len(rows) + 1)
        return [OcrLine(r, (0.0, step * (i + 0.5), float(w), step * (i + 1.2)), None) for i, r in enumerate(rows)]

    def release(self) -> None:
        unload(self.base_url, [self.reader, self.tiebreaker, self.escalation])
        release(self.inner)

    def _read(self, crop, model: str, prompt: str, length: int) -> str:
        return first_line(self._chat(crop, model, prompt, 20 + length))

    def _chat(self, img, model: str, prompt: str, max_tokens: int) -> str:
        return chat(self.base_url, img, model, prompt, max_tokens, self.timeout)



_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"})


def first_line(text: str) -> str:
    """OCR models append junk (markdown fences, repeats) after the line; keep the first real one."""
    for line in text.splitlines():
        line = re.sub(r"^#+\s*|\*\*|__|^`+\w*|`+$", "", line.strip()).strip()
        if line and not line.startswith("```"):
            return tidy(line.translate(_QUOTES))
    return ""


def tidy(line: str) -> str:
    """Undo tokenizer-style spacing some models emit: "( gone ) and someday ," -> "(gone) and someday,".

    Quotes are left alone: a quotation can open on one line and close on the next.
    """
    line = re.sub(r"\s+([,.;:!?)\]])", r"\1", line)
    line = re.sub(r"([(\[])\s+", r"\1", line)
    return re.sub(r"\s{2,}", " ", line).strip()
