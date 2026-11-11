"""Club-wide mailbox verdicts: one member's Verifalia answer serves every member,
the background job spends only the day's leftover credits in queue order, and
proof_for folds verdicts with real mail evidence.

Verifalia and DNS are mocked. From backend/: python tests/test_mailbox_verdicts.py
"""
import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.name}"
os.environ["JWT_SECRET"] = "mailbox-verdicts-test-secret-xxxxxxxxxx"
os.environ["INBOX_VERIFY_PROVIDER"] = "verifalia"
os.environ["EXTERNAL_EMAIL_VERIFICATION_ENABLED"] = "true"
os.environ["VERIFALIA_API_KEY"] = "test-key-not-a-live-credential"
os.environ["VERIFALIA_DAILY_CREDIT_CAP"] = "25"
os.environ["VERIFALIA_PRESEND_RESERVE"] = "5"

from app.database import get_db, init_db  # noqa: E402
from app.services import email_verification as EV  # noqa: E402
from app.services import mailbox_verdicts as MV  # noqa: E402

#: address -> mailbox the mocked provider answers with; "unavailable"/"exhausted" are provider failures.
answers: dict[str, str] = {}
calls: list[str] = []
dns_calls: list[str] = []


async def fake_verifalia(email: str) -> dict:
    calls.append(email)
    outcome = answers.get(email, "provider_high_confidence")
    if outcome in ("unavailable", "exhausted"):
        return EV._result("inconclusive", "verifalia", "provider failure", provider_state=outcome)
    return EV._result(outcome, "verifalia", f"Provider classification: {outcome}", cost=1, provider_state="complete")


async def fake_dns(raw: str, smtp_probe: bool = False, **_kw) -> dict:
    dns_calls.append(raw)
    if raw.endswith("@nomx.example"):
        return {"reason": "no_mx", "mx_valid": False}
    return {"reason": "", "mx_valid": True}


def mocked():
    return patch.multiple(EV, _verifalia=fake_verifalia, verify_email_deliverability=fake_dns)


async def run(sql: str, args: tuple = ()) -> int:
    db = await get_db()
    try:
        cur = await db.execute(sql, args)
        await db.commit()
        return int(cur.lastrowid or 0)
    finally:
        await db.close()


async def rows(sql: str, args: tuple = ()) -> list[dict]:
    db = await get_db()
    try:
        return [dict(r) for r in await (await db.execute(sql, args)).fetchall()]
    finally:
        await db.close()


async def proof(emails: list[str]) -> dict[str, str]:
    db = await get_db()
    try:
        return await MV.proof_for(db, emails)
    finally:
        await db.close()


async def reset() -> None:
    for table in ("outreach_events", "outreach_messages", "campaign_contacts", "campaigns", "contacts",
                  "yucgoutreach_prospects", "yucgoutreach_discovery_runs", "mailbox_verdicts",
                  "candidate_suppressions", "verification_reservations", "verification_provider_days",
                  "verification_provider_control", "email_checks", "email_candidates", "people"):
        await run(f"DELETE FROM {table}")
    MV._count = None
    answers.clear()
    calls.clear()
    dns_calls.clear()
    for uid in (1, 2):
        await run("INSERT OR IGNORE INTO users(id, email, name, role) VALUES (?, ?, ?, 'standard')", (uid, f"m{uid}@club.test", f"M{uid}"))


async def contact(email: str, *, owner=None, confidence="medium", ai=None, status=None) -> int:
    return await run(
        "INSERT INTO contacts(name, email, owner_id, confidence, ai_verdict, email_verification_status) VALUES (?,?,?,?,?,?)",
        ("Person", email, owner, confidence, ai, status),
    )


async def campaign(status: str) -> int:
    return await run("INSERT INTO campaigns(name, status, owner_user_id, sender_user_id) VALUES ('c', ?, 1, 1)", (status,))


async def run_prospects(emails: list[tuple], ai=None) -> int:
    run_id = await run("INSERT INTO yucgoutreach_discovery_runs(user_id, company_name, status) VALUES (1, 'Co', 'completed')")
    for email, score, verdict in emails:
        await run(
            "INSERT INTO yucgoutreach_prospects(run_id, email, score, ai_verdict) VALUES (?,?,?,?)",
            (run_id, email, score, verdict),
        )
    return run_id


