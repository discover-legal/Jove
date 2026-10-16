from jove.docling_layout import Region, build
from jove.ocr import OcrLine


def line(text, x0, y0, x1, y1, form=False):
    return OcrLine(text, (x0, y0, x1, y1), 1.0, form=form)


def test_lines_fill_regions_in_reading_order():
    regions = [Region("section header", (0, 0, 500, 40)), Region("text", (0, 60, 500, 140)),
               Region("text inside a picture or logo", (520, 0, 600, 60))]
    ocr = [
        line("about the", 10, 65, 200, 85), line("artist and more", 10, 90, 300, 110),  # one paragraph, two lines
        line("SUNSET SESSIONS", 10, 5, 300, 35),
        line("f8", 530, 10, 560, 30),  # logo text
        line("Marcus Oyelaran", 10, 200, 200, 220, form=True),  # a filled field: its own unit
        line("stray footer", 10, 300, 100, 320),  # outside every region
    ]
    blocks = build("p0001", ocr, regions)
    got = [(b.role, b.text) for b in blocks]
    assert got[:3] == [("section header", "SUNSET SESSIONS"), ("text", "about the artist and more"),
                       ("text inside a picture or logo", "f8")]
    assert ("filled form field", "Marcus Oyelaran") in got and got[-1] == ("text outside the detected layout", "stray footer")
    unit = blocks[1].children[0]
    assert [w.text for w in unit.children] == ["about", "the", "artist", "and", "more"]
    assert len({w.id for w in unit.children}) == 5
