"""A job's result as one HTML page: every page image with its tagged text boxed,
and the found values listed by field. Rank-1 value tags only (no ``#none`` or
``#label``), which is what a reader wants to check at a glance."""

import hashlib
from html import escape

from .choose import is_value

STYLE = """
:root{--bg:#fff;--fg:#1d1d1f;--muted:#6e6e73;--line:#e5e5ea;--card:#f7f7f9}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--fg:#f2f2f4;--muted:#9a9aa1;--line:#2c2c30;--card:#1c1c1f}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif}
header{padding:16px;border-bottom:1px solid var(--line)}
header h1{margin:0 0 4px;font-size:20px}
header p{margin:0;color:var(--muted)}
a{color:inherit}
main{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,26rem);gap:16px;padding:16px}
@media (max-width:900px){main{grid-template-columns:minmax(0,1fr)}}
.page{position:relative;margin:0 0 16px;border:1px solid var(--line);background:#fff}
.page img,.page svg{display:block;width:100%;height:auto}
.page svg{position:absolute;inset:0;height:100%}
.page rect{fill-opacity:.18;stroke-width:2;vector-effect:non-scaling-stroke}
.page rect:hover,.page rect.on{fill-opacity:.45}
.page figcaption{position:absolute;top:4px;left:4px;font-size:12px;background:#000a;color:#fff;padding:1px 6px;border-radius:4px}
aside section{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin:0 0 12px}
aside h2{margin:0 0 6px;font-size:13px;font-family:ui-monospace,monospace;display:flex;gap:6px;align-items:center}
aside h2 i{width:10px;height:10px;border-radius:2px;display:inline-block}
aside ol{margin:0;padding:0;list-style:none}
aside li{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;padding:3px 0;border-top:1px solid var(--line);cursor:pointer}
aside li:first-child{border-top:0}
aside li small{color:var(--muted);font-variant-numeric:tabular-nums}
aside .text{overflow-wrap:anywhere}
.empty{color:var(--muted)}
"""

SCRIPT = """
document.querySelectorAll('[data-node]').forEach(el => el.addEventListener('mouseenter', () => {
  document.querySelectorAll('.on').forEach(x => x.classList.remove('on'));
  document.querySelectorAll('[data-node="' + el.dataset.node + '"]').forEach(x => x.classList.add('on'));
}));
document.querySelectorAll('li[data-node]').forEach(li => li.addEventListener('click', () => {
  document.querySelector('rect[data-node="' + li.dataset.node + '"]')?.scrollIntoView({block: 'center', behavior: 'smooth'});
}));
"""


def render(job_id: str, status: dict, records: list[dict]) -> str:
    head = f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>" \
           f"<title>Jove result</title><style>{STYLE}</style>"
    if status["status"] != "done":
        note = escape(status.get("error") or "This page refreshes until the job is done.")
        refresh = "" if status["status"] == "failed" else "<meta http-equiv=refresh content=5>"
        return f"{head}{refresh}<header><h1>Job {status['status']}</h1><p>{note}</p></header>"

    job = next(r for r in records if r["type"] == "job")
    pages = [r for r in records if r["type"] == "page"]
    nodes = {r["id"]: r for r in records if r["type"] == "node"}
    tags = [e for e in records if e["type"] == "edge" and e["rel"] == "tagged" and e["rank"] == 1
            and is_value(e["to"]) and e["from"] in nodes]

    by_page: dict[str, list[dict]] = {}
    by_field: dict[str, list[dict]] = {}
    for e in tags:
        by_page.setdefault(nodes[e["from"]]["page_id"], []).append(e)
        by_field.setdefault(e["to"], []).append(e)

    figures = []
    for p in pages:
        rects = []
        for e in by_page.get(p["page_id"], []):
            n = nodes[e["from"]]
            x0, y0, x1, y1 = n["bbox"]
            c = colour(e["to"])
            rects.append(f"<rect data-node='{escape(n['id'])}' x='{x0}' y='{y0}' width='{x1 - x0}' height='{y1 - y0}' "
                         f"fill='{c}' stroke='{c}'><title>{escape(e['to'])} · {e['score']:.2f}\n"
                         f"{escape(n['text'])}</title></rect>")
        figures.append(
            f"<figure class=page id='{p['page_id']}'><img src='pages/{p['page_id']}.png' alt='page {p['source_page']}' "
            f"width='{p['width']}' height='{p['height']}'>"
            f"<svg viewBox='0 0 {p['width']} {p['height']}' preserveAspectRatio='none'>{''.join(rects)}</svg>"
            f"<figcaption>{escape(p['source'])} · page {p['source_page']}</figcaption></figure>")

    sections = []
    for field in sorted(by_field):
        items = sorted(by_field[field], key=lambda e: (nodes[e["from"]]["page_id"], -e["score"]))
        rows = "".join(
            f"<li data-node='{escape(e['from'])}'><span class=text>{escape(_clip(nodes[e['from']]['text']))}</span>"
            f"<small>{e['score']:.2f} · {nodes[e['from']]['page_id']}</small></li>" for e in items)
        sections.append(f"<section><h2><i style='background:{colour(field)}'></i>{escape(field)}</h2><ol>{rows}</ol></section>")

    files = ", ".join(escape(i["filename"]) for i in job["inputs"])
    chooser = escape(" ".join(str(v) for v in job["chooser"].values()))
    summary = f"{len(tags)} values in {len(by_field)} fields across {len(pages)} pages · chooser {chooser} · " \
              f"<a href='result.jsonl'>result.jsonl</a>"
    found = "".join(sections) or "<p class=empty>Nothing was tagged.</p>"
    return (f"{head}<header><h1>{files}</h1><p>{summary}</p></header>"
            f"<main><div>{''.join(figures)}</div><aside>{found}</aside></main><script>{SCRIPT}</script>")


def colour(field: str) -> str:
    """A stable colour per top-level field, so a field's boxes match its list."""
    top = field.split(".")[0].split("#")[0].removesuffix("[]")
    return f"hsl({int(hashlib.sha256(top.encode()).hexdigest()[:4], 16) % 360} 70% 45%)"


def _clip(text: str, n: int = 240) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"
