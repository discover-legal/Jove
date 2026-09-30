"""Turn uploaded files into hashed, rasterised pages."""

import hashlib
import io
from dataclasses import dataclass, field
from pathlib import Path

import pypdfium2
from PIL import Image, ImageSequence

PDF_DPI = 200


@dataclass
class InputFile:
    filename: str
    sha256: str
    pages: list[str] = field(default_factory=list)


@dataclass
class Page:
    page_id: str
    source: str
    source_page: int
    image_path: Path
    sha256: str
    width: int
    height: int
    # Born-digital PDF pages: (line text, bbox in image pixels) from the text layer.
    text_layer: list[tuple[str, tuple[float, float, float, float]]] | None = None


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ingest(files: list[tuple[str, bytes]], out_dir: Path) -> tuple[list[InputFile], list[Page]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    inputs: list[InputFile] = []
    pages: list[Page] = []
    for filename, data in files:
        record = InputFile(filename, sha256(data))
        for n, (image, text_layer) in enumerate(_images(filename, data), start=1):
            page_id = f"p{len(pages) + 1:04d}"
            buf = io.BytesIO()
            image.convert("RGB").save(buf, format="PNG")
            png = buf.getvalue()
            path = out_dir / f"{page_id}.png"
            path.write_bytes(png)
            pages.append(Page(page_id, filename, n, path, sha256(png), image.width, image.height, text_layer))
            record.pages.append(page_id)
        inputs.append(record)
    return inputs, pages


def _images(filename: str, data: bytes):
    if data.startswith(b"%PDF"):
        pdf = pypdfium2.PdfDocument(data)
        try:
            for page in pdf:
                yield page.render(scale=PDF_DPI / 72).to_pil(), _text_layer(page)
        finally:
            pdf.close()
        return
    try:
        image = Image.open(io.BytesIO(data))
    except Exception as e:
        raise ValueError(f"{filename}: not a PDF or readable image") from e
    for frame in ImageSequence.Iterator(image):
        yield frame.copy(), None


def _text_layer(page) -> list[tuple[str, tuple[float, float, float, float]]] | None:
    """Lines of a PDF page's own text, boxed in rendered-image pixels; None if it has none."""
    tp = page.get_textpage()
    try:
        n = tp.count_chars()
        if n == 0:
            return None
        text = tp.get_text_range(0, n)
        if len(text) != n:  # generated characters don't map 1:1 to boxes; don't guess
            return None
        scale = PDF_DPI / 72
        page_h = page.get_height()
        lines, chars, boxes = [], [], []

        def flush():
            s = "".join(chars).strip()
            if s and boxes:
                l, b, r, t = (min(x[0] for x in boxes), min(x[1] for x in boxes),
                              max(x[2] for x in boxes), max(x[3] for x in boxes))
                lines.append((s, (l * scale, (page_h - t) * scale, r * scale, (page_h - b) * scale)))
            chars.clear()
            boxes.clear()

        for i, ch in enumerate(text):
            if ch in "\r\n":
                flush()
                continue
            chars.append(ch)
            if not ch.isspace():
                boxes.append(tp.get_charbox(i))
        flush()
        return lines or None
    finally:
        tp.close()
