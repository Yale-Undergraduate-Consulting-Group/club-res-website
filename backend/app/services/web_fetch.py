"""Web fetch/search adapter, layered: TinyFish first, self-hosted Firecrawl
second, a real Tavily key (if one is ever configured) as a last resort at
call sites.

TinyFish (https://www.tinyfish.ai)'s Search and Fetch capabilities are
reached through Monid (https://monid.ai), a hosted API broker - MONID_API_KEY
authenticates against Monid's own API (https://api.monid.ai), not TinyFish
directly. A single `POST /v1/run` call with {"provider": "tinyfish",
"endpoint": "/search"|"/fetch", "input": {...}} runs synchronously and
returns {"status": "COMPLETED", "output": {...}} - confirmed live against
TinyFish's published free-tier pricing (billedUnits: 0 for both endpoints).
TinyFish itself is a browser-rendered search/fetch service, engineered
around the exact failure mode self-hosted Firecrawl hit in production here:
its own Monid catalog entry reports "healthy" status where Firecrawl's
/v1/search consistently returned zero results (bot-blocked upstream, see
docs/FIRECRAWL-SEARCH-EXPANSION-PLAN.md section 1.6).

Firecrawl (per docs/FIRECRAWL-SEARCH-EXPANSION-PLAN.md) runs on an OCI VM,
reachable from this app once the EC2 host joined the same Tailscale mesh
(plan section 1.3, completed). It stays wired as the second layer: if
TinyFish/Monid is ever unconfigured, rate-limited, or has an outage,
Firecrawl is a working fallback rather than an immediate drop to nothing.

The club does not pay for Tavily/Apify/Verifalia. A caller checks
TAVILY_API_KEY only as an optional bonus path if one is ever configured
later - it is never required.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import deque
from contextvars import ContextVar
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.database import get_db
from app.services.discovery_gate import discovery_job
from app.services.generation_policy import reserve_firecrawl_call, reserve_tinyfish_call

logger = logging.getLogger("yucg.firecrawl")
_TIMEOUT_S = 30.0
_MONID_API_BASE = "https://api.monid.ai"
_MONID_SEARCH_TIMEOUT_S = 20.0
_MONID_FETCH_TIMEOUT_S = 45.0
_MONID_POLL_ATTEMPTS = 5
_MONID_POLL_INTERVAL_S = 2.0
# A search that is not finished on the first answer is usually finished a
# moment later; polling it every 2s added up to 2s to each such query of a
# run. Same ~10s ceiling, checked four times as often.
_MONID_SEARCH_POLL_ATTEMPTS = 20
_MONID_SEARCH_POLL_INTERVAL_S = 0.5


@dataclass(frozen=True)
class FetchedPage:
    url: str
    content: str
    links: list[str]
    screenshot: str | None = None


def firecrawl_configured() -> bool:
    return bool((os.getenv("FIRECRAWL_URL") or "").strip())


def tinyfish_configured() -> bool:
    """TinyFish is reached through Monid - MONID_API_KEY is a Monid platform
    key (format monid_<stage>_<secret>), not a TinyFish-issued key."""
    return bool((os.getenv("MONID_API_KEY") or "").strip())


def web_search_configured() -> bool:
    """True when any hosted/self-hosted search backend is wired - the check
    every discovery-pipeline gate should use instead of naming one provider,
    so a gate does not need editing again the next time the backend layer
    order changes."""
    return tinyfish_configured() or firecrawl_configured()


# --- Search pacing, usage and cache --------------------------------------
# One process serves every member, Find run and the builder, so this module
# state is the club-wide view of Monid's published free-tier search limit.

_SEARCH_PACER_WINDOW_S = 60.0
#: Monotonic start times of the Monid searches inside the current window.
_search_slots: deque[float] = deque()
_EMPTY_SEARCH_CACHE_TTL_S = 6 * 3600.0
_SEARCH_USAGE_KEEP_S = 2 * 86400.0
_SEARCH_PRUNE_BATCH = 500


def _searches_per_window() -> int:
    try:
        return max(1, int(os.getenv("MONID_SEARCHES_PER_MINUTE", "30")))
    except ValueError:
        return 30


def _search_cache_ttl_s() -> float:
    """WEB_SEARCH_CACHE_TTL_HOURS=0 turns the cache off."""
    try:
        return max(0.0, float(os.getenv("WEB_SEARCH_CACHE_TTL_HOURS", "168"))) * 3600
    except ValueError:
        return 168 * 3600.0


async def _await_search_slot() -> None:
    """Wait until one more Monid search fits the sliding window, then take
    the slot. Waiting, not failing: a burst of Find queries queues for a few
    seconds instead of drawing 429s that read as "search is down". Check and
    take happen with no await between them, so one event loop needs no lock.
    ponytail: in-memory window - a restart may allow one extra burst inside
    the first minute; hour/day budgets come from web_search_usage instead."""
    while True:
        now = time.monotonic()
        while _search_slots and now - _search_slots[0] >= _SEARCH_PACER_WINDOW_S:
            _search_slots.popleft()
        if len(_search_slots) < _searches_per_window():
            _search_slots.append(now)
            return
        await asyncio.sleep(_search_slots[0] + _SEARCH_PACER_WINDOW_S - now)


async def _record_search_usage() -> None:
    """One row per real upstream search call. Bookkeeping never fails a
    search: without the table (no init_db) the call is simply not counted."""
    now = time.time()
    try:
        db = await get_db()
        try:
            await db.execute("INSERT INTO web_search_usage (at) VALUES (?)", (now,))
            await db.execute(
                "DELETE FROM web_search_usage WHERE id IN "
                "(SELECT id FROM web_search_usage WHERE at < ? LIMIT ?)",
                (now - _SEARCH_USAGE_KEEP_S, _SEARCH_PRUNE_BATCH),
            )
            await db.commit()
        finally:
            await db.close()
    except Exception as exc:  # best-effort bookkeeping
        logger.warning("web_search usage not recorded: %s: %s", type(exc).__name__, exc)


async def search_usage_last_hour(seconds: float = 3600) -> int:
    """Real upstream searches (Monid pages and Firecrawl calls, cache hits
    excluded) in the last ``seconds``; rows are kept two days, so a 24h
    window works too. Raises if the usage table is unreadable - a budget
    check must not read an outage as an unused budget."""
    db = await get_db()
    try:
        cur = await db.execute("SELECT COUNT(*) FROM web_search_usage WHERE at >= ?",
                               (time.time() - seconds,))
        row = await cur.fetchone()
    finally:
        await db.close()
    return int(row[0]) if row else 0


def _search_cache_key(query: str) -> str:
    """Whitespace and case collapsed; operators (site:, OR, quotes) kept."""
    return " ".join(query.split()).lower()


async def _search_cache_get(key: str, page: int, max_results: int) -> list[dict[str, Any]] | None:
    try:
        db = await get_db()
        try:
            cur = await db.execute(
                "SELECT results_json FROM web_search_cache "
                "WHERE query_key = ? AND page = ? AND max_results = ? AND expires_at > ?",
                (key, page, max_results, time.time()),
            )
            row = await cur.fetchone()
        finally:
            await db.close()
        return json.loads(row[0]) if row else None
    except Exception as exc:  # a cache miss, never a failed search
        logger.warning("web_search cache read skipped: %s: %s", type(exc).__name__, exc)
        return None


async def _search_cache_put(key: str, page: int, max_results: int,
                            results: list[dict[str, Any]]) -> None:
    """Only successful answers reach here. An empty one is kept 6h: "nothing
    found" is worth re-asking sooner than a list of hits."""
    ttl = _search_cache_ttl_s()
    if not results:
        ttl = min(ttl, _EMPTY_SEARCH_CACHE_TTL_S)
    now = time.time()
    try:
        db = await get_db()
        try:
            await db.execute(
                "DELETE FROM web_search_cache WHERE id IN "
                "(SELECT id FROM web_search_cache WHERE expires_at <= ? LIMIT ?)",
                (now, _SEARCH_PRUNE_BATCH),
            )
            await db.execute(
                """INSERT INTO web_search_cache (query_key, page, max_results, results_json, expires_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(query_key, page, max_results) DO UPDATE
                   SET results_json = excluded.results_json, expires_at = excluded.expires_at""",
                (key, page, max_results, json.dumps(results), now + ttl),
            )
            await db.commit()
        finally:
            await db.close()
    except Exception as exc:  # the search itself already succeeded
        logger.warning("web_search cache write skipped: %s: %s", type(exc).__name__, exc)


