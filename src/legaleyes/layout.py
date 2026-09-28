"""Build a document tree from OCR boxes: page → block → line → word.

OCR engines split lines at horizontal gaps ("Name:Jane" / "Doe"), so fragments
that share a baseline are merged back into one line first. Blocks are runs of
lines without a large vertical gap. Word boxes are estimated from character
offsets, because line-level OCR gives no per-word boxes.
"""

import statistics
from dataclasses import dataclass, field

from .ocr import OcrLine

Box = tuple[float, float, float, float]
BLOCK_GAP = 0.8  # vertical gap, in median line heights, that starts a new block
COLUMN_GAP = 4.0  # horizontal gap, in line heights, that separates two lines on one row


@dataclass
class DocNode:
    id: str
    kind: str  # block | line | word
    page_id: str
    bbox: Box
    text: str
    children: list["DocNode"] = field(default_factory=list)


def build(page_id: str, ocr: list[OcrLine]) -> list[DocNode]:
    rows = _rows([o for o in ocr if o.text.strip()])
    if not rows:
        return []
    height = statistics.median(b[3] - b[1] for b, _ in rows) or 1.0

    blocks: list[list[tuple[Box, str]]] = []
    for row in rows:
        if blocks and row[0][1] - blocks[-1][-1][0][3] <= BLOCK_GAP * height:
            blocks[-1].append(row)
        else:
            blocks.append([row])

    out = []
    for bi, rows_in_block in enumerate(blocks, start=1):
        block_id = f"{page_id}/b{bi:02d}"
        lines = []
        for li, (box, text) in enumerate(rows_in_block, start=1):
            line_id = f"{block_id}/l{li:02d}"
            lines.append(DocNode(line_id, "line", page_id, box, text, _words(line_id, page_id, box, text)))
        out.append(DocNode(block_id, "block", page_id, _union([l.bbox for l in lines]),
                           "\n".join(l.text for l in lines), lines))
    return out


def _rows(ocr: list[OcrLine]) -> list[tuple[Box, str]]:
    """Merge OCR fragments sharing a baseline; keep far-apart columns separate."""
    groups: list[list[OcrLine]] = []
    for o in sorted(ocr, key=lambda o: (o.bbox[1] + o.bbox[3]) / 2):
        for g in groups:
            ref = g[0].bbox
            overlap = min(ref[3], o.bbox[3]) - max(ref[1], o.bbox[1])
            if overlap >= 0.5 * min(ref[3] - ref[1], o.bbox[3] - o.bbox[1]):
                g.append(o)
                break
        else:
            groups.append([o])

    rows = []
    for g in groups:
        g.sort(key=lambda o: o.bbox[0])
        h = max(o.bbox[3] - o.bbox[1] for o in g) or 1.0
        run = [g[0]]
        for o in g[1:]:
            if o.bbox[0] - run[-1].bbox[2] > COLUMN_GAP * h:
                rows.append(_join(run))
                run = []
            run.append(o)
        rows.append(_join(run))
    return sorted(rows, key=lambda r: (r[0][1], r[0][0]))


def _join(run: list[OcrLine]) -> tuple[Box, str]:
    return _union([o.bbox for o in run]), " ".join(o.text.strip() for o in run)


def _words(line_id: str, page_id: str, box: Box, text: str) -> list[DocNode]:
    words, pos = [], 0
    width = box[2] - box[0]
    for wi, word in enumerate(text.split(), start=1):
        start = text.index(word, pos)
        pos = start + len(word)
        x0 = box[0] + width * start / len(text)
        x1 = box[0] + width * pos / len(text)
        words.append(DocNode(f"{line_id}/w{wi:02d}", "word", page_id,
                             (round(x0, 1), box[1], round(x1, 1), box[3]), word))
    return words


def _union(boxes: list[Box]) -> Box:
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))
