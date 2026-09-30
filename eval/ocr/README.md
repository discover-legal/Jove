# OCR benchmark

```sh
uv run python eval/ocr/make_synthetic.py      # regenerate synthetic pages (deterministic)
uv run python eval/ocr/bench.py app           # score what the app ships
uv run python eval/ocr/bench.py v6_small      # or any engine in engines.py
```

- `real/`: real captures with hand transcriptions (currently one bar flyer
  photographed under low light: blurred, italic, angled).
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