# --- TinyFish, via Monid ------------------------------------------------


async def _monid_run(client: httpx.AsyncClient, api_key: str, endpoint: str,
                      *, query_params: dict[str, Any] | None = None,
                      body: dict[str, Any] | None = None,
                      poll_attempts: int, poll_interval_s: float) -> dict[str, Any] | None:
    """POST /v1/run against Monid's TinyFish provider and, on the rare
    non-terminal response, poll /v1/runs/{id} until a terminal status.
    Returns the run's `output` dict on COMPLETED, or None on any failure
    (HTTP error, non-COMPLETED terminal status, network error, malformed
    body) - callers fall back to Firecrawl."""
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload: dict[str, Any] = {"provider": "tinyfish", "endpoint": endpoint, "input": {}}
    if query_params:
        payload["input"]["queryParams"] = query_params
    if body:
        payload["input"]["body"] = body

    try:
        resp = await client.post(f"{_MONID_API_BASE}/v1/run", json=payload, headers=headers)
        data = resp.json()
        if resp.status_code >= 400:
            logger.warning("monid tinyfish%s HTTP %s: %s", endpoint, resp.status_code, str(data)[:300])
            return None
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("monid tinyfish%s request failed: %s: %s", endpoint, type(exc).__name__, exc)
        return None

    run_id = data.get("runId") if isinstance(data, dict) else None
    for _ in range(poll_attempts):
        status = data.get("status") if isinstance(data, dict) else None
        if status == "COMPLETED":
            output = data.get("output")
            return output if isinstance(output, dict) else None
        if status in {"FAILED", "BLOCKED", "STOPPED", "TIMED_OUT"}:
            logger.warning("monid tinyfish%s run %s ended %s: %r", endpoint, run_id, status, data.get("error"))
            return None
        if not run_id:
            logger.warning("monid tinyfish%s malformed response: %r", endpoint, data)
            return None
        await asyncio.sleep(poll_interval_s)
        try:
            resp = await client.get(f"{_MONID_API_BASE}/v1/runs/{run_id}", headers=headers)
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("monid tinyfish%s poll failed for run %s: %s: %s", endpoint, run_id, type(exc).__name__, exc)
            return None
    logger.warning("monid tinyfish%s run %s never reached a terminal status", endpoint, run_id)
    return None


