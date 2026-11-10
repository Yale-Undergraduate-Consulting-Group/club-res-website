"""Serialize HTML crawls to the RAM this instance can hold.

Tavily, Apify, and Bedrock keep page HTML off this box — those stay ungated.
Domain crawl + BeautifulSoup in one job is fine on 2 GB; two at once is not.
Extra HTML crawls queue. Override with DISCOVERY_JOBS (1–4).
"""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import AsyncIterator

#: Set by a discovery run to a one-item list; every gate wait made on its behalf
#: adds its seconds, so the run can report how long it queued for a slot.
gate_wait: ContextVar[list[float] | None] = ContextVar("discovery_gate_wait", default=None)


def _cgroup_or_host_ram_bytes() -> int | None:
    for p in (
        Path("/sys/fs/cgroup/memory.max"),
        Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"),
    ):
        try:
            raw = p.read_text().strip()
        except OSError:
            continue
        if raw in ("max", ""):
            continue
        if raw.isdigit():
            n = int(raw)
            if 0 < n < 1 << 50:
                return n
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        return None
    return None


def discovery_job_slots() -> int:
    env = (os.getenv("DISCOVERY_JOBS") or "").strip()
    if env.isdigit():
        return max(1, min(int(env), 4))
    mem = _cgroup_or_host_ram_bytes()
    if mem is None:
        return 1
    gib = mem / (1024**3)
    if gib < 3:
        return 1
    if gib < 7:
        return 2
    return 3


_sem: asyncio.Semaphore | None = None
_sem_n: int | None = None


def _semaphore() -> asyncio.Semaphore:
    global _sem, _sem_n
    n = discovery_job_slots()
    if _sem is None or _sem_n != n:
        _sem = asyncio.Semaphore(n)
        _sem_n = n
    return _sem


@asynccontextmanager
async def discovery_job() -> AsyncIterator[bool]:
    """Yield True if this caller waited for another Find on this process."""
    sem = _semaphore()
    t0 = asyncio.get_running_loop().time()
    await sem.acquire()
    waited = asyncio.get_running_loop().time() - t0
    acc = gate_wait.get()
    if acc is not None:
        acc[0] += waited
    queued = waited > 0.05
    try:
        yield queued
    finally:
        sem.release()
