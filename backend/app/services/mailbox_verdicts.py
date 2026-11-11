"""Club-wide mailbox verdicts and the background job that keeps them current.

One row per address, whoever asked. A Verifalia answer is written here by
email_verification (pre-send check, roster pass or this job) and read back for
any member before a credit is spent, so the club pays once per address for
CACHE_DAYS. ``proof_for`` folds those verdicts together with what mail actually
did (replies, bounces, delivered, suppressions) into one proof level per address.

``run_mailbox_backlog`` spends the day's leftover credits on addresses nobody has
checked yet, minus a reserve kept for pre-send checks. Its checks run as actor 0,
the same account-less actor the roster pass uses: no user row, so nobody can sign
in as it, and its reservations never collide with a member's.
"""
from __future__ import annotations

import logging
import os
import time

from app.database import get_db
from app.services.contact_intelligence import Mailbox, now_iso

logger = logging.getLogger(__name__)

BACKLOG_ACTOR = 0
#: Credits one tick may spend. A check can wait ~15 s on Verifalia, so a paid plan's
#: 250 credits are spread over ticks rather than held in one long pass.
TICK_CREDITS = 25
_PAGE = 200
_CHUNK = 400
#: backlog_status runs on a page load and counting the queue pages through the catalog,
#: so the count is kept this long (and dropped whenever a verdict is stored).
_COUNT_SECONDS = 120.0
_count: tuple[float, int] | None = None

#: Weakest to strongest; the position is the rank. A reply outranks everything (the
#: mailbox demonstrably works); an observed bounce outranks a provider's "rejected".
_PROOF = ("unverified", "catch_all", "mailbox_confirmed", "delivered", "mailbox_rejected", "bounced", "replied")
_RANK = {name: rank for rank, name in enumerate(_PROOF)}
_FROM_VERDICT = {
    Mailbox.PROVIDER_HIGH_CONFIDENCE.value: "mailbox_confirmed",
    Mailbox.PROVIDER_MEDIUM_CONFIDENCE.value: "mailbox_confirmed",
    Mailbox.ACCEPT_ALL_OR_RISKY.value: "catch_all",
    Mailbox.RECIPIENT_REJECTED.value: "mailbox_rejected",
}
_FROM_CHECK = {
    Mailbox.HUMAN_REPLY_OBSERVED.value: "replied",
    Mailbox.PERMANENT_FAILURE_OBSERVED.value: "bounced",
    Mailbox.PREVIOUSLY_DELIVERED.value: "delivered",
}
_FROM_EVENT = {"replied": "replied", "bounced": "bounced", "delivered": "delivered"}

#: email_verification_status after a verdict, in the vocabulary the rest of the app
#: already reads ('valid' / 'likely_valid' / 'invalid' / 'dead'; see delivery_policy
#: and yucgoutreach_discovery._is_verified). Inconclusive verdicts change nothing.
_STATUS = {
    Mailbox.PROVIDER_HIGH_CONFIDENCE.value: "valid",
    Mailbox.PROVIDER_MEDIUM_CONFIDENCE.value: "valid",
    Mailbox.ACCEPT_ALL_OR_RISKY.value: "likely_valid",
}


def _reserve() -> int:
    return max(0, int(os.getenv("VERIFALIA_PRESEND_RESERVE", "5") or 5))


async def add_mailbox_verdicts(db) -> None:
    """Migration outreach:4. The verdict table, and the per-day credit table's
    allowance ceiling raised from 25 to 250 so a paid Verifalia plan works.

    SQLite cannot alter a CHECK, so the (one row a day) table is rebuilt. That is
    the one narrowing-free exception to "a migration only adds": it happens inside
    this migration's transaction, so the running release never sees it missing,
    and it keeps every row."""
    await db.execute(
        """CREATE TABLE IF NOT EXISTS mailbox_verdicts (
               email TEXT PRIMARY KEY COLLATE NOCASE, mailbox TEXT NOT NULL, method TEXT,
               reason TEXT, checked_at TEXT NOT NULL, expires_at TEXT)"""
    )
    legacy = await (await db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='verification_provider_days'"
    )).fetchone()
    if legacy and "BETWEEN 0 AND 25)" in legacy["sql"]:
        await db.execute(
            """CREATE TABLE verification_provider_days_wide (
                   day TEXT PRIMARY KEY, used INTEGER NOT NULL DEFAULT 0,
                   allowance INTEGER NOT NULL CHECK(allowance BETWEEN 0 AND 250))"""
        )
        await db.execute("INSERT INTO verification_provider_days_wide SELECT day, used, allowance FROM verification_provider_days")
        await db.execute("DROP TABLE verification_provider_days")
        await db.execute("ALTER TABLE verification_provider_days_wide RENAME TO verification_provider_days")


