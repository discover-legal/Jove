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

The root may also carry `x-instructions`: standing context for every question,
such as whose point of view the data takes. A job may add its own `context`
(e.g. "Our firm: Harper & Leung LLP. Our client: Jane Doe."). Both go into Jev's
shared state, and every question is told to read them.

Schemas:
- `schemas/flyer.schema.json`: ads, event flyers, posters and posted notices
  (publisher, partners, headline, event and performers, offer, description,
  rules, legal references, calls to action, contact). 34 fields.
- `schemas/document_structure.schema.json`: a document's anatomy (letterhead,
  title, form code, caption with parties, dates, references, counsel,
  addressee, headings, body, form labels / values / checkboxes, signature
  block, footer), independent of legal meaning. On the IPC order it put the
  letterhead's address and phones under `letterhead` (the legal schema had
  given them to the client). On the N4 it separated printed labels from
  filled values.
- `schemas/legal_intake.schema.json` is the reference schema: Ontario law-firm
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
2. **ocr** (pluggable). This was chosen by measurement on `eval/ocr`; see its README.
   - **Born-digital PDF pages** use their own text layer: exact text, no GPU,
     about 0.1 s per page. Filled form fields (which live in annotations, not
     the page text) are drawn into the page image and added to the text layer,
     each boxed by its field. Lines are boxed from pdfium's character boxes,
     scaled to the rendered image. Pages with under 40 characters of text
     layer go to OCR.
   - **Engine:** RapidOCR 3 with the PP-OCRv6 small models, run locally on CPU.
     RapidOCR fetches the models once and checks them against pinned SHA-256s.
   - **Space recovery:** the recogniser sometimes drops spaces
     ("expires2027-04-03"). They are put back from per-character boxes, but only
     where a wide gap coincides with a letter/digit/punctuation change. A colon
     glued to a capital ("ID:Ontario") is also split.
   - **Recursive re-read (three-reader vote, `vote.py`):**
     - Every line the OCR is less than 0.995 sure of is cut out of the page,
       straightened, and read again by glm-ocr, which is trusted for words.
     - If glm's reading looks nothing like the OCR's, granite3.2-vision checks
       whether glm is hallucinating (the medoid wins).
     - Every number needs a second witness. qwen3.5 is asked only for lines where
       one is missing, and unbacked numbers revert to the OCR's visibly uncertain
       reading.
     - When detection finds almost nothing, glm's whole-page reading is used.
     - All three models run locally in Ollama. The older single-model re-read is
       still available with `LE_OCR_REREAD=vlm`.
3. **layout**: page → **block** → **line** → **word**.
   - Page skew is estimated from the OCR box corners, and layout runs on
     de-rotated boxes. Fragments that overlap horizontally are never the same line.
   - A wide gap splits a line only at a *gutter*, i.e. white space shared
     by other rows. Table rows keep each label with its value.
   - The page is first cut into regions (recursive XY-cut): horizontal white
     bands, then vertical gutters, but only between content whose lines don't
     share baselines. Panels on a sign are read one after another; a table's
     label and value columns stay together. A wide gap between text of very
     different sizes also splits a row (a logo beside body text).
   - A new block starts at a vertical gap of more than 0.8 line heights, or at
     line spacing above 1.5× the median. The spacing rule matters for photos,
     where OCR boxes are inflated.
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
- **Forced splits.** Some chunks hold several values even when one tag clearly
  dominates, so they are split regardless of separation and re-asked from where
  they were asked:
  - a multi-line block tagged with a single field (the MATTER block also names
    parties and amounts);
  - a line with two or more "Label:" pairs ("Full name: …  Date of birth: …").
- **Split gate (yes/no).** Separation and the structural rules only say a
  chunk *might* hold several values; running prose that merely mentions things
  looks the same. So before any split, the chooser is asked one yes/no question
  (a Jev `Noul`, one batched request per page per round) with the chunk's text
  and its best labels so far: "should this be split and labelled piece by
  piece?" Below 0.5 (`LE_SPLIT_GATE`), the chunk keeps its tags whole, and the
  `split_gate` value is recorded on its edge.
  - On the court endorsement this cut word fragments from 46 to 2, and the
    reasoning paragraphs stay whole (P(split) 0.09–0.30).
  - It kept every useful split: name + date of birth 0.93, phone + email
    0.93, amount + deadline 0.77, addresses 0.81–0.85.
- **Parts see their parent's text** as context: "22 King Street West" only
  reads as a party's address next to "Ms. Jane Doe".
- **Array items**: when a chunk is split inside an array item node, that chunk
  becomes the item's **instance anchor**, and all its parts inherit it. A
  forced split of a clear chunk anchors its whole path. Words anchor to their
  line, the smallest unit that can hold a list item, so "Smith & Co. LLP" split
  into words is still one party. So a
  block tagged `parties[]` whose lines split into name and role is one party.
- **Words**: runs of consecutive words with the same tag and anchor are merged
  into **spans**. "Jane" and "Doe" become the span "Jane Doe".

### Flat boundary tagger (`LE_TAGGER=flat`)

