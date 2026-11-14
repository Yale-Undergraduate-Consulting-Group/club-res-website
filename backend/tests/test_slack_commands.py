"""`/yucg` subcommands: help, onboard, link, unlink, usage, requests.

What matters: a stranger can find out how to link but sees no club data;
`unlink` removes only the asker's own link; usage dollars and other members'
names are for admins only; and a feature request is saved, rate-limited, and
reaches the admins without the model ever being called.
"""
import asyncio
import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JWT_SECRET", "slack-commands-test-secret-value-1234")
os.environ["SLACK_SIGNING_SECRET"] = "commands-test-signing-secret"
os.environ["FRONTEND_URL"] = "https://outreach.example.org"
os.environ["AI_MONTHLY_BUDGET_USD"] = "20"

import httpx  # noqa: E402
from fastapi import FastAPI  # noqa: E402

from app.database import get_db, init_db  # noqa: E402
from app.services import slack_commands  # noqa: E402

ADMIN_SLACK = "UADMIN0001"
MEMBER_SLACK = "UMEMBER001"
STRANGER_SLACK = "USTRANGER1"


def sign(body: bytes) -> dict:
    timestamp = str(int(time.time()))
    signature = "v0=" + hmac.new(
        os.environ["SLACK_SIGNING_SECRET"].encode(),
        b"v0:" + timestamp.encode() + b":" + body,
        hashlib.sha256,
    ).hexdigest()
    return {
        "x-slack-request-timestamp": timestamp,
        "x-slack-signature": signature,
        "content-type": "application/x-www-form-urlencoded",
    }


async def seed() -> None:
    from app.token_crypto import encrypt_token

    db = await get_db()
    try:
        await db.executemany(
            "INSERT INTO users(id,email,name,role,is_active) VALUES (?,?,?,?,1)",
            [(1, "admin@yale.edu", "Ada Admin", "admin"), (2, "member@yale.edu", "Mo Member", "standard")],
        )
        await db.executemany(
            """INSERT INTO user_slack_tokens (user_id, access_token, team_id, team_name, user_slack_id, scope)
               VALUES (?, ?, 'T1', 'YUCG', ?, 'chat:write')""",
            [(1, encrypt_token("xoxb-admin"), ADMIN_SLACK), (2, encrypt_token("xoxb-member"), MEMBER_SLACK)],
        )
        cost = lambda usd: json.dumps({"model_id": "m", "cost_usd": usd, "status": "completed"})  # noqa: E731
        await db.executemany(
            "INSERT INTO usage_events(user_id,event_type,resource_type,details_json) VALUES (?, 'bedrock_reserved', 'slack', ?)",
            [(1, cost(1.0)), (2, cost(2.5)), (2, cost(0.5))],
        )
        await db.commit()
    finally:
        await db.close()


async def command(client, slack_user: str, text: str) -> str:
    body = urlencode({"user_id": slack_user, "text": text}).encode()
    response = await client.post("/api/slack/commands", content=body, headers=sign(body))
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["response_type"] == "ephemeral", "a subcommand answer must be private"
    return payload["text"]


def only_whole_subcommands_are_taken() -> None:
    cases = {
        "help": ("help", ""),
        "<@U0BOT> onboard": ("onboard", ""),
        "/yucg usage": ("usage", ""),
        "yucg link": ("link", ""),
        "stats": ("usage", ""),
        "request let me export a campaign": ("requests", "let me export a campaign"),
        "Unlink": ("unlink", ""),
    }
    for text, expected in cases.items():
        assert slack_commands.parse_subcommand(text) == expected, text
    for question in ("link me to A24's contacts", "usage of the word synergy", "how are we doing at A24?", ""):
        assert slack_commands.parse_subcommand(question) is None, question


async def a_stranger_can_learn_to_link_but_nothing_more(client) -> None:
    assert "/profile?view=integrations" in await command(client, STRANGER_SLACK, "link")
    assert "/yucg link" in await command(client, STRANGER_SLACK, "help")
    tour = await command(client, STRANGER_SLACK, "onboard")
    assert ":white_large_square:" in tour and ":white_check_mark:" not in tour
    for private in ("usage", "requests add dark mode", "unlink"):
        reply = await command(client, STRANGER_SLACK, private)
        assert reply == slack_commands.NOT_LINKED, private
    assert "Mo Member" not in await command(client, STRANGER_SLACK, "usage")


async def usage_dollars_and_names_are_for_admins(client) -> None:
    member = await command(client, MEMBER_SLACK, "usage")
    assert "2 AI calls" in member
    assert "20% used" in member  # $4.00 of $20 across the club
    assert "$" not in member and "Ada Admin" not in member

    admin = await command(client, ADMIN_SLACK, "usage")
    assert "$4.00 of $20.00" in admin
    assert "Mo Member: 2 calls, $3.00" in admin


async def link_names_the_account_and_unlink_removes_only_mine(client) -> None:
    assert "Mo Member" in await command(client, MEMBER_SLACK, "link")
    assert "Unlinked" in await command(client, MEMBER_SLACK, "unlink")
    db = await get_db()
    try:
        rows = await (await db.execute("SELECT user_id FROM user_slack_tokens ORDER BY user_id")).fetchall()
    finally:
        await db.close()
    assert [row["user_id"] for row in rows] == [1], "unlink must remove the asker's link and nobody else's"
    assert await command(client, MEMBER_SLACK, "usage") == slack_commands.NOT_LINKED


