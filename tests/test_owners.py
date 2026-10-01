from legaleyes.choose import FakeChooser
from legaleyes.owners import Owners, _lists
from legaleyes.schema import build_tree

SCHEMA = {
    "type": "object",
    "x-owners": {
        "client": {"description": "our client", "name": ["client.name"], "contact": "client.contact"},
        "party": {"description": "another party", "name": ["parties[].name"], "contact": "parties[].contact"},
        "issuer": {"description": "the document's issuer", "name": ["issuer.name"], "contact": "issuer.contact"},
    },
    "properties": {
        "client": {"type": "object", "properties": {
            "name": {"type": "string"},
            "contact": {"type": "object", "properties": {"phone": {"type": "string"}, "city": {"type": "string"}}}}},
        "parties": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"},
            "contact": {"type": "object", "properties": {"phone": {"type": "string"}, "city": {"type": "string"}}}}}},
        "issuer": {"type": "object", "properties": {
            "name": {"type": "string"},
            "contact": {"type": "object", "properties": {"phone": {"type": "string"}, "city": {"type": "string"}}}}},
        "amount": {"type": "string"},
    },
}


def graph():
    nodes = [{"type": "node", "id": f"p0001/b01/l0{i}", "kind": "line", "page_id": "p0001", "text": t}
             for i, t in enumerate(["Tel: 416-326-3333", "Jane Doe", "$40", "Acme Ltd"], start=1)]
    edges = [{"type": "edge", "rel": "tagged", "from": n["id"], "to": to, "rank": 1, "score": 0.9, "instances": {}}
             for n, to in zip(nodes, ["client.contact.phone", "client.name", "amount", "client.name"])]
    return nodes, edges


def owner_rule(q):
    if "416-326-3333" in q.subject:
        return "issuer"
    if "Acme" in q.subject:
        return "party"
    return "client"


def test_values_move_to_their_owner():
    nodes, edges = graph()
    chooser = FakeChooser(owner_rule)
    out = Owners(SCHEMA, build_tree(SCHEMA), chooser).apply(nodes, edges)
    tags = {e["from"][-3:]: (e["to"], e.get("owner", {}).get("owner"), e["instances"]) for e in out}
    assert tags["l01"][:2] == ("issuer.contact.phone", "issuer")  # letterhead phone was not the client's
    assert tags["l02"][:2] == ("client.name", "client")  # stays
    assert tags["l03"] == ("amount", None, {})  # not an owned field: not asked
    assert tags["l04"][:2] == ("parties[].name", "party") and tags["l04"][2] == {"parties[]": "p0001/b01/l04"}
    [(state, questions)] = chooser.calls
    assert len(questions) == 3 and set(next(iter(questions.values())).options) == {"client", "party", "issuer", "none"}


def test_no_owners_no_questions():
    nodes, edges = graph()
    chooser = FakeChooser(owner_rule)
    schema = {k: v for k, v in SCHEMA.items() if k != "x-owners"}
    assert Owners(schema, build_tree(schema), chooser).apply(nodes, edges) == edges and not chooser.calls


def test_lists():
    assert _lists("parties[].contact.phones[]") == ["parties[]", "parties[].contact.phones[]"]
    assert _lists("client.name") == []
