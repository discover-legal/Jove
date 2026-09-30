import json

from legaleyes.choose import FakeChooser
from legaleyes.ocr import FakeOcr
from legaleyes.pipeline import run

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
