"""Entity resolution: which names, in which documents, are the same person or organisation.

Every name the tagger found (a party's name, their lawyer, the names on each side of a
case, a document's author or recipient) is a mention. Values that hold several names
("Marcus Oyelaran and Priya Oyelaran", "Smith, Jones & Co. LLP") are split first.
Mentions are then grouped, each merge with the reason a person can check:

- the same name, ignoring case, punctuation, titles ("Ms.") and the form of a company
  suffix ("Inc." / "Incorporated");
- a small OCR difference ("Whitcornbe" / "Whitcombe");
- a shorter form of one person's name ("D. Whitcombe", "Jane Doe" for "Jane Alexandra Doe"),
  only when exactly one person fits; otherwise it is flagged as ambiguous;
- a lone surname or word ("Whitcombe") joins a fuller name only within its own document,
  and only when one fits.

Each entity carries flags for a conflict check: the client in one place and another
party in another, or two different dates of birth or registration numbers.
Deterministic and local: no model is asked.
"""

import re
from difflib import SequenceMatcher

# Fields whose values name a person or an organisation.
NAME_FIELDS = ("name", "other_names[]", "organization", "represented_by", "names[]", "author",
               "recipient", "lender")
# Facts on a party record that must agree across its mentions.
FACTS = {"date_of_birth": "date of birth", "registration_number": "registration number"}

# Company suffixes, as one canonical word each; two-word forms are folded first.
SUFFIXES = {"inc": "inc", "incorporated": "inc", "ltd": "ltd", "limited": "ltd", "corp": "corp",
            "corporation": "corp", "co": "co", "company": "co", "llp": "llp", "lp": "lp", "llc": "llc",
            "pte": "pte", "plc": "plc", "ulc": "ulc", "gp": "gp", "pc": "pc", "srl": "srl", "sa": "sa",
            "ltee": "ltd", "ltée": "ltd", "dmcc": "dmcc", "fze": "fze", "gmbh": "gmbh", "ag": "ag", "bv": "bv",
            "nv": "nv", "sarl": "sarl", "pty": "pty", "sas": "sas", "spa": "spa", "oy": "oy", "ab": "ab"}
PHRASES = {"professional corporation": "pc", "general partnership": "gp", "limited partnership": "lp"}
# Words that make a name an organisation even without a suffix.
ORG_WORDS = set("""holdings holding services service management group capital transit solutions infrastructure
lands land development ministry minister department tribunal board court bank trust trustee guardian partners
partnership constructors legal law property properties rental rentals realty insurance agency authority
commission council city region regional municipality government university college school hospital association
society foundation international enterprises industries systems technologies consulting investments trading
export energy corporation office registry clinic centre center network fund securities agent security
financial credit union church police services commissioner""".split())
TITLES = {"mr", "mrs", "ms", "miss", "mx", "dr", "hon", "honourable", "the", "justice", "madam", "sir", "me"}
JUDGE = re.compile(r",?\s+(?:C\.?J\.?O\.?|J\.?J?\.?A\.?|J\.)$")  # "Carnwath J.", "Doherty J.A."
CONNECTORS = {"of", "and", "de", "du", "des", "la", "le", "for", "&"}
SPLIT = re.compile(r"\s*;\s*|\s*,\s*|\s+v\.?\s+|\s+and\s+|\s+&\s+|\s+et\s+")
FOR = re.compile(r",?\s+for\s+the\s+(?:[a-z]+\s*)+(?:\([^)]*\)\s*)?(?=[A-Z]|$)")
OCR_SAME = 0.9  # SequenceMatcher ratio above which two long names differ only by OCR noise


def _words(name: str) -> list[str]:
    return [w for w in re.split(r"\s+", name.strip()) if w]


def _bare(word: str) -> str:
    return re.sub(r"[^\w&]", "", word.casefold())


def _is_suffix_run(words: list[str]) -> bool:
    """"Co. LLP", "Limited": a piece that only finishes the company name before it."""
    return bool(words) and all(_bare(w) in SUFFIXES or _bare(w) in ("&", "and") for w in words)


