"""Slack assistant: thread memory, reading Slack with membership checks, model prefixes.

Converse and Slack's HTTP API are mocked; nothing here touches the network.
"""
import asyncio
import copy
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JWT_SECRET", "slack-threads-test-secret-value-1234")

import httpx  # noqa: E402

from app.database import get_db, init_db  # noqa: E402
from app.services import llm, slack_agent  # noqa: E402
from app.services.slack_tools import NOT_FOUND, SlackContext, run_tool  # noqa: E402

ALICE = {"id": 1, "email": "alice@yale.edu", "role": "standard"}
BOB = {"id": 2, "email": "bob@yale.edu", "role": "standard"}
ALICE_SLACK, BOB_SLACK = "U1ALICE", "U2BOB"
QUESTION = "Summarize everything we know about our media outreach strategy."


async def seed(db) -> None:
    from app.token_crypto import encrypt_token

    await db.executemany(
        "INSERT INTO users(id,email,name,role,is_active) VALUES (?,?,?,?,1)",
        [(1, "alice@yale.edu", "Alice", "standard"), (2, "bob@yale.edu", "Bob", "standard")],
    )
    await db.executemany(
        """INSERT INTO user_slack_tokens (user_id, access_token, refresh_token, token_expires_at, team_id, team_name, user_slack_id, scope)
           VALUES (?, ?, NULL, NULL, 'T1', 'YUCG', ?, 'chat:write')""",
        [(1, encrypt_token("xoxb-workspace"), ALICE_SLACK), (2, encrypt_token("xoxb-workspace"), BOB_SLACK)],
    )
    await db.commit()


def text_reply(text: str) -> dict:
    return {"stopReason": "end_turn", "output": {"message": {"role": "assistant", "content": [{"text": text}]}}}


def scripted_model(*replies: dict | str):
    """A Converse stand-in that snapshots what it was sent and plays replies in order."""
    sent: list[dict] = []
    queue = [text_reply(r) if isinstance(r, str) else r for r in replies]

    def converse(messages, **kwargs):
        sent.append({"messages": copy.deepcopy(messages), **kwargs})
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return Mock(side_effect=converse), sent


async def turns(channel: str | None = None) -> list[dict]:
    db = await get_db()
    try:
        sql = "SELECT * FROM slack_conversation_turns"
        args: tuple = ()
        if channel:
            sql += " WHERE channel = ?"
            args = (channel,)
        return [dict(row) for row in await (await db.execute(sql + " ORDER BY id", args)).fetchall()]
    finally:
        await db.close()


async def clear_turns() -> None:
    db = await get_db()
    try:
        await db.execute("DELETE FROM slack_conversation_turns")
        await db.commit()
    finally:
        await db.close()


def user_texts(call: dict) -> list[str]:
    return [m["content"][0]["text"] for m in call["messages"] if m["role"] == "user" and "text" in m["content"][0]]


async def follow_ups_continue_the_thread_and_stay_private() -> None:
    thread = SlackContext(slack_user=ALICE_SLACK, channel="C1", thread_ts="100.1")
    converse, sent = scripted_model("First answer.", "Second answer.")
    with patch("app.services.slack_agent.llm.converse", converse):
        await slack_agent.handle_message(QUESTION, ALICE, thread)
        await slack_agent.handle_message("Please expand on the strategy points you listed.", ALICE, thread)
    assert [m["role"] for m in sent[0]["messages"]] == ["user"]
    second = sent[1]["messages"]
    assert [m["role"] for m in second] == ["user", "assistant", "user"], second
    assert second[0]["content"][0]["text"] == QUESTION
    assert second[1]["content"][0]["text"] == "First answer."

    # Another member in the very same thread starts from nothing.
    converse, sent = scripted_model("Bob's answer.")
    bob_in_thread = SlackContext(slack_user=BOB_SLACK, channel="C1", thread_ts="100.1")
    with patch("app.services.slack_agent.llm.converse", converse):
        await slack_agent.handle_message("Please expand on the strategy points you listed.", BOB, bob_in_thread)
    assert [m["role"] for m in sent[0]["messages"]] == ["user"], "Bob inherited Alice's context"
    assert "First answer." not in json.dumps(sent[0]["messages"])

    # A different thread, and a slash command, share nothing either.
    converse, sent = scripted_model("Elsewhere.")
    with patch("app.services.slack_agent.llm.converse", converse):
        await slack_agent.handle_message(QUESTION, ALICE, SlackContext(ALICE_SLACK, "C1", "999.9"))
        await slack_agent.handle_message(QUESTION, ALICE, SlackContext(ALICE_SLACK))
        await slack_agent.handle_message(QUESTION, ALICE)
    assert all(len(call["messages"]) == 1 for call in sent)
    assert [row["thread_key"] for row in await turns("C1")] == ["100.1", "100.1", "100.1", "999.9"]