async def mail_event(email: str, kind: str, n: int = 1) -> None:
    """A sent message to `email` with one tracked event."""
    cid = await contact(f"holder-{email}-{kind}-{n}@holder.test", owner=1)
    camp = await campaign("completed")
    cc = await run("INSERT INTO campaign_contacts(campaign_id, contact_id, status) VALUES (?,?, 'sent')", (camp, cid))
    msg = await run(
        "INSERT INTO outreach_messages(campaign_contact_id, sender_id, recipient, tracking_token, rfc_message_id, sent_at) "
        "VALUES (?,1,?,?,?, '2026-01-01T00:00:00+00:00')",
        (cc, email, f"tok-{email}-{kind}-{n}", f"<{email}-{kind}-{n}@x>"),
    )
    await run("INSERT INTO outreach_events(message_id, kind, source_id, occurred_at) VALUES (?,?,?, '2026-01-02T00:00:00+00:00')",
              (msg, kind, f"s-{kind}-{n}"))


async def verdict(email: str, mailbox: str, *, days: int = 30) -> None:
    expires = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
    await run(
        "INSERT INTO mailbox_verdicts(email, mailbox, method, reason, checked_at, expires_at) VALUES (?,?, 'verifalia', 'r', ?, ?)",
        (email, mailbox, datetime.now(timezone.utc).isoformat(), expires),
    )


async def credits_used() -> int:
    got = await rows("SELECT used FROM verification_provider_days")
    return int(got[0]["used"]) if got else 0


async def migration_applies_once() -> None:
    await init_db()
    await init_db()
    assert [r["version"] for r in await rows("SELECT version FROM schema_migrations WHERE app='outreach' AND version=4")] == [4]
    assert await rows("SELECT 1 FROM sqlite_master WHERE name='mailbox_verdicts'")

    # A database from before the release has the old 0..25 allowance check and a row to keep.
    await run("DROP TABLE verification_provider_days")
    await run("CREATE TABLE verification_provider_days (day TEXT PRIMARY KEY, used INTEGER NOT NULL DEFAULT 0, "
              "allowance INTEGER NOT NULL CHECK(allowance BETWEEN 0 AND 25))")
    await run("INSERT INTO verification_provider_days VALUES ('2026-01-01', 7, 25)")
    await run("DELETE FROM schema_migrations WHERE app='outreach' AND version=4")
    await init_db()
    await init_db()
    assert [r["version"] for r in await rows("SELECT version FROM schema_migrations WHERE app='outreach' AND version=4")] == [4]
    assert (await rows("SELECT used, allowance FROM verification_provider_days WHERE day='2026-01-01'"))[0] == {"used": 7, "allowance": 25}
    await run("INSERT INTO verification_provider_days VALUES ('2026-01-02', 0, 250)")  # the old check refused this


async def verdict_is_shared_between_members() -> None:
    await reset()
    with mocked():
        first = await EV.assess_address("shared@acme.com", actor_id=1, external=True)
        second = await EV.assess_address("Shared@Acme.com", actor_id=2, external=True)
        third = await EV.assess_address("shared@acme.com", actor_id=2)  # no external flag still sees the club's answer
    assert calls == ["shared@acme.com"], calls
    assert first["provider_state"] == "complete" and second["provider_state"] == "cached" and third["provider_state"] == "cached"
    assert second["mailbox"] == third["mailbox"] == "provider_high_confidence"
    assert await credits_used() == 1
    stored = await rows("SELECT method, expires_at FROM mailbox_verdicts")
    assert len(stored) == 1 and stored[0]["method"] == "verifalia"
    days = (datetime.fromisoformat(stored[0]["expires_at"]) - datetime.now(timezone.utc)).days
    assert 88 <= days <= 90, days


async def ceiling_is_250() -> None:
    await reset()
    with mocked(), patch.dict(os.environ, {"VERIFALIA_DAILY_CREDIT_CAP": "999"}):
        await EV.assess_address("ceiling@acme.com", actor_id=1, external=True)
    assert (await rows("SELECT allowance FROM verification_provider_days"))[0]["allowance"] == 250


async def rejected_verdict_suppresses_and_marks_dead() -> None:
    await reset()
    cid = await contact("ghost@acme.com", owner=None, status="likely_valid")
    await run_prospects([("ghost@acme.com", 50, None)])
    answers["ghost@acme.com"] = "recipient_rejected"
    with mocked():
        got = await EV.assess_address("ghost@acme.com", actor_id=1, external=True)
        again = await EV.assess_address("ghost@acme.com", actor_id=2, external=True)
    assert got["mailbox"] == "recipient_rejected" and calls == ["ghost@acme.com"]
    assert again["mailbox"] == "permanent_failure_observed"  # the suppression answers before any credit
    assert (await rows("SELECT email_verification_status s FROM contacts WHERE id=?", (cid,)))[0]["s"] == "dead"
    assert (await rows("SELECT email_verification_status s FROM yucgoutreach_prospects"))[0]["s"] == "invalid"
    assert await rows("SELECT 1 FROM candidate_suppressions WHERE email='ghost@acme.com'")
    assert (await proof(["ghost@acme.com"]))["ghost@acme.com"] == "mailbox_rejected"


