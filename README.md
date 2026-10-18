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
like) into views for people and graphs for tools. The values are grouped as
records: each list item (Party 1, Address 1, Amount 1) and each section outside
lists (Matter, Contact). Each value carries its owner (our client, the other
party, the issuer) and the page and line it was read from. Values scoring below
0.7 are marked unsure. Captions ("Full name:"), brackets and trailing commas
are dropped for display; the tag still covers the text as read. `--all` keeps
captions, rank-2/3 tags and one-line blocks.

```sh
uv run python -m jove.graph data/jobs/*/result.jsonl --to summary    # records, as text
uv run python -m jove.graph data/jobs/*/result.jsonl --to dot | dot -Tsvg > cards.svg
uv run python -m jove.graph data/jobs/*/result.jsonl --to obsidian --out vault/
uv run python -m jove.graph data/jobs/*/result.jsonl --to tree       # the page tree
uv run python -m jove.graph data/jobs/*/result.jsonl --to cypher --out graph.cypher
uv run python -m jove.graph data/jobs/*/result.jsonl --to typeql --out typedb/
```

```
intake_form.photo.jpg
  Party 1  (Our client)
    Name                 Jane Alexandra Doe          p1 l4
    Date of birth        1980-04-03                  p1 l4
  Party 2  (Other party)
    Name                 Acme Property Management Inc.  p1 l10
    Role                 landlord                    p1 l10
```

- **Graphviz** (`--to dot`) draws one card per record (field, value, page and
  line), coloured by owner, beside the page's lines, with a line from each value
  to where it was read. `--to dot-full` draws the full graph instead: page tree,
  schema fields, and each tag as a diamond joining text, field, item and owner.
- **Obsidian:** open `vault/` as a vault.
  - `Documents/` holds each document's home note, with every record as a
    table.
  - `Records/` holds a note per record: its values as properties and a table
    linking each value to its source line.
  - `Owners/` links each owner to their records.
  - `People/` holds a note per person or organisation (see below).
  - The page lines (`Source/`) and the schema (`Schema/`) are hidden from the
    graph view, which shows records, owners and documents.
- **FalkorDB** (or Neo4j or Memgraph via the `.cypher` file) holds a property
  graph. Every node has a readable `name`, which the browsers show. Each list
  item also carries its values as properties (`date_of_birth`, `role`, `owner`;
  a field called `name` becomes `value_name`).
  ```sh
  uv sync --extra graph
  docker run -d -p 6379:6379 -p 3000:3000 falkordb/falkordb
  uv run python -m jove.graph data/jobs/*/result.jsonl --load falkor://localhost:6379/jove
  ```
  Browse it at http://localhost:3000:
  `MATCH (p:Item {list: 'parties[]'}) RETURN p.value_name, p.role, p.date_of_birth, p.owner`.
- **TypeDB** holds a hypergraph: each tag is one n-ary `tag` relation that links
  the text, its field, its list items and its owner, named for reading
  ("Party › Name = Jane Alexandra Doe").
  ```sh
  docker run -d -p 1729:1729 -p 8000:8000 typedb/typedb:3.13.6
  uv run python -m jove.graph data/jobs/*/result.jsonl --load typedb://localhost:1729/jove
  ```
  View it in [TypeDB Studio](https://studio.typedb.com) (connect to
  `http://localhost:8000`, user `admin`, password `password`), e.g.
  `match $t isa tag, has name $n; $n like "^Party";`

### People and organisations across documents

Every name the tagger found is a mention: a party's name, their lawyer, the
names on each side of a case, a document's author or recipient. `jove.entities`
groups mentions that name the same person or organisation, across all the
documents given, and says why each joined:

- the same name, ignoring case, titles ("Ms.") and the form of a company suffix
  ("Inc." / "Incorporated", "Professional Corporation");
- a letter or two apart in a long name (OCR);
- a shorter form of one person's name ("D. Whitcombe", "Jane Doe" for "Jane
  Alexandra Doe"), only when exactly one person fits; otherwise the entity is
  flagged as ambiguous;
- a lone word ("Whitcombe") only within its own document, and only when one
  fuller name fits.

Values naming several are split first ("Marcus Oyelaran and Priya Oyelaran"),
but not organisations with "and" or "&" in their name ("Smith & Co. LLP"). In a
counsel line ("Rahul Shastri, for the respondents Rutmet Inc.") the names after
"for the …" are the represented party. Each entity is flagged for a conflict
check when it is our client in one place and another party in another, and when
its dates of birth or registration numbers differ. It is deterministic: no
model is asked.

```
People and organisations
  Jane Alexandra Doe  (person, 2 mentions in 2 documents)
    demand_letter.photo.jpg  Party 1 › Name, Our client: “Jane Doe”  p1 l6  [a shorter form of the name]
    intake_form.photo.jpg    Party 1 › Name, Our client: “Jane Alexandra Doe”  p1 l4
  Priya Oyelaran  (person, 2 mentions in 2 documents)
    ⚠ Conflict check: our client in n4_photo.pdf; another party in n4_filled.pdf
```

The summary ends with this list. In the vault, `People/` has a note per entity
(its mentions, where each was read and why it is the same, flags as warnings),
and every name in a record's table links to it, so the graph view joins
documents through the people they share. Cypher adds `Entity` nodes with
`NAMES` edges from the text; TypeDB adds `actor` entities and `mention`
relations.

An existing vault, graph or database is only overwritten with `--replace`.

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
