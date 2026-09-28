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


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ingest(files: list[tuple[str, bytes]], out_dir: Path) -> tuple[list[InputFile], list[Page]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    inputs: list[InputFile] = []
    pages: list[Page] = []
    for filename, data in files:
        record = InputFile(filename, sha256(data))
        for n, image in enumerate(_images(filename, data), start=1):
            page_id = f"p{len(pages) + 1:04d}"
            buf = io.BytesIO()
            image.convert("RGB").save(buf, format="PNG")
            png = buf.getvalue()
            path = out_dir / f"{page_id}.png"
            path.write_bytes(png)
            pages.append(Page(page_id, filename, n, path, sha256(png), image.width, image.height))
            record.pages.append(page_id)
        inputs.append(record)
    return inputs, pages


def _images(filename: str, data: bytes):
    if data.startswith(b"%PDF"):
        pdf = pypdfium2.PdfDocument(data)
        try:
            for page in pdf:
                yield page.render(scale=PDF_DPI / 72).to_pil()
        finally:
            pdf.close()
        return
    try:
        image = Image.open(io.BytesIO(data))
    except Exception as e:
        raise ValueError(f"{filename}: not a PDF or readable image") from e
    for frame in ImageSequence.Iterator(image):
        yield frame.copy()
