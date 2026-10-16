import json
import time

from fastapi.testclient import TestClient

from jove.choose import FakeChooser
from jove.ocr import FakeOcr
from jove.pipeline import run
from jove.web import create_app

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


def test_numpy_boxes_serialize(tmp_path):
    import numpy as np

    from jove.ocr import OcrLine

    class NumpyOcr:
        def describe(self):
            return {"provider": "numpy"}

        def read(self, page):
            return [OcrLine("Jane Doe", tuple(np.float32(v) for v in (0, 0, 80, 18)), 0.99)]

    out = run("j", tmp_path, [("b.png", png())], SCHEMA, NumpyOcr(), chooser())
    assert all(json.loads(line) for line in out.read_text().splitlines())



def test_pdf_text_layer_is_used(tmp_path):
    from pathlib import Path

    from jove.ingest import ingest
    from jove.ocr import TextLayerFirst

    class Never:
        def describe(self):
            return {}

        def read(self, page):
            raise AssertionError("OCR must not run on a born-digital page")

    pdf_bytes = (Path(__file__).parent.parent / "eval/ocr/real/divct_endorsement.pdf").read_bytes()
    _, pages = ingest([("e.pdf", pdf_bytes)], tmp_path)
    lines = TextLayerFirst(Never()).read(pages[0])
    texts = [l.text for l in lines]
    assert "Court File No. 490/00" in texts and "SUPERIOR COURT OF JUSTICE" in texts
    box = lines[texts.index("Court File No. 490/00")].bbox
    assert 0 < box[0] < box[2] <= pages[0].width and 0 < box[1] < box[3] < pages[0].height / 4  # top right of page


def test_images_have_no_text_layer(tmp_path):
    from jove.ingest import ingest

    _, pages = ingest([("b.png", png())], tmp_path)
    assert pages[0].text_layer is None


def test_filled_pdf_form_values_are_read(tmp_path):
    from pathlib import Path

    from jove.ingest import ingest

    pdf = (Path(__file__).parent.parent / "eval/tagging/docs/n4_filled.pdf").read_bytes()
    _, pages = ingest([("n4.pdf", pdf)], tmp_path)
    form_values = {t for p in pages for t, _, form in p.text_layer if form}
    printed = {t for p in pages for t, _, form in p.text_layer if not form}
    assert {"Marcus Oyelaran and Priya Oyelaran", "3,780.00", "dwhitcombe@whitcombelegal.ca"} <= form_values
    assert "Address of Rental Unit:" in printed and "Address of Rental Unit:" not in form_values


def test_form_values_are_marked_in_questions(tmp_path):
    from pathlib import Path

    from jove.ingest import ingest
    from jove.layout import build
    from jove.ocr import TextLayerFirst
    from jove.tagger import Tagger, _Task
    from jove.schema import build_tree

    pdf = (Path(__file__).parent.parent / "eval/tagging/docs/n4_filled.pdf").read_bytes()
    _, pages = ingest([("n4.pdf", pdf)], tmp_path)
    lines = [l for b in build("p0001", TextLayerFirst(None).read(pages[0])) for l in b.children]
    value = next(l for l in lines if l.text == "3,780.00")
    label = next(l for l in lines if l.text == "Address of Rental Unit:")
    assert value.form and not label.form
    tree = build_tree(SCHEMA)
    q = Tagger(tree, None)._question(_Task(value, tree, None, {}), tree)
    assert "fillable form field" in q.instructions and "label" in q.options


def test_ingest_turns_phone_photos_upright(tmp_path):
    import io

    from PIL import Image

    from jove.ingest import ingest

    img = Image.new("RGB", (300, 200), "white")  # stored landscape...
    exif = img.getexif()
    exif[0x0112] = 6  # ...with "rotate 90° clockwise to display", as phones write it
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif)
    _, pages = ingest([("photo.jpg", buf.getvalue())], tmp_path)
    assert (pages[0].width, pages[0].height) == (200, 300)
