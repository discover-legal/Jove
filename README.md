# Jove

[![tests](https://github.com/discover-legal/Jove/actions/workflows/tests.yml/badge.svg)](https://github.com/discover-legal/Jove/actions/workflows/tests.yml)

Jove turns scanned documents into structured data. Give it scanned pages
(PDFs or images) and a data schema (a JSON Schema). It tags every piece of text
with the schema field that text gives a value for, and it emits a JSONL graph
for downstream systems. Each tag carries a hash that points back to the exact
page and bytes it came from.

It was built for Ontario law-firm intake (court endorsements, tribunal forms,
demand letters, client intake forms), but the schema is yours: the repo also
ships schemas for court filings (with e-signing and production-stamp data),
flyers and posted notices, and document structure.

- **Fully automatic.** There is no review UI. Jove tags and does not choose: if
  three lines all look like the client's name, all three are tagged with
  their scores, and picking one is the consumer's job.
- **Local by default.** OCR and tagging run on your own machine. The one
  hosted option (TypeSafe Jev) is opt-in, because it sends page text off the
  machine.
- **Pluggable.** The OCR engine and the "chooser" (the model that decides which
  field a line belongs to) are both swappable.

> Status: a working research prototype, not production software. Read
> [Results](#results) before relying on it.

## How it works

```
ingest ──► OCR ──► layout ──► tag ──► emit (JSONL graph)
```

1. **Ingest:** hash each file and rasterise every page.
2. **OCR:** born-digital PDFs use their own text layer. Scans and photos go
   through PP-OCRv6 (RapidOCR), and three local vision models vote to correct
   its lines. A last pass finds lines the detector never boxed (slanted or
   logo text) and adds them only if a second, cropped reading confirms them.
3. **Layout:** group lines into blocks.
4. **Tag:** for every line, ask a decision model which schema field the line
   gives a value for (or `none`, or `label` for a printed form caption). Lines
   holding several values are split into words and asked again. A final pass
   checks who each value belongs to (our client, the opposing party, the
   issuer).
5. **Emit:** a JSONL graph of pages, blocks, lines and spans, with ranked
   `tagged` edges and SHA-256 provenance. See [Graphs](#graphs) to view it in
   Obsidian, FalkorDB or TypeDB.

[docs/PLAN.md](docs/PLAN.md) covers the method, output format, configuration
and measurements in full.

## Choosers

The chooser is the model that answers the tagging questions. Set it with
`JOVE_CHOOSER`.

| `JOVE_CHOOSER` | Runs | Notes |
|---|---|---|
| `clef` (default) | local, llama.cpp | [Cloudflare Clef-Flash](https://huggingface.co/Cloudflare/clef-flash) (9B, Apache-2.0), an open decision model that speaks Jev's API and returns real option probabilities. |
| `jev` | hosted | [TypeSafe Jev](https://typesafe.ai) via `typesafe-sdk`. The most accurate option, and about 2 s per page. **Page text leaves the machine.** |
| `llm` | local, Ollama | Any OpenAI-compatible chat model (`qwen3.5:9b-16k` by default). The model *states* its probabilities as text: slower and less accurate than Clef. |

## Run

Requires Python ≥ 3.12, [uv](https://docs.astral.sh/uv/),
[Ollama](https://ollama.com) for the OCR models and a
[llama.cpp](https://github.com/ggml-org/llama.cpp/releases) release (b11655 or
later, which includes [Clef support](https://github.com/ggml-org/llama.cpp/pull/29831))
for the chooser. A GPU is strongly recommended; it was developed on an 8 GB
RTX 5060.

```sh
uv sync --extra ocr --extra jev

# OCR models, via Ollama
ollama pull glm-ocr               # second reader for every doubtful line
ollama pull granite3.2-vision     # hallucination check
ollama pull qwen3.5:9b-16k        # number witness
ollama pull qwen3-vl:4b-instruct  # locates lines the OCR detector missed

# the chooser: Clef-Flash Q4_K_M (6.2 GB), downloaded on first start
llama-server -hf ggml-org/Clef-Flash-GGUF:Q4_K_M --alias clef-flash \
    -c 16384 -b 16384 -ub 16384 -np 1 -fa on --sleep-idle-seconds 30 --port 8080

uv run uvicorn jove.web:app   # JOVE_CLEF_BASE_URL defaults to http://localhost:8080
```

The first run downloads the PP-OCRv6 models (hash-checked by RapidOCR). Then
open http://localhost:8000. Drop in files, pick a schema, and Jove takes you to a
results page: each page with its tagged text boxed, the values listed by field,
filters by field and score, and a "Download page" button that saves the result
as one self-contained HTML file. Or from the command line:

```sh
curl -F files=@scan.pdf -F schema_name=legal_intake \
     -F "context=Our firm: Harper & Leung LLP. Our client: Jane Doe." localhost:8000/jobs
curl localhost:8000/jobs/<id>                 # status
curl localhost:8000/jobs/<id>/result.jsonl    # the graph
# results page: http://localhost:8000/jobs/<id>/view
```

**Clef's server flags.** The decision head reads the whole request in one
batch, so `-b`/`-ub` cap the request size, and Jove halves any batch the server
rejects as too large. On 8 GB of VRAM, 16k is the most that fits beside the
weights. Clef and the OCR models take turns on the GPU: `--sleep-idle-seconds`
frees Clef's VRAM while it is idle (it wakes in about 5 s), and Jove unloads the
OCR models once every page is read.

### Hosted Jev

```sh
JOVE_CHOOSER=jev TYPESAFE_API_KEY=... uv run uvicorn jove.web:app
```

## Graphs

`jove.graph` turns results (one `result.jsonl` per document, as many as you
like) into graphs: the document tree (document → page → block → line → span),
each piece of text's tags, the list items values share and whose each value is.
Schema fields are shared, so documents meet at the fields they fill.

```sh
uv run python -m jove.graph data/jobs/*/result.jsonl --to tree               # indented text
uv run python -m jove.graph data/jobs/*/result.jsonl --to dot | dot -Tsvg > graph.svg
uv run python -m jove.graph data/jobs/*/result.jsonl --to obsidian --out vault/
uv run python -m jove.graph data/jobs/*/result.jsonl --to cypher --out graph.cypher
uv run python -m jove.graph data/jobs/*/result.jsonl --to typeql --out typedb/
```

- **Graphviz:** `--to dot | dot -Tsvg > graph.svg` draws the tree with each tag
  as a small diamond joining text, field, list item and owner. No server needed.
- **Obsidian:** open `vault/` as a vault. There is one note per node, edges
  are wikilinks, and the graph view comes coloured by kind.
- **FalkorDB** (or Neo4j or Memgraph via the `.cypher` file) holds a plain
  property graph:
  ```sh
  uv sync --extra graph
  docker run -d -p 6379:6379 -p 3000:3000 falkordb/falkordb
  uv run python -m jove.graph data/jobs/*/result.jsonl --load falkor://localhost:6379/jove
  ```
  Browse it at http://localhost:3000 and try
  `MATCH p=(:Document)-[:CONTAINS*]->() RETURN p`.
- **TypeDB** holds a hypergraph: each tag is one n-ary `tag` relation that
  links the text, its field, its list items and its owner:
  ```sh
  docker run -d -p 1729:1729 -p 8000:8000 typedb/typedb:3.13.6
  uv run python -m jove.graph data/jobs/*/result.jsonl --load typedb://localhost:1729/jove
  ```
  View it in [TypeDB Studio](https://studio.typedb.com) (connect to
  `http://localhost:8000`, user `admin`, password `password`). For example:
  `match $t isa tag, links (value: $v, field: $f, owner: $o); $f has path "parties[].name";`

Loading replaces the named graph or database.

## Results

Measured on [eval/tagging](eval/tagging/README.md): 11 documents and 230
hand-labelled values. The set covers public Ontario court and tribunal
decisions, a fax-degraded copy, an LTB Form N4 filled with invented values
(both digital and as a phone photo), synthetic intake documents, and two
real-world photos. All runs use identical cached OCR and the detailed intake
schema. (The event-flyer photo has since been removed. On the remaining 10
documents, Jev scores F1 0.82 with the default coarse schema at under half the
cost; see docs/PLAN.md.)

| chooser | recall | precision | F1 | time (11 docs) |
|---|---|---|---|---|
| Jev (hosted), mean of 3 runs | 0.83 | 0.87 | 0.843 ± 0.007 | minutes |
| **Clef-Flash Q4_K_M (local default, RTX 5060 8 GB)** | 0.63 | 0.66 | 0.645 | 103 min |

Clef gives the same answers every run; Jev doesn't, hence its three runs. On
the four shortest documents (71 values), the `llm` chooser (qwen3.5:9b) scored
F1 0.50 in 49 minutes, against Clef's 0.67 in 16 minutes and Jev's ~0.87.

**Time per job,** OCR included, locally: about a minute for a one-page photo,
and 5–25 minutes for a multi-page legal document. With Jev, tagging adds only
seconds to the OCR time.

OCR is measured separately in [eval/ocr](eval/ocr/README.md) (33 pages): word
F1 0.976 and number recall 0.93. Clean scans, faxes and photos are essentially
perfect; blurred, shaken photos are the weak spot (F1 0.90).

## Test

```sh
uv run pytest
```

## License

Apache-2.0. See [LICENSE](LICENSE).
