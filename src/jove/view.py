"""Jove's pages: the upload form and a job's result.

The result page shows every page image with its tagged text boxed, and the
values found listed by field group. Rank-1 value tags only (no ``#none`` or
``#label``), which is what a reader wants to check at a glance. ``standalone``
inlines the page images, so a result can be saved as one HTML file and shown
anywhere. Everything is inline: no fonts, scripts or styles are fetched.
"""

import base64
from datetime import datetime
from html import escape
from pathlib import Path

from .choose import is_value

REPO = "https://github.com/discover-legal/Jove"

STYLE = """
:root{--bg:#f6f5f1;--surface:#fff;--ink:#1c1b19;--muted:#6b675f;--line:#e4e1d9;--soft:#efede7;
--accent:#2b44c7;--accent-ink:#fff;--radius:10px;
--sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;--mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 var(--sans)}
a{color:var(--accent)}
button,input,select,textarea{font:inherit;color:inherit}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.wrap{max-width:1180px;margin:0 auto;padding:0 16px}
.top{border-bottom:1px solid var(--line);background:var(--surface)}
.top .wrap{display:flex;align-items:center;gap:12px;min-height:56px}
.brand{font-weight:700;font-size:18px;letter-spacing:-.01em;color:var(--ink);text-decoration:none;display:flex;align-items:center;gap:8px}
.brand svg{width:22px;height:22px}
.top .grow{flex:1}
.top nav a{color:var(--muted);text-decoration:none;font-size:14px}
.top nav a:hover{color:var(--ink)}
h1{font-size:28px;line-height:1.2;letter-spacing:-.02em;margin:0 0 8px}
.lede{color:var(--muted);margin:0;max-width:44rem}
.hero{padding:40px 0 24px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:20px}
.grid{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(0,1fr);gap:20px;align-items:start;padding-bottom:40px}
@media (max-width:860px){.grid{grid-template-columns:minmax(0,1fr)}}
label.field{display:block;font-weight:600;font-size:14px;margin:0 0 6px}
.hint{color:var(--muted);font-size:13px;font-weight:400}
.stack>*+*{margin-top:18px}
.drop{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:6px;text-align:center;
border:1.5px dashed var(--line);border-radius:var(--radius);padding:28px 16px;background:var(--soft);cursor:pointer;transition:border-color .15s,background .15s}
.drop:hover,.drop.over{border-color:var(--accent);background:#eef0fb}
.drop input{position:absolute;width:1px;height:1px;opacity:0}
.drop strong{font-size:15px}
.drop .files{font-size:13px;color:var(--ink);font-family:var(--mono);overflow-wrap:anywhere}
select,input[type=text],textarea{width:100%;background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:9px 11px}
textarea{min-height:12rem;font:13px/1.45 var(--mono)}
.schema-desc{color:var(--muted);font-size:13px;margin:6px 0 0}
.btn{display:inline-flex;align-items:center;gap:8px;border:0;border-radius:8px;padding:10px 16px;background:var(--accent);color:var(--accent-ink);font-weight:600;cursor:pointer;text-decoration:none}
.btn:hover{filter:brightness(1.08)}
.btn.ghost{background:transparent;color:var(--ink);border:1px solid var(--line)}
.btn.ghost:hover{background:var(--soft);filter:none}
.btn.small{padding:6px 11px;font-size:13px}
details summary{cursor:pointer;color:var(--muted);font-size:14px}
.how ol{margin:0;padding-left:20px}
.how li+li{margin-top:8px}
.how h2,.jobs h2{font-size:15px;margin:0 0 10px}
.jobs{margin-top:20px}
.jobs ul{list-style:none;margin:0;padding:0}
.jobs li{display:flex;gap:10px;align-items:baseline;padding:8px 0;border-top:1px solid var(--line);font-size:14px}
.jobs li:first-child{border-top:0}
.jobs li a{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--ink);text-decoration:none}
.jobs li a:hover{color:var(--accent)}
.pill{display:inline-block;font-size:12px;border-radius:999px;padding:1px 8px;background:var(--soft);color:var(--muted);white-space:nowrap}
.pill.done{background:#e6f4ea;color:#1e6b34}.pill.failed{background:#fce8e6;color:#a1271b}.pill.running,.pill.queued{background:#eef0fb;color:var(--accent)}
.muted{color:var(--muted)}
.foot{color:var(--muted);font-size:13px;padding:24px 0 32px;border-top:1px solid var(--line);margin-top:8px}
/* result */
.head{padding-block:24px 12px;display:flex;flex-wrap:wrap;gap:12px 20px;align-items:flex-end}
.head>div:first-child{flex:1 1 22rem;min-width:0}
.head .links{display:flex;gap:8px}
.head h1{font-size:22px;overflow-wrap:anywhere}
.stats{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0 0}
.stat{background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:6px 10px;font-size:13px}
.stat b{font-size:15px;margin-right:4px}
.toolbar{position:sticky;top:0;z-index:5;background:color-mix(in srgb,var(--bg) 92%,transparent);backdrop-filter:blur(6px);
border-bottom:1px solid var(--line);padding:10px 0}
.toolbar .wrap{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.chip{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);background:var(--surface);border-radius:999px;padding:4px 10px;font-size:13px;cursor:pointer;user-select:none}
.chip i{width:9px;height:9px;border-radius:50%;background:var(--c)}
.chip[aria-pressed=false]{opacity:.45}
.chip[aria-pressed=false] i{background:transparent;box-shadow:inset 0 0 0 1.5px var(--c)}
.score{display:inline-flex;align-items:center;gap:8px;font-size:13px;color:var(--muted);margin-left:auto}
.score input{width:120px;accent-color:var(--accent)}
.result{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,25rem);gap:20px;padding-block:16px 8px;align-items:start}
@media (max-width:960px){.result{grid-template-columns:minmax(0,1fr)}.findings{position:static!important;max-height:none!important}}
@media (max-width:640px){.toolbar{position:static}.toolbar .wrap{flex-wrap:nowrap;overflow-x:auto;scrollbar-width:none}
.toolbar .wrap::-webkit-scrollbar{display:none}.chip,.score{flex:0 0 auto}.score{margin-left:0}h1{font-size:22px}}
.page{position:relative;margin:0 0 16px;border:1px solid var(--line);border-radius:6px;overflow:hidden;background:#fff;box-shadow:0 1px 2px #0000000d}
.page img{display:block;width:100%;height:auto}
.page svg{position:absolute;inset:0;width:100%;height:100%}
.page rect{fill:var(--c);stroke:var(--c);fill-opacity:.14;stroke-width:1.6;vector-effect:non-scaling-stroke;cursor:pointer;transition:fill-opacity .12s}
.page rect.mention{stroke-dasharray:4 3;fill-opacity:.08}
.page rect:hover,.page rect.on{fill-opacity:.38}
.page figcaption{position:absolute;top:8px;left:8px;font-size:12px;background:#1c1b19cc;color:#fff;padding:2px 8px;border-radius:999px}
.findings{position:sticky;top:64px;max-height:calc(100vh - 80px);overflow:auto;padding-right:2px}
.group{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);margin:0 0 12px;overflow:hidden}
.group h2{display:flex;align-items:center;gap:8px;margin:0;padding:10px 14px;font-size:14px;border-bottom:1px solid var(--line);background:var(--soft)}
.group h2 i{width:10px;height:10px;border-radius:3px;background:var(--c)}
.group h2 span{margin-left:auto;color:var(--muted);font-weight:400;font-size:12px}
.group ol{list-style:none;margin:0;padding:0}
.group li{padding:8px 14px;border-top:1px solid var(--line);cursor:pointer}
.group li:first-child{border-top:0}
.group li:hover,.group li.on{background:#f3f4fd}
.group .f{font:12px var(--mono);color:var(--muted)}
.group .t{overflow-wrap:anywhere;margin:2px 0 4px}
.bar{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--muted);font-variant-numeric:tabular-nums}
.bar span{flex:0 0 64px;height:4px;border-radius:2px;background:var(--line);overflow:hidden}
.bar span::before{content:"";display:block;height:100%;width:var(--w);background:var(--c)}
.tag-m{font-size:11px;border:1px dashed var(--muted);border-radius:4px;padding:0 4px}
[hidden]{display:none!important}
.status{max-width:36rem;margin:48px auto}
@media (max-width:600px){.status{margin:24px 16px}}
.spinner{width:22px;height:22px;border-radius:50%;border:3px solid var(--line);border-top-color:var(--accent);animation:spin 1s linear infinite;display:inline-block;vertical-align:middle;margin-right:10px}
@keyframes spin{to{transform:rotate(360deg)}}
@media (prefers-reduced-motion:reduce){.spinner{animation:none}}
"""

