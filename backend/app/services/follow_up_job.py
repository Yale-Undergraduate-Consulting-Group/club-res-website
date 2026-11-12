"""
Follow-up sequence job: send due follow-up emails for campaigns that have a sequence attached.
Runs hourly so recipient-local send windows in every time zone are reachable. Uses the explicitly
bound original sender and durable step claims.

A follow-up goes to a contact who was already mailed, so a campaign that is held for unsent rows
(`needs_attention`) or still releasing keeps following up; only a paused campaign stops. A sequence
stops for a contact who replied (an opt-out is a reply: `replied_at` is set) or whose address bounced.
An automatic reply (out of office) pushes the next step back two business days from that reply
rather than sending into the absence. A due step waits for the next run unless the recipient's local
time is inside the send window, and it counts against the sender's own daily limit.
"""
from datetime import datetime, timedelta, timezone
from app.database import get_db
from app.services.send_gating import company_reply, replied_companies

AUTO_REPLY_DEFER_BUSINESS_DAYS = 2
# A paused campaign stops its follow-ups; the gate's hold concerns unsent rows only.
FOLLOW_UP_CAMPAIGN_STATES = ("sent", "needs_attention", "releasing")


def _add_business_days(day, days: int):
    while days > 0:
        day += timedelta(days=1)
        if day.weekday() < 5:
            days -= 1
    return day


async def _thread_signal(db, campaign_contact_id: int):
    """(stopped, auto_reply_date): did the thread bounce or get a reply, and when did the
    latest automatic reply arrive."""
    rows = await (await db.execute(
        """SELECT e.kind, MAX(e.occurred_at) AS at FROM outreach_events e
           JOIN outreach_messages m ON m.id = e.message_id
           WHERE m.campaign_contact_id = ? AND e.kind IN ('bounced', 'replied', 'auto_reply')
           GROUP BY e.kind""",
        (campaign_contact_id,),
    )).fetchall()
    kinds = {row["kind"]: row["at"] for row in rows}
    if "bounced" in kinds or "replied" in kinds:
        return True, None
    auto = kinds.get("auto_reply")
    if not auto:
        return False, None
    try:
        return False, datetime.fromisoformat(str(auto).replace("Z", "+00:00")).date()
    except ValueError:
        return False, None