def kind(name: str) -> str:
    """"organisation", "person" or "partial" (one word: a surname, a first name or a fragment)."""
    words = [_bare(w) for w in _words(name)]
    words = [w for w in words if w]
    text = " ".join(words)
    if any(w in SUFFIXES for w in words[1:]) or any(p in text for p in PHRASES) or any(w in ORG_WORDS for w in words):
        return "organisation"
    if any(w in CONNECTORS for w in words):
        return "organisation"
    raw = _words(name)
    if not name.isupper() and any(len(w) >= 3 and w.isupper() and w.isalpha() for w in raw):
        return "organisation"  # "UIL Commodities": an acronym in a mixed-case name
    if JUDGE.search(name):
        return "person"  # "Carnwath J.": a judge, by surname
    people = [w for w in words if w not in TITLES]
    return "person" if len(people) >= 2 else "partial"


def tidy(name: str) -> str:
    """For display: no quotes, titles or trailing punctuation; ALL CAPS becomes Title Case."""
    name = name.strip(" \t\"'“”‘’,;:()[]")
    if name.endswith(".") and not (_words(name) and (_bare(_words(name)[-1]) in SUFFIXES or len(_bare(_words(name)[-1])) == 1)):
        name = name[:-1]
    words = _words(name)
    while len(words) > 1 and _bare(words[0]) in TITLES:
        words = words[1:]
    name = " ".join(words).strip(" ,;:")
    if name.isupper() and len(name) > 3:
        name = " ".join(w if _bare(w) in SUFFIXES and len(w) <= 3 else w.lower() if _bare(w) in CONNECTORS
                        else w.capitalize() for w in name.split())
    return name


def split(value: str) -> list[str]:
    """The names in one value: "Marcus Oyelaran and Priya Oyelaran" -> two; "Smith & Co. LLP",
    "Ontario Infrastructure and Lands Corporation" -> one each. A company named inside a
    sentence ("We act for Acme Property Management Inc. Our client...") is picked out."""
    if _sentence(value):
        return [m for m in _orgs_in(value)]
    parts = [p for p in SPLIT.split(value)]
    seps = SPLIT.findall(value)
    out: list[str] = []
    joined = parts[0]
    for sep, part in zip(seps, parts[1:]):
        lead = joined.strip()
        if (_is_suffix_run(_words(part)) or (kind(lead) == "organisation" and not _has_suffix(lead)
                                             and not _first_lower(part) and part.strip())
                or (not part.strip())):
            if part.strip():
                joined += sep + part
            continue
        out.append(joined)
        joined = part
    out.append(joined)
    names = []
    for p in out:
        words = _words(p)
        while words and (_bare(words[0]) in CONNECTORS or words[0][:1].islower()):
            words = words[1:]  # "and Metrolinx", "for the"
        while words and (_bare(words[-1]) in CONNECTORS or words[-1][:1].islower()):
            words = words[:-1]
        p = tidy(" ".join(words))
        if len(_bare(p)) >= 2 and not re.fullmatch(r"(?:\w\.\s*)+", p) and not _is_suffix_run(_words(p)):
            names.append(p)
    return names


def _has_suffix(name: str) -> bool:
    words = [_bare(w) for w in _words(name)]
    return any(w in SUFFIXES for w in words[1:]) or any(p in " ".join(words) for p in PHRASES)


def _first_lower(part: str) -> bool:
    w = _words(part)
    return bool(w) and w[0][:1].islower() and _bare(w[0]) not in CONNECTORS


def _sentence(value: str) -> bool:
    """Prose rather than a list of names: three or more lowercase words that are not connectors."""
    return sum(1 for w in _words(value) if w[:1].islower() and _bare(w) not in CONNECTORS) >= 3


def _orgs_in(text: str) -> list[str]:
    """Capitalised runs that end in a company suffix: "We act for Acme Property Management Inc." -> Acme…"""
    words = _words(text)
    out = []
    for i, w in enumerate(words):
        if _bare(w) in SUFFIXES and i > 0 and not (i + 1 < len(words) and _bare(words[i + 1]) in SUFFIXES):
            j = i
            while j > 0 and (words[j - 1][:1].isupper() or _bare(words[j - 1]) in ("&", "of", "and")
                             or _bare(words[j - 1]) in SUFFIXES):
                j -= 1
            while j < i and (_bare(words[j]) in CONNECTORS or _bare(words[j]) in TITLES | {"we", "our"}):
                j += 1
            if j < i:
                out.append(tidy(" ".join(words[j:i + 1])))
    return out


def key(name: str) -> str:
    """The form names are compared in: "Acme Property Management Incorporated" -> "acme property management inc"."""
    text = " ".join(_bare(w) for w in _words(name))
    text = text.replace("&", " and ")
    for phrase, short in PHRASES.items():
        text = text.replace(phrase, short)
    words = [SUFFIXES.get(w, w) for w in text.split()]
    while len(words) > 1 and words[0] in TITLES:
        words = words[1:]
    return " ".join(w for w in words if w)


