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


def make_chooser() -> Chooser:
    name = os.environ.get("LE_CHOOSER", "llm")
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
    raise ValueError(f"unknown LE_CHOOSER {name!r}")
