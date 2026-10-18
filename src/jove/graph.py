"""Results as graphs: the document tree, what each piece of text gives, and whose it is.

    uv run python -m jove.graph RESULT.jsonl [...] --to summary
    uv run python -m jove.graph RESULT.jsonl [...] --to tree
    uv run python -m jove.graph RESULT.jsonl [...] --to obsidian --out vault/
    uv run python -m jove.graph RESULT.jsonl [...] --to dot | dot -Tsvg > graph.svg
    uv run python -m jove.graph RESULT.jsonl [...] --to cypher --out graph.cypher
    uv run python -m jove.graph RESULT.jsonl [...] --to typeql --out typedb/
    uv run python -m jove.graph RESULT.jsonl [...] --load falkor://localhost:6379/jove
    uv run python -m jove.graph RESULT.jsonl [...] --load typedb://localhost:1729/jove

One model, rendered three ways:

- tree / obsidian / cypher / dot: a plain graph. Document → page → block → line → span
  (contains); text → schema field (tagged, every rank); text → list item (in);
  item → its list field (item_of); field → parent field (part_of); text → owner.
- typeql: a hypergraph. A tag is one n-ary relation that links the text, the
  field, the list items it belongs to and its owner, so "Jane Doe is the client's
  name in party #1" is one fact rather than four edges.

Several results make one graph: text, pages and items are per document, schema
fields are shared, so documents meet at the fields they fill.

Every node gets a readable name ("Party › Date of birth", "Party 1 – Jane Doe",
"Our client", "Page 1 · scan.pdf"), used by all renderers. By default the graph
keeps what a person needs: rank-1 tags, values rather than the captions around
them ("Full name:"), and blocks only when they group several lines. --all keeps
everything.
"""

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from . import entities as er
from .choose import NONE, is_value


@dataclass
class Graph:
    nodes: dict[str, dict] = field(default_factory=dict)  # id -> {"label": ..., props}
    edges: list[tuple[str, str, str, dict]] = field(default_factory=list)  # (from, rel, to, props)
    tags: list[dict] = field(default_factory=list)  # hyperedges: text, field, items, owner, props
    nested: set[tuple[str, str]] = field(default_factory=set)  # (item, parent item) already linked
    entities: list[dict] = field(default_factory=list)  # jove.entities groups, across documents

    def add(self, nid: str, label: str, **props) -> str:
        self.nodes.setdefault(nid, {"label": label, **{k: v for k, v in props.items() if v is not None}})
        return nid

    def link(self, a: str, rel: str, b: str, **props) -> None:
        self.edges.append((a, rel, b, {k: v for k, v in props.items() if v is not None}))


