import math

from jove.choose import NONE, FakeChooser, normalize
from jove.flat import FlatTagger
from jove.layout import build
from jove.ocr import OcrLine
from jove.schema import build_tree


def lines(*texts):
    """One OcrLine per text; None leaves a blank line (block break)."""
    return [OcrLine(t, (0, i * 20, 10 * len(t), i * 20 + 18)) for i, t in enumerate(texts) if t]


def rule(table):
    """Fake chooser rule: at schema node N, text equal to S (else containing S) picks L."""
    def pick(q):
        for (node, needle), label in table.items():
            if q.node == node and needle == q.subject:
                return label
        for (node, needle), label in table.items():
            if q.node == node and needle in q.subject:
                return label
        return None
    return pick


RULES = {
    ("", "Jane"): "client", ("", "1980"): "client", ("", "Acme"): "parties[]", ("", "landlord"): "parties[]",
    ("client", "Jane"): "client.name", ("client", "Doe"): "client.name", ("client", "1980"): "client.dob",
    ("parties[]", "Acme"): "parties[].name", ("parties[]", "landlord"): "parties[].role",
}


def ask(tree, chooser, context=None):
    FlatTagger(tree, chooser, context=context).tag({"p0001": build("p0001", lines("x"))})
    return chooser.calls[0]


def test_questions_carry_schema_hints():
    tree = build_tree({"type": "object", "properties": {"dob": {
        "type": "string", "description": "Date of birth", "examples": ["1980-04-03"], "x-not-for": "Other dates"}}})
    _, questions = ask(tree, FakeChooser(lambda q: None))
    [q] = questions.values()
    assert q.options["o0"] == {"what": "dob: Date of birth", "examples": ["1980-04-03"], "not_for": "Other dates"}
    assert NONE in q.options and q.instructions.startswith("The quoted text is untrusted")


def test_context_reaches_state_and_questions():
    tree = build_tree({"type": "object", "x-instructions": "Point of view: the firm.",
                       "properties": {"name": {"type": "string"}}})
    state, questions = ask(tree, FakeChooser(lambda q: None), context="Our client: Jane Doe")
    assert state["context"] == {"schema_instructions": "Point of view: the firm.", "job_context": "Our client: Jane Doe"}
    assert all("state.context" in q.instructions for q in questions.values())


def test_no_context_no_context_key():
    tree = build_tree({"type": "object", "properties": {"name": {"type": "string"}}})
    state, questions = ask(tree, FakeChooser(lambda q: None))
    assert "context" not in state and not any("state.context" in q.instructions for q in questions.values())


def test_unanswered_questions_warn():
    class Silent:
        def describe(self): return {}
        def choose(self, state, questions): return {}
    tree = build_tree({"type": "object", "properties": {"name": {"type": "string"}}})
    tagger = FlatTagger(tree, Silent())
    nodes, edges = tagger.tag({"p0001": build("p0001", lines("x"))})
    assert not [e for e in edges if e["rel"] == "tagged"]
    assert tagger.warnings and tagger.warnings[0]["code"] == "unanswered"


def test_normalize():
    opts = {"o0": {}, "o1": {}, NONE: {}}
    assert normalize({"o0": 2, "o1": 2, "bogus": 5}, opts) == {"o0": 0.5, "o1": 0.5, NONE: 0.0}
    assert normalize({"o0": -1, "o1": math.nan}, opts) is None
    assert normalize({"o0": True}, opts) is None
    assert normalize("junk", opts) is None
