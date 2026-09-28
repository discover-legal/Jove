# LegalEyes

Give it scanned pages and a data schema. It tags every piece of text with the
schema field it gives a value for, working coarse to fine through the schema
tree (block → line → word), and emits a JSONL graph for downstream machines.
Fully automatic, with no review UI.

See [docs/PLAN.md](docs/PLAN.md) for the tagging method, output format and roadmap.
The reference schema is [schemas/legal_intake.schema.json](schemas/legal_intake.schema.json)
(Ontario law-firm intake).

## Run

```sh
uv sync --extra ocr --extra jev

# local (default): any OpenAI-compatible endpoint, e.g. Ollama
ollama pull qwen2.5:7b
uv run uvicorn legaleyes.web:app

# or TypeSafe Jev
LE_CHOOSER=jev TYPESAFE_API_KEY=... uv run uvicorn legaleyes.web:app
```

The first run downloads RapidOCR's English recognition model (9 MB, hash-checked)
to `~/.cache/legaleyes`.

```sh
curl -F files=@scan.pdf -F schema=@schemas/legal_intake.schema.json localhost:8000/jobs
curl localhost:8000/jobs/<id>
curl localhost:8000/jobs/<id>/result.jsonl
```

## Test

```sh
uv run pytest
```
