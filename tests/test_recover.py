from legaleyes import recover
from legaleyes.ingest import Page
from legaleyes.ocr import FakeOcr, OcrLine
from legaleyes.recover import MissedLines, gap, missing, page_rows, parse_boxes


def line(text, y=0):
    return OcrLine(text, (0, y, 100, y + 20), 0.9)


def test_page_rows_drop_markup_and_loops():
    assert page_rows("**NOTICE**\nPEST BROADLEAF\nPEST BROADLEAF\n```\n---\n") == ["NOTICE", "PEST BROADLEAF"]


def test_missing_keeps_rows_with_a_word_found_nowhere():
    lines = [line("PRODUCT USED FIESTA"), line("FOR INFORMATION CONTACT", 30), line("Weed", 60)]
    rows = ["PRODUCT USED FIESTA", "CONTACT WEED MAN", "PRODUCT USED FIESTA.", "Oct 5 2026", "Weed"]
    assert missing(rows, lines) == ["CONTACT WEED MAN", "Oct 5 2026"]


def test_gap_reads_the_part_of_a_row_no_nearby_line_covers():
    contact = OcrLine("CONTACT", (100, 100, 300, 140), 0.9)
    logo = OcrLine("Weed", (400, 300, 500, 340), 0.9)  # far below: its "Weed" doesn't cover this row
    below = OcrLine("CALL COLLECT", (150, 125, 650, 160), 0.9)  # inside the band, but shares no word
    words, region = gap("CONTACT WEED MAN", (100, 110, 700, 125), [contact, logo, below], line_h=40)
    assert words == "WEED MAN"
    assert region[0] == 300 and region[2] == 700 and region[3] - region[1] == 60  # widened to 1.5 lines
    assert gap("CONTACT", (100, 110, 300, 125), [contact], line_h=40) is None


def test_parse_boxes_ignores_malformed_items():
    reply = '```json\n[{"text": "WEED MAN", "bbox_2d": [480, 560, 720, 590]}, {"text": "x"}, {"bbox_2d": [1,2,3,4]}]\n```'
    assert parse_boxes(reply) == [{"text": "WEED MAN", "bbox_2d": [480, 560, 720, 590]}]
    assert parse_boxes("I could not find them") == []


def _page(tmp_path):
    import cv2
    import numpy as np

    path = tmp_path / "p.png"
    cv2.imwrite(str(path), np.full((1000, 1000, 3), 255, np.uint8))
    return Page("p0001", "p.png", 1, path, "0" * 64, 1000, 1000)


def test_adds_a_located_line_only_when_the_crop_reads_back_the_same(tmp_path, monkeypatch):
    def fake_chat(base_url, img, model, prompt, max_tokens, timeout):
        if model == "locator":
            return ('[{"text": "WEED MAN", "bbox_2d": [400, 500, 700, 540]},'
                    ' {"text": "Call 1-800-555-0199", "bbox_2d": [100, 800, 500, 840]}]')
        if img.shape[0] == 1000:  # the whole page
            return "PRODUCT USED FIESTA\nWEED MAN\nCall 1-800-555-0199"
        # crops: the first reads back, the second disagrees on a digit
        return "WEED MAN" if img.shape[1] < 350 else "Call 1-800-555-0198"  # boxes are 300 and 400 wide

    monkeypatch.setattr(recover, "chat", fake_chat)
    ocr = MissedLines(FakeOcr({"p0001": ["PRODUCT USED FIESTA"]}), reader="reader", locator="locator")
    got = ocr.read(_page(tmp_path))
    assert [l.text for l in got] == ["PRODUCT USED FIESTA", "WEED MAN"]
    assert (got[1].bbox[0], got[1].bbox[2]) == (400.0, 700.0)


def test_nothing_missing_means_no_locator_call(tmp_path, monkeypatch):
    calls = []

    def fake_chat(base_url, img, model, prompt, max_tokens, timeout):
        calls.append(model)
        return "PRODUCT USED FIESTA"

    monkeypatch.setattr(recover, "chat", fake_chat)
    ocr = MissedLines(FakeOcr({"p0001": ["PRODUCT USED FIESTA"]}), reader="reader", locator="locator")
    assert [l.text for l in ocr.read(_page(tmp_path))] == ["PRODUCT USED FIESTA"]
    assert calls == ["reader"]


def test_release_unloads_every_model_once(monkeypatch):
    from legaleyes import vote
    from legaleyes.ocr import TextLayerFirst
    from legaleyes.vote import LineVote

    unloaded = []
    monkeypatch.setattr(vote, "unload", lambda base_url, models: unloaded.append(list(models)))
    monkeypatch.setattr(recover, "unload", lambda base_url, models: unloaded.append(list(models)))
    stack = TextLayerFirst(MissedLines(LineVote(FakeOcr({}), reader="glm-ocr", tiebreaker="granite",
                                                escalation="qwen"), reader="glm-ocr", locator="qwen3-vl"))
    stack.release()
    assert unloaded == [["glm-ocr", "qwen3-vl"], ["glm-ocr", "granite", "qwen"]]
