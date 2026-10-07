"""
Generic multi-agent batch runner — N concurrent workers each processing a batch slice.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")

BatchFn = Callable[[list[T]], Awaitable[R]]
MergeFn = Callable[[list[R]], R]
ProgressFn = Callable[[int, int], Awaitable[None] | None]


async def run_batch_agents(
    items: list[T],
    *,
    batch_size: int,
    agents: int,
    worker: BatchFn[T, R],
    merge: MergeFn[R, R] | None = None,
    on_progress: ProgressFn | None = None,
) -> R | list[R]:
    """Split items into batches; up to `agents` batches run concurrently."""
    if not items:
        return [] if merge is None else merge([])

    batches = [items[i : i + batch_size] for i in range(0, len(items), batch_size)]
    sem = asyncio.Semaphore(max(1, agents))
    done = 0
    lock = asyncio.Lock()

    async def _one(batch: list[T]) -> R:
        nonlocal done
        async with sem:
            result = await worker(batch)
            if on_progress:
                async with lock:
                    done += 1
                    current = done
                maybe = on_progress(current, len(batches))
                if maybe is not None:
                    await maybe
            return result

    results = await asyncio.gather(*[_one(b) for b in batches])
    if merge is not None:
        return merge(results)
    return results
