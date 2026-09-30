"""Chooser backed by TypeSafe's Jev (typesafe-sdk; reads TYPESAFE_API_KEY).

Questions are packed into as few requests as Jev's limits allow: 32k tokens
for state plus the longest question, 64k for state plus every question.
Budgeted in UTF-8 bytes: measured on jev-1.13 with the reference schema, our
JSON runs ~2.5 bytes per token, so the budgets assume 2.0. If Jev still
answers max_tokens_exceeded, the batch is halved and retried.
"""

import json

from .choose import Question, normalize

MAX_STATE_QUESTION_BYTES = 64_000
MAX_REQUEST_BYTES = 128_000


class JevChooser:
    def __init__(self, model: str | None = None, client=None):
        if client is None:
            from typesafe_sdk import TypeSafeClient

            client = TypeSafeClient(model=model)
        self._client = client
        self.model = model

    def describe(self) -> dict:
        return {"provider": "jev", "model": self.model or "jev-latest"}

    def choose(self, state: dict, questions: dict[str, Question]) -> dict[str, dict[str, float]]:
        from typesafe_sdk import Choice

        out: dict[str, dict[str, float]] = {}
        pending = batches(state, questions)
        while pending:
            batch = pending.pop()
            try:
                response = self._client.system_one(
                    state=state,
                    questions={k: Choice(instructions=q.instructions, criteria=q.options) for k, q in batch.items()},
                    model=self.model,
                )
            except Exception as e:
                if "max_tokens_exceeded" in str(e) and len(batch) > 1:
                    items = list(batch.items())
                    pending += [dict(items[: len(items) // 2]), dict(items[len(items) // 2:])]
                    continue
                raise
            for key, q in batch.items():
                answer = response.answers.get(key)
                if answer is not None and answer.type == "choice":
                    probs = normalize(answer.probabilities, q.options)
                    if probs is not None:
                        out[key] = probs
        return out


def batches(state: dict, questions: dict[str, Question]) -> list[dict[str, Question]]:
    state_bytes = _size(state)
    out: list[dict[str, Question]] = []
    current: dict[str, Question] = {}
    total = longest = 0
    for key, q in questions.items():
        size = _size({"instructions": q.instructions, "criteria": q.options})
        if state_bytes + size > MAX_STATE_QUESTION_BYTES:
            raise ValueError(f"question {key} ({q.subject[:40]!r}) is too large for one Jev request")
        if current and (state_bytes + max(longest, size) > MAX_STATE_QUESTION_BYTES
                        or state_bytes + total + size > MAX_REQUEST_BYTES):
            out.append(current)
            current, total, longest = {}, 0, 0
        current[key] = q
        total += size
        longest = max(longest, size)
    if current:
        out.append(current)
    return out


def _size(value) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode())
