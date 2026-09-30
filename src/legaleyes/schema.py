"""Parse a JSON Schema subset into a tree of schema nodes.

Paths are dotted with ``[]`` for array items (``parties[].name``). Besides
``description``, nodes may carry ``examples`` (JSON Schema) and ``x-not-for``
(ours): what belongs in a sibling instead. Both help a model tell similar
siblings apart. The root may carry ``x-instructions``: standing context for
every question, such as whose point of view the data takes.
"""

import re
from dataclasses import dataclass, field

SCALARS = {"string", "number", "integer", "boolean"}
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class SchemaError(ValueError):
    pass


@dataclass
class SchemaNode:
    path: str  # "" for the root
    name: str
    type: str  # "object" or a scalar type
    description: str | None = None
    examples: tuple[str, ...] = ()
    not_for: str | None = None
    is_array_item: bool = False
    children: list["SchemaNode"] = field(default_factory=list)
    instructions: str | None = None  # root only: ``x-instructions``, e.g. whose point of view

    @property
    def leaf(self) -> bool:
        return not self.children

    def internal_nodes(self) -> list["SchemaNode"]:
        """This node and every non-leaf below it, depth first."""
        if self.leaf:
            return []
        out = [self]
        for c in self.children:
            out.extend(c.internal_nodes())
        return out

    def leaves(self) -> list["SchemaNode"]:
        if self.leaf:
            return [self]
        return [leaf for c in self.children for leaf in c.leaves()]


@dataclass(frozen=True)
class Field:
    path: str
    type: str
    description: str | None = None
    examples: tuple[str, ...] = ()
    not_for: str | None = None


def build_tree(schema: dict) -> SchemaNode:
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise SchemaError("schema root must be {\"type\": \"object\", ...}")
    root = _node(schema, "", "", schema)
    if isinstance(schema.get("x-instructions"), str):
        root.instructions = schema["x-instructions"]
    return root


def flatten(schema: dict) -> dict[str, Field]:
    return {
        n.path: Field(n.path, n.type, n.description, n.examples, n.not_for)
        for n in build_tree(schema).leaves()
    }


def _node(node: dict, path: str, name: str, described: dict, array_item: bool = False) -> SchemaNode:
    if not isinstance(node, dict):
        raise SchemaError(f"schema at {path or '<root>'} must be an object")
    kind = node.get("type")
    meta = dict(
        description=described.get("description") or node.get("description"),
        examples=tuple(str(e) for e in described.get("examples", node.get("examples", []))),
        not_for=described.get("x-not-for") or node.get("x-not-for"),
        is_array_item=array_item,
    )
    if kind == "array":
        items = node.get("items")
        if not isinstance(items, dict):
            raise SchemaError(f"array at {path} needs items")
        if items.get("type") == "array":
            raise SchemaError(f"arrays of arrays are not supported at {path}")
        # The array's own description describes its items better than the item's.
        return _node(items, f"{path}[]", name, node, array_item=True)
    if kind == "object":
        props = node.get("properties")
        if not isinstance(props, dict) or not props:
            raise SchemaError(f"object at {path or '<root>'} needs properties")
        children = []
        for key, child in props.items():
            if not _NAME.fullmatch(key):
                raise SchemaError(f"unsupported property name {key!r}")
            children.append(_node(child, f"{path}.{key}" if path else key, key, child))
        return SchemaNode(path, name, "object", children=children, **meta)
    if kind in SCALARS:
        return SchemaNode(path, name, kind, **meta)
    raise SchemaError(f"unsupported type {kind!r} at {path}")
