"""Direct messages from the club's Slack bot, shared by the nightly digest and
the admin setup reminders.

One DM is conversations.open then chat.postMessage; a member known only by
email is found first with users.lookupByEmail. Every call goes through
``call``, which waits out Slack's HTTP 429 (Retry-After) a few times and then
reports ``ratelimited``. Failures come back as Slack's own error code, never
as a success.
"""
from __future__ import annotations

import asyncio
import time

import httpx

API = "https://slack.com/api/"
MAX_RETRIES = 3
MAX_WAIT_SECONDS = 30.0
# Slack allows roughly one chat.postMessage per second per channel; DMs go to
# different channels, but a steady 1/s keeps a whole-club run well inside every
# tier of the Web API limits.
MIN_SECONDS_BETWEEN_MEMBERS = 1.0

_last_member_at = 0.0


def new_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=10.0)


async def pause(seconds: float) -> None:
    await asyncio.sleep(seconds)


async def pace() -> None:
    """Hold each member's DM at least a second after the previous one, across requests."""
    global _last_member_at
    wait = _last_member_at + MIN_SECONDS_BETWEEN_MEMBERS - time.monotonic()
    if wait > 0:
        await pause(wait)
    _last_member_at = time.monotonic()


async def call(client: httpx.AsyncClient, token: str, method: str, *, params: dict | None = None,
               body: dict | None = None) -> dict:
    """One Web API call; the parsed reply (``ok`` and ``error`` always present)."""
    headers = {"Authorization": f"Bearer {token}"}
    for attempt in range(MAX_RETRIES + 1):
        try:
            if params is not None:
                response = await client.get(API + method, params=params, headers=headers)
            else:
                response = await client.post(API + method, json=body, headers=headers)
        except httpx.HTTPError:
            return {"ok": False, "error": "network_error"}
        if response.status_code == 429:
            if attempt == MAX_RETRIES:
                return {"ok": False, "error": "ratelimited"}
            try:
                wait = float(response.headers.get("Retry-After", "1"))
            except ValueError:
                wait = 1.0
            await pause(min(max(wait, 0.0), MAX_WAIT_SECONDS))
            continue
        try:
            data = response.json()
        except ValueError:
            return {"ok": False, "error": "bad_response"}
        if not isinstance(data, dict):
            return {"ok": False, "error": "bad_response"}
        if not data.get("ok") and not data.get("error"):
            data["error"] = "unknown_error"
        return data
    return {"ok": False, "error": "ratelimited"}  # unreachable; the loop always returns


async def user_id_for_email(client: httpx.AsyncClient, token: str, email: str) -> tuple[str | None, str | None]:
    """(Slack user id, None), or (None, Slack's error code) such as ``users_not_found``."""
    data = await call(client, token, "users.lookupByEmail", params={"email": email})
    user = (data.get("user") or {}).get("id") if data.get("ok") else None
    if data.get("ok") and not user:
        return None, "bad_response"
    return user, None if data.get("ok") else data["error"]


async def post_dm(client: httpx.AsyncClient, token: str, slack_user_id: str, text: str) -> str | None:
    """Open the DM and post ``text``. None when posted, else Slack's error code."""
    opened = await call(client, token, "conversations.open", body={"users": slack_user_id})
    if not opened.get("ok"):
        return opened["error"]
    channel = (opened.get("channel") or {}).get("id")
    if not channel:
        return "bad_response"
    posted = await call(client, token, "chat.postMessage", body={"channel": channel, "text": text})
    return None if posted.get("ok") else posted["error"]
