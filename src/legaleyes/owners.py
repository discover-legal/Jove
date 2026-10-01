"""Ownership pass: decide *whose* a name or contact detail is, separately from *what* it is.

The schema tree answers "what field is this?", and for contact details that
question can't see whose they are: a tribunal's letterhead address looks just
like the client's address. Schemas declare their owners under ``x-owners``:

    "x-owners": {
      "client": {"description": "...", "name": ["client.person.full_name", ...], "contact": "client.contact"},
      "party":  {"description": "...", "name": ["parties[].name"], "contact": "parties[].contact"},
      "issuer": {"description": "...", "name": ["document_issuer.name"], "contact": "document_issuer.contact"}
    }

Every value tagged with an owner's name field, or with anything under an
owner's contact prefix, gets one more Choice: whose is it? (one option per
owner, plus nobody/can't tell). If the answer names a different owner, the tag
moves to that owner's equivalent field (``client.contact.address.city`` ->
``document_issuer.contact.address.city``). The answer is recorded on the edge.
"""

from collections import defaultdict

from .choose import NONE, Chooser, Question
from .schema import SchemaNode
from .tagger import UNTRUSTED


class Owners:
    def __init__(self, schema: dict, tree: SchemaNode, chooser: Chooser, context: dict | None = None):
        self.owners: dict = schema.get("x-owners") or {}
        self.fields = {leaf.path for leaf in tree.leaves()}
        self.chooser = chooser
        self.context = context or {}
        self.warnings: list[dict] = []

    def owner_of(self, path: str) -> tuple[str, str] | None:
        """(owner key, kind) where kind is "name" or the contact suffix (".address.city")."""
        for key, o in self.owners.items():
            if path in o.get("name", []):
                return key, "name"
            prefix = o.get("contact")
            if prefix and path.startswith(prefix + "."):
                return key, path[len(prefix):]
        return None

    def equivalent(self, owner: str, kind: str) -> str | None:
        o = self.owners[owner]
        path = o["name"][0] if kind == "name" else (o["contact"] + kind if o.get("contact") else None)
        return path if path in self.fields else None

    def apply(self, nodes: list[dict], edges: list[dict]) -> list[dict]:
        if not self.owners:
            return edges
        by_id = {n["id"]: n for n in nodes}
        candidates = [e for e in edges if e["type"] == "edge" and e["rel"] == "tagged" and e["rank"] == 1
                      and self.owner_of(e["to"])]
        pages: dict[str, list[dict]] = defaultdict(list)
        for e in candidates:
            pages[by_id[e["from"]]["page_id"]].append(e)

        for page_id, page_edges in pages.items():
            lines = [n for n in nodes if n["page_id"] == page_id and n["kind"] == "line"]
            state = {"page": page_id, "lines": [{"id": n["id"], "text": n["text"]} for n in lines]}
            if self.context:
                state = {"context": self.context, **state}
            questions = {f"w{i}": self._question(by_id[e["from"]], e["to"]) for i, e in enumerate(page_edges)}
            answers = self.chooser.choose(state, questions)
            for i, e in enumerate(page_edges):
                probs = answers.get(f"w{i}")
                if probs is None:
                    self.warnings.append({"code": "owner_unanswered", "detail": e["from"]})
                    continue
                current, kind = self.owner_of(e["to"])
                best = max(probs, key=probs.get)
                e["owner"] = {"owner": best, "p": round(probs[best], 4), "was": e["to"]}
                if best in (NONE, current):
                    continue
                target = self.equivalent(best, kind)
                if target is None:
                    continue
                e["to"] = target
                # Leave the old owner's lists; a value moved into a list (parties[]) starts its own item.
                inst = {k: v for k, v in (e.get("instances") or {}).items() if k not in _lists(e["owner"]["was"])}
                for arr in _lists(target):
                    inst.setdefault(arr, _anchor(e["from"]))
                e["instances"] = inst
        return edges

    def _question(self, node: dict, path: str) -> Question:
        options = {key: {"what": o["description"]} for key, o in self.owners.items()}
        options[NONE] = {"what": "Nobody in particular, or it can't be told from the document."}
        pov = " Interpret it as state.context describes." if self.context else ""
        return Question(
            instructions=(
                UNTRUSTED
                + f"Text {node['id']} reads: {node['text']!r}. It was identified as {path}."
                + " Whose is it? Use the whole page (state.lines): who issued the document, who it is"
                + " addressed to, and who the matter is about." + pov
            ),
            options=options,
            subject=node["text"],
            node="#owner",
            labels={k: k for k in options},
        )


def _lists(path: str) -> list[str]:
    """Array prefixes of a path: "parties[].contact.phones[]" -> ["parties[]", "parties[].contact.phones[]"]."""
    out, i = [], path.find("[]")
    while i != -1:
        out.append(path[: i + 2])
        i = path.find("[]", i + 2)
    return out


def _anchor(node_id: str) -> str:
    parts = node_id.split("/")
    return "/".join(parts[:3]) if len(parts) > 3 else node_id
