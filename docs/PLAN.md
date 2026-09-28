# LegalEyes — plan

## What it is

A web service with one job: given **scanned pages** and a **data schema**, tag
every piece of text with the schema field it gives a value for, and emit the
result as a JSONL graph for later machines. It is fully automatic, with no human
review. It **tags and does not choose**: if three chunks all look like
`client.person.full_name`, all three are tagged with their scores, and picking
one is the consumer's job.

The previous iOS / Meta-glasses app is tagged `ios-final` in git.

## Inputs

- **Pages**: PDFs and/or images (PNG, JPEG, TIFF). Each PDF page is rasterised
  at 200 dpi, and each image frame is one page.
- **Schema**: a JSON Schema subset.
  - The root is `type: object`. Objects nest via `properties`; `type: array`
    takes `items` that are an object or a scalar; scalars are `string`,
    `number`, `integer` or `boolean`. Arrays of arrays are not supported.
  - Model hints: `description`, `examples`, and `x-not-for` (ours: what belongs
    in a sibling field instead). On an array, the array's hints describe its items.
  - Paths are dotted, with `[]` for array items: `parties[].contact.address.city`.

`schemas/legal_intake.schema.json` is the reference schema: Ontario law-firm
intake with 15 top-level branches, 121 leaf fields and 26 internal nodes, at
most 15 children per node and 4 levels deep. Its examples use Ontario courts,
tribunals, IDs and file-number formats. SIN and OHIP numbers are deliberately
absent.

## Pipeline

```
ingest ──► ocr ──► layout ──► tag ──► emit (JSONL graph)
```

1. **ingest**: hash every input file, rasterise each page to PNG, and hash every
   page. Page ids are `p0001`… in upload order.
2. **ocr** (pluggable): RapidOCR, run locally on CPU. It uses PaddleOCR's English
   recognition model, because the bundled Chinese+English model drops spaces
   between English words. That model downloads once from Hugging Face, is checked
   against a pinned SHA-256, and is cached in `~/.cache/legaleyes`.
3. **layout**: page → **block** → **line** → **word**.
   - OCR fragments on the same baseline are merged back into one line, but
     columns far apart stay separate lines.
   - A vertical gap of more than 0.8 line heights starts a new block.
   - Word boxes are estimated from character offsets.
4. **tag**: see below.
5. **emit**: see the output section below.

## Tagging: the schema tree meets the document tree

