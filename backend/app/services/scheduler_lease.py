"""One process at a time runs the scheduled jobs.

A deploy starts the new container beside the old one, so for a short overlap
two processes share the database. Both start a scheduler, but a job body runs
only in the process holding this lease: mail sends, Gmail sync and queue
drains never run twice. The holder renews the lease; a process that stops
releases it, and a process that dies loses it when it expires.
"""
from __future__ import annotations

import asyncio
import functools
import logging
import time
import uuid
from typing import Any, Awaitable, Callable

from app.database import get_db

logger = logging.getLogger(__name__)

LEASE_NAME = "scheduler"
TTL_SECONDS = 45.0
RENEW_SECONDS = 15.0

HOLDER = uuid.uuid4().hex
_held = False


def is_leader() -> bool:
    return _held


async def _ensure(db) -> None:
    await db.execute(
        "CREATE TABLE IF NOT EXISTS process_leases (name TEXT PRIMARY KEY, holder TEXT NOT NULL, expires_at REAL NOT NULL)"
    )


async def try_acquire(holder: str = HOLDER, ttl: float = TTL_SECONDS) -> bool:
    """Take or renew the lease; True when ``holder`` holds it afterwards."""
    now = time.time()
    db = await get_db()
    try:
        await _ensure(db)
        await db.execute("BEGIN IMMEDIATE")
        await db.execute(
            """INSERT INTO process_leases(name, holder, expires_at) VALUES (?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET holder=excluded.holder, expires_at=excluded.expires_at
               WHERE process_leases.holder=excluded.holder OR process_leases.expires_at<=?""",
            (LEASE_NAME, holder, now + ttl, now),
        )
        row = await (await db.execute("SELECT holder FROM process_leases WHERE name=?", (LEASE_NAME,))).fetchone()
        await db.commit()
        return bool(row) and row[0] == holder
    finally:
        await db.close()


async def release(holder: str = HOLDER) -> None:
    db = await get_db()
    try:
        await _ensure(db)
        await db.execute("DELETE FROM process_leases WHERE name=? AND holder=?", (LEASE_NAME, holder))
        await db.commit()
    finally:
        await db.close()


async def keep_lease(stop: asyncio.Event) -> None:
    """Acquire and renew until ``stop`` is set, then release."""
    global _held
    try:
        while not stop.is_set():
            try:
                held = await try_acquire()
            except Exception:
                # Without a confirmed renewal this process must assume it lost the lease.
                logger.exception("scheduler lease renewal failed")
                held = False
            if held != _held:
                logger.info("scheduler lease %s", "acquired" if held else "lost")
            _held = held
            try:
                await asyncio.wait_for(stop.wait(), timeout=RENEW_SECONDS)
            except asyncio.TimeoutError:
                pass
    finally:
        was_held, _held = _held, False
        if was_held:
            await release()


def leader_only(job: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """Run the scheduled job only in the process holding the lease."""
    @functools.wraps(job)
    async def run(*args, **kwargs):
        if not _held:
            return None
        return await job(*args, **kwargs)
    return run
