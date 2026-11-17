"""Bound the HTML crawls this instance runs at once to the RAM it can hold.

A crawl reads at most eleven pages (the homepage and ten team pages) of at
most contact_scraper.CRAWL_MAX_PAGE_BYTES each and parses them one at a time,
so one crawl stays under CRAWL_RAM_BYTES.
Six real big-company crawls at once (Google, JPMorgan Chase, Yale, Amazon,
Apple, Hyatt; 2026-11-16) raised peak RSS by 42 MB in total. Slots are what
the container leaves after APP_RAM_RESERVE_BYTES for the rest of the app,
1-4. Override with DISCOVERY_JOBS (1-4).

Web search, the model and remote page readers keep page HTML off this box and
are not gated here; Firecrawl has its own gate in web_fetch.
"""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable

#: Set by a discovery run to a one-item list; every gate wait made on its behalf
#: adds its seconds, so the run can report how long it queued for a slot.
gate_wait: ContextVar[list[float] | None] = ContextVar("discovery_gate_wait", default=None)

#: What one crawl may hold: its pages (contact_scraper caps each one) and the
#: parse tree of the page being read.
CRAWL_RAM_BYTES = 128 * 1024**2
#: What the app needs besides crawls: measured at 261 MiB on prod, doubled.
APP_RAM_RESERVE_BYTES = 512 * 1024**2
MAX_SLOTS = 4


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
        return max(1, min(int(env), MAX_SLOTS))
    mem = _cgroup_or_host_ram_bytes()
    if mem is None:
        return 1
    return max(1, min(MAX_SLOTS, (mem - APP_RAM_RESERVE_BYTES) // CRAWL_RAM_BYTES))


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
async def discovery_job(on_wait: Callable[[], Awaitable[None]] | None = None) -> AsyncIterator[None]:
    """Hold one crawl slot. ``on_wait`` is awaited first when every slot is
    taken, so a caller can say it is waiting while it waits rather than after."""
    sem = _semaphore()
    if on_wait is not None and sem.locked():
        await on_wait()
    t0 = asyncio.get_running_loop().time()
    await sem.acquire()
    acc = gate_wait.get()
    if acc is not None:
        acc[0] += asyncio.get_running_loop().time() - t0
    try:
        yield
    finally:
        sem.release()
