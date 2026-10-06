"""The Slack helper: what a member asks, and what it refuses to do.

A bot in a workspace is a public door on the club database. Three properties
matter more than the wording of any answer: an unsigned request is refused, a
Slack user who has not connected a club account gets nothing but instructions,
and the helper never answers its own messages or the same event twice.
"""
import asyncio
import hashlib
import hmac
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JWT_SECRET", "slack-helper-test-secret-value-1234")
os.environ["SLACK_SIGNING_SECRET"] = "helper-test-signing-secret"
os.environ["FRONTEND_URL"] = "https://outreach.example.org"

import httpx  # noqa: E402
from fastapi import FastAPI  # noqa: E402

from app.database import get_db, init_db  # noqa: E402
from app.services import slack_agent  # noqa: E402

SLACK_USER = "U09G4Q1HRDZ"


def sign(body: bytes, timestamp: str | None = None) -> dict:
    timestamp = timestamp or str(int(time.time()))
    signature = "v0=" + hmac.new(
        os.environ["SLACK_SIGNING_SECRET"].encode(),
        b"v0:" + timestamp.encode() + b":" + body,
        hashlib.sha256,
    ).hexdigest()
    return {"x-slack-request-timestamp": timestamp, "x-slack-signature": signature}


async def seed(db) -> None:
    await db.execute(
        "INSERT INTO users(id,email,name,role,is_active) VALUES (1,'member@yale.edu','Member','admin',1)"
    )
    from app.token_crypto import encrypt_token

    await db.execute(
        """INSERT INTO user_slack_tokens (user_id, access_token, refresh_token, token_expires_at, team_id, team_name, user_slack_id, scope)
           VALUES (1, ?, NULL, NULL, 'T1', 'YUCG', ?, 'chat:write')""",
        (encrypt_token("xoxb-workspace"), SLACK_USER),
    )
    # A company with people on file, none written to; and one already mailed.
    await db.executemany(
        "INSERT INTO contacts (id,name,email,company) VALUES (?,?,?,?)",
        [
            (1, "Jean Bartik", "jean@a24films.com", "A24"),
            (2, "Klara Dan", "klara@a24films.com", "A24"),
            (3, "Ada Lovelace", "ada@neon.com", "NEON"),
        ],
    )
    await db.execute(
        "INSERT INTO campaigns (id,name,status,owner_user_id,sender_user_id) VALUES (1,'NEON first touch','releasing',1,1)"
    )
    await db.execute(
        """INSERT INTO campaign_contacts (campaign_id,contact_id,email_subject,email_body,status,sent_at,sent_by_user_id)
           VALUES (1,3,'S','B','sent',CURRENT_TIMESTAMP,1)"""
    )
    await db.commit()


def plain_english_questions_reach_the_right_door() -> None:
    """Members ask questions; the helper must land them on one screen. A wrong
    guess here sends someone to Find people when the people are already on
    file, which is exactly the run-around this is meant to remove."""
    cases = [
        ("how are we doing at A24?", "company", "A24"),
        ("<@U0HELPER> who is at A24", "company", "A24"),
        ("draft something for Jean Bartik", "draft", "Jean Bartik"),
        ("what's queued?", "queue"),
        ("any follow-ups waiting?", "followups"),
        ("who should I write to next?", "targets"),
        ("hello", "help"),
        ("", "help"),
        ("company \"Kane Pixels / Backrooms IP\"", "company", "Kane Pixels / Backrooms IP"),
    ]
    for question, *expected in cases:
        route, argument = slack_agent.parse_intent(question)
        assert route == expected[0], f"{question!r} routed to {route}, expected {expected[0]}"
        if len(expected) > 1:
            assert argument == expected[1], f"{question!r} gave subject {argument!r}"


