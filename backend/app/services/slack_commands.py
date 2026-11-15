"""`/yucg <subcommand>`: help, link, unlink, onboard, usage, requests.

The same words also work on their own in a DM with the helper or right after
an @mention, so a member never has to remember which door they used. These
answers come from the database, never from the model, and every one of them
is private to the asker.

`help`, `link` and `onboard` also answer a Slack user who has not connected a
club account yet: they are how that person finds out how to connect.
"""
from __future__ import annotations

import logging
import re

from app.database import get_db

logger = logging.getLogger(__name__)

SUBCOMMANDS = ("help", "onboard", "link", "unlink", "usage", "requests")
# Answered before the asker has a club account.
OPEN_TO_UNLINKED = {"help", "onboard", "link"}
ALIASES = {"request": "requests", "stats": "usage", "onboarding": "onboard", "commands": "help"}

MAX_REQUEST_CHARS = 1_000
REQUESTS_PER_MEMBER_PER_DAY = 5

_MENTION = re.compile(r"<@[^>]+>")

NOT_LINKED = (
    "I do not know which club account this is yet. Send `/yucg onboard` and I'll walk you "
    "through it: open YUCG Resources → Profile → Integrations, press *Connect Slack*, then try again."
)


def parse_subcommand(text: str) -> tuple[str, str] | None:
    """(subcommand, rest) when the message is one of the subcommands, else None.

    Only `requests` takes text after it, so "link me to A24's contacts" is a
    question for the assistant, not the link command.
    """
    cleaned = _MENTION.sub(" ", text or "").strip()
    if not cleaned:
        return None
    parts = cleaned.split(maxsplit=1)
    word = parts[0].lower().lstrip("/").strip("?!.,")
    if word == "yucg":  # "yucg help" typed as a message
        if len(parts) == 1:
            return "help", ""
        parts = parts[1].split(maxsplit=1)
        word = parts[0].lower().strip("?!.,")
    word = ALIASES.get(word, word)
    rest = parts[1].strip() if len(parts) > 1 else ""
    if word not in SUBCOMMANDS:
        return None
    if rest and word != "requests":
        return None
    return word, rest


def _url(path: str) -> str:
    from app.services.slack_agent import app_url
    return f"{app_url()}{path}"


def _link(label: str, path: str) -> str:
    return f"<{_url(path)}|{label}>"


def is_admin(member: dict | None) -> bool:
    return bool(member) and member.get("role") == "admin"


# --------------------------------------------------------------------- help

def help_text(member: dict | None) -> str:
    lines = [
        "*YUCG Resources in Slack*",
        "*Commands* (only you see the answers)",
        "• `/yucg help`: this list",
        "• `/yucg onboard`: a one-minute tour and your setup checklist",
        "• `/yucg link`: connect your Slack to your club account",
        "• `/yucg unlink`: disconnect it",
        "• `/yucg usage`: your AI use this month and the club budget"
        + (" (admins also see each member)" if is_admin(member) else ""),
        "• `/yucg requests <idea>`: send the club admins a feature idea",
        "",
        "*Outreach shortcuts* (instant, no AI)",
        "• `/yucg company <name>`: who we know there and where we stand",
        "• `/yucg draft <person>`: open a draft to them",
        "• `/yucg queue`: your campaigns waiting to send",
        "• `/yucg followups`: follow-ups due",
        "• `/yucg targets`: people on file nobody has written to",
        "",
        "*Ask me anything about your outreach*",
        "• DM me, mention me in a channel, or type `/yucg <question>`",
        "• _how are we doing at A24?_ · _what's queued?_ · _follow-ups?_ · _who should I write to next?_",
        "• In a thread: _summarize this thread_",
        "",
        f"The club's other tools (Survey builder, Market sentiment) are on {_link('YUCG Resources', '/')}.",
        "I only read club data you can already see on the website. I never send mail or change anything.",
    ]
    if not member:
        lines.insert(0, "_Your Slack is not linked yet. Start with `/yucg link`._\n")
    return "\n".join(lines)


# --------------------------------------------------------------------- link

def link_text(member: dict | None) -> str:
    if member:
        who = member.get("name") or member.get("email") or "your club account"
        email = f" ({member['email']})" if member.get("email") and member.get("name") else ""
        return (
            f"You're linked as *{who}*{email}.\n"
            "To disconnect, send `/yucg unlink`."
        )
    return (
        "*Link your Slack to YUCG Resources*\n"
        f"1. Open {_link('Profile → Integrations', '/profile?view=integrations')} and sign in.\n"
        "2. Press *Connect Slack* and approve.\n"
        "3. Come back and send `/yucg onboard`.\n"
        "Once linked, I can answer questions about the contacts, campaigns and drafts you can see."
    )


