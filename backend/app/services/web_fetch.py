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
import logging
import os
from contextvars import ContextVar
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.services.discovery_gate import discovery_job
from app.services.generation_policy import reserve_firecrawl_call, reserve_tinyfish_call

logger = logging.getLogger("yucg.firecrawl")
_TIMEOUT_S = 30.0
_MONID_API_BASE = "https://api.monid.ai"
_MONID_SEARCH_TIMEOUT_S = 20.0
_MONID_FETCH_TIMEOUT_S = 45.0
_MONID_POLL_ATTEMPTS = 5
_MONID_POLL_INTERVAL_S = 2.0


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


async def _tinyfish_web_search(query: str, max_results: int, *, user_id: int | None = None) -> list[dict[str, Any]] | None:
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

    max_pages = min(11, max(1, -(-max(1, max_results) // _TINYFISH_SEARCH_PAGE_SIZE)))
    started = time.monotonic()
    out: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    async with httpx.AsyncClient(timeout=_MONID_SEARCH_TIMEOUT_S) as client:
        for page in range(max_pages):
            if user_id is not None:
                await reserve_tinyfish_call(user_id)
            params = dict(base_params)
            if page:
                params["page"] = page
            output = await _monid_run(
                client, api_key, "/search", query_params=params,
                poll_attempts=_MONID_POLL_ATTEMPTS, poll_interval_s=_MONID_POLL_INTERVAL_S,
            )
            if output is None:
                if page == 0:
                    return None
                break
            results = output.get("results")
            if not isinstance(results, list):
                if page == 0:
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
                 len(out), query, time.monotonic() - started, page + 1)
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


async def web_search(query: str, max_results: int = 8, *, user_id: int | None = None) -> list[dict[str, Any]]:
    """Web search. Tries TinyFish first, then Firecrawl. Returns [] when
    neither is configured. A genuine empty result from TinyFish is trusted
    and returned as-is, not treated as a reason to fall back to Firecrawl."""
    _last_search_failure.set(None)
    reason = None
    if tinyfish_configured():
        results = await _tinyfish_web_search(query, max_results, user_id=user_id)
        if results is not None:
            return results
        reason = "TinyFish gave no answer"
    if firecrawl_configured():
        results = await _firecrawl_web_search(query, max_results, user_id=user_id)
        if results or _last_search_failure.get() is None:
            return results
        reason = _last_search_failure.get()
    if reason:
        _last_search_failure.set(reason)
    return []
