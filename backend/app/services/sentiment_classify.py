"""Sentiment scoring for the Market sentiment app, through app.services.llm.

Texts go to the model in batches (one call per BATCH_SIZE texts), on the
ranking model, charged to the member who started the run
(``purpose='sentiment'``). A text the model does not score is asked for again;
if it still has no score the run fails: nothing is guessed.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Awaitable, Callable

from fastapi import HTTPException

from app.services.llm import complete_text, rank_model_id

BATCH_SIZE = 30
MAX_TEXT_CHARS = 500
ATTEMPTS = 3

SYSTEM = (
    "You are a sentiment classifier for market research. You receive a numbered list of texts. "
    "For each text give a sentiment score from -1.0 (very negative) to 1.0 (very positive); "
    "0 is neutral. Judge the attitude the writer expresses, not the topic. "
    'Answer with JSON only, in this shape: {"items": [{"i": 1, "score": 0.4}, ...]} '
    "with one entry per text, using the text's number as i."
)

Progress = Callable[[int, int], Awaitable[None]]


class ClassificationError(RuntimeError):
    """The model could not score every text."""


def _prompt(batch: list[tuple[int, str]]) -> str:
    return "\n".join(f"{n}. " + " ".join(text[:MAX_TEXT_CHARS].split()) for n, text in batch)


def _parse(raw: str) -> dict[int, float]:
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        return {}
    try:
        data = json.loads(match.group())
    except json.JSONDecodeError:
        return {}
    scores: dict[int, float] = {}
    items = data.get("items") if isinstance(data, dict) else None
    for item in items if isinstance(items, list) else []:
        try:
            scores[int(item["i"])] = max(-1.0, min(1.0, float(item["score"])))
        except (KeyError, TypeError, ValueError):
            continue
    return scores


def _call(batch: list[tuple[int, str]], user_id: int) -> dict[int, float]:
    raw = complete_text(_prompt(batch), rank_model_id(), SYSTEM, user_id=user_id,
                        purpose="sentiment", max_tokens=min(4096, 40 + 24 * len(batch)))
    wanted = {n for n, _ in batch}
    return {n: s for n, s in _parse(raw).items() if n in wanted}


async def classify(texts: list[str], user_id: int, progress: Progress | None = None) -> list[float]:
    """One score in [-1, 1] per text, in order."""
    scores: list[float | None] = [None] * len(texts)
    for start in range(0, len(texts), BATCH_SIZE):
        pending = [(n + 1, texts[start + n]) for n in range(min(BATCH_SIZE, len(texts) - start))]
        for _ in range(ATTEMPTS):
            try:
                got = await asyncio.to_thread(_call, pending, user_id)
            except HTTPException as exc:
                raise ClassificationError(str(exc.detail)) from None
            for n, score in got.items():
                scores[start + n - 1] = score
            pending = [(n, t) for n, t in pending if scores[start + n - 1] is None]
            if not pending:
                break
        if pending:
            raise ClassificationError(f"The model returned no score for {len(pending)} of {len(texts)} texts")
        if progress:
            await progress(min(start + BATCH_SIZE, len(texts)), len(texts))
    return [s for s in scores if s is not None]
