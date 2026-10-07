"""What the club is about to say next, and what quietly stopped saying it.

A sequence runs unattended once a campaign is released. The steps were
definable and the campaign readable, but nothing could answer "who hears from
us tomorrow" - and a sequence that paused because its campaign needs attention
looked exactly like one that had finished.
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JWT_SECRET", "follow-up-schedule-secret-xxxxxxxx")

from app.database import get_db, init_db
from app.routers.outreach import follow_up_schedule


async def _seed(db) -> None:
    await db.execute(
        """INSERT INTO users(id,email,name,role,is_active)
           VALUES (1,'me@yale.edu','Me','standard',1),(2,'other@yale.edu','Other','standard',1)""")
    await db.execute("INSERT INTO follow_up_sequences(id,name,user_id) VALUES (1,'Polite two-step',1)")
    await db.execute(
        """INSERT INTO follow_up_steps(sequence_id,days_after,subject,body,step_order)
           VALUES (1,3,'Following up','b',0)""")
    await db.execute(
        """INSERT INTO campaigns(id,name,status,owner_user_id,sender_user_id,sequence_id)
           VALUES (1,'Acme first touch','sent',1,1,1),
                  (2,'Paused run','needs_attention',1,1,1),
                  (3,'Someone else','sent',2,2,1)""")
    people = [
        (1, 'Ada Lovelace', 'ada@acme.com'), (2, 'Grace Hopper', 'grace@acme.com'),
        (3, 'Alan Turing', 'alan@acme.com'), (4, 'Jean Bartik', 'jean@quiet.io'),
        (5, 'Klara Dan', 'klara@other.com'),
    ]
    for cid, name, email in people:
        await db.execute("INSERT INTO contacts(id,name,email,company,owner_id) VALUES (?,?,?,'Acme Corp',1)",
                         (cid, name, email))
    # Overdue, upcoming, replied, in a paused campaign, and another member's.
    rows = [
        (1, 1, 1, "datetime('now','-10 days')", None, 'sent'),
        (2, 1, 2, "datetime('now','-1 days')", None, 'sent'),
        (3, 1, 3, "datetime('now','-10 days')", "datetime('now','-2 days')", 'sent'),
        (4, 2, 4, "datetime('now','-10 days')", None, 'sent'),
        (5, 3, 5, "datetime('now','-10 days')", None, 'sent'),
    ]
    for cc_id, campaign, contact, last_sent, replied, status in rows:
        await db.execute(
            f"""INSERT INTO campaign_contacts
                (id,campaign_id,contact_id,email_subject,email_body,status,sent_at,
                 last_sequence_sent_at,sequence_step_sent,replied_at,sent_by_user_id)
                VALUES (?,?,?,'s','b',?,{last_sent},{last_sent},0,{replied or 'NULL'},1)""",
            (cc_id, campaign, contact, status))
        # The next step's intent, snapshotted at release exactly as the job reads it.
        await db.execute(
            """INSERT INTO outreach_dispatches
               (dispatch_key,campaign_contact_id,sender_user_id,recipient,subject,body,delay_days)
               VALUES (?,?,1,'x@y.z','Following up','b',3)""",
            (f"followup:{cc_id}:0", cc_id))
    await db.commit()


def tests() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["DATABASE_URL"] = "sqlite:///" + tmp + "/sched.db"

        async def scenario() -> None:
            await init_db()
            db = await get_db()
            await _seed(db)
            await db.close()

            out = await follow_up_schedule({"id": 1, "role": "standard"})
            queued = {row["contact_name"]: row for row in out["scheduled"]}
            stopped = {row["contact_name"]: row["reason"] for row in out["stopped"]}

            # Due ten days ago against a three-day step: overdue, and said so.
            assert queued["Ada Lovelace"]["overdue"] is True, queued["Ada Lovelace"]
            # Sent yesterday: still waiting, with the date it will go.
            assert queued["Grace Hopper"]["overdue"] is False
            assert queued["Grace Hopper"]["due_on"], queued["Grace Hopper"]

            assert stopped["Alan Turing"] == "They replied"
            # The one that matters: paused campaigns stop follow-ups and say
            # so nowhere else in the app.
            assert "needs_attention" in stopped["Jean Bartik"], stopped
            assert "paused" in stopped["Jean Bartik"]

            # Another member's campaign is not this member's schedule.
            assert "Klara Dan" not in queued and "Klara Dan" not in stopped

        asyncio.run(scenario())


if __name__ == "__main__":
    tests()
    print("follow-up schedule: ok")