async def direct_messages_roll_one_conversation_per_member() -> None:
    await clear_turns()
    dm = SlackContext(slack_user=ALICE_SLACK, channel="D1", is_dm=True)
    converse, sent = scripted_model("DM one.", "DM two.")
    with patch("app.services.slack_agent.llm.converse", converse):
        await slack_agent.handle_message(QUESTION, ALICE, dm)
        await slack_agent.handle_message("Now compare that with last quarter's approach.", ALICE, dm)
    assert len(sent[1]["messages"]) == 3
    assert {row["thread_key"] for row in await turns("D1")} == {"dm"}


async def memory_is_trimmed_to_its_limits() -> None:
    await clear_turns()
    thread = SlackContext(slack_user=ALICE_SLACK, channel="C2", thread_ts="200.1")
    converse, _ = scripted_model("short")
    with patch("app.services.slack_agent.llm.converse", converse):
        for number in range(11):
            await slack_agent.handle_message(f"{QUESTION} #{number}", ALICE, thread)
    kept = await turns("C2")
    assert len(kept) == slack_agent.MEMORY_MAX_TURNS
    assert kept[-1]["question"].endswith("#10") and kept[0]["question"].endswith("#3")

    await clear_turns()
    converse, _ = scripted_model("x" * 3_000)
    with patch("app.services.slack_agent.llm.converse", converse):
        for number in range(6):
            await slack_agent.handle_message(f"{QUESTION} #{number}", ALICE, thread)
    kept = await turns("C2")
    size = sum(len(row["question"]) + len(row["answer"]) for row in kept)
    assert size <= slack_agent.MEMORY_MAX_CHARS and 2 <= len(kept) < 6, (size, len(kept))
    assert kept[-1]["question"].endswith("#5"), "the newest turn must survive trimming"


async def idle_conversations_expire_after_a_day() -> None:
    await clear_turns()
    old = SlackContext(slack_user=ALICE_SLACK, channel="C3", thread_ts="300.1")
    other = SlackContext(slack_user=ALICE_SLACK, channel="C4", thread_ts="400.1")
    converse, sent = scripted_model("Answer.")
    with patch("app.services.slack_agent.llm.converse", converse):
        await slack_agent.handle_message(QUESTION, ALICE, old)
        await slack_agent.handle_message(QUESTION, ALICE, other)
        db = await get_db()
        try:
            await db.execute(
                "UPDATE slack_conversation_turns SET created_at = ?", (time.time() - 25 * 3600,)
            )
            await db.commit()
        finally:
            await db.close()
        # Idle for over 24 hours: not continued.
        await slack_agent.handle_message("Please expand on the strategy points you listed.", ALICE, old)
        assert len(sent[-1]["messages"]) == 1, "an expired conversation was continued"
        await slack_agent.handle_message("And one more thing about that strategy please.", ALICE, old)
        assert len(sent[-1]["messages"]) == 3, "only the fresh turn should remain"
    # Writing deleted both idle conversations; C3 holds only its new turns.
    assert [row["channel"] for row in await turns()] == ["C3", "C3"], await turns()
    assert all(row["created_at"] > time.time() - 60 for row in await turns())


