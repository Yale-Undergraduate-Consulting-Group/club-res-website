"""Week-shaped Slack posts. Failures are swallowed — Slack is the hallway, not the CRM."""
from __future__ import annotations

import os

import httpx


async def post_week_event(text: str) -> bool:
    # The refreshed install token (app/services/slack_tokens.py) outlives a
    # pasted SLACK_BOT_TOKEN, which dies after 12 hours with token rotation on;
    # workspace_access_token falls back to that variable.
    from app.services.slack_tokens import workspace_access_token

    channel = (os.getenv("SLACK_DIGEST_CHANNEL_ID") or "").strip()
    if not channel or not text.strip():
        return False
    try:
        token = await workspace_access_token()
        if not token:
            return False
        async with httpx.AsyncClient(timeout=12.0) as client:
            r = await client.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {token}"},
                json={"channel": channel, "text": text.strip()},
            )
        return bool(r.json().get("ok"))
    except Exception:
        return False
