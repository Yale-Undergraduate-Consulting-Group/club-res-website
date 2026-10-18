"""The first address to a company proves the format; the rest wait on it.

Every address here was derived from a guessed format, and mx-mode verification
only proves the domain accepts mail. Mailing a whole company on an unproven
format turns one wrong guess into a batch of hard bounces from a member's real
Gmail account.
"""
import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["JWT_SECRET"] = "send-gating-test-secret-xxxxxxxxxxxx"
os.environ["CAMPAIGN_SEND_DELAY_SEC"] = "0"

from app.database import get_db, init_db
from app.routers.campaigns import drain_campaign
from app.services import send_gating


def _iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


async def _seed(db, *, people: int, host: str = "acme.com") -> None:
    await db.execute("INSERT INTO users(id,email,role,is_active) VALUES (1,'me@yale.edu','standard',1)")
    await db.execute(
        "INSERT INTO campaigns(id,name,status,owner_user_id,sender_user_id) VALUES (1,'Acme','releasing',1,1)"
    )
    for i in range(1, people + 1):
        await db.execute(
            "INSERT INTO contacts(id,name,email,owner_id) VALUES (?,?,?,NULL)",
            (i, f"Person {i}", f"person{i}@{host}"),
        )
        await db.execute(
            """INSERT INTO campaign_contacts(id,campaign_id,contact_id,email_subject,email_body,status)
               VALUES (?,1,?,?,?,'pending')""",
            (i, i, f"Subject {i}", f"Body {i}"),
        )
    await db.commit()


async def _drain(sent_log: list[str]) -> dict:
    async def fake_send(**kwargs):
        sent_log.append(kwargs["to_email"])
        return {"thread_id": f"t{len(sent_log)}", "message_id": f"m{len(sent_log)}"}

    with patch("app.services.gmail_api.send_via_gmail_api_with_tracking", fake_send), \
         patch("app.services.dispatch_service.claim", _passthrough_claim), \
         patch("app.services.dispatch_service.finish", _noop):
        return await drain_campaign(1, 1, limit=10)


async def _passthrough_claim(db, key, sender):
    row_id = int(key.split(":")[1])
    row = await (await db.execute(
        """SELECT cc.email_subject, cc.email_body, c.email
           FROM campaign_contacts cc JOIN contacts c ON c.id = cc.contact_id WHERE cc.id=?""",
        (row_id,),
    )).fetchone()
    return {
        "recipient": row["email"],
        "subject": row["email_subject"],
        "body": row["email_body"],
    }


async def _noop(*args, **kwargs):
    return None


async def one_probe_then_the_rest() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["DATABASE_URL"] = "sqlite:///" + tmp + "/gate.db"
        await init_db()
        db = await get_db()
        await _seed(db, people=6)
        await db.close()

        # Nothing is known about acme.com, so exactly one address goes out even
        # though the tick was allowed to send ten.
        sent: list[str] = []
        first = await _drain(sent)
        assert sent == ["person1@acme.com"], sent
        assert first["held"] == 5, first
        assert "held until the first address" in first["hold_reason"]
        # The queue is still working, not broken.
        assert first["status"] == "releasing", first

        # A second tick inside the grace period adds nobody: the probe has not
        # been proven yet.
        second = await _drain(sent)
        assert sent == ["person1@acme.com"], sent
        assert second["sent"] == 0 and second["held"] == 5

        # Quiet past the grace period means no bounce came back, so the rest go.
        db = await get_db()
        await db.execute(
            "UPDATE campaign_contacts SET sent_at=? WHERE id=1",
            (_iso(datetime.now(timezone.utc) - timedelta(minutes=send_gating.grace_minutes() + 1)),),
        )
        await db.commit()
        await db.close()
        third = await _drain(sent)
        assert third["sent"] == 5, third
        assert len(sent) == 6 and third["held"] == 0
        assert third["status"] == "sent"


async def a_bounced_probe_stops_the_company() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["DATABASE_URL"] = "sqlite:///" + tmp + "/bounce.db"
        await init_db()
        db = await get_db()
        await _seed(db, people=4)
        await db.close()

        sent: list[str] = []
        await _drain(sent)
        assert sent == ["person1@acme.com"]

        # gmail_reply_sync marks the probe bounced. The three behind it were
        # built from the same broken format, so they must not follow.
        db = await get_db()
        await db.execute("UPDATE campaign_contacts SET status='bounced' WHERE id=1")
        await db.commit()
        await db.close()

        after = await _drain(sent)
        assert sent == ["person1@acme.com"], sent
        assert after["held"] == 3
        assert "bounced" in after["hold_reason"] and "email format" in after["hold_reason"]
        # And it stops asking: a human has to supply the format.
        assert after["status"] == "needs_attention", after
        db = await get_db()
        row = await (await db.execute("SELECT status FROM campaigns WHERE id=1")).fetchone()
        assert row["status"] == "needs_attention"
        await db.close()


