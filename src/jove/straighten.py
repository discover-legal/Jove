"""Straighten photographed pages before OCR, and map the lines back.

Two cases, in order:

- **Paper on a background:** when the page shows a clear sheet (the largest
  bright quadrilateral, 15-90% of the frame), it is flattened by a perspective
  warp. A scan is its own sheet, so it is left alone.
- **Tilt:** otherwise the skew is the length-weighted median angle of the
  line boxes from a first reading. Text isn't fooled by curbs, fences and
  table edges, as edge-based (Hough) estimates are. Pages tilted by
  ``min_angle`` or more are rotated level.

The straightened page is read again, and the reading the engine is surer of
(confidence-weighted characters) wins: a wrong sheet outline distorts text,
and on eval/ocr's shaken photos flattening alone cost word F1.

Lines come back in the original page's coordinates (quads mapped through the
inverse warp), so provenance boxes still point at the page as uploaded.
"""

import dataclasses
import math
import tempfile
from pathlib import Path

from .ingest import Page
from .ocr import Ocr, OcrLine, release


class Straighten:
    def __init__(self, inner: Ocr, min_angle: float = 1.0, max_angle: float = 30.0):
        self.inner = inner
        self.min_angle = min_angle
        self.max_angle = max_angle

    def describe(self) -> dict:
        return {**self.inner.describe(), "straighten": {"min_angle": self.min_angle}}

    def release(self) -> None:
        release(self.inner)

    def read(self, page: Page) -> list[OcrLine]:
        import cv2

        img = cv2.imread(str(page.image_path))
        first = self.inner.read(page)
        fix = paper(img)
        if fix is None:
            angle = text_angle(first)
            if angle is None or not self.min_angle <= abs(angle) <= self.max_angle:
                return first
            fix = rotation(img.shape, angle)
        H, size = fix
        warped = cv2.warpPerspective(img, H, size, borderMode=cv2.BORDER_REPLICATE)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "straight.png"
            cv2.imwrite(str(path), warped)
            lines = self.inner.read(dataclasses.replace(page, image_path=path, width=size[0], height=size[1]))
        # A wrong sheet outline (double exposures, shadows) distorts text: keep whichever reading is surer.
        if _sureness(lines) <= _sureness(first):
            return first
        return [unwarp(l, H) for l in lines]


def text_angle(lines: list[OcrLine]) -> float | None:
    """Length-weighted median baseline angle of the lines, in degrees (positive = clockwise)."""
    samples = []
    for l in lines:
        if not l.quad or len(l.quad) != 4:
            continue
        (x0, y0), (x1, y1) = l.quad[0], l.quad[1]
        length = math.hypot(x1 - x0, y1 - y0)
        height = math.hypot(l.quad[3][0] - x0, l.quad[3][1] - y0)
        if length > 2 * height:  # single words and stamps have unreliable angles
            samples.append((math.degrees(math.atan2(y1 - y0, x1 - x0)), length))
    if len(samples) < 3:
        return None
    samples.sort()
    half, acc = sum(w for _, w in samples) / 2, 0.0
    for angle, w in samples:
        acc += w
        if acc >= half:
            return angle
    return None


def paper(img):
    """(homography, size) flattening the sheet of paper in a photo, or None (no clear sheet, or a scan)."""
    import cv2
    import numpy as np

    h, w = img.shape[:2]
    f = 1000 / max(h, w)
    small = cv2.resize(img, None, fx=f, fy=f)
    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    c = max(contours, key=cv2.contourArea)
    share = cv2.contourArea(c) / (small.shape[0] * small.shape[1])
    if not 0.15 <= share <= 0.9:
        return None
    quad = cv2.approxPolyDP(c, 0.02 * cv2.arcLength(c, True), True)
    if len(quad) != 4 or not cv2.isContourConvex(quad):
        return None
    pts = quad.reshape(4, 2).astype(np.float32) / f
    s, d = pts.sum(1), np.diff(pts, axis=1).ravel()
    tl, br, tr, bl = pts[np.argmin(s)], pts[np.argmax(s)], pts[np.argmin(d)], pts[np.argmax(d)]
    qw = max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl))
    qh = max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr))
    dst = np.float32([[0, 0], [qw, 0], [qw, qh], [0, qh]])
    return cv2.getPerspectiveTransform(np.float32([tl, tr, br, bl]), dst), (int(qw), int(qh))


def rotation(shape, angle: float):
    """(homography, size) levelling text tilted by ``angle`` degrees (clockwise), on a canvas that fits the page."""
    import cv2
    import numpy as np

    h, w = shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    cos, sin = abs(M[0, 0]), abs(M[0, 1])
    nw, nh = w * cos + h * sin, w * sin + h * cos
    M[0, 2] += nw / 2 - w / 2
    M[1, 2] += nh / 2 - h / 2
    return np.vstack([M, [0, 0, 1]]), (int(math.ceil(nw)), int(math.ceil(nh)))


def unwarp(line: OcrLine, H) -> OcrLine:
    import cv2
    import numpy as np

    x0, y0, x1, y1 = line.bbox
    quad = line.quad or ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
    pts = cv2.perspectiveTransform(np.float32(quad).reshape(-1, 1, 2), np.linalg.inv(H)).reshape(-1, 2)
    back = tuple((float(x), float(y)) for x, y in pts)
    xs, ys = [p[0] for p in back], [p[1] for p in back]
    return dataclasses.replace(line, bbox=(min(xs), min(ys), max(xs), max(ys)), quad=back)


def _sureness(lines: list[OcrLine]) -> float:
    """Characters read, weighted by the engine's confidence in each line."""
    return sum(len(l.text.strip()) * (l.confidence or 0.0) for l in lines)
