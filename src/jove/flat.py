"""Flat boundary tagger: Jev decides grouping as well as labels.

OCR lines are the atoms (layout is used only for reading order and
paragraphs); the chooser is asked everything about each line in one parallel
round per page:

- the schema Choice fan-out (which field is this line?), with its neighbours as context;
- continues: does it continue the same single value as the previous line?
- same item: is it about the same person/organization/item as the line one
  back, and two back?

Running prose is not cut into lines: a block's lines that read as one
paragraph (``paragraphs``) are asked about as one unit, so a sentence keeps its
meaning and gets one tag. Values named inside it are still found by the
mentions pass (x-mentions).

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
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .choose import Chooser, YesNo, is_value
from .layout import DocNode
from .schema import SchemaNode
from .questions import LABEL_PAIR, POV, UNTRUSTED, Path, choice, label, paths, standing

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
    pos: int | None = None  # a mention's place among its unit's pieces: only neighbours merge


class FlatTagger:
    def __init__(self, schema: SchemaNode, chooser: Chooser, context: str | None = None,
                 threshold: float = THRESHOLD, keep: int = 3, workers: int = 8, paragraphs: bool = True):
        self.schema = schema
        self.chooser = chooser
        self.threshold = threshold
        self.keep = keep
        self.workers = workers
        self.paragraphs = paragraphs
        self.context = standing(schema, context)  # goes in the chooser's shared state
        self.warnings: list[dict] = []
        # Objects/list items whose values may be mentioned inside prose (x-mentions).
        self.mention_nodes = [n for n in schema.walk() if n.mentions and not n.leaf]
        # Fields whose every example has a digit (amounts, dates): a mention without one isn't theirs.
        self.numeric = {n.path for n in schema.walk() if n.leaf and n.examples
                        and all(any(c.isdigit() for c in str(e)) for e in n.examples)}

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
        # The units asked about: lines, and paragraphs of prose (kind "paragraph", lines as children).
        lines = [u for b in blocks for u in (paragraphs(b) if self.paragraphs else b.children)]
        if not lines:
            return [], []
        state = _state(page_id, lines, self.context)

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
                    for i, l in enumerate(lines) if l.kind == "line" and len(l.children) > 1 and line_tops[i]}
        # Same request: does a prose line mention a value of an x-mentions field? A line
        # tagged inside that field already is its value; a paragraph tagged there (a
        # jurat) still holds the field's other values (its date, its place).
        for i, l in enumerate(lines):
            if len(_words(l)) >= 3 and line_tops[i]:
                for k, m in enumerate(self.mention_nodes):
                    if l.kind == "paragraph" or not line_tops[i][0].target.startswith(m.path):
                        split_qs[f"men{i}_{k}"] = self._mention_q(l, m)
        yes_no = getattr(self.chooser, "yes_no", None)
        split_yes = yes_no(state, split_qs) if split_qs and yes_no else {}
        split = [i for i, l in enumerate(lines) if f"split{i}" in split_qs
                 and self._p(split_yes, f"split{i}", l.id, default=0.0) >= self.threshold]
        mentions = [(i, k) for i in range(len(lines)) if i not in split for k in range(len(self.mention_nodes))
                    if split_yes.get(f"men{i}_{k}", 0.0) >= self.threshold]

        # Round 3: words of the lines that should be split.
        word_qs, word_keys, word_yn = {}, {}, {}
        for i in split:
            words = lines[i].children
            word_keys[i] = [self._choices(word_qs, f"l{i}w{j}", w, lines[i].text, "It appears inside")
                            for j, w in enumerate(words)]
            for j in range(1, len(words)):
                word_yn[f"cont{i}_{j}"] = self._continues_q(words[j - 1].text, words[j].text, "word",
                                                            within=lines[i].text)
        # ... and pieces (words of a line, clauses of a paragraph) of prose that mentions an
        # x-mentions field, asked only within it.
        mention_keys = {}
        for i, k in mentions:
            m = self.mention_nodes[k]
            mention_keys[(i, k)] = [self._choices(word_qs, f"m{i}_{k}w{j}", w, lines[i].text,
                                                  "It appears inside", start=m)
                                    for j, w in enumerate(_pieces(lines[i]))]
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
        nodes, edges = self._records(page_id, lines, values, instances, split)

        # Mentions: extra spans inside prose, which keeps its own tags. In reading order
        # (mention_keys follows the lines); pieces between them gave no value.
        found: list[Unit] = []
        for (i, k), keys in mention_keys.items():
            m = self.mention_nodes[k]
            for j, w in enumerate(_pieces(lines[i])):
                top = self._top(word_choices, word_qs, keys[j], w, start=m)
                if top and is_value(top[0].target) and (
                        top[0].target not in self.numeric or any(ch.isdigit() for ch in w.text)):
                    found.append(Unit(w, i, top, _separation(top), 0.0, word=True, pos=j))
        mvalues = self._merge(found)
        n2, e2 = self._records(page_id, [], mvalues, self._items(mvalues, yes), [], prefix="m", mention=True)
        return nodes + n2, edges + e2

    # ------------------------------------------------------------------ questions

    def _choices(self, qs: dict, prefix: str, doc: DocNode, context: str, context_prefix: str,
                 start: SchemaNode | None = None) -> list:
        start = start or self.schema
        out = []
        for k, node in enumerate(start.internal_nodes()):
            key = f"{prefix}n{k}"
            qs[key] = choice(doc, node, context, context_prefix, bool(self.context))
            out.append((key, node))
        return out

    def _split_q(self, line: DocNode, neighbours: str, top: list[Path]) -> YesNo:
        labels = ", ".join(f"{label(p.target)} ({p.score:.2f})" for p in top)
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

    def _mention_q(self, line: DocNode, m: SchemaNode) -> YesNo:
        examples = f" (for example {', '.join(m.examples[:3])})" if m.examples else ""
        fields = ", ".join(c.name for c in m.children)
        return YesNo(
            instructions=UNTRUSTED + f"Line {line.id} reads: {line.text!r}."
            + f" Does the text itself mention a {m.name} ({m.description or m.name}){examples}:"
            + f" a value for {fields}, even inside a sentence?" + self._pov(),
            options={
                "true": f"Yes: a specific {m.name} value is written in this text.",
                "false": f"No: the text names no specific {m.name}.",
            },
            subject=line.text,
        )

    def _continues_q(self, prev: str, cur: str, unit: str, within: str | None = None) -> YesNo:
        where = f" Both are in the line {within!r}." if within else ""
        return YesNo(
            instructions=UNTRUSTED + f"Previous {unit}: {prev!r}. This {unit}: {cur!r}.{where}"
            + f" Does this {unit} continue the same single value as the previous {unit}?" + self._pov(),
            options={
                "true": "Yes: one value runs across both (the rest of a name, an address, a date, "
                        "a phone number, or the same sentence).",
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
        return POV if self.context else ""

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

    def _top(self, answers: dict, qs: dict, keys: list, doc: DocNode, start: SchemaNode | None = None) -> list[Path]:
        start = start or self.schema
        dists = {}
        for key, node in keys:
            probs = answers.get(key)
            if probs is None:
                self.warnings.append({"code": "unanswered", "detail": f"{doc.id} at {node.path or '<root>'}"})
                continue
            dists[node.path] = {qs[key].labels[k]: p for k, p in probs.items()}
        if start.path not in dists:
            return []
        return sorted(paths(start, dists), key=lambda p: -p.score)[: self.keep]

    # ------------------------------------------------------------------ assembly

    def _merge(self, units: list[Unit]) -> list[Unit]:
        """Consecutive units that continue each other and share a tag become one value."""
        out: list[Unit] = []
        for u in units:
            prev = out[-1] if out else None
            same_tag = prev is not None and prev.top[0].target == u.top[0].target and prev.word == u.word
            # Adjacent words of one line with the same tag are one value ("Senior Adjudicator");
            # across lines, only the "continues" answer joins them.
            # Clauses are whole phrases already ("$2,450" stays apart from "rent of").
            same_line_words = same_tag and u.word and prev.line_index == u.line_index \
                and u.doc.kind != "clause" and (u.pos is None or u.pos == prev.pos + 1)
            if same_tag and (same_line_words or (not u.word and u.link >= self.threshold)):
                prev.members = (prev.members or [prev.doc]) + [u.doc]
                prev.pos = u.pos
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
                    # A repeated field starts a new item, unless the field is itself a list
                    # inside the item (a side's names[]: several names, one side).
                    repeat = leaf in fields.get((arr, oid), set()) and "[]" not in leaf[len(arr):]
                    if p >= self.threshold and not repeat:
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

    def _records(self, page_id, lines, values, instances, split, prefix: str = "v", mention: bool = False):
        nodes, edges = [], []
        for line in lines:
            nodes.append(_node(line))
            edges.append({"type": "edge", "rel": "contains", "from": page_id, "to": line.id})
            if line.kind == "paragraph":
                for child in line.children:
                    nodes.append(_node(child))
                    edges.append({"type": "edge", "rel": "contains", "from": line.id, "to": child.id})
        for n, (v, inst) in enumerate(zip(values, instances), start=1):
            members = v.members or [v.doc]
            if len(members) == 1 and not v.word:
                source = v.doc.id  # an unsplit line is its own value
            else:
                source = f"{page_id}/{prefix}{n:03d}"
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
                    if mention:
                        edge["mention"] = True  # found inside a prose line that keeps its own tag
                edges.append(edge)
        return nodes, edges


def _separation(top: list[Path]) -> float:
    return top[0].score / top[1].score if len(top) > 1 and top[1].score > 0 else math.inf


def _anchor(v: Unit) -> str:
    return v.doc.id.rsplit("/", 1)[0] if v.word else v.doc.id


def _neighbours(lines: list[DocNode], i: int, n: int = 300) -> str:
    prev = lines[i - 1].text[-n:] if i else ""  # a neighbouring paragraph: the part next to this unit
    nxt = lines[i + 1].text[:n] if i + 1 < len(lines) else ""
    return f"before: {prev} | after: {nxt}"


# A line opening a new paragraph or list item: "1.", "(a)", "iv)", a bullet.
_MARKER = re.compile(r"^(\(?[1-9]\d{0,2}[.)]|\(?[a-z]{1,4}\)|[-•*–])\s")


def paragraphs(block: DocNode) -> list[DocNode]:
    """The block's lines, with each run of running prose joined into one paragraph node.

    A paragraph ends after a short line (the next line's first word would have fitted
    in the room left before the block's right edge, so ragged-right text still
    joins), unless that line visibly stops mid-sentence with a comma or a lowercase
    last word: a pre-printed form with a blank filled in ("... on" / "May 3, 2024,").
    A logo word or heading ("YORK") ends it. It also ends before a numbered or
    bulleted line. Only runs that read as prose
    become paragraphs: two or more lines, at least 12 words, mostly lowercase words,
    and no line with several "Label:" captions (a form's label/value rows stay lines).
    """
    lines = block.children
    if len(lines) < 2:
        return list(lines)
    right = max(l.bbox[2] for l in lines)
    runs: list[list[DocNode]] = [[lines[0]]]
    for prev, line in zip(lines, lines[1:]):
        char = (prev.bbox[2] - prev.bbox[0]) / max(len(prev.text), 1)
        first = len(line.text.split()[0]) if line.text.split() else 0
        short = right - prev.bbox[2] >= (first + 1) * char
        if (not short or _open(prev.text)) and not _MARKER.match(line.text):
            runs[-1].append(line)
        else:
            runs.append([line])
    out: list[DocNode] = []
    for run in runs:
        if not _prose(run):
            out += run
            continue
        n = sum(u.kind == "paragraph" for u in out) + 1
        box = (min(l.bbox[0] for l in run), min(l.bbox[1] for l in run),
               max(l.bbox[2] for l in run), max(l.bbox[3] for l in run))
        out.append(DocNode(f"{block.id}/p{n:02d}", "paragraph", block.page_id, box, _join(run), list(run)))
    return out


def _open(text: str) -> bool:
    """Does the line stop mid-sentence: a trailing comma, or a lowercase last word ("... on")?"""
    words = text.split()
    last = words[-1] if words else ""
    return last.endswith(",") or (last[:1].islower() and last[-1:].isalpha())


def _prose(run: list[DocNode]) -> bool:
    words = [w for l in run for w in l.text.split()]
    lower = sum(w[0].islower() for w in words if w[0].isalpha())
    return (len(run) >= 2 and len(words) >= 12 and lower >= 0.3 * len(words)
            and not any(len(LABEL_PAIR.findall(l.text)) >= 2 for l in run))


def _join(run: list[DocNode]) -> str:
    """The run's text as one paragraph, undoing end-of-line hyphenation ("posses-" + "sion")."""
    text = run[0].text
    for line in run[1:]:
        if text.endswith("-") and line.text[:1].islower():
            text = text[:-1] + line.text
        else:
            text += " " + line.text
    return text


def _words(unit: DocNode) -> list[DocNode]:
    return [w for l in unit.children for w in l.children] if unit.kind == "paragraph" else unit.children


def _pieces(unit: DocNode) -> list[DocNode]:
    """What a mention can be made of: a line's words, or a paragraph's clauses.

    Asked word by word, a sentence comes back as scattered single words ("in", "of",
    "December"); its clauses ("of the City of Ottawa,", "on May 3, 2024,") are the
    phrases a value is written in. Money and dates are pieces of their own first
    ("$4,900.00", "September 30, 2024"): no clause cut splits one, and no long clause
    hides one. A clause ends at , ; : or a sentence's full stop (not an
    abbreviation's: "O. Reg.", "A.").
    """
    if unit.kind != "paragraph":
        return unit.children
    words = _words(unit)
    ends, pos = [], -1
    for w in words:  # each word's end offset in the joined text
        pos += len(w.text) + 1
        ends.append(pos)
    text = " ".join(w.text for w in words)
    value_at = {}  # first word of a value -> one past its last word
    for m in _VALUE.finditer(text):
        first = next(i for i, e in enumerate(ends) if e > m.start())
        last = next(i for i, e in enumerate(ends) if e >= m.end())
        value_at[first] = last + 1

    out: list[DocNode] = []

    def emit(piece: list[DocNode]) -> None:
        if piece:
            box = (min(x.bbox[0] for x in piece), min(x.bbox[1] for x in piece),
                   max(x.bbox[2] for x in piece), max(x.bbox[3] for x in piece))
            out.append(DocNode(f"{unit.id}/c{len(out) + 1:02d}", "clause", unit.page_id, box,
                               " ".join(x.text for x in piece)))

    run: list[DocNode] = []
    n = 0
    while n < len(words):
        if n in value_at:
            emit(run)
            emit(words[n: value_at[n]])
            run, n = [], value_at[n]
            continue
        t = words[n].text
        run.append(words[n])
        if any(c.isalnum() for c in t) and (t[-1:] in ",;:" or (t.endswith(".") and len(t.rstrip(".")) > 3)):
            emit(run)
            run = []
        n += 1
    emit(run)
    return out


_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
# Values worth asking about on their own: money, and dates in their common written forms.
_VALUE = re.compile(
    r"\$\s?\d[\d,]*(?:\.\d{2})?"
    rf"|\b{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?[^\w\s]{{0,3}}(?:\s+[^\w\s]{{1,3}})?,?\s+\d{{4}}\b"
    rf"|\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:day\s+of\s+)?{_MONTH},?\s+\d{{4}}\b"
    r"|\b\d{1,4}[/-]\d{1,2}[/-]\d{2,4}\b")


def _state(page_id: str, lines: list[DocNode], context: dict) -> dict:
    state = {"page": page_id, "lines": [{"id": l.id, "text": l.text} for l in lines]}
    return {"context": context, **state} if context else state


def _node(n: DocNode) -> dict:
    return {"type": "node", "id": n.id, "kind": n.kind, "page_id": n.page_id,
            "bbox": [round(float(v), 1) for v in n.bbox], "text": n.text}