async def a_proven_company_is_never_throttled() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["DATABASE_URL"] = "sqlite:///" + tmp + "/proven.db"
        await init_db()
        db = await get_db()
        await _seed(db, people=4)
        # A delivery has already been observed against this format.
        await db.execute(
            """INSERT INTO company_email_patterns(company_domain,pattern_key,pattern_template,
                   confidence,sample_count,verified_samples,sources_json)
               VALUES ('acme.com','first.last','{first}.{last}',0.9,3,2,'["gmail_reply"]')"""
        )
        await db.commit()
        await db.close()

        sent: list[str] = []
        result = await _drain(sent)
        assert len(sent) == 4, sent
        assert result["held"] == 0 and result["status"] == "sent"


async def a_member_stated_format_counts_as_proof() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["DATABASE_URL"] = "sqlite:///" + tmp + "/asserted.db"
        await init_db()
        db = await get_db()
        await _seed(db, people=3)
        # No delivery yet, but a member stated the format outright.
        await db.execute(
            """INSERT INTO company_email_patterns(company_domain,pattern_key,pattern_template,
                   confidence,sample_count,verified_samples,sources_json)
               VALUES ('acme.com','first.last','{first}.{last}',0.8,0,0,'["member:1"]')"""
        )
        await db.commit()
        await db.close()

        sent: list[str] = []
        result = await _drain(sent)
        assert len(sent) == 3 and result["held"] == 0


async def one_held_company_does_not_stall_another() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["DATABASE_URL"] = "sqlite:///" + tmp + "/two.db"
        await init_db()
        db = await get_db()
        await _seed(db, people=3)                      # acme.com, ids 1-3
        await db.execute(
            """INSERT INTO company_email_patterns(company_domain,pattern_key,pattern_template,
                   confidence,sample_count,verified_samples,sources_json)
               VALUES ('proven.org','first.last','{first}.{last}',0.9,2,2,'["gmail_reply"]')"""
        )
        for i in (4, 5):
            await db.execute(
                "INSERT INTO contacts(id,name,email,owner_id) VALUES (?,?,?,NULL)",
                (i, f"Other {i}", f"other{i}@proven.org"),
            )
            await db.execute(
                """INSERT INTO campaign_contacts(id,campaign_id,contact_id,email_subject,email_body,status)
                   VALUES (?,1,?,'s','b','pending')""",
                (i, i),
            )
        await db.commit()
        await db.close()

        # The unproven company sits at the head of the queue. It must not block
        # the proven one behind it.
        sent: list[str] = []
        result = await _drain(sent)
        assert sorted(sent) == ["other4@proven.org", "other5@proven.org", "person1@acme.com"], sent
        assert result["held"] == 2


async def a_teammate_who_already_wrote_blocks_a_second_email() -> None:
    """Two members of one society writing to the same person in the same week
    is the failure a client actually notices. Nothing downstream would catch
    it: the club sends as individuals, so each member's queue looks clean."""
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["DATABASE_URL"] = "sqlite:///" + tmp + "/teammate.db"
        await init_db()
        db = await get_db()
        await _seed(db, people=2)
        await db.execute("INSERT INTO users(id,email,name,role,is_active) VALUES (2,'aaron@yale.edu','Aaron',       'standard',1)")
        # Aaron wrote to person1 last week, from his own campaign.
        await db.execute(
            "INSERT INTO campaigns(id,name,status,owner_user_id,sender_user_id) VALUES (2,'Aaron run','sent',2,2)")
        await db.execute(
            """INSERT INTO campaign_contacts(campaign_id,contact_id,email_subject,email_body,status,sent_at,sent_by_user_id)
               VALUES (2,1,'s','b','sent',datetime('now','-7 days'),2)""")
        # acme.com is proven, so nothing else can explain a hold.
        await db.execute(
            """INSERT INTO company_email_patterns(company_domain,pattern_key,pattern_template,
                   confidence,sample_count,verified_samples,sources_json)
               VALUES ('acme.com','first.last','{first}.{last}',0.9,3,2,'["gmail_reply"]')""")
        await db.commit()
        await db.close()

        sent: list[str] = []
        result = await _drain(sent)
        assert sent == ["person2@acme.com"], sent
        assert result["held"] == 1, result
        assert "Aaron already wrote to them" in result["hold_reason"], result["hold_reason"]
        # A colleague writing to a different person at the same company is
        # fine and often deliberate, so person2 went.

        # And the block is on teammates, not on the member's own history: a
        # follow-up campaign to someone you wrote to yourself must still send.
        db = await get_db()
        await db.execute("UPDATE campaign_contacts SET sent_by_user_id = 1 WHERE campaign_id = 2")
        await db.execute("UPDATE campaign_contacts SET status='pending', sent_at=NULL WHERE campaign_id = 1")
        await db.execute("UPDATE campaigns SET status='releasing' WHERE id=1")
        await db.commit()
        await db.close()
        sent2: list[str] = []
        again = await _drain(sent2)
        assert sorted(sent2) == ["person1@acme.com", "person2@acme.com"], sent2
        assert again["held"] == 0


def tests() -> None:
    asyncio.run(one_probe_then_the_rest())
    asyncio.run(a_bounced_probe_stops_the_company())
    asyncio.run(a_proven_company_is_never_throttled())
    asyncio.run(a_member_stated_format_counts_as_proof())
    asyncio.run(one_held_company_does_not_stall_another())
    asyncio.run(a_teammate_who_already_wrote_blocks_a_second_email())


if __name__ == "__main__":
    tests()
    print("send gating: ok")
