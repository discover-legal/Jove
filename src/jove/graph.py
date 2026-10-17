"""Results as graphs: the document tree, what each piece of text gives, and whose it is.

    uv run python -m jove.graph RESULT.jsonl [...] --to tree
    uv run python -m jove.graph RESULT.jsonl [...] --to obsidian --out vault/
    uv run python -m jove.graph RESULT.jsonl [...] --to cypher --out graph.cypher
    uv run python -m jove.graph RESULT.jsonl [...] --to typeql --out typedb/
    uv run python -m jove.graph RESULT.jsonl [...] --load falkor://localhost:6379/jove
    uv run python -m jove.graph RESULT.jsonl [...] --load typedb://localhost:1729/jove

One model, rendered three ways:

- tree / obsidian / cypher: a plain graph. Document → page → block → line → span
  (contains); text → schema field (tagged, every rank); text → list item (in);
  item → its list field (item_of); field → parent field (part_of); text → owner.
- typeql: a hypergraph. A tag is one n-ary relation that links the text, the
  field, the list items it belongs to and its owner, so "Jane Doe is the client's
  name in party #1" is one fact rather than four edges.

Several results make one graph: text, pages and items are per document, schema
fields are shared, so documents meet at the fields they fill.
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from .choose import NONE, is_value


@dataclass
class Graph:
    nodes: dict[str, dict] = field(default_factory=dict)  # id -> {"label": ..., props}
    edges: list[tuple[str, str, str, dict]] = field(default_factory=list)  # (from, rel, to, props)
    tags: list[dict] = field(default_factory=list)  # hyperedges: text, field, items, owner, props

    def add(self, nid: str, label: str, **props) -> str:
        self.nodes.setdefault(nid, {"label": label, **{k: v for k, v in props.items() if v is not None}})
        return nid

    def link(self, a: str, rel: str, b: str, **props) -> None:
        self.edges.append((a, rel, b, {k: v for k, v in props.items() if v is not None}))


def build(results: list[list[dict]]) -> Graph:
    g = Graph()
    for records in results:
        job = next(r for r in records if r["type"] == "job")
        jid = job["job_id"]
        local = lambda i: f"{jid}/{i}"  # noqa: E731 — page and node ids repeat across documents
        g.add(jid, "Document", created_at=job.get("created_at"), tagger=job.get("tagger"),
              chooser=(job.get("chooser") or {}).get("provider"), context=job.get("context"))
        for r in records:
            if r["type"] == "page":
                g.add(local(r["page_id"]), "Page", source=r["source"], page=r["source_page"], sha256=r["sha256"])
                g.link(jid, "contains", local(r["page_id"]))
            elif r["type"] == "node":
                g.add(local(r["id"]), r["kind"].capitalize(), text=r["text"], bbox=r["bbox"])
        for e in (r for r in records if r["type"] == "edge"):
            if e["rel"] == "contains":
                g.link(local(e["from"]), "contains", local(e["to"]))
            elif e["rel"] == "tagged" and is_value(e["to"]):
                text, path = local(e["from"]), _field(g, e["to"])
                g.link(text, "tagged", path, rank=e["rank"], score=e["score"])
                if e["rank"] != 1:
                    g.tags.append({"text": text, "field": path, "items": [], "owner": None,
                                   "rank": e["rank"], "score": e["score"]})
                    continue
                items = _items(g, jid, e.get("instances") or {})
                for item in items[-1:]:
                    g.link(text, "in", item)
                owner, p = None, None
                if (o := e.get("owner")) and o["owner"] != NONE:
                    owner, p = f"{jid}/owner:{o['owner']}", o["p"]
                    if owner not in g.nodes:
                        g.link(g.add(owner, "Owner", name=o["owner"]), "of", jid)
                    g.link(text, "owned_by", owner, p=p)
                g.tags.append({"text": text, "field": path, "items": items, "owner": owner, "rank": 1,
                               "score": e["score"], "separation": e.get("separation"), "owner_p": p})
    return g


def _field(g: Graph, path: str) -> str:
    """The field's node, with its parents up to the schema root (part_of edges)."""
    nid = f"field:{path}"
    if nid not in g.nodes:
        g.add(nid, "Field", path=path, list=path.endswith("[]"))
        parent = _parent(path)
        if parent:
            g.link(nid, "part_of", _field(g, parent))
    return nid


