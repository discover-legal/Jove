import json

from jove.choose import FakeChooser
from jove.ocr import FakeOcr
from jove.pipeline import run

from .conftest import png
from .test_pipeline import SCHEMA
from .test_tagger import RULES, rule

OCR = FakeOcr({"p0001": ["INTAKE", "Acme", "landlord", "Bolt", "tenant", "Name: Jane Doe DOB: 1980"]})
LINE = "Name: Jane Doe DOB: 1980"


def gate(q):
    text = q.instructions
    if "hold two or more separate values" in text:
        return 0.9 if LINE in text else 0.1
    if "same person, organization" in text:  # same item: Acme+landlord, Bolt+tenant
        pairs = [("'Acme'", "'landlord'"), ("'Bolt'", "'tenant'")]
        return 0.9 if any(a in text and b in text for a, b in pairs) else 0.1
    if "continue the same single value" in text:
        return 0.9 if "'Jane'" in text and "'Doe'" in text else 0.1
    return 0.1


def records(tmp_path):
    chooser = FakeChooser(rule(RULES | {("", "Bolt"): "parties[]", ("", "tenant"): "parties[]", ("", "Doe"): "client",
                                        ("parties[]", "Bolt"): "parties[].name",
                                        ("parties[]", "tenant"): "parties[].role"}), gate=gate)
    out = run("j", tmp_path, [("b.png", png())], SCHEMA, OCR, chooser, tagger="flat")
    return [json.loads(l) for l in out.read_text().splitlines()]


def test_flat_tags_splits_and_groups(tmp_path):
    recs = records(tmp_path)
    nodes = {r["id"]: r for r in recs if r["type"] == "node"}
    tags = {nodes[e["from"]]["text"]: (e["to"], e.get("instances")) for e in recs
            if e["type"] == "edge" and e["rel"] == "tagged" and e["rank"] == 1}
    assert tags["Jane Doe"][0] == "client.name"  # words of the split line, merged by "continues"
    assert tags["1980"][0] == "client.dob"
    assert tags["INTAKE"][0] == "#none"
    acme, landlord = tags["Acme"][1]["parties[]"], tags["landlord"][1]["parties[]"]
    bolt, tenant = tags["Bolt"][1]["parties[]"], tags["tenant"][1]["parties[]"]
    assert acme == landlord and bolt == tenant and acme != bolt
    assert recs[0]["tagger"] == "flat"


def test_two_parties_on_one_line_are_two_items(tmp_path):
    line = "Acme landlord Bolt tenant"
    ocr = FakeOcr({"p0001": [line]})

    def g(q):
        return 0.9 if "hold two or more separate values" in q.instructions else 0.1

    rules = RULES | {("", "Bolt"): "parties[]", ("", "tenant"): "parties[]", ("", line): "parties[]",
                     ("parties[]", "Bolt"): "parties[].name", ("parties[]", "tenant"): "parties[].role"}
    out = run("j", tmp_path, [("b.png", png())], SCHEMA, ocr, FakeChooser(rule(rules), gate=g), tagger="flat")
    recs = [json.loads(l) for l in out.read_text().splitlines()]
    nodes = {r["id"]: r for r in recs if r["type"] == "node"}
    inst = {nodes[e["from"]]["text"]: e["instances"]["parties[]"] for e in recs
            if e["type"] == "edge" and e["rel"] == "tagged" and e["rank"] == 1 and "parties[]" in (e.get("instances") or {})}
    assert inst["Acme"] == inst["landlord"] and inst["Bolt"] == inst["tenant"] and inst["Acme"] != inst["Bolt"]


FLYER = {
    "type": "object",
    "properties": {
        "description": {"type": "array", "items": {"type": "string"}},
        "performers": {"type": "array", "x-mentions": True, "description": "People featured",
                       "items": {"type": "object", "properties": {"name": {"type": "string"}, "role": {"type": "string"}}}},
    },
}


def test_mentions_inside_prose(tmp_path):
    line = "joined by Craig Riley (percussion) and Mike Oresco (bass)."
    ocr = FakeOcr({"p0001": [line]})
    names, roles = {"Craig", "Riley", "Mike", "Oresco"}, {"(percussion)", "(bass)."}

    def choice(q):
        if q.node == "" and q.subject == line:
            return "description[]"
        if q.node == "performers[]":
            return "performers[].name" if q.subject in names else "performers[].role" if q.subject in roles else None
        return None

    def gate(q):
        return 0.9 if "mention a performers" in q.instructions else 0.1

    out = run("j", tmp_path, [("b.png", png())], FLYER, ocr, FakeChooser(choice, gate=gate), tagger="flat")
    recs = [json.loads(l) for l in out.read_text().splitlines()]
    nodes = {r["id"]: r for r in recs if r["type"] == "node"}
    tags = [(nodes[e["from"]]["text"], e["to"], e.get("instances"), e.get("mention")) for e in recs
            if e["type"] == "edge" and e["rel"] == "tagged" and e["rank"] == 1]
    assert (line, "description[]", {}, None) in [(t, p, i, m) for t, p, i, m in tags]  # the line keeps its tag
    found = {t: (p, i["performers[]"]) for t, p, i, m in tags if m}
    assert found["Craig Riley"][0] == "performers[].name" and found["(percussion)"][0] == "performers[].role"
    assert found["Craig Riley"][1] == found["(percussion)"][1] != found["Mike Oresco"][1] == found["(bass)."][1]
