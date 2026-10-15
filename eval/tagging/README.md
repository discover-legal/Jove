# Tagging benchmark

```sh
export TYPESAFE_API_KEY=...
uv run python eval/tagging/run.py tree        # or flat; OCR is cached per page
uv run python eval/tagging/score.py eval/tagging/results/tree [-v]
uv run python eval/tagging/run.py flat --chooser laya --out laya   # local Laya instead of Jev
uv run python eval/tagging/run.py flat --chooser clef --out clef   # local Clef-Flash (LE_CLEF_BASE_URL)
uv run python eval/tagging/run.py flat --chooser clef --ocr-cache fresh --out clef_fresh   # re-OCR into cache/fresh/
uv run python eval/tagging/chunks.py rules --ocr ppocr4            # no chooser: OCR/layout ceiling
```

`--ocr tesseract|ppocr4|v6` (run.py and chunks.py) swaps the OCR engine
(eval/tagging/ocr_engines.py); those engines read the page image even when
there is a text layer, and are cached under `cache/<engine>/`.

`gold/*.json` gives each document's expected values: text, field (`path`), any
accepted alternatives (`alt`), and `region` for spans where any piece tagged
with that field counts (e.g. a biography). `groups` lists snippets that must
come out as one list item. The gold is hand-made from the cached OCR text,
so OCR errors appear in it as-is.

Results are in docs/PLAN.md ("Measured").

## Documents

- `gold/` covers 11 documents, 222 values.
- Synthetic: the intake form and demand letter (eval/ocr/synthetic).
- Real photos: the gig flyer and the transit notice (eval/ocr/real).
- Public Ontario documents, in `docs/`:
  - Divisional Court endorsement (eval/ocr/real);
  - Court of Appeal for Ontario 2022 ONCA 250 (costs endorsement), plus a
    fax-degraded copy;
  - 2022 ONCA 100, page 1;
  - IPC Order PO-2917, pp. 1-2.
- The official LTB Form N4, filled by `make_forms.py` with **invented** values:
  a digital PDF and a phone-photo copy (image-only PDF, so it goes through OCR).

Blank forms come from tribunalsontario.ca; `make_forms.py` fills them.