def _parent(path: str) -> str:
    """contact.addresses[].full -> contact.addresses[] -> contact -> ''."""
    return path.rsplit(".", 1)[0] if "." in path else ""


def _items(g: Graph, jid: str, instances: dict) -> list[str]:
    """List items, outermost first; an item inside another item's list is part_of it."""
    out = []
    for arr in sorted(instances, key=len):
        item = f"{jid}/item:{arr}@{instances[arr]}"
        if item not in g.nodes:
            g.link(g.add(item, "Item", list=arr, anchor=instances[arr]), "item_of", _field(g, arr))
            if out:
                g.link(item, "part_of", out[-1])
        out.append(item)
    return out


# ---------------------------------------------------------------------- tree

def tree(g: Graph) -> str:
    """The document tree as indented text, each piece of text with its rank-1 tag."""
    kids: dict[str, list[str]] = {}
    for a, rel, b, _ in g.edges:
        if rel == "contains":
            kids.setdefault(a, []).append(b)
    tag = {t["text"]: t for t in g.tags if t["rank"] == 1}
    lines: list[str] = []

    def walk(nid: str, depth: int) -> None:
        n = g.nodes[nid]
        what = n.get("text") or n.get("source") or nid
        line = f"{'  ' * depth}{n['label'].lower()} {nid.split('/', 1)[-1]}: {_clip(what, 70)}"
        if t := tag.get(nid):
            line += f"  → {t['field'][6:]} ({t['score']:.2f})"
            if t["owner"]:
                line += f" [{g.nodes[t['owner']]['name']}]"
        lines.append(line)
        for k in kids.get(nid, []):
            walk(k, depth + 1)

    for nid, n in g.nodes.items():
        if n["label"] == "Document":
            walk(nid, 0)
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ obsidian

COLOURS = {"Document": 0x2B44C7, "Page": 0x6B7280, "Block": 0x9CA3AF, "Line": 0x0F8FA8, "Span": 0x1F9D6B,
           "Segment": 0x1F9D6B, "Chunk": 0x1F9D6B, "Field": 0xD6452F, "Item": 0xE08A00, "Owner": 0x9B4FD0}


def obsidian(g: Graph, out: Path) -> None:
    """A vault: one note per node in a folder per label, edges as [[wikilinks]]."""
    names = {nid: _note_name(nid, n) for nid, n in g.nodes.items()}
    links: dict[str, list[str]] = {}
    for a, rel, b, props in g.edges:
        extra = ", ".join(f"{k} {v}" for k, v in props.items())
        links.setdefault(a, []).append(f"- {rel} [[{names[b]}]]" + (f" ({extra})" if extra else ""))
    for nid, n in g.nodes.items():
        folder = out / n["label"]
        folder.mkdir(parents=True, exist_ok=True)
        props = {"id": nid, **{k: v for k, v in n.items() if k != "label"}, "tags": [f"jove/{n['label'].lower()}"]}
        front = "\n".join(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in props.items())
        body = f"---\n{front}\n---\n\n"
        if n.get("text"):
            body += "> " + n["text"].replace("\n", "\n> ") + "\n\n"
        body += "\n".join(links.get(nid, [])) + "\n"
        (folder / f"{names[nid]}.md").write_text(body)
    settings = out / ".obsidian"
    settings.mkdir(exist_ok=True)
    groups = [{"query": f"path:{label}", "color": {"a": 1, "rgb": rgb}} for label, rgb in COLOURS.items()]
    (settings / "graph.json").write_text(json.dumps({"colorGroups": groups}, indent=2))


def _note_name(nid: str, n: dict) -> str:
    if n["label"] == "Field":
        name = n["path"].replace("[]", "-list")
    elif n["label"] == "Item":
        name = f"{nid.split('/item:')[0]} {n['list'].replace('[]', '-list')} @ {n['anchor']}"
    elif n["label"] == "Owner":
        name = nid.replace("/owner:", " ")
    elif n.get("text"):
        name = f"{_clip(n['text'], 40)} – {nid}"
    else:
        name = nid
    return re.sub(r'[\\/:*?"<>|#^\[\]\n]+', " ", name).strip()


# -------------------------------------------------------------------- cypher