LOGO = ("<svg viewBox='0 0 24 24' aria-hidden=true><circle cx=12 cy=12 r=10 fill='#2b44c7'/>"
        "<path d='M3 10.5c5 1.6 13 1.6 18 0M2.6 14c5.5 1.7 13.3 1.7 18.8 0' stroke='#fff' stroke-width='1.4' fill='none'/>"
        "<ellipse cx=15.5 cy=15.3 rx=2.2 ry=1.3 fill='#f6c77a'/></svg>")

INDEX_SCRIPT = """
const drop = document.querySelector('.drop'), input = drop.querySelector('input'), list = drop.querySelector('.files');
const show = () => { list.textContent = [...input.files].map(f => f.name).join(', '); };
input.addEventListener('change', show);
['dragenter','dragover'].forEach(e => drop.addEventListener(e, ev => { ev.preventDefault(); drop.classList.add('over'); }));
['dragleave','drop'].forEach(e => drop.addEventListener(e, ev => { ev.preventDefault(); drop.classList.remove('over'); }));
drop.addEventListener('drop', ev => { input.files = ev.dataTransfer.files; show(); });
const select = document.querySelector('select[name=schema_name]'), custom = document.getElementById('custom'),
      desc = document.querySelector('.schema-desc');
const sync = () => { const o = select.selectedOptions[0]; custom.hidden = select.value !== '';
  custom.querySelector('textarea').required = select.value === ''; desc.textContent = o ? o.dataset.desc || '' : ''; };
select.addEventListener('change', sync); sync();
"""

