"""Recursive OCR: re-read doubtful lines with a local vision model.

Lines the OCR engine is less than ``threshold`` sure of are cut out of the page
(perspective-corrected from their quad), enlarged, and transcribed by a vision
model with the OCR reading as a hint. The model is treated as a constrained spell-checker, not a rewriter, because
vision LLMs complete sentences from memory, read neighbouring lines, and swap in
plausible names ("Craiy Okafor" -> "Brian Okafor"). ``constrain`` keeps the OCR's
token structure and accepts a model token only when:

- it replaces exactly one OCR token and is spelled like it (similarity >= 0.5),
  or a split/merge changes the joined text only slightly (>= 0.75);
- digits don't change, except by a classic glyph confusion (O/0, l/1, S/5, B/8).
  A model will "fix" a blurred 198o into a plausible 1980; a confident wrong
  date or amount is worse than a visibly garbled one.

Added or dropped tokens are ignored.

On eval/ocr this took the real flyer from word F1 0.665 to 0.776 while leaving
confident lines (clean scans) untouched and unslowed.
"""

import base64
import difflib
import logging
import re

import httpx

from .ingest import Page
from .ocr import Ocr, OcrLine

log = logging.getLogger(__name__)

PROMPT = (
    "This image is one line of text cropped from a photographed document. "
    "Transcribe it exactly: same words, spelling, punctuation, capitalisation and numbers. "
    "A rough OCR reading was: {hint!r}. It may contain errors; trust the image. "
    "Output only the transcription."
)
_CONFUSABLE = str.maketrans("OoQDIl|iSsZzBbGg", "0000111155228869")


class VlmReread:
    def __init__(self, inner: Ocr, base_url: str, model: str, threshold: float = 0.98,
                 min_similarity: float = 0.5, timeout: float = 120):
        self.inner = inner
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.threshold = threshold
        self.min_similarity = min_similarity
        self.timeout = timeout
        self._offline = False

    def describe(self) -> dict:
        return {**self.inner.describe(), "reread": {"model": self.model, "threshold": self.threshold}}

    def release(self) -> None:
        from .ollama import unload

        unload(self.base_url.removesuffix("/v1"), [self.model])

    def read(self, page: Page) -> list[OcrLine]:
        lines = self.inner.read(page)
        doubtful = [i for i, l in enumerate(lines) if l.quad and (l.confidence or 0) < self.threshold]
        if not doubtful or self._offline:
            return lines
        import cv2

        img = cv2.imread(str(page.image_path))
        for i in doubtful:
            line = lines[i]
            try:
                text = self._transcribe(line_crop(img, line.quad), line.text)
            except httpx.TransportError as e:
                log.warning("vision re-read disabled, %s unreachable: %s", self.base_url, e)
                self._offline = True
                break
            if text and "\n" not in text and similarity(text, line.text) >= self.min_similarity:
                lines[i] = OcrLine(constrain(line.text, text), line.bbox, line.confidence, line.quad)
        return lines

    def _transcribe(self, crop, hint: str) -> str:
        import cv2

        ok, png = cv2.imencode(".png", crop)
        resp = httpx.post(f"{self.base_url}/chat/completions", timeout=self.timeout, json={
            "model": self.model, "temperature": 0, "reasoning_effort": "none",
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": PROMPT.format(hint=hint)},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png.tobytes()).decode()}},
            ]}],
        })
        resp.raise_for_status()
        return (resp.json()["choices"][0]["message"].get("content") or "").strip().strip('"').strip()


def line_crop(img, quad, pad_x: float = 0.5, pad_y: float = 0.1, height: int = 64):
    """Straighten one line out of the page and scale it to at least ``height`` px.

    Vertical padding is kept small so neighbouring lines stay out of the crop.
    """
    import cv2
    import numpy as np

    q = np.float32(quad)
    w = int(max(np.linalg.norm(q[1] - q[0]), np.linalg.norm(q[2] - q[3])))
    h = int(max(np.linalg.norm(q[3] - q[0]), np.linalg.norm(q[2] - q[1])))
    px, py = pad_x * h, pad_y * h
    dst = np.float32([[px, py], [w + px, py], [w + px, h + py], [px, h + py]])
    crop = cv2.warpPerspective(img, cv2.getPerspectiveTransform(q, dst), (int(w + 2 * px), int(h + 2 * py)),
                               borderMode=cv2.BORDER_REPLICATE)
    f = height / crop.shape[0]
    return cv2.resize(crop, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC) if f > 1 else crop


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(a=a.lower(), b=b.lower(), autojunk=False).ratio()


def constrain(ocr: str, vlm: str, token_similarity: float = 0.5, group_similarity: float = 0.75) -> str:
    """Accept the VLM's corrections only where they stay close to what OCR saw."""
    a, b = ocr.split(), vlm.split()
    out: list[str] = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        old, new = a[i1:i2], b[j1:j2]
        if op == "equal" or op in ("insert", "delete"):
            out += old  # added words are ignored, dropped words kept
        elif len(old) == len(new):
            out += [pick(x, y, token_similarity) for x, y in zip(old, new)]
        elif (not any(re.search(r"\d", t) for t in old + new)
              and similarity(" ".join(old), " ".join(new)) >= group_similarity):
            out += new  # a split or merge of words, e.g. "ntimateshppod-back" -> "intimate, stripped-back"
        else:
            out += old
    return " ".join(out)


def pick(ocr_tok: str, vlm_tok: str, min_similarity: float = 0.5) -> str:
    """One token: the model's if spelled like the OCR's and digits agree; else the OCR's."""
    if re.search(r"\d", ocr_tok) or re.search(r"\d", vlm_tok):
        return vlm_tok if ocr_tok.translate(_CONFUSABLE) == vlm_tok.translate(_CONFUSABLE) else ocr_tok
    if similarity(ocr_tok, vlm_tok) < min_similarity:
        return ocr_tok
    # OCR sees capitals by shape, so keep its first-letter case; only I/l/i are
    # ambiguous ("Inducted" -> "inducted" is a real fix, "Canade's" -> "canada's" is not).
    if ocr_tok[:1] not in "Iil" and ocr_tok[:1].isupper() and vlm_tok[:1].islower():
        return vlm_tok[:1].upper() + vlm_tok[1:]
    return vlm_tok