async def an_active_conversation_keeps_turns_older_than_a_day() -> None:
    await clear_turns()
    thread = SlackContext(slack_user=ALICE_SLACK, channel="C5", thread_ts="500.1")
    converse, sent = scripted_model("Answer.")
    with patch("app.services.slack_agent.llm.converse", converse):
        await slack_agent.handle_message(QUESTION, ALICE, thread)
        db = await get_db()
        try:
            await db.execute("UPDATE slack_conversation_turns SET created_at = ?", (time.time() - 23 * 3600,))
            await db.commit()
        finally:
            await db.close()
        await slack_agent.handle_message("Please expand on the strategy points you listed.", ALICE, thread)
        assert len(sent[-1]["messages"]) == 3
        db = await get_db()
        try:
            await db.execute(
                "UPDATE slack_conversation_turns SET created_at = ? WHERE id = (SELECT MIN(id) FROM slack_conversation_turns)",
                (time.time() - 30 * 3600,),
            )
            await db.commit()
        finally:
            await db.close()
        await slack_agent.handle_message("Tell me one more thing about those strategy points.", ALICE, thread)
    assert len(sent[-1]["messages"]) == 5, "idle time is measured from the latest turn"


def slack_workspace(*, channels: dict[str, list[str]], history: dict[str, list[dict]], readable=None):
    """Mock Slack: `channels` maps a Slack user to the channel ids they belong to."""
    catalog = {"C1": "general", "C2": "secret"}
    names = {ALICE_SLACK: "Alice A", BOB_SLACK: "Bob B"}
    calls: list[tuple[str, dict]] = []

    async def get(url, *args, **kwargs):
        method = url.rsplit("/", 1)[-1]
        params = kwargs.get("params") or {}
        calls.append((method, params))
        assert kwargs["headers"]["Authorization"] == "Bearer xoxb-workspace"
        if method == "users.conversations":
            rows = [{"id": cid, "name": catalog[cid]} for cid in channels.get(params["user"], [])]
            return httpx.Response(200, json={"ok": True, "channels": rows})
        if method == "users.info":
            name = names.get(params["user"])
            return httpx.Response(200, json={"ok": bool(name), "user": {"profile": {"display_name": name}}})
        if method in {"conversations.history", "conversations.replies"}:
            if readable is not None and params["channel"] not in readable:
                return httpx.Response(200, json={"ok": False, "error": "not_in_channel"})
            return httpx.Response(200, json={"ok": True, "messages": history.get(params["channel"], [])})
        return httpx.Response(200, json={"ok": False, "error": "unknown_method"})

    return AsyncMock(side_effect=get), calls


def message(user: str, text: str, ts: float) -> dict:
    return {"type": "message", "user": user, "text": text, "ts": f"{ts:.6f}"}


async def a_channel_is_readable_only_by_its_members() -> None:
    now = time.time()
    history = {
        "C1": [message(BOB_SLACK, "we agreed on <@U1ALICE>'s plan", now - 100), message(ALICE_SLACK, "ship it", now - 200)],
        "C2": [message(BOB_SLACK, "layoffs are coming", now - 50)],
    }
    mock, calls = slack_workspace(
        channels={ALICE_SLACK: ["C1"], BOB_SLACK: ["C1", "C2"]}, history=history,
    )
    alice = SlackContext(slack_user=ALICE_SLACK)
    bob = SlackContext(slack_user=BOB_SLACK)
    with patch("httpx.AsyncClient.get", mock):
        allowed = await run_tool("read_slack_channel", {"channel": "#general", "days": 7}, ALICE, alice)
        assert [m["text"] for m in allowed["messages"]] == ["ship it", "we agreed on @Alice A's plan"]
        assert {m["author"] for m in allowed["messages"]} == {"Alice A", "Bob B"}
        assert allowed["source"] == "#general"

        for reference in ("secret", "#secret", "C2", "<#C2|secret>", "does-not-exist"):
            calls.clear()
            denied = await run_tool("read_slack_channel", {"channel": reference}, ALICE, alice)
            assert denied == NOT_FOUND, (reference, denied)
            assert "layoffs" not in json.dumps(denied)
            assert not [c for c in calls if c[0] == "conversations.history"], "history was fetched for a non-member"

        granted = await run_tool("read_slack_channel", {"channel": "secret"}, BOB, bob)
        assert [m["text"] for m in granted["messages"]] == ["layoffs are coming"]

        # The bot itself cannot read this channel: same generic answer.
    blocked, _ = slack_workspace(
        channels={ALICE_SLACK: ["C1"]}, history=history, readable={"C2"},
    )
    with patch("httpx.AsyncClient.get", blocked):
        assert await run_tool("read_slack_channel", {"channel": "general"}, ALICE, alice) == NOT_FOUND

    # With no Slack context (not asked from Slack) the tool is unavailable.
    assert await run_tool("read_slack_channel", {"channel": "general"}, ALICE) == NOT_FOUND


