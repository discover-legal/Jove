import json
import time

from fastapi.testclient import TestClient

from legaleyes.choose import FakeChooser
from legaleyes.ocr import FakeOcr
from legaleyes.pipeline import run
from legaleyes.web import create_app

from .conftest import pdf, png
from .test_tagger import RULES, rule

SCHEMA = {
    "type": "object",
    "properties": {
        "client": {"type": "object", "properties": {"name": {"type": "string"}, "dob": {"type": "string"}}},
        "parties": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "role": {"type": "string"}}}},
    },
}
LINE = "Name: Jane Doe DOB: 1980"
OCR = FakeOcr({
    "p0001": ["INTAKE FORM", "", LINE],
    "p0002": ["Blank page footer"],
    "p0003": ["Acme", "landlord"],
})


def chooser():
    return FakeChooser(rule(RULES | {("client", LINE): {"client.name": 0.5, "client.dob": 0.45},
                                     ("parties[]", "Acme\nlandlord"): {"parties[].name": 0.5, "parties[].role": 0.45}}))


def records(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_pipeline_emits_graph(tmp_path):
    out = run("job1", tmp_path, [("a.pdf", pdf(2)), ("b.png", png())], SCHEMA, OCR, chooser())
    recs = records(out)
    kinds = [r["type"] for r in recs]
    assert kinds[0] == "job" and kinds.count("page") == 3
    assert kinds.index("node") > kinds.index("page") and kinds.index("edge") > kinds.index("node")

    pages = {r["page_id"]: r for r in recs if r["type"] == "page"}
    assert pages["p0001"]["tags"] == ["client.dob", "client.name"]
    assert not pages["p0002"]["placed"]
    assert pages["p0003"]["tags"] == ["parties[].name", "parties[].role"]

    nodes = {r["id"]: r for r in recs if r["type"] == "node"}
    tagged = {(e["from"], e["to"]) for e in recs if e["type"] == "edge" and e["rel"] == "tagged" and e["rank"] == 1}
    spans = {nodes[f]["text"]: t for f, t in tagged if nodes[f]["kind"] == "span"}
    assert spans == {"Name:": "client#none", "Jane Doe": "client.name", "DOB:": "client#none", "1980": "client.dob"}
    assert ("p0001/b01", "#none") in tagged  # the heading, explicitly none

    within = [e for e in recs if e["type"] == "edge" and e["rel"] == "within"]
    assert {(e["from"], e["to"]) for e in within} == {("p0001/b02", "client"), ("p0003/b01", "parties[]")}

    party = [e["instances"] for e in recs if e["type"] == "edge" and e["rel"] == "tagged"
             and e["rank"] == 1 and e["from"].startswith("p0003")]
    assert party == [{"parties[]": "p0003/b01"}] * 2

    contains = {(e["from"], e["to"]) for e in recs if e["type"] == "edge" and e["rel"] == "contains"}
    assert ("p0001", "p0001/b02") in contains and ("p0001/b02/l01", "p0001/b02/l01/s02") in contains


def test_web_roundtrip(tmp_path):
    client = TestClient(create_app(tmp_path, make_ocr=lambda: OCR, make_chooser=chooser, separation=1.5))
    resp = client.post("/jobs", files=[("files", ("a.pdf", pdf(2))), ("files", ("b.png", png()))],
                       data={"schema": json.dumps(SCHEMA)})
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]
    for _ in range(50):
        status = client.get(f"/jobs/{job_id}").json()
        if status["status"] in ("done", "failed"):
            break
        time.sleep(0.05)
    assert status == {"status": "done", "error": None}
    body = client.get(f"/jobs/{job_id}/result.jsonl").text
    assert json.loads(body.splitlines()[0])["job_id"] == job_id


def test_web_rejects_bad_schema(tmp_path):
    client = TestClient(create_app(tmp_path, make_ocr=lambda: OCR, make_chooser=chooser))
    resp = client.post("/jobs", files=[("files", ("b.png", png()))], data={"schema": '{"type":"array"}'})
    assert resp.status_code == 400


def test_web_unknown_job(tmp_path):
    client = TestClient(create_app(tmp_path))
    assert client.get("/jobs/../../etc").status_code == 404
    assert client.get("/jobs/" + "0" * 32).status_code == 404


def test_web_bad_upload_fails_job(tmp_path):
    client = TestClient(create_app(tmp_path, make_ocr=lambda: OCR, make_chooser=chooser))
    job_id = client.post("/jobs", files=[("files", ("x.png", b"not an image"))],
                         data={"schema": json.dumps(SCHEMA)}).json()["job_id"]
    status = client.get(f"/jobs/{job_id}").json()
    assert status["status"] == "failed" and "not a PDF or readable image" in status["error"]
