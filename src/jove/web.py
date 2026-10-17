"""Web surface: upload pages + schema, poll, view the result, download JSONL or the page."""

import json
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from . import config, pipeline, view
from .choose import Chooser
from .ocr import Ocr
from .schema import SchemaError, build_tree


def create_app(
    data_dir: Path | None = None,
    make_ocr: Callable[[], Ocr] = config.make_ocr,
    make_chooser: Callable[[], Chooser] = config.make_chooser,
    schemas_dir: Path | None = None,
) -> FastAPI:
    root = (data_dir or config.data_dir()) / "jobs"
    schemas = _schemas(schemas_dir or config.schemas_dir())
    app = FastAPI(title="Jove")

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
        (d / "status.json").write_text(json.dumps({"status": status, "error": error, "updated": _now()}))

    def work(job_id: str, d: Path, files: list[tuple[str, bytes]], schema: dict, context: str | None) -> None:
        set_status(d, "running")
        try:
            pipeline.run(job_id, d, files, schema, make_ocr(), make_chooser(), context=context,
                         gate=config.split_gate(), owners=config.owners(), layout_engine=config.layout(),
                         paragraphs=config.paragraphs())
        except Exception as e:
            set_status(d, "failed", f"{type(e).__name__}: {e}")
        else:
            set_status(d, "done")

    def page(job_id: str, standalone: bool) -> str:
        d = job_dir(job_id)
        status = json.loads((d / "status.json").read_text())
        path = d / "result.jsonl"
        records = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        images = {p.stem: p for p in (d / "pages").glob("p*.png")} if standalone else None
        return view.render(job_id, status, records, _meta(d), images)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return view.index(list(schemas.values()), _recent(root))

    @app.post("/jobs")
    async def submit(request: Request, background: BackgroundTasks, files: list[UploadFile],
                     schema: str | None = Form(None), schema_name: str | None = Form(None),
                     context: str | None = Form(None)):
        if schema and schema.strip():
            text, title = schema, None
        elif schema_name in schemas:
            text, title = schemas[schema_name]["text"], schemas[schema_name]["title"]
        else:
            raise HTTPException(400, "bad schema: give schema (JSON text) or a known schema_name")
        try:
            parsed = json.loads(text)
            build_tree(parsed)
        except (json.JSONDecodeError, SchemaError) as e:
            raise HTTPException(400, f"bad schema: {e}")
        uploads = [(f.filename or f"upload{i}", await f.read()) for i, f in enumerate(files)]
        job_id = uuid.uuid4().hex
        d = root / job_id
        d.mkdir(parents=True)
        (d / "schema.json").write_text(json.dumps(parsed, indent=2))
        (d / "job.json").write_text(json.dumps({"files": [name for name, _ in uploads],
                                                "schema": title or parsed.get("title"), "created": _now()}))
        set_status(d, "queued")
        background.add_task(work, job_id, d, uploads, parsed, (context or "").strip() or None)
        if "text/html" in request.headers.get("accept", ""):  # the form: go and watch the result
            return RedirectResponse(f"/jobs/{job_id}/view", status_code=303)
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

    @app.get("/jobs/{job_id}/view", response_class=HTMLResponse)
    def result_view(job_id: str) -> str:
        return page(job_id, standalone=False)

    @app.get("/jobs/{job_id}/view.html")
    def result_file(job_id: str):
        """The result page as one self-contained file (page images inlined), to save or share."""
        if not (job_dir(job_id) / "result.jsonl").exists():
            raise HTTPException(409, "job not finished")
        return Response(page(job_id, standalone=True), media_type="text/html",
                        headers={"Content-Disposition": f'attachment; filename="jove-{job_id[:8]}.html"'})

    @app.get("/jobs/{job_id}/pages/{page_id}.png")
    def page_image(job_id: str, page_id: str):
        path = job_dir(job_id) / "pages" / f"{page_id}.png"
        if not re.fullmatch(r"p\d{4}", page_id) or not path.exists():
            raise HTTPException(404, "no such page")
        return FileResponse(path, media_type="image/png")

    return app


def _schemas(folder: Path) -> dict[str, dict]:
    """The *.schema.json files offered on the form, by file name (invalid ones are skipped)."""
    out = {}
    for path in sorted(folder.glob("*.schema.json")) if folder.is_dir() else []:
        try:
            text = path.read_text()
            parsed = json.loads(text)
            build_tree(parsed)
        except (OSError, json.JSONDecodeError, SchemaError):
            continue
        name = path.name.removesuffix(".schema.json")
        out[name] = {"name": name, "title": parsed.get("title") or name, "description": parsed.get("description"),
                     "text": text}
    return out


def _meta(d: Path) -> dict:
    try:
        return json.loads((d / "job.json").read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _recent(root: Path, n: int = 8) -> list[dict]:
    jobs = []
    for d in root.glob("*/status.json") if root.is_dir() else []:
        meta, status = _meta(d.parent), json.loads(d.read_text())
        created = meta.get("created") or ""
        jobs.append({"id": d.parent.name, "status": status["status"], "created": created,
                     "label": ", ".join(meta.get("files", [])) or d.parent.name[:8],
                     "when": _local(created)})
    return sorted(jobs, key=lambda j: j["created"], reverse=True)[:n]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _local(stamp: str) -> str:
    try:
        return datetime.fromisoformat(stamp).astimezone().strftime("%b %-d, %H:%M")
    except ValueError:
        return ""


def __getattr__(name: str):
    # `uvicorn jove.web:app` builds the real app lazily so imports stay side-effect free.
    if name == "app":
        return create_app()
    raise AttributeError(name)
