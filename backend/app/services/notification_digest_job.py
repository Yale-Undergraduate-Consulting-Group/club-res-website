"""
Daily notification digest: for users with admin_digest or campaign_summary enabled,
build a short digest and send via Slack (if connected). Run daily after follow-ups.
"""
import os
from datetime import datetime, timezone, timedelta

from app.database import get_db


async def run_notification_digests() -> dict:
    """
    For each user with notification prefs, build digest and send via Slack.
    Returns {"sent": count, "errors": [...]}.
    """
    import httpx

    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT user_id, admin_digest, campaign_summary FROM notification_preferences
               WHERE admin_digest = 1 OR campaign_summary = 1"""
        )
        prefs = await cursor.fetchall()
        if not prefs:
            return {"sent": 0, "errors": []}

        sent = 0
        errors = []
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")

        for row in prefs:
            user_id = row["user_id"]
            admin_digest = row["admin_digest"]
            campaign_summary = row["campaign_summary"]

            cursor = await db.execute(
                "SELECT user_slack_id FROM user_slack_tokens WHERE user_id = ?",
                (user_id,),
            )
            slack_row = await cursor.fetchone()
            if not slack_row:
                continue

            parts = []
            if admin_digest:
                cursor = await db.execute(
                    """SELECT action, resource_type, resource_id, details, created_at
                       FROM audit_log WHERE created_at >= ? ORDER BY created_at DESC LIMIT 15""",
                    (since,),
                )
                audit_rows = await cursor.fetchall()
                if audit_rows:
                    parts.append("*Recent activity (24h)*")
                    for r in audit_rows:
                        parts.append(f"• {r['action']} {r['resource_type'] or ''} {r['resource_id'] or ''}")

            if campaign_summary:
                cursor = await db.execute(
                    """SELECT c.name, COUNT(cc.id) as sent
                       FROM campaigns c
                       JOIN campaign_contacts cc ON cc.campaign_id = c.id AND cc.status = 'sent'
                       WHERE date(cc.sent_at) >= date('now', '-7 days')
                       GROUP BY c.id"""
                )
                camp_rows = await cursor.fetchall()
                if camp_rows:
                    parts.append("*Campaigns (last 7 days)*")
                    for r in camp_rows:
                        parts.append(f"• {r['name']}: {r['sent']} sent")

            if not parts:
                continue

            text = "YUCG Outreach – Daily digest\n\n" + "\n".join(parts)

            # Rotation means the token stored at install has almost certainly
            # expired by the time this nightly job runs; ask for a live one.
            from app.services.slack_tokens import (
                SlackReauthorizationRequired, slack_access_token,
            )

            try:
                token = await slack_access_token(user_id)
            except SlackReauthorizationRequired as exc:
                errors.append({"user_id": user_id, "error": f"Slack must be reconnected ({exc})"})
                continue
            if not token:
                errors.append({"user_id": user_id, "error": "No usable Slack token"})
                continue
            user_slack_id = slack_row["user_slack_id"]
            async with httpx.AsyncClient(timeout=10.0) as client:
                try:
                    open_res = await client.post(
                        "https://slack.com/api/conversations.open",
                        json={"users": [user_slack_id]},
                        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    )
                    if not open_res.json().get("ok"):
                        errors.append({"user_id": user_id, "error": "Slack DM open failed"})
                        continue
                    channel = open_res.json().get("channel", {}).get("id")
                    if not channel:
                        continue
                    msg_res = await client.post(
                        "https://slack.com/api/chat.postMessage",
                        json={"channel": channel, "text": text},
                        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    )
                    if msg_res.json().get("ok"):
                        sent += 1
                    else:
                        errors.append({"user_id": user_id, "error": msg_res.text or "postMessage failed"})
                except Exception as e:
                    errors.append({"user_id": user_id, "error": str(e)})

        # If no DMs were sent, post one digest to the club channel. The token
        # comes from an install where possible: with rotation enabled, a token
        # pasted into SLACK_BOT_TOKEN stops working twelve hours later.
        from app.services.slack_tokens import workspace_access_token

        channel_id = (os.getenv("SLACK_DIGEST_CHANNEL_ID") or "").strip()
        bot_token = await workspace_access_token() if channel_id else None
        if sent == 0 and bot_token and channel_id:
            parts = []
            cursor = await db.execute(
                """SELECT action, resource_type, resource_id, details, created_at
                   FROM audit_log WHERE created_at >= ? ORDER BY created_at DESC LIMIT 15""",
                (since,),
            )
            audit_rows = await cursor.fetchall()
            if audit_rows:
                parts.append("*Recent activity (24h)*")
                for r in audit_rows:
                    parts.append(f"• {r['action']} {r['resource_type'] or ''} {r['resource_id'] or ''}")
            cursor = await db.execute(
                """SELECT c.name, COUNT(cc.id) as sent
                   FROM campaigns c
                   JOIN campaign_contacts cc ON cc.campaign_id = c.id AND cc.status = 'sent'
                   WHERE date(cc.sent_at) >= date('now', '-7 days')
                   GROUP BY c.id"""
            )
            camp_rows = await cursor.fetchall()
            if camp_rows:
                parts.append("*Campaigns (last 7 days)*")
                for r in camp_rows:
                    parts.append(f"• {r['name']}: {r['sent']} sent")
            if parts:
                text = "YUCG Outreach – Daily digest\n\n" + "\n".join(parts)
                async with httpx.AsyncClient(timeout=10.0) as client:
                    try:
                        msg_res = await client.post(
                            "https://slack.com/api/chat.postMessage",
                            json={"channel": channel_id, "text": text},
                            headers={"Authorization": f"Bearer {bot_token}", "Content-Type": "application/json"},
                        )
                        if msg_res.json().get("ok"):
                            sent += 1
                        else:
                            errors.append({"user_id": None, "error": msg_res.text or "channel postMessage failed"})
                    except Exception as e:
                        errors.append({"user_id": None, "error": str(e)})

        return {"sent": sent, "errors": errors}
    finally:
        await db.close()
