"""Prove one mailbox at a company before mailing the rest of it.

Most addresses this tool sends to were never observed anywhere: they were
built from a learned or guessed format. INBOX_VERIFY_MODE=mx only proves the
domain accepts mail, never that the mailbox exists, so a wrong format passes
every check and is only discovered by the bounce.

That is survivable for one address and damaging for thirty at once: a batch of
hard bounces to a single domain is exactly the pattern spam filters score
against the sending account, and the account here is a member's real Gmail.

So the first address to an unproven company goes alone. Message tracking turns
a Gmail bounce or reply into a mailbox event; roster_email.apply_mailbox_proof
then feeds that operational result back into the pattern's confidence. This
module only decides who is allowed to go now.

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
HELD_OUTSIDE_WINDOW = "outside_send_window"
HELD_COMPANY_REPLIED = "company_replied"
HELD_COMPANY_MEETING = "company_meeting"
HELD_COMPANY_CAP = "company_cap_reached"


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


def company_send_cap() -> int:
    """First-touch emails to distinct people at one company, club-wide, per window.

    Three people at a firm is a campaign; ten is a pattern its mail filter and
    its people both notice. 0 turns the cap off.
    """
    try:
        return max(0, min(int((os.getenv("COMPANY_SEND_CAP") or "").strip() or 3), 1000))
    except ValueError:
        return 3


def company_send_cap_days() -> int:
    try:
        return max(1, min(int((os.getenv("COMPANY_SEND_CAP_DAYS") or "").strip() or 30), 365))
    except ValueError:
        return 30


def company_keys(company: str | None, domain: str | None, email: str | None) -> frozenset[str]:
    """Every identity one contact has at its company.

    Contacts for one company arrive with a domain, with only a name, or with
    only an address, so a single key would miss the match the member cares
    about. Two contacts share a company when any of these keys is shared. A
    consumer mailbox says nothing about an employer and contributes none.
    """
    from app.services.company_claims import company_key
    from app.services.contact_intelligence import CONSUMER_DOMAINS

    keys = {company_key(company, domain)}
    if (company or "").strip():
        keys.add(company_key(company, None))
    host = mail_host(email or "")
    if host and host not in CONSUMER_DOMAINS:
        keys.add(company_key(None, host))
    return frozenset(k for k in keys if k and k != "n:")


def _company_label(company: str | None, domain: str | None, email: str | None) -> str:
    return (company or "").strip() or (domain or "").strip() or mail_host(email or "") or "that company"


async def replied_companies(db) -> dict[str, str]:
    """Company key -> display name, for every company where anyone replied.

    Club-wide and permanent: a reply means a person there has already answered
    one of us, and the next email from any member lands in the same inbox.
    An automatic reply is not a reply. The mark on the row (replied_at or the
    replied status) and the mailbox event both count, because a manual mark
    and a Gmail sync reach only one of them.
    """
    rows = await (await db.execute(
        """SELECT DISTINCT c.company, c.company_domain, c.email
           FROM campaign_contacts cc JOIN contacts c ON c.id = cc.contact_id
           WHERE cc.replied_at IS NOT NULL OR cc.status = 'replied'
              OR EXISTS (SELECT 1 FROM outreach_messages m
                         JOIN outreach_events e ON e.message_id = m.id
                         WHERE m.campaign_contact_id = cc.id AND e.kind = 'replied')"""
    )).fetchall()
    index: dict[str, str] = {}
    for row in rows:
        label = _company_label(row["company"], row["company_domain"], row["email"])
        for key in company_keys(row["company"], row["company_domain"], row["email"]):
            index.setdefault(key, label)
    return index


BOOKED_STATUSES = ("meeting", "closed")


async def meeting_companies(db) -> dict[str, str]:
    """Company key -> display name, for every company where a member booked a meeting.

    Same club-wide stop as a reply: a contact the Pipeline board marks
    ``meeting`` or ``closed`` means the conversation moved off email, so no
    member follows up with, or first-mails, anyone at that company. Moving the
    contact back (for example to ``contacted``) lifts the stop.
    """
    rows = await (await db.execute(
        f"""SELECT DISTINCT company, company_domain, email FROM contacts
            WHERE pipeline_status IN ({",".join("?" * len(BOOKED_STATUSES))})""",
        BOOKED_STATUSES,
    )).fetchall()
    index: dict[str, str] = {}
    for row in rows:
        label = _company_label(row["company"], row["company_domain"], row["email"])
        for key in company_keys(row["company"], row["company_domain"], row["email"]):
            index.setdefault(key, label)
    return index


async def company_conversations(db) -> dict[str, tuple[str, str]]:
    """Company key -> (reason, display name) for companies the club has already reached.

    A reply and a booked meeting stop follow-ups and hold first sends alike;
    a reply is reported first when both apply.
    """
    index = {key: (HELD_COMPANY_MEETING, label) for key, label in (await meeting_companies(db)).items()}
    index.update({key: (HELD_COMPANY_REPLIED, label) for key, label in (await replied_companies(db)).items()})
    return index


def company_stop(index: dict[str, tuple[str, str]], company: str | None, domain: str | None,
                 email: str | None) -> tuple[str, str] | None:
    """(reason, company name) if the company replied or booked a meeting, else None."""
    for key in sorted(company_keys(company, domain, email)):
        if key in index:
            return index[key]
    return None


class CompanyCap:
    """Who the club has already first-touched at each company, in the window.

    Load and use it inside the write transaction that claims the rows: the
    claim then sees every earlier claim, and a concurrent drain waits for the
    transaction, so the cap cannot be exceeded by racing claims. Follow-ups
    go to people already contacted and never count.
    """

    def __init__(self, entries: list[tuple[frozenset[str], str, datetime]], cap: int, days: int):
        self.entries = entries
        self.cap = cap
        self.days = days

    @classmethod
    async def load(cls, db) -> "CompanyCap":
        cap, days = company_send_cap(), company_send_cap_days()
        if cap == 0:
            return cls([], 0, days)
        rows = await (await db.execute(
            """SELECT d.claimed_at, d.recipient, c.company, c.company_domain
               FROM outreach_dispatches d
               JOIN campaign_contacts cc ON cc.id = d.campaign_contact_id
               JOIN contacts c ON c.id = cc.contact_id
               WHERE d.dispatch_key LIKE 'initial:%' AND d.claimed_at IS NOT NULL
                 AND d.claimed_at > datetime('now', ?)""",
            (f"-{days} days",),
        )).fetchall()
        entries = []
        for row in rows:
            at = _parse_ts(row["claimed_at"])
            if at:
                entries.append((company_keys(row["company"], row["company_domain"], row["recipient"]),
                                (row["recipient"] or "").strip().lower(), at))
        return cls(entries, cap, days)

    def admit(self, row: dict) -> dict[str, Any] | None:
        """None when the row may go (and is counted); otherwise the hold."""
        if not self.cap:
            return None
        email = (row.get("email") or "").strip().lower()
        keys = company_keys(row.get("company"), row.get("company_domain"), email)
        if not keys:
            return None
        first_touch: dict[str, datetime] = {}
        for entry_keys, person, at in self.entries:
            if entry_keys & keys and (person not in first_touch or at < first_touch[person]):
                first_touch[person] = at
        if email in first_touch or len(first_touch) < self.cap:
            self.entries.append((keys, email, datetime.now(timezone.utc)))
            return None
        times = sorted(first_touch.values())
        return {
            "row": row, "host": "", "reason": HELD_COMPANY_CAP,
            "company": _company_label(row.get("company"), row.get("company_domain"), email),
            "people": len(first_touch),
            "next_slot": times[len(times) - self.cap] + timedelta(days=self.days),
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


async def select_sendable(db, campaign_id: int, rows: list, sender_id: int | None = None,
                          probing: set[str] | None = None) -> tuple[list, list[dict[str, Any]]]:
    """Split claimable rows into those allowed to send now and those held.

    Rows are returned in their original order so the queue stays first-in
    first-out within a company. A row that goes alone to an unproven company
    is marked ``row["probe"] = True``. A caller that gates a queue in pages
    passes the same ``probing`` set to every page, so one company still gets
    one probe, not one per page.
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
    conversations = await company_conversations(db)
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=grace_minutes())

    sendable: list = []
    held: list[dict[str, Any]] = []
    probing = set() if probing is None else probing
    for row in rows:
        # One reply or booked meeting anywhere at the company outranks every
        # per-person rule: the next email from any member reaches the same people.
        stop = company_stop(conversations, row.get("company"), row.get("company_domain"), row.get("email"))
        if stop:
            held.append({"row": row, "host": by_id.get(row["id"]) or "",
                         "reason": stop[0], "company": stop[1]})
            continue
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
        sendable.append({**row, "probe": True})
        probing.add(host)
    return sendable, held


