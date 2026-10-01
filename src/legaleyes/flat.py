"""Flat boundary tagger: Jev decides grouping as well as labels.

The tree tagger (tagger.py) lets the layout's blocks decide which lines belong
together and descends block -> line -> word. This one treats OCR lines as the
atoms (layout is used only for reading order) and asks the chooser everything
about each line in one parallel round per page:

- the schema Choice fan-out (which field is this line?), with its neighbours as context;
- continues: does it continue the same single value as the previous line?
- same item: is it about the same person/organization/item as the line one
  back, and two back?

Round 2 asks, for every multi-word line, whether it should be split, showing
the labels round 1 found (a blind version of this question missed splits the
labelled one caught). Round 3 labels the words of lines that said yes. Then
the result is assembled with no tuned thresholds on geometry:

- adjacent words of a line that share a tag are one value; lines join when
  they "continue" each other and share a tag;
- list items (parties[], amounts[]...) follow the same-item answers, and a
  field repeating inside an item starts a new item (two names = two parties).
"""

import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .choose import Chooser, YesNo
from .layout import DocNode
from .schema import SchemaNode
from .tagger import UNTRUSTED, Path, Tagger, _paths, _Task

THRESHOLD = 0.5


@dataclass
class Unit:
    doc: DocNode
    line_index: int  # index of its line on the page
    top: list[Path]
    separation: float
    link: float  # P(continues the previous unit's value)
    word: bool = False
    members: list[DocNode] = field(default_factory=list)  # set when units merge into one value


