import json

from jove.choose import FakeChooser
from jove.ocr import FakeOcr
from jove.pipeline import run

from .conftest import png
from .test_pipeline import SCHEMA
from .test_questions import RULES, rule

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
    out = run("j", tmp_path, [("b.png", png())], SCHEMA, OCR, chooser)
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


def test_two_parties_on_one_line_are_two_items(tmp_path):
    line = "Acme landlord Bolt tenant"
    ocr = FakeOcr({"p0001": [line]})

    def g(q):
        return 0.9 if "hold two or more separate values" in q.instructions else 0.1

    rules = RULES | {("", "Bolt"): "parties[]", ("", "tenant"): "parties[]", ("", line): "parties[]",
                     ("parties[]", "Bolt"): "parties[].name", ("parties[]", "tenant"): "parties[].role"}
    out = run("j", tmp_path, [("b.png", png())], SCHEMA, ocr, FakeChooser(rule(rules), gate=g))
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
    line = "joined by Dana Okafor (percussion) and Theo Marchetti (bass)."
    ocr = FakeOcr({"p0001": [line]})
    names, roles = {"Dana", "Okafor", "Theo", "Marchetti"}, {"(percussion)", "(bass)."}

    def choice(q):
        if q.node == "" and q.subject == line:
            return "description[]"
        if q.node == "performers[]":
            return "performers[].name" if q.subject in names else "performers[].role" if q.subject in roles else None
        return None

    def gate(q):
        return 0.9 if "mention a performers" in q.instructions else 0.1

    out = run("j", tmp_path, [("b.png", png())], FLYER, ocr, FakeChooser(choice, gate=gate))
    recs = [json.loads(l) for l in out.read_text().splitlines()]
    nodes = {r["id"]: r for r in recs if r["type"] == "node"}
    tags = [(nodes[e["from"]]["text"], e["to"], e.get("instances"), e.get("mention")) for e in recs
            if e["type"] == "edge" and e["rel"] == "tagged" and e["rank"] == 1]
    assert (line, "description[]", {}, None) in [(t, p, i, m) for t, p, i, m in tags]  # the line keeps its tag
    found = {t: (p, i["performers[]"]) for t, p, i, m in tags if m}
    assert found["Dana Okafor"][0] == "performers[].name" and found["(percussion)"][0] == "performers[].role"
    assert found["Dana Okafor"][1] == found["(percussion)"][1] != found["Theo Marchetti"][1] == found["(bass)."][1]


AFFIDAVIT = [
    "1. I am a manager with the city, one of the defendants in this action, and I have",
    "personal knowledge of the matters in this affidavit, except where I say other-",
    "wise.",
    "2. I have conducted a diligent search of the records of the city and made enquiries",
    "of others in order to make this affidavit.",
]


def test_prose_lines_join_into_paragraphs():
    from jove import layout
    from jove.flat import paragraphs
    from jove.ocr import OcrLine

    lines = [OcrLine(t, (0, i * 20, 820, i * 20 + 18) if len(t) > 50 else (0, i * 20, 10 * len(t), i * 20 + 18))
             for i, t in enumerate(AFFIDAVIT)]
    [block] = layout.build("p0001", lines)
    units = paragraphs(block)
    assert [u.kind for u in units] == ["paragraph", "paragraph"]  # a short line ends one, "2." starts the next
    assert units[0].text.endswith("except where I say otherwise.")  # hyphenation undone
    assert [l.text for l in units[1].children] == AFFIDAVIT[3:]


def test_form_rows_and_captions_stay_lines():
    from jove import layout
    from jove.flat import paragraphs
    from jove.ocr import OcrLine

    form = ["Tenant's name: Jane Doe Unit: 4 Street: King Street West", "Landlord's name: Acme Inc. Phone: 416-555-0100"]
    caption = ["JANE DOE and JOHN DOE", "Plaintiffs"]
    for rows in (form, caption):
        [block] = layout.build("p0001", [OcrLine(t, (0, i * 20, 600, i * 20 + 18)) for i, t in enumerate(rows)])
        assert [u.kind for u in paragraphs(block)] == ["line"] * len(rows)


