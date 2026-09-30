"""Minimal web surface: upload pages + schema, poll, download JSONL."""

import json
import uuid
from collections.abc import Callable
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from . import config, pipeline
from .ocr import Ocr
from .choose import Chooser
from .schema import SchemaError, build_tree

FORM = """<!doctype html>
<meta charset="utf-8"><title>LegalEyes</title>
<style>body{font:16px system-ui;max-width:40rem;margin:2rem auto;padding:0 1rem}
textarea{width:100%;height:14rem;font:13px monospace}</style>
<h1>LegalEyes</h1>
<form method="post" action="/jobs" enctype="multipart/form-data">
<p><label>Scanned pages (PDF / images)<br><input type="file" name="files" multiple required></label></p>
<p><label>Schema (JSON Schema)<br><textarea name="schema" required></textarea></label></p>
<p><label>Context (optional: whose file this is, e.g. "Our firm: Harper &amp; Leung LLP. Our client: Jane Doe.")<br>
<input name="context" style="width:100%"></label></p>
<p><button>Submit</button></p>
</form>"""


def create_app(
    data_dir: Path | None = None,
    make_ocr: Callable[[], Ocr] = config.make_ocr,
    make_chooser: Callable[[], Chooser] = config.make_chooser,
    separation: float | None = None,
) -> FastAPI:
    root = (data_dir or config.data_dir()) / "jobs"
    app = FastAPI(title="LegalEyes")

    def job_dir(job_id: str) -> Path:
        try:
            uuid.UUID(hex=job_id)
        except ValueError:
            raise HTTPException(404, "no such job")
        d = root / job_id
        if not d.is_dir():
            raise HTTPException(404, "no such job")
        return d

    def set_status(d: Path, status: str, error: str | None = None) -> None:
        (d / "status.json").write_text(json.dumps({"status": status, "error": error}))

    def work(job_id: str, d: Path, files: list[tuple[str, bytes]], schema: dict, context: str | None) -> None:
        set_status(d, "running")
        try:
            pipeline.run(job_id, d, files, schema, make_ocr(), make_chooser(),
                         separation if separation is not None else config.separation(), context=context,
                         gate=config.split_gate(), tagger=config.tagger())
        except Exception as e:
            set_status(d, "failed", f"{type(e).__name__}: {e}")
        else:
            set_status(d, "done")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return FORM

    @app.post("/jobs")
    async def submit(background: BackgroundTasks, files: list[UploadFile], schema: str = Form(...),
                     context: str | None = Form(None)):
        try:
            parsed = json.loads(schema)
            build_tree(parsed)
        except (json.JSONDecodeError, SchemaError) as e:
            raise HTTPException(400, f"bad schema: {e}")
        uploads = [(f.filename or f"upload{i}", await f.read()) for i, f in enumerate(files)]
        job_id = uuid.uuid4().hex
        d = root / job_id
        d.mkdir(parents=True)
        (d / "schema.json").write_text(json.dumps(parsed, indent=2))
        set_status(d, "queued")
        background.add_task(work, job_id, d, uploads, parsed, (context or "").strip() or None)
        return {"job_id": job_id}

    @app.get("/jobs/{job_id}")
    def status(job_id: str):
        return json.loads((job_dir(job_id) / "status.json").read_text())

    @app.get("/jobs/{job_id}/result.jsonl")
    def result(job_id: str):
        path = job_dir(job_id) / "result.jsonl"
        if not path.exists():
            raise HTTPException(409, "job not finished")
        return FileResponse(path, media_type="application/x-ndjson", filename=f"{job_id}.jsonl")

    return app


def __getattr__(name: str):
    # `uvicorn legaleyes.web:app` builds the real app lazily so imports stay side-effect free.
    if name == "app":
        return create_app()
    raise AttributeError(name)
