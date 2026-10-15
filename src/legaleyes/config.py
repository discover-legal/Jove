"""Build providers from environment variables (see docs/PLAN.md)."""

import os
from pathlib import Path

from .choose import Chooser
from .ocr import Ocr


def data_dir() -> Path:
    return Path(os.environ.get("LE_DATA_DIR", "./data"))


def separation() -> float:
    return float(os.environ.get("LE_SEPARATION", "1.5"))


def tagger() -> str:
    name = os.environ.get("LE_TAGGER", "flat")  # measured better: docs/PLAN.md "Measured"
    if name not in ("tree", "flat"):
        raise ValueError(f"unknown LE_TAGGER {name!r}")
    return name


def layout() -> str:
    name = os.environ.get("LE_LAYOUT", "rules")
    if name not in ("rules", "docling"):
        raise ValueError(f"unknown LE_LAYOUT {name!r}")
    return name


def owners() -> bool:
    return os.environ.get("LE_OWNERS", "on") != "off"


def split_gate() -> float:
    return float(os.environ.get("LE_SPLIT_GATE", "0.5"))


LLM_BASE_URL = "http://localhost:11434/v1"
LLM_MODEL = "qwen3.5:9b-16k"


def make_ocr() -> Ocr:
    name = os.environ.get("LE_OCR", "rapidocr")
    if name != "rapidocr":
        raise ValueError(f"unknown LE_OCR {name!r}")
    from .ocr import RapidOcr

    ocr: Ocr = RapidOcr()
    if os.environ.get("LE_OCR_STRAIGHTEN", "off") == "on":  # off: it cost word F1 on eval/ocr (docs/PLAN.md)
        from .straighten import Straighten

        ocr = Straighten(ocr)
    mode = os.environ.get("LE_OCR_REREAD", "vote")
    if mode == "vote":
        from .vote import LineVote

        ocr = LineVote(
            ocr,
            base_url=os.environ.get("LE_LLM_BASE_URL", LLM_BASE_URL),
            reader=os.environ.get("LE_OCR_READER", "glm-ocr"),
            tiebreaker=os.environ.get("LE_OCR_TIEBREAKER", "granite3.2-vision"),
            escalation=os.environ.get("LE_OCR_ESCALATION", LLM_MODEL),
        )
        if os.environ.get("LE_OCR_RECOVER", "on") != "off":
            from .recover import MissedLines

            ocr = MissedLines(
                ocr,
                base_url=os.environ.get("LE_LLM_BASE_URL", LLM_BASE_URL),
                reader=os.environ.get("LE_OCR_READER", "glm-ocr"),
                locator=os.environ.get("LE_OCR_LOCATOR", "qwen3-vl:4b-instruct"),
            )
    elif mode == "vlm":
        from .reread import VlmReread

        ocr = VlmReread(
            ocr,
            base_url=os.environ.get("LE_LLM_BASE_URL", LLM_BASE_URL),
            model=os.environ.get("LE_VLM_MODEL", LLM_MODEL),
            threshold=float(os.environ.get("LE_OCR_REREAD_BELOW", "0.98")),
        )
    from .ocr import TextLayerFirst

    return TextLayerFirst(ocr)


CLEF_BASE_URL = "http://localhost:8080"


def make_clef() -> Chooser:
    """Cloudflare's open Clef-Flash served locally by llama-server: it speaks Jev's
    /v1/systemone, so the Jev chooser and SDK are reused against a local URL."""
    from typesafe_sdk import TypeSafeClient

    from .jev import JevChooser

    model = os.environ.get("LE_CLEF_MODEL", "clef-flash")
    client = TypeSafeClient(base_url=os.environ.get("LE_CLEF_BASE_URL", CLEF_BASE_URL),
                            api_key="local", model=model, timeout=600.0)
    chooser = JevChooser(model=model, client=client)
    chooser.provider = "clef"
    return chooser


def make_chooser(name: str | None = None) -> Chooser:
    name = name or os.environ.get("LE_CHOOSER", "clef")  # beat the llm chooser on eval/tagging (docs/PLAN.md)
    if name == "llm":
        from .llm import LlmChooser

        return LlmChooser(
            base_url=os.environ.get("LE_LLM_BASE_URL", LLM_BASE_URL),
            model=os.environ.get("LE_LLM_MODEL", LLM_MODEL),
            api_key=os.environ.get("LE_LLM_API_KEY", ""),
            reasoning=os.environ.get("LE_LLM_REASONING", "none") or None,
        )
    if name == "jev":
        from .jev import JevChooser

        return JevChooser(model=os.environ.get("LE_JEV_MODEL") or None)
    if name == "clef":
        return make_clef()
    if name == "laya":
        from .laya import LayaChooser

        return LayaChooser(model=os.environ.get("LE_LAYA_MODEL") or None)
    raise ValueError(f"unknown LE_CHOOSER {name!r}")
