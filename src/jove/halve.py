"""Halving tagger: Jev decides the units.

Start from the whole document. Given the schema, the chooser is asked whether
the chunk holds more than one value, so it should be cut in two. If yes, it is
halved and both halves are asked again; where the answer is no, that chunk is
one unit and is classified against the schema as it stands. Jev stops before
the pieces become nonsense, so nothing decides by rule where a value ends.

A cut falls at the line boundary nearest the middle of the chunk's text, or,
inside one line, at the nearest gap between words. A chunk too long to quote
in a question is halved without asking.
"""

import math
from dataclasses import dataclass, field

from .choose import Chooser, YesNo, is_value
from .layout import DocNode
from .questions import POV, UNTRUSTED, Path, choice, paths, standing
from .schema import SchemaNode

THRESHOLD = 0.5
MAX_TEXT = 30_000  # characters; Jev takes 64 kB of state and question together


@dataclass
class Leaf:
    a: int  # word range [a, b) in reading order
    b: int
    top: list[Path] = field(default_factory=list)
    instances: dict[str, str] = field(default_factory=dict)


class HalvingTagger:
    def __init__(self, schema: SchemaNode, chooser: Chooser, context: str | None = None,
                 threshold: float = THRESHOLD, keep: int = 3, max_text: int = MAX_TEXT):
        self.schema = schema
        self.chooser = chooser
        self.threshold = threshold
        self.keep = keep
        self.max_text = max_text
        self.context = standing(schema, context)
        self.warnings: list[dict] = []

    def tag(self, pages: dict[str, list[DocNode]]) -> tuple[list[dict], list[dict]]:
        """Return (node records, edge records) in the pipeline's JSONL shapes."""
        self.words: list[DocNode] = []
        self.line_of: list[DocNode] = []
        for blocks in pages.values():
            for block in blocks:
                for line in block.children:
                    for w in line.children:
                        self.words.append(w)
                        self.line_of.append(line)
        if not self.words:
            return [], []
        self.state = {"schema": _outline(self.schema), **({"context": self.context} if self.context else {})}
        leaves = [Leaf(a, b) for a, b in self._halve()]
        self._classify(leaves)
        leaves = self._rejoin(leaves)
        self._items(leaves)
        return self._records(leaves)

    # ------------------------------------------------------------------ halving

    def _halve(self) -> list[tuple[int, int]]:
        yes_no = getattr(self.chooser, "yes_no", None)
        leaves, todo = [], [(0, len(self.words))]
        while todo:
            ask = [c for c in todo if c[1] - c[0] > 1 and len(self._text(*c)) <= self.max_text]
            qs = {f"h{n}": self._halve_q(*c) for n, c in enumerate(ask)}
            answers = yes_no(self.state, qs) if qs and yes_no else {}
            p = {c: answers.get(f"h{n}") for n, c in enumerate(ask)}
            for c, v in p.items():
                if v is None and yes_no:
                    self.warnings.append({"code": "unanswered", "detail": f"halve {self._text(*c)[:60]!r}"})
            follow = []
            for a, b in todo:
                too_long = b - a > 1 and (a, b) not in p
                if too_long or (p.get((a, b)) or 0.0) >= self.threshold:
                    m = self._cut(a, b)
                    follow += [(a, m), (m, b)]
                else:
                    leaves.append((a, b))
            todo = follow
        return sorted(leaves)

    def _cut(self, a: int, b: int) -> int:
        """The line boundary nearest the middle of [a, b), or the word gap nearest it in one line."""
        cuts = [i for i in range(a + 1, b) if self.line_of[i] is not self.line_of[i - 1]] or list(range(a + 1, b))
        size = [0]
        for w in self.words[a:b]:
            size.append(size[-1] + len(w.text) + 1)
        half = size[-1] / 2
        return min(cuts, key=lambda i: abs(size[i - a] - half))

    def _text(self, a: int, b: int) -> str:
        out = []
        for i in range(a, b):
            if i > a:
                out.append("\n" if self.line_of[i] is not self.line_of[i - 1] else " ")
            out.append(self.words[i].text)
        return "".join(out)

    def _halve_q(self, a: int, b: int) -> YesNo:
        text = self._text(a, b)
        return YesNo(
            instructions=UNTRUSTED + f"Text reads: {text!r}."
            + " state.schema lists the fields this document is being read for."
            + " Does this text hold more than one value for those fields, so it should be cut in two"
            + " and each half looked at on its own?" + (POV if self.context else ""),
            options={
                "true": "Yes: it holds two or more separate values (different fields, or several items of a "
                        "list), or a value mixed with text that is not part of it.",
                "false": "No: it is one value (a name, an address, a date, a paragraph of reasons), a label "
                         "with its value, or text that gives no value for any field.",
            },
            subject=text,
        )

    # ------------------------------------------------------------------ classifying

    def _classify(self, leaves: list[Leaf]) -> None:
        qs, keys = {}, []
        nodes = self.schema.internal_nodes()
        for n, leaf in enumerate(leaves):
            doc = self._doc(leaf, f"c{n}")
            before = self._text(leaves[n - 1].a, leaves[n - 1].b)[-200:] if n else ""
            after = self._text(leaves[n + 1].a, leaves[n + 1].b)[:200] if n + 1 < len(leaves) else ""
            around = " … ".join(t for t in (before, after) if t)
            keys.append([])
            for k, node in enumerate(nodes):
                qs[f"c{n}n{k}"] = choice(doc, node, around, "Neighbouring text", bool(self.context))
                keys[-1].append((f"c{n}n{k}", node))
        answers = self.chooser.choose(self.state, qs)
        for leaf, ks in zip(leaves, keys):
            dists = {}
            for key, node in ks:
                probs = answers.get(key)
                if probs is None:
                    self.warnings.append({"code": "unanswered", "detail": f"{key} at {node.path or '<root>'}"})
                    continue
                dists[node.path] = {qs[key].labels[o]: p for o, p in probs.items()}
            if self.schema.path in dists:
                leaf.top = sorted(paths(self.schema, dists), key=lambda p: -p.score)[: self.keep]

    def _doc(self, leaf: Leaf, key: str) -> DocNode:
        words = self.words[leaf.a: leaf.b]
        lines = {id(self.line_of[i]): self.line_of[i] for i in range(leaf.a, leaf.b)}.values()
        return DocNode(key, "chunk", words[0].page_id, _box(words), self._text(leaf.a, leaf.b), words,
                       form=all(l.form for l in lines))

    def _rejoin(self, leaves: list[Leaf]) -> list[Leaf]:
        """A cut inside one line can fall inside a value ("Name: Jane | Doe DOB: 1980"); its
        pieces come back with the same field, and neighbours on a line that share one are one value."""
        out: list[Leaf] = []
        for leaf in leaves:
            prev = out[-1] if out else None
            if (prev and prev.top and leaf.top and is_value(leaf.top[0].target)
                    and prev.top[0].target == leaf.top[0].target and self.line_of[prev.b - 1] is self.line_of[leaf.a]):
                prev.b = leaf.b
                prev.top = max(prev.top, leaf.top, key=lambda t: t[0].score)
                continue
            out.append(leaf)
        return out

    # ------------------------------------------------------------------ list items

    def _items(self, leaves: list[Leaf]) -> None:
        """Consecutive values under one list (parties[]) join an item when the chooser says they're
        about the same thing; a field repeating within an item starts a new one."""
        valued = [l for l in leaves if l.top and is_value(l.top[0].target)]
        pairs = {}  # (array path, leaf index) -> previous leaf index under that array
        last: dict[str, int] = {}
        for k, leaf in enumerate(valued):
            for node in leaf.top[0].nodes:
                if node.is_array_item:
                    if node.path in last:
                        pairs[(node.path, k)] = last[node.path]
                    last[node.path] = k
        yes_no = getattr(self.chooser, "yes_no", None)
        qs = {f"s{n}": self._same_q(valued[j], valued[k]) for n, ((_, k), j) in enumerate(pairs.items())}
        answers = yes_no(self.state, qs) if qs and yes_no else {}
        same = {key: answers.get(f"s{n}", 0.0) >= self.threshold for n, key in enumerate(pairs)}
        used: dict[tuple[str, str], set[str]] = {}
        for k, leaf in enumerate(valued):
            for node in leaf.top[0].nodes:
                if not node.is_array_item:
                    continue
                arr, target = node.path, leaf.top[0].target
                j = pairs.get((arr, k))
                prev = valued[j].instances.get(arr) if j is not None else None
                repeat = prev is not None and target in used.get((arr, prev), set()) and "[]" not in target[len(arr):]
                iid = prev if prev and same.get((arr, k)) and not repeat else f"{self.words[leaf.a].page_id}/i{k:03d}"
                leaf.instances[arr] = iid
                used.setdefault((arr, iid), set()).add(target)

    def _same_q(self, prev: Leaf, cur: Leaf) -> YesNo:
        a, b = self._text(prev.a, prev.b), self._text(cur.a, cur.b)
        return YesNo(
            instructions=UNTRUSTED + f"Earlier text: {a!r}. This text: {b!r}."
            + " Are both about the same person, organization, event, amount or other single item"
            + " (for example a party's name and that party's role or address)?" + (POV if self.context else ""),
            options={"true": "Yes: they describe the same item.",
                     "false": "No: different items, or either is not about any particular item."},
            subject=b,
        )

    # ------------------------------------------------------------------ records

    def _records(self, leaves: list[Leaf]) -> tuple[list[dict], list[dict]]:
        nodes, edges = [], []
        for n, leaf in enumerate(leaves, start=1):
            if not leaf.top:
                continue
            by_page: dict[str, list[int]] = {}
            for i in range(leaf.a, leaf.b):
                by_page.setdefault(self.words[i].page_id, []).append(i)
            parts = list(by_page.items())
            instances = dict(leaf.instances)
            second = leaf.top[1].score if len(leaf.top) > 1 else 0.0
            separation = leaf.top[0].score / second if second > 0 else math.inf
            for part, (page_id, idx) in enumerate(parts):
                node_id = f"{page_id}/h{n:03d}" + (f"p{part + 1}" if len(parts) > 1 else "")
                words = [self.words[i] for i in idx]
                nodes.append({"type": "node", "id": node_id, "kind": "chunk", "page_id": page_id,
                              "bbox": [round(float(v), 1) for v in _box(words)], "text": self._text(idx[0], idx[-1] + 1)})
                edges.append({"type": "edge", "rel": "contains", "from": page_id, "to": node_id})
                for rank, path in enumerate(leaf.top, start=1):
                    edge = {"type": "edge", "rel": "tagged", "from": node_id, "to": path.target, "rank": rank,
                            "score": round(path.score, 4), "asked_from": ""}
                    if rank == 1:
                        edge["separation"] = None if math.isinf(separation) else round(separation, 3)
                        edge["instances"] = instances
                    edges.append(edge)
        return nodes, edges


def _outline(schema: SchemaNode) -> list[str]:
    """The schema's fields, one line each, for the shared state."""
    return [f"{n.path}: {n.description}" if n.description else n.path for n in schema.walk() if n.leaf]


def _box(words: list[DocNode]) -> tuple[float, float, float, float]:
    return (min(w.bbox[0] for w in words), min(w.bbox[1] for w in words),
            max(w.bbox[2] for w in words), max(w.bbox[3] for w in words))
