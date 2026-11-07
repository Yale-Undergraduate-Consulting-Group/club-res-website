"""Web fetch/search adapter, layered: TinyFish first, self-hosted Firecrawl
second, a real Tavily key (if one is ever configured) as a last resort at
call sites.

TinyFish (https://www.tinyfish.ai)'s Search and Fetch capabilities are
reached through Monid (https://monid.ai), a hosted API broker - MONID_API_KEY
authenticates against Monid's own API (https://api.monid.ai), not TinyFish
directly. A single `POST /v1/run` call with {"provider": "tinyfish",
"endpoint": "/free/search"|"/free/fetch", "input": {...}} runs synchronously
and returns {"status":"COMPLETED","output":{...}}.

Use the /free/ paths. Monid moved TinyFish's free tier there: /free/search and
/free/fetch cost $0 a call and work with an empty wallet (verified live
2026-11-07), while the old /search ($0.005 a call) and /fetch ($0.001 a result)
are now paid and answer 402 WALLET_INSUFFICIENT_FUNDS when the wallet is empty.
Both pairs take the same input.

TinyFish itself is a browser-rendered search/fetch service, engineered around
the exact failure mode self-hosted Firecrawl hit here: Firecrawl's `/v1/search`
returned zero results behind its upstream bot protection (see
docs/FIRECRAWL-SEARCH-EXPANSION-PLAN.md section 1.6).

Firecrawl stays wired as a second layer. A successful response, including a
successful search with zero matches, is authoritative; an HTTP or transport
failure remains distinguishable from "nothing found." This is fallback
wiring, not proof that Firecrawl's upstream search currently returns useful
results.

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
# TinyFish's free tier on Monid; see the module docstring before changing.
_MONID_SEARCH_ENDPOINT = "/free/search"
_MONID_FETCH_ENDPOINT = "/free/fetch"
_MONID_SEARCH_TIMEOUT_S = 20.0
_MONID_FETCH_TIMEOUT_S = 45.0
_MONID_POLL_ATTEMPTS = 5
_MONID_POLL_INTERVAL_S = 2.0
# A search that is not finished on the first answer is usually finished a
# moment later; polling it every 2s added up to 2s to each such query of a
# run. Same ~10s ceiling, checked four times as often.
_MONID_SEARCH_POLL_ATTEMPTS = 20
_MONID_SEARCH_POLL_INTERVAL_S = 0.5


# --- Search provider health and back-off ----------------------------------
# Monid answers 402 when its wallet is empty, 401/403 for a bad key and 429
# when rate limited. Every one of those used to come back as "no results", so
# a provider outage read as "nobody found". The kind of the last failure is
# kept here: callers read it (last_search_failure_kind) to tell the member the
# search could not run, the admin card reads monid_health(), and after a
# credit/auth/rate failure calls are suppressed for a short while so a burst
# of lanes does not hammer an API that will refuse every one of them.
# ponytail: one in-process record - the app is one writer process; a restart
# forgets it, and the first call afterwards learns the state again.

OUT_OF_CREDIT = "out_of_credit"
AUTH = "auth"
RATE_LIMITED = "rate_limited"
TIMEOUT = "timeout"
FAILING = "failing"
#: Worst first: which of several failures in one run the member is told about.
PROVIDER_KIND_RANK = (OUT_OF_CREDIT, AUTH, RATE_LIMITED, TIMEOUT, FAILING)
#: The one plain sentence a member sees. Never the provider's own error text.
PROVIDER_MEMBER_MESSAGE = {
    OUT_OF_CREDIT: "People search is paused: the search service is out of credit. A maintainer needs to top it up.",
    AUTH: "People search is paused: the search service refused our access key. A maintainer needs to fix it.",
    RATE_LIMITED: "People search is busy: the search service is limiting requests. Try again in a few minutes.",
    TIMEOUT: "People search is temporarily unavailable: the search service is not answering. Try again later.",
    FAILING: "People search is temporarily unavailable. Try again later.",
}
#: Seconds the provider is left alone after a failure that repeating cannot fix.
_BACKOFF_S = {OUT_OF_CREDIT: 300.0, AUTH: 300.0, RATE_LIMITED: 60.0}


def classify_provider_status(status: int) -> str:
    """The failure kind for an HTTP status a search provider answered with."""
    if status in (402, 432):
        return OUT_OF_CREDIT
    if status in (401, 403):
        return AUTH
    if status == 429:
        return RATE_LIMITED
    return FAILING


_monid: dict[str, Any] = {
    "state": "unknown",  # unknown (no call yet), working, or a failure kind
    "last_ok_at": None, "last_failure_at": None, "blocked_until": 0.0,
}


def _monid_note_ok() -> None:
    if _monid["state"] != "working":
        logger.info("monid search provider working again (was %s)", _monid["state"])
    _monid.update(state="working", last_ok_at=time.time(), blocked_until=0.0)


def _monid_note_failure(kind: str, detail: str) -> None:
    """Record a failed call. Logged once per change of state, so a burst of
    refused calls is one warning, not hundreds."""
    if _monid["state"] != kind:
        logger.warning("monid search provider %s: %s", kind, detail[:300])
    _monid.update(state=kind, last_failure_at=time.time())
    if kind in _BACKOFF_S:
        _monid["blocked_until"] = time.monotonic() + _BACKOFF_S[kind]


def monid_backoff_kind() -> str | None:
    """The failure kind while Monid is being left alone, else None. When the
    back-off ends the next call goes through and decides the state afresh."""
    if time.monotonic() < _monid["blocked_until"]:
        return str(_monid["state"])
    return None


def monid_health() -> dict[str, Any]:
    """What the last real Monid call showed, for the admin card. No call is
    made here: a probe would spend the wallet this is reporting on."""
    blocked_for = max(0.0, _monid["blocked_until"] - time.monotonic())
    state = _monid["state"] if tinyfish_configured() else "unconfigured"
    return {
        "configured": tinyfish_configured(),
        "state": state,
        "last_ok_at": _monid["last_ok_at"],
        "last_failure_at": _monid["last_failure_at"],
        "paused_for_s": round(blocked_for),
        "message": PROVIDER_MEMBER_MESSAGE.get(state),
    }


def reset_monid_health() -> None:
    """Forget everything learned about the provider (tests)."""
    _monid.update(state="unknown", last_ok_at=None, last_failure_at=None, blocked_until=0.0)


#: Why the last Monid call of this task failed; set by _monid_run, per task so
#: parallel searches never read each other's outcome.
_last_monid_failure_kind: ContextVar[str | None] = ContextVar("monid_failure_kind", default=None)


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
# applies one club-wide request pace before Monid.

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
    body) - callers fall back to Firecrawl. Why it failed is left in
    _last_monid_failure_kind and in monid_health()."""
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload: dict[str, Any] = {"provider": "tinyfish", "endpoint": endpoint, "input": {}}
    if query_params:
        payload["input"]["queryParams"] = query_params
    if body:
        payload["input"]["body"] = body

    def fail(kind: str, detail: str) -> None:
        _last_monid_failure_kind.set(kind)
        _monid_note_failure(kind, f"tinyfish{endpoint} {detail}")

    try:
        resp = await client.post(f"{_MONID_API_BASE}/v1/run", json=payload, headers=headers)
        if resp.status_code >= 400:
            fail(classify_provider_status(resp.status_code), f"HTTP {resp.status_code}: {resp.text[:200]}")
            return None
        data = resp.json()
    except httpx.TimeoutException as exc:
        fail(TIMEOUT, f"request timed out: {type(exc).__name__}")
        return None
    except (httpx.HTTPError, ValueError) as exc:
        fail(FAILING, f"request failed: {type(exc).__name__}: {exc}")
        return None

    run_id = data.get("runId") if isinstance(data, dict) else None
    for _ in range(poll_attempts):
        status = data.get("status") if isinstance(data, dict) else None
        if status == "COMPLETED":
            output = data.get("output")
            if isinstance(output, dict):
                _monid_note_ok()
                return output
            fail(FAILING, f"run {run_id} completed without output")
            return None
        if status in {"FAILED", "BLOCKED", "STOPPED", "TIMED_OUT"}:
            fail(TIMEOUT if status == "TIMED_OUT" else FAILING, f"run {run_id} ended {status}: {data.get('error')!r}")
            return None
        if not run_id:
            fail(FAILING, f"malformed response: {data!r}")
            return None
        await asyncio.sleep(poll_interval_s)
        try:
            resp = await client.get(f"{_MONID_API_BASE}/v1/runs/{run_id}", headers=headers)
            if resp.status_code >= 400:
                fail(classify_provider_status(resp.status_code), f"poll HTTP {resp.status_code} for run {run_id}")
                return None
            data = resp.json()
        except httpx.TimeoutException as exc:
            fail(TIMEOUT, f"poll timed out for run {run_id}: {type(exc).__name__}")
            return None
        except (httpx.HTTPError, ValueError) as exc:
            fail(FAILING, f"poll failed for run {run_id}: {type(exc).__name__}: {exc}")
            return None
    fail(TIMEOUT, f"run {run_id} never reached a terminal status")
    return None