async def proof_precedence() -> None:
    await reset()
    await mail_event("R@x.com", "replied")
    await mail_event("r@x.com", "bounced")
    await mail_event("b@x.com", "bounced")
    await mail_event("b@x.com", "delivered", 2)
    await verdict("b@x.com", "provider_high_confidence")
    await mail_event("j@x.com", "delivered")
    await run("INSERT INTO candidate_suppressions(email, state, observed_at) VALUES ('j@x.com','permanent_failure','2026-01-01')")
    await mail_event("d@x.com", "delivered")
    await verdict("d@x.com", "provider_high_confidence")
    await verdict("m@x.com", "provider_medium_confidence")
    await verdict("k@x.com", "accept_all_or_risky")
    await verdict("e@x.com", "provider_high_confidence", days=-1)
    await verdict("i@x.com", "inconclusive")
    pid = await run("INSERT INTO people(canonical_key, name, normalized_name, created_at) VALUES ('k','P','p','2026-01-01')")
    cand = await run("INSERT INTO email_candidates(person_id, email, origin, method, created_at) VALUES (?,?,'user_supplied','t','2026-01-01')",
                     (pid, "Mixed.Case@proof.com"))
    await run("INSERT INTO email_checks(candidate_id, actor_id, verifier, check_type, result, reason, checked_at) "
              "VALUES (?,1,'gmail','observed','human_reply_observed','reply','2026-01-01')", (cand,))
    got = await proof(["R@X.com", "b@x.com", "j@x.com", "d@x.com", "m@x.com", "k@x.com", "e@x.com", "i@x.com", "n@x.com", "MIXED.case@proof.com", " "])
    assert got == {
        "r@x.com": "replied", "b@x.com": "bounced", "j@x.com": "mailbox_rejected", "d@x.com": "delivered",
        "m@x.com": "mailbox_confirmed", "k@x.com": "catch_all", "e@x.com": "unverified", "i@x.com": "unverified",
        "n@x.com": "unverified", "mixed.case@proof.com": "replied",
    }, got


async def job_follows_queue_and_skips() -> None:
    await reset()
    releasing, draft, done = await campaign("releasing"), await campaign("draft"), await campaign("completed")
    for camp, email, status in ((draft, "a-draft@acme.com", "pending"), (releasing, "a-rel@acme.com", "pending"),
                                (done, "sent@acme.com", "sent")):
        cid = await contact(email, owner=1)
        await run("INSERT INTO campaign_contacts(campaign_id, contact_id, status) VALUES (?,?,?)", (camp, cid, status))
    await contact("A-Draft@acme.com")  # same mailbox, other case: checked once
    await contact("cat-low@beta.com", confidence="low")
    await contact("cat-high@beta.com", confidence="high")
    await contact("cat-junk@beta.com", ai="junk")
    await contact("cat-dead@beta.com", status="dead")
    await contact("info@beta.com")
    await contact("someone@gmail.com")
    await contact("priv@beta.com", owner=1)
    await contact("fresh@beta.com")
    await verdict("fresh@beta.com", "provider_high_confidence")
    await contact("supp@beta.com")
    await run("INSERT INTO candidate_suppressions(email, state, observed_at) VALUES ('supp@beta.com','permanent_failure','2026-01-01')")
    await contact("delivered@beta.com")
    await mail_event("delivered@beta.com", "delivered")
    await run_prospects([("olga@gamma.com", 90, None)])
    await run_prospects([("nick@gamma.com", 10, "real"), ("anna@gamma.com", 80, None), ("junk@gamma.com", 99, "junk"),
                         ("cat-high@beta.com", 70, None), ("", 5, None)])
    expected = ["a-rel@acme.com", "a-draft@acme.com", "cat-high@beta.com", "cat-low@beta.com",
                "anna@gamma.com", "nick@gamma.com", "olga@gamma.com"]
    answers["nick@gamma.com"] = "accept_all_or_risky"
    db = await get_db()
    try:
        before = await MV.backlog_status(db)
    finally:
        await db.close()
    assert before["queue_remaining"] == len(expected) and before["enabled"] and before["credits_cap_today"] == 25, before
    with mocked():
        result = await MV.run_mailbox_backlog()
    assert calls == expected, calls
    assert result["spent"] == len(expected) == await credits_used()
    db = await get_db()
    try:
        after = await MV.backlog_status(db)
    finally:
        await db.close()
    assert after["queue_remaining"] == 0 and after["verified_total"] == len(expected) + 1, after  # + the seeded fresh verdict
    status = {r["email"]: r["email_verification_status"] for r in await rows("SELECT email, email_verification_status FROM contacts")}
    assert status["cat-high@beta.com"] == "valid" and status["cat-dead@beta.com"] == "dead", status
    prospects = {r["email"]: r["email_verification_status"] for r in await rows("SELECT email, email_verification_status FROM yucgoutreach_prospects")}
    assert prospects["nick@gamma.com"] == "likely_valid" and prospects["anna@gamma.com"] == "valid", prospects
    assert prospects["junk@gamma.com"] is None