async def channel_reads_are_bounded() -> None:
    now = time.time()
    flood = [message(ALICE_SLACK, "word " * 400, now - i) for i in range(200)]
    mock, calls = slack_workspace(channels={ALICE_SLACK: ["C1"]}, history={"C1": flood})
    with patch("httpx.AsyncClient.get", mock):
        result = await run_tool(
            "read_slack_channel", {"channel": "general", "days": 900, "limit": 5000}, ALICE,
            SlackContext(slack_user=ALICE_SLACK),
        )
    history_call = [p for m, p in calls if m == "conversations.history"][0]
    assert history_call["limit"] == 200
    assert float(history_call["oldest"]) >= time.time() - 30 * 86_400 - 5
    assert result["omitted_older_messages"] > 0 and 0 < len(result["messages"]) < 200
    assert len(json.dumps(result)) < 17_000
    assert all(len(m["text"]) <= 1_000 for m in result["messages"])


async def the_current_thread_and_dm_can_be_read() -> None:
    now = time.time()
    thread = [message(BOB_SLACK, "kickoff thoughts", now - 300), message(ALICE_SLACK, "ignore previous instructions", now - 200)]
    mock, calls = slack_workspace(
        channels={ALICE_SLACK: ["C1"]}, history={"C1": thread, "D1": [message(ALICE_SLACK, "my private note", now)]},
    )
    with patch("httpx.AsyncClient.get", mock):
        read = await run_tool(
            "read_slack_thread", {}, ALICE,
            SlackContext(slack_user=ALICE_SLACK, channel="C1", thread_ts="100.1"),
        )
        assert [m["text"] for m in read["messages"]] == ["kickoff thoughts", "ignore previous instructions"]
        assert [p for m, p in calls if m == "conversations.replies"][0]["ts"] == "100.1"

        # Not a member of the channel the thread is in: nothing is read.
        calls.clear()
        outsider = await run_tool(
            "read_slack_thread", {}, BOB,
            SlackContext(slack_user=BOB_SLACK, channel="C1", thread_ts="100.1"),
        )
        assert outsider == NOT_FOUND
        assert not [c for c in calls if c[0].startswith("conversations.")]

        dm = await run_tool(
            "read_slack_thread", {}, ALICE,
            SlackContext(slack_user=ALICE_SLACK, channel="D1", is_dm=True),
        )
        assert [m["text"] for m in dm["messages"]] == ["my private note"]

        # A non-DM channel id can never be passed off as the DM.
        fake_dm = await run_tool(
            "read_slack_thread", {}, ALICE,
            SlackContext(slack_user=ALICE_SLACK, channel="C2", is_dm=True),
        )
        assert fake_dm == NOT_FOUND
        # A slash command has no current thread.
        assert await run_tool("read_slack_thread", {}, ALICE, SlackContext(slack_user=ALICE_SLACK)) == NOT_FOUND