async def fresh_verdict(db, email: str):
    """The unexpired verdict for an address, or None."""
    return await (await db.execute(
        "SELECT mailbox, method, reason, checked_at, expires_at FROM mailbox_verdicts WHERE email=? AND expires_at>?",
        ((email or "").strip().lower(), now_iso()),
    )).fetchone()


async def store_verdict(db, email: str, result: dict) -> None:
    """Record a verdict in the caller's transaction and carry it to the rows that
    hold this address: a rejected mailbox marks its contacts dead (prospects
    invalid); a confirmed or catch-all one upgrades rows not already dead."""
    global _count
    _count = None
    address = email.strip().lower()
    mailbox = str(result["mailbox"])
    await db.execute(
        """INSERT INTO mailbox_verdicts(email,mailbox,method,reason,checked_at,expires_at) VALUES(?,?,?,?,?,?)
           ON CONFLICT(email) DO UPDATE SET mailbox=excluded.mailbox, method=excluded.method, reason=excluded.reason,
               checked_at=excluded.checked_at, expires_at=excluded.expires_at""",
        (address, mailbox, result.get("method"), result.get("reason"), result.get("checked_at") or now_iso(), result.get("expires_at")),
    )
    if mailbox == Mailbox.RECIPIENT_REJECTED.value:
        await db.execute("UPDATE contacts SET email_verification_status='dead' WHERE lower(email)=?", (address,))
        await db.execute("UPDATE yucgoutreach_prospects SET email_verification_status='invalid' WHERE lower(email)=?", (address,))
    elif mailbox in _STATUS:
        for table in ("contacts", "yucgoutreach_prospects"):
            await db.execute(
                f"""UPDATE {table} SET email_verification_status=?
                    WHERE lower(email)=? AND COALESCE(email_verification_status,'') NOT IN ('dead','invalid')""",
                (_STATUS[mailbox], address),
            )


def _chunks(items: list[str]):
    for start in range(0, len(items), _CHUNK):
        yield items[start:start + _CHUNK]


async def proof_for(db, emails: list[str]) -> dict[str, str]:
    """Lowercase address -> proof level, one entry per requested address.

    Strongest evidence wins: replied > bounced > mailbox_rejected > delivered >
    mailbox_confirmed > catch_all > unverified. A verdict counts only while
    unexpired, so a provider answer older than CACHE_DAYS drops back to unverified.
    Replies, bounces and deliveries come from Gmail-synced outreach events and the
    per-address check history; a suppression with no bounce behind it is a provider
    rejection. A handful of batched queries, never one per address."""
    wanted = list(dict.fromkeys(e.strip().lower() for e in emails if e and e.strip()))
    best = {e: "unverified" for e in wanted}

    def note(address: str, proof: str | None) -> None:
        if proof and address in best and _RANK[proof] > _RANK[best[address]]:
            best[address] = proof

    now = now_iso()
    for chunk in _chunks(wanted):
        marks = ",".join("?" * len(chunk))
        for row in await (await db.execute(
            f"""SELECT lower(e.email) AS email, c.result AS what FROM email_checks c
                JOIN email_candidates e ON e.id=c.candidate_id
                WHERE e.email IN ({marks}) AND c.result IN ('human_reply_observed','permanent_failure_observed','previously_delivered')""",
            chunk,
        )).fetchall():
            note(row["email"], _FROM_CHECK.get(row["what"]))
        for row in await (await db.execute(
            f"""SELECT lower(m.recipient) AS email, ev.kind AS what FROM outreach_events ev
                JOIN outreach_messages m ON m.id=ev.message_id
                WHERE lower(m.recipient) IN ({marks}) AND ev.kind IN ('replied','bounced','delivered')""",
            chunk,
        )).fetchall():
            note(row["email"], _FROM_EVENT.get(row["what"]))
        for row in await (await db.execute(
            f"SELECT lower(email) AS email FROM candidate_suppressions WHERE email IN ({marks})", chunk,
        )).fetchall():
            note(row["email"], "mailbox_rejected")
        for row in await (await db.execute(
            f"SELECT lower(email) AS email, mailbox FROM mailbox_verdicts WHERE email IN ({marks}) AND expires_at>?",
            (*chunk, now),
        )).fetchall():
            note(row["email"], _FROM_VERDICT.get(row["mailbox"]))
    return best


#: Where the job looks, best first. Each selects `email` and skips rows already
#: marked dead/invalid, junk, with an unexpired verdict or a suppression.
_UNCHECKED = """COALESCE({t}.email,'')!='' AND COALESCE({t}.email_verification_status,'') NOT IN ('dead','invalid')
    AND NOT EXISTS (SELECT 1 FROM mailbox_verdicts v WHERE v.email={t}.email AND v.expires_at>?)
    AND NOT EXISTS (SELECT 1 FROM candidate_suppressions s WHERE s.email={t}.email)"""