async def job_keeps_the_presend_reserve() -> None:
    await reset()
    await run_prospects([(f"p{n:02d}@volume.com", 100 - n, None) for n in range(40)])
    with mocked():
        first = await MV.run_mailbox_backlog()
        second = await MV.run_mailbox_backlog()
        third = await MV.run_mailbox_backlog()
        assert len(calls) == 20 and first["spent"] == 20, (calls, first)  # 25 - reserve 5
        assert second.get("skipped") == "reserve" and third.get("skipped") == "reserve"
        presend = await EV.assess_address("late@volume.com", actor_id=1, external=True)  # the reserve is still there
    assert presend["provider_state"] == "complete" and await credits_used() == 21
    # Credits a member spent today count against the job's budget too.
    await reset()
    await run_prospects([(f"q{n:02d}@volume.com", 100 - n, None) for n in range(40)])
    with mocked():
        for n in range(10):
            await EV.assess_address(f"member{n}@other.com", actor_id=1, external=True)
        calls.clear()
        await MV.run_mailbox_backlog()
    assert len(calls) == 10 and await credits_used() == 20


async def job_stops_on_provider_trouble() -> None:
    for outcome in ("unavailable", "exhausted"):
        await reset()
        await run_prospects([(f"s{n}@stop.com", 50 - n, None) for n in range(5)])
        answers["s1@stop.com"] = outcome
        with mocked():
            await MV.run_mailbox_backlog()
        assert calls == ["s0@stop.com", "s1@stop.com"], (outcome, calls)


async def job_does_nothing_when_off() -> None:
    await reset()
    await run_prospects([("off@off.com", 50, None)])
    for env in ({"EXTERNAL_EMAIL_VERIFICATION_ENABLED": "false"}, {"INBOX_VERIFY_PROVIDER": "smtp"}, {"VERIFALIA_API_KEY": ""}):
        with mocked(), patch.dict(os.environ, env):
            assert (await MV.run_mailbox_backlog()).get("skipped") == "disabled", env
    await run("INSERT INTO verification_provider_control(provider, disabled) VALUES ('verifalia', 1)")
    with mocked():
        assert (await MV.run_mailbox_backlog()).get("skipped") == "disabled"
    assert calls == [] and dns_calls == []


async def domain_without_mail_route_does_not_block_the_queue() -> None:
    await reset()
    await run_prospects([("first@nomx.example", 90, None), ("second@fine.com", 10, None)])
    with mocked():
        first = await MV.run_mailbox_backlog()
        second = await MV.run_mailbox_backlog()
    assert calls == ["second@fine.com"] and first["spent"] == 1 and first["local"] == 1, (calls, first)
    assert dns_calls.count("first@nomx.example") == 1, dns_calls  # looked up once, then remembered
    assert second["queued"] == 0
    assert await credits_used() == 1


async def main() -> None:
    await migration_applies_once()
    await verdict_is_shared_between_members()
    await ceiling_is_250()
    await rejected_verdict_suppresses_and_marks_dead()
    await proof_precedence()
    await job_follows_queue_and_skips()
    await job_keeps_the_presend_reserve()
    await job_stops_on_provider_trouble()
    await job_does_nothing_when_off()
    await domain_without_mail_route_does_not_block_the_queue()


def test_mailbox_verdicts() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    test_mailbox_verdicts()
    print("ok")