This follows TypeSafe's
[hierarchical classification cookbook](https://docs.typesafe.ai/cookbooks/hierarchical_classification).
Every internal schema node becomes a **Choice** over its direct children plus
`none`. Each option carries its description, examples, `not_for` and child
names. A path's score is the geometric mean of its edge probabilities.
**Separation** is the best path's score divided by the runner-up's.

- **Fan-out.** Every block is asked every internal node's Choice in one go:
  26 questions for the reference schema. Because every edge gets a probability,
  every path can be scored exactly, and no beam pruning is needed.
- **Clear answer** (separation ≥ 1.5, set by `LE_SEPARATION`): the chunk is
  tagged with its top 3 paths. `none` is a real answer (`#none` for the root,
  `client#none` inside `client`).
- **Ambiguous answer**: the chunk probably holds several things. It is split
  into its parts (block → lines → words; single-child levels are skipped).
  The parts are re-asked **from the schema node where the two best paths
  diverged**, not from the root.
  - Example: "Name: Jane Doe  DOB: 1980" is clearly `client`, but torn between
    name and DOB. Its words are re-asked from `client`, and each word sees its
    line as context.
- **Array items**: when a chunk is split inside an array item node, that chunk
  becomes the item's **instance anchor**, and all its parts inherit it. So a
  block tagged `parties[]` whose lines split into name and role is one party.
- **Words**: runs of consecutive words with the same tag and anchor are merged
  into **spans**. "Jane" and "Doe" become the span "Jane Doe".

### Choosers (pluggable)

The tagger needs one operation: given a question, return a probability for
each option. Chooser output is never trusted: unknown options and bad numbers
are dropped, the rest are renormalised, and unanswered questions produce warnings.

| Chooser | What it is |
|---|---|
| `llm` (default, local) | Any OpenAI-compatible endpoint; the default is Ollama `qwen2.5:7b`. The model *states* the probabilities, so its separations are rougher. |
| `jev` | TypeSafe Jev via `typesafe-sdk`. It returns real option probabilities. Requests are packed to Jev's limits (32k tokens for state + longest question, 64k for everything, budgeted in bytes). A reference-schema block costs about 33 KB, so roughly 5 blocks fit per request, at about $0.0004 per block. **Page text leaves the machine.** |
| `fake` | Rule-driven, for tests. |

## Output: JSONL graph

One JSON object per line. The order is `job`, `page`, `node`, `edge`, `warning`.

```jsonc
{"type":"job","job_id":"…","created_at":"…","schema_sha256":"…",
 "inputs":[{"filename":"a.pdf","sha256":"…","pages":["p0001"]}],
 "ocr":{"provider":"rapidocr","rec_model":"en_PP-OCRv3_rec_infer.onnx"},
 "chooser":{"provider":"jev","model":"jev-latest"},"separation_threshold":1.5}
{"type":"page","page_id":"p0001","source":"a.pdf","source_page":1,"sha256":"…",
 "width":1700,"height":2200,"tags":["client.person.full_name"],"placed":true}
{"type":"node","id":"p0001/b02/l01/s02","kind":"span","page_id":"p0001",
 "bbox":[82,147,241,175],"text":"Jane Doe"}          // kind: block | line | span
{"type":"edge","rel":"contains","from":"p0001/b02/l01","to":"p0001/b02/l01/s02"}
{"type":"edge","rel":"within","from":"p0001/b02","to":"client.person",
 "score":0.94,"separation":1.04,"asked_from":""}     // split: parts were re-asked from here
{"type":"edge","rel":"tagged","from":"p0001/b02/l01/s02","to":"client.person.full_name",
 "rank":1,"score":0.91,"separation":6.2,"asked_from":"client.person","instances":{}}
{"type":"edge","rel":"tagged","from":"p0001/b01","to":"#none","rank":1, …}
{"type":"warning","code":"unanswered","detail":"p0001/b03 at client"}
```

- The node ids encode the containment path (`page/block/line/span`).
- A node that was **split** gets a `within` edge. A node that was **not split** gets
  `tagged` edges, ranks 1 to 3; spans get rank 1 only.
- `instances` maps array item paths (e.g. `parties[]`) to the anchoring node
  id. Tags that share an anchor belong to the same item.
- The hashes let a downstream system prove which exact bytes a tag came from.

## Web surface

- `GET /`: a bare upload form (files + schema)
- `POST /jobs`: multipart `files` + `schema` (JSON text); the schema is checked on
  upload. Returns `{job_id}`.
- `GET /jobs/{id}`: `{status: queued|running|done|failed, error}`
- `GET /jobs/{id}/result.jsonl`: the output

Jobs run in the background, in-process, and store their state under `data/jobs/<id>/`.

## Configuration (env)

| var | default |
|---|---|
| `LE_OCR` | `rapidocr` |
| `LE_OCR_REC_MODEL` | *(verified English model, auto-downloaded)* |
| `LE_CHOOSER` | `llm` |
| `LE_LLM_BASE_URL` / `LE_LLM_MODEL` / `LE_LLM_API_KEY` | `http://localhost:11434/v1` / `qwen2.5:7b` / — |
| `LE_JEV_MODEL` | `jev-latest` |
| `TYPESAFE_API_KEY` | *(required for `jev`)* |
| `LE_SEPARATION` | `1.5` |
| `LE_DATA_DIR` | `./data` |

## Status and next steps

- **Done:** the whole pipeline, tested with fake choosers (28 tests); real OCR
  and layout checked on a rendered form; Jev request packing and response
  parsing checked against the SDK's own types.
- **Next: run it for real.** Put real Ontario scans through Jev (needs a key)
  and through Ollama. Build a small labelled evaluation set and a scorer
  (per-field precision and recall on spans, and whether list items group
  correctly). Tune `LE_SEPARATION` and the block gap on that set.
- **Cost:** block fan-out asks all 26 nodes every time. Asking level by level
  would cut tokens but add round trips. Only worth it if cost matters.
- **Layout:** tables and multi-column pages; word boxes from OCR character
  boxes rather than estimates.
- **Documents:** splitting page runs into documents and classifying them
  (DocJev-style) would add a document level above blocks.
- **Later:** a job queue, auth, and a review UI (explicitly out of scope for now).
