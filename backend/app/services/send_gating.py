"""Prove one mailbox at a company before mailing the rest of it.

Most addresses this tool sends to were never observed anywhere: they were
built from a learned or guessed format. INBOX_VERIFY_MODE=mx only proves the
domain accepts mail, never that the mailbox exists, so a wrong format passes
every check and is only discovered by the bounce.

That is survivable for one address and damaging for thirty at once: a batch of
hard bounces to a single domain is exactly the pattern spam filters score
against the sending account, and the account here is a member's real Gmail.

So the first address to an unproven company goes alone. What happens next is
already wired: gmail_reply_sync runs every two minutes, marks a bounced row
and feeds the outcome back into the format's confidence via
record_send_outcome. This module only decides who is allowed to go now.

A company is proven when some format for its mail host has a delivery behind
it (verified_samples) or a member stated it outright. Otherwise the campaign's
own history for that host decides: a probe in flight holds the rest, a bounced
probe stops the company and needs a human, and a probe that has been quiet for
the grace period releases the rest.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

HELD_PROBE_IN_FLIGHT = "probe_in_flight"
HELD_PROBE_BOUNCED = "probe_bounced"
HELD_TEAMMATE_SENT = "teammate_already_wrote"


def teammate_quiet_days() -> int:
    """How long another member's email to a person blocks a second one.

    A term is roughly twelve weeks, so ninety days means a recipient hears
    from the club at most once per member per term unless someone overrides.
    """
    try:
        return max(1, min(int(os.getenv("TEAMMATE_QUIET_DAYS", "90") or 90), 365))
    except ValueError:
        return 90


async def _mailed_by_teammates(db, rows: list, sender_id: int | None) -> dict[str, str]:
    """Recipients another member has already written to, and who wrote.

    Keyed on the address rather than the company, because that is the thing
    the recipient experiences. Two members writing to two different people at
    one company is fine and often deliberate; two members writing to the same
    person is the club looking disorganised.
    """
    emails = sorted({(row["email"] or "").strip().lower() for row in rows if row.get("email")})
    if not emails:
        return {}
    placeholders = ",".join("?" * len(emails))
    found = await (await db.execute(
        f"""SELECT lower(c.email) AS email, COALESCE(u.name, u.email) AS who, cc.sent_by_user_id
            FROM campaign_contacts cc
            JOIN contacts c ON c.id = cc.contact_id
            JOIN users u ON u.id = cc.sent_by_user_id
            WHERE lower(c.email) IN ({placeholders})
              AND cc.sent_at IS NOT NULL
              AND cc.sent_at > datetime('now', ?)""",
        (*emails, f"-{teammate_quiet_days()} days"),
    )).fetchall()
    # Only a *teammate's* send blocks. A member re-contacting someone they
    # wrote to themselves is a follow-up, which is the normal case: pending
    # rows carry no sender yet, so the sender has to come from the campaign
    # rather than from the rows.
    return {
        row["email"]: row["who"]
        for row in found
        if sender_id is None or int(row["sent_by_user_id"]) != int(sender_id)
    }


def grace_minutes() -> int:
    """How long a delivered probe stays unconfirmed before the rest follow.

    Long enough for a hard bounce to come back (they arrive in seconds to
    minutes), short enough that a member does not wait a working day.
    """
    try:
        value = int(os.getenv("MAILBOX_PROOF_GRACE_MINUTES", "45") or 45)
    except ValueError:
        return 45
    return max(1, min(value, 24 * 60))


def mail_host(email: str) -> str:
    from app.services.contact_scraper import normalize_domain

    if not email or "@" not in email:
        return ""
    return normalize_domain(email.rsplit("@", 1)[-1])


async def proven_hosts(db, hosts: Iterable[str]) -> set[str]:
    """Hosts whose format is backed by a real delivery or a member's word."""
    wanted = sorted({h for h in hosts if h})
    if not wanted:
        return set()
    placeholders = ",".join("?" * len(wanted))
    rows = await (await db.execute(
        f"""SELECT company_domain, verified_samples, sources_json
            FROM company_email_patterns WHERE company_domain IN ({placeholders})""",
        wanted,
    )).fetchall()
    proven: set[str] = set()
    for row in rows:
        if int(row["verified_samples"] or 0) > 0:
            proven.add(row["company_domain"])
            continue
        try:
            sources = json.loads(row["sources_json"] or "[]")
        except (TypeError, ValueError):
            sources = []
        if any(str(s).startswith("member:") for s in sources):
            proven.add(row["company_domain"])
    return proven


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


