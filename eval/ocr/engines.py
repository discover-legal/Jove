"""OCR engine variants under evaluation. Each factory returns image path -> OcrLines."""

from pathlib import Path

from jove.ocr import OcrLine
from jove.ingest import Page


def _page(path) -> Page:
    return path if isinstance(path, Page) else Page("p0001", path.name, 1, path, "", 0, 0)


def rapidocr_baseline():
    """What the app ships today: RapidOCR 1.4 det + English PP-OCRv3 rec."""
    from jove.config import make_ocr
    ocr = make_ocr()
    return lambda path: ocr.read(_page(path))


ENGINES = {
    "rapidocr": rapidocr_baseline,
}


def _rapid3(det_ver, det_type, det_lang, rec_ver, rec_type, rec_lang, **extra):
    def factory():
        from rapidocr import LangDet, LangRec, ModelType, OCRVersion, RapidOCR

        engine = RapidOCR(params={
            "Global.log_level": "error",
            "Det.ocr_version": OCRVersion[det_ver], "Det.model_type": ModelType[det_type], "Det.lang_type": LangDet[det_lang],
            "Rec.ocr_version": OCRVersion[rec_ver], "Rec.model_type": ModelType[rec_type], "Rec.lang_type": LangRec[rec_lang],
            **extra,
        })

        def read(path):

            path = getattr(path, "image_path", path)  # bench may pass a Page
            r = engine(str(path))
            out = []
            for box, text, score in zip(r.boxes if r.boxes is not None else [], r.txts or [], r.scores or []):
                xs, ys = [p[0] for p in box], [p[1] for p in box]
                out.append(OcrLine(text, (min(xs), min(ys), max(xs), max(ys)), float(score),
                                   tuple((float(x), float(y)) for x, y in box)))
            return out
        return read
    return factory


ENGINES |= {
    "v5_en": _rapid3("PPOCRV5", "SERVER", "CH", "PPOCRV5", "MOBILE", "EN"),
    "v5_latin": _rapid3("PPOCRV5", "SERVER", "CH", "PPOCRV5", "MOBILE", "LATIN"),
    "v6_tiny": _rapid3("PPOCRV6", "TINY", "EN", "PPOCRV6", "TINY", "EN"),
    "v6_small": _rapid3("PPOCRV6", "SMALL", "EN", "PPOCRV6", "SMALL", "EN"),
    "v6_medium": _rapid3("PPOCRV6", "MEDIUM", "EN", "PPOCRV6", "MEDIUM", "EN"),
}


# --- preprocessing variants ------------------------------------------------

import tempfile  # noqa: E402

import cv2  # noqa: E402
import numpy as np  # noqa: E402


def upscale(img, f=2.0):
    return cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)


def unsharp(img, amount=1.0, sigma=2.0):
    blur = cv2.GaussianBlur(img, (0, 0), sigma)
    return cv2.addWeighted(img, 1 + amount, blur, -amount, 0)


def clahe(img):
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    lab[..., 0] = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(lab[..., 0])
    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)


def page_warp(img):
    """Find the paper (largest bright quadrilateral) and flatten it; no-op if none."""
    small = cv2.resize(img, None, fx=0.25, fy=0.25)
    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return img
    c = max(contours, key=cv2.contourArea)
    if cv2.contourArea(c) < 0.15 * small.shape[0] * small.shape[1]:
        return img
    quad = cv2.approxPolyDP(c, 0.02 * cv2.arcLength(c, True), True)
    if len(quad) != 4:
        return img
    pts = quad.reshape(4, 2).astype(np.float32) * 4
    s, d = pts.sum(1), np.diff(pts, axis=1).ravel()
    tl, br, tr, bl = pts[np.argmin(s)], pts[np.argmax(s)], pts[np.argmin(d)], pts[np.argmax(d)]
    w = int(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl)))
    h = int(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)))
    M = cv2.getPerspectiveTransform(np.float32([tl, tr, br, bl]), np.float32([[0, 0], [w, 0], [w, h], [0, h]]))
    return cv2.warpPerspective(img, M, (w, h))


def pre(base: str, *steps):
    def factory():
        inner = ENGINES[base]()

        def read(path):

            path = getattr(path, "image_path", path)  # bench may pass a Page
            img = cv2.imread(str(path))
            for step in steps:
                img = step(img)
            with tempfile.NamedTemporaryFile(suffix=".png") as f:
                cv2.imwrite(f.name, img)
                return inner(Path(f.name))
        return read
    return factory


ENGINES |= {
    "v6s_up2": pre("v6_small", upscale),
    "v6s_sharp": pre("v6_small", unsharp),
    "v6s_clahe": pre("v6_small", clahe),
    "v6s_warp": pre("v6_small", page_warp),
    "v6s_warp_up2_sharp": pre("v6_small", page_warp, upscale, unsharp),
}


# --- recursive re-read of doubtful lines with a local vision model ------------

import base64  # noqa: E402
import re  # noqa: E402

import httpx  # noqa: E402
from rapidfuzz.distance import Levenshtein  # noqa: E402

VLM_PROMPT = (
    "This image is one line of text cropped from a photographed document. "
    "Transcribe it exactly: same words, spelling, punctuation, capitalisation and numbers. "
    "A rough OCR reading was: {hint!r}. It may contain errors; trust the image. "
    "Output only the transcription."
)


def line_crop(img, quad, pad=0.35, height=64):
    q = np.float32(quad)
    w = int(max(np.linalg.norm(q[1] - q[0]), np.linalg.norm(q[2] - q[3])))
    h = int(max(np.linalg.norm(q[3] - q[0]), np.linalg.norm(q[2] - q[1])))
    p = pad * h
    dst = np.float32([[p, p], [w + p, p], [w + p, h + p], [p, h + p]])
    M = cv2.getPerspectiveTransform(q, dst)
    crop = cv2.warpPerspective(img, M, (int(w + 2 * p), int(h + 2 * p)), borderMode=cv2.BORDER_REPLICATE)
    f = height / crop.shape[0]
    return cv2.resize(crop, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC) if f > 1 else crop


