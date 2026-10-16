import math

import pytest

from jove.ingest import Page
from jove.ocr import OcrLine
from jove.straighten import Straighten, rotation, text_angle, unwarp

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")


def tilted(text, x, y, length, angle, height=30):
    """A line box whose baseline runs ``angle`` degrees clockwise from horizontal."""
    a = math.radians(angle)
    dx, dy = length * math.cos(a), length * math.sin(a)
    hx, hy = -height * math.sin(a), height * math.cos(a)
    quad = ((x, y), (x + dx, y + dy), (x + dx + hx, y + dy + hy), (x + hx, y + hy))
    xs, ys = [p[0] for p in quad], [p[1] for p in quad]
    return OcrLine(text, (min(xs), min(ys), max(xs), max(ys)), 0.9, quad)


def test_text_angle_is_the_weighted_median_and_ignores_short_words():
    lines = [tilted("a long line of text", 100, 100 + 60 * i, 600, 5) for i in range(4)]
    lines.append(tilted("x", 50, 50, 20, 40))  # one word at a silly angle: ignored
    assert text_angle(lines) == pytest.approx(5)
    assert text_angle(lines[:2]) is None  # too few lines to judge


def test_rotation_levels_the_text_and_unwarp_maps_it_back():
    line = tilted("a long line of text", 300, 400, 600, 7)
    H, size = rotation((1000, 1200), 7)
    pts = cv2.perspectiveTransform(np.float32(line.quad).reshape(-1, 1, 2), H).reshape(-1, 2)
    assert pts[1][1] == pytest.approx(pts[0][1], abs=0.5)  # level after the warp
    assert all(0 <= x <= size[0] and 0 <= y <= size[1] for x, y in pts)
    level = OcrLine(line.text, (0, 0, 0, 0), 0.9, tuple(map(tuple, pts)))
    back = unwarp(level, H)
    assert np.allclose(back.quad, line.quad, atol=0.5)


class TiltedReader:
    """Fake OCR: on the original page it sees tilted lines; on a levelled page, level ones."""

    def __init__(self):
        self.sizes = []

    def read(self, page):
        self.sizes.append((page.width, page.height))
        if len(self.sizes) == 1:
            return [tilted(f"line {i} of the notice", 200, 200 + 80 * i, 700, 6) for i in range(5)]
        return [OcrLine(f"line {i} of the notice", (200, 200 + 80 * i, 900, 230 + 80 * i), 0.99,
                        ((200, 200 + 80 * i), (900, 200 + 80 * i), (900, 230 + 80 * i), (200, 230 + 80 * i)))
                for i in range(5)]


def test_tilted_page_is_read_again_level_and_boxes_come_back_in_page_coordinates(tmp_path):
    path = tmp_path / "p.png"
    cv2.imwrite(str(path), np.full((1200, 1000, 3), 128, np.uint8))  # flat grey: no sheet to find
    reader = TiltedReader()
    lines = Straighten(reader).read(Page("p0001", "p.png", 1, path, "0" * 64, 1000, 1200))
    assert len(reader.sizes) == 2 and reader.sizes[1] != (1000, 1200)  # read twice, on a bigger canvas
    # the level reading, mapped back onto the page as uploaded, carries the page's tilt again
    assert text_angle(lines) == pytest.approx(6, abs=0.1)
    assert all(0 <= v <= 1200 for l in lines for v in l.bbox)


def test_level_page_is_read_once(tmp_path):
    path = tmp_path / "p.png"
    cv2.imwrite(str(path), np.full((1200, 1000, 3), 128, np.uint8))

    class Level(TiltedReader):
        def read(self, page):
            self.sizes.append(1)
            return [tilted(f"line {i}", 200, 200 + 80 * i, 700, 0.3) for i in range(5)]

    reader = Level()
    Straighten(reader).read(Page("p0001", "p.png", 1, path, "0" * 64, 1000, 1200))
    assert reader.sizes == [1]