async def unlink(member: dict) -> str:
    """Disconnect the member's Slack, exactly as the website's Disconnect does."""
    db = await get_db()
    try:
        await db.execute("DELETE FROM user_slack_tokens WHERE user_id = ?", (int(member["id"]),))
        await db.commit()
    finally:
        await db.close()
    return (
        "Unlinked. I no longer know which club account this Slack user is.\n"
        "Send `/yucg link` to connect again."
    )


# ------------------------------------------------------------------ onboard

async def onboard_text(member: dict | None) -> str:
    from app.services.member_readiness import readiness

    facts: dict = {}
    if member:
        db = await get_db()
        try:
            facts = (await readiness(db, [int(member["id"])])).get(int(member["id"])) or {}
        finally:
            await db.close()

    def step(done: bool, label: str, path: str, why: str) -> str:
        return f"{':white_check_mark:' if done else ':white_large_square:'} {_link(label, path)}: {why}"

    return "\n".join([
        "*Welcome to YUCG Resources* :wave:",
        "I'm the club's assistant in Slack. Here's the one-minute tour.",
        "",
        "*1. Your setup*",
        step(bool(member), "Link Slack", "/profile?view=integrations",
             "so I know which club account you are (`/yucg link`)."),
        step(bool(facts.get("gmail")), "Connect your Gmail", "/profile?view=integrations",
             "outreach goes out from your own account."),
        step(bool(facts.get("sign_off")), "Set your email sign-off", "/profile?view=settings",
             "your name, role and links under every email."),
        "",
        "*2. Try one of these*",
        "• `/yucg how are we doing at A24?`",
        "• DM me _who should I write to next?_",
        "• In a channel thread: _mention me and ask me to summarize this thread_",
        "",
        "*3. Good to know*",
        "• Answers in channels are visible only to you.",
        "• I only read what you can already see on the website, and I never send mail.",
        "• I can be wrong. Check names and numbers before anything goes to a client.",
        "• `/yucg help` lists every command. `/yucg requests <idea>` tells the admins what you'd like next.",
    ])


# -------------------------------------------------------------------- usage

_MONTH = "created_at >= strftime('%Y-%m-01 00:00:00','now')"
_COST = (
    "COALESCE(json_extract(details_json,'$.cost_usd'),"
    "json_extract(details_json,'$.estimated_cost_usd'),0)"
)


async def usage_text(member: dict) -> str:
    from app.services.generation_policy import monthly_budget_usd

    db = await get_db()
    try:
        mine = await (await db.execute(
            f"""SELECT COUNT(*) AS calls, COALESCE(SUM({_COST}),0) AS cost
                FROM usage_events WHERE event_type='bedrock_reserved' AND user_id=? AND {_MONTH}""",
            (int(member["id"]),),
        )).fetchone()
        club = await (await db.execute(
            f"""SELECT COUNT(*) AS calls, COALESCE(SUM({_COST}),0) AS cost
                FROM usage_events WHERE event_type='bedrock_reserved' AND {_MONTH}"""
        )).fetchone()
        top = []
        if is_admin(member):
            top = await (await db.execute(
                f"""SELECT COALESCE(u.name, u.email, 'Background jobs') AS who,
                           COUNT(*) AS calls, COALESCE(SUM({_COST}),0) AS cost
                    FROM usage_events e LEFT JOIN users u ON u.id = e.user_id
                    WHERE e.event_type='bedrock_reserved' AND e.{_MONTH}
                    GROUP BY e.user_id ORDER BY cost DESC, calls DESC LIMIT 10"""
            )).fetchall()
    finally:
        await db.close()

    try:
        budget = monthly_budget_usd()
    except Exception:
        budget = 0.0
    spent = float(club["cost"] or 0)
    share = f"{min(100, round(100 * spent / budget))}%" if budget > 0 else "unknown"
    lines = [
        "*Your AI use this month*",
        f"• {int(mine['calls'] or 0)} AI calls",
        f"*Club AI budget*: {share} used this month",
    ]
    if is_admin(member):
        lines[1] += f" (${float(mine['cost'] or 0):.2f})"
        lines.append(f"• ${spent:.2f} of ${budget:.2f} across {int(club['calls'] or 0)} calls")
        if top:
            lines.append("*By member* (admins only)")
            lines += [f"• {row['who']}: {int(row['calls'])} calls, ${float(row['cost']):.2f}" for row in top]
    lines.append("_Counts every AI call made for you, on the website and in Slack._")
    return "\n".join(lines)