async def run_follow_up_sequences() -> dict:
    """
    Find campaign_contacts where the next sequence step is due and send it.
    Returns {"sent": count, "waiting": due steps held for the send window, "errors": [...]}.
    """
    from app.services import send_window
    from app.services.gmail_api import send_via_gmail_api_with_tracking

    db = await get_db()
    try:
        # Campaigns with a sequence attached
        marks = ",".join("?" * len(FOLLOW_UP_CAMPAIGN_STATES))
        cursor = await db.execute(
            f"SELECT id, sequence_id, owner_user_id, sender_user_id FROM campaigns WHERE sequence_id IS NOT NULL AND status IN ({marks})",
            FOLLOW_UP_CAMPAIGN_STATES,
        )
        campaigns = await cursor.fetchall()
        if not campaigns:
            return {"sent": 0, "waiting": 0, "errors": []}

        today = datetime.now(timezone.utc).date()
        sent = 0
        waiting = 0
        errors = []

        for camp in campaigns:
            cid = camp["id"]
            seq_id = camp["sequence_id"]
            # Templates never delegate the creator's mailbox.
            sender_user_id = camp["sender_user_id"]
            if not sender_user_id or camp["owner_user_id"] != sender_user_id:
                errors.append({"campaign_id": cid, "error": "Sender ownership requires reconciliation"})
                continue
            # A sender who lost access to Outreach sends no more follow-ups.
            from app.app_access import may_use
            if not await may_use(db, sender_user_id, "outreach"):
                errors.append({"campaign_id": cid, "error": "Sender no longer has access to Outreach"})
                continue

            # Campaign contacts: initial send done, sequence not finished, no reply or bounce yet
            cursor = await db.execute(
                """SELECT cc.id, cc.contact_id, cc.sequence_step_sent, cc.last_sequence_sent_at, cc.sent_by_user_id,
                          c.company, c.company_domain, c.email
                   FROM campaign_contacts cc
                   JOIN contacts c ON c.id = cc.contact_id
                   WHERE cc.campaign_id = ? AND cc.status = 'sent'
                     AND cc.last_sequence_sent_at IS NOT NULL
                     AND cc.replied_at IS NULL""",
                (cid,),
            )
            contacts = await cursor.fetchall()
            replied = await replied_companies(db)

            for cc in contacts:
                if cc["sent_by_user_id"] != sender_user_id:
                    errors.append({"campaign_contact_id": cc["id"], "error": "Original sender does not match campaign"})
                    continue
                # Someone at this company answered a member: nobody follows up there any more.
                if company_reply(replied, cc["company"], cc["company_domain"], cc["email"]):
                    continue
                step_idx = cc["sequence_step_sent"]
                step = await (await db.execute(
                    "SELECT * FROM outreach_dispatches WHERE dispatch_key=? AND sender_user_id=? AND state='ready'",
                    (f"followup:{cc['id']}:{step_idx}", sender_user_id),
                )).fetchone()
                if not step:
                    continue
                days_after = step["delay_days"] or 0
                last_sent = cc["last_sequence_sent_at"]
                if last_sent is None:
                    continue
                # Parse last_sequence_sent_at (e.g. "2025-03-10 12:00:00")
                try:
                    if hasattr(last_sent, "date"):
                        last_date = last_sent.date() if hasattr(last_sent, "date") else last_sent
                    else:
                        last_date = datetime.fromisoformat(str(last_sent).replace("Z", "+00:00")).date()
                except Exception:
                    continue
                due_date = last_date + timedelta(days=days_after)
                if due_date > today:
                    continue
                stopped, auto_reply_on = await _thread_signal(db, cc["id"])
                if stopped:
                    continue
                if auto_reply_on:
                    # Out of office: wait two business days past the automatic reply.
                    if _add_business_days(auto_reply_on, AUTO_REPLY_DEFER_BUSINESS_DAYS) > today:
                        continue
                # Outside the recipient's local business hours the step waits for a later run.
                if not send_window.open_now(await send_window.recipient_zone(db, cc["contact_id"])):
                    waiting += 1
                    continue

                # Get contact email
                cursor = await db.execute(
                    "SELECT email FROM contacts WHERE id = ?", (cc["contact_id"],)
                )
                contact_row = await cursor.fetchone()
                if not contact_row:
                    continue
                to_email = contact_row["email"]
                subject = step["subject"] or "Following up"
                body = step["body"] or ""

                from app.services.dispatch_service import begin_write, snapshot, claim, finish
                key = f"followup:{cc['id']}:{step_idx}"
                await begin_write(db)
                current = await (await db.execute(
                    f"""SELECT cc.id FROM campaign_contacts cc JOIN campaigns camp ON camp.id=cc.campaign_id
                    JOIN users u ON u.id=camp.sender_user_id
                    WHERE cc.id=? AND cc.sequence_step_sent=? AND cc.status='sent' AND cc.replied_at IS NULL
                    AND cc.sent_by_user_id=? AND camp.sender_user_id=? AND camp.owner_user_id=?
                    AND camp.status IN ({marks}) AND camp.sequence_id=? AND u.is_active=1""",
                    (cc["id"], step_idx, sender_user_id, sender_user_id, sender_user_id, *FOLLOW_UP_CAMPAIGN_STATES, seq_id),
                )).fetchone()
                if current and company_reply(
                    await replied_companies(db), cc["company"], cc["company_domain"], cc["email"]
                ):
                    current = None
                if not current:
                    await db.commit()
                    continue
                original = await (await db.execute(
                    "SELECT recipient FROM outreach_dispatches WHERE dispatch_key=? AND state='sent'",
                    (f"initial:{cc['id']}",),
                )).fetchone()
                if original:
                    to_email = original["recipient"]
                else:
                    # Never silently retarget a follow-up after shared contact edits.
                    history = await (await db.execute(
                        "SELECT recipient FROM outreach_messages WHERE campaign_contact_id=? AND sender_id=? AND sent_at IS NOT NULL ORDER BY id LIMIT 1",
                        (cc["id"], sender_user_id),
                    )).fetchone()
                    if not history:
                        await db.commit()
                        errors.append({"campaign_contact_id": cc["id"], "error": "Original recipient requires reconciliation"})
                        continue
                    to_email = history["recipient"]
                await snapshot(db, key, cc["id"], sender_user_id, to_email, subject, body)
                intent = await claim(db, key, sender_user_id)
                await db.commit()
                if not intent:
                    continue
                try:
                    from app.services.settings_service import load_sign_off
                    send_meta = await send_via_gmail_api_with_tracking(
                        user_id=sender_user_id,
                        to_email=intent["recipient"],
                        subject=intent["subject"],
                        body=intent["body"],
                        campaign_contact_id=cc["id"],
                        dispatch_key=key,
                        sign_off=await load_sign_off(sender_user_id),
                    )
                    tid = send_meta.get("thread_id")
                    mid = send_meta.get("message_id")
                    await db.execute(
                        """UPDATE campaign_contacts SET sequence_step_sent = ?, last_sequence_sent_at = CURRENT_TIMESTAMP,
                           sent_by_user_id = COALESCE(?, sent_by_user_id),
                           gmail_thread_id = COALESCE(?, gmail_thread_id),
                           gmail_message_id = ?
                           WHERE id = ?""",
                        (step_idx + 1, sender_user_id, tid, mid, cc["id"]),
                    )
                    await finish(db, key, send_meta)
                    await db.commit()
                    sent += 1
                except Exception as e:
                    from app.services.gmail_api import DeliveryNotAttemptedError
                    safe_to_retry = isinstance(e, DeliveryNotAttemptedError)
                    if safe_to_retry:
                        await db.execute(
                            "DELETE FROM outreach_messages WHERE dispatch_key=? AND sent_at IS NULL",
                            (key,),
                        )
                    await finish(db, key, error=e, safe_to_retry=safe_to_retry)
                    await db.commit()
                    errors.append({"campaign_contact_id": cc["id"], "error": str(e)})

        return {"sent": sent, "waiting": waiting, "errors": errors}
    finally:
        await db.close()