RESULT_SCRIPT = """
const all = sel => document.querySelectorAll(sel);
const mark = id => { all('.on').forEach(x => x.classList.remove('on'));
  all('[data-node="' + CSS.escape(id) + '"]').forEach(x => x.classList.add('on')); };
all('[data-node]').forEach(el => el.addEventListener('mouseenter', () => mark(el.dataset.node)));
all('li[data-node]').forEach(li => li.addEventListener('click', () => {
  document.querySelector('rect[data-node="' + CSS.escape(li.dataset.node) + '"]')?.scrollIntoView({block: 'center', behavior: 'smooth'}); }));
all('rect[data-node]').forEach(r => r.addEventListener('click', () => {
  document.querySelector('li[data-node="' + CSS.escape(r.dataset.node) + '"]')?.scrollIntoView({block: 'nearest', behavior: 'smooth'}); }));
const slider = document.getElementById('min'), out = document.getElementById('minv');
const apply = () => {
  const off = new Set([...all('.chip[aria-pressed=false]')].map(c => c.dataset.group)), min = +slider.value;
  out.textContent = min.toFixed(2);
  all('[data-group]').forEach(el => { if (el.classList.contains('chip')) return;
    el.hidden = off.has(el.dataset.group) || (el.dataset.score !== undefined && +el.dataset.score < min); });
  all('.group').forEach(g => { g.hidden = off.has(g.dataset.group) || !g.querySelector('li:not([hidden])'); });
};
all('.chip').forEach(c => c.addEventListener('click', () => {
  c.setAttribute('aria-pressed', c.getAttribute('aria-pressed') === 'false'); apply(); }));
slider.addEventListener('input', apply); apply();
"""