class FlatTagger:
    def __init__(self, schema: SchemaNode, chooser: Chooser, context: str | None = None,
                 threshold: float = THRESHOLD, keep: int = 3, workers: int = 8):
        self.schema = schema
        self.chooser = chooser
        self.threshold = threshold
        self.keep = keep
        self.workers = workers
        self._q = Tagger(schema, chooser, context=context)  # question builder + shared context
        self.warnings: list[dict] = []

    # ------------------------------------------------------------------ public

    def tag(self, pages: dict[str, list[DocNode]]) -> tuple[list[dict], list[dict]]:
        """Return (node records, edge records) in the pipeline's JSONL shapes."""
        with ThreadPoolExecutor(self.workers) as pool:
            per_page = list(pool.map(lambda item: self._page(*item), pages.items()))
        nodes, edges = [], []
        for n, e in per_page:
            nodes += n
            edges += e
        return nodes, edges

    # ------------------------------------------------------------------ one page

    def _page(self, page_id: str, blocks: list[DocNode]) -> tuple[list[dict], list[dict]]:
        lines = [l for b in blocks for l in b.children]
        if not lines:
            return [], []
        state = _state(page_id, lines, self._q.context)

        # Round 1: everything about every line, in parallel.
        choice_qs, keys = {}, []
        for i, line in enumerate(lines):
            ctx = _neighbours(lines, i)
            keys.append(self._choices(choice_qs, f"l{i}", line, ctx, "Neighbouring lines"))
        yn = {}
        for i, line in enumerate(lines):
            if i >= 1:
                yn[f"cont{i}"] = self._continues_q(lines[i - 1].text, line.text, "line")
                yn[f"same1_{i}"] = self._same_item_q(lines[i - 1].text, line.text)
            if i >= 2:
                yn[f"same2_{i}"] = self._same_item_q(lines[i - 2].text, line.text)
        choices, yes = self._ask(state, choice_qs, yn)
        line_tops = [self._top(choices, choice_qs, k, lines[i]) for i, k in enumerate(keys)]

        # Round 2: should each multi-word line be split? Asked for every such line
        # (a confident single label can hide a second value), but only now, so the
        # question can show the labels found: "city (0.41), postal code (0.35)".
        split_qs = {f"split{i}": self._split_q(l, _neighbours(lines, i), line_tops[i])
                    for i, l in enumerate(lines) if len(l.children) > 1 and line_tops[i]}
        yes_no = getattr(self.chooser, "yes_no", None)
        split_yes = yes_no(state, split_qs) if split_qs and yes_no else {}
        split = [i for i, l in enumerate(lines) if f"split{i}" in split_qs
                 and self._p(split_yes, f"split{i}", l.id, default=0.0) >= self.threshold]

        # Round 3: words of the lines that should be split.
        word_qs, word_keys, word_yn = {}, {}, {}
        for i in split:
            words = lines[i].children
            word_keys[i] = [self._choices(word_qs, f"l{i}w{j}", w, lines[i].text, "It appears inside")
                            for j, w in enumerate(words)]
            for j in range(1, len(words)):
                word_yn[f"cont{i}_{j}"] = self._continues_q(words[j - 1].text, words[j].text, "word",
                                                            within=lines[i].text)
        word_choices, word_yes = self._ask(state, word_qs, word_yn) if word_qs else ({}, {})

        # Units in reading order: unsplit lines, and the words of split lines.
        units: list[Unit] = []
        for i, line in enumerate(lines):
            first_link = self._p(yes, f"cont{i}", line.id, default=0.0) if i else 0.0
            if i in word_keys:
                for j, w in enumerate(line.children):
                    top = self._top(word_choices, word_qs, word_keys[i][j], w)
                    link = first_link if j == 0 else self._p(word_yes, f"cont{i}_{j}", w.id, default=0.0)
                    if top:
                        units.append(Unit(w, i, top, _separation(top), link, word=True))
            elif line_tops[i]:
                top = line_tops[i]
                units.append(Unit(line, i, top, _separation(top), first_link))

        values = self._merge(units)
        instances = self._items(values, yes)
        return self._records(page_id, lines, values, instances, split)

    # ------------------------------------------------------------------ questions

    def _choices(self, qs: dict, prefix: str, doc: DocNode, context: str, context_prefix: str) -> list:
        task = _Task(doc, self.schema, context, {})
        out = []
        for k, node in enumerate(self.schema.internal_nodes()):
            key = f"{prefix}n{k}"
            qs[key] = self._q._question(task, node, context_prefix=context_prefix)
            out.append((key, node))
        return out

    def _split_q(self, line: DocNode, neighbours: str, top: list[Path]) -> YesNo:
        from .tagger import _label

        labels = ", ".join(f"{_label(p.target)} ({p.score:.2f})" for p in top)
        return YesNo(
            instructions=UNTRUSTED + f"Line {line.id} reads: {line.text!r}. Neighbouring lines: {neighbours!r}."
            + f" The best schema labels for the whole line are: {labels}."
            + " Does this line hold two or more separate values that belong to different fields of the schema,"
            + " so it should be split and labelled word by word?" + self._pov(),
            options={
                "true": "Yes: separate values for different fields (a name and a date of birth, a phone and an "
                        "email, an amount and a deadline), or a list of several items.",
                "false": "No: one value, a label with its value, a heading, or running prose (reasons, "
                         "narrative) that merely mentions things.",
            },
            subject=line.text,
        )

    def _continues_q(self, prev: str, cur: str, unit: str, within: str | None = None) -> YesNo:
        where = f" Both are in the line {within!r}." if within else ""
        return YesNo(
            instructions=UNTRUSTED + f"Previous {unit}: {prev!r}. This {unit}: {cur!r}.{where}"
            + f" Does this {unit} continue the same single value as the previous {unit}?" + self._pov(),
            options={
                "true": f"Yes: one value runs across both (the rest of a name, an address, a date, "
                        f"a phone number, or the same sentence).",
                "false": f"No: this {unit} starts a new value, a label, or unrelated text.",
            },
            subject=cur,
        )

    def _same_item_q(self, prev: str, cur: str) -> YesNo:
        return YesNo(
            instructions=UNTRUSTED + f"Earlier line: {prev!r}. This line: {cur!r}."
            + " Are both lines about the same person, organization, event, amount or other single item"
            + " (for example a party's name and that party's role or address)?" + self._pov(),
            options={
                "true": "Yes: they describe the same item.",
                "false": "No: different items, or either line is not about any particular item.",
            },
            subject=cur,
        )

    def _pov(self) -> str:
        return " Interpret it as state.context describes." if self._q.context else ""

    # ------------------------------------------------------------------ answers

    def _ask(self, state: dict, choice_qs: dict, yn_qs: dict):
        yes_no = getattr(self.chooser, "yes_no", None)
        with ThreadPoolExecutor(2) as pool:
            f_choice = pool.submit(self.chooser.choose, state, choice_qs)
            f_yes = pool.submit(yes_no, state, yn_qs) if (yes_no and yn_qs) else None
            return f_choice.result(), (f_yes.result() if f_yes else {})

    def _p(self, answers: dict, key: str, doc_id: str, default: float) -> float:
        p = answers.get(key)
        if p is None:
            self.warnings.append({"code": "unanswered", "detail": f"{doc_id} {key}"})
            return default
        return p

    def _top(self, answers: dict, qs: dict, keys: list, doc: DocNode) -> list[Path]:
        dists = {}
        for key, node in keys:
            probs = answers.get(key)
            if probs is None:
                self.warnings.append({"code": "unanswered", "detail": f"{doc.id} at {node.path or '<root>'}"})
                continue
            dists[node.path] = {qs[key].labels[k]: p for k, p in probs.items()}
        if self.schema.path not in dists:
            return []
        return sorted(_paths(self.schema, dists), key=lambda p: -p.score)[: self.keep]

    # ------------------------------------------------------------------ assembly

    def _merge(self, units: list[Unit]) -> list[Unit]:
        """Consecutive units that continue each other and share a tag become one value."""
        out: list[Unit] = []
        for u in units:
            prev = out[-1] if out else None
            same_tag = prev is not None and prev.top[0].target == u.top[0].target and prev.word == u.word
            # Adjacent words of one line with the same tag are one value ("Senior Adjudicator");
            # across lines, only the "continues" answer joins them.
            same_line_words = same_tag and u.word and prev.line_index == u.line_index
            if same_tag and (same_line_words or (not u.word and u.link >= self.threshold)):
                prev.members = (prev.members or [prev.doc]) + [u.doc]
                continue
            out.append(u)
        return out

    def _items(self, values: list[Unit], yes: dict) -> list[dict[str, str]]:
        """Assign list-item instances from the same-item answers; a repeated field starts a new item."""
        out: list[dict[str, str]] = []
        fields: dict[tuple[str, str], set[str]] = {}  # (array, instance id) -> leaves used
        for k, v in enumerate(values):
            inst: dict[str, str] = {}
            for node in v.top[0].nodes:
                if not node.is_array_item:
                    continue
                arr, leaf = node.path, v.top[0].target
                joined = None
                for back in (1, 2):
                    if k - back < 0:
                        break
                    other = values[k - back]
                    oid = out[k - back].get(arr)
                    if oid is None:
                        continue
                    if other.line_index == v.line_index:
                        p = 1.0  # same line
                    else:
                        gap = v.line_index - other.line_index
                        p = yes.get(f"same{gap}_{v.line_index}", 0.0) if gap in (1, 2) else 0.0
                    if p >= self.threshold and leaf not in fields.get((arr, oid), set()):
                        joined = oid
                        break
                iid = joined or _anchor(v)
                if not joined and (arr, iid) in fields:
                    # A new item whose natural anchor (the line) is already an item: two
                    # parties on one line ("DAVID GOODIS, ..., and JOHN DOE, ..."). Anchor at the word.
                    iid = v.doc.id
                inst[arr] = iid
                fields.setdefault((arr, iid), set()).add(leaf)
            out.append(inst)
        return out

    def _records(self, page_id, lines, values, instances, split):
        nodes, edges = [], []
        for line in lines:
            nodes.append(_node(line))
            edges.append({"type": "edge", "rel": "contains", "from": page_id, "to": line.id})
        for n, (v, inst) in enumerate(zip(values, instances), start=1):
            members = v.members or [v.doc]
            if len(members) == 1 and not v.word:
                source = v.doc.id  # an unsplit line is its own value
            else:
                source = f"{page_id}/v{n:03d}"
                kind = "span" if v.word else "segment"
                text = (" " if v.word else "\n").join(m.text for m in members)
                box = [min(m.bbox[0] for m in members), min(m.bbox[1] for m in members),
                       max(m.bbox[2] for m in members), max(m.bbox[3] for m in members)]
                nodes.append({"type": "node", "id": source, "kind": kind, "page_id": page_id,
                              "bbox": [round(float(x), 1) for x in box], "text": text})
                parents = {m.id.rsplit("/", 1)[0] if v.word else m.id for m in members}
                for parent in sorted(parents):
                    edges.append({"type": "edge", "rel": "contains", "from": parent, "to": source})
            for rank, path in enumerate(v.top if len(members) == 1 else v.top[:1], start=1):
                edge = {"type": "edge", "rel": "tagged", "from": source, "to": path.target, "rank": rank,
                        "score": round(path.score, 4), "asked_from": ""}
                if rank == 1:
                    edge["separation"] = None if math.isinf(v.separation) else round(v.separation, 3)
                    edge["instances"] = inst
                edges.append(edge)
        return nodes, edges


def _separation(top: list[Path]) -> float:
    return top[0].score / top[1].score if len(top) > 1 and top[1].score > 0 else math.inf


def _anchor(v: Unit) -> str:
    return v.doc.id.rsplit("/", 1)[0] if v.word else v.doc.id


def _neighbours(lines: list[DocNode], i: int) -> str:
    prev = lines[i - 1].text if i else ""
    nxt = lines[i + 1].text if i + 1 < len(lines) else ""
    return f"before: {prev} | after: {nxt}"


def _state(page_id: str, lines: list[DocNode], context: dict) -> dict:
    state = {"page": page_id, "lines": [{"id": l.id, "text": l.text} for l in lines]}
    return {"context": context, **state} if context else state


def _node(n: DocNode) -> dict:
    return {"type": "node", "id": n.id, "kind": n.kind, "page_id": n.page_id,
            "bbox": [round(float(v), 1) for v in n.bbox], "text": n.text}
