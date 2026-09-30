import json
from pathlib import Path

import pytest

pytest.importorskip("typesafe_sdk")
from typesafe_sdk import SystemOneResponse

from legaleyes.choose import NONE, Question
from legaleyes.jev import MAX_REQUEST_BYTES, MAX_STATE_QUESTION_BYTES, JevChooser, batches
from legaleyes.layout import build
from legaleyes.ocr import OcrLine
from legaleyes.schema import build_tree
from legaleyes.tagger import Tagger


class FakeClient:
    def __init__(self):
        self.calls = []

    def system_one(self, state, questions, model=None):
        self.calls.append(questions)
        answers = {}
        for key, q in questions.items():
            probs = {k: (0.8 if k == "o0" else 0.2 / (len(q.criteria) - 1)) for k in q.criteria}
            answers[key] = {"type": "choice", "choice": "o0", "confidence": 0.8, "probabilities": probs}
        return SystemOneResponse.model_validate({"model": "jev-1.13.0", "usage": {}, "answers": answers})


def q(text="x", options=3):
    opts = {f"o{i}": {"what": f"option {i}"} for i in range(options - 1)} | {NONE: {"what": "none"}}
    return Question(f"Text reads {text!r}", opts, text, "", {k: k for k in opts})


def test_choose_parses_sdk_answers():
    chooser = JevChooser(client=FakeClient())
    out = chooser.choose({"page": "p0001"}, {"a": q(), "b": q()})
    assert out["a"]["o0"] == pytest.approx(0.8) and sum(out["b"].values()) == pytest.approx(1)


def test_batches_respect_limits():
    qs = {f"k{i}": q("y" * 5000) for i in range(100)}
    out = batches({"page": "p"}, qs)
    assert len(out) > 1 and sum(len(b) for b in out) == 100
    for b in out:
        size = sum(len(json.dumps({"instructions": x.instructions, "criteria": x.options})) for x in b.values())
        assert size <= MAX_REQUEST_BYTES
    with pytest.raises(ValueError, match="too large"):
        batches({"page": "p"}, {"huge": q("z" * MAX_STATE_QUESTION_BYTES)})


def test_legal_schema_fan_out_fits_and_runs():
    tree = build_tree(json.loads((Path(__file__).parent.parent / "schemas" / "legal_intake.schema.json").read_text()))
    client = FakeClient()
    blocks = build("p0001", [OcrLine("Name: Jane Doe", (0, 0, 140, 18))])
    decisions = Tagger(tree, JevChooser(client=client)).tag({"p0001": blocks})
    assert decisions
    first = client.calls[0]
    assert len(first) == len(tree.internal_nodes())


def test_halves_batch_on_max_tokens_exceeded():
    class TooBig(FakeClient):
        def system_one(self, state, questions, model=None):
            if len(questions) > 2:
                raise RuntimeError('400 {"detail":{"error_type":"max_tokens_exceeded"}}')
            return super().system_one(state, questions, model)
    client = TooBig()
    out = JevChooser(client=client).choose({"page": "p"}, {f"k{i}": q() for i in range(5)})
    assert len(out) == 5 and all(len(c) <= 2 for c in client.calls)


def test_yes_no_parses_noul():
    from legaleyes.choose import YesNo

    class NoulClient:
        def system_one(self, state, questions, model=None):
            assert all(q.type == "noul" and set(q.criteria) == {"true", "false"} for q in questions.values())
            return SystemOneResponse.model_validate({"model": "jev-1.13.0", "usage": {},
                "answers": {k: {"type": "noul", "noul": 0.3} for k in questions}})

    out = JevChooser(client=NoulClient()).yes_no({"page": "p"}, {"g0": YesNo("split?", {"true": "y", "false": "n"}, "x")})
    assert out == {"g0": 0.3}
