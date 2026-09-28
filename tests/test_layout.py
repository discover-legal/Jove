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


def test_far_columns_stay_separate_lines():
    ocr = [OcrLine("Left", (0, 0, 40, 20)), OcrLine("Right", (400, 0, 450, 20))]
    [block] = build("p0001", ocr)
    assert [l.text for l in block.children] == ["Left", "Right"]


def test_empty_page():
    assert build("p0001", []) == []
