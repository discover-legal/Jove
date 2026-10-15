"""Chooser backed by Laya (convaiinnovations/laya), a local open-weights decision model.

Laya takes the same typed questions as Jev (choice / noul) but reads at most
512 tokens per question, so it never sees the whole page. Each question's own
text (the quoted line, its neighbours, the labels so far) becomes a small
per-question state, and only the question sentence stays in the instructions:
measured on hand-made caption lines, evidence in the instructions put field
options at 0.02-0.06 where evidence in the state put them at 0.9.

Runs on CPU or GPU; ~20 ms per warm call on an RTX 5060.
"""

import os
import re
import threading

from .choose import Question, YesNo, normalize
from .tagger import UNTRUSTED

# torch 2.14 routes some eager ops through Triton kernels it JIT-compiles, which
# needs the Python headers (python3.x-dev). Laya runs fine on the plain kernels.
os.environ.setdefault("TORCH_DISABLE_NATIVE_JIT", "1")

MAX_LEN = 512  # the English checkpoint's limit
HEAD_MAX_LEN = 320  # question + options; the rest is left for the state

_QUESTION = re.compile(r"[^.?]*\?")


class LayaChooser:
    def __init__(self, model: str | None = None, device: str | None = None, router=None):
        if router is None:
            from laya import Router

            router = Router(models={"english": model} if model else None, device=device, preload=True)
        self._router = router
        self.model = model
        self._lock = threading.Lock()  # one model; the taggers ask from several threads
        self.requests = 0  # usage counters, same as JevChooser
        self.input_tokens = 0
        self.truncated = 0

    def describe(self) -> dict:
        return {"provider": "laya", "model": self.model or "convaiinnovations/laya"}

    def choose(self, state: dict, questions: dict[str, Question]) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for key, q in questions.items():
            ask, evidence = split_question(q.instructions)
            answer = self._predict(state, evidence, {"type": "choice", "instructions": ask,
                                                     "criteria": {k: render(c) for k, c in q.options.items()}})
            if answer is not None and answer.get("type") == "choice":
                probs = normalize(answer.get("probabilities"), q.options)
                if probs is not None:
                    out[key] = probs
        return out

    def yes_no(self, state: dict, questions: dict[str, YesNo]) -> dict[str, float]:
        out: dict[str, float] = {}
        for key, q in questions.items():
            ask, evidence = split_question(q.instructions)
            answer = self._predict(state, evidence, {"type": "noul", "instructions": ask, "criteria": q.options})
            if answer is not None and answer.get("type") == "noul":
                p = answer.get("noul")
                if isinstance(p, (int, float)) and 0.0 <= p <= 1.0:
                    out[key] = float(p)
        return out

    def _predict(self, state: dict, evidence: str, question: dict) -> dict | None:
        small = {"context": state["context"], "text": evidence} if state.get("context") else {"text": evidence}
        with self._lock:
            response = self._router.predict(small, {"q": question}, max_len=MAX_LEN, head_max_len=HEAD_MAX_LEN)
        usage = response.get("usage") or {}
        self.requests += 1
        self.input_tokens += usage.get("input_tokens") or 0
        self.truncated += bool(usage.get("truncated"))
        return (response.get("answers") or {}).get("q")


def split_question(instructions: str) -> tuple[str, str]:
    """(the question sentences, everything else) from one of our instructions."""
    text = instructions.removeprefix(UNTRUSTED)
    asks = [m.group(0).strip() for m in _QUESTION.finditer(text) if "'" not in m.group(0) and '"' not in m.group(0)]
    if not asks:
        return text, ""
    rest = text
    for a in asks:
        rest = rest.replace(a, "")
    return " ".join(asks), " ".join(rest.split())


def render(criterion) -> str:
    """The option's name and the start of its description. Laya keeps 48 tokens
    per option and splits a shared budget between them; on ONCA 250 lines, this
    beat the full criterion with examples and not_for (2 of 3 better, 1 tied)."""
    if not isinstance(criterion, dict):
        return str(criterion)
    name, _, description = criterion.get("what", "").partition(":")
    return f"{name}: {description.strip()[:60]}" if description else name
