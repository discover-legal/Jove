import cv2
import httpx
import numpy as np

from jove.ingest import Page
from jove.ocr import OcrLine, respace
from jove.reread import VlmReread, constrain, line_crop


def test_constrain_digits():
    assert constrain("its 198o dobiut, The", "its 1980 debut, The") == "its 1980 debut, The"  # o->0 is a glyph fix
    assert constrain("paid $2,450.00 on", "paid $2,480.00 on") == "paid $2,450.00 on"  # 5->8 is a new number
    assert constrain("due 2024", "due by 2025") == "due 2024"


def test_constrain_blocks_hallucination():
    # Real failures seen on the flyer with qwen3.5:
    assert constrain("went on to sell more than five million albums",
                     "went on to sell more than five million albums worldwide, despite a Grammy nomination, why?"
                     ) == "went on to sell more than five million albums"  # continued the sentence
    assert constrain("seflino. joined by draig Riley (porousslon) and",
                     "setting, joined by Brian Riley (percussion) and"
                     ) == "setting, joined by draig Riley (percussion) and"  # swapped in a plausible name
    assert constrain("muslclanghip bohind some", "friendship behind some") == "muslclanghip behind some"


def test_constrain_accepts_real_fixes():
    assert constrain("There are few guilarlatg whose", "There are few guitarists whose") == "There are few guitarists whose"
    assert constrain("into on ntimate,strtppod-back", "into an intimate, stripped-back") == "into an intimate, stripped-back"


def test_respace():
    chars = [(c, 0.9, [[x, 0], [x + 10, 0], [x + 10, 10], [x, 10]]) for c, x in
             zip("expires2027", [0, 11, 22, 33, 44, 55, 66, 85, 96, 107, 118])]
    assert respace("expires2027", chars) == "expires 2027"
    assert respace("ID:Ontario at 10:00", None) == "ID: Ontario at 10:00"


def test_line_crop_straightens_and_enlarges():
    img = np.full((200, 400, 3), 255, np.uint8)
    crop = line_crop(img, ((10, 20), (210, 40), (208, 60), (8, 40)))
    assert crop.shape[0] >= 64 and crop.shape[1] > crop.shape[0]


class _Inner:
    def describe(self):
        return {"provider": "fake"}

    def read(self, page):
        return [OcrLine("Thnrg are fen", (0, 0, 100, 20), 0.6, ((0, 0), (100, 0), (100, 20), (0, 20))),
                OcrLine("Clear line", (0, 30, 100, 50), 0.99, ((0, 30), (100, 30), (100, 50), (0, 50)))]


def test_reread_only_doubtful_lines(tmp_path, monkeypatch):
    path = tmp_path / "p.png"
    cv2.imwrite(str(path), np.full((60, 120, 3), 255, np.uint8))
    asked = []

    def fake(self, crop, hint):
        asked.append(hint)
        return "There are few"
    monkeypatch.setattr(VlmReread, "_transcribe", fake)
    r = VlmReread(_Inner(), "http://x", "m")
    lines = r.read(Page("p0001", "p.png", 1, path, "", 120, 60))
    assert [l.text for l in lines] == ["There are few", "Clear line"] and asked == ["Thnrg are fen"]


def test_reread_goes_offline_quietly(tmp_path, monkeypatch):
    path = tmp_path / "p.png"
    cv2.imwrite(str(path), np.full((60, 120, 3), 255, np.uint8))

    def down(self, crop, hint):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(VlmReread, "_transcribe", down)
    r = VlmReread(_Inner(), "http://x", "m")
    page = Page("p0001", "p.png", 1, path, "", 120, 60)
    assert [l.text for l in r.read(page)] == ["Thnrg are fen", "Clear line"]
    assert r._offline


def test_constrain_case():
    assert constrain("Inducted Into Canada's Walk", "inducted into canada's walk") == "inducted into Canada's Walk"
    assert constrain("Induoted Into Canade's", "inducted into canada's") == "inducted into Canada's"