def _page(title: str, body: str, script: str = "", refresh: int | None = None) -> str:
    meta = f"<meta http-equiv=refresh content={refresh}>" if refresh else ""
    js = f"<script>{script}</script>" if script else ""
    return (f"<!doctype html><html lang=en><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'>{meta}"
            f"<title>{escape(title)}</title><style>{STYLE}</style>{body}{js}</html>")


def _top(standalone: bool = False) -> str:
    home = REPO if standalone else "/"
    nav = f"<nav><a href='{REPO}'>GitHub</a></nav>"
    return (f"<header class=top><div class=wrap><a class=brand href='{home}'>{LOGO}Jove</a>"
            f"<span class=grow></span>{nav}</div></header>")


def _foot() -> str:
    return (f"<footer class=wrap><p class=foot>Jove tags scanned documents against a JSON data schema, locally. "
            f"Every tag points back to the exact page and bytes it came from. "
            f"<a href='{REPO}'>Source on GitHub</a>.</p></footer>")


# ---------------------------------------------------------------------- index

def index(schemas: list[dict], jobs: list[dict]) -> str:
    """The upload form. ``schemas``: {name, title, description}; ``jobs``: recent, newest first."""
    options = "".join(
        f"<option value='{escape(s['name'])}' data-desc='{escape(s.get('description') or '')}'"
        f"{' selected' if i == 0 else ''}>{escape(s['title'])}</option>" for i, s in enumerate(schemas))
    rows = "".join(
        f"<li><a href='/jobs/{j['id']}/view'>{escape(j['label'])}</a>"
        f"<span class='pill {escape(j['status'])}'>{escape(j['status'])}</span>"
        f"<span class=muted>{escape(j['when'])}</span></li>" for j in jobs)
    recent = f"<section class='card jobs'><h2>Recent jobs</h2><ul>{rows}</ul></section>" if jobs else ""
    body = f"""{_top()}<main class=wrap>
<section class=hero><h1>Scanned pages in, structured data out.</h1>
<p class=lede>Give Jove a PDF or photos and a data schema. It tags each piece of text with the field it gives a
value for, and shows you where on the page it found it. OCR and tagging run on this machine.</p></section>
<div class=grid>
<form class='card stack' method=post action='/jobs' enctype='multipart/form-data'>
<div><label class=field for=files>Documents <span class=hint>PDF or images, several at once</span></label>
<label class=drop><input id=files type=file name=files multiple required accept='.pdf,image/*'>
<strong>Drop files here or click to choose</strong><span class=hint>Born-digital PDFs use their own text; scans and photos are OCR'd.</span>
<span class=files></span></label></div>
<div><label class=field for=schema_name>Schema</label>
<select id=schema_name name=schema_name>{options}<option value='' data-desc='Paste any JSON Schema with object properties.'>Custom JSON Schema…</option></select>
<p class=schema-desc></p>
<div id=custom hidden><textarea name=schema aria-label='Custom JSON Schema' placeholder='{{"type": "object", "properties": {{ ... }}}}'></textarea></div></div>
<div><label class=field for=context>Context <span class=hint>optional: whose file this is</span></label>
<input id=context type=text name=context placeholder='Our firm: Harper &amp; Leung LLP. Our client: Jane Doe.'></div>
<div><button class=btn type=submit>Tag documents</button></div>
</form>
<div><section class='card how'><h2>How it works</h2><ol>
<li><b>Read.</b> Each page is rasterised and read: the PDF's text layer, or OCR checked by a vote of local vision models.</li>
<li><b>Tag.</b> A decision model answers, for each line or paragraph, which schema field it gives a value for, with a probability.</li>
<li><b>Show.</b> Values are boxed on the page and listed by field. The JSONL graph carries SHA-256 provenance for every tag.</li>
</ol></section>{recent}</div>
</div></main>{_foot()}"""
    return _page("Jove", body, INDEX_SCRIPT)


