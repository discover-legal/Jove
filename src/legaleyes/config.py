"""Build providers from environment variables (see docs/PLAN.md)."""

import os
from pathlib import Path

from .choose import Chooser
from .ocr import Ocr


def data_dir() -> Path:
    return Path(os.environ.get("LE_DATA_DIR", "./data"))


def separation() -> float:
    return float(os.environ.get("LE_SEPARATION", "1.5"))


def make_ocr() -> Ocr:
    name = os.environ.get("LE_OCR", "rapidocr")
    if name == "rapidocr":
        from .ocr import RapidOcr

        model = os.environ.get("LE_OCR_REC_MODEL")
        return RapidOcr(Path(model) if model else None)
    raise ValueError(f"unknown LE_OCR {name!r}")


def make_chooser() -> Chooser:
    name = os.environ.get("LE_CHOOSER", "llm")
    if name == "llm":
        from .llm import LlmChooser

        return LlmChooser(
            base_url=os.environ.get("LE_LLM_BASE_URL", "http://localhost:11434/v1"),
            model=os.environ.get("LE_LLM_MODEL", "qwen2.5:7b"),
            api_key=os.environ.get("LE_LLM_API_KEY", ""),
        )
    if name == "jev":
        from .jev import JevChooser

        return JevChooser(model=os.environ.get("LE_JEV_MODEL") or None)
    raise ValueError(f"unknown LE_CHOOSER {name!r}")
