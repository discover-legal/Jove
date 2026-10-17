"""Build a document tree from OCR boxes: page → block → line → word.

OCR engines split lines at horizontal gaps ("Name:Jane" / "Doe"), so fragments
that share a baseline are merged back into one line first. Blocks are runs of
lines without a large vertical gap. Word boxes are estimated from character
offsets, because line-level OCR gives no per-word boxes.

Photos are rarely square to the page. When the OCR engine gives box corners,
the page's skew is estimated from them and layout runs on de-rotated boxes;
otherwise a tilted line's upright bounding box overlaps its neighbours and
lines get merged. Output boxes stay in page coordinates.
"""

import math
import statistics
from dataclasses import dataclass, field

from .ocr import OcrLine

Box = tuple[float, float, float, float]
BLOCK_GAP = 0.8  # vertical gap, in median line heights, that starts a new block
BLOCK_PITCH = 1.5  # ...or line-to-line spacing above this multiple of the median spacing
COLUMN_GAP = 4.0  # horizontal gap, in line heights, that separates two lines on one row
SIZE_RATIO = 1.4  # text heights this far apart are different elements...
MIXED_GAP = 1.0  # ...and split at a gap of this many (smaller) line heights


@dataclass
class DocNode:
    id: str
    kind: str  # block | line | word
    page_id: str
    bbox: Box
    text: str
    children: list["DocNode"] = field(default_factory=list)
    form: bool = False  # every fragment came from a filled PDF form field


@dataclass
class _Frag:
    text: str
    box: Box  # layout space (de-skewed)
    page_box: Box  # page space
    form: bool = False


def build(page_id: str, ocr: list[OcrLine]) -> list[DocNode]:
    ocr = [o for o in ocr if o.text.strip()]
    angle = skew(ocr)
    frags = [_Frag(o.text.strip(), _deskew(o, angle), o.bbox, o.form) for o in ocr]
    regions = [r for r in (_rows(region) for region in xycut(frags)) if r]
    rows = [row for region in regions for row in region]
    if not rows:
        return []
    height = statistics.median(r.box[3] - r.box[1] for r in rows) or 1.0
    # Photos inflate OCR boxes until neighbouring lines "overlap", hiding gaps;
    # the spacing between line centres still shows paragraph breaks.
    steps = []
    for region in regions:
        centers = [(r.box[1] + r.box[3]) / 2 for r in region]
        # Side-by-side columns give ~0 pitch between rows at the same height; ignore those.
        steps += [b - a for a, b in zip(centers, centers[1:]) if b - a > 0.3 * height]
    pitch = statistics.median(steps) if len(steps) >= 3 else math.inf

    blocks: list[list[_Frag]] = []
    for region in regions:
        start = len(blocks)
        for row in region:
            prev = blocks[-1][-1] if len(blocks) > start else None
            if prev is not None:
                gap = row.box[1] - prev.box[3]
                step = (row.box[1] + row.box[3]) / 2 - (prev.box[1] + prev.box[3]) / 2
                if gap <= BLOCK_GAP * height and step <= BLOCK_PITCH * pitch:
                    blocks[-1].append(row)
                    continue
            blocks.append([row])

    out = []
    for bi, rows_in_block in enumerate(blocks, start=1):
        block_id = f"{page_id}/b{bi:02d}"
        lines = []
        for li, row in enumerate(rows_in_block, start=1):
            box, text = row.page_box, row.text
            line_id = f"{block_id}/l{li:02d}"
            lines.append(DocNode(line_id, "line", page_id, box, text, _words(line_id, page_id, box, text), form=row.form))
        out.append(DocNode(block_id, "block", page_id, _union([l.bbox for l in lines]),
                           "\n".join(l.text for l in lines), lines))
    return out


def xycut(frags: list[_Frag]) -> list[list[_Frag]]:
    """Split the page into regions read one after another (recursive XY-cut).

    Horizontal bands of whitespace (at least one line height) cut first, then
    vertical gutters, but only between content whose lines don't share
    baselines: panels side by side on a sign are separate regions, while a
    table's label and value columns stay together so rows keep label and value.
    """
    if len(frags) < 2:
        return [frags] if frags else []
    h = statistics.median(f.box[3] - f.box[1] for f in frags) or 1.0

    band = _cut(frags, axis=1, min_gap=1.0 * h)
    if band:
        return [r for part in band for r in xycut(part)]
    col = _cut(frags, axis=0, min_gap=1.0 * h)
    if col and not _aligned(*col, h):
        return [r for part in col for r in xycut(part)]
    return [frags]


