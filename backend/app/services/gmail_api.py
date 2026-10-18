"""
Gmail API - Send emails using OAuth tokens from Google sign-in.
No App Password required; uses the logged-in user's Google account.
"""
import os
import base64
import secrets
from email.utils import make_msgid, formataddr
from html import escape
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from typing import Optional, Any
import httpx
from app.services.email_body import SignOff
from app.services.mail_address import validate_recipient, validate_header
from app.services.delivery_policy import require_delivery_enabled

GOOGLE_CLIENT_ID = (os.getenv("GOOGLE_CLIENT_ID") or "").strip()
GOOGLE_CLIENT_SECRET = (os.getenv("GOOGLE_CLIENT_SECRET") or "").strip()


class DeliveryNotAttemptedError(ValueError):
    """The provider rejected the request before Gmail could accept the message."""


async def get_valid_access_token(user_id: int) -> tuple[str, str] | None:
    """
    Get a valid access token for the user. Refreshes if expired.
    Returns (access_token, user_email) or None if no tokens.
    """
    from app.database import get_db
    import time

    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT email, access_token, refresh_token, token_expires_at FROM users WHERE id = ? AND is_active = 1",
            (user_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        from app.token_crypto import decrypt_token, encrypt_token

        email = row["email"]
        stored_refresh_token = row["refresh_token"]
        access_token = decrypt_token(row["access_token"])
        refresh_token = decrypt_token(stored_refresh_token)
        expires_at = row["token_expires_at"]

        # If we have a valid access token (with 5 min buffer), use it
        now = time.time()
        try:
            still_valid = expires_at is None or float(expires_at) > now + 300
        except (TypeError, ValueError):
            still_valid = False
        if access_token and still_valid:
            return access_token, email

        # Refresh if we have refresh_token
        if not refresh_token or not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
            return None

        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if r.status_code != 200:
                return None
            data = r.json()
            new_access = data.get("access_token")
            new_expires = data.get("expires_in", 3600)
            if not new_access:
                return None

        expires_at = now + new_expires
        await db.execute(
            """UPDATE users SET access_token = ?, token_expires_at = ?
               WHERE id = ? AND is_active = 1 AND refresh_token = ?""",
            (encrypt_token(new_access), expires_at, user_id, stored_refresh_token),
        )
        changed = await (await db.execute("SELECT changes() AS n")).fetchone()
        await db.commit()
        # A disconnect, deactivation, or newer authorization may have completed
        # while Google was refreshing the old credential. Never resurrect it.
        if not changed or int(changed["n"] or 0) != 1:
            return None
        return new_access, email
    finally:
        await db.close()



def _attach_files(msg: MIMEMultipart, attachments: list[tuple[bytes, str, str]]) -> None:
    """Attach files to MIME message. Each tuple is (content, filename, mime_type)."""
    import email.encoders
    for content, filename, mime_type in attachments:
        main_type, sub_type = (mime_type.split("/") + ["octet-stream", "stream"])[:2]
        part = MIMEBase(main_type, sub_type)
        part.set_payload(content)
        email.encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment", filename=filename)
        msg.attach(part)

def _build_message(
    subject: str,
    from_value: str,
    to_email: str,
    plain: str,
    html_body: str,
    attachments: list[tuple[bytes, str, str]] | None = None,
    message_id: str | None = None,
) -> MIMEMultipart:
    """Build a valid MIME tree: alternatives nested inside multipart/mixed."""
    alternatives = MIMEMultipart("alternative")
    alternatives.attach(MIMEText(plain, "plain", "utf-8"))
    alternatives.attach(MIMEText(html_body, "html", "utf-8"))
    msg = MIMEMultipart("mixed") if attachments else alternatives
    msg["Subject"] = subject
    msg["From"] = from_value
    msg["To"] = to_email
    if message_id:
        msg["Message-ID"] = message_id
    if attachments:
        msg.attach(alternatives)
        _attach_files(msg, attachments)
    return msg


async def send_via_gmail_api(
    user_id: int,
    to_email: str,
    subject: str,
    body: str,
    from_name: Optional[str] = None,
    attachments: Optional[list[tuple[bytes, str, str]]] = None,
) -> bool:
    """
    Send email via Gmail API using the user's OAuth tokens.
    Returns True on success, raises on failure.
    """
    require_delivery_enabled()
    validate_recipient(to_email)
    validate_header(subject)
    validate_header(from_name or "")
    result = await get_valid_access_token(user_id)
    if not result:
        raise DeliveryNotAttemptedError(
            "No Gmail access. Connect your own Gmail account in Profile, under Integrations."
        )
    access_token, from_email = result

    full_body = body

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr((from_name or "YUCG Outreach", from_email))
    msg["To"] = to_email
    msg.attach(MIMEText(full_body, "plain", "utf-8"))
    if attachments:
        _attach_files(msg, attachments)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii").rstrip("=")

    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            json={"raw": raw},
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
        )
        if r.status_code == 401:
            raise ValueError(
                "Gmail access expired. Sign out and sign in again to re-authorize."
            )
        if r.status_code >= 400:
            err = r.text
            raise RuntimeError(f"Gmail API error: {r.status_code} - {err}")

    return True




