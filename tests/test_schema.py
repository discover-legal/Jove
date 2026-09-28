import json
from pathlib import Path

import pytest

from legaleyes.schema import SchemaError, build_tree, flatten


def test_flatten_paths(schema):
    fields = flatten(schema)
    assert list(fields) == ["client.name", "client.dob", "claim_amount", "parties[].name", "parties[].role"]
    assert fields["client.name"].description == "Client's full legal name"
    assert fields["claim_amount"].type == "number"


def test_array_of_scalars():
    fields = flatten({"type": "object", "properties": {"tags": {"type": "array", "items": {"type": "string"}}}})
    assert list(fields) == ["tags[]"]


@pytest.mark.parametrize("bad", [
    {"type": "array", "items": {"type": "string"}},
    {"type": "object", "properties": {}},
    {"type": "object", "properties": {"x": {"type": "null"}}},
    {"type": "object", "properties": {"a.b": {"type": "string"}}},
    {"type": "object", "properties": {"xs": {"type": "array"}}},
])
def test_rejects(bad):
    with pytest.raises(SchemaError):
        flatten(bad)


def test_tree(schema):
    tree = build_tree(schema)
    assert [n.path for n in tree.internal_nodes()] == ["", "client", "parties[]"]
    parties = tree.children[2]
    assert parties.is_array_item and parties.name == "parties"
    assert [c.path for c in parties.children] == ["parties[].name", "parties[].role"]


def test_array_description_describes_items():
    tree = build_tree({"type": "object", "properties": {"xs": {
        "type": "array", "description": "The xs", "x-not-for": "ys", "items": {"type": "string"}}}})
    [xs] = tree.children
    assert (xs.path, xs.description, xs.not_for, xs.leaf) == ("xs[]", "The xs", "ys", True)


def test_bundled_legal_intake_schema():
    path = Path(__file__).parent.parent / "schemas" / "legal_intake.schema.json"
    fields = flatten(json.loads(path.read_text()))
    assert len(fields) > 100
    assert fields["parties[].contact.address.postal_code"].type == "string"
    dob = fields["client.person.date_of_birth"]
    assert dob.examples and dob.not_for
    assert all(f.description for f in fields.values())