def cypher(g: Graph) -> list[str]:
    """OpenCypher statements (FalkorDB, Neo4j, Memgraph): one per line, nodes then edges."""
    out = [f"CREATE INDEX FOR (n:{label}) ON (n.id)" for label in sorted({n["label"] for n in g.nodes.values()})]
    out += [f"CREATE (:{n['label']} {_props({'id': nid, **n})})" for nid, n in g.nodes.items()]
    for a, rel, b, props in g.edges:
        la, lb = g.nodes[a]["label"], g.nodes[b]["label"]
        out.append(f"MATCH (a:{la} {{id: {_lit(a)}}}), (b:{lb} {{id: {_lit(b)}}}) "
                   f"CREATE (a)-[:{rel.upper()} {_props(props)}]->(b)")
    return out


def _props(props: dict) -> str:
    return "{" + ", ".join(f"{k}: {_lit(v)}" for k, v in props.items() if k != "label") + "}"


def _lit(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, list):
        return "[" + ", ".join(_lit(x) for x in v) + "]"
    return json.dumps(v, ensure_ascii=False) if isinstance(v, str) else repr(v)


# -------------------------------------------------------------------- typeql

TYPEQL_SCHEMA = """define
  attribute node-id, value string;
  attribute text, value string;
  attribute bbox, value string;
  attribute path, value string;
  attribute name, value string;
  attribute source, value string;
  attribute sha256, value string;
  attribute page-number, value integer;
  attribute created-at, value string;
  attribute chooser, value string;
  attribute tagger, value string;
  attribute context, value string;
  attribute rank, value integer;
  attribute score, value double;
  attribute separation, value double;
  attribute owner-p, value double;

  entity node @abstract, owns node-id @key, plays containment:container, plays containment:part;
  entity document sub node, owns created-at, owns chooser, owns tagger, owns context, plays ownership:document;
  entity page sub node, owns source, owns sha256, owns page-number;
  entity region @abstract, sub node, owns text, owns bbox, plays tag:value;
  entity block sub region;
  entity line sub region;
  entity span sub region;
  entity segment sub region;
  entity chunk sub region;
  entity field sub node, owns path, plays tag:field, plays listing:list;
  entity item sub node, owns path, plays tag:item, plays listing:item;
  entity owner sub node, owns name, plays tag:owner, plays ownership:owner;

  # A tag is one hyperedge: the text, the field it gives, the list items it is part of, whose it is.
  relation tag, relates value, relates field, relates item @card(0..), relates owner,
    owns rank, owns score, owns separation, owns owner-p;
  # Trees: document > page > block > line > span; field > field; item > item.
  relation containment, relates container, relates part;
  relation listing, relates list, relates item;
  relation ownership, relates owner, relates document;
"""

_ENTITY = {"Document": "document", "Page": "page", "Block": "block", "Line": "line", "Span": "span",
           "Segment": "segment", "Chunk": "chunk", "Field": "field", "Item": "item", "Owner": "owner"}
_ATTR = {"text": "text", "bbox": "bbox", "path": "path", "name": "name", "source": "source", "sha256": "sha256",
         "page": "page-number", "created_at": "created-at", "chooser": "chooser", "tagger": "tagger",
         "context": "context", "list": "path"}


def typeql(g: Graph, batch: int = 100) -> tuple[str, list[str]]:
    """(schema, insert queries). Entities first, then binary relations, then the tag hyperedges."""
    queries: list[str] = []
    ents = []
    for nid, n in g.nodes.items():
        has = [f"has node-id {_lit(nid)}"]
        for k, v in n.items():
            if k in _ATTR and not (n["label"] == "Field" and k == "list"):
                has.append(f"has {_ATTR[k]} {_lit(','.join(map(str, v)) if k == 'bbox' else v)}")
        ents.append(f"$n{len(ents)} isa {_ENTITY[n['label']]}, " + ", ".join(has) + ";")
    queries += ["insert\n" + "\n".join(ents[i:i + batch]) for i in range(0, len(ents), batch)]

    rels = []
    for a, rel, b, props in g.edges:
        if rel == "contains" or (rel == "part_of"):
            rels.append(("containment", {"container": b if rel == "part_of" else a, "part": a if rel == "part_of" else b}, {}))
        elif rel == "item_of":
            rels.append(("listing", {"item": a, "list": b}, {}))
        elif rel == "of":
            rels.append(("ownership", {"owner": a, "document": b}, {}))
    for t in g.tags:
        players = {"value": [t["text"]], "field": [t["field"]], "item": t["items"], "owner": [t["owner"]] if t["owner"] else []}
        attrs = {"rank": t["rank"], "score": t["score"], "separation": t.get("separation"), "owner-p": t.get("owner_p")}
        rels.append(("tag", players, attrs))
    for i in range(0, len(rels), batch):
        queries.append(_relations(g, rels[i:i + batch]))
    return TYPEQL_SCHEMA, queries


