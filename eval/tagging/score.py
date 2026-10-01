"""Score a tagger's JSONL against eval/tagging/gold.

    uv run python eval/tagging/score.py eval/tagging/results/<tagger>

Predictions are rank-1 `tagged` edges that aren't `#none` or `#label`, with their node's text.
  recall     gold values found with an accepted field. Strict on text: the
             prediction must contain the value without much else (tagging a whole
             paragraph as a name doesn't count as finding the name).
  precision  predictions whose field is accepted for some gold value on
             overlapping text. Loose on text. Tags on text the gold doesn't
             mention count as wrong.
  groups     gold list items (e.g. a party and its role) whose texts all come out
             under one instance of the array, distinct from the other groups'.
"""

import json
import re
import sys
from pathlib import Path

GOLD = Path(__file__).parent / "gold"


def norm(text: str) -> str:
    return re.sub(r"[^0-9a-z]", "", text.lower())


def paths(g: dict) -> set[str]:
    return {g["path"], *g.get("alt", [])}


def predictions(result: Path) -> list[dict]:
    recs = [json.loads(l) for l in result.read_text().splitlines()]
    nodes = {r["id"]: r for r in recs if r["type"] == "node"}
    return [
        {"text": nodes[e["from"]]["text"], "path": e["to"], "score": e["score"], "instances": e.get("instances") or {}}
        for e in recs
        if e["type"] == "edge" and e["rel"] == "tagged" and e["rank"] == 1
        and not e["to"].endswith(("#none", "#label"))
    ]


def strict(g: dict, p: dict) -> bool:
    gt, pt = norm(g["text"]), norm(p["text"])
    if not gt or not pt or p["path"] not in paths(g):
        return False
    if g.get("region"):
        return pt in gt
    if p["path"] != g["path"]:
        return gt in pt  # alternatives are broader readings (address.full holds the postal code)
    return (gt in pt and len(pt) <= 4 * len(gt) + 12) or (pt in gt and len(pt) >= 0.6 * len(gt))


def loose(g: dict, p: dict) -> bool:
    gt, pt = norm(g["text"]), norm(p["text"])
    return bool(gt and pt) and p["path"] in paths(g) and (gt in pt or pt in gt)


def score(gold: dict, preds: list[dict]) -> dict:
    found = [g for g in gold["tags"] if any(strict(g, p) for p in preds)]
    right = [p for p in preds if any(loose(g, p) for g in gold["tags"])]
    group_ids = []
    for grp in gold.get("groups", []):
        ids = set()
        for text in grp["texts"]:
            hits = [p for p in preds if p["path"].startswith(grp["array"]) and
                    (norm(text) in norm(p["text"]) or norm(p["text"]) in norm(text)) and norm(p["text"])]
            ids |= {p["instances"].get(grp["array"]) for p in hits}
            if not hits:
                ids.add(None)
        group_ids.append(ids)
    groups_ok = [len(ids) == 1 and None not in ids and all(ids.isdisjoint(o) for j, o in enumerate(group_ids) if o is not ids)
                 for ids in group_ids]
    return {
        "recall": (len(found), len(gold["tags"])),
        "precision": (len(right), len(preds)),
        "groups": (sum(groups_ok), len(groups_ok)),
        "missed": [g["text"] for g in gold["tags"] if g not in found],
        "wrong": [(p["text"][:50], p["path"]) for p in preds if p not in right],
    }


def main() -> None:
    results = Path(sys.argv[1])
    verbose = "-v" in sys.argv
    totals = {"recall": [0, 0], "precision": [0, 0], "groups": [0, 0]}
    for gold_path in sorted(GOLD.glob("*.json")):
        result = results / f"{gold_path.stem}.jsonl"
        if not result.exists():
            print(f"{gold_path.stem:<20} (no result)")
            continue
        s = score(json.loads(gold_path.read_text()), predictions(result))
        for k in totals:
            totals[k][0] += s[k][0]
            totals[k][1] += s[k][1]
        fmt = lambda k: f"{k} {s[k][0]}/{s[k][1]}"
        print(f"{gold_path.stem:<20} {fmt('recall'):<16} {fmt('precision'):<18} {fmt('groups')}")
        if verbose:
            print("    missed:", s["missed"])
            print("    wrong: ", s["wrong"])
    r, p, g = (totals[k] for k in ("recall", "precision", "groups"))
    rr, pp = r[0] / max(r[1], 1), p[0] / max(p[1], 1)
    print(f"{'TOTAL':<20} recall {rr:.3f} ({r[0]}/{r[1]})  precision {pp:.3f} ({p[0]}/{p[1]})  "
          f"F1 {2 * rr * pp / max(rr + pp, 1e-9):.3f}  groups {g[0]}/{g[1]}")


if __name__ == "__main__":
    main()