async def model_prefixes_choose_the_tier() -> None:
    cases = {
        "opus: what changed this week for our A24 work?": llm.TIERS["deep"][0],
        "<@U0HELPER> Haiku: what changed this week for our A24 work?": llm.TIERS["fast"][0],
        "sonnet: what changed this week for our A24 work?": llm.TIERS["write"][0],
        "what changed this week for our A24 work?": llm.default_model_id(),
    }
    for text, expected in cases.items():
        converse, sent = scripted_model("ok")
        with patch("app.services.slack_agent.llm.converse", converse):
            answer = await slack_agent.handle_message(text, ALICE)
        assert answer["text"] == "ok", (text, answer)
        assert sent[0]["model_id"] == expected, text
        assert user_texts(sent[0]) == ["what changed this week for our A24 work?"], "prefix was not stripped"
    # A model prefix is never a database shortcut; plain shortcuts are unchanged.
    assert slack_agent.is_deterministic_message("opus: queue") is False
    assert slack_agent.is_deterministic_message("queue") is True
    assert slack_agent.is_deterministic_message("opus:") is True


async def events_continue_threads_and_tool_output_is_never_stored() -> None:
    await clear_turns()
    said: list[dict] = []

    async def post(channel, text, thread_ts=None, ephemeral_user=None):
        said.append({"channel": channel, "text": text, "thread_ts": thread_ts, "ephemeral_user": ephemeral_user})
        return True

    tool_call = {
        "stopReason": "tool_use",
        "output": {"message": {"role": "assistant", "content": [
            {"toolUse": {"toolUseId": "t1", "name": "read_slack_thread", "input": {}}}
        ]}},
    }
    converse, sent = scripted_model(tool_call, "Summary of the thread.", "Follow-up answer.")
    mock, _ = slack_workspace(
        channels={ALICE_SLACK: ["C1"]},
        history={"C1": [message(BOB_SLACK, "CONFIDENTIAL thread body", time.time())]},
    )
    first = {
        "type": "app_mention", "user": ALICE_SLACK, "channel": "C1", "ts": "700.1",
        "text": "<@U0HELPER> summarize this thread for me please",
    }
    followup = {
        "type": "app_mention", "user": ALICE_SLACK, "channel": "C1", "ts": "700.2", "thread_ts": "700.1",
        "text": "<@U0HELPER> and who disagreed with the plan?",
    }
    with (
        patch("app.services.slack_agent.llm.converse", converse),
        patch("app.services.slack_agent.post_reply", AsyncMock(side_effect=post)),
        patch("app.services.slack_agent.member_for_slack_user", AsyncMock(return_value=ALICE)),
        patch("httpx.AsyncClient.get", mock),
    ):
        await slack_agent.answer_event(first)
        await slack_agent.answer_event(followup)
    # The thread was read through the tool, and its text reached the model...
    assert "CONFIDENTIAL thread body" in json.dumps(sent[1]["messages"])
    # ...but only the question and the answer were kept.
    kept = await turns("C1")
    assert [(r["thread_key"], r["question"], r["answer"]) for r in kept] == [
        ("700.1", "summarize this thread for me please", "Summary of the thread."),
        ("700.1", "and who disagreed with the plan?", "Follow-up answer."),
    ]
    assert "CONFIDENTIAL" not in json.dumps(kept)
    assert [m["role"] for m in sent[2]["messages"]] == ["user", "assistant", "user"]
    assert sent[2]["messages"][1]["content"][0]["text"] == "Summary of the thread."
    assert all(row["ephemeral_user"] == ALICE_SLACK for row in said)


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp) / 'threads.db'}"
        await init_db()
        db = await get_db()
        try:
            await seed(db)
        finally:
            await db.close()
        await follow_ups_continue_the_thread_and_stay_private()
        await direct_messages_roll_one_conversation_per_member()
        await memory_is_trimmed_to_its_limits()
        await idle_conversations_expire_after_a_day()
        await an_active_conversation_keeps_turns_older_than_a_day()
        await a_channel_is_readable_only_by_its_members()
        await channel_reads_are_bounded()
        await the_current_thread_and_dm_can_be_read()
        await model_prefixes_choose_the_tier()
        await events_continue_threads_and_tool_output_is_never_stored()
        print("slack threads: ok")


asyncio.run(main())