async def _tinyfish_fetch_page(url: str, *, user_id: int | None = None) -> FetchedPage | None:
    """Returns None on any failure (auth, rate limit, network, no content) -
    the caller falls back to Firecrawl. Never raises except the member
    quota HTTPException, matching every other paid/metered-call gate."""
    api_key = (os.getenv("MONID_API_KEY") or "").strip()
    if not api_key or not url:
        return None
    if monid_backoff_kind():
        return None
    if user_id is not None:
        await reserve_tinyfish_call(user_id)

    started = time.monotonic()
    async with httpx.AsyncClient(timeout=_MONID_FETCH_TIMEOUT_S) as client:
        output = await _monid_run(
            client, api_key, _MONID_FETCH_ENDPOINT, body={"urls": [url], "links": True},
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
            blocked = monid_backoff_kind()
            if blocked:
                # No call: Monid refused the last one for a reason waiting
                # fixes, and every lane asking again would only be refused too.
                _last_monid_failure_kind.set(blocked)
                if page == start_page:
                    return None
                break
            if user_id is not None:
                await reserve_tinyfish_call(user_id)
            params = dict(base_params)
            if page:
                params["page"] = page
            # The request goes out right after its slot; counting comes after,
            # so a slow usage write never bunches requests past the window.
            await _await_search_slot()
            output = await _monid_run(
                client, api_key, _MONID_SEARCH_ENDPOINT, query_params=params,
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
            _set_search_failure(f"Firecrawl HTTP {exc.response.status_code}",
                                classify_provider_status(exc.response.status_code))
            return []
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("firecrawl web_search failed for %r in %.2fs: %s: %s",
                            query, time.monotonic() - started, type(exc).__name__, exc)
            _set_search_failure(f"Firecrawl {type(exc).__name__}",
                                TIMEOUT if isinstance(exc, httpx.TimeoutException) else FAILING)
            return []

    results = data.get("data") if isinstance(data, dict) else None
    if not isinstance(results, list):
        logger.warning("firecrawl web_search malformed response for %r: %r", query, data)
        _set_search_failure("Firecrawl malformed response", FAILING)
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
#: The same failure as one of PROVIDER_KIND_RANK.
_last_search_failure_kind: ContextVar[str | None] = ContextVar("web_search_failure_kind", default=None)


def last_search_failure() -> str | None:
    """The reason the most recent web_search in this task failed, or None."""
    return _last_search_failure.get()


def last_search_failure_kind() -> str | None:
    """The kind of that failure (out_of_credit, auth, rate_limited, timeout,
    failing), or None when the search answered - including with nothing."""
    return _last_search_failure_kind.get()


def _set_search_failure(reason: str, kind: str) -> None:
    _last_search_failure.set(reason)
    _last_search_failure_kind.set(kind)


async def web_search(query: str, max_results: int = 8, *, user_id: int | None = None,
                     page: int = 0) -> list[dict[str, Any]]:
    """Web search. Tries TinyFish first, then Firecrawl. Returns [] when
    neither is configured. A genuine empty result from TinyFish is trusted
    and returned as-is, not treated as a reason to fall back to Firecrawl.

    An empty list is only "found nothing" when last_search_failure() is None;
    otherwise the search could not run (see last_search_failure_kind()).

    ``page`` > 0 asks for a later results page. Only TinyFish pages, so a later
    page never falls back to Firecrawl's first page of the same query.

    Answers are read through the shared web_search_cache first; a hit spends
    no search, no quota and no pacer slot. Failures are never cached."""
    _last_search_failure.set(None)
    _last_search_failure_kind.set(None)
    _last_monid_failure_kind.set(None)
    key = _search_cache_key(query or "")
    use_cache = bool(key) and _search_cache_ttl_s() > 0
    if use_cache:
        cached = await _search_cache_get(key, page, max_results)
        if cached is not None:
            return cached
    reason = kind = None
    if tinyfish_configured():
        results = await _tinyfish_web_search(query, max_results, user_id=user_id, start_page=page)
        if results is not None:
            if use_cache:
                await _search_cache_put(key, page, max_results, results)
            return results
        kind = _last_monid_failure_kind.get() or FAILING
        reason = f"TinyFish {kind}"
    if firecrawl_configured() and not page:
        results = await _firecrawl_web_search(query, max_results, user_id=user_id)
        if _last_search_failure.get() is None:
            # The fallback answered. An empty answer is still a successful
            # search, so it clears the failed primary provider rather than
            # becoming "search unavailable".
            if use_cache:
                await _search_cache_put(key, page, max_results, results)
            return results
        reason, kind = _last_search_failure.get(), _last_search_failure_kind.get() or kind
    if reason:
        _set_search_failure(reason, kind or FAILING)
    return []
