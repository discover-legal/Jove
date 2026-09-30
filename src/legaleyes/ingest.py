"""Turn uploaded files into hashed, rasterised pages."""

import ctypes
import hashlib
import io
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import pypdfium2
import pypdfium2.raw as pdfium_c
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
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # "XFA pdf but no XFA support": the AcroForm layer renders
            pdf.init_forms()  # without this, filled form fields are neither drawn nor readable
        try:
            for page in pdf:
                yield page.render(scale=PDF_DPI / 72, may_draw_forms=True).to_pil(), _text_layer(page, pdf)
        finally:
            pdf.close()
        return
    try:
        image = Image.open(io.BytesIO(data))
    except Exception as e:
        raise ValueError(f"{filename}: not a PDF or readable image") from e
    for frame in ImageSequence.Iterator(image):
        yield frame.copy(), None


def _text_layer(page, pdf) -> list[tuple[str, tuple[float, float, float, float]]] | None:
    """Lines of a PDF page's own text, boxed in rendered-image pixels; None if it has none.

    Filled form fields live in annotations, not in the page text: their values
    are appended, each boxed by its field's rectangle.
    """
    lines = _page_text(page) or []
    lines += _form_values(page, pdf)
    return lines or None


def _form_values(page, pdf) -> list[tuple[str, tuple[float, float, float, float]]]:
    form = getattr(pdf, "formenv", None)
    if form is None:
        return []
    scale, page_h = PDF_DPI / 72, page.get_height()
    out = []
    for i in range(pdfium_c.FPDFPage_GetAnnotCount(page.raw)):
        annot = pdfium_c.FPDFPage_GetAnnot(page.raw, i)
        try:
            if pdfium_c.FPDFAnnot_GetSubtype(annot) != pdfium_c.FPDF_ANNOT_WIDGET:
                continue
            if pdfium_c.FPDFAnnot_GetFormFieldType(form.raw, annot) not in (
                    pdfium_c.FPDF_FORMFIELD_TEXTFIELD, pdfium_c.FPDF_FORMFIELD_COMBOBOX):
                continue
            n = pdfium_c.FPDFAnnot_GetFormFieldValue(form.raw, annot, None, 0)
            if n <= 2:
                continue
            buf = ctypes.create_string_buffer(n)
            pdfium_c.FPDFAnnot_GetFormFieldValue(form.raw, annot, ctypes.cast(buf, ctypes.POINTER(pdfium_c.FPDF_WCHAR)), n)
            value = buf.raw[: n - 2].decode("utf-16-le").strip()
            rect = pdfium_c.FS_RECTF()
            if not value or not pdfium_c.FPDFAnnot_GetRect(annot, rect):
                continue
            out.append((value, (rect.left * scale, (page_h - rect.top) * scale,
                                rect.right * scale, (page_h - rect.bottom) * scale)))
        finally:
            pdfium_c.FPDFPage_CloseAnnot(annot)
    return out


def _page_text(page) -> list[tuple[str, tuple[float, float, float, float]]] | None:
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