def _compose(
    body: str,
    sign_off: SignOff | None = None,
    attachment_names: Optional[list[str]] = None,
) -> tuple[str, str]:
    """One renderer for every send path.

    The three paths previously disagreed with each other and with the editor:
    one attached the editor's HTML as the plain-text part, another escaped it
    so recipients saw literal tags. All of them now produce a real text
    alternative and real HTML.
    """
    from app.services.email_body import render_email

    return render_email(body, sign_off=sign_off, attachments=attachment_names)


async def send_via_gmail_api_multipart(
    user_id: int,
    to_email: str,
    subject: str,
    body: str,
    from_name: Optional[str] = None,
    attachments: Optional[list[tuple[bytes, str, str]]] = None,
    sign_off: SignOff | None = None,
) -> bool:
    """Send the composed message plus its sign-off. No tracking pixel."""
    require_delivery_enabled()
    validate_recipient(to_email)
    validate_header(subject)
    validate_header(from_name or "")
    result = await get_valid_access_token(user_id)
    if not result:
        raise DeliveryNotAttemptedError(
            "No Gmail access. Connect your own Gmail account in Profile, under Integrations."
        )
    access_token, from_email = result

    full_body, html_body = _compose(
        body, sign_off, [name for _, name, _ in (attachments or [])],
    )

    msg = _build_message(
        subject,
        formataddr((from_name or "YUCG Outreach", from_email)),
        to_email,
        full_body,
        html_body,
        attachments,
    )

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii").rstrip("=")
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            json={"raw": raw},
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
        )
        if r.status_code == 401:
            raise DeliveryNotAttemptedError(
                "Gmail access expired. Sign out and sign in again to re-authorize."
            )
        if 400 <= r.status_code < 500:
            raise DeliveryNotAttemptedError(f"Gmail rejected the send request ({r.status_code}).")
        if r.status_code >= 500:
            raise RuntimeError(f"Gmail send result is uncertain ({r.status_code}); reconcile before retrying.")
    return True


async def send_via_gmail_api_with_tracking(
    user_id: int,
    to_email: str,
    subject: str,
    body: str,
    campaign_contact_id: int,
    from_name: Optional[str] = None,
    dispatch_key: Optional[str] = None,
    sign_off: SignOff | None = None,
    attachments: Optional[list[tuple[bytes, str, str]]] = None,
) -> dict[str, Any]:
    """Send HTML email with open-tracking pixel for campaigns.

    Returns {"ok": True, "message_id": str|None, "thread_id": str|None} on success.
    Raises on transport / Gmail errors.
    """
    from app.routers.track import get_tracking_pixel_url

    require_delivery_enabled()
    validate_recipient(to_email)
    validate_header(subject)
    validate_header(from_name or "")
    result = await get_valid_access_token(user_id)
    if not result:
        raise DeliveryNotAttemptedError(
            "No Gmail access. Connect your own Gmail account in Profile, under Integrations."
        )
    access_token, from_email = result

    full_body, composed_html = _compose(
        body, sign_off, [name for _, name, _ in (attachments or [])],
    )
    token = secrets.token_urlsafe(32)
    tracking_url = get_tracking_pixel_url(token)
    rfc_message_id = make_msgid(domain=from_email.rsplit('@', 1)[-1])
    from app.database import get_db
    db = await get_db()
    try:
        if dispatch_key is not None:
            from app.services.dispatch_service import begin_write
            await begin_write(db)
            intent = await (await db.execute('SELECT * FROM outreach_dispatches WHERE dispatch_key=?',(dispatch_key,))).fetchone()
            if (not intent or intent['state']!='claimed' or intent['sender_user_id']!=user_id
                    or intent['campaign_contact_id']!=campaign_contact_id or intent['recipient']!=to_email
                    or intent['subject']!=subject or intent['body']!=body):
                raise ValueError('Send does not match its claimed immutable dispatch')
        cursor = await db.execute(
            """INSERT INTO outreach_messages
               (campaign_contact_id, sender_id, recipient, tracking_token, rfc_message_id, dispatch_key)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (campaign_contact_id, user_id, to_email, token, rfc_message_id, dispatch_key),
        )
        tracking_message_id = cursor.lastrowid
        await db.commit()
    finally:
        await db.close()
    pixel = f'<img src="{tracking_url}" width="1" height="1" alt="" style="display:none" />'
    html_body = composed_html.replace("</body></html>", pixel + "</body></html>")

    msg = _build_message(
        subject,
        formataddr((from_name or "YUCG Outreach", from_email)),
        to_email,
        full_body,
        html_body,
        attachments,
        rfc_message_id,
    )

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii").rstrip("=")

    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            json={"raw": raw},
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
        )
        if r.status_code == 401:
            raise DeliveryNotAttemptedError(
                "Gmail access expired. Sign out and sign in again to re-authorize."
            )
        if 400 <= r.status_code < 500:
            raise DeliveryNotAttemptedError(f"Gmail rejected the send request ({r.status_code}).")
        if r.status_code >= 500:
            raise RuntimeError(f"Gmail send result is uncertain ({r.status_code}); reconcile before retrying.")
        data = r.json()
        db = await get_db()
        try:
            await db.execute(
                """UPDATE outreach_messages SET gmail_message_id = ?, gmail_thread_id = ?,
                   sent_at = CURRENT_TIMESTAMP WHERE id = ?""",
                (data.get("id"), data.get("threadId"), tracking_message_id),
            )
            await db.commit()
        finally:
            await db.close()
        return {
            "ok": True,
            "message_id": data.get("id"),
            "thread_id": data.get("threadId"),
        }
