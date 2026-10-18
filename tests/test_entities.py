from jove.entities import key, kind, resolve, split


def _m(name, doc="a", owner=None, **facts):
    return {"as": name, "doc": doc, "owner": owner, "facts": facts}


def test_one_value_can_name_several():
    assert split("Marcus Oyelaran and Priya Oyelaran") == ["Marcus Oyelaran", "Priya Oyelaran"]
    assert split("Sharon Vogel, Peter Wardle and Cheryl Labiris, for the") == ["Sharon Vogel", "Peter Wardle", "Cheryl Labiris"]
    assert split("UD Trading Pte. Limited v. TransAsia Private Capital") == ["UD Trading Pte. Limited", "TransAsia Private Capital"]
    # but "and" and "&" inside one organisation's name do not split it
    assert split("Smith & Co. LLP") == ["Smith & Co. LLP"]
    assert split("Ontario Infrastructure and Lands Corporation") == ["Ontario Infrastructure and Lands Corporation"]
    assert split("PUBLIC GUARDIAN AND TRUSTEE") == ["Public Guardian and Trustee"]
    assert split("We act for Acme Property Management Inc. Our client advises that rent") == ["Acme Property Management Inc."]
    assert split("Ms. Jane Doe") == ["Jane Doe"] and split("J.") == [] and split("Limited, Rutmet Inc.") == ["Rutmet Inc."]


def test_kinds_and_keys():
    assert [kind(n) for n in ("Jane Doe", "Rutmet Inc.", "UIL Commodities DMCC", "Carnwath J.", "Whitcombe")] == \
        ["person", "organisation", "organisation", "person", "partial"]
    assert key("Acme Property Management Incorporated") == key("ACME PROPERTY MANAGEMENT INC.")
    assert key("Whitcombe Legal Services Professional Corporation") == "whitcombe legal services pc"


def test_resolution():
    found = resolve([_m("Daniel Whitcombe", "a"), _m("D. Whitcombe", "a"), _m("Daniel Whitcornbe", "b"),
                     _m("Jane Alexandra Doe", "a", "client", date_of_birth="1980-04-03"),
                     _m("Jane Doe", "b", "party", date_of_birth="1981-04-03"),
                     _m("Acme Property Management Inc.", "a"), _m("Acme Property Management Incorporated", "b"),
                     _m("Whitcombe", "a"), _m("Whitcombe", "c")])
    names = {e["name"]: e for e in found}
    assert [m["how"] for m in names["Daniel Whitcombe"]["mentions"]] == \
        ["", "a shorter form of the name", "same name but for a letter or two (OCR?)",
         "part of the name, in the same document"]
    jane = names["Jane Alexandra Doe"]
    assert jane["aliases"] == ["Jane Doe"]
    assert any(f.startswith("Conflict check: our client in a; another party in b") for f in jane["flags"])
    assert "Different date of birth: 1980-04-03 / 1981-04-03" in jane["flags"]
    assert len(names["Acme Property Management Inc."]["mentions"]) == 2
    assert names["Whitcombe"]["kind"] == "partial"  # in document c, where no fuller name fits


def test_initials_that_fit_two_people_stay_apart():
    found = resolve([_m("Daniel Whitcombe"), _m("David Whitcombe"), _m("D. Whitcombe")])
    assert len(found) == 3
    assert any("could be any of: Daniel Whitcombe, David Whitcombe" in f for e in found for f in e["flags"])


def test_run_together_names_are_parted_by_a_name_seen_elsewhere():
    found = resolve([_m("Priya Oyelaran Lakeshore Rental Holdings Inc.", "a"), _m("Lakeshore Rental Holdings Inc.", "b")])
    assert [e["name"] for e in found] == ["Priya Oyelaran", "Lakeshore Rental Holdings Inc."]


def test_counsel_and_whom_they_act_for():
    from jove.entities import mentions

    rec = {"doc": "d", "key": "k", "title": "Party 6", "owner_key": None, "rows": [
        {"path": "parties[].represented_by", "field": "Represented by", "text": "t", "page": 1, "line": 18,
         "line_id": "l", "score": 0.9, "value": "Rahul Shastri and Kristie Jennings, for the respondents Rutmet Inc."}]}
    assert [(m["as"], m["field"]) for m in mentions([rec])] == [
        ("Rahul Shastri", "Represented by"), ("Kristie Jennings", "Represented by"), ("Rutmet Inc.", "Represented party")]