# ---------------------------------------------------------------------- result

def render(job_id: str, status: dict, records: list[dict], meta: dict | None = None,
           images: dict[str, Path] | None = None) -> str:
    """A job's page. ``images`` (page id -> PNG path) inlines the pages, for a standalone file."""
    meta = meta or {}
    standalone = images is not None
    if status["status"] != "done":
        return _pending(status, meta)

    job = next(r for r in records if r["type"] == "job")
    pages = [r for r in records if r["type"] == "page"]
    nodes = {r["id"]: r for r in records if r["type"] == "node"}
    tags = [e for e in records if e["type"] == "edge" and e["rel"] == "tagged" and e["rank"] == 1
            and is_value(e["to"]) and e["from"] in nodes]

    by_page: dict[str, list[dict]] = {}
    by_group: dict[str, list[dict]] = {}
    for e in tags:
        by_page.setdefault(nodes[e["from"]]["page_id"], []).append(e)
        by_group.setdefault(group(e["to"]), []).append(e)

    figures = []
    order = sorted(by_group, key=lambda g: min(_reading(e["from"]) for e in by_group[g]))
    palette = {g: PALETTE[i % len(PALETTE)] for i, g in enumerate(order)}
    for k, p in enumerate(pages):
        rects = []
        for e in by_page.get(p["page_id"], []):
            n = nodes[e["from"]]
            x0, y0, x1, y1 = n["bbox"]
            g = group(e["to"])
            rects.append(
                f"<rect data-node='{escape(n['id'])}' data-group='{escape(g)}' data-score='{e['score']:.4f}'"
                f"{' class=mention' if e.get('mention') else ''} style='--c:{palette[g]}' x='{x0}' y='{y0}' "
                f"width='{max(x1 - x0, 1)}' height='{max(y1 - y0, 1)}'><title>{escape(e['to'])} · {e['score']:.2f}\n"
                f"{escape(n['text'])}</title></rect>")
        src = _data_uri(images[p["page_id"]]) if standalone else f"pages/{p['page_id']}.png"
        figures.append(
            f"<figure class=page id='{p['page_id']}'><img src='{src}' alt='{escape(p['source'])}, page {p['source_page']}' "
            f"width='{p['width']}' height='{p['height']}'{' loading=lazy' if k else ''}>"
            f"<svg viewBox='0 0 {p['width']} {p['height']}' preserveAspectRatio='none'>{''.join(rects)}</svg>"
            f"<figcaption>{escape(p['source'])} · page {p['source_page']}</figcaption></figure>")

    groups, chips = [], []
    for g in order:
        items = sorted(by_group[g], key=lambda e: _reading(e["from"]))
        rows = "".join(
            f"<li data-node='{escape(e['from'])}' data-group='{escape(g)}' data-score='{e['score']:.4f}' style='--c:{palette[g]}'>"
            f"<div class=f>{escape(_field(e['to']))}{' <span class=tag-m>in prose</span>' if e.get('mention') else ''}</div>"
            f"<div class=t>{escape(_clip(nodes[e['from']]['text']))}</div>"
            f"<div class=bar><span style='--w:{e['score'] * 100:.0f}%'></span>{e['score']:.2f} · "
            f"page {nodes[e['from']]['page_id'].removeprefix('p').lstrip('0')}</div></li>" for e in items)
        groups.append(f"<section class=group data-group='{escape(g)}' style='--c:{palette[g]}'><h2><i></i>{escape(_title(g))}"
                      f"<span>{len(items)}</span></h2><ol>{rows}</ol></section>")
        chips.append(f"<button type=button class=chip data-group='{escape(g)}' aria-pressed=true style='--c:{palette[g]}'>"
                     f"<i></i>{escape(_title(g))}</button>")

    files = ", ".join(i["filename"] for i in job["inputs"])
    chooser = job["chooser"].get("model") or job["chooser"].get("provider", "")
    took = _took(meta.get("created"), status.get("updated"))
    stats = [f"<b>{len(tags)}</b>values", f"<b>{len(by_group)}</b>field groups", f"<b>{len(pages)}</b>pages"]
    if meta.get("schema"):
        stats.append(f"<b>{escape(meta['schema'])}</b>schema")
    stats.append(f"<b>{escape(chooser)}</b>chooser")
    if took:
        stats.append(f"<b>{took}</b>to tag")
    links = "" if standalone else (
        "<a class='btn ghost small' href='view.html' download>Download page</a>"
        "<a class='btn ghost small' href='result.jsonl'>JSONL</a>")
    body = f"""{_top(standalone)}<main>
<div class='wrap head'><div><h1>{escape(files)}</h1><div class=stats>{''.join(f"<span class=stat>{s}</span>" for s in stats)}</div></div>
<div class=links>{links}</div></div>
<div class=toolbar><div class=wrap>{''.join(chips)}
<label class=score>min score <input id=min type=range min=0 max=1 step=0.05 value=0><output id=minv>0.00</output></label></div></div>
<div class='wrap result'><div>{''.join(figures)}</div>
<aside class=findings aria-label='Values found'>{''.join(groups) or "<p class=muted>Nothing was tagged.</p>"}</aside></div>
</main>{_foot()}"""
    return _page(f"{files} · Jove", body, RESULT_SCRIPT)