def test_a_paragraph_is_tagged_once_and_mentions_inside_it_are_found(tmp_path):
    ocr = FakeOcr({"p0001": ["joined on stage tonight by our good friends for one evening only, by",
                             "Dana Okafor, on percussion, and Theo Marchetti, on bass, for the whole show."]})
    asked = []

    def choice(q):  # mentions come back as clauses, not scattered words
        asked.append(q.subject)
        if q.node == "" and q.subject.startswith("joined on stage"):
            return "description[]"
        if q.node == "performers[]":
            return {"by Dana Okafor,": "performers[].name", "and Theo Marchetti,": "performers[].name",
                    "on percussion,": "performers[].role", "on bass,": "performers[].role"}.get(q.subject)
        return None

    def gate(q):
        return 0.9 if "mention a performers" in q.instructions else 0.1

    out = run("j", tmp_path, [("b.png", png())], FLYER, ocr, FakeChooser(choice, gate=gate))
    recs = [json.loads(l) for l in out.read_text().splitlines()]
    nodes = {r["id"]: r for r in recs if r["type"] == "node"}
    [para] = [n for n in nodes.values() if n["kind"] == "paragraph"]
    tags = [(nodes[e["from"]]["text"], e["to"], e.get("mention")) for e in recs
            if e["type"] == "edge" and e["rel"] == "tagged" and e["rank"] == 1]
    assert (para["text"], "description[]", None) in tags
    assert not any(s in asked for s in (ocr._lines["p0001"]))  # the lines themselves were never asked
    found = {t: p for t, p, m in tags if m}
    assert found == {"by Dana Okafor,": "performers[].name", "on percussion,": "performers[].role",
                     "and Theo Marchetti,": "performers[].name", "on bass,": "performers[].role"}


def test_a_short_line_mid_sentence_runs_on():
    from jove import layout
    from jove.flat import paragraphs
    from jove.ocr import OcrLine

    jurat = ["SWORN before me at the City of Toronto, in the", "Province of Ontario, on",  # a filled-in blank
             "December 8, 2020.", "Commissioner for Taking Affidavits."]
    [block] = layout.build("p0001", [OcrLine(t, (0, i * 20, 10 * len(t), i * 20 + 18)) for i, t in enumerate(jurat)])
    units = paragraphs(block)
    assert [u.kind for u in units] == ["paragraph", "line"]
    assert units[0].text == " ".join(jurat[:3])


def test_a_paragraph_tagged_inside_a_mentions_field_is_still_searched(tmp_path):
    ocr = FakeOcr({"p0001": ["tonight on stage for one evening only, a special show with our friends:",
                             "Dana Okafor, on percussion, and the whole band for the whole show."]})

    def choice(q):
        paragraph = q.subject.startswith("tonight on stage") and q.subject.endswith("show.")
        if q.node == "" and paragraph:
            return "performers[]"  # the whole paragraph is about performers...
        if q.node == "performers[]":
            if paragraph:
                return "performers[].role"
            return "performers[].name" if q.subject.startswith("Dana Okafor") else None
        return None

    def gate(q):
        return 0.9 if "mention a performers" in q.instructions else 0.1

    out = run("j", tmp_path, [("b.png", png())], FLYER, ocr, FakeChooser(choice, gate=gate))
    recs = [json.loads(l) for l in out.read_text().splitlines()]
    nodes = {r["id"]: r for r in recs if r["type"] == "node"}
    found = {nodes[e["from"]]["text"]: e["to"] for e in recs
             if e["type"] == "edge" and e["rel"] == "tagged" and e.get("mention")}
    assert found == {"Dana Okafor,": "performers[].name"}  # ...and still yields the names inside it


def test_paragraph_pieces_keep_money_and_dates_whole():
    from jove.flat import _pieces
    from jove.layout import DocNode

    text = ("Rent of $2,450 for each of July and August remains unpaid, for a total of $4,900.00. "
            "Unless paid by September 30, 2024, our client will apply.")
    words = [DocNode(f"w{i}", "word", "p", (0, 0, 1, 1), t) for i, t in enumerate(text.split())]
    para = DocNode("p", "paragraph", "p", (0, 0, 1, 1), text, [DocNode("l", "line", "p", (0, 0, 1, 1), text, words)])
    assert [c.text for c in _pieces(para)] == [
        "Rent of", "$2,450", "for each of July and August remains unpaid,", "for a total of", "$4,900.00.",
        "Unless paid by", "September 30, 2024,", "our client will apply."]


def test_a_mention_of_a_numeric_field_needs_a_digit(tmp_path):
    schema = {"type": "object", "properties": {
        "description": {"type": "array", "items": {"type": "string"}},
        "amounts": {"type": "array", "x-mentions": True, "items": {"type": "object", "properties": {
            "amount": {"type": "string", "examples": ["$1,200.00", "CAD 45,000"]}}}}}}
    ocr = FakeOcr({"p0001": ["In response, the ministry said that the whole fee of the request was",
                             "$120.00, which it would waive for the appellant in this appeal."]})

    def choice(q):
        if q.node == "":
            return "description[]"
        return "amounts[].amount" if q.node == "amounts[]" else None  # every clause, as Clef did

    def gate(q):
        return 0.9 if "mention a amounts" in q.instructions else 0.1

    out = run("j", tmp_path, [("b.png", png())], schema, ocr, FakeChooser(choice, gate=gate))
    recs = [json.loads(l) for l in out.read_text().splitlines()]
    nodes = {r["id"]: r for r in recs if r["type"] == "node"}
    found = [nodes[e["from"]]["text"] for e in recs if e["type"] == "edge" and e["rel"] == "tagged" and e.get("mention")]
    assert found == ["$120.00,"]