_SOURCES = (
    # (a) people already queued to be mailed: running campaigns first.
    f"""SELECT c.email FROM campaign_contacts cc
        JOIN campaigns camp ON camp.id=cc.campaign_id JOIN contacts c ON c.id=cc.contact_id
        WHERE cc.status='pending' AND camp.status IN ('draft','paused','releasing') AND {_UNCHECKED.format(t='c')}
        ORDER BY CASE camp.status WHEN 'releasing' THEN 0 WHEN 'paused' THEN 1 ELSE 2 END, cc.id
        LIMIT ? OFFSET ?""",
    # (b) the shared catalog (no owner), strongest guess first.
    f"""SELECT c.email FROM contacts c
        WHERE c.owner_id IS NULL AND COALESCE(c.ai_verdict,'')!='junk' AND {_UNCHECKED.format(t='c')}
        ORDER BY CASE c.confidence WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, c.id DESC
        LIMIT ? OFFSET ?""",
    # (c) discovery prospects: newest run first, then score.
    f"""SELECT p.email FROM yucgoutreach_prospects p
        WHERE COALESCE(p.ai_verdict,'')!='junk' AND {_UNCHECKED.format(t='p')}
        ORDER BY p.run_id DESC, p.score DESC, p.id
        LIMIT ? OFFSET ?""",
)


async def _queue(db, limit: int | None) -> list[str]:
    """Addresses worth a credit, in order, deduplicated by lowercase address:
    individual work addresses whose proof level is still 'unverified'. Read-only,
    so paging by offset is stable. limit=None counts the whole queue."""
    from app.services.email_verification import local_rejection

    queue: list[str] = []
    seen: set[str] = set()
    now = now_iso()
    for sql in _SOURCES:
        offset = 0
        while limit is None or len(queue) < limit:
            rows = await (await db.execute(sql, (now, _PAGE, offset))).fetchall()
            if not rows:
                break
            offset += len(rows)
            page = []
            for row in rows:
                address = row["email"].strip().lower()
                if address not in seen:
                    seen.add(address)
                    if not local_rejection(address):
                        page.append(address)
            proof = await proof_for(db, page)
            queue.extend(a for a in page if proof[a] == "unverified")
    return queue if limit is None else queue[:limit]


async def _queue_remaining(db) -> int:
    global _count
    if _count and time.monotonic() - _count[0] < _COUNT_SECONDS:
        return _count[1]
    remaining = len(await _queue(db, None))
    _count = (time.monotonic(), remaining)
    return remaining


async def backlog_status(db) -> dict:
    """What the background job is doing, for the directory page."""
    from app.services.email_verification import credit_state, verifalia_selected

    state = await credit_state(db)
    verified = await (await db.execute("SELECT COUNT(*) AS n FROM mailbox_verdicts WHERE method='verifalia'")).fetchone()
    return {
        "enabled": verifalia_selected() and state["enabled"],
        "credits_used_today": state["used"],
        "credits_cap_today": state["allowance"],
        "verified_total": int(verified["n"]),
        "queue_remaining": await _queue_remaining(db),
    }


async def run_mailbox_backlog() -> dict:
    """Leader-only job. Spend today's credits, less the pre-send reserve, on the
    queue. Stops on the first provider error or exhaustion. A check that never
    reaches the provider (mail domain has no route) is remembered for its short
    local expiry so it cannot sit at the head of the queue and starve the rest."""
    from app.services.email_verification import assess_address, credit_state, verifalia_selected

    db = await get_db()
    try:
        state = await credit_state(db)
        if not (verifalia_selected() and state["enabled"]):
            return {"ok": True, "skipped": "disabled"}
        budget = min(TICK_CREDITS, state["remaining"] - _reserve())
        if budget <= 0:
            return {"ok": True, "skipped": "reserve", "remaining": state["remaining"]}
        queue = await _queue(db, budget)
    finally:
        await db.close()

    spent = local = 0
    for address in queue:
        try:
            result = await assess_address(address, actor_id=BACKLOG_ACTOR, external=True)
        except Exception:
            logger.exception("mailbox backlog check failed; stopping this tick")
            break
        provider = str(result.get("provider_state") or "")
        if provider in ("disabled", "exhausted", "unavailable"):
            break
        if provider == "complete":
            spent += 1
        elif result.get("mailbox") in (Mailbox.DOMAIN_HAS_NO_MAIL_ROUTE.value, Mailbox.BAD_SYNTAX.value) and result.get("expires_at"):
            db = await get_db()
            try:
                await db.execute("BEGIN IMMEDIATE")
                await store_verdict(db, address, result)
                await db.commit()
            except Exception:
                await db.rollback()
                raise
            finally:
                await db.close()
            local += 1
    return {"ok": True, "queued": len(queue), "spent": spent, "local": local}