def _core(k: str) -> list[str]:
    """An organisation's key without suffixes or "and": what names it."""
    return [w for w in k.split() if w not in SUFFIXES.values() and w != "and"]


def _person(k: str) -> tuple[list[str], str]:
    words = [w for w in k.split() if w not in TITLES]
    return words[:-1], words[-1] if words else ""


def _fits(short: str, full: str) -> bool:
    """Could person `short` be a shorter way of writing `full`? Same surname; each given name
    the same or its initial, in order, missing ones allowed ("Jane Doe" fits "Jane Alexandra Doe")."""
    (sg, ss), (fg, fs) = _person(short), _person(full)
    if ss != fs or not sg or not fg:
        return False
    if sg[0] != fg[0] and not (len(sg[0]) == 1 and fg[0].startswith(sg[0])):
        return False
    rest = iter(fg[1:])
    return all(any(g == f or (len(g) == 1 and f.startswith(g)) for f in rest) for g in sg[1:])


def resolve(mentions: list[dict]) -> list[dict]:
    """Group mentions into entities. A mention is a dict with at least "as" (the name), "doc",
    "owner" (an owner key or None) and "facts" ({fact: value}); it is returned inside its
    entity, with "how": why it joined. Entities come in order of first mention."""
    for m in mentions:
        m["key"], m["kind"] = key(m["as"]), kind(m["as"])
    mentions = _unprefix(mentions)
    for n, m in enumerate(mentions):
        m["n"] = n
    groups: list[dict] = []
    by_key: dict[str, dict] = {}
    lone: dict[str, dict] = {}  # a word alone, in documents where no fuller name has it

    def new(m: dict, how: str) -> dict:
        grp = {"kind": m["kind"], "mentions": [], "flags": []}
        groups.append(grp)
        return add(grp, m, how)

    def add(grp: dict, m: dict, how: str) -> dict:
        m["how"] = how
        grp["mentions"].append(m)
        by_key.setdefault(m["key"], grp)
        if m["kind"] == "organisation" or grp["kind"] == "partial":
            grp["kind"] = m["kind"] if m["kind"] != "partial" else grp["kind"]
        return grp

    # Fullest first, so short forms ("D. Whitcombe", "Whitcombe") meet the names they shorten.
    order = sorted(range(len(mentions)), key=lambda i: (mentions[i]["kind"] == "partial",
                                                        -sum(len(w) > 1 for w in mentions[i]["key"].split()), i))
    for i in order:
        m = mentions[i]
        same = by_key.get(m["key"])
        if same and (m["kind"] != "partial" or same["kind"] == "partial"
                     or any(o["doc"] == m["doc"] for o in same["mentions"])):
            add(same, m, "same name")
            continue
        if m["kind"] == "partial" and m["key"] in lone:
            add(lone[m["key"]], m, "same name")
            continue
        if m["kind"] == "organisation":
            core = _core(m["key"])
            match = next((g for g in groups if g["kind"] == "organisation" and any(
                (len(core) >= 2 and _core(o["key"]) == core) for o in g["mentions"])), None)
            if match:
                add(match, m, "same name, other company suffix")
                continue
        if m["kind"] != "partial":
            match = next((g for g in groups if g["kind"] == m["kind"] and any(
                len(m["key"]) >= 10 and m["key"][:1] == o["key"][:1]
                and SequenceMatcher(None, m["key"], o["key"]).ratio() >= OCR_SAME for o in g["mentions"])), None)
            if match:
                add(match, m, "same name but for a letter or two (OCR?)")
                continue
        if m["kind"] == "person":
            fits = [g for g in groups if g["kind"] == "person" and any(_fits(m["key"], o["key"]) for o in g["mentions"])]
            if len(fits) == 1:
                add(fits[0], m, "a shorter form of the name")
                continue
            grp = new(m, "")
            if len(fits) > 1:
                grp["flags"].append(f"“{m['as']}” could be any of: " + ", ".join(_best(g) for g in fits))
            continue
        if m["kind"] == "partial":
            word = m["key"].split()[-1] if m["key"] else ""
            fits = [g for g in groups if g["kind"] != "partial" and any(
                o["doc"] == m["doc"] and word and (word == _person(o["key"])[1] if g["kind"] == "person"
                                                   else word in _core(o["key"])) for o in g["mentions"])]
            if len(fits) == 1:
                add(fits[0], m, "part of the name, in the same document")
                continue
            lone[m["key"]] = new(m, "")
            continue
        new(m, "")

    for grp in groups:
        grp["mentions"].sort(key=lambda m: m["n"])
        grp["name"] = _best(grp)
        grp["aliases"] = sorted({m["as"] for m in grp["mentions"]} - {grp["name"]})
        _flag(grp)
    groups.sort(key=lambda g: g["mentions"][0]["n"])
    for grp in groups:
        for m in grp["mentions"]:
            del m["n"]
    return groups