async def _tinyfish_fetch_page(url: str, *, user_id: int | None = None) -> FetchedPage | None:
    """Returns None on any failure (auth, rate limit, network, no content) -
    the caller falls back to Firecrawl. Never raises except the member
    quota HTTPException, matching every other paid/metered-call gate."""
    api_key = (os.getenv("MONID_API_KEY") or "").strip()
    if not api_key or not url:
        return None
    if user_id is not None:
        await reserve_tinyfish_call(user_id)

    started = time.monotonic()
    async with httpx.AsyncClient(timeout=_MONID_FETCH_TIMEOUT_S) as client:
        output = await _monid_run(
            client, api_key, "/fetch", body={"urls": [url], "links": True},
            poll_attempts=_MONID_POLL_ATTEMPTS, poll_interval_s=_MONID_POLL_INTERVAL_S,
        )

    if output is None:
        return None
    results = output.get("results")
    if not isinstance(results, list) or not results:
        logger.info("tinyfish fetch_page no result for %s in %.2fs (errors=%r)",
                     url, time.monotonic() - started, output.get("errors"))
        return None
    doc = results[0]
    if not isinstance(doc, dict):
        return None
    content = str(doc.get("text") or "")
    if not content:
        logger.info("tinyfish fetch_page empty content for %s in %.2fs", url, time.monotonic() - started)
        return None
    links = [str(link) for link in (doc.get("links") or []) if link]
    logger.info("tinyfish fetch_page ok for %s in %.2fs: %d chars, %d links",
                 url, time.monotonic() - started, len(content), len(links))
    return FetchedPage(url=str(doc.get("final_url") or doc.get("url") or url), content=content, links=links)


