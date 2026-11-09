"""Keep register reads from starving the rest of the app.

A register summary or search scans the whole company register (about a
second each on the prod server), and the app runs as one process on two
cores. Load-tested 2026-11-09: ten members on Find contacts saturated it, and
sign-in, Home and writes then timed out behind the queue. So the routes:

- share one summary for SUMMARY_TTL seconds; it only changes on ingest;
- share identical searches for BROWSE_TTL seconds (public register data, not
  per member; "claimed by" is added fresh to every response);
- run at most one uncached scan at a time, leaving a core for everything
  else, and skip a scan whose browser has already gone (typing cancels the
  previous search, but the server would otherwise still compute it).

The service functions stay uncached; only the HTTP routes go through here.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

from fastapi import HTTPException

SUMMARY_TTL = 600.0
BROWSE_TTL = 120.0
BROWSE_ENTRIES = 256

_gate = asyncio.Semaphore(1)
_summary: tuple[float, dict[str, Any]] | None = None
_summary_lock = asyncio.Lock()
_browse: dict[tuple, tuple[float, dict[str, Any]]] = {}


async def _scan(run: Callable[[], Awaitable[Any]], gone: Callable[[], Awaitable[bool]] | None) -> Any:
    async with _gate:
        if gone is not None and await gone():
            # 499: the client closed the request; nobody reads this response.
            raise HTTPException(499, "Request cancelled")
        return await run()


async def summary(gone: Callable[[], Awaitable[bool]] | None = None) -> dict[str, Any]:
    global _summary
    if _summary and time.monotonic() - _summary[0] < SUMMARY_TTL:
        return _summary[1]
    async with _summary_lock:  # concurrent first callers share one scan
        if _summary and time.monotonic() - _summary[0] < SUMMARY_TTL:
            return _summary[1]
        from app.services.company_register import register_summary

        value = await _scan(register_summary, gone)
        _summary = (time.monotonic(), value)
        return value


async def browse(params: dict[str, Any], gone: Callable[[], Awaitable[bool]] | None = None) -> dict[str, Any]:
    from app.services.company_claims import claims_for, company_key
    from app.services.company_register import browse_register

    key = tuple(sorted(params.items()))

    def fresh() -> dict[str, Any] | None:
        hit = _browse.get(key)
        return hit[1] if hit and time.monotonic() - hit[0] < BROWSE_TTL else None

    async def compute() -> dict[str, Any]:
        # Identical searches queue at the gate; the first one fills the cache
        # for the rest.
        cached = fresh()
        if cached is not None:
            return cached
        result = await browse_register(**params)
        if len(_browse) >= BROWSE_ENTRIES:
            now = time.monotonic()
            for stale in [k for k, (at, _) in _browse.items() if now - at >= BROWSE_TTL] or [next(iter(_browse))]:
                _browse.pop(stale, None)
        _browse[key] = (time.monotonic(), result)
        return result

    page = fresh() or await _scan(compute, gone)
    # Claims change minute to minute; the cached page must not freeze them.
    items = [dict(item) for item in page["items"]]
    claims = await claims_for([{"company_name": i["display_name"], "company_domain": i["company_domain"]} for i in items])
    for item in items:
        held = claims.get(company_key(item["display_name"], item["company_domain"]))
        item["claimed_by"] = held["member"] if held else None
    return {**page, "items": items}