def _cut(frags: list[_Frag], axis: int, min_gap: float) -> list[list[_Frag]] | None:
    """Split at the widest empty interval along ``axis`` (0 = x, 1 = y), if one is wide enough."""
    lo, hi = axis, axis + 2
    spans = sorted(frags, key=lambda f: f.box[lo])
    best, best_gap, reach = None, min_gap, spans[0].box[hi]
    for i in range(1, len(spans)):
        gap = spans[i].box[lo] - reach
        if gap > best_gap:
            best, best_gap = i, gap
        reach = max(reach, spans[i].box[hi])
    if best is None:
        return None
    return [spans[:best], spans[best:]]


def _aligned(left: list[_Frag], right: list[_Frag], h: float) -> bool:
    """Do most lines on the smaller side have a partner at the same height on the other side?"""
    small, big = sorted((left, right), key=len)
    centers = [(f.box[1] + f.box[3]) / 2 for f in big]
    hits = sum(any(abs((f.box[1] + f.box[3]) / 2 - c) < 0.3 * h for c in centers) for f in small)
    return hits >= 0.8 * len(small)  # table rows almost all line up; panels only by chance


def skew(ocr: list[OcrLine]) -> float:
    """Median text-baseline angle in radians, from the top edges of wide boxes."""
    angles = []
    for o in ocr:
        if o.quad and len(o.quad) == 4:
            (x0, y0), (x1, y1) = o.quad[0], o.quad[1]
            if x1 - x0 > 2 * (o.bbox[3] - o.bbox[1]) / 3 and x1 > x0:
                angles.append(math.atan2(y1 - y0, x1 - x0))
    return statistics.median(angles) if angles else 0.0


def _deskew(o: OcrLine, angle: float) -> Box:
    points = o.quad or ((o.bbox[0], o.bbox[1]), (o.bbox[2], o.bbox[1]), (o.bbox[2], o.bbox[3]), (o.bbox[0], o.bbox[3]))
    if not angle:
        return o.bbox
    c, s = math.cos(-angle), math.sin(-angle)
    rotated = [(x * c - y * s, x * s + y * c) for x, y in points]
    xs, ys = [p[0] for p in rotated], [p[1] for p in rotated]
    return (min(xs), min(ys), max(xs), max(ys))


def _rows(frags: list[_Frag]) -> list[_Frag]:
    """Merge fragments sharing a baseline; keep far-apart columns separate."""
    groups: list[list[_Frag]] = []
    for f in sorted(frags, key=lambda f: (f.box[1] + f.box[3]) / 2):
        for g in groups:
            ref = g[0].box
            overlap = min(ref[3], f.box[3]) - max(ref[1], f.box[1])
            # Fragments of one line sit side by side; if they overlap horizontally
            # they are different lines (perspective makes one skew angle imperfect).
            beside = all(_x_overlap(m.box, f.box) <= 0.2 * min(m.box[2] - m.box[0], f.box[2] - f.box[0]) for m in g)
            if beside and overlap >= 0.5 * min(ref[3] - ref[1], f.box[3] - f.box[1]):
                g.append(f)
                break
        else:
            groups.append([f])

    for g in groups:
        g.sort(key=lambda f: f.box[0])

    rows = []
    for g in groups:
        h = max(f.box[3] - f.box[1] for f in g) or 1.0
        run = [g[0]]
        for f in g[1:]:
            gap = f.box[0] - run[-1].box[2]
            # A wide gap splits the row at a gutter (whitespace other rows share), or
            # between text of clearly different sizes (a logo beside body text on a
            # sign). Otherwise it's a table row ("Vacation pay ...... 98.73") and the
            # label must stay with its value.
            hl, hr = run[-1].box[3] - run[-1].box[1], f.box[3] - f.box[1]
            mixed = max(hl, hr) > SIZE_RATIO * min(hl, hr)
            if (gap > COLUMN_GAP * h and _gutter(groups, g, (run[-1].box[2] + f.box[0]) / 2)) or \
                    (mixed and gap > MIXED_GAP * min(hl, hr)):
                rows.append(_join(run))
                run = []
            run.append(f)
        rows.append(_join(run))
    return sorted(rows, key=lambda r: (r.box[1], r.box[0]))


def _gutter(groups: list[list[_Frag]], row: list[_Frag], x: float, needed: int = 2) -> bool:
    """True if at least ``needed`` other rows have text on both sides of x and none across it."""
    count = 0
    for g in groups:
        if g is row:
            continue
        left = any(f.box[2] < x for f in g)
        right = any(f.box[0] > x for f in g)
        across = any(f.box[0] <= x <= f.box[2] for f in g)
        if left and right and not across:
            count += 1
            if count >= needed:
                return True
    return False


def _x_overlap(a: Box, b: Box) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0]))


def _join(run: list[_Frag]) -> _Frag:
    return _Frag(" ".join(f.text for f in run), _union([f.box for f in run]), _union([f.page_box for f in run]),
                 all(f.form for f in run))


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
