"""The one decision the tagger needs: a probability over labelled options.

Backends (Jev, a local LLM, a fake) implement ``Chooser``. Their output is
never trusted: ``normalize`` drops unknown options and bad numbers.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

NONE = "none"
LABEL = "label"  # built-in option: text naming a field (a form caption), not giving a value


def is_value(target: str) -> bool:
    """True for a real field tag; False for ``...#none`` and ``...#label``."""
    return not target.endswith(("#none", "#label"))


@dataclass
class Question:
    instructions: str
    options: dict[str, dict]  # option key -> criterion {"what", "examples", "not_for", ...}
    subject: str  # the text being asked about (also quoted in instructions)
    node: str  # schema path whose children are the options ("" = root)
    labels: dict[str, str]  # option key -> schema path it stands for; for fakes and debugging


@dataclass
class YesNo:
    instructions: str
    options: dict[str, str]  # {"true": what yes means, "false": what no means}
    subject: str


class Chooser(Protocol):
    def describe(self) -> dict: ...
    def choose(self, state: dict, questions: dict[str, Question]) -> dict[str, dict[str, float]]:
        """Return a probability per option for each answered question key."""
        ...

    # Optional: without it no line is split or joined, and nothing is found inside prose.
    def yes_no(self, state: dict, questions: dict[str, YesNo]) -> dict[str, float]:
        """Return P(yes) for each answered question key."""
        ...


def normalize(raw: object, options: dict) -> dict[str, float] | None:
    if not isinstance(raw, dict):
        return None
    probs = {
        k: float(v) for k, v in raw.items()
        if k in options and isinstance(v, (int, float)) and not isinstance(v, bool)
        and math.isfinite(v) and v >= 0
    }
    total = sum(probs.values())
    if total <= 0:
        return None
    return {k: probs.get(k, 0.0) / total for k in options}


class FakeChooser:
    """Answers with ``rule(question)``: a label (0.9 on it, the rest spread), a
    {label: probability} dict, or None (0.9 on ``none``)."""

    def __init__(self, rule: Callable[[Question], str | dict[str, float] | None],
                 gate: Callable[[YesNo], float] | None = None):
        self.rule = rule
        self.calls: list[tuple[dict, dict[str, Question]]] = []
        self.gate_calls: list[dict[str, YesNo]] = []
        # Without a gate rule the fake has no yes/no ability at all, like a chooser that lacks it.
        self.yes_no = self._yes_no(gate) if gate else None

    def _yes_no(self, gate: Callable[[YesNo], float]):
        def yes_no(state: dict, questions: dict[str, YesNo]) -> dict[str, float]:
            self.gate_calls.append(questions)
            return {k: gate(q) for k, q in questions.items()}
        return yes_no

    def describe(self) -> dict:
        return {"provider": "fake"}

    def choose(self, state: dict, questions: dict[str, Question]) -> dict[str, dict[str, float]]:
        self.calls.append((state, questions))
        out = {}
        for key, q in questions.items():
            wanted = self.rule(q)
            if isinstance(wanted, dict):
                given = {k: wanted.get(label, 0.0) for k, label in q.labels.items()}
                rest = max(0.0, 1 - sum(given.values())) / len(q.options)
                out[key] = {k: p + rest for k, p in given.items()}
                continue
            pick = next((k for k, label in q.labels.items() if label == wanted), NONE)
            rest = 0.1 / (len(q.options) - 1)
            out[key] = {k: 0.9 if k == pick else rest for k in q.options}
        return out