def _unprefix(mentions: list[dict]) -> list[dict]:
    """"Priya Oyelaran Lakeshore Rental Holdings Inc." when "Lakeshore Rental Holdings Inc."
    is named elsewhere: two names run together, so two mentions."""
    orgs = {m["key"] for m in mentions if m["kind"] == "organisation"}
    out = []
    for m in mentions:
        core = _core(m["key"])
        cut = next((len(core) - len(_core(o)) for o in orgs if o != m["key"] and len(_core(o)) >= 2
                    and len(_core(o)) < len(core) and core[-len(_core(o)):] == _core(o)), 0)
        words = _words(m["as"])
        head = " ".join(words[:cut])
        if m["kind"] == "organisation" and cut and kind(head) == "person":
            for part in (head, " ".join(words[cut:])):
                out.append({**m, "as": part, "key": key(part), "kind": kind(part)})
        else:
            out.append(m)
    return out


def _best(grp: dict) -> str:
    """The fullest way the entity is written, preferring mixed case."""
    written = [m["as"] for m in grp["mentions"]]
    return max(written, key=lambda a: (m_kind_rank(a), len(_words(a)), written.count(a), not a.isupper(), -written.index(a)))


def m_kind_rank(name: str) -> int:
    return {"organisation": 2, "person": 2, "partial": 0}[kind(name)]


OWNER_WORDS = {"client": "our client", "party": "another party", "issuer": "the issuer"}


def _flag(grp: dict) -> None:
    roles: dict[str, list[str]] = {}
    for m in grp["mentions"]:
        if m.get("owner") in ("client", "party"):
            roles.setdefault(m["owner"], []).append(m.get("doc_name", m["doc"]))
    if "client" in roles and "party" in roles:
        grp["flags"].append("Conflict check: our client in " + ", ".join(dict.fromkeys(roles["client"]))
                            + "; another party in " + ", ".join(dict.fromkeys(roles["party"])))
    for fact, words in FACTS.items():
        seen: dict[str, list[str]] = {}
        for m in grp["mentions"]:
            if v := (m.get("facts") or {}).get(fact):
                seen.setdefault(re.sub(r"\W", "", v.casefold()), []).append(v)
        if len(seen) > 1:
            grp["flags"].append(f"Different {words}: " + " / ".join(vs[0] for vs in seen.values()))
    if grp["kind"] == "partial":
        grp["flags"].append("Only part of a name: check the source")


def mentions(recs: list[dict]) -> list[dict]:
    """The names in jove.graph records, one mention per name, with the record's facts
    (a party's date of birth) and where it was read."""
    out = []
    for rec in recs:
        facts = {f: r["value"] for r in rec["rows"] for f in FACTS if r["path"].endswith("." + f)}
        for r in rec["rows"]:
            leaf = r["path"].rsplit(".", 1)[-1]
            if leaf not in NAME_FIELDS:
                continue
            # "Rahul Shastri and Kristie Jennings, for the respondents Rutmet Inc.": counsel, then whom they act for
            cut = FOR.search(r["value"])
            counsel, client = (r["value"][:cut.start()], r["value"][cut.end():]) if cut else (r["value"], "")
            pieces = [(n, r["field"]) for n in split(counsel)] + [(n, "Represented party") for n in split(client)]
            for name, field in pieces:
                own = leaf == "name" and field == r["field"]  # the record is this party; their lawyer's facts are not
                out.append({"as": name, "doc": rec["doc"], "record": rec["key"], "title": rec["title"],
                            "field": field, "path": r["path"], "value": r["value"], "text": r["text"],
                            "page": r["page"], "line": r["line"], "line_id": r["line_id"], "score": r["score"],
                            "owner": rec.get("owner_key") if own else None, "facts": facts if own else {}})
    return out
