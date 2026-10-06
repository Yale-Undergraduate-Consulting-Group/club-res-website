"""
Follow-up sequence job: send due follow-up emails for campaigns that have a sequence attached.
Run daily. Uses the explicitly bound original sender and durable step claims.

Skips contacts who have already replied (replied_at set or status = 'replied'), so follow-ups
only go to recipients who have not responded. Replies are recorded by sender-scoped Gmail sync
or by the campaign owner's manual mark.
"""
from datetime import datetime, timezone
from app.database import get_db


async def run_follow_up_sequences() -> dict:
    """
    Find campaign_contacts where the next sequence step is due and send it.
    Returns {"sent": count, "errors": [...]}.
    """
    from app.services.gmail_api import send_via_gmail_api_with_tracking

    db = await get_db()
    try:
        # Campaigns with a sequence attached
        cursor = await db.execute(
            "SELECT id, sequence_id, owner_user_id, sender_user_id FROM campaigns WHERE sequence_id IS NOT NULL AND status = 'sent'"
        )
        campaigns = await cursor.fetchall()
        if not campaigns:
            return {"sent": 0, "errors": []}

        today = datetime.now(timezone.utc).date()
        sent = 0
        errors = []

        for camp in campaigns:
            cid = camp["id"]
            seq_id = camp["sequence_id"]
            # Templates never delegate the creator's mailbox.
            sender_user_id = camp["sender_user_id"]
            if not sender_user_id or camp["owner_user_id"] != sender_user_id:
                errors.append({"campaign_id": cid, "error": "Sender ownership requires reconciliation"})
                continue


            # Campaign contacts: initial send done, sequence not finished, no reply yet
            cursor = await db.execute(
                """SELECT cc.id, cc.contact_id, cc.sequence_step_sent, cc.last_sequence_sent_at, cc.sent_by_user_id
                   FROM campaign_contacts cc
                   JOIN contacts c ON c.id = cc.contact_id
                   WHERE cc.campaign_id = ? AND cc.status = 'sent'
                     AND cc.last_sequence_sent_at IS NOT NULL
                     AND cc.replied_at IS NULL""",
                (cid,),
            )
            contacts = await cursor.fetchall()

            for cc in contacts:
                if cc["sent_by_user_id"] != sender_user_id:
                    errors.append({"campaign_contact_id": cc["id"], "error": "Original sender does not match campaign"})
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
                from datetime import timedelta
                due_date = last_date + timedelta(days=days_after)
                if due_date > today:
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
                    """SELECT cc.id FROM campaign_contacts cc JOIN campaigns camp ON camp.id=cc.campaign_id
                    JOIN users u ON u.id=camp.sender_user_id
                    WHERE cc.id=? AND cc.sequence_step_sent=? AND cc.status='sent' AND cc.replied_at IS NULL
                    AND cc.sent_by_user_id=? AND camp.sender_user_id=? AND camp.owner_user_id=?
                    AND camp.status='sent' AND camp.sequence_id=? AND u.is_active=1""",
                    (cc["id"], step_idx, sender_user_id, sender_user_id, sender_user_id, seq_id),
                )).fetchone()
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

        return {"sent": sent, "errors": errors}
    finally:
        await db.close()