_SITE_OPERATOR_RE = re.compile(r"(-)?\bsite:([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})(?:/\S*)?", re.I)


def _extract_site_operators(query: str) -> tuple[str, str, str]:
    """Google-style site:/-site: operators are the established way every
    caller in this codebase restricts a search to a domain (originally
    written for Tavily/Firecrawl, which honour them as literal query text).
    TinyFish's own docs say it "still honours" the operator, but live
    testing showed it is actually ignored - a `site:linkedin.com/in` query
    returns generic leadership-training pages, not LinkedIn profiles, while
    the documented `include_domains` param returns real LinkedIn profiles
    for the identical intent. Translate rather than requiring every caller
    to be rewritten for one backend's quirk."""
    include_domains: list[str] = []
    exclude_domains: list[str] = []

    def replace(match: "re.Match[str]") -> str:
        domain = match.group(2).lower()
        (exclude_domains if match.group(1) else include_domains).append(domain)
        return " "

    cleaned = _SITE_OPERATOR_RE.sub(replace, query)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned, ",".join(dict.fromkeys(include_domains)), ",".join(dict.fromkeys(exclude_domains))


_TINYFISH_SEARCH_PAGE_SIZE = 8  # observed results per page across every live call this session


async def _tinyfish_web_search(query: str, max_results: int, *, user_id: int | None = None,
                               start_page: int = 0) -> list[dict[str, Any]] | None:
    """Returns None on failure (caller falls back to Firecrawl) and a list
    (possibly empty) on success - a real empty result is trusted, not
    treated as a failure to fall back from. TinyFish's search API has no
    result-count parameter, only pagination (page: 0-10, ~8 results/page
    observed) - fetch additional pages when a caller asks for more than one
    page holds, deduping by URL, rather than silently capping every search
    at one page's worth of results regardless of what was requested."""
    api_key = (os.getenv("MONID_API_KEY") or "").strip()
    if not api_key or not query:
        return None

    cleaned_query, include_domains, exclude_domains = _extract_site_operators(query)
    if not cleaned_query:
        return None
    base_params: dict[str, Any] = {"query": cleaned_query}
    if include_domains:
        base_params["include_domains"] = include_domains
    if exclude_domains:
        base_params["exclude_domains"] = exclude_domains

    start_page = max(0, min(int(start_page or 0), 10))
    max_pages = min(11 - start_page, max(1, -(-max(1, max_results) // _TINYFISH_SEARCH_PAGE_SIZE)))
    started = time.monotonic()
    out: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    async with httpx.AsyncClient(timeout=_MONID_SEARCH_TIMEOUT_S) as client:
        for page in range(start_page, start_page + max_pages):
            if user_id is not None:
                await reserve_tinyfish_call(user_id)
            params = dict(base_params)
            if page:
                params["page"] = page
            # The request goes out right after its slot; counting comes after,
            # so a slow usage write never bunches requests past the window.
            await _await_search_slot()
            output = await _monid_run(
                client, api_key, "/search", query_params=params,
                poll_attempts=_MONID_SEARCH_POLL_ATTEMPTS, poll_interval_s=_MONID_SEARCH_POLL_INTERVAL_S,
            )
            await _record_search_usage()
            if output is None:
                if page == start_page:
                    return None
                break
            results = output.get("results")
            if not isinstance(results, list):
                if page == start_page:
                    logger.warning("tinyfish web_search malformed response for %r: %r", query, output)
                    return None
                break
            if not results:
                break
            added = False
            for item in results:
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url") or "")
                if url and url in seen_urls:
                    continue
                if url:
                    seen_urls.add(url)
                added = True
                out.append({
                    "title": str(item.get("title") or ""),
                    "url": url,
                    "content": str(item.get("snippet") or "")[:1500],
                })
                if len(out) >= max_results:
                    break
            if len(out) >= max_results or not added:
                break
    logger.info("tinyfish web_search %d results for %r in %.2fs (%d page(s))",
                 len(out), query, time.monotonic() - started, page + 1 - start_page)
    return out



# --- Firecrawl ---------------------------------------------------------


async def _firecrawl_fetch_page(url: str, *, user_id: int | None = None) -> FetchedPage | None:
    """Fetch one URL through Firecrawl. Returns None when unconfigured or on
    any failure — callers fall back to their existing fetch, they never block
    on this. Never raises for a missing/unreachable Firecrawl; only a member
    quota HTTPException propagates, matching every other paid-call gate.
    """
    base = (os.getenv("FIRECRAWL_URL") or "").strip().rstrip("/")
    if not base or not url:
        return None

    if user_id is not None:
        await reserve_firecrawl_call(user_id)

    api_key = (os.getenv("FIRECRAWL_API_KEY") or "").strip()
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    payload: dict[str, Any] = {"url": url, "formats": ["markdown", "links"]}

    # Same slot budget as an HTML crawl job: Firecrawl's browser render is
    # comparable RAM/CPU cost to this box doing its own BeautifulSoup parse.
    started = time.monotonic()
    async with discovery_job():
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
                resp = await client.post(f"{base}/v1/scrape", json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.warning("firecrawl fetch_page HTTP %s for %s in %.2fs: %s",
                            exc.response.status_code, url, time.monotonic() - started, exc.response.text[:300])
            return None
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("firecrawl fetch_page failed for %s in %.2fs: %s: %s",
                            url, time.monotonic() - started, type(exc).__name__, exc)
            return None

    doc = data.get("data") if isinstance(data, dict) else None
    if not isinstance(doc, dict):
        logger.warning("firecrawl fetch_page malformed response for %s: %r", url, data)
        return None
    content = str(doc.get("markdown") or doc.get("content") or "")
    if not content:
        warning = data.get("warning") if isinstance(data, dict) else None
        logger.info("firecrawl fetch_page empty content for %s in %.2fs (warning=%r)",
                     url, time.monotonic() - started, warning)
        return None
    links = [str(link) for link in (doc.get("links") or []) if link]
    screenshot = doc.get("screenshot")
    logger.info("firecrawl fetch_page ok for %s in %.2fs: %d chars, %d links",
                 url, time.monotonic() - started, len(content), len(links))
    return FetchedPage(url=url, content=content, links=links, screenshot=screenshot)


async def _firecrawl_web_search(query: str, max_results: int, *, user_id: int | None = None) -> list[dict[str, Any]]:
    """Web search through Firecrawl. Returns [] when unconfigured or on any
    failure — matches the existing Tavily helpers' shape exactly
    ([{"title", "url", "content"}]) so callers need no changes beyond
    swapping which function they call.
    """
    base = (os.getenv("FIRECRAWL_URL") or "").strip().rstrip("/")
    if not base or not query:
        return []

    if user_id is not None:
        await reserve_firecrawl_call(user_id)

    api_key = (os.getenv("FIRECRAWL_API_KEY") or "").strip()
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    payload: dict[str, Any] = {"query": query, "limit": max(1, min(20, max_results))}

    await _record_search_usage()
    started = time.monotonic()
    async with discovery_job():
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
                resp = await client.post(f"{base}/v1/search", json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.warning("firecrawl web_search HTTP %s for %r in %.2fs: %s",
                            exc.response.status_code, query, time.monotonic() - started, exc.response.text[:300])
            _last_search_failure.set(f"Firecrawl HTTP {exc.response.status_code}")
            return []
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("firecrawl web_search failed for %r in %.2fs: %s: %s",
                            query, time.monotonic() - started, type(exc).__name__, exc)
            _last_search_failure.set(f"Firecrawl {type(exc).__name__}")
            return []

    results = data.get("data") if isinstance(data, dict) else None
    if not isinstance(results, list):
        logger.warning("firecrawl web_search malformed response for %r: %r", query, data)
        _last_search_failure.set("Firecrawl malformed response")
        return []
    out: list[dict[str, Any]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        out.append({
            "title": str(item.get("title") or ""),
            "url": str(item.get("url") or ""),
            "content": str(item.get("description") or item.get("content") or "")[:1500],
        })
    # Firecrawl returns 200/success even with zero hits, with a "warning"
    # field explaining why (typically its underlying search provider is
    # rate-limited or genuinely has nothing for the query) - log it, since
    # this is otherwise indistinguishable from "the code is broken" without
    # this exact detail.
    if not out:
        warning = data.get("warning") if isinstance(data, dict) else None
        logger.info("firecrawl web_search 0 results for %r in %.2fs (warning=%r)",
                     query, time.monotonic() - started, warning)
    else:
        logger.info("firecrawl web_search %d results for %r in %.2fs",
                     len(out), query, time.monotonic() - started)
    return out


# --- Public waterfall: TinyFish -> Firecrawl ---------------------------


async def fetch_page(url: str, *, user_id: int | None = None) -> FetchedPage | None:
    """Fetch one URL. Tries TinyFish first, then Firecrawl. Returns None
    when neither is configured or both fail - callers fall back to their
    existing fetch, they never block on this."""
    if tinyfish_configured():
        page = await _tinyfish_fetch_page(url, user_id=user_id)
        if page is not None:
            return page
    if firecrawl_configured():
        return await _firecrawl_fetch_page(url, user_id=user_id)
    return None


#: Why the last web_search in this task came back empty through failure
#: rather than finding nothing. Both providers swallow their errors and return
#: nothing, so a search run could not tell "no people here" from "search is
#: down" and told members large companies rarely publish emails. Per task, so
#: parallel searches never read each other's outcome.
_last_search_failure: ContextVar[str | None] = ContextVar("web_search_failure", default=None)


def last_search_failure() -> str | None:
    """The reason the most recent web_search in this task failed, or None."""
    return _last_search_failure.get()


async def web_search(query: str, max_results: int = 8, *, user_id: int | None = None,
                     page: int = 0) -> list[dict[str, Any]]:
    """Web search. Tries TinyFish first, then Firecrawl. Returns [] when
    neither is configured. A genuine empty result from TinyFish is trusted
    and returned as-is, not treated as a reason to fall back to Firecrawl.

    ``page`` > 0 asks for a later results page. Only TinyFish pages, so a later
    page never falls back to Firecrawl's first page of the same query.

    Answers are read through the shared web_search_cache first; a hit spends
    no search, no quota and no pacer slot. Failures are never cached."""
    _last_search_failure.set(None)
    key = _search_cache_key(query or "")
    use_cache = bool(key) and _search_cache_ttl_s() > 0
    if use_cache:
        cached = await _search_cache_get(key, page, max_results)
        if cached is not None:
            return cached
    reason = None
    if tinyfish_configured():
        results = await _tinyfish_web_search(query, max_results, user_id=user_id, start_page=page)
        if results is not None:
            if use_cache:
                await _search_cache_put(key, page, max_results, results)
            return results
        reason = "TinyFish gave no answer"
    if firecrawl_configured() and not page:
        results = await _firecrawl_web_search(query, max_results, user_id=user_id)
        if results or _last_search_failure.get() is None:
            if use_cache:
                await _search_cache_put(key, page, max_results, results)
            return results
        reason = _last_search_failure.get()
    if reason:
        _last_search_failure.set(reason)
    return []
