from legaleyes.layout import build
from legaleyes.ocr import OcrLine


def test_merges_fragments_and_splits_blocks():
    ocr = [
        OcrLine("Doe", (222, 30, 288, 59)),
        OcrLine("Name:Jane", (22, 29, 216, 59)),
        OcrLine("DOB: 1980-04-03", (22, 65, 300, 95)),
        OcrLine("Opposing party: Acme Ltd", (22, 200, 500, 230)),
    ]
    blocks = build("p0001", ocr)
    assert [b.text for b in blocks] == ["Name:Jane Doe\nDOB: 1980-04-03", "Opposing party: Acme Ltd"]
    assert [l.id for l in blocks[0].children] == ["p0001/b01/l01", "p0001/b01/l02"]
    words = blocks[1].children[0].children
    assert [w.text for w in words] == ["Opposing", "party:", "Acme", "Ltd"]
    assert words[0].bbox[0] == 22 and words[-1].bbox[2] == 500


def test_columns_split_only_at_a_shared_gutter():
    two_columns = [OcrLine(t, (x, y, x + 150, y + 20)) for y in (0, 25, 50)
                   for x, t in ((0, f"left {y}"), (400, f"right {y}"))]
    [block] = build("p0001", two_columns)
    assert [l.text for l in block.children] == ["left 0", "right 0", "left 25", "right 25", "left 50", "right 50"]


def test_table_rows_keep_label_with_value():
    rows = [
        OcrLine("Regular", (0, 0, 100, 20)), OcrLine("80.00", (200, 0, 260, 20)), OcrLine("2,200.00", (600, 0, 700, 20)),
        OcrLine("Vacation pay", (0, 25, 150, 45)), OcrLine("98.73", (620, 25, 700, 45)),
    ]
    [block] = build("p0001", rows)
    assert [l.text for l in block.children] == ["Regular 80.00 2,200.00", "Vacation pay 98.73"]


def test_empty_page():
    assert build("p0001", []) == []


def test_tilted_lines_are_not_merged():
    # Two lines tilted 8°: their upright bounding boxes overlap by more than half, but the lines don't.
    import math
    def tilted(text, x, y, w=400, h=20, a=math.radians(8)):
        c, s = math.cos(a), math.sin(a)
        quad = ((x, y), (x + w * c, y + w * s), (x + w * c - h * s, y + w * s + h * c), (x - h * s, y + h * c))
        xs, ys = [p[0] for p in quad], [p[1] for p in quad]
        return OcrLine(text, (min(xs), min(ys), max(xs), max(ys)), 1.0, quad)
    [block] = build("p0001", [tilted("first line", 0, 0), tilted("second line", 0, 30)])
    assert [l.text for l in block.children] == ["first line", "second line"]


def test_different_text_sizes_split_at_a_gap():
    ocr = [OcrLine("STOPPERS", (0, 0, 200, 40)), OcrLine("Working in partnership with", (240, 10, 500, 30))]
    [block] = build("p0001", ocr)
    assert [l.text for l in block.children] == ["STOPPERS", "Working in partnership with"]


def test_side_by_side_panels_read_one_after_another():
    ocr = [OcrLine("CRIME", (0, 0, 120, 30)), OcrLine("STOPPERS", (0, 40, 120, 70)),
           OcrLine("Working in partnership with", (200, 15, 450, 35)),
           OcrLine("the Regional Municipality of York", (200, 45, 470, 65))]
    blocks = build("p0001", ocr)
    assert [[l.text for l in b.children] for b in blocks] == [
        ["CRIME", "STOPPERS"], ["Working in partnership with", "the Regional Municipality of York"]]
