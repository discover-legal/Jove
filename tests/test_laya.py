from legaleyes.choose import Question, YesNo
from legaleyes.laya import LayaChooser, render, split_question
from legaleyes.tagger import UNTRUSTED


class FakeRouter:
    def __init__(self):
        self.calls = []

    def predict(self, state, questions, **kw):
        self.calls.append((state, questions))
        q = questions["q"]
        if q["type"] == "noul":
            return {"answers": {"q": {"type": "noul", "noul": 0.7}}, "usage": {"input_tokens": 10}}
        probs = {k: (0.8 if k == "o0" else 0.2 / (len(q["criteria"]) - 1)) for k in q["criteria"]}
        return {"answers": {"q": {"type": "choice", "probabilities": probs}}, "usage": {"input_tokens": 10}}


def test_split_moves_evidence_out_of_the_instructions():
    ask, rest = split_question(UNTRUSTED + "Text p1/l2 (line) reads: 'DATE: 2022'. Neighbouring lines: 'a | b'."
                               " Which option of the schema does this text give a value for? If several, pick one.")
    assert ask == "Which option of the schema does this text give a value for?"
    assert "DATE: 2022" in rest and "Which option" not in rest and "untrusted" not in rest


def test_question_quoting_text_stays_whole():
    ask, rest = split_question("Is 'Applicant' a role?")
    assert ask == "Is 'Applicant' a role?" and rest == ""


def test_render_keeps_name_and_short_description():
    assert render({"what": "court: the name of the court", "examples": ["ONSC"]}) == "court: the name of the court"
    assert render("plain") == "plain"


def test_chooser_puts_evidence_in_state_and_normalizes():
    router = FakeRouter()
    chooser = LayaChooser(router=router)
    q = Question("Text reads: 'X'. Which option does this text give a value for?",
                 {"o0": {"what": "a: b"}, "none": {"what": "none: c"}}, "X", "", {"o0": "a", "none": "a#none"})
    out = chooser.choose({"context": "ctx", "lines": []}, {"k": q})
    assert out["k"]["o0"] == 0.8
    state, questions = router.calls[0]
    assert state == {"context": "ctx", "text": "Text reads: 'X'."}
    assert questions["q"]["instructions"] == "Which option does this text give a value for?"
    yes = chooser.yes_no({}, {"y": YesNo("Line: 'X'. Does it continue?", {"true": "yes", "false": "no"}, "X")})
    assert yes == {"y": 0.7} and chooser.requests == 2
