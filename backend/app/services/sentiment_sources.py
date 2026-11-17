"""Outside data for the Market sentiment app: Reddit's official API (app-only
OAuth) and SerpAPI's Google Maps engines. Both are optional settings; without
them the app says so instead of failing (``NotConfigured``).

Network errors are re-raised without the request URL: SerpAPI takes its key in
the query string and it must not reach a member's screen or the logs.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import httpx
from app.services.costs import record_paid_call

REDDIT_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
REDDIT_API = "https://oauth.reddit.com"
REDDIT_USER_AGENT = "web:yucg-resources:1.0 (Yale Undergraduate Consulting Group)"
SERPAPI_URL = "https://serpapi.com/search.json"

REDDIT_TIME_FILTERS = ("hour", "day", "week", "month", "year", "all")
TIMEOUT = httpx.Timeout(20.0, connect=10.0)


class NotConfigured(RuntimeError):
    """The server has no key for this source."""


class SourceError(RuntimeError):
    """The source refused or failed; the message is safe to show."""


def reddit_configured() -> bool:
    return bool((os.getenv("REDDIT_CLIENT_ID") or "").strip() and (os.getenv("REDDIT_CLIENT_SECRET") or "").strip())


def serpapi_configured() -> bool:
    return bool((os.getenv("SERPAPI_KEY") or "").strip())


REDDIT_MESSAGE = "Reddit is not configured on this server"
GOOGLE_MESSAGE = "Google reviews are not configured on this server"


# --- Reddit ------------------------------------------------------------------

async def _reddit_token(client: httpx.AsyncClient) -> str:
    if not reddit_configured():
        raise NotConfigured(REDDIT_MESSAGE)
    response = await client.post(
        REDDIT_TOKEN_URL,
        auth=(os.environ["REDDIT_CLIENT_ID"].strip(), os.environ["REDDIT_CLIENT_SECRET"].strip()),
        data={"grant_type": "client_credentials"},
        headers={"User-Agent": REDDIT_USER_AGENT},
    )
    token = response.json().get("access_token") if response.status_code == 200 else None
    if not token:
        raise SourceError(f"Reddit refused the server's credentials ({response.status_code})")
    return token


async def search_reddit(subreddits: list[str], query: str, time_filter: str, limit: int) -> list[dict[str, Any]]:
    """Posts matching ``query`` in each subreddit, in Reddit's own relevance order, up to ``limit`` per subreddit. One failing subreddit raises."""
    posts: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            headers = {"Authorization": f"bearer {await _reddit_token(client)}", "User-Agent": REDDIT_USER_AGENT}
            for subreddit in subreddits:
                after: str | None = None
                seen = 0
                while seen < limit:
                    params: dict[str, Any] = {"q": query, "restrict_sr": 1, "sort": "relevance",
                                              "t": time_filter, "limit": min(100, limit - seen), "raw_json": 1}
                    if after:
                        params["after"] = after
                    response = await client.get(f"{REDDIT_API}/r/{subreddit}/search", params=params, headers=headers)
                    if response.status_code == 404 or response.status_code == 403:
                        raise SourceError(f"r/{subreddit} is not available (private, banned or missing)")
                    if response.status_code != 200:
                        raise SourceError(f"Reddit answered {response.status_code} for r/{subreddit}")
                    data = response.json().get("data") or {}
                    children = data.get("children") or []
                    for child in children:
                        post = child.get("data") or {}
                        created = datetime.fromtimestamp(float(post.get("created_utc") or 0), tz=timezone.utc)
                        posts.append({
                            "id": post.get("id"),
                            "subreddit": post.get("subreddit") or subreddit,
                            "title": post.get("title") or "",
                            "text": post.get("selftext") or "",
                            "created": created.isoformat(),
                            "month": created.strftime("%Y-%m"),
                            "score": int(post.get("score") or 0),
                            "num_comments": int(post.get("num_comments") or 0),
                            "url": f"https://www.reddit.com{post.get('permalink') or ''}",
                        })
                    seen += len(children)
                    after = data.get("after")
                    if not after or not children:
                        break
    except httpx.HTTPError as exc:
        raise SourceError(f"Could not reach Reddit ({type(exc).__name__})") from None
    return posts


# --- Google reviews through SerpAPI ------------------------------------------

async def _serp(client: httpx.AsyncClient, params: dict[str, Any]) -> dict[str, Any]:
    if not serpapi_configured():
        raise NotConfigured(GOOGLE_MESSAGE)
    try:
        await record_paid_call("serpapi", engine=str(params.get("engine") or ""))
        response = await client.get(SERPAPI_URL, params={**params, "api_key": os.environ["SERPAPI_KEY"].strip()})
    except httpx.HTTPError as exc:
        raise SourceError(f"Could not reach SerpAPI ({type(exc).__name__})") from None
    if response.status_code != 200:
        raise SourceError(f"SerpAPI answered {response.status_code}")
    data = response.json()
    if isinstance(data, dict) and data.get("error"):
        # An empty search is not a failure.
        if "hasn't returned any results" in str(data["error"]):
            return {}
        raise SourceError("SerpAPI reported an error for this search")
    return data


def _place(entry: dict[str, Any]) -> dict[str, Any] | None:
    if not entry.get("data_id"):
        return None
    return {
        "data_id": entry["data_id"],
        "place_id": entry.get("place_id") or "",
        "name": entry.get("title") or "",
        "address": entry.get("address") or "",
        "rating": entry.get("rating"),
        "review_count": entry.get("reviews") or 0,
    }


async def search_places(query: str) -> list[dict[str, Any]]:
    """Google Maps places for a search such as "Blue Bottle Coffee New Haven"."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        data = await _serp(client, {"engine": "google_maps", "type": "search", "q": query})
    entries = list(data.get("local_results") or [])
    if not entries and isinstance(data.get("place_results"), dict):
        entries = [data["place_results"]]
    places = [p for p in (_place(e) for e in entries) if p]
    return places[:20]


async def fetch_reviews(data_id: str, count: int) -> list[dict[str, Any]]:
    """Up to ``count`` written reviews of one place, most helpful first."""
    reviews: list[dict[str, Any]] = []
    token: str | None = None
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for page in range(4):
            params: dict[str, Any] = {"engine": "google_maps_reviews", "data_id": data_id}
            if token:
                params.update(next_page_token=token, num=20)
            data = await _serp(client, params)
            for review in data.get("reviews") or []:
                text = (review.get("snippet") or review.get("extracted_snippet", {}).get("original") or "").strip()
                if text:
                    reviews.append({
                        "author": (review.get("user") or {}).get("name") or "Anonymous",
                        "rating": review.get("rating"),
                        "text": text,
                        "date": review.get("date") or "",
                    })
            token = (data.get("serpapi_pagination") or {}).get("next_page_token")
            if len(reviews) >= count or not token:
                break
    return reviews[:count]