def build(results: list[list[dict]], everything: bool = False) -> Graph:
    g = Graph()
    for records in results:
        job = next((r for r in records if r["type"] == "job"), None)
        if job is None:
            raise ValueError("not a Jove result: no job record")
        jid = job["job_id"]
        if jid in g.nodes:
            raise ValueError(f"two results share job id {jid!r} (the same document from two runs?)")
        local = lambda i: f"{jid}/{i}"  # noqa: E731 — page and node ids repeat across documents
        source = ", ".join(i["filename"] for i in job.get("inputs", [])) or None
        g.add(jid, "Document", source=source, created_at=job.get("created_at"), tagger=job.get("tagger"),
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
                        g.link(g.add(owner, "Owner", key=o["owner"]), "of", jid)
                    g.link(text, "owned_by", owner, p=p)
                g.tags.append({"text": text, "field": path, "items": items, "owner": owner, "rank": 1,
                               "score": e["score"], "separation": e.get("separation"), "owner_p": p})
    if not everything:
        _prune(g)
    _name(g)
    _resolve(g)
    return g


def _resolve(g: Graph) -> None:
    """Entity nodes: the people and organisations named, each linked from the text naming it."""
    recs = records(g)
    for rec in recs:
        rec["owner_key"] = g.nodes[rec["owner"]]["key"] if rec["owner"] else None
    found = er.mentions(recs)
    for m in found:
        m["doc_name"] = g.nodes[m["doc"]]["name"]
    g.entities = er.resolve(found)
    owners = {n["name"]: nid for nid, n in g.nodes.items()}
    taken = set(owners)
    for i, e in enumerate(g.entities, start=1):
        e["id"] = nid = f"entity:{i}"
        name = _safe(e["name"])
        clash = owners.get(name)
        if clash and g.nodes[clash]["label"] in ("Line", "Block", "Span", "Segment", "Chunk"):
            g.nodes[clash]["name"] = other = _safe(f"{name} (text)")  # the entity keeps the plain name
            taken.add(other)
            taken.discard(name)
        k = 2
        while name in taken:
            name, k = f"{_safe(e['name'])} {k}", k + 1
        taken.add(name)
        g.add(nid, "Entity", kind=e["kind"], aliases=e["aliases"], flags=e["flags"])
        g.nodes[nid]["name"] = name
        for m in e["mentions"]:
            g.link(m["text"], "names", nid, written=m["as"], how=m["how"] or None)


def _prune(g: Graph) -> None:
    """Keep what a person needs: rank-1 tags, tagged spans, blocks of several lines, used fields."""
    g.tags = [t for t in g.tags if t["rank"] == 1]
    tagged = {t["text"] for t in g.tags}
    drop = {nid for nid, n in g.nodes.items() if n["label"] in ("Span", "Segment") and nid not in tagged}
    kids: dict[str, list[str]] = {}
    for a, rel, b, _ in g.edges:
        if rel == "contains":
            kids.setdefault(a, []).append(b)
    parent = {b: a for a, rel, b, _ in g.edges if rel == "contains"}
    lone = {nid for nid, n in g.nodes.items() if n["label"] == "Block" and len(kids.get(nid, [])) == 1}
    edges = []
    for a, rel, b, props in g.edges:
        if a in drop or b in drop or (rel == "tagged" and props.get("rank", 1) != 1) or b in lone:
            continue
        edges.append((parent[a], rel, b, props) if a in lone else (a, rel, b, props))
    g.edges = edges
    used = {t["field"] for t in g.tags} | {f"field:{g.nodes[i]['list']}" for t in g.tags for i in t["items"]}
    for f in list(used):
        while (f := _parent(f[6:])) and (f := f"field:{f}") not in used:
            used.add(f)
    drop |= lone | {nid for nid, n in g.nodes.items() if n["label"] == "Field" and nid not in used}
    g.nodes = {nid: n for nid, n in g.nodes.items() if nid not in drop}
    g.edges = [e for e in g.edges if e[0] in g.nodes and e[2] in g.nodes]


# Owner keys as people read them; any other key is shown capitalised.
OWNER_NAMES = {"client": "Our client", "party": "Other party", "issuer": "Issuer", "property": "The property"}
# Fields that name a list item, best first: "Party 1 – Jane Doe".
TITLE_FIELDS = ("name", "title", "full", "document_type", "type", "description", "number")


def _name(g: Graph) -> None:
    """A readable, unique name on every node, used by every renderer."""
    docs = {nid: _clip(n.get("source") or nid, 60) for nid, n in g.nodes.items() if n["label"] == "Document"}
    titles: dict[str, str] = {}
    for t in g.tags:
        if t["items"]:
            leaf = t["field"].rsplit(".", 1)[-1].removesuffix("[]")
            rank = TITLE_FIELDS.index(leaf) if leaf in TITLE_FIELDS else len(TITLE_FIELDS)
            best = titles.get(t["items"][-1])
            if best is None or rank < best[0]:
                titles[t["items"][-1]] = (rank, uncaptioned(g.nodes[t["text"]]["text"]))
    counters: dict[tuple[str, str], int] = {}
    seen: dict[str, int] = {}
    for nid, n in g.nodes.items():
        doc = nid.split("/", 1)[0]
        kind = n["label"]
        if kind == "Document":
            name = docs[nid]
        elif kind == "Page":
            name = f"Page {n['page']} · {n['source']}"
        elif kind == "Field":
            name = field_name(n["path"])
        elif kind == "Item":
            k = counters[(doc, n["list"])] = counters.get((doc, n["list"]), 0) + 1
            name = f"{_words(n['list'].rsplit('.', 1)[-1])} {k}"
            if nid in titles:
                name += f" – {_clip(titles[nid][1], 40)}"
        elif kind == "Owner":
            name = OWNER_NAMES.get(n["key"], n["key"].replace("_", " ").capitalize())
        else:
            name = _clip(n["text"], 60)
        name = _safe(name)
        if name in seen and kind not in ("Field", "Document"):
            name = _safe(f"{name} ({docs.get(doc, doc)})") if len(docs) > 1 else name
        if name in seen:
            seen[name] += 1
            name = f"{name} {seen[name]}"
        seen.setdefault(name, 1)
        n["name"] = name


def uncaptioned(text: str) -> str:
    """For display: "Amount claimed: $2,450.00" -> "$2,450.00". The tag still covers the whole line."""
    return _CAPTION.sub("", text, count=1) or text


_CAPTION = re.compile(r"^[A-Za-z][\w .'/()&-]{0,30}:\s+(?=\S)")


def field_name(path: str) -> str:
    """parties[].date_of_birth -> "Party › Date of birth"; contact.phones[] -> "Contact › Phone"."""
    return " › ".join(_words(p) for p in path.split("."))


def _words(key: str) -> str:
    plural = key.endswith("[]")
    word = key.removesuffix("[]").replace("_", " ")
    if plural:
        last = word.rsplit(" ", 1)[-1]
        stem = (last[:-3] + "y" if last.endswith("ies") else last[:-2]
                if last.endswith(("sses", "xes", "ches", "shes")) else last[:-1] if last.endswith("s") else last)
        word = word[: len(word) - len(last)] + stem
    return word[:1].upper() + word[1:]


def _safe(name: str) -> str:
    """Usable as a file name and a wikilink: no / \\ : * ? " < > | # ^ [ ]."""
    name = re.sub(r"\s*:\s*", " – ", name)
    return re.sub(r"\s+", " ", re.sub(r'[\\/*?"<>|#^\[\]]+', " ", name)).strip(" –") or "untitled"


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
        if out and (item, out[-1]) not in g.nested:  # a side may sit under more than one proceeding
            g.nested.add((item, out[-1]))
            g.link(item, "part_of", out[-1])
        out.append(item)
    return out


# ---------------------------------------------------------------------- tree

def tree(g: Graph) -> str:
    """The document tree as indented text, each value with what it is and whose."""
    kids: dict[str, list[str]] = {}
    for a, rel, b, _ in g.edges:
        if rel == "contains":
            kids.setdefault(a, []).append(b)
    tag = {t["text"]: t for t in g.tags if t["rank"] == 1}
    lines: list[str] = []

    def walk(nid: str, depth: int) -> None:
        n = g.nodes[nid]
        line = f"{'  ' * depth}{n['name'] if n['label'] in ('Document', 'Page') else _clip(n['text'], 70)}"
        if t := tag.get(nid):
            line += f"  → {_what(g, t)}"
        lines.append(line)
        for k in kids.get(nid, []):
            walk(k, depth + 1)

    for nid, n in g.nodes.items():
        if n["label"] == "Document":
            walk(nid, 0)
    return "\n".join(lines) + "\n"


def _what(g: Graph, t: dict) -> str:
    """"Party › Name, Party 1, Our client" — plus "(unsure 0.47)" below LOW."""
    out = g.nodes[t["field"]]["name"]
    if t["items"]:
        out += f", {g.nodes[t['items'][-1]]['name'].split(' – ')[0]}"
    if t["owner"]:
        out += f", {g.nodes[t['owner']]['name']}"
    return out + (f" (unsure {t['score']:.2f})" if t["score"] < LOW else "")


LOW = 0.7  # tags scoring below this are marked unsure


# ------------------------------------------------------------------- records

def clean(text: str) -> str:
    """A value as a person writes it down: no caption, wrapping brackets or trailing comma."""
    text = " ".join(uncaptioned(text).split())
    if text[:1] in "([" and text[-1:] in ")]":
        text = text[1:-1]
    return text.rstrip(",;") or text


def records(g: Graph) -> list[dict]:
    """The values grouped as a person reads them: one record per list item (Party 1, Address 1)
    and one per section outside lists (Matter, Contact), each row a field, its value and where
    it was read. Records come in reading order, each document's together."""
    parent = {b: a for a, rel, b, _ in g.edges if rel == "contains"}
    where: dict[str, tuple[int, int, str]] = {}  # line or chunk id -> (page number, line number, id)
    counts: dict[str, int] = {}
    for nid, n in g.nodes.items():
        if n["label"] in ("Line", "Chunk"):
            page = parent.get(nid)
            while page and g.nodes[page]["label"] != "Page":
                page = parent.get(page)
            counts[page] = counts.get(page, 0) + 1
            where[nid] = (g.nodes[page]["page"] if page else 0, counts[page], nid)

    def source(text: str) -> tuple[int, int, str]:
        n = text
        while n is not None and n not in where:
            n = parent.get(n)
        return where.get(n, (0, 0, text))

    out: dict[str, dict] = {}
    for t in g.tags:
        if t["rank"] != 1:
            continue
        doc = t["text"].split("/", 1)[0]
        path = g.nodes[t["field"]]["path"]
        if t["items"]:
            key = t["items"][-1]
            base = g.nodes[key]["list"]
            title, name = g.nodes[key]["name"].split(" – ")[0], g.nodes[key]["name"]
        else:
            base = path.split(".")[0]
            key, title = f"{doc}/section:{base}", _words(base)
            name = title
        rest = path[len(base):].lstrip(".") or path.rsplit(".", 1)[-1]
        page, line, line_id = source(t["text"])
        rec = out.setdefault(key, {"key": key, "doc": doc, "title": title, "name": name, "list": base,
                                   "rows": [], "owners": []})
        rec["rows"].append({"field": field_name(rest), "path": path, "value": clean(g.nodes[t["text"]]["text"]),
                            "raw": g.nodes[t["text"]]["text"], "score": t["score"], "page": page, "line": line,
                            "line_id": line_id, "text": t["text"]})
        if t["owner"]:
            rec["owners"].append(t["owner"])
    for rec in out.values():
        o = rec.pop("owners")
        rec["owner"] = max(set(o), key=o.count) if o else None
        rec["first"] = min((r["page"], r["line"]) for r in rec["rows"])
    docs = list(dict.fromkeys(n for n, d in g.nodes.items() if d["label"] == "Document"))
    return sorted(out.values(), key=lambda r: (docs.index(r["doc"]), r["first"]))


def summary(g: Graph) -> str:
    """The records as a person would list them, each value with where to check it."""
    out: list[str] = []
    recs = records(g)
    for doc, d in g.nodes.items():
        if d["label"] != "Document":
            continue
        out.append(d["name"])
        for rec in (r for r in recs if r["doc"] == doc):
            owner = f"  ({g.nodes[rec['owner']]['name']})" if rec["owner"] else ""
            out.append(f"  {rec['title']}{owner}")
            for r in rec["rows"]:
                unsure = f"  ? {r['score']:.2f}" if r["score"] < LOW else ""
                out.append(f"    {r['field']:<20} {_clip(r['value'], 60):<60}  p{r['page']} l{r['line']}{unsure}")
        out.append("")
    return "\n".join(out + people(g))


def people(g: Graph) -> list[str]:
    """Who is named, across the documents: each entity, where it is mentioned and as what,
    then its flags (conflict check, facts that disagree). Lone fragments come last."""
    whole = [e for e in g.entities if e["kind"] != "partial"]
    parts = [e for e in g.entities if e["kind"] == "partial"]
    if not g.entities:
        return []
    out = ["People and organisations"]
    for e in whole + parts:
        docs = len({m["doc"] for m in e["mentions"]})
        out.append(f"  {g.nodes[e['id']]['name']}  ({e['kind']}, {len(e['mentions'])} mention"
                   f"{'s' if len(e['mentions']) != 1 else ''}{f' in {docs} documents' if docs > 1 else ''})")
        for flag in e["flags"]:
            out.append(f"    ⚠ {flag}")
        for m in e["mentions"]:
            who = f", {OWNER_NAMES.get(m['owner'], m['owner'])}" if m["owner"] else ""
            how = f"  [{m['how']}]" if m["how"] else ""
            out.append(f"    {_clip(m['doc_name'], 24):<24} {m['title']} › {m['field']}{who}: “{m['as']}”"
                       f"  p{m['page']} l{m['line']}{how}")
    return out + [""]


# ------------------------------------------------------------------ obsidian

COLOURS = {"Document": 0x2B44C7, "Page": 0x6B7280, "Block": 0x9CA3AF, "Line": 0x0F8FA8, "Span": 0x1F9D6B,
           "Segment": 0x1F9D6B, "Chunk": 0x1F9D6B, "Field": 0xD6452F, "Item": 0xE08A00, "Owner": 0x9B4FD0,
           "Entity": 0xBE185D}
# Owner keys -> (strong, pale) colours; anything else is grey.
OWNER_COLOURS = {"client": ("#7C3AED", "#F3EEFF"), "party": ("#C2410C", "#FFF1E8"),
                 "issuer": ("#0F766E", "#E6F6F4"), "property": ("#15803D", "#EAF7EE")}
GREY = ("#4B5563", "#F3F4F6")

# How each edge reads to a person, from its source note.
REL_WORDS = {"contains": "includes", "tagged": "is", "in": "about", "item_of": "one of",
             "owned_by": "belongs to", "of": "in", "part_of": "under", "names": "names"}
# Vault folders: what a person reads at the top, provenance and schema out of the way.
FOLDERS = {"Document": "Documents", "Item": "Records", "Owner": "Owners", "Field": "Schema", "Entity": "People",
           "Page": "Source", "Block": "Source", "Line": "Source", "Chunk": "Source"}


def obsidian(g: Graph, out: Path, replace: bool = False) -> None:
    """A vault for people. Each record (Party 1, Matter, Address 1) is a note whose properties
    and table hold its values, each value linked to the source line it was read from. Each
    document has a home note with all its records. Pages, lines and the schema sit in Source/
    and Schema/, which the graph view hides, so the graph shows records, owners and documents."""
    ours = [out / f for f in set(FOLDERS.values()) if (out / f).exists()]
    if ours and not replace:
        raise SystemExit(f"{out} already holds an export; pass --replace to rewrite it")
    for folder in ours:  # only the folders this writes, so stale notes don't linger
        shutil.rmtree(folder)
    recs = records(g)
    names = {nid: _note(n["name"]) for nid, n in g.nodes.items()}
    for rec in recs:  # sections outside lists are records too; name them per document if needed
        if rec["key"] not in names:
            names[rec["key"]] = _unique(rec["name"], names, g.nodes[rec["doc"]]["name"])

    named: dict[str, list[str]] = {}  # text id -> the entities it names
    for e in g.entities:
        for m in e["mentions"]:
            if names[e["id"]] not in named.setdefault(m["text"], []):
                named[m["text"]].append(names[e["id"]])

    def value(x: dict) -> str:
        who = named.get(x["text"], [])
        return f"**{_cell(x['value'])}**" + (" → " + ", ".join(f"[[{w}]]" for w in who) if who else "")

    def write(nid: str, kind: str, front: dict, body: str) -> None:
        folder = out / FOLDERS[kind]
        folder.mkdir(parents=True, exist_ok=True)
        props = {**{k: v for k, v in front.items() if v not in (None, "", [])}, "tags": [f"jove/{kind.lower()}"]}
        text = "\n".join(f"{_yaml_key(k)}: {json.dumps(v, ensure_ascii=False)}" for k, v in props.items())
        (folder / f"{names[nid]}.md").write_text(f"---\n{text}\n---\n\n{body}")

    for rec in recs:
        owner = rec["owner"]
        values = {}
        for r in rec["rows"]:
            values.setdefault(r["field"], r["value"])
        rows = "\n".join(f"| {r['field']} | {value(r)} | [[{names[r['line_id']]}\\|p{r['page']} l{r['line']}]] |"
                         f" {'⚠ ' if r['score'] < LOW else ''}{r['score']:.2f} |" for r in rec["rows"])
        body = (f"{'Belongs to [[' + names[owner] + ']] · ' if owner else ''}In [[{names[rec['doc']]}]]\n\n"
                f"| Field | Value | Read from | Score |\n|---|---|---|---|\n{rows}\n")
        fields = sorted({f"[[{names[t]}]]" for t in (f"field:{r['path']}" for r in rec["rows"]) if t in names})
        body += "\nFields: " + ", ".join(fields) + "\n"
        write(rec["key"], "Item", {**values, "record": rec["title"], "owner": f"[[{names[owner]}]]" if owner else None,
                                   "document": f"[[{names[rec['doc']]}]]"}, body)

    for nid, n in g.nodes.items():
        kind = n["label"]
        if kind in ("Item", "Span", "Segment"):
            continue  # items are written as records; values live in their records' tables
        if kind == "Document":
            mine = [r for r in recs if r["doc"] == nid]
            body = f"Read from {n.get('source') or nid}. Values marked ⚠ are unsure: check them against the source.\n"
            for r in mine:
                who = f" · {names[r['owner']]}" if r["owner"] else ""
                body += f"\n### [[{names[r['key']]}|{r['title']}]]{who}\n\n| Field | Value | Read from |\n|---|---|---|\n"
                body += "\n".join(f"| {x['field']} | {'⚠ ' if x['score'] < LOW else ''}{value(x)} |"
                                   f" [[{names[x['line_id']]}\\|p{x['page']} l{x['line']}]] |" for x in r["rows"]) + "\n"
            write(nid, kind, {"source": n.get("source"), "created": n.get("created_at"), "context": n.get("context")}, body)
        elif kind == "Entity":
            e = next(x for x in g.entities if x["id"] == nid)
            body = "".join(f"> [!warning] {f}\n" for f in e["flags"]) + ("\n" if e["flags"] else "")
            body += "| Document | Record | Field | Written | Whose | Read from | Why the same |\n|---|---|---|---|---|---|---|\n"
            body += "\n".join(f"| [[{names[m['doc']]}]] | [[{names[m['record']]}\\|{m['title']}]] | {m['field']} |"
                               f" {_cell(m['as'])} | {OWNER_NAMES.get(m['owner'], m['owner'] or '')} |"
                               f" [[{names[m['line_id']]}\\|p{m['page']} l{m['line']}]] | {m['how']} |"
                               for m in e["mentions"]) + "\n"
            write(nid, kind, {"kind": e["kind"], "aliases": e["aliases"],
                              "documents": sorted({f"[[{names[m['doc']]}]]" for m in e["mentions"]}),
                              "flags": e["flags"]}, body)
        elif kind == "Owner":
            mine = [r for r in recs if r["owner"] == nid]
            write(nid, kind, {"document": f"[[{names[nid.split('/', 1)[0]]}]]"},
                  "\n".join(f"- [[{names[r['key']]}]]" for r in mine) + "\n")
        elif kind == "Field":
            parent = _parent(n["path"])
            write(nid, kind, {"path": n["path"]}, f"Under [[{names['field:' + parent]}]]\n" if parent else "")
        else:  # Page, Block, Line, Chunk: the source, for checking a value against the scan
            up = next((a for a, rel, b, _ in g.edges if rel == "contains" and b == nid), None)
            body = ("> " + n["text"].replace("\n", "\n> ") + "\n\n" if n.get("text") else "") + \
                   (f"In [[{names[up]}]]\n" if up else "")
            write(nid, kind, {"page": n.get("page"), "source": n.get("source")}, body)

    settings = out / ".obsidian"
    settings.mkdir(exist_ok=True)
    groups = [{"query": f"path:Records [\"owner\":{json.dumps(names[o])}]",
               "color": {"a": 1, "rgb": int(OWNER_COLOURS.get(g.nodes[o]["key"], GREY)[0][1:], 16)}}
              for o in {r["owner"] for r in recs if r["owner"]}]
    groups += [{"query": "path:Records", "color": {"a": 1, "rgb": int(GREY[0][1:], 16)}},
               {"query": "path:Owners", "color": {"a": 1, "rgb": 0x9B4FD0}},
               {"query": "path:People", "color": {"a": 1, "rgb": COLOURS["Entity"]}},
               {"query": "path:Documents", "color": {"a": 1, "rgb": 0x2B44C7}}]
    (settings / "graph.json").write_text(json.dumps({
        "search": "-path:Source -path:Schema", "showTags": False, "showOrphans": False, "colorGroups": groups,
        "textFadeMultiplier": -1, "nodeSizeMultiplier": 1.3, "lineSizeMultiplier": 0.6,
        "linkDistance": 120, "repelStrength": 15}, indent=2))


def _note(name: str) -> str:
    """A note name Obsidian links to as a note: "scan.pdf" would link to a PDF attachment,
    so it becomes "scan (pdf)"; a trailing full stop is dropped."""
    name = re.sub(r"\.(\w{2,4})$", lambda m: f" ({m[1].lower()})" if m[1].lower() in _EXTS else m[0], name)
    return name.rstrip(".") or name


_EXTS = {"pdf", "jpg", "jpeg", "png", "gif", "webp", "tif", "tiff", "bmp", "heic", "svg", "mp3", "mp4", "webm",
         "docx", "doc", "txt", "csv", "json", "html", "htm", "xml", "md", "canvas", "base"}


def _unique(name: str, names: dict, doc: str) -> str:
    taken = set(names.values())
    if name not in taken:
        return name
    name = _safe(f"{name} ({doc})")
    k = 2
    while f"{name} {k}" in taken and name in taken:
        k += 1
    return name if name not in taken else f"{name} {k}"


def _yaml_key(key: str) -> str:
    return key if re.fullmatch(r"[A-Za-z][\w ]*", key) else json.dumps(key)


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


# ----------------------------------------------------------------------- dot

def dot(g: Graph) -> str:
    """Graphviz cards: each page's lines on the left, one card per record on the right
    (field | value | page and line), coloured by owner, a line from each value to the line
    it was read from. Unsure values are italic with their score and a dashed line."""
    recs = records(g)
    out = ['digraph jove {', '  graph [fontname="Helvetica", fontsize=11, rankdir=LR, ranksep=1.2, nodesep=0.25, pad=0.3];',
           '  node [shape=plain, fontname="Helvetica", fontsize=10];', '  edge [arrowhead=none];']
    parent = {b: a for a, rel, b, _ in g.edges if rel == "contains"}
    for k, (doc, d) in enumerate((n, d) for n, d in g.nodes.items() if d["label"] == "Document"):
        out.append(f'  subgraph cluster_d{k} {{ label=<<B>{_h(d["name"])}</B>>; labeljust=l; color="#D1D5DB"; style=rounded;')
        for pid, p in ((n, x) for n, x in g.nodes.items() if x["label"] == "Page" and n.startswith(doc + "/")):
            lines = [n for n, x in g.nodes.items() if x["label"] in ("Line", "Chunk") and _up(parent, n, pid)]
            rows = "".join(f'<TR><TD ALIGN="RIGHT"><FONT COLOR="#9CA3AF" POINT-SIZE="8">l{i}</FONT></TD>'
                           f'<TD ALIGN="LEFT" PORT="l{i}"><FONT POINT-SIZE="9">{_h(_clip(g.nodes[n]["text"], 90))}</FONT></TD></TR>'
                           for i, n in enumerate(lines, start=1))
            out.append(f'    {_lit(pid)} [label=<<TABLE BORDER="1" COLOR="#9CA3AF" CELLBORDER="0" CELLSPACING="0" '
                       f'CELLPADDING="3" BGCOLOR="#FAFAFA"><TR><TD COLSPAN="2" ALIGN="LEFT"><B>{_h(p["name"])}</B></TD></TR>'
                       f'{rows}</TABLE>>];')
        mine = [r for r in recs if r["doc"] == doc]
        for rec in mine:
            fg, bg = OWNER_COLOURS.get(g.nodes[rec["owner"]]["key"], GREY) if rec["owner"] else GREY
            who = f'  ·  {_h(g.nodes[rec["owner"]]["name"].split(" (")[0])}' if rec["owner"] else ""
            rows = [f'<TR><TD COLSPAN="3" BGCOLOR="{fg}" ALIGN="LEFT"><FONT COLOR="white"><B>{_h(rec["title"])}</B>{who}</FONT></TD></TR>']
            for i, r in enumerate(rec["rows"]):
                v = (f'<I>{_h(_clip(r["value"], 60))}</I> <FONT COLOR="#B91C1C" POINT-SIZE="8">? {r["score"]:.2f}</FONT>'
                     if r["score"] < LOW else f'<B>{_h(_clip(r["value"], 60))}</B>')
                rows.append(f'<TR><TD ALIGN="LEFT" PORT="r{i}"><FONT COLOR="#6B7280" POINT-SIZE="9">{_h(r["field"])}</FONT></TD>'
                            f'<TD ALIGN="LEFT">{v}</TD><TD ALIGN="RIGHT"><FONT COLOR="#9CA3AF" POINT-SIZE="8">'
                            f'p{r["page"]} l{r["line"]}</FONT></TD></TR>')
            out.append(f'    {_lit(rec["key"])} [label=<<TABLE BORDER="1" COLOR="{fg}" CELLBORDER="0" CELLSPACING="0" '
                       f'CELLPADDING="4" BGCOLOR="{bg}">{"".join(rows)}</TABLE>>];')
        if mine:
            out.append("    { rank=same; " + " ".join(_lit(r["key"]) for r in mine) + " }")
        out.append("  }")
        for rec in mine:
            fg = (OWNER_COLOURS.get(g.nodes[rec["owner"]]["key"], GREY) if rec["owner"] else GREY)[0]
            for i, r in enumerate(rec["rows"]):
                page = _up(parent, r["line_id"], None)
                if page:
                    style = "dashed" if r["score"] < LOW else "solid"
                    out.append(f'  {_lit(page)}:l{r["line"]}:e -> {_lit(rec["key"])}:r{i}:w [color="{fg}99", style={style}];')
    return "\n".join(out + ["}"]) + "\n"


def _up(parent: dict, nid: str, page: str | None):
    """With a page id: is nid under it? Without: the page nid is under (or None)."""
    n = parent.get(nid)
    while n is not None:
        if (page is None and n.count("/") == 1 and "/" in n) or n == page:
            return n if page is None else True
        n = parent.get(n)
    return None if page is None else False


def _h(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")



def dot_full(g: Graph) -> str:
    """Graphviz: the document tree left to right, each rank-1 tag drawn as a hyperedge
    (a small diamond) joining the text to its field, list item and owner."""
    out = ['digraph jove {', '  rankdir=LR; ranksep=0.35; nodesep=0.08;',
           '  node [fontname="Helvetica", fontsize=10, style="filled,rounded", shape=box, penwidth=0];',
           '  edge [color="#9CA3AF", arrowsize=0.5];']
    for nid, n in g.nodes.items():
        fill = f"#{COLOURS[n['label']]:06X}"
        shape = {"Field": "ellipse", "Item": "ellipse", "Owner": "ellipse"}.get(n["label"], "box")
        out.append(f'  {_lit(nid)} [label={_lit(n["name"])}, shape={shape}, fillcolor="{fill}33", '
                   f'color="{fill}", penwidth=1.2];')
    for a, rel, b, _ in g.edges:
        if rel in ("contains", "part_of", "item_of"):
            out.append(f'  {_lit(a)} -> {_lit(b)}' + (' [style=dotted]' if rel != "contains" else "") + ";")
    for i, t in enumerate(t for t in g.tags if t["rank"] == 1):
        hub = f"tag{i}"
        out.append(f'  {hub} [shape=diamond, label="{t["score"]:.2f}", fontsize=7, width=0.3, height=0.3, '
                   f'fixedsize=true, fillcolor="{"#D6452F" if t["score"] >= LOW else "#F2A99E"}", fontcolor=white];')
        out.append(f'  {_lit(t["text"])} -> {hub} [arrowhead=none, color="#D6452F"];')
        out.append(f'  {hub} -> {_lit(t["field"])} [color="#D6452F", label="is", fontsize=7];')
        for item in t["items"][-1:]:
            out.append(f'  {hub} -> {_lit(item)} [color="#E08A00", style=dashed];')
        if t["owner"]:
            out.append(f'  {hub} -> {_lit(t["owner"])} [color="#9B4FD0", label="belongs to", fontsize=7];')
    return "\n".join(out + ["}"]) + "\n"


# -------------------------------------------------------------------- cypher

def cypher(g: Graph) -> list[str]:
    """OpenCypher statements (FalkorDB, Neo4j, Memgraph): one per line, nodes then edges."""
    out = [f"CREATE INDEX FOR (n:{label}) ON (n.id)" for label in sorted({n["label"] for n in g.nodes.values()})]
    # name: the caption graph browsers show by default.
    values = _item_values(g)
    out += [f"CREATE (:{n['label']} {_props({'id': nid, **n, **values.get(nid, {})})})" for nid, n in g.nodes.items()]
    for a, rel, b, props in g.edges:
        la, lb = g.nodes[a]["label"], g.nodes[b]["label"]
        out.append(f"MATCH (a:{la} {{id: {_lit(a)}}}), (b:{lb} {{id: {_lit(b)}}}) "
                   f"CREATE (a)-[:{rel.upper()} {_props(props)}]->(b)")
    return out


def _item_values(g: Graph) -> dict[str, dict]:
    """Each list item's values as properties, keyed by field: {date_of_birth: "1980-04-03"}.
    A key the node already uses (name, path…) gets a "value_" prefix."""
    out: dict[str, dict] = {}
    for rec in records(g):
        if rec["key"] in g.nodes:
            props = out.setdefault(rec["key"], {"owner": g.nodes[rec["owner"]]["key"] if rec["owner"] else None})
            for r in rec["rows"]:
                key = re.sub(r"\W+", "_", r["path"][len(rec["list"]):].strip(".[]")).strip("_") or "value"
                key = f"value_{key}" if key in g.nodes[rec["key"]] or key in ("id", "owner") else key
                props.setdefault(key, r["value"])
    return {k: {a: b for a, b in v.items() if b is not None} for k, v in out.items()}


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

  attribute key, value string;
  attribute kind, value string;
  attribute other-name, value string;
  attribute flag, value string;
  attribute written, value string;
  attribute how, value string;

  entity node @abstract, owns node-id @key, owns name, plays containment:container, plays containment:part;
  entity document sub node, owns source, owns created-at, owns chooser, owns tagger, owns context, plays ownership:document;
  entity page sub node, owns source, owns sha256, owns page-number;
  entity region @abstract, sub node, owns text, owns bbox, plays tag:value, plays mention:text;
  entity block sub region;
  entity line sub region;
  entity span sub region;
  entity segment sub region;
  entity chunk sub region;
  entity field sub node, owns path, plays tag:field, plays listing:list;
  entity item sub node, owns path, plays tag:item, plays listing:item;
  entity owner sub node, owns key, plays tag:owner, plays ownership:owner;
  # A person or organisation, across documents (jove.entities).
  entity actor sub node, owns kind, owns other-name @card(0..), owns flag @card(0..), plays mention:actor;

  # A tag is one hyperedge: the text, the field it gives, the list items it is part of, whose it is.
  relation tag, relates value, relates field, relates item @card(0..), relates owner, owns name,
    owns rank, owns score, owns separation, owns owner-p;
  # Trees: document > page > block > line > span; field > field; item > item.
  relation containment, relates container, relates part;
  relation listing, relates list, relates item;
  relation ownership, relates owner, relates document;
  relation mention, relates text, relates actor, owns written, owns how;
"""

_ENTITY = {"Document": "document", "Page": "page", "Block": "block", "Line": "line", "Span": "span",
           "Segment": "segment", "Chunk": "chunk", "Field": "field", "Item": "item", "Owner": "owner",
           "Entity": "actor"}
_ATTR = {"text": "text", "bbox": "bbox", "path": "path", "name": "name", "source": "source", "sha256": "sha256",
         "page": "page-number", "created_at": "created-at", "chooser": "chooser", "tagger": "tagger",
         "context": "context", "list": "path", "key": "key", "kind": "kind", "aliases": "other-name", "flags": "flag"}


def typeql(g: Graph, batch: int = 100) -> tuple[str, list[str]]:
    """(schema, insert queries). Entities first, then binary relations, then the tag hyperedges."""
    queries: list[str] = []
    ents = []
    for nid, n in g.nodes.items():
        has = [f"has node-id {_lit(nid)}"]
        for k, v in n.items():
            if k in _ATTR and not (n["label"] == "Field" and k == "list"):
                for x in v if isinstance(v, list) and k != "bbox" else [v]:
                    has.append(f"has {_ATTR[k]} {_lit(','.join(map(str, x)) if k == 'bbox' else x)}")
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
        elif rel == "names":
            rels.append(("mention", {"text": a, "actor": b}, {"written": props.get("written"), "how": props.get("how")}))
    for t in g.tags:
        players = {"value": [t["text"]], "field": [t["field"]], "item": t["items"], "owner": [t["owner"]] if t["owner"] else []}
        attrs = {"name": f"{g.nodes[t['field']]['name']} = {clean(g.nodes[t['text']]['text'])}",
                 "rank": t["rank"], "score": t["score"], "separation": t.get("separation"), "owner-p": t.get("owner_p")}
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

def load_falkor(g: Graph, url: str, replace: bool = False) -> None:
    from falkordb import FalkorDB  # optional: uv sync --extra graph

    u = urlparse(url)
    db = FalkorDB(host=u.hostname or "localhost", port=u.port or 6379)
    name = u.path.strip("/") or "jove"
    if name in db.list_graphs():
        if not replace:
            raise SystemExit(f"FalkorDB graph {name!r} exists; pass --replace to overwrite it")
        db.select_graph(name).delete()
    graph = db.select_graph(name)
    for q in cypher(g):
        graph.query(q)


def load_typedb(g: Graph, url: str, replace: bool = False) -> None:
    from typedb.driver import Credentials, DriverOptions, DriverTlsConfig, TransactionType, TypeDB

    u = urlparse(url)
    name = u.path.strip("/") or "jove"
    driver = TypeDB.driver(f"{u.hostname or 'localhost'}:{u.port or 1729}",
                           Credentials(u.username or "admin", u.password or "password"),
                           DriverOptions(DriverTlsConfig.disabled()))
    try:
        if driver.databases.contains(name):
            if not replace:
                raise SystemExit(f"TypeDB database {name!r} exists; pass --replace to overwrite it")
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
    ap.add_argument("--to", choices=["summary", "tree", "dot", "dot-full", "obsidian", "cypher", "typeql"])
    ap.add_argument("--out", type=Path, help="output file or folder (summary, tree, dot and cypher default to stdout)")
    ap.add_argument("--all", action="store_true", help="keep captions, rank-2/3 tags and one-line blocks")
    ap.add_argument("--replace", action="store_true", help="overwrite an existing vault, graph or database")
    ap.add_argument("--load", help="falkor://host:port/graph or typedb://[user:pass@]host:port/database")
    args = ap.parse_args(argv)
    if not args.to and not args.load:
        ap.error("give --to and/or --load")
    g = build([[json.loads(l) for l in p.read_text().splitlines() if l.strip()] for p in args.results], args.all)
    if args.to in ("summary", "tree", "dot", "dot-full", "cypher"):
        text = {"summary": summary, "tree": tree, "dot": dot, "dot-full": dot_full}.get(
            args.to, lambda g: "\n".join(cypher(g)) + "\n")(g)
        args.out.write_text(text) if args.out else sys.stdout.write(text)
    elif args.to:
        if not args.out:
            ap.error(f"--to {args.to} needs --out DIR")
        if args.to == "obsidian":
            obsidian(g, args.out, args.replace)
        else:
            schema, queries = typeql(g)
            args.out.mkdir(parents=True, exist_ok=True)
            (args.out / "schema.tql").write_text(schema)
            (args.out / "data.tql").write_text("\n\n".join(queries) + "\n")
    if args.load:
        scheme = urlparse(args.load).scheme
        if scheme not in ("falkor", "typedb"):
            ap.error(f"--load wants falkor:// or typedb://, not {scheme}://")
        (load_falkor if scheme == "falkor" else load_typedb)(g, args.load, args.replace)
    print(f"{len(g.nodes)} nodes, {len(g.edges)} edges, {len(g.tags)} tags", file=sys.stderr)


if __name__ == "__main__":
    main()
