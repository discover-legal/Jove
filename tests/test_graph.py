from jove.graph import build, cypher, dot, dot_full, field_name, obsidian, records, summary, tree, typeql

RESULT = [
    {"type": "job", "job_id": "j1", "tagger": "flat", "chooser": {"provider": "fake"}},
    {"type": "page", "page_id": "p0001", "source": "a.pdf", "source_page": 1, "sha256": "ab"},
    {"type": "node", "id": "p0001/b01", "kind": "block", "page_id": "p0001", "bbox": [0, 0, 9, 9], "text": "Name: Jane Doe"},
    {"type": "node", "id": "p0001/b01/l01", "kind": "line", "page_id": "p0001", "bbox": [0, 0, 9, 9], "text": "Name: Jane Doe"},
    {"type": "node", "id": "p0001/v001", "kind": "span", "page_id": "p0001", "bbox": [5, 0, 9, 9], "text": "Jane Doe"},
    {"type": "edge", "rel": "contains", "from": "p0001", "to": "p0001/b01"},
    {"type": "edge", "rel": "contains", "from": "p0001/b01", "to": "p0001/b01/l01"},
    {"type": "edge", "rel": "contains", "from": "p0001/b01/l01", "to": "p0001/v001"},
    {"type": "edge", "rel": "tagged", "from": "p0001/b01/l01", "to": "#label", "rank": 1, "score": 0.8},
    {"type": "edge", "rel": "tagged", "from": "p0001/v001", "to": "parties[].name", "rank": 1, "score": 0.9,
     "instances": {"parties[]": "p0001/b01/l01"}, "owner": {"owner": "client", "p": 1.0, "was": "parties[].name"}},
    {"type": "edge", "rel": "tagged", "from": "p0001/v001", "to": "matter.title", "rank": 2, "score": 0.05},
]


def test_build():
    g = build([RESULT, [{**r, "job_id": "j2"} if r["type"] == "job" else r for r in RESULT]])
    assert g.nodes["j1/p0001/v001"]["text"] == "Jane Doe"
    assert ("j1/p0001", "contains", "j1/p0001/b01/l01", {}) in g.edges  # a one-line block is skipped
    assert ("field:parties[].name", "part_of", "field:parties[]", {}) in g.edges  # the schema tree
    assert "field:#label" not in g.nodes and "field:matter.title" not in g.nodes  # no captions, rank 1 only
    [tag] = [t for t in g.tags if t["text"] == "j1/p0001/v001"]
    assert tag["items"] == ["j1/item:parties[]@p0001/b01/l01"] and tag["owner"] == "j1/owner:client"
    assert sum(n["label"] == "Field" for n in g.nodes.values()) == 2  # shared by both documents
    assert sum(n["label"] == "Owner" for n in g.nodes.values()) == 2  # each document's own client
    everything = build([RESULT], everything=True)
    assert "field:matter.title" in everything.nodes and "j1/p0001/b01" in everything.nodes


def test_names_are_for_people():
    g = build([RESULT, [{**r, "job_id": "j2"} if r["type"] == "job" else r for r in RESULT]])
    names = {nid: n["name"] for nid, n in g.nodes.items()}
    assert names["field:parties[].name"] == "Party › Name"
    assert names["j1/item:parties[]@p0001/b01/l01"] == "Party 1 – Jane Doe"
    assert names["j1/owner:client"] == "Our client" and names["j2/owner:client"] == "Our client (j2)"
    assert names["j1/p0001"] == "Page 1 · a.pdf"
    assert len(set(names.values())) == len(names)  # unique: they are Obsidian file names
    assert field_name("contact.addresses[].postal_code") == "Contact › Address › Postal code"
    assert field_name("matter.reference_numbers[]") == "Matter › Reference number"


def test_tree_and_summary():
    g = build([RESULT])
    assert "    Name: Jane Doe\n      Jane Doe  → Party › Name, Party 1, Our client\n" in tree(g)
    assert "  Party 1  (Our client)\n    Name                 Jane Doe" in summary(g) and "p1 l1" in summary(g)
    [rec] = records(g)
    assert (rec["title"], rec["rows"][0]["value"], rec["rows"][0]["line"]) == ("Party 1", "Jane Doe", 1)


def test_renderings(tmp_path):
    g = build([RESULT])
    lines = cypher(g)
    assert any('name: "Jane Doe (text)"' in l and ":Span" in l for l in lines)  # the person keeps the plain name
    assert any(':Entity' in l and 'name: "Jane Doe"' in l and 'kind: "person"' in l for l in lines)
    assert any("CREATE (a)-[:NAMES {written: \"Jane Doe\"}]->(b)" in l for l in lines)
    assert any("CREATE (a)-[:TAGGED {rank: 1, score: 0.9}]->(b)" in s for s in lines)
    schema, queries = typeql(g)
    assert "relation mention" in schema and any("mention (text: " in q for q in queries)
    assert "relation tag" in schema and 'has name "Party 1 – Jane Doe"' in queries[0]
    assert any("tag (value: " in q and "item: " in q and "owner: " in q and "has rank 1" in q for q in queries)
    cards = dot(g)
    assert "<B>Party 1</B>" in cards and "<B>Jane Doe</B>" in cards and '"j1/p0001":l1:e -> ' in cards
    drawn = dot_full(g)
    assert drawn.startswith("digraph") and 'tag0 -> "field:parties[].name"' in drawn and 'tag0 -> "j1/owner:client"' in drawn
    assert any('value_name: "Jane Doe"' in l and 'owner: "client"' in l for l in lines)  # values on the item
    obsidian(g, tmp_path)
    item = (tmp_path / "Records" / "Party 1 – Jane Doe.md").read_text()
    assert 'Name: "Jane Doe"' in item and 'owner: "[[Our client]]"' in item  # values as properties
    assert "| Name | **Jane Doe** → [[Jane Doe]] | [[Name – Jane Doe\\|p1 l1]] |" in item  # linked to the line and the person
    assert (tmp_path / "Source" / "Name – Jane Doe.md").exists() and (tmp_path / "Schema" / "Party › Name.md").exists()
    assert '"search": "-path:Source -path:Schema"' in (tmp_path / ".obsidian" / "graph.json").read_text()
    assert "| [[j1]] | [[Party 1 – Jane Doe\\|Party 1]] | Name | Jane Doe | Our client |" in \
        (tmp_path / "People" / "Jane Doe.md").read_text()


def test_refuses_what_it_would_clobber(tmp_path):
    import pytest

    with pytest.raises(ValueError, match="share job id"):
        build([RESULT, RESULT])
    obsidian(build([RESULT]), tmp_path)
    with pytest.raises(SystemExit):  # it would rewrite the vault's folders
        obsidian(build([RESULT]), tmp_path)
    obsidian(build([RESULT]), tmp_path, replace=True)