def keep_ocr_digits(ocr: str, vlm: str) -> str:
    """Take the VLM's words but the OCR's tokens wherever digits are involved.

    A language model "fixes" a blurred 198o into a plausible 1980; for legal
    documents a confident wrong number is worse than a visibly garbled one.
    """
    import difflib

    a, b = ocr.split(), vlm.split()
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op == "equal":
            out += a[i1:i2]
        elif op == "replace" and i2 - i1 == j2 - j1:
            out += [_pick(x, y) for x, y in zip(a[i1:i2], b[j1:j2])]  # token by token
        elif any(re.search(r"\d", t) for t in a[i1:i2] + b[j1:j2]):
            out += a[i1:i2]
        else:
            out += b[j1:j2]
    return " ".join(out)


# Glyphs OCR confuses with digits. A VLM token that matches the OCR token under
# this map only normalises what OCR saw; anything else would be a new number.
_CONFUSABLE = str.maketrans("OoQDIl|iSsZzBbGg", "0000111155228869")


def _pick(ocr_tok: str, vlm_tok: str) -> str:
    if not (re.search(r"\d", ocr_tok) or re.search(r"\d", vlm_tok)):
        return vlm_tok
    return vlm_tok if ocr_tok.translate(_CONFUSABLE) == vlm_tok.translate(_CONFUSABLE) else ocr_tok


def vlm_reread(base: str, model="qwen3.5:9b-16k", threshold=0.95, min_similarity=0.5, log=None, guard_digits=True):
    def factory():
        inner = ENGINES[base]()

        def read(path):

            path = getattr(path, "image_path", path)  # bench may pass a Page
            lines = inner(path)
            img = cv2.imread(str(path))
            for i, line in enumerate(lines):
                if (line.confidence or 0) >= threshold or not line.quad:
                    continue
                ok, png = cv2.imencode(".png", line_crop(img, line.quad))
                r = httpx.post("http://localhost:11434/v1/chat/completions", timeout=120, json={
                    "model": model, "temperature": 0, "reasoning_effort": "none",
                    "messages": [{"role": "user", "content": [
                        {"type": "text", "text": VLM_PROMPT.format(hint=line.text)},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png.tobytes()).decode()}},
                    ]}],
                }).json()
                text = (r["choices"][0]["message"].get("content") or "").strip().strip('"').strip()
                sim = Levenshtein.normalized_similarity(text.lower(), line.text.lower())
                if text and "\n" not in text and sim >= min_similarity:
                    if guard_digits:
                        text = keep_ocr_digits(line.text, text)
                    if log is not None:
                        log.append((line.text, text, round(sim, 2)))
                    lines[i] = OcrLine(text, line.bbox, line.confidence, line.quad)
            return lines
        return read
    return factory


ENGINES |= {
    "v6s_vlm": vlm_reread("v6_small"),
}
ENGINES |= {"v6s_vlm98": vlm_reread("v6_small", threshold=0.98)}
ENGINES |= {"v6s_vlm98_raw": vlm_reread("v6_small", threshold=0.98, guard_digits=False)}


def app_ocr():
    """Whatever src/jove/ocr.py ships (config.make_ocr)."""
    from jove.config import make_ocr

    ocr = make_ocr()
    return lambda path: ocr.read(_page(path))


ENGINES |= {"app": app_ocr}


# --- whole-page OCR VLMs ------------------------------------------------------

_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"})


def vlm_page_text(path, model="glm-ocr", prompt="Text Recognition:") -> str:
    b = base64.b64encode(Path(path).read_bytes()).decode()
    r = httpx.post("http://localhost:11434/api/chat", timeout=600, json={
        "model": model, "stream": False, "think": False, "options": {"temperature": 0, "num_ctx": 16384},
        "messages": [{"role": "user", "content": prompt, "images": [b]}]}).json()
    text = r.get("message", {}).get("content", "")
    text = re.sub(r"^```\w*|```$", "", text.strip(), flags=re.M)  # some models fence their output
    return text.translate(_QUOTES)


def vlm_page(model, prompt="Text Recognition:"):
    """Whole-page transcription; lines get stacked fake boxes so layout keeps their order."""
    def factory():
        def read(path):
            path = getattr(path, "image_path", path)  # bench may pass a Page
            lines = [l.strip() for l in vlm_page_text(path, model, prompt).splitlines() if l.strip()]
            return [OcrLine(t, (0, i * 30, 10 * len(t), i * 30 + 20), 1.0) for i, t in enumerate(lines)]
        return read
    return factory


ENGINES |= {
    "glm_page": vlm_page("glm-ocr"),
    "granite_page": vlm_page("granite3.2-vision", "Transcribe all the text in this image exactly, line by line. Output only the text."),
}


def plain_v6():
    from jove.ocr import RapidOcr

    ocr = RapidOcr()
    return lambda path: ocr.read(_page(path))


ENGINES |= {
    "v6_respace": plain_v6,
}


def voted(**kw):
    def factory():
        from jove.ocr import RapidOcr
        from jove.vote import LineVote

        ocr = LineVote(RapidOcr(), **kw)
        return lambda path: ocr.read(_page(path))
    return factory


ENGINES |= {"vote": voted()}
ENGINES |= {"vote_deepseek": voted(escalation="deepseek-ocr")}
ENGINES |= {"vote_q3vl": voted(tiebreaker="qwen3-vl:4b", escalation="qwen3-vl:4b")}
