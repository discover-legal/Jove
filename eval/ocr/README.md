# OCR benchmark

```sh
uv run python eval/ocr/make_synthetic.py      # regenerate synthetic pages (deterministic)
uv run python eval/ocr/bench.py app           # score what the app ships
uv run python eval/ocr/bench.py v6_small      # or any engine in engines.py
```

- `real/`: real captures. A bar flyer photographed under low light (blurred,
  italic, angled), a torn transit-stop notice at night (reflective, multi-panel),
  both hand-transcribed; and a born-digital Divisional Court endorsement PDF,
  whose truth is its own text layer.
- `synthetic/`: six Ontario legal pages (intake form, demand letter, court
  endorsement, pay stub, N4 notice, italic notes). Each is rendered as `scan`,
  `photo` (perspective, tilt, blur, uneven light, JPEG), `fax`, `hard`
  (a stronger photo) and `shake` (small text, a hand-shake double exposure and
  motion blur, dim: modelled on the flyer). The ground truth is exact.

Metrics: `cer` (character error rate, reading order after layout), `word_f1`,
and `num_rec` (tokens with digits that come back exactly).

## Round 1 (2026-09-27, 25 pages, before `shake` existed)

| engine | ALL f1 | ALL num | flyer f1 | flyer cer | s/page |
|---|---|---|---|---|---|
| RapidOCR 1.4 + PP-OCRv3 en (previous) | 0.834 | 0.740 | 0.308 | 0.263 | 1.4 |
| PP-OCRv5 server det + en rec | 0.969 | 0.953 | 0.636 | 0.115 | 2.8 |
| PP-OCRv6 small | 0.978 | 0.969 | 0.665 | 0.083 | 1.2 |
| PP-OCRv6 medium | 0.978 | 0.975 | 0.701 | 0.085 | 3.4 |
| v6 small + upscale / unsharp / CLAHE / page warp | ≤0.974 | — | ≤0.676 | — | — |
| v6 small + unconstrained VLM re-read | 0.990 | 0.977 | 0.818 | 0.163 | 2.8 |
| **app**: v6 small + respace + gutter layout + constrained VLM re-read | **0.994** | **0.977** | **0.933** | **0.023** | 2.4 |

Lessons:
- The OCR model generation matters most (v3 → v6).
- Classic image preprocessing didn't help.
- An unconstrained vision LLM raises word scores while inventing text
  ("…albums worldwide, despite a Grammy nomination, why?", "Craiy" → "Brian").
  Constraining it to the OCR's token structure fixed both problems.
- The only real image is one flyer. Add real Ontario scans before trusting fine differences.

## Round 2 (2026-09-28, 31 pages including `shake`)

| engine | ALL f1 | ALL num | shake f1 | shake num | flyer f1 | flyer num | s/page |
|---|---|---|---|---|---|---|---|
| round-1 app (constrained qwen re-read) | 0.910 | 0.822 | 0.560 | 0.176 | 0.933 | 0.600 | 3.4 |
| glm-ocr whole page, alone | 0.931 | 0.910 | 0.984 | 0.897 | 0.973 | 0.800 | 5.3 |
| PP-OCR + glm page fusion (constrained) | 0.883 | 0.822 | 0.462 | 0.176 | 0.749 | 0.600 | 5.1 |
| vote, qwen3.5 tiebreaker on every dispute | 0.932 | 0.887 | 0.660 | 0.451 | 0.931 | 0.800 | 30.9 |
| vote, granite medoid | 0.928 | 0.866 | 0.683 | 0.382 | 0.765 | 0.600 | 7.1 |
| vote, qwen3-vl:4b tie+witness (fits in VRAM) | — | — | 0.758 | 0.342 | 0.933 | 0.600 | 20 |
| **app: vote (glm-ocr words, granite outlier check, qwen3.5 number witness)** | **0.975** | **0.923** | **0.884** | **0.636** | **0.942** | **0.800** | 13.8 |

Scans, faxes and photos are now essentially perfect (f1 ≥ 0.996, numbers 1.000).

Lessons:
- **glm-ocr alone** is superb on blurred photos, but it loops on faxes (CER 27.8: it
  repeats text until the context is full) and states wrong numbers confidently
  (a blurred 1986 became "1996"). Never trust it alone.
- **Constraining it to PP-OCR's reading** fails where PP-OCR is garbage: there is
  nothing similar to accept.
- **A plain majority vote** fails because readers aren't equal. Two weak readers
  outvote the strong one.
- **What works:** trust the strongest reader for words, check it for
  hallucination only when it looks nothing like the OCR, and require a second
  witness for every number, escalating to a stronger model only for those lines.
- **Recognition score floor:** RapidOCR's default drops lines below 0.5. On shaky
  pages that discards real lines; keep them and let the vote repair them.
- **8 GB VRAM:** deepseek-ocr (8.7 GB) doesn't fit. The shipped trio doesn't fit
  together either, so Ollama swaps models; that's most of the 40 s on shaky pages.

## Round 3 (2026-09-29)

- The layout step now does an XY-cut into regions (tables stay whole) and splits
  rows between very different text sizes. Photo CER 0.027 → 0.016, fax
  0.028 → 0.017, hard 0.018 → 0.007.
- The PDF text layer is used when present. The court endorsement went from f1
  0.933 / numbers 0.571 (plain OCR) to 0.992 / 1.000, at 0.1 s per page.
- Real set: court PDF f1 0.992, flyer 0.942, transit sign 0.922.

## Round 4 (2026-10-15): missed lines and straightening

Same 33 pages, all four arms run the same day. A recovery-off re-run matched
round 2 exactly (f1 0.974, numbers 0.923), so these runs repeat.

| app OCR | ALL f1 | ALL num | shake f1 | shake num | s/page |
|---|---|---|---|---|---|
| vote (round 2 setup) | 0.974 | 0.923 | 0.884 | 0.636 | 23.2 |
| **+ missed-line recovery (default)** | **0.976** | **0.929** | **0.898** | **0.669** | 32.8 |
| + page straightening, surer reading wins | 0.968 | 0.917 | 0.850 | 0.603 | 27.1 |
| + both | 0.972 | 0.932 | 0.876 | 0.688 | 34.1 |

- **Missed-line recovery** (`recover.py`) adds lines the detector never boxed.
  glm-ocr reads the whole page, qwen3-vl:4b-instruct locates rows with words no
  detected line has, and each candidate is cropped and read again. It is kept only
  if the crop matches (similarity ≥ 0.7, numbers backed). It gave pay_stub.shake
  f1 0.66 → 0.74 and numbers 0.2 → 0.4. On a real pesticide sign photo it
  found "WEED MAN" (slanted serif) and a logo's "Man®". The synthetic set barely
  exercises it: its pages have few undetected lines. Rows over 12 words
  (paragraphs) are skipped, because a misread word there is not a missed line.
  The thinking qwen3-vl tag timed out on such rows.
- **Page straightening** (`straighten.py`, `LE_OCR_STRAIGHTEN=on`) flattens a
  detected sheet, or levels a page from its line angles, reads it again, and
  keeps the reading the engine is surer of. It helped ltb_notice.shake (numbers
  0.44 → 0.89) but hurt demand_letter.shake and the handwritten note: on double
  exposures the engine is confidently wrong. Ungated, it was worse still (ALL
  f1 0.961). It is **off by default**. The vote already straightens every line
  crop, and layout de-rotates boxes, so a page-level warp adds little here.
- Phone photos are turned upright from their EXIF orientation at ingest.

