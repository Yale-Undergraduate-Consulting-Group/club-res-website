"""The club's Slack helper: ask it anything, get club state and the next door.

Members live in Slack; the tools live behind seven doors on the website. The
agent's whole job is to collapse that: ask about a company or your queue, and
it answers from the shared database and hands back the one door that is
actually the next step — Find people when nobody is on file, Drafts when
people are on file but nobody has been written to, Pipeline when replies are
what is outstanding.

Deliberately deterministic. It reads the database and picks a door; it does
not call a model, and it never sends mail or mutates a campaign. A Slack
message is a hallway conversation, and nothing in the hallway should be able
to mail a client.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import time
from urllib.parse import quote

from app.database import get_db

# Slack signs every request; anything older than this is a replay.
SIGNATURE_MAX_AGE_SECONDS = 300


def app_url() -> str:
    return (os.getenv("FRONTEND_URL") or "http://localhost:5173").strip().rstrip("/")


def verify_slack_signature(body: bytes, timestamp: str, signature: str) -> bool:
    """True when this request really came from Slack, recently.

    Without the signing secret configured nothing is accepted: an unsigned
    endpoint on the public CloudFront door would let anyone read club state.
    """
    secret = (os.getenv("SLACK_SIGNING_SECRET") or "").strip()
    if not secret or not timestamp or not signature:
        return False
    try:
        age = abs(time.time() - float(timestamp))
    except (TypeError, ValueError):
        return False
    if age > SIGNATURE_MAX_AGE_SECONDS:
        return False
    expected = "v0=" + hmac.new(
        secret.encode(), b"v0:" + timestamp.encode() + b":" + body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


async def member_for_slack_user(slack_user_id: str) -> dict | None:
    """The club member behind a Slack id, or None.

    Identity comes from the member's own install, so the agent can never
    answer for someone who has not connected their account.
    """
    if not slack_user_id:
        return None
    db = await get_db()
    try:
        row = await (await db.execute(
            """SELECT u.id, u.email, u.name FROM user_slack_tokens t
               JOIN users u ON u.id = t.user_id AND u.is_active = 1
               WHERE t.user_slack_id = ?""",
            (slack_user_id,),
        )).fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


def _ephemeral(text: str) -> dict:
    """Only the member who typed the command sees the answer."""
    return {"response_type": "ephemeral", "text": text}


def _link(label: str, path: str) -> str:
    return f"<{app_url()}{path}|{label}>"


HELP = (
    "*YUCG helper.* Mention me or send me a direct message — plain English is fine.\n"
    "• _how are we doing at A24?_ — who is on file there, and the next step\n"
    "• _draft something for Jean Bartik_ — opens Drafts for them\n"
    "• _what's queued?_ — your campaigns waiting to send\n"
    "• _follow-ups?_ — recipients mid-sequence who have not replied\n"
    "• _who should I write to next?_ — companies with people nobody has written to\n"
    "I read the club database and hand you the right door. I never send mail."
)


async def _company(member: dict, name: str) -> dict:
    if not name:
        return _ephemeral("Name a company: `/yucg company A24`")
    db = await get_db()
    try:
        counts = await (await db.execute(
            """SELECT COUNT(*) AS on_file,
                      SUM(CASE WHEN cc.sent_at IS NOT NULL THEN 1 ELSE 0 END) AS mailed,
                      SUM(CASE WHEN cc.replied_at IS NOT NULL THEN 1 ELSE 0 END) AS replied
               FROM contacts c
               LEFT JOIN campaign_contacts cc ON cc.contact_id = c.id
               WHERE LOWER(TRIM(c.company)) = ?
                 AND (c.owner_id IS NULL OR c.owner_id = ?)""",
            (name.strip().lower(), member["id"]),
        )).fetchone()
    finally:
        await db.close()
    on_file = int(counts["on_file"] or 0)
    mailed = int(counts["mailed"] or 0)
    replied = int(counts["replied"] or 0)
    company_param = quote(name.strip())

    # The routing is the point: the same question gets a different door
    # depending on what the database already knows.
    if on_file == 0:
        return _ephemeral(
            f"*{name.strip()}* — nobody on file yet.\n"
            f"Next: {_link('Find people there', f'/scraper?view=company&company={company_param}')}"
        )
    if mailed == 0:
        return _ephemeral(
            f"*{name.strip()}* — {on_file} on file, none written to yet.\n"
            f"Next: {_link('Write to them', f'/studio?q={company_param}')}"
            f" · {_link('See the people', f'/scraper?view=company&company={company_param}')}"
        )
    return _ephemeral(
        f"*{name.strip()}* — {on_file} on file, {mailed} mailed, {replied} replied.\n"
        f"Next: {_link('Check replies in Pipeline', '/outreach')}"
        f" · {_link('Write to someone new', f'/studio?q={company_param}')}"
    )


async def _draft(member: dict, who: str) -> dict:
    target = f"/studio?q={quote(who.strip())}" if who.strip() else "/studio"
    label = f"Drafts for {who.strip()}" if who.strip() else "Drafts"
    return _ephemeral(f"Next: {_link(label, target)}")


async def _queue(member: dict, _: str) -> dict:
    db = await get_db()
    try:
        rows = await (await db.execute(
            """SELECT c.id, c.name, c.status,
                      SUM(CASE WHEN cc.status = 'pending' THEN 1 ELSE 0 END) AS pending,
                      SUM(CASE WHEN cc.status = 'failed' THEN 1 ELSE 0 END) AS failed
               FROM campaigns c LEFT JOIN campaign_contacts cc ON cc.campaign_id = c.id
               WHERE c.owner_user_id = ? AND c.status IN ('draft','releasing','paused','needs_attention')
               GROUP BY c.id ORDER BY c.updated_at DESC LIMIT 5""",
            (member["id"],),
        )).fetchall()
    finally:
        await db.close()
    if not rows:
        return _ephemeral(
            "Nothing of yours is waiting to send.\n"
            f"Next: {_link('Build a campaign', '/scraper?view=company')}"
        )
    lines = []
    for row in rows:
        failed = int(row["failed"] or 0)
        detail = f", {failed} failed" if failed else ""
        open_link = _link("open", f"/campaigns/{row['id']}")
        lines.append(
            f"• *{row['name']}* — {row['status']}, {int(row['pending'] or 0)} queued{detail} · {open_link}"
        )
    return _ephemeral("*Your campaigns waiting to send*\n" + "\n".join(lines))


async def _followups(member: dict, _: str) -> dict:
    db = await get_db()
    try:
        row = await (await db.execute(
            """SELECT COUNT(*) AS due FROM campaign_contacts cc
               JOIN campaigns c ON c.id = cc.campaign_id
               WHERE c.sender_user_id = ? AND c.sequence_id IS NOT NULL
                 AND cc.status = 'sent' AND cc.replied_at IS NULL
                 AND cc.last_sequence_sent_at IS NOT NULL""",
            (member["id"],),
        )).fetchone()
    finally:
        await db.close()
    due = int(row["due"] or 0)
    if due == 0:
        return _ephemeral(
            "No follow-ups are waiting on a sequence of yours.\n"
            f"Next: {_link('Follow-ups', '/outreach?tab=followups')}"
        )
    return _ephemeral(
        f"{due} recipient(s) are in a follow-up sequence and have not replied. "
        "The daily job sends the next step when it is due.\n"
        f"Next: {_link('See the schedule', '/outreach?tab=followups')}"
    )


async def _targets(member: dict, _: str) -> dict:
    db = await get_db()
    try:
        rows = await (await db.execute(
            """SELECT TRIM(c.company) AS company, COUNT(*) AS people
               FROM contacts c
               WHERE c.company IS NOT NULL AND TRIM(c.company) <> ''
                 AND (c.owner_id IS NULL OR c.owner_id = ?)
                 AND NOT EXISTS (
                     SELECT 1 FROM campaign_contacts cc
                     WHERE cc.contact_id = c.id AND cc.sent_at IS NOT NULL
                 )
               GROUP BY LOWER(TRIM(c.company))
               ORDER BY people DESC, company ASC LIMIT 5""",
            (member["id"],),
        )).fetchall()
    finally:
        await db.close()
    if not rows:
        return _ephemeral(
            "Everyone on file has been written to.\n"
            f"Next: {_link('Find more companies', '/scraper?view=register')}"
        )
    lines = []
    for row in rows:
        write_link = _link("write to them", f"/studio?q={quote(row['company'])}")
        lines.append(f"• *{row['company']}* — {int(row['people'])} on file · {write_link}")
    return _ephemeral("*People on file nobody has written to*\n" + "\n".join(lines))


ROUTES = {
    "company": _company,
    "find": _company,
    "draft": _draft,
    "write": _draft,
    "queue": _queue,
    "campaigns": _queue,
    "followups": _followups,
    "follow-ups": _followups,
    "targets": _targets,
}


# Words a member actually types, mapped to the door they mean. Checked in
# order, so "who should I write to next" reaches the target list rather than
# the drafting screen. Keywords here must not be words that appear in company
# names — "nobody" once sent "how are we doing at Nobody Ltd" to the target
# list instead of to that company.
INTENT_WORDS: list[tuple[tuple[str, ...], str]] = [
    (("help", "what can you do", "commands"), "help"),
    (("next", "targets", "target list", "untouched", "not written"), "targets"),
    (("follow-up", "follow up", "followup", "sequence"), "followups"),
    (("queue", "queued", "campaign", "waiting to send", "release"), "queue"),
    (("draft", "write to", "compose", "email for"), "draft"),
    (("company", "at ", "who is at", "who's at", "people at", "contacts at", "find"), "company"),
]

# Routes that answer about the member's own work take no subject.
SUBJECTLESS = {"queue", "followups", "targets", "help"}

_MENTION = re.compile(r"<@[^>]+>")
# Words that are never the name of a company or a person, so whatever a
# member wraps their question in, the subject survives.
_FILLER = re.compile(
    r"\b(?:hi|hey|hello|please|can|could|you|we|us|i|me|my|the|a|an|for|about|how|are|doing|"
    r"at|with|on|of|to|any|is|there|do|have|who|should|next|what|whats|s|something|anything|"
    r"note|message|email|mail|draft|drafts|people|person|contacts|contact|someone|new|going)\b",
    re.I,
)


def parse_intent(text: str) -> tuple[str, str]:
    """Turn a sentence into (route, argument).

    A helper in Slack is asked questions, not given verbs, so "how are we
    doing at A24?" has to reach the same place as `/yucg company A24`. The
    match is keyword order, not a model: the answer must be the same every
    time, and a wrong guess costs a member their next step.
    """
    cleaned = _MENTION.sub(" ", text or "").strip()
    if not cleaned:
        return "help", ""
    lowered = cleaned.lower()

    # An explicit verb still wins, so the slash-command vocabulary keeps
    # working in conversation.
    first = lowered.split(maxsplit=1)[0].strip("?,.!")
    if first in ROUTES:
        rest = cleaned.split(maxsplit=1)
        if first in SUBJECTLESS:
            return first, ""
        # Still pull the subject out of the remainder: "draft something for
        # Jean Bartik" must reach Jean Bartik, not "something for Jean".
        return first, (_subject(rest[1]) if len(rest) > 1 else "")

    for words, route in INTENT_WORDS:
        if any(word in lowered for word in words):
            return route, ("" if route in SUBJECTLESS else _subject(cleaned))
    return "help", ""


def _subject(text: str) -> str:
    """What is left once the question words are removed: the company or person."""
    # Quoted names win outright: "draft for 'Kane Pixels / Backrooms IP'".
    quoted = re.search(r"[\"'“](.+?)[\"'”]", text)
    if quoted:
        return quoted.group(1).strip()
    stripped = _FILLER.sub(" ", text)
    stripped = re.sub(r"[?!.,]", " ", stripped)
    # Keep original casing for the words that survived, so company names read
    # as themselves in the reply.
    survivors = {word.lower() for word in stripped.split() if word.strip()}
    kept = [word for word in text.split() if word.lower().strip("?!.,") in survivors]
    return " ".join(kept).strip(" ?.!,")


ROUTES = {
    "company": _company,
    "find": _company,
    "draft": _draft,
    "write": _draft,
    "queue": _queue,
    "campaigns": _queue,
    "followups": _followups,
    "follow-ups": _followups,
    "targets": _targets,
}


async def handle_message(text: str, member: dict) -> dict:
    """Answer one question from a member, however it was phrased."""
    route, argument = parse_intent(text)
    handler = ROUTES.get(route)
    if handler is None:
        return _ephemeral(HELP)
    return await handler(member, argument)


async def already_handled(event_id: str) -> bool:
    """True when this event has been answered before.

    Slack retries an event if the acknowledgement is slow, and the live box
    replaces its container, so the record has to outlive the process or a
    member gets the same answer three times.
    """
    if not event_id:
        return False
    db = await get_db()
    try:
        await db.execute("DELETE FROM slack_events WHERE created_at < ?", (time.time() - 86400,))
        try:
            await db.execute(
                "INSERT INTO slack_events (event_id, created_at) VALUES (?, ?)",
                (event_id, time.time()),
            )
        except Exception:
            await db.commit()
            return True
        await db.commit()
        return False
    finally:
        await db.close()


async def post_reply(channel: str, text: str, thread_ts: str | None = None) -> bool:
    """Say it in Slack, with a token that is valid right now."""
    import httpx

    from app.services.slack_tokens import (
        SlackReauthorizationRequired, workspace_access_token,
    )

    if not channel or not text:
        return False
    try:
        token = await workspace_access_token()
    except SlackReauthorizationRequired:
        token = None
    if not token:
        return False
    payload: dict[str, object] = {"channel": channel, "text": text}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            "https://slack.com/api/chat.postMessage",
            json=payload,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
    return bool(response.json().get("ok"))


async def answer_event(event: dict) -> bool:
    """Answer a mention or a direct message. Returns whether we said anything."""
    text = str(event.get("text") or "")
    channel = str(event.get("channel") or "")
    slack_user = str(event.get("user") or "")
    # A mention inside a thread is answered in that thread; a direct message
    # is answered in the DM itself.
    thread_ts = event.get("thread_ts") if event.get("type") == "app_mention" else None
    member = await member_for_slack_user(slack_user)
    if not member:
        return await post_reply(
            channel,
            "I do not know which club account this is. Open YUCG Outreach → Profile → "
            "Integrations and press *Connect Slack*, then ask me again.",
            thread_ts,
        )
    answer = await handle_message(text, member)
    return await post_reply(channel, str(answer.get("text") or HELP), thread_ts)
