from jove.graph import build, cypher, obsidian, tree, typeql

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
    assert g.nodes["j1/p0001/v001"] == {"label": "Span", "text": "Jane Doe", "bbox": [5, 0, 9, 9]}
    assert ("j1", "contains", "j1/p0001", {}) in g.edges and ("j1/p0001", "contains", "j1/p0001/b01", {}) in g.edges
    assert ("field:parties[].name", "part_of", "field:parties[]", {}) in g.edges  # the schema tree
    assert "field:#label" not in g.nodes  # labels and nones aren't values
    [tag] = [t for t in g.tags if t["rank"] == 1 and t["text"] == "j1/p0001/v001"]
    assert tag["items"] == ["j1/item:parties[]@p0001/b01/l01"] and tag["owner"] == "j1/owner:client"
    assert sum(n["label"] == "Field" for n in g.nodes.values()) == 4  # shared by both documents
    assert sum(n["label"] == "Owner" for n in g.nodes.values()) == 2  # each document's own client


def test_tree():
    text = tree(build([RESULT]))
    assert "      line p0001/b01/l01: Name: Jane Doe\n        span p0001/v001: Jane Doe  → parties[].name (0.90) [client]" in text


def test_renderings(tmp_path):
    g = build([RESULT])
    lines = cypher(g)
    assert 'CREATE (:Span {id: "j1/p0001/v001", text: "Jane Doe", bbox: [5, 0, 9, 9]})' in lines
    assert any("CREATE (a)-[:TAGGED {rank: 1, score: 0.9}]->(b)" in s for s in lines)
    schema, queries = typeql(g)
    assert "relation tag" in schema
    assert any("tag (value: " in q and "item: " in q and "owner: " in q and "has rank 1" in q for q in queries)
    obsidian(g, tmp_path)
    note = (tmp_path / "Span" / "Jane Doe – j1 p0001 v001.md").read_text()
    assert "- tagged [[parties-list.name]] (rank 1, score 0.9)" in note and "- owned_by [[j1 client]] (p 1.0)" in note
    assert (tmp_path / "Field" / "parties-list.md").exists() and (tmp_path / ".obsidian" / "graph.json").exists()
