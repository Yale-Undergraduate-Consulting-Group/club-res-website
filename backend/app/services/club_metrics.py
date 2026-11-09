"""The club's outreach numbers against the semester targets (Admin → Metrics).

Every rate carries its count and denominator; a target is judged only once the
denominator reaches MIN_SAMPLE, so one bounce out of two sends does not read
as a failing semester. Timestamps are stored in two text formats (SQLite's
"YYYY-MM-DD HH:MM:SS" and ISO with "T"/"+00:00"), so every comparison goes
through datetime(). Not measured here, with the reason: positive replies
(replies are not classified) and uptime (no external probe).
"""
from __future__ import annotations

from typing import Any

MIN_SAMPLE = 5

# (key, label, comparison, target). Rates are percentages.
TARGETS = (
    ("ready_to_send", "Members ready to send (Gmail connected)", ">=", 90),
    ("searches_usable", "Searches finding 5+ people with an email", ">=", 70),
    ("drafts_sent", "Drafts that get sent", ">=", 50),
    ("released_in_week", "Contacts released within 7 days of being added", ">=", 80),
    ("bounce_rate", "Bounce rate", "<", 5),
    ("reply_rate", "Reply rate", ">=", 5),
    ("needs_reconciliation", "Sends needing manual reconciliation", "<", 1),
)


def _rate(part: int, whole: int) -> float | None:
    return round(100 * part / whole, 1) if whole else None


async def _one(db: Any, sql: str, args: tuple = ()) -> Any:
    row = await (await db.execute(sql, args)).fetchone()
    return row[0] if row else None


async def club_metrics(db: Any, days: int) -> dict[str, Any]:
    from app.services.member_readiness import readiness

    since = f"-{int(days)} days"
    ready = await readiness(db)
    members = len(ready)
    ready_n = sum(r["ready_to_send"] for r in ready.values())

    runs = await (await db.execute(
        """SELECT r.user_id, (SELECT COUNT(*) FROM yucgoutreach_prospects p WHERE p.run_id = r.id
                 AND p.email IS NOT NULL AND p.email != '' AND (p.ai_verdict IS NULL OR p.ai_verdict != 'junk')) AS usable
           FROM yucgoutreach_discovery_runs r
           WHERE r.origin = 'member' AND r.status = 'completed' AND datetime(r.created_at) >= datetime('now', ?)""",
        (since,))).fetchall()
    usable_runs = sum(1 for r in runs if r["usable"] >= 5)

    drafts = await (await db.execute(
        """SELECT g.user_id, EXISTS (SELECT 1 FROM campaign_contacts cc WHERE cc.campaign_id = g.campaign_id
                 AND cc.contact_id = g.contact_id AND cc.sent_at IS NOT NULL) AS sent
           FROM generated_emails g WHERE datetime(g.created_at) >= datetime('now', ?)""", (since,))).fetchall()
    drafts_sent = sum(r["sent"] for r in drafts)

    # A contact counts once its week is up or it was released, whichever is first.
    added = await (await db.execute(
        """SELECT released_at IS NOT NULL AND datetime(released_at) <= datetime(added_at, '+7 days') AS on_time
           FROM campaign_contacts WHERE added_at IS NOT NULL AND datetime(added_at) >= datetime('now', ?)
             AND (released_at IS NOT NULL OR datetime(added_at) <= datetime('now', '-7 days'))""",
        (since,))).fetchall()
    on_time = sum(r["on_time"] for r in added)
    waiting = await (await db.execute(
        """SELECT COUNT(*) AS n, MIN(cc.added_at) AS oldest FROM campaign_contacts cc
           JOIN campaigns c ON c.id = cc.campaign_id
           WHERE cc.status = 'pending' AND c.status IN ('draft', 'paused')""")).fetchone()

    messages = await (await db.execute(
        """SELECT m.sender_id,
                  EXISTS (SELECT 1 FROM outreach_events e WHERE e.message_id = m.id AND e.kind = 'bounced') AS bounced,
                  EXISTS (SELECT 1 FROM outreach_events e WHERE e.message_id = m.id AND e.kind = 'replied') AS replied
           FROM outreach_messages m WHERE m.sent_at IS NOT NULL AND datetime(m.sent_at) >= datetime('now', ?)""",
        (since,))).fetchall()
    sent = len(messages)
    bounced = sum(r["bounced"] for r in messages)
    replied = sum(r["replied"] for r in messages)
    # A claim normally completes within a minute; one still open after 15 is stuck.
    stuck = int(await _one(db, """SELECT COUNT(*) FROM outreach_dispatches WHERE state = 'ambiguous'
        OR (state = 'claimed' AND datetime(claimed_at) < datetime('now', '-15 minutes'))""") or 0)

    values = {
        "ready_to_send": (ready_n, members),
        "searches_usable": (usable_runs, len(runs)),
        "drafts_sent": (drafts_sent, len(drafts)),
        "released_in_week": (on_time, len(added)),
        "bounce_rate": (bounced, sent),
        "reply_rate": (replied, sent),
        "needs_reconciliation": (stuck, max(sent, stuck)),
    }
    targets = []
    for key, label, op, goal in TARGETS:
        part, whole = values[key]
        value = _rate(part, whole)
        met = None if whole < MIN_SAMPLE or value is None else (value >= goal if op == ">=" else value < goal)
        targets.append({"key": key, "label": label, "count": part, "of": whole, "value": value,
                        "target": f"{'≥' if op == '>=' else '<'} {goal}%", "met": met})

    names = {int(r["id"]): r["name"] or r["email"] for r in await (await db.execute(
        "SELECT id, name, email FROM users WHERE is_active = 1 AND COALESCE(role, 'standard') != 'system'")).fetchall()}
    per: dict[int, dict[str, Any]] = {uid: {"user_id": uid, "name": name, "searches": 0, "drafts": 0, "sent": 0,
                                            "replies": 0, "bounces": 0, "ready_to_send": ready.get(uid, {}).get("ready_to_send", False)}
                                      for uid, name in names.items()}
    for r in runs:
        if r["user_id"] in per:
            per[r["user_id"]]["searches"] += 1
    for r in drafts:
        if r["user_id"] in per:
            per[r["user_id"]]["drafts"] += 1
    for r in messages:
        row = per.get(r["sender_id"])
        if row:
            row["sent"] += 1
            row["replies"] += r["replied"]
            row["bounces"] += r["bounced"]

    return {
        "days": int(days),
        "min_sample": MIN_SAMPLE,
        "targets": targets,
        "waiting": {"contacts": int(waiting["n"] or 0), "oldest_added_at": waiting["oldest"]},
        "members": sorted(per.values(), key=lambda m: (-m["sent"], -m["searches"], str(m["name"]).lower())),
        "not_measured": ["Positive replies: replies are not classified yet.",
                         "Uptime: there is no external uptime probe."],
    }