# ----------------------------------------------------------------- requests

async def save_request(member: dict, slack_user: str, idea: str) -> tuple[str, int | None]:
    """(reply, saved request id). Delivery to admins happens afterwards."""
    idea = re.sub(r"<!(?:channel|here|everyone)[^>]*>", "", idea or "").strip()
    if not idea:
        return "Add your idea after the command, for example: `/yucg requests show reply rates per campaign`", None
    if len(idea) > MAX_REQUEST_CHARS:
        return f"That's {len(idea):,} characters; please keep it under {MAX_REQUEST_CHARS:,}.", None
    db = await get_db()
    try:
        row = await (await db.execute(
            "SELECT COUNT(*) AS n FROM slack_feature_requests WHERE user_id=? AND created_at >= datetime('now','-1 day')",
            (int(member["id"]),),
        )).fetchone()
        if int(row["n"] or 0) >= REQUESTS_PER_MEMBER_PER_DAY:
            return (
                f"You've sent {REQUESTS_PER_MEMBER_PER_DAY} requests in the last day, which is the limit. "
                "Try again tomorrow."
            ), None
        cursor = await db.execute(
            "INSERT INTO slack_feature_requests (user_id, slack_user_id, text) VALUES (?,?,?)",
            (int(member["id"]), slack_user or None, idea),
        )
        await db.commit()
        request_id = int(cursor.lastrowid)
    finally:
        await db.close()
    quoted = "\n".join(f"> {line}" for line in idea.splitlines())
    return f"Thanks! I've saved your request and sent it to the club admins:\n{quoted}", request_id


async def deliver_request(request_id: int) -> int:
    """DM every active admin who has linked Slack. Returns how many were reached."""
    from app.services.slack_agent import post_reply

    db = await get_db()
    try:
        request = await (await db.execute(
            """SELECT r.text, r.slack_user_id, COALESCE(u.name, u.email) AS who
               FROM slack_feature_requests r JOIN users u ON u.id = r.user_id WHERE r.id=?""",
            (request_id,),
        )).fetchone()
        admins = await (await db.execute(
            """SELECT t.user_slack_id FROM users u
               JOIN user_slack_tokens t ON t.user_id = u.id
               WHERE u.role='admin' AND u.is_active=1 AND t.user_slack_id IS NOT NULL"""
        )).fetchall()
    finally:
        await db.close()
    if not request:
        return 0
    sender = f"<@{request['slack_user_id']}>" if request["slack_user_id"] else request["who"]
    quoted = "\n".join(f"> {line}" for line in str(request["text"]).splitlines())
    message = f":bulb: *Feature request from {sender}* ({request['who']})\n{quoted}"
    reached = 0
    for admin in admins:
        try:
            # A user id as the channel posts in that admin's DM with the app.
            if await post_reply(str(admin["user_slack_id"]), message):
                reached += 1
        except Exception:
            logger.exception("Could not deliver Slack feature request %s", request_id)
    logger.info("Slack feature request %s delivered to %s/%s admins", request_id, reached, len(admins))
    return reached


async def create_tables(db) -> None:
    """Migration core:2 - `/yucg requests <idea>`, kept even when no admin could be reached."""
    await db.execute(
        """CREATE TABLE IF NOT EXISTS slack_feature_requests (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               user_id INTEGER NOT NULL REFERENCES users(id),
               slack_user_id TEXT,
               text TEXT NOT NULL,
               created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_slack_feature_requests_user ON slack_feature_requests(user_id, created_at)"
    )


# ------------------------------------------------------------------ routing

async def answer(name: str, rest: str, member: dict | None, slack_user: str) -> tuple[str, int | None]:
    """(reply text, saved request id to deliver or None)."""
    if not member and name not in OPEN_TO_UNLINKED:
        return NOT_LINKED, None
    if name == "help":
        return help_text(member), None
    if name == "link":
        return link_text(member), None
    if name == "onboard":
        return await onboard_text(member), None
    if name == "unlink":
        return await unlink(member), None
    if name == "usage":
        return await usage_text(member), None
    return await save_request(member, slack_user, rest)

