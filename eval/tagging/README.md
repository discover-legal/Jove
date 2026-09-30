# Tagging benchmark

```sh
export TYPESAFE_API_KEY=...
uv run python eval/tagging/run.py tree        # or flat; OCR is cached per page
uv run python eval/tagging/score.py eval/tagging/results/tree [-v]
```

`gold/*.json` gives each document's expected values: text, field (`path`), any
accepted alternatives (`alt`), and `region` for spans where any piece tagged
with that field counts (e.g. a biography). `groups` lists snippets that must
come out as one list item. The gold is hand-made from the cached OCR text,
so OCR errors appear in it as-is.

Results are in docs/PLAN.md ("Measured").