async def a_request_is_saved_capped_and_sent_to_admins(client) -> None:
    sent: list[tuple] = []

    async def fake_post(channel, text, thread_ts=None, ephemeral_user=None):
        sent.append((channel, text))
        return True

    converse = Mock(side_effect=AssertionError("a subcommand must never reach the model"))
    with patch("app.services.slack_agent.post_reply", fake_post), \
            patch("app.services.slack_agent.llm.converse", converse):
        assert "Add your idea" in await command(client, ADMIN_SLACK, "requests")
        reply = await command(client, ADMIN_SLACK, "requests show reply rates <!channel>")
        assert "saved your request" in reply
        assert sent == [(ADMIN_SLACK, sent[0][1])] and "show reply rates" in sent[0][1]
        assert "<!channel>" not in sent[0][1], "a request must not ping a whole channel"
        for _ in range(slack_commands.REQUESTS_PER_MEMBER_PER_DAY - 1):
            await command(client, ADMIN_SLACK, "requests another idea")
        assert "limit" in await command(client, ADMIN_SLACK, "requests one too many")
    db = await get_db()
    try:
        saved = (await (await db.execute("SELECT COUNT(*) AS n FROM slack_feature_requests")).fetchone())["n"]
    finally:
        await db.close()
    assert saved == slack_commands.REQUESTS_PER_MEMBER_PER_DAY


async def the_same_words_work_in_a_direct_message() -> None:
    from app.services import slack_agent

    post = AsyncMock(return_value=True)
    converse = Mock(side_effect=AssertionError("a subcommand must never reach the model"))
    with patch("app.services.slack_agent.post_reply", post), \
            patch("app.services.slack_agent.llm.converse", converse):
        await slack_agent.answer_event({
            "type": "message", "channel_type": "im", "channel": "D1", "user": ADMIN_SLACK, "text": "onboard",
        })
        await slack_agent.answer_event({
            "type": "message", "channel_type": "im", "channel": "D2", "user": STRANGER_SLACK, "text": "link",
        })
    tour, link = post.await_args_list
    assert tour.args[0] == "D1" and "Welcome to YUCG Outreach" in tour.args[1]
    assert ":white_check_mark: <https://outreach.example.org/profile?view=integrations|Link Slack>" in tour.args[1]
    assert link.args[0] == "D2" and "/profile?view=integrations" in link.args[1]


async def a_model_question_says_working_then_pings_the_asker() -> None:
    from app.services import slack_agent

    post = AsyncMock(return_value=True)
    react = AsyncMock(return_value=None)
    with patch("app.services.slack_agent.post_reply", post), \
            patch("app.services.slack_agent.react", react), \
            patch("app.services.slack_agent._claude_answer", AsyncMock(return_value="Here is the summary.")):
        await slack_agent.answer_event({
            "type": "message", "channel_type": "im", "channel": "D1", "user": ADMIN_SLACK,
            "text": "summarize what we know about our media outreach",
        })
        await slack_agent.answer_event({
            "type": "app_mention", "channel": "C1", "user": ADMIN_SLACK, "ts": "1.0",
            "text": "<@U0BOT> summarize this thread",
        })
    working, answer, channel_working, channel_answer = post.await_args_list
    assert working.args[:2] == ("D1", slack_agent.WORKING_REPLY)
    assert answer.args[1] == f"<@{ADMIN_SLACK}> Here is the summary."
    # In a channel both stay private to the asker.
    assert channel_working.args[1] == slack_agent.WORKING_REPLY and channel_working.args[3] == ADMIN_SLACK
    assert channel_answer.args[3] == ADMIN_SLACK

    assert [c.args for c in react.await_args_list] == [
        ("reactions.add", "D1", ""), ("reactions.remove", "D1", ""),
        ("reactions.add", "C1", "1.0"), ("reactions.remove", "C1", "1.0"),
    ]

    post.reset_mock()
    react.reset_mock()
    with patch("app.services.slack_agent.post_reply", post), patch("app.services.slack_agent.react", react):
        await slack_agent.answer_event({
            "type": "message", "channel_type": "im", "channel": "D1", "user": ADMIN_SLACK, "text": "what's queued?",
        })
    assert post.await_count == 1, "an instant shortcut needs no working note"
    assert not post.await_args.args[1].startswith("<@")
    assert react.await_count == 0, "an instant shortcut needs no reaction"


async def a_token_without_reactions_write_still_answers() -> None:
    from app.services import slack_agent

    slack = Mock(return_value=httpx.Response(200, json={"ok": False, "error": "missing_scope"}))

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            return slack(url, json)

    with patch("httpx.AsyncClient", FakeClient), \
            patch("app.services.slack_tokens.workspace_access_token", AsyncMock(return_value="xoxb-test")):
        await slack_agent.react("reactions.add", "C1", "1.0")  # must not raise
    url, body = slack.call_args.args
    assert url.endswith("/reactions.add") and body == {"channel": "C1", "timestamp": "1.0", "name": "eyes"}


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp) / 'commands.db'}"
        await init_db()
        await seed()

        from app.routers import slack as slack_router

        app = FastAPI()
        app.include_router(slack_router.router, prefix="/api/slack")
        only_whole_subcommands_are_taken()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            await a_stranger_can_learn_to_link_but_nothing_more(client)
            await usage_dollars_and_names_are_for_admins(client)
            await a_request_is_saved_capped_and_sent_to_admins(client)
            await the_same_words_work_in_a_direct_message()
            await a_model_question_says_working_then_pings_the_asker()
            await a_token_without_reactions_write_still_answers()
            await link_names_the_account_and_unlink_removes_only_mine(client)
        print("slack commands: ok")


asyncio.run(main())
