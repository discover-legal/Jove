"""OCR backends. Each returns text lines with pixel bounding boxes."""

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx

from .ingest import Page


@dataclass
class OcrLine:
    text: str
    bbox: tuple[float, float, float, float]  # x0, y0, x1, y1
    confidence: float | None = None


class Ocr(Protocol):
    def describe(self) -> dict: ...
    def read(self, page: Page) -> list[OcrLine]: ...


# RapidOCR's bundled recogniser is Chinese+English and drops spaces between
# English words ("CLIENTINTAKEFORM"). PaddleOCR's English model keeps them.
EN_REC_URL = "https://huggingface.co/SWHL/RapidOCR/resolve/main/PP-OCRv3/en_PP-OCRv3_rec_infer.onnx"
EN_REC_SHA256 = "ef7abd8bd3629ae57ea2c28b425c1bd258a871b93fd2fe7c433946ade9b5d9ea"


def english_rec_model(cache: Path | None = None) -> Path:
    """Fetch the English recognition model once, verified against a pinned hash."""
    path = (cache or Path.home() / ".cache" / "legaleyes") / "en_PP-OCRv3_rec_infer.onnx"
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == EN_REC_SHA256:
        return path
    data = httpx.get(EN_REC_URL, follow_redirects=True, timeout=300).raise_for_status().content
    if hashlib.sha256(data).hexdigest() != EN_REC_SHA256:
        raise RuntimeError("English OCR model download failed its hash check")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return path


class RapidOcr:
    def __init__(self, rec_model: Path | None = None):
        from rapidocr_onnxruntime import RapidOCR

        self._rec_model = rec_model or english_rec_model()
        self._engine = RapidOCR(rec_model_path=str(self._rec_model))

    def describe(self) -> dict:
        return {"provider": "rapidocr", "rec_model": self._rec_model.name}

    def read(self, page: Page) -> list[OcrLine]:
        result, _ = self._engine(str(page.image_path))
        lines = []
        for box, text, score in result or []:
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            lines.append(OcrLine(text, (min(xs), min(ys), max(xs), max(ys)), float(score)))
        return lines


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