async def the_answer_states_club_state_and_one_next_step() -> None:
    member = {"id": 1, "email": "member@yale.edu"}

    on_file = await slack_agent.handle_message("how are we doing at A24?", member)
    assert "2 on file" in on_file["text"] and "none written to" in on_file["text"]
    assert "https://outreach.example.org/studio?q=A24" in on_file["text"]

    mailed = await slack_agent.handle_message("who is at NEON", member)
    assert "1 mailed" in mailed["text"]
    assert "/outreach" in mailed["text"], "an already-mailed company points at replies"

    unknown = await slack_agent.handle_message("how are we doing at Nobody Ltd", member)
    assert "nobody on file" in unknown["text"].lower()
    assert "/scraper?view=company&company=Nobody%20Ltd" in unknown["text"]

    queue = await slack_agent.handle_message("what's queued?", member)
    assert "NEON first touch" in queue["text"] and "/campaigns/1" in queue["text"]

    targets = await slack_agent.handle_message("who should I write to next?", member)
    assert "A24" in targets["text"] and "NEON" not in targets["text"], targets["text"]

    # Every answer hands over a page to open, never an endpoint to call: the
    # helper navigates, it does not act.
    for answer in (on_file, mailed, queue, targets):
        links = re.findall(r"<(https?://[^|>]+)", answer["text"])
        assert links, answer["text"]
        for link in links:
            assert link.startswith("https://outreach.example.org/"), link
            assert "/api/" not in link, link