def _pending(status: dict, meta: dict) -> str:
    label = escape(", ".join(meta.get("files", [])) or "Your documents")
    if status["status"] == "failed":
        inner = (f"<h1>That didn't work</h1><p class=lede>{label}</p>"
                 f"<p><code>{escape(status.get('error') or 'unknown error')}</code></p><p><a class=btn href='/'>Try again</a></p>")
        return _page("Failed · Jove", f"{_top()}<main class='wrap status card'>{inner}</main>")
    inner = (f"<h1><span class=spinner></span>Tagging…</h1><p class=lede>{label}</p>"
             f"<p class=muted>Status: {escape(status['status'])}. Locally this takes about a minute per photo and a few "
             f"minutes for a multi-page filing. This page updates by itself.</p>")
    return _page("Tagging · Jove", f"{_top()}<main class='wrap status card'>{inner}</main>", refresh=4)


# ---------------------------------------------------------------------- helpers

def group(path: str) -> str:
    """The top-level field a tag belongs to: its group on the page and in the list."""
    return path.split(".")[0].split("#")[0].removesuffix("[]")


# Distinct on white, assigned in reading order, so neighbouring groups never share a hue.
PALETTE = ["#2b6cdf", "#d6452f", "#1f9d6b", "#9b4fd0", "#e08a00", "#0f8fa8",
           "#c2378b", "#5b6b00", "#7a5230", "#3d4fb8", "#b0302f", "#2e7d32"]


def _title(g: str) -> str:
    return g.replace("_", " ").capitalize()


def _field(path: str) -> str:
    rest = path.split(".", 1)[1] if "." in path else path
    return rest.replace("[]", "").replace(".", " › ").replace("_", " ")


def _reading(node_id: str) -> tuple:
    return tuple(node_id.split("/"))


def _clip(text: str, n: int = 280) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def _took(created: str | None, updated: str | None) -> str:
    try:
        secs = (datetime.fromisoformat(updated) - datetime.fromisoformat(created)).total_seconds()
    except (TypeError, ValueError):
        return ""
    return f"{secs:.0f} s" if secs < 90 else f"{secs / 60:.1f} min"


def _data_uri(path: Path, side: int = 1600) -> str:
    """The page as an inline JPEG, scaled to ``side`` px: a 6-page filing stays a few MB."""
    import io

    from PIL import Image

    img = Image.open(path).convert("RGB")
    img.thumbnail((side, side))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
