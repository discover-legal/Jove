# Jove — plan

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
- `schemas/court_document.schema.json`: an Ontario court filing (court, file
  numbers, parties, title and form, deponent, body paragraphs, schedules, jurat
  with remote commissioning, lawyer's certificate, signatures, back-sheet
  counsel), plus the copy's metadata: e-signing data (platform, envelope ID,
  signer stamps, timestamps, certificate of completion), production page stamps
  and law-firm document numbers. Deponent and jurat are `x-mentions`. 46 fields.
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
     - All three models run locally in Ollama. It replaced a single-model
       re-read (word F1 0.910 against the vote's 0.975 on eval/ocr round 2).
   - **Missed lines (`recover.py`):** glm-ocr reads the whole page. Rows with a
     word no detected line has (and at most 12 words) are located by
     qwen3-vl:4b-instruct. The stretch of the row no detected line covers is
     cropped and read again, and the line is added only if that reading matches,
     numbers included. `JOVE_OCR_RECOVER=off` disables it.
   - Straightening whole photographed pages before OCR was tried and removed:
     it cost word F1 on eval/ocr (round 4), and the vote already straightens
     every line it re-reads.
   - Images are turned upright from their EXIF orientation at ingest.
   - **GPU hand-off:** once every page is read, the OCR models are unloaded from
     Ollama (`keep_alive: 0`), so the chooser gets the whole GPU.
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
   - Docling's layout regions were tried as an alternative and removed
     (2026-10-17): on Clef, 10 documents, F1 0.598 against 0.620 with these
     rules, 36% slower; it helped only the intake form (13 vs 9/19).
4. **tag**: see below.
5. **emit**: see the output section below.

## Tagging: the schema tree meets the document tree

This follows TypeSafe's
[hierarchical classification cookbook](https://docs.typesafe.ai/cookbooks/hierarchical_classification).
Every internal schema node becomes a **Choice** over its direct children plus
`none`. Each option carries its description, examples, `not_for` and child
names. A path's score is the geometric mean of its edge probabilities.
**Separation** is the best path's score divided by the runner-up's.

- **Fan-out.** Every unit is asked every internal node's Choice in one go:
  26 questions for the reference schema. Because every edge gets a probability,
  every path can be scored exactly, and no beam pruning is needed. The top 3
  paths are kept; `none` is a real answer (`#none` for the root,
  `client#none` inside `client`).
- **Labels are their own answer.** Every Choice offers `label` next to `none`
  ("the text names a field rather than giving its value"), so a form caption
  becomes `<node>#label` instead of being forced into a field. Text typed into
  a filled PDF form field is marked in its question as certainly a value.
- **Ownership pass (`owners.py`, `JOVE_OWNERS=on`).** Schemas may declare
  `x-owners`: per owner (client, other party, document issuer…) its name
  fields and contact prefix. Every value tagged with an owner's name or
  contact field gets one more Choice: *whose is it?* If the answer names
  another owner, the tag moves to that owner's equivalent field
  (`client.contact.address.city` → `document_issuer.contact.address.city`).
  The answer is recorded on the edge as `owner`.
- **History.** A tree tagger came first: it asked about layout blocks and split
  ambiguous ones into lines and words, re-asked from the schema node where the
  two best paths diverged. The flat tagger beat it on every round (Measured,
  rounds 1–2), and the tree tagger was removed on 2026-10-16.

### The flat tagger (`flat.py`)

OCR lines are the atoms; layout supplies
only reading order. Grouping is Jev's decision, not a set of geometry
thresholds, except for running prose:
- **Paragraphs (`JOVE_PARAGRAPHS`, on):** a block's lines that read as prose
  are one atom, asked about once and never split. A paragraph ends after a
  short line that finishes a sentence (the next line's first word would have
  fitted on it), or before a numbered or bulleted line. A short line that stops
  mid-sentence runs on (a pre-printed jurat with the date filled in). Only runs
  of at least 2 lines and 12 words, at least 30% lowercase words and no row of
  several `Label:` captions count as prose. Values named inside a paragraph are
  found by the mentions pass, word by word: Jev labels each word and says
  whether it continues the previous word's value, and a value runs until a word
  doesn't continue it or gives a field of its own. No patterns decide where a
  value ends. On the affidavit of documents this made 111 lines into 53 lines
  and 19 paragraphs.
  Measured with Jev on 10 documents (2026-10-17; one run each, noise ±0.007):

  | setup | recall | precision | F1 |
  |---|---|---|---|
  | earlier line-by-line runs (3) | 0.82–0.85 | 0.86 | 0.840–0.855 |
  | line by line, today | 0.816 | 0.838 | 0.827 |
  | paragraphs, mentions by clause + money/date/phone/web patterns | 0.788–0.806 | 0.835–0.850 | 0.818–0.820 |
  | **paragraphs, mentions word by word (current)** | 0.788 | 0.807–0.822 | 0.798–0.805 |

  The patterns were removed for a smaller design at a cost of about 0.02 F1.
  Its main losses: a sign whose panels OCR reads as one paragraph (the phone
  number and web address inside are never asked about on their own), and
  amounts and dates inside a letter's sentences.
- **Halving (`JOVE_TAGGER=halve`, `halve.py`, experimental).** Start from the
  whole document and ask, given the schema (in the shared state), "does this
  hold more than one value, so it should be cut in two?"; halve where yes
  (at the line boundary nearest the middle, or the word gap nearest it inside
  one line) and ask both halves again; classify each chunk where the answer is
  no. Neighbouring chunks on one line with the same field are rejoined. First
  run, Jev, 10 documents (2026-10-17): F1 0.655 (recall 155/217, precision
  0.605), 296 requests, $0.36. Better than line by line on the court decisions
  (recall 20/27 vs 17–18 on ONCA 250), much worse on forms (N4 17/27 vs 24/27):
  a cut at the middle falls inside values ("Marcus Oyelaran and Priya
  Oyelaran" became "Marcus Oyelaran" / "and Priya" / "Oyelaran").
  **Jev picks the cut:** when it says halve, a Choice over the 5 gaps nearest
  the middle (line boundaries first), each shown with the words either side:
  F1 0.732 (recall 172/217, precision 0.680), 371 requests, $0.34. Recall is
  close to line by line (177), and the court decisions are the best of any
  setup (ONCA 250 and its fax copy 23/27). Precision is the gap: mostly the
  wrong field for a well-cut chunk (an issuer's address as the client's,
  judges as parties); list items no longer group (0/13).
  Classifying each chunk with the chunk it was cut from (±60 words) instead
  of its neighbours made it worse: F1 0.677, wrong-branch tags 17 → 24, forms
  down (N4 18 → 15/27) while court decisions gained one value each. Reverted.
  **Owners pass fixed for halving:** it showed Jev the page's lines, and the
  halving tagger emits none, so "whose is it?" was asked blind. With the page's
  chunks instead: F1 0.732 → 0.784, recall 182/217 (the highest of any setup).

**Coarse schema (2026-10-17).** `legal_intake_coarse` gives each kind of value
one home: every person or organization is a party (role says client, judge,
tribunal...), every contact detail sits under `contact` (the owners pass says
whose), all money is `amounts[]`, dated things are `events[]` or
`deadlines[]`. 62 fields under 14 sections, against 134 under 30. The gold is
mapped by `eval/tagging/make_coarse_gold.py` into `gold_coarse/` (run.py and
score.py take `--gold gold_coarse`). Jev, 10 documents, one run each:

| tagger | fine schema | coarse schema |
|---|---|---|
| flat | F1 0.798 (P 0.807), $0.47 | F1 0.789 (P 0.868), $0.22 |
| halving | F1 0.784 (R 182/217), $0.34 | F1 0.785 (R 183/217), $0.17 |

Accuracy is the same within noise; the coarse schema halves the cost. The
coarse scores don't check whose a contact detail is (that is on the edge as
`owner`, which the scorer ignores), so they are slightly more lenient there.
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
against tree's 17/27. It is also about 20% cheaper. It became the default
(and later the only tagger).

**Round 3 (2026-09-30):** label option, a `document_issuer` branch in the
legal schema, and the ownership pass. Flat tagger, 11 documents, 230 gold
values.

| flat tagger | recall | precision | F1 | requests | tokens |
|---|---|---|---|---|---|
| round-2 setup (rescored on today's gold) | 0.835 | 0.807 | 0.820 | 273 | 10.3M |
| + `label` option + issuer branch | 0.826 | 0.866 | 0.846 | 355 | 13.7M |
| + ownership pass (default) | 0.826 | 0.871 | 0.848 | 364 | 13.7M |

What each part did:
- **The issuer slot and the label option did nearly all of it.** With a place
  for letterheads, Jev puts the IPC's address and phones there itself.
- **Labels:** 45 form captions on the N4 and 16 on the intake form now come
  out as labels.
- **The ownership pass** asked 102 questions, kept 99 tags, and moved 3
  defensibly (the tenant's surname to `client` per the job context; a law
  firm letterhead from issuer to opposing party). It is a cheap safety net
  (+9 requests).
- **Cost:** the extra `label` option in every Choice and the extra root branch
  cost ~33% more tokens.

**Noise (2026-10-01): Jev is not deterministic.** The same document and the
same questions give different answers run to run (2022 ONCA 100: recall
15, 14, 15 of 18 over three runs). Three full runs of the default setup:

| | F1 | |
|---|---|---|
| run 1 | 0.838 | |
| run 2 | 0.839 | |
| run 3 | 0.852 | mean 0.843 ± ~0.007 |

So differences under ~0.015 F1 are noise:
- flat vs tree (+0.034) and the label/issuer gain (+0.026) are above it;
- the ownership pass (+0.002) is not.

Compare configurations over ≥3 runs. For production, consider caching Jev
answers by question hash: the same document then tags the same way twice,
and re-runs are free.

**Local decision model (2026-10-15): Clef-Flash.** Flat tagger, same 11
documents and cached OCR, Clef-Flash Q4_K_M on llama-server (RTX 5060 8 GB).
One run.

| chooser | recall | precision | F1 | groups | requests | tokens | time |
|---|---|---|---|---|---|---|---|
| Jev (3 runs) | 0.828 | 0.859 | 0.843 | 4/14 | ~364 | 13.7M | minutes |
| Clef-Flash Q4_K_M | 0.630 | 0.661 | 0.645 | 2/14 | 856 | 9.2M | 103 min |

- It does real work, unlike Laya (0/45; tried 2026-10-01 and removed): it is closest to Jev on the flyer
  (11/13), the transit sign (16/21) and the Divisional Court endorsement
  (12/16). It is weakest on the demand letter (8/18), the intake form (10/19)
  and ONCA 100 (10/18), where it leaves values as `none`.
- The fax copy scored the same as the clean ONCA 250 (15/27 each).
- Speed is the cost: ~1,700 tokens/s. The 16k micro-batch splits most
  requests (856 against Jev's ~364), and each repeats the page state.
- Untested: Q8_0 (9.2 GB, would need CPU offload here), or the larger `Clef`.
- **Against the old default (`llm`, qwen3.5:9b-16k in Ollama),** on the four
  short documents (flyer, transit sign, demand letter, intake form; 71 values):

  | chooser | recall | precision | F1 | time |
  |---|---|---|---|---|
  | Jev (3 runs) | 0.85 | 0.90 | ~0.87 | minutes |
  | Clef-Flash | 0.63 | 0.71 | 0.67 | 16 min |
  | qwen3.5:9b (`llm`) | 0.51 | 0.49 | 0.50 | 49 min |

  The LLM writes a probability for every option as text, which is slow: its
  full 11-document run had not finished the first document (ONCA 100) after
  2.5 hours, against Clef's 8 minutes. **Clef is now the default.**
- **Clef is deterministic:** re-running unchanged documents gave identical
  scores, so single runs are enough to compare Clef configurations (unlike Jev).
- **With today's OCR (fresh cache: missed-line recovery on).** The six OCR'd
  documents were read again. Only two lines changed: the transit sign gained
  "SAYit" (recall 16 → 17/21, precision 22 → 23/27), and the flyer gained a
  garbled handle "@glasatlger_a". Everything else was identical. These
  documents have few lines the detector misses; the pesticide-sign photo did.
- **Prompt cache: no gain.** With `--cache-ram`, requests sharing 95% of their
  prefix (the same page state) took the same 7.3 s, and so did an exact repeat:
  llama.cpp's decision-model path recomputes the whole request.
- **Sharing the GPU with OCR:** with `--sleep-idle-seconds 30` the server frees
  its VRAM while idle (6.8 GB → 0.1 GB) and wakes in about 5 s. With the OCR
  models unloaded after OCR, a one-page job ran OCR (glm-ocr + qwen3-vl, 7.3 GB)
  and then Clef, each with the whole 8 GB GPU, in 61 s.

**Grouping fixes (2026-10-01):**
- Two parties on one line ("DAVID GOODIS, Senior Adjudicator, and JOHN DOE,
  Requester") are now two items: the second item anchors at its word when
  the line anchor is taken.
- Adjacent words in a line with the same tag are one value, whatever the
  "continues" answer says, so "Senior Adjudicator" no longer splits into two
  roles.
- The court endorsement now scores 16/16, 16/16, with 3/3 groups.

**Round 4 (2026-10-01): mentions inside prose, and caption sides.**
- `x-mentions` (on `event.performers[]`, `parties[]`, `amounts[]`): every
  unsplit prose line of 3+ words gets a yes/no per marked field ("does it
  mention a performer?"), batched with the split gate. Lines that say yes have
  their words labelled within that field only. The line keeps its own tag, and
  the mentions become extra spans flagged `mention: true`.
- `proceedings[].sides[] { role, names[] }` in the legal schema. A field that is
  itself a list inside an item (names[]) may repeat without starting a new
  item.

| flat tagger, new gold (14 groups) | F1 | groups |
|---|---|---|
| baseline, 3 runs | 0.838 / 0.839 / 0.852 (mean 0.843) | 4/14 |
| + mentions + sides, 2 runs (credits ran out) | 0.854 / 0.846 (mean 0.850) | 5/14 |

The overall difference is within noise. Per feature:
- **Mentions** fixed the flyer: recall 10/13 → 13/13, with both performers
  paired with their instruments (a name + "(percussion)", another
  name + "(bass)").
- **Sides** mostly didn't take: Jev keeps tagging caption names as
  `parties[].name`, and used `sides` once. The likely fix, untested, is an
  `x-not-for` on `parties[].name` sending caption party blocks to `sides`.

Real errors still made (before round 3):
- a tribunal's letterhead address and phones are tagged as the *client's*
  contact details (fixed in round 3: `document_issuer` + ownership);
- blank form labels ("Street Number: Street Name:") get tagged as values
  (fixed in round 3: `label`);
- a party's role printed on a separate line doesn't attach to the party;
- performers named inside prose are missed.

#### OCR engines: how much of the schema is still fillable (2026-10-01)

`eval/tagging/chunks.py --ocr ENGINE` needs no chooser. "read" counts gold
values whose text comes back exactly (normalised case and punctuation)
somewhere on the page. "whole" counts values that also sit inside a single unit,
which is the most a tagger could fill. Every engine except `app` reads page
images even when the PDF has a text layer. **Bias:** the gold was transcribed
from the app's own OCR, so `app` scores 1.000 by construction.

| OCR | read | whole | flyer | ONCA 250 fax | ONCA 100 | transit sign |
|---|---|---|---|---|---|---|
| app (text layer, else PP-OCRv6 + glm/granite/qwen vote) | 1.000 | 0.991 | 10/10 | 25/25 | 17/17 | 18/18 |
| PP-OCRv6 small, plain, image only | 0.967 | 0.958 | 5/10 | 25/25 | 16/17 | 17/18 |
| PP-OCRv4 mobile (older), image only | 0.921 | 0.916 | 4/10 | 20/25 | 14/17 | 16/18 |
| Tesseract 5 | *pending: not installed* | | | | | |

Clean scans and forms come through intact with every engine. The losses are on
the photo (flyer), the fax and small print, and they are usually digits and
names, which are the values that matter. Clean pages could not tell these
engines apart. "alone", which measures layout rather than OCR, barely moves
(0.67–0.69).

### Choosers (pluggable)

The tagger needs one operation: given a question, return a probability for
each option. Chooser output is never trusted: unknown options and bad numbers
are dropped, the rest are renormalised, and unanswered questions produce warnings.

| Chooser | What it is |
|---|---|
| `llm` (local) | Any OpenAI-compatible endpoint; the default is Ollama `qwen3.5:9b-16k` with thinking off. The model *states* the probabilities, and on the flyer it was overconfident enough that nothing ever split. |
| `jev` | TypeSafe Jev via `typesafe-sdk`. It returns real option probabilities. Requests are packed to Jev's limits (32k tokens for state + longest question, 64k for everything). The budget is in bytes: we measured ~2.5 bytes/token and budget at 2.0; a batch Jev still rejects is halved and retried. A reference-schema block is about 13k tokens (~$0.0005). A whole page tags in ~2 s. **Page text leaves the machine.** |
| `clef` (default, local) | Cloudflare Clef-Flash (Qwen3.5-9B + a joint schema head, Apache-2.0) served by llama-server (b11655+, `ggml-org/Clef-Flash-GGUF` Q4_K_M, 6.2 GB). It speaks Jev's `/v1/systemone`, so `JevChooser` and the SDK are reused with `JOVE_CLEF_BASE_URL`. The head reads the whole request in one micro-batch: on 8 GB, `-ub 16384` is the most that fits (a 32k micro-batch wants an 8.7 GB compute buffer), and llama-server's "too large to process" halves the batch like Jev's `max_tokens_exceeded`. About 1,700 tokens/s on the RTX 5060, so ~8 min per dense page. Local; nothing leaves the machine. F1 0.645 against Jev's 0.843 (see Measured). |
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
 "bbox":[82,147,241,175],"text":"Jane Doe"}          // kind: block | paragraph | line | segment | span
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

A light, dependency-free UI (`view.py`: inline CSS and a little vanilla JS; no
fonts, scripts or styles fetched, so it works offline and exports cleanly).

- `GET /`: the upload form: drop files, pick one of the schemas in `schemas/`
  (`JOVE_SCHEMAS_DIR`) or paste your own, optional context; and recent jobs.
- `POST /jobs`: multipart `files` + `schema` (JSON text) or `schema_name` (a file
  in the schemas folder, without `.schema.json`); the schema is checked on
  upload. Returns `{job_id}`, or redirects a browser (the form) to the view.
- `GET /jobs/{id}`: `{status: queued|running|done|failed, error, updated}`
- `GET /jobs/{id}/result.jsonl`: the output
- `GET /jobs/{id}/view`: the result for a person to check: page images with the
  rank-1 value tags boxed (a colour per top-level field, dashed for values found
  inside prose), and the values listed by field group with scores. Field-group
  filters and a minimum-score slider; hovering or clicking links a box to its
  row. It refreshes until the job is done.
- `GET /jobs/{id}/view.html`: the same page as one self-contained file (pages
  inlined as JPEG), to save, share or embed elsewhere.
- `GET /jobs/{id}/pages/{page_id}.png`: a page image, as rasterised at ingest

Jobs run in the background, in-process, and store their state under `data/jobs/<id>/`.

## Configuration (env)

| var | default |
|---|---|
| `JOVE_OCR` | `rapidocr` |
| `JOVE_OCR_REREAD` | `vote` (anything else turns re-reading off) |
| `JOVE_OCR_READER` / `JOVE_OCR_TIEBREAKER` / `JOVE_OCR_ESCALATION` | `glm-ocr` / `granite3.2-vision` / `qwen3.5:9b-16k` |
| `JOVE_OCR_RECOVER` / `JOVE_OCR_LOCATOR` | `on` / `qwen3-vl:4b-instruct` |
| `JOVE_CHOOSER` | `clef` (or `llm`, `jev`) |
| `JOVE_LLM_BASE_URL` / `JOVE_LLM_MODEL` / `JOVE_LLM_API_KEY` | `http://localhost:11434/v1` / `qwen3.5:9b-16k` / — |
| `JOVE_LLM_REASONING` | `none` (turns off model thinking; empty omits the parameter) |
| `JOVE_JEV_MODEL` | `jev-latest` |
| `JOVE_CLEF_BASE_URL` / `JOVE_CLEF_MODEL` | `http://localhost:8080` / `clef-flash` |
| `TYPESAFE_API_KEY` | *(required for `jev`)* |
| `JOVE_SPLIT_GATE` | `0.5` (split only if the yes/no gate's P(split) ≥ this) |
| `JOVE_PARAGRAPHS` | `on`: the tagger asks about prose paragraph by paragraph (`off`: line by line) |
| `JOVE_OWNERS` | `on` (`off` skips the ownership pass) |
| `JOVE_DATA_DIR` | `./data` |

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
  correctly). Tune the split gate and the block gap on that set.
- **Cost:** block fan-out asks all 26 nodes every time. Asking level by level
  would cut tokens but add round trips. Only worth it if cost matters.
- **Layout:** tables and multi-column pages; word boxes from OCR character
  boxes rather than estimates.
- **Documents:** splitting page runs into documents and classifying them
  (DocJev-style) would add a document level above blocks.
- **Later:** a job queue, auth, and a review UI (explicitly out of scope for now).
