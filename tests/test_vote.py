from jove.vote import back_numbers, decide, first_line, medoid


def test_first_line_drops_model_junk():
    assert first_line("SUNSET SESSIONS\n\n\n```markdown\n\nSUNSET") == "SUNSET SESSIONS"
    assert first_line("```\n**Smith & Co. LLP**\n```") == "Smith & Co. LLP"
    assert first_line("“Someday,” both") == '"Someday," both'


def test_agreement_takes_the_ocr_models_punctuation():
    assert decide("ABOUT THE ARTIST", "ABOUT THE ARTIST:") == "ABOUT THE ARTIST:"


def test_ocr_model_is_trusted_for_words():
    assert decide("Smin & Co, 14.P", "Smith & Co. LLP") == "Smith & Co. LLP"


def test_hallucinating_ocr_model_is_outvoted():
    got = decide("Thank you for supporting", "Visit our website today", tiebreak=lambda: "Thank you for supportimg")
    assert got == "Thank you for supporting"


def test_numbers_need_a_second_witness():
    calls = []

    def esc(answer):
        return lambda: calls.append(answer) or answer
    # glm says 1996, the escalation reader says 1980: nobody backs 1996, so OCR's 198o stays.
    assert decide("its 198o debut", "its 1996 debut", escalate=esc("its 1980 debut")) == "its 198o debut"
    # the escalation reader backs 1209 against OCR's 1299: accepted.
    assert decide("1299 Bay Suonet", "1209 Bay Street", escalate=esc("1209 Bay Street")) == "1209 Bay Street"
    # backed by OCR already: no escalation call.
    calls.clear()
    assert decide("paid $2,45O.00 now", "paid $2,450.00 now", escalate=esc("x")) == "paid $2,450.00 now" and not calls


def test_medoid_and_back_numbers():
    assert medoid(["abc", "abd", "xyz"]) == 0
    assert back_numbers("paid $2,480 now", ["paid $2,450 now"], "paid $2,450 now") == "paid $2,450 now"
    assert back_numbers("paid $2,480 now", ["paid $2,480 now"], "paid $2,450 now") == "paid $2,480 now"


def test_whole_page_fallback_when_detection_finds_nothing(tmp_path, monkeypatch):
    import cv2
    import numpy as np

    from jove.ingest import Page
    from jove.vote import LineVote

    class NoLines:
        def describe(self):
            return {}

        def read(self, page):
            return []

    path = tmp_path / "p.png"
    cv2.imwrite(str(path), np.full((400, 300, 3), 255, np.uint8))
    monkeypatch.setattr(LineVote, "_chat", lambda self, img, model, prompt, n: "Client meeting notes\n```markdown\nOct 2, 2024")
    lines = LineVote(NoLines()).read(Page("p0001", "p.png", 1, path, "", 300, 400))
    assert [l.text for l in lines] == ["Client meeting notes", "Oct 2, 2024"]
    assert lines[0].bbox[1] < lines[1].bbox[1] and lines[0].quad is None


def test_tidy_and_partial_number_revert():
    from jove.vote import tidy
    assert tidy("( gone ) and someday , both") == "(gone) and someday, both"
    # same-length groups: only the unbacked number reverts, the words keep their fixes
    assert back_numbers("474 Lakeview Newmarket ON", ["4n4 Lakoview Newmnarkel ON"], "4n4 Lakoview Newmnarkel ON") \
        == "4n4 Lakeview Newmarket ON"
