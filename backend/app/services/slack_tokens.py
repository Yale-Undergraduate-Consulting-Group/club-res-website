"""One place that hands out a usable Slack token.

The club's Slack app uses **token rotation**, which cannot be turned off again:
`oauth.v2.access` returns an access token that expires (12 hours) plus a
single-use refresh token, and the refresh returns a *new* pair. Two
consequences drive this module:

* Nothing may read `access_token` from the row and use it directly — by the
  time the nightly digest runs, the token stored at install is expired.
* A refresh token may be spent exactly once. Two callers refreshing the same
  row concurrently would each write a pair, and the loser's credential is
  already dead. The write is therefore conditional on the refresh token that
  was read, exactly as the Gmail path is, and a caller that loses the race
  re-reads rather than overwriting a newer credential.

A workspace that has not opted into rotation returns no refresh token; those
installs keep working because the expiry is then unknown and the token is used
as-is.
"""
from __future__ import annotations

import os
import time

import httpx

from app.database import get_db
from app.token_crypto import decrypt_token, encrypt_token

# Refresh this far before the stated expiry: a digest run that starts just
# inside the window must not post with a token that dies mid-run.
REFRESH_BUFFER_SECONDS = 600
SLACK_REFRESH_URL = "https://slack.com/api/oauth.v2.access"


class SlackReauthorizationRequired(RuntimeError):
    """The stored credential is dead and only the member can replace it."""


async def _store_rotated(db, user_id: int, spent_refresh: str, data: dict) -> str | None:
    """Persist a rotated pair, but only against the credential we read."""
    access = data.get("access_token")
    refresh = data.get("refresh_token") or None
    expires_in = data.get("expires_in")
    if not access:
        return None
    expires_at = time.time() + float(expires_in) if expires_in else None
    await db.execute(
        """UPDATE user_slack_tokens
           SET access_token = ?, refresh_token = ?, token_expires_at = ?
           WHERE user_id = ? AND refresh_token = ?""",
        (encrypt_token(access), encrypt_token(refresh) if refresh else None,
         expires_at, user_id, spent_refresh),
    )
    changed = await (await db.execute("SELECT changes() AS n")).fetchone()
    await db.commit()
    # A disconnect, a re-install, or another worker's refresh may have landed
    # while Slack was answering. Never resurrect the credential we replaced.
    if not changed or int(changed["n"] or 0) != 1:
        return None
    return access


async def slack_access_token(user_id: int) -> str | None:
    """A token that is valid now, refreshing it first when rotation says so.

    Returns None when the member has no install or the credential can no
    longer be refreshed without them reconnecting.
    """
    db = await get_db()
    try:
        row = await (await db.execute(
            "SELECT access_token, refresh_token, token_expires_at FROM user_slack_tokens WHERE user_id = ?",
            (user_id,),
        )).fetchone()
        if not row or not row["access_token"]:
            return None
        access = decrypt_token(row["access_token"])
        stored_refresh = row["refresh_token"]
        refresh = decrypt_token(stored_refresh) if stored_refresh else ""
        expires_at = row["token_expires_at"]

        # No rotation on this install: there is nothing to refresh and no
        # expiry to respect.
        if not refresh:
            return access or None
        try:
            fresh_enough = expires_at is not None and float(expires_at) > time.time() + REFRESH_BUFFER_SECONDS
        except (TypeError, ValueError):
            fresh_enough = False
        if access and fresh_enough:
            return access

        client_id = (os.getenv("SLACK_CLIENT_ID") or "").strip()
        client_secret = (os.getenv("SLACK_CLIENT_SECRET") or "").strip()
        if not client_id or not client_secret:
            return None
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                SLACK_REFRESH_URL,
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if response.status_code != 200:
            return None
        data = response.json()
        if not data.get("ok"):
            # invalid_refresh_token means the member must reconnect; anything
            # else is transient and the next run may succeed.
            if data.get("error") in {"invalid_refresh_token", "invalid_grant_type", "token_revoked"}:
                raise SlackReauthorizationRequired(str(data.get("error")))
            return None
        return await _store_rotated(db, user_id, stored_refresh, data)
    finally:
        await db.close()


async def workspace_access_token() -> str | None:
    """A token for posting to a club channel.

    With rotation on, a token pasted into ``SLACK_BOT_TOKEN`` stops working
    twelve hours later, so the durable credential is an install: the newest
    one is used and refreshed like any other. The environment variable stays
    as the fallback for a workspace that never opted into rotation.
    """
    db = await get_db()
    try:
        row = await (await db.execute(
            "SELECT user_id FROM user_slack_tokens ORDER BY created_at DESC, user_id DESC LIMIT 1"
        )).fetchone()
    finally:
        await db.close()
    if row:
        try:
            token = await slack_access_token(int(row["user_id"]))
        except SlackReauthorizationRequired:
            token = None
        if token:
            return token
    return (os.getenv("SLACK_BOT_TOKEN") or "").strip() or None
