"""Environment guard applies at every mail transport boundary, including manual tests."""
import os


def require_delivery_enabled():
    if os.getenv('APP_ENV', 'production').lower() == 'beta' or os.getenv('EMAIL_DELIVERY_ENABLED', 'true').lower() != 'true':
        raise ValueError('Email delivery is disabled in this environment')

async def recipient_blocked_reason(db, email: str) -> str | None:
    """Address-wide safety state applies even if a shared contact was retargeted."""
    from app.services.contact_intelligence import address_is_suppressed
    if not email or "@" not in email:
        return None
    if await address_is_suppressed(db, email):
        return "suppressed"
    failure = await (await db.execute(
        """SELECT 1 FROM outreach_events e JOIN outreach_messages m ON m.id=e.message_id
        WHERE lower(m.recipient)=lower(?) AND e.kind IN ('bounced','unsubscribed','suppressed')
        LIMIT 1""", (email,),
    )).fetchone()
    legacy = await (await db.execute(
        """SELECT 1 FROM contacts WHERE lower(email)=lower(?) AND
        (email_verification_status IN ('dead','invalid','recipient_rejected','permanent_failure_observed')
        OR pipeline_status IN ('suppressed','unsubscribed')) LIMIT 1""", (email,),
    )).fetchone()
    if failure or legacy:
        return "permanent failure"
    return None


async def require_recipient_allowed(db, email: str):
    from fastapi import HTTPException
    reason = await recipient_blocked_reason(db, email)
    if reason:
        raise HTTPException(409, "Recipient is suppressed or has a permanent delivery failure.")
