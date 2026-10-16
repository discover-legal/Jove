"""Docling for structure, our OCR for text.

Docling's layout models find what a page is made of (headings, paragraphs,
list items, captions, tables and their cells, pictures) and in what order to
read them. Its own OCR is weaker than ours on phone photos, so only its
regions are used. Each of our OCR / text-layer lines is placed into the region
it sits in, and every region becomes one unit for the tagger, carrying its
layout role ("table cell under 'Rent Owing $'", "section header").

- Text inside picture regions (logos) is kept, with the role "text inside a picture or logo".
- A filled PDF form field is always its own unit: it is one value by construction.
- Lines outside every region become units of their own, slotted into reading
  order by position.
"""

import logging
from dataclasses import dataclass

from .layout import DocNode, _deskew, _Frag, _rows, _union, _words, skew
from .ocr import OcrLine

Box = tuple[float, float, float, float]


@dataclass
class Region:
    role: str
    box: Box  # page pixels, top-left origin


class DoclingLayout:
    """Runs Docling once per input file and returns its regions per page."""

    def __init__(self):
        logging.getLogger("docling").setLevel(logging.ERROR)
        from docling.document_converter import DocumentConverter

        self._converter = DocumentConverter()

    def regions(self, filename: str, data: bytes, sizes: list[tuple[int, int]]) -> list[list[Region]]:
        """Regions for each page of one input, scaled to the pages' pixel sizes (as ingested)."""
        import io

        from docling.datamodel.base_models import DocumentStream

        doc = self._converter.convert(DocumentStream(name=filename, stream=io.BytesIO(data))).document
        out: list[list[Region]] = [[] for _ in sizes]

        def place(prov, role: str, box=None) -> None:
            i = prov.page_no - 1
            if i >= len(sizes):
                return
            page = doc.pages[prov.page_no].size
            b = (box or prov.bbox).to_top_left_origin(page.height)
            sx, sy = sizes[i][0] / page.width, sizes[i][1] / page.height
            out[i].append(Region(role, (b.l * sx, b.t * sy, b.r * sx, b.b * sy)))

        for item, _level in doc.iterate_items():
            if not getattr(item, "prov", None):
                continue
            label = str(getattr(item, "label", "")).split(".")[-1]
            if label == "table":
                headers = {c.start_col_offset_idx: c.text for c in item.data.table_cells if c.column_header}
                for cell in item.data.table_cells:
                    if cell.bbox is None:
                        continue
                    if cell.column_header:
                        role = "table column header"
                    else:
                        head = headers.get(cell.start_col_offset_idx)
                        role = f"table cell under {head!r}" if head else "table cell"
                    place(item.prov[0], role, cell.bbox)
            elif label == "picture":
                place(item.prov[0], "text inside a picture or logo")
            else:
                place(item.prov[0], label.replace("_", " "))
        return out


def build(page_id: str, ocr: list[OcrLine], regions: list[Region]) -> list[DocNode]:
    """Blocks in reading order, one per region (or leftover line group), each holding one unit."""
    ocr = [o for o in ocr if o.text.strip()]
    angle = skew(ocr)
    groups: list[tuple[str, list[_Frag]]] = [(r.role, []) for r in regions]
    loose: list[_Frag] = []
    for o in ocr:
        frag = _Frag(o.text.strip(), _deskew(o, angle), o.bbox, o.form)
        best, area = None, 0.0
        for i, r in enumerate(regions):
            a = _overlap(o.bbox, r.box)
            if a > area:
                best, area = i, a
        own = (o.bbox[2] - o.bbox[0]) * (o.bbox[3] - o.bbox[1]) or 1.0
        if o.form:
            groups.append(("filled form field", [frag]))  # one field, one value
        elif best is not None and area >= 0.5 * own:
            groups[best][1].append(frag)
        else:
            loose.append(frag)

    units: list[tuple[float, str, list[_Frag]]] = []  # (reading position, role, fragments)
    for i, (role, frags) in enumerate(groups):
        if frags:
            pos = float(i) if i < len(regions) else _position(regions, _union([f.page_box for f in frags]))
            units.append((pos, role, frags))
    # Leftovers: grouped into rows by the plain layout rules, then slotted in by position.
    for row in _rows(loose):
        units.append((_position(regions, row.page_box), "text outside the detected layout", [row]))
    units.sort(key=lambda u: u[0])

    blocks = []
    for bi, (_pos, role, frags) in enumerate(units, start=1):
        block_id = f"{page_id}/b{bi:02d}"
        rows = _rows(frags)
        line_id = f"{block_id}/l01"
        words, text_parts, boxes = [], [], []
        for row in rows:
            for w in _words(line_id, page_id, row.page_box, row.text):
                words.append(w)
            text_parts.append(row.text)
            boxes.append(row.page_box)
        for wi, w in enumerate(words, start=1):  # renumber across the region's rows
            w.id = f"{line_id}/w{wi:02d}"
        box = _union(boxes)
        unit = DocNode(line_id, "line", page_id, box, " ".join(text_parts), words,
                       form=all(f.form for f in frags), role=role)
        blocks.append(DocNode(block_id, "block", page_id, box, unit.text, [unit], role=role))
    return blocks


def _overlap(a: Box, b: Box) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def _position(regions: list[Region], box: Box) -> float:
    """Reading position for text Docling didn't cover: just before the first region below it."""
    top = box[1]
    for i, r in enumerate(regions):
        if r.box[1] >= top:
            return i - 0.5
    return len(regions) + top / 1e6