def _relations(g: Graph, rels: list[tuple[str, dict, dict]]) -> str:
    match, insert, var = [], [], {}
    for _, players, _ in rels:
        for ids in players.values():
            for nid in [ids] if isinstance(ids, str) else ids:
                if nid not in var:
                    var[nid] = f"$v{len(var)}"
                    match.append(f"{var[nid]} isa {_ENTITY[g.nodes[nid]['label']]}, has node-id {_lit(nid)};")
    for kind, players, attrs in rels:
        roles = ", ".join(f"{role}: {var[nid]}" for role, ids in players.items()
                          for nid in ([ids] if isinstance(ids, str) else ids))
        has = "".join(f", has {k} {_lit(v)}" for k, v in attrs.items() if v is not None)
        insert.append(f"{kind} ({roles}){has};")
    return "match\n" + "\n".join(match) + "\ninsert\n" + "\n".join(insert)


# --------------------------------------------------------------------- load

def load_falkor(g: Graph, url: str) -> None:
    from falkordb import FalkorDB  # optional: uv sync --extra graph

    u = urlparse(url)
    db = FalkorDB(host=u.hostname or "localhost", port=u.port or 6379)
    name = u.path.strip("/") or "jove"
    if name in db.list_graphs():
        db.select_graph(name).delete()
    graph = db.select_graph(name)
    for q in cypher(g):
        graph.query(q)


def load_typedb(g: Graph, url: str) -> None:
    from typedb.driver import Credentials, DriverOptions, DriverTlsConfig, TransactionType, TypeDB

    u = urlparse(url)
    name = u.path.strip("/") or "jove"
    driver = TypeDB.driver(f"{u.hostname or 'localhost'}:{u.port or 1729}",
                           Credentials(u.username or "admin", u.password or "password"),
                           DriverOptions(DriverTlsConfig.disabled()))
    try:
        if driver.databases.contains(name):
            driver.databases.get(name).delete()
        driver.databases.create(name)
        schema, queries = typeql(g)
        with driver.transaction(name, TransactionType.SCHEMA) as tx:
            tx.query(schema).resolve()
            tx.commit()
        with driver.transaction(name, TransactionType.WRITE) as tx:
            for q in queries:
                tx.query(q).resolve()
            tx.commit()
    finally:
        driver.close()


def _clip(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m jove.graph", description=__doc__.split("\n\n")[0])
    ap.add_argument("results", nargs="+", type=Path, help="result.jsonl files (one per document)")
    ap.add_argument("--to", choices=["tree", "obsidian", "cypher", "typeql"])
    ap.add_argument("--out", type=Path, help="output file or folder (tree and cypher default to stdout)")
    ap.add_argument("--load", help="falkor://host:port/graph or typedb://[user:pass@]host:port/database")
    args = ap.parse_args(argv)
    if not args.to and not args.load:
        ap.error("give --to and/or --load")
    g = build([[json.loads(l) for l in p.read_text().splitlines() if l.strip()] for p in args.results])
    if args.to in ("tree", "cypher"):
        text = tree(g) if args.to == "tree" else "\n".join(cypher(g)) + "\n"
        args.out.write_text(text) if args.out else sys.stdout.write(text)
    elif args.to:
        if not args.out:
            ap.error(f"--to {args.to} needs --out DIR")
        if args.to == "obsidian":
            obsidian(g, args.out)
        else:
            schema, queries = typeql(g)
            args.out.mkdir(parents=True, exist_ok=True)
            (args.out / "schema.tql").write_text(schema)
            (args.out / "data.tql").write_text("\n\n".join(queries) + "\n")
    if args.load:
        scheme = urlparse(args.load).scheme
        (load_falkor if scheme == "falkor" else load_typedb if scheme == "typedb" else ap.error(scheme))(g, args.load)
    print(f"{len(g.nodes)} nodes, {len(g.edges)} edges, {len(g.tags)} tags", file=sys.stderr)


if __name__ == "__main__":
    main()
