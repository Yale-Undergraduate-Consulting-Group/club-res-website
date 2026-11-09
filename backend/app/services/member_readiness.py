"""What each member still has to set up before outreach works for them.

Only a connected Gmail blocks sending: mail goes out from the member's own
account. A sign-off name of their own, Slack and a project are recommended;
without the sign-off the member's Google name signs their mail. One query per
fact covers every member, so the admin view stays cheap at club scale.
"""
from __future__ import annotations

from typing import Any


async def readiness(db: Any, user_ids: list[int] | None = None) -> dict[int, dict]:
    """Setup facts for active members (all of them, or `user_ids`)."""
    where, params = "is_active = 1 AND COALESCE(role, 'standard') != 'system'", []
    if user_ids is not None:
        if not user_ids:
            return {}
        where += f" AND id IN ({','.join('?' * len(user_ids))})"
        params = list(user_ids)
    users = await (await db.execute(
        f"SELECT id, access_token, refresh_token FROM users WHERE {where}", params)).fetchall()
    if not users:
        return {}
    ids = [int(u["id"]) for u in users]
    marks = ",".join("?" * len(ids))

    async def having(sql: str, args: list) -> set[int]:
        return {int(r[0]) for r in await (await db.execute(sql, args)).fetchall()}

    signed_in = await having(f"SELECT DISTINCT user_id FROM login_log WHERE user_id IN ({marks})", ids)
    slack = await having(f"SELECT user_id FROM user_slack_tokens WHERE user_id IN ({marks})", ids)
    project = await having(f"SELECT DISTINCT user_id FROM user_project_assignments WHERE user_id IN ({marks})", ids)
    sent = await having(
        f"SELECT DISTINCT sender_id FROM outreach_messages WHERE sent_at IS NOT NULL AND sender_id IN ({marks})", ids)
    sign_off = {
        int(r["key"].split(":")[1])
        for r in await (await db.execute(
            "SELECT key FROM settings WHERE key LIKE 'member:%:sign_off_name' AND trim(COALESCE(value, '')) != ''"
        )).fetchall()
        if r["key"].split(":")[1].isdigit()
    }
    result = {}
    for u in users:
        uid = int(u["id"])
        gmail = bool(u["access_token"] or u["refresh_token"])
        result[uid] = {
            "signed_in": uid in signed_in,
            "gmail": gmail,
            "sign_off": uid in sign_off,
            "slack": uid in slack,
            "project": uid in project,
            "has_sent": uid in sent,
            "ready_to_send": gmail,
        }
    return result
