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
ollama pull qwen3.5:9b-16k      # tagging + OCR number witness
ollama pull glm-ocr             # OCR second reader
ollama pull granite3.2-vision   # OCR hallucination check
uv run uvicorn legaleyes.web:app

# or TypeSafe Jev
LE_CHOOSER=jev TYPESAFE_API_KEY=... uv run uvicorn legaleyes.web:app
```

The first run downloads the PP-OCRv6 models (hash-checked by RapidOCR).
The OCR benchmark lives in [eval/ocr](eval/ocr/README.md).

```sh
curl -F files=@scan.pdf -F schema=@schemas/legal_intake.schema.json \
     -F "context=Our firm: Harper & Leung LLP. Our client: Jane Doe." localhost:8000/jobs
curl localhost:8000/jobs/<id>
curl localhost:8000/jobs/<id>/result.jsonl
```

## Test

```sh
uv run pytest
```
