"""Admin → Members → Send setup reminders: a Slack DM from the club bot to each
member with something still missing from their setup.

What is missing is always computed here, from the database, at the moment of
the preview and again at the moment of sending; a client only names members.
Every attempt is recorded in ``setup_reminders`` (migration core:8) and the
audit log. No AI writes the message.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.database import get_db
from app.services import slack_dm
from app.services.audit_service import log_audit
from app.services.member_readiness import readiness

REMINDED_WITHIN_DAYS = 7
RETENTION_DAYS = 365
MAX_MEMBERS_PER_REQUEST = 100
_STAMP = "%Y-%m-%d %H:%M:%S"

# key, admin preview label, line in the member's DM, Profile tab holding the fix.
# Gmail first: it is the only item that blocks sending.
ITEMS = (
    ("gmail", "Gmail", "Connect your Gmail (required: mail goes out from your own account)", "integrations"),
    ("role", "Signature role", "Add your role to your email signature", "settings"),
    ("linkedin", "LinkedIn", "Add your LinkedIn link to your email signature", "settings"),
    ("phone", "Phone", "Add your phone number to your email signature", "settings"),
    ("sign_off", "Sign-off name", "Set the name you want to sign emails with", "settings"),
    ("slack", "Slack", "Connect your Slack", "integrations"),
)
_BY_KEY = {item[0]: item for item in ITEMS}
_SIGNATURE_FIELDS = {"role": "sign_off_role", "linkedin": "sign_off_linkedin", "phone": "sign_off_phone"}


class SlackNotConnected(RuntimeError):
    """No usable club bot token."""


async def _bot_token() -> str | None:
    from app.services.slack_tokens import workspace_access_token

    return await workspace_access_token()


async def _missing(db: Any, user_ids: list[int] | None = None) -> dict[int, dict]:
    """{member id: {name, email, missing: [keys in ITEMS order]}} for incomplete active members only."""
    facts = await readiness(db, user_ids)
    if not facts:
        return {}
    marks = ",".join("?" * len(facts))
    people = {
        int(r["id"]): r
        for r in await (await db.execute(
            f"SELECT id, name, email FROM users WHERE id IN ({marks})", list(facts))).fetchall()
    }
    filled: dict[int, set[str]] = {}
    for r in await (await db.execute(
            "SELECT key, value FROM settings WHERE key LIKE 'member:%:sign_off_%' AND trim(COALESCE(value, '')) != ''"
    )).fetchall():
        _, uid, field = r["key"].split(":", 2)
        if uid.isdigit():
            filled.setdefault(int(uid), set()).add(field)
    result = {}
    for uid, f in facts.items():
        absent = {
            "gmail": not f["gmail"],
            "sign_off": not f["sign_off"],
            "slack": not f["slack"],
            **{key: field not in filled.get(uid, set()) for key, field in _SIGNATURE_FIELDS.items()},
        }
        missing = [key for key, *_ in ITEMS if absent[key]]
        if missing:
            result[uid] = {"name": people[uid]["name"] or "", "email": people[uid]["email"], "missing": missing}
    return result


async def _last_sent(db: Any, user_ids: list[int]) -> dict[int, str]:
    if not user_ids:
        return {}
    marks = ",".join("?" * len(user_ids))
    rows = await (await db.execute(
        f"SELECT user_id, MAX(created_at) AS at FROM setup_reminders WHERE result = 'sent' AND user_id IN ({marks})"
        " GROUP BY user_id", user_ids)).fetchall()
    return {int(r["user_id"]): r["at"] for r in rows}


async def preview() -> dict:
    """Who is missing what, whether Slack can be used, and who was reminded this week."""
    db = await get_db()
    try:
        incomplete = await _missing(db)
        last = await _last_sent(db, list(incomplete))
    finally:
        await db.close()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=REMINDED_WITHIN_DAYS)).strftime(_STAMP)
    members = [
        {
            "user_id": uid,
            "name": m["name"],
            "email": m["email"],
            "missing": [{"key": k, "label": _BY_KEY[k][1]} for k in m["missing"]],
            "reminded_at": last[uid] if last.get(uid, "") >= cutoff else None,
        }
        for uid, m in incomplete.items()
    ]
    members.sort(key=lambda m: (m["name"] or m["email"]).lower())
    return {"slack_connected": bool(await _bot_token()), "members": members}


def message(name: str, missing: list[str]) -> str:
    """The DM: first name, only what is missing, a link to where each is fixed."""
    from app.services.slack_agent import app_url

    first = (name or "").strip().split(" ")[0] or "there"
    base = app_url()
    lines = [f"Hi {first}! A few things are still missing from your YUCG Resources setup:", ""]
    for key in missing:
        _, _, line, tab = _BY_KEY[key]
        lines.append(f"• {line}: <{base}/profile?tab={tab}|Profile → {'Integrations' if tab == 'integrations' else 'Email sign-off'}>")
    lines += ["", "Each takes a minute. Thank you!"]
    return "\n".join(lines)


async def _record(admin_id: int, uid: int, items: list[str], status: str, error: str | None) -> None:
    now = datetime.now(timezone.utc)
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO setup_reminders (user_id, items, result, error, admin_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (uid, ",".join(items), status, error, admin_id, now.strftime(_STAMP)),
        )
        # Retention: a year of attempts is kept; older rows go with the next one written.
        await db.execute("DELETE FROM setup_reminders WHERE created_at < ?",
                         ((now - timedelta(days=RETENTION_DAYS)).strftime(_STAMP),))
        await db.commit()
    finally:
        await db.close()
    await log_audit(admin_id, "setup_reminder", "user", str(uid),
                    f"{status}{f' ({error})' if error else ''}: {','.join(items)}")


async def send(user_ids: list[int], admin_id: int) -> list[dict]:
    """DM each named member what is missing now. One result per id, in order:
    sent, not_found (not in the Slack workspace), failed (with Slack's error
    code), or skipped (nothing missing, or not an active member)."""
    token = await _bot_token()
    if not token:
        raise SlackNotConnected("Slack is not connected: the club bot has no usable token.")
    ids = list(dict.fromkeys(int(i) for i in user_ids))
    db = await get_db()
    try:
        incomplete = await _missing(db, ids)
    finally:
        await db.close()
    results = []
    async with slack_dm.new_client() as client:
        for uid in ids:
            member = incomplete.get(uid)
            if not member:
                results.append({"user_id": uid, "status": "skipped", "error": None, "items": []})
                continue
            await slack_dm.pace()
            slack_id, error = await slack_dm.user_id_for_email(client, token, member["email"])
            if slack_id:
                error = await slack_dm.post_dm(client, token, slack_id, message(member["name"], member["missing"]))
            status = "sent" if not error else "not_found" if error == "users_not_found" else "failed"
            await _record(admin_id, uid, member["missing"], status, error)
            results.append({"user_id": uid, "status": status, "error": None if status != "failed" else error,
                            "items": member["missing"]})
    return results


async def create_tables(db: Any) -> None:
    """Migration core:8. Admin-readable log of reminder attempts; rows older than a year may be deleted."""
    await db.execute(
        """CREATE TABLE IF NOT EXISTS setup_reminders (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               user_id INTEGER NOT NULL,
               items TEXT NOT NULL,
               result TEXT NOT NULL,
               error TEXT,
               admin_id INTEGER NOT NULL,
               created_at TEXT NOT NULL)"""
    )
    await db.execute("CREATE INDEX IF NOT EXISTS idx_setup_reminders_user ON setup_reminders (user_id, created_at)")