async def _host_history(db, campaign_id: int) -> dict[str, dict[str, Any]]:
    """What this campaign has already attempted, per mail host."""
    rows = await (await db.execute(
        """SELECT cc.status, cc.sent_at, c.email
           FROM campaign_contacts cc JOIN contacts c ON c.id = cc.contact_id
           WHERE cc.campaign_id = ? AND cc.status IN ('sending','sent','bounced','replied')""",
        (campaign_id,),
    )).fetchall()
    history: dict[str, dict[str, Any]] = {}
    for row in rows:
        host = mail_host(row["email"] or "")
        if not host:
            continue
        entry = history.setdefault(host, {"in_flight": False, "bounced": False, "last_sent": None, "answered": False})
        status = row["status"]
        if status == "sending":
            entry["in_flight"] = True
        elif status == "bounced":
            entry["bounced"] = True
        elif status == "replied":
            # A human answered, so the mailbox is real regardless of the clock.
            entry["answered"] = True
        sent_at = _parse_ts(row["sent_at"])
        if sent_at and (entry["last_sent"] is None or sent_at > entry["last_sent"]):
            entry["last_sent"] = sent_at
    return history


async def select_sendable(db, campaign_id: int, rows: list,
                          sender_id: int | None = None) -> tuple[list, list[dict[str, Any]]]:
    """Split claimable rows into those allowed to send now and those held.

    Rows are returned in their original order so the queue stays first-in
    first-out within a company.
    """
    rows = list(rows)
    if not rows:
        return [], []
    by_id = {}
    for row in rows:
        by_id[row["id"]] = mail_host(row["email"] or "")
    proven = await proven_hosts(db, by_id.values())
    history = await _host_history(db, campaign_id)
    recently_mailed = await _mailed_by_teammates(db, rows, sender_id)
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=grace_minutes())

    sendable: list = []
    held: list[dict[str, Any]] = []
    probing: set[str] = set()
    for row in rows:
        # The club sends as individual members, so nothing downstream would
        # notice two of them writing to the same person. The recipient would.
        teammate = recently_mailed.get((row["email"] or "").strip().lower())
        if teammate:
            held.append({"row": row, "host": by_id.get(row["id"]) or "",
                         "reason": HELD_TEAMMATE_SENT, "teammate": teammate})
            continue
        host = by_id.get(row["id"]) or ""
        if not host or host in proven:
            sendable.append(row)
            continue
        entry = history.get(host) or {}
        if entry.get("bounced"):
            held.append({"row": row, "host": host, "reason": HELD_PROBE_BOUNCED})
            continue
        if entry.get("answered"):
            sendable.append(row)
            continue
        last_sent = entry.get("last_sent")
        if entry.get("in_flight") or host in probing:
            held.append({"row": row, "host": host, "reason": HELD_PROBE_IN_FLIGHT})
            continue
        if last_sent is not None:
            if last_sent <= cutoff:
                # Quiet since the grace period: no bounce came back, so the
                # format stands up well enough to mail the rest.
                sendable.append(row)
            else:
                held.append({"row": row, "host": host, "reason": HELD_PROBE_IN_FLIGHT})
            continue
        # Nothing tried yet at this company: this row is the probe, and it
        # goes alone.
        sendable.append(row)
        probing.add(host)
    return sendable, held


def describe_hold(held: list[dict[str, Any]]) -> str:
    """One line a member can act on, or empty when nothing is held."""
    if not held:
        return ""
    teammates = sorted({h.get("teammate") or "a teammate" for h in held if h["reason"] == HELD_TEAMMATE_SENT})
    bounced = sorted({h["host"] for h in held if h["reason"] == HELD_PROBE_BOUNCED})
    waiting = sorted({h["host"] for h in held if h["reason"] == HELD_PROBE_IN_FLIGHT})
    parts: list[str] = []
    if teammates:
        held_n = sum(1 for h in held if h["reason"] == HELD_TEAMMATE_SENT)
        parts.append(
            f"{held_n} recipient(s) held: {', '.join(teammates)} already wrote to them in the last "
            f"{teammate_quiet_days()} days. Remove them, or ask who is further along."
        )
    if bounced:
        parts.append(
            f"{len(held)} email(s) held: the first address to {', '.join(bounced)} bounced, "
            "so the rest use a format that does not work. Set the company's email format, then retry."
        )
    elif waiting:
        parts.append(
            f"{len(held)} email(s) held until the first address to {', '.join(waiting)} is proven. "
            f"They send automatically within {grace_minutes()} minutes unless it bounces."
        )
    return " ".join(parts)