This is the alternative to the tree. OCR lines are the atoms; layout supplies
only reading order. Grouping is Jev's decision, not a set of geometry
thresholds:
- **Round 1, one parallel batch per page:** every line's schema Choice fan-out
  (with its neighbouring lines), "does it continue the previous line's value?",
  and "is it about the same item as the line one back / two back?".
- **Round 2:** the labelled split gate for every multi-word line.
- **Round 3:** words of the lines that said yes.
- **Assembly:** consecutive units that continue each other and share a tag become
  one value. List items follow the same-item answers, and a field repeating
  within an item starts a new one.

### Measured (eval/tagging)

**Round 1 (2026-09-29):** 5 documents, 87 gold values, identical cached OCR, Jev.

| tagger | recall | precision | F1 | list items | cost |
|---|---|---|---|---|---|
| tree, gate only on ambiguity | 0.816 | 0.961 | 0.883 | 3/6 | $0.070 |
| tree, gate on every splittable chunk | 0.943 | 0.857 | 0.898 | 3/6 | $0.117 |
| flat | 0.908 | 0.884 | 0.896 | 3/6 | $0.107 |

**Round 2 (2026-09-30):** 11 documents, 222 gold values. Six were added from
public sources:
- two Court of Appeal for Ontario decisions (2022 ONCA 250, and 2022 ONCA 100
  p.1), plus a fax-degraded copy of the first;
- IPC Order PO-2917;
- the official LTB Form N4, filled with invented values, as a digital PDF and
  as a phone-photo copy.

| tagger | recall | precision | F1 | tokens | cost |
|---|---|---|---|---|---|
| tree | 0.847 | 0.760 | 0.801 | ~13.2M | ~$0.55 |
| **flat (default)** | **0.865** | **0.807** | **0.835** | ~10.3M | ~$0.43 |

Flat wins most on degraded scans: on the faxed decision it found 21/27 values
against tree's 17/27. It is also about 20% cheaper. It is the default;
`LE_TAGGER=tree` restores the tree.

Real errors both taggers still make:
- a tribunal's letterhead address and phones are tagged as the *client's*
  contact details;
- blank form labels ("Street Number: Street Name:") get tagged as values;
- a party's role printed on a separate line doesn't attach to the party;
- performers named inside prose are missed.

### Choosers (pluggable)

The tagger needs one operation: given a question, return a probability for
each option. Chooser output is never trusted: unknown options and bad numbers
are dropped, the rest are renormalised, and unanswered questions produce warnings.

| Chooser | What it is |
|---|---|
| `llm` (default, local) | Any OpenAI-compatible endpoint; the default is Ollama `qwen3.5:9b-16k` with thinking off. The model *states* the probabilities, and on the flyer it was overconfident enough that nothing ever split. |
| `jev` | TypeSafe Jev via `typesafe-sdk`. It returns real option probabilities. Requests are packed to Jev's limits (32k tokens for state + longest question, 64k for everything). The budget is in bytes: we measured ~2.5 bytes/token and budget at 2.0; a batch Jev still rejects is halved and retried. A reference-schema block is about 13k tokens (~$0.0005). A whole page tags in ~2 s. **Page text leaves the machine.** |
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
| `LE_OCR_REREAD` | `vote` (or `vlm`; anything else turns re-reading off) |
| `LE_OCR_READER` / `LE_OCR_TIEBREAKER` / `LE_OCR_ESCALATION` | `glm-ocr` / `granite3.2-vision` / `qwen3.5:9b-16k` |
| `LE_CHOOSER` | `llm` |
| `LE_LLM_BASE_URL` / `LE_LLM_MODEL` / `LE_LLM_API_KEY` | `http://localhost:11434/v1` / `qwen3.5:9b-16k` / — |
| `LE_LLM_REASONING` | `none` (turns off model thinking; empty omits the parameter) |
| `LE_JEV_MODEL` | `jev-latest` |
| `TYPESAFE_API_KEY` | *(required for `jev`)* |
| `LE_SEPARATION` | `1.5` |
| `LE_SPLIT_GATE` | `0.5` (split only if the yes/no gate's P(split) ≥ this) |
| `LE_TAGGER` | `flat` (or `tree`) |
| `LE_DATA_DIR` | `./data` |

## Status and next steps

- **Tagging with Jev (2026-09-29):** it works as designed on the synthetic
  intake form and demand letter, a real Divisional Court endorsement, a gig
  flyer and a transit-stop notice (the last two with the flyer schema). Tagging
  takes about 2 s per page, and the time is dominated by OCR. Known weak spots:
  - roles printed apart from names ("Applicant") become their own list item;
  - logo clusters on signs are jumbled by layout.
- **Done:** the whole pipeline (39 tests). The OCR was benchmarked and tuned on
  `eval/ocr` (31 pages): word F1 went from 0.83 to 0.975 and number recall from
  0.74 to 0.92. Scans, faxes and photos are essentially perfect. The real
  low-light flyer photo went from 0.31 to 0.94. The local LLM chooser was run end to end on
  that flyer. Jev request packing and response parsing are checked against the
  SDK's own types.
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