async def an_unsigned_request_is_refused(client) -> None:
    body = urlencode({"user_id": SLACK_USER, "text": "queue"}).encode()
    response = await client.post(
        "/api/slack/commands", content=body,
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 401, response.text

    stale = sign(body, timestamp=str(int(time.time()) - 3600))
    response = await client.post(
        "/api/slack/commands", content=body,
        headers={"content-type": "application/x-www-form-urlencoded", **stale},
    )
    assert response.status_code == 401, "a replayed request was accepted"


async def a_slack_user_without_a_club_account_gets_instructions(client) -> None:
    body = urlencode({"user_id": "U-STRANGER", "text": "what's queued?"}).encode()
    response = await client.post(
        "/api/slack/commands", content=body,
        headers={"content-type": "application/x-www-form-urlencoded", **sign(body)},
    )
    assert response.status_code == 200
    assert "Connect Slack" in response.json()["text"]
    assert "NEON" not in response.json()["text"], "club state leaked to an unknown Slack user"


async def the_slash_command_answers_a_question(client) -> None:
    body = urlencode({"user_id": SLACK_USER, "text": "how are we doing at A24?"}).encode()
    response = await client.post(
        "/api/slack/commands", content=body,
        headers={"content-type": "application/x-www-form-urlencoded", **sign(body)},
    )
    assert response.status_code == 200
    assert response.json()["response_type"] == "ephemeral"
    assert "2 on file" in response.json()["text"]


async def slack_can_verify_the_event_subscription(client) -> None:
    body = json.dumps({"type": "url_verification", "challenge": "abc123"}).encode()
    response = await client.post(
        "/api/slack/events", content=body, headers={"content-type": "application/json"}
    )
    assert response.status_code == 200 and response.text == "abc123"


def capture_replies() -> tuple[AsyncMock, list[dict]]:
    """Patch only the Slack post, not every HTTP call: the test client speaks
    to the app over httpx too, and patching that swallowed the request."""
    said: list[dict] = []

    async def record(channel: str, text: str, thread_ts: str | None = None) -> bool:
        said.append({"channel": channel, "text": text, "thread_ts": thread_ts})
        return True

    return AsyncMock(side_effect=record), said


async def a_mention_is_answered_once_in_its_thread(client) -> None:
    event = {
        "type": "app_mention", "user": SLACK_USER, "channel": "C1",
        "text": "<@U0HELPER> how are we doing at A24?", "thread_ts": "1700000000.1",
    }
    body = json.dumps({"type": "event_callback", "event_id": "Ev1", "event": event}).encode()
    poster, said = capture_replies()

    with patch("app.services.slack_agent.post_reply", poster):
        first = await client.post(
            "/api/slack/events", content=body,
            headers={"content-type": "application/json", **sign(body)},
        )
        # Slack retries the same event when it thinks we were slow.
        second = await client.post(
            "/api/slack/events", content=body,
            headers={"content-type": "application/json", **sign(body)},
        )
    assert first.status_code == 200, first.text
    assert second.json().get("duplicate") is True, second.text
    assert len(said) == 1, f"the retry was answered again: {said}"
    assert said[0]["channel"] == "C1" and said[0]["thread_ts"] == "1700000000.1"
    assert "2 on file" in said[0]["text"]


async def the_helper_never_answers_itself(client) -> None:
    for event in (
        {"type": "message", "channel_type": "im", "bot_id": "B1", "text": "hi", "channel": "D1"},
        {"type": "message", "channel_type": "channel", "user": SLACK_USER, "text": "hi", "channel": "C9"},
        {"type": "reaction_added", "user": SLACK_USER, "channel": "C9"},
    ):
        body = json.dumps({
            "type": "event_callback", "event_id": f"Ev-{event.get('type')}-{event.get('channel')}",
            "event": event,
        }).encode()
        poster, said = capture_replies()
        with patch("app.services.slack_agent.post_reply", poster):
            response = await client.post(
                "/api/slack/events", content=body,
                headers={"content-type": "application/json", **sign(body)},
            )
        assert response.status_code == 200
        assert said == [], f"the helper replied to {event}"


async def a_direct_message_is_answered_in_the_dm(client) -> None:
    event = {
        "type": "message", "channel_type": "im", "user": SLACK_USER,
        "channel": "D1", "text": "who should I write to next?",
    }
    body = json.dumps({"type": "event_callback", "event_id": "Ev-dm", "event": event}).encode()
    poster, said = capture_replies()

    with patch("app.services.slack_agent.post_reply", poster):
        response = await client.post(
            "/api/slack/events", content=body,
            headers={"content-type": "application/json", **sign(body)},
        )
    assert response.status_code == 200
    assert len(said) == 1 and said[0]["channel"] == "D1"
    assert said[0]["thread_ts"] is None, "a direct message was answered in a thread"
    assert "A24" in said[0]["text"]


async def the_reply_is_posted_with_a_workspace_token() -> None:
    """post_reply is the only place the helper talks back, so it is the only
    place a token is needed; it must be a live one, not the row's."""
    calls: list[dict] = []

    async def slack(url, *args, **kwargs):
        calls.append({"url": url, "json": kwargs.get("json"), "headers": kwargs.get("headers")})
        return httpx.Response(200, json={"ok": True})

    with patch("httpx.AsyncClient.post", AsyncMock(side_effect=slack)):
        assert await slack_agent.post_reply("C1", "hello", "1700000000.1") is True
    assert calls[0]["url"].endswith("chat.postMessage")
    assert calls[0]["json"] == {"channel": "C1", "text": "hello", "thread_ts": "1700000000.1"}
    assert calls[0]["headers"]["Authorization"] == "Bearer xoxb-workspace"


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp) / 'helper.db'}"
        await init_db()
        db = await get_db()
        try:
            await seed(db)
        finally:
            await db.close()

        from app.routers import slack as slack_router

        app = FastAPI()
        app.include_router(slack_router.router, prefix="/api/slack")
        plain_english_questions_reach_the_right_door()
        await the_answer_states_club_state_and_one_next_step()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await an_unsigned_request_is_refused(client)
            await a_slack_user_without_a_club_account_gets_instructions(client)
            await the_slash_command_answers_a_question(client)
            await slack_can_verify_the_event_subscription(client)
            await a_mention_is_answered_once_in_its_thread(client)
            await the_helper_never_answers_itself(client)
            await a_direct_message_is_answered_in_the_dm(client)
        await the_reply_is_posted_with_a_workspace_token()
        print("slack helper: ok")


asyncio.run(main())