def describe_hold(held: list[dict[str, Any]]) -> str:
    """One line a member can act on, or empty when nothing is held."""
    if not held:
        return ""
    replied = sorted({h.get("company") or "that company" for h in held if h["reason"] == HELD_COMPANY_REPLIED})
    meetings = sorted({h.get("company") or "that company" for h in held if h["reason"] == HELD_COMPANY_MEETING})
    capped = [h for h in held if h["reason"] == HELD_COMPANY_CAP]
    teammates = sorted({h.get("teammate") or "a teammate" for h in held if h["reason"] == HELD_TEAMMATE_SENT})
    bounced = sorted({h["host"] for h in held if h["reason"] == HELD_PROBE_BOUNCED})
    waiting = sorted({h["host"] for h in held if h["reason"] == HELD_PROBE_IN_FLIGHT})
    window = [h for h in held if h["reason"] == HELD_OUTSIDE_WINDOW]
    parts: list[str] = []
    if teammates:
        held_n = sum(1 for h in held if h["reason"] == HELD_TEAMMATE_SENT)
        parts.append(
            f"{held_n} recipient(s) held: {', '.join(teammates)} already wrote to them in the last "
            f"{teammate_quiet_days()} days. Remove them, or ask who is further along."
        )
    if replied:
        held_n = sum(1 for h in held if h["reason"] == HELD_COMPANY_REPLIED)
        parts.append(
            f"{held_n} recipient(s) held: a teammate already has a reply from {', '.join(replied)}. "
            "Remove them, or ask who is further along."
        )
    if meetings:
        held_n = sum(1 for h in held if h["reason"] == HELD_COMPANY_MEETING)
        parts.append(
            f"{held_n} recipient(s) held: A teammate already has a meeting with {', '.join(meetings)}. "
            "Remove them, or ask who is further along."
        )
    for company in sorted({h["company"] for h in capped}):
        mine = [h for h in capped if h["company"] == company]
        span = "this month" if company_send_cap_days() == 30 else f"in the last {company_send_cap_days()} days"
        slot = min(h["next_slot"] for h in mine)
        parts.append(
            f"{len(mine)} email(s) held: {mine[0]['people']} people at {company} already emailed {span}; "
            f"next slot {slot.strftime('%Y-%m-%d')}. They send automatically when it opens."
        )
    if bounced:
        held_n = sum(1 for h in held if h["reason"] == HELD_PROBE_BOUNCED)
        parts.append(
            f"{held_n} email(s) held: the first address to {', '.join(bounced)} bounced, "
            "so the rest use a format that does not work. Set the company's email format, then retry."
        )
    elif waiting:
        held_n = sum(1 for h in held if h["reason"] == HELD_PROBE_IN_FLIGHT)
        parts.append(
            f"{held_n} email(s) held until the first address to {', '.join(waiting)} is proven. "
            f"They send automatically within {grace_minutes()} minutes unless it bounces."
        )
    if window:
        opens = min(h["opens_at"] for h in window)
        parts.append(
            f"{len(window)} email(s) wait for their recipients' business hours. "
            f"They send automatically when it opens (next at {opens.strftime('%Y-%m-%d %H:%M')} UTC)."
        )
    return " ".join(parts)
