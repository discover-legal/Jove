"""Map the detailed intake gold (gold_detailed/) onto the coarse legal_intake schema (gold/).

    uv run python eval/tagging/make_coarse_gold.py   # rewrites eval/tagging/gold/

Each fine path has one coarse home: names and contact details lose their owner
(the owners pass records it instead), money moves to amounts[], dates to
events[] or deadlines[]. Judges go to proceedings[].judges[], and may also be
tagged as party names. Documents with another schema are copied unchanged, so
totals stay comparable.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).parent
COARSE = "schemas/legal_intake.schema.json"
DETAILED = "schemas/legal_intake_detailed.schema.json"

# (pattern, replacement), first match wins; patterns are anchored regexes over fine paths.
RULES = [
    (r"(client|document_issuer|parties\[\])\.contact\.address\.(\w+)", r"contact.addresses[].\2"),
    (r"property\.address\.(\w+)", r"contact.addresses[].\1"),
    (r"(client|document_issuer|parties\[\])\.contact\.(phones|emails)\[\]", r"contact.\2[]"),
    (r"document_issuer\.contact\.website", "contact.websites[]"),
    (r"client\.identification\[\]\.(\w+)", r"identification[].\1"),
    (r"client\.person\.full_name|client\.organization\.legal_name|document_issuer\.name", "parties[].name"),
    (r"proceedings\[\]\.judge", "proceedings[].judges[]"),
    (r"document_issuer\.department", "parties[].organization"),
    (r"client\.person\.(date_of_birth|occupation)", r"parties[].\1"),
    (r"client\.person\.other_names\[\]", "parties[].other_names[]"),
    (r"matter\.key_deadlines\[\]\.(\w+)", r"deadlines[].\1"),
    (r"proceedings\[\]\.filing_date", "events[].date"),
    (r"proceedings\[\]\.next_hearing_date", "deadlines[].date"),
    (r"property\.(price|deposit|mortgage_amount)", "amounts[].amount"),
    (r"referenced_documents\[\]\.(\w+)", r"documents[].\1"),
    (r"referral\.source", "specifics.referral_source"),
    (r"(amounts|events|parties|proceedings)\[\]\..+|matter\..+", r"\g<0>"),  # unchanged
]


def coarse(path: str) -> str:
    for pattern, repl in RULES:
        if re.fullmatch(pattern, path):
            return re.sub(pattern, repl, path)
    raise ValueError(f"no coarse home for {path}")


def main() -> None:
    out = ROOT / "gold"
    out.mkdir(exist_ok=True)
    for path in sorted((ROOT / "gold_detailed").glob("*.json")):
        gold = json.loads(path.read_text())
        if gold["schema"] == DETAILED:
            gold["schema"] = COARSE
            for tag in gold["tags"]:
                tag["path"] = coarse(tag["path"])
                alt = [coarse(a) for a in tag.get("alt", [])]
                alt += ["parties[].name"] if tag["path"] == "proceedings[].judges[]" else []
                alt = [a for a in dict.fromkeys(alt) if a != tag["path"]]
                if alt:
                    tag["alt"] = alt
                else:
                    tag.pop("alt", None)
        (out / path.name).write_text(json.dumps(gold, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
