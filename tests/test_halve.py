import json

from jove.choose import FakeChooser
from jove.ocr import FakeOcr
from jove.pipeline import run

from .conftest import png
from .test_pipeline import SCHEMA

OCR = FakeOcr({"p0001": ["CLIENT INTAKE", "", "Name: Jane Doe DOB: 1980", "", "Acme", "landlord"]})
VALUES = {"Jane Doe": "client.name", "1980": "client.dob", "Acme": "parties[].name", "landlord": "parties[].role",
          "Name:": "client#label", "DOB:": "client#label"}


def touched(text):
    """The values a chunk has any word of."""
    words = set(text.split())
    return [v for v in VALUES if words & set(v.split())]


def gate(q):
    text = q.instructions
    if "cut in two" in text:  # halve while a chunk touches two or more values, as Jev would
        return 0.9 if len(touched(q.subject)) > 1 else 0.1
    if "same person, organization" in text:
        return 0.9 if "'Acme'" in text and "'landlord'" in text else 0.1
    return 0.1


def choice(q):
    [value] = touched(q.subject) or [None]
    if value is None:
        return None
    path = VALUES[value]
    node = path.split("#")[0] if "#" in path else path.rsplit(".", 1)[0]
    return path if q.node == node else node if q.node == "" else None


def test_halving_stops_where_jev_says_one_value(tmp_path):
    chooser = FakeChooser(choice, gate=gate)
    out = run("j", tmp_path, [("b.png", png())], SCHEMA, OCR, chooser, tagger="halve")
    recs = [json.loads(l) for l in out.read_text().splitlines()]
    nodes = {r["id"]: r for r in recs if r["type"] == "node"}
    tags = {nodes[e["from"]]["text"]: (e["to"], e["instances"]) for e in recs
            if e["type"] == "edge" and e["rel"] == "tagged" and e["rank"] == 1}
    assert tags["Jane Doe"][0] == "client.name" and tags["1980"][0] == "client.dob"  # "Jane | Doe" cut, rejoined
    assert tags["Name:"][0] == tags["DOB:"][0] == "client#label"
    assert tags["CLIENT INTAKE"][0] == "#none"
    assert tags["Acme"][0] == "parties[].name" and tags["landlord"][0] == "parties[].role"
    assert tags["Acme"][1] == tags["landlord"][1]  # one party
    halving = [q for call in chooser.gate_calls for q in call.values() if "cut in two" in q.instructions]
    assert halving[0].subject.startswith("CLIENT INTAKE") and halving[0].subject.endswith("landlord")  # whole doc first
    assert recs[0]["tagger"] == "halve"
