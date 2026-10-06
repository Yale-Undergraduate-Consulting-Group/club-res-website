"""Shared mailbox assessment. External provider calls stay out of caller transactions."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse

from fastapi import HTTPException

from app.database import get_db
from app.services.contact_intelligence import (
    CONSUMER_DOMAINS,
    DISPOSABLE_DOMAINS,
    Mailbox,
    ROLE_LOCALS,
    mailbox_from_legacy,
    now_iso,
)
from app.services.contact_intelligence_schema import init_contact_intelligence_schema
from app.services.email_verifier import verify_email_deliverability, verify_email_format

logger = logging.getLogger(__name__)

DAILY_CAP = 25
CACHE_DAYS = max(1, min(int(os.getenv("VERIFALIA_RESPONSE_CACHE_DAYS", "14") or 14), 90))

VERIFALIA_ENDPOINT = "https://api.verifalia.com/v2.7/email-validations"
VERIFALIA_WAIT_MS = 15000
_CLASSIFICATION = {
    "deliverable": Mailbox.PROVIDER_HIGH_CONFIDENCE,
    "undeliverable": Mailbox.RECIPIENT_REJECTED,
    "risky": Mailbox.ACCEPT_ALL_OR_RISKY,
    "unknown": Mailbox.INCONCLUSIVE,
}
_STATUS = {
    "success": Mailbox.PROVIDER_HIGH_CONFIDENCE,
    "serveriscatchall": Mailbox.ACCEPT_ALL_OR_RISKY,
    "mailboxdoesnotexist": Mailbox.RECIPIENT_REJECTED,
    "domaindoesnotexist": Mailbox.RECIPIENT_REJECTED,
    "domainismisconfigured": Mailbox.RECIPIENT_REJECTED,
    "domainhasnullmx": Mailbox.RECIPIENT_REJECTED,
}


def _day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _hash(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()


def _result(mailbox: str, method: str, reason: str, *, cost=0, expires_at=None, provider_state="not_requested", provider_request_id=None) -> dict:
    return {
        "mailbox": mailbox,
        "method": method,
        "reason": reason[:500],
        "checked_at": now_iso(),
        "expires_at": expires_at,
        "cost_units": cost,
        "source_ids": [],
        "provider_state": provider_state,
        "provider_request_id": provider_request_id,
    }


async def _ensure(db) -> None:
    await init_contact_intelligence_schema(db)


async def _admin(actor_id: int) -> None:
    db = await get_db()
    try:
        row = await (await db.execute("SELECT role FROM users WHERE id=?", (actor_id,))).fetchone()
        if not row or row["role"] != "admin":
            raise HTTPException(403, "Administrator access required")
    finally:
        await db.close()


def _enabled() -> bool:
    return os.getenv("EXTERNAL_EMAIL_VERIFICATION_ENABLED", "false").lower() == "true" and bool(
        (os.getenv("VERIFALIA_API_KEY") or "").strip()
    )


async def provider_capacity(*, actor_id: int) -> dict:
    await _admin(actor_id)
    db = await get_db()
    try:
        await _ensure(db)
        control = await (await db.execute("SELECT disabled FROM verification_provider_control WHERE provider='verifalia'")).fetchone()
        day = await (await db.execute("SELECT used, allowance FROM verification_provider_days WHERE day=?", (_day(),))).fetchone()
        allowance = int((day["allowance"] if day else min(DAILY_CAP, int(os.getenv("VERIFALIA_DAILY_CREDIT_CAP", str(DAILY_CAP)) or DAILY_CAP))))
        used = int(day["used"] if day else 0)
        return {
            "provider": "verifalia",
            "enabled": _enabled() and not (control and control["disabled"]),
            "day": _day(),
            "used": used,
            "remaining": max(0, allowance - used),
            "allowance": allowance,
        }
    finally:
        await db.close()


async def set_provider_enabled(*, actor_id: int, enabled: bool) -> dict:
    await _admin(actor_id)
    db = await get_db()
    try:
        await _ensure(db)
        await db.execute(
            """INSERT INTO verification_provider_control(provider,disabled,changed_by,changed_at)
               VALUES('verifalia',?,?,?)
               ON CONFLICT(provider) DO UPDATE SET disabled=excluded.disabled,changed_by=excluded.changed_by,changed_at=excluded.changed_at""",
            (0 if enabled else 1, actor_id, now_iso()),
        )
        await db.commit()
    finally:
        await db.close()
    return await provider_capacity(actor_id=actor_id)


async def assess_address(email: str, *, actor_id: int, external: bool = False, manual: bool = False, reason: str | None = None) -> dict:
    raw = (email or "").strip().lower()
    fmt = verify_email_format(raw)
    if not fmt.get("valid"):
        return _result(Mailbox.BAD_SYNTAX.value, "local", "Address syntax is not usable")
    local, _, domain = raw.partition("@")
    if local in ROLE_LOCALS:
        return _result(Mailbox.BAD_SYNTAX.value, "local", "Role or generic mailbox, not an individual address")
    if domain in CONSUMER_DOMAINS | DISPOSABLE_DOMAINS:
        return _result(Mailbox.BAD_SYNTAX.value, "local", "Consumer or disposable domain is not used for outreach")

    db = await get_db()
    try:
        await _ensure(db)
        suppressed = await (await db.execute(
            "SELECT state FROM candidate_suppressions WHERE email=? COLLATE NOCASE", (raw,)
        )).fetchone()
        if suppressed:
            return _result(Mailbox.PERMANENT_FAILURE_OBSERVED.value, "history", f"Address is {suppressed['state']}", expires_at=None)
        historic = await (await db.execute(
            """SELECT c.result, c.reason, c.checked_at, c.expires_at, c.verifier FROM email_checks c
               JOIN email_candidates e ON e.id=c.candidate_id
               WHERE e.email=? COLLATE NOCASE AND c.result IN ('previously_delivered','human_reply_observed','permanent_failure_observed')
               ORDER BY CASE c.result WHEN 'permanent_failure_observed' THEN 0 WHEN 'human_reply_observed' THEN 1 ELSE 2 END, c.checked_at DESC LIMIT 1""",
            (raw,),
        )).fetchone()
        if historic:
            return _result(historic["result"], historic["verifier"], historic["reason"], expires_at=historic["expires_at"])
        cached = await (await db.execute(
            "SELECT result_json FROM verification_cache WHERE address_hash=? AND actor_id=? AND expires_at>?",
            (_hash(raw), actor_id, now_iso()),
        )).fetchone()
        if cached:
            payload = json.loads(cached["result_json"])
            payload["provider_state"] = "cached"
            return payload
    finally:
        await db.close()

    local_check = await verify_email_deliverability(raw, smtp_probe=False)
    mailbox = mailbox_from_legacy(local_check)
    result = _result(mailbox, "dns_mx", local_check.get("reason") or "Mail-route lookup", expires_at=(datetime.now(timezone.utc) + timedelta(days=7)).isoformat())
    if mailbox in (Mailbox.BAD_SYNTAX.value, Mailbox.DOMAIN_HAS_NO_MAIL_ROUTE.value):
        return result
    if not external:
        return result

    if not reason and manual:
        raise HTTPException(422, "A reason is required to spend a validation credit")
    provider = (os.getenv("INBOX_VERIFY_PROVIDER") or "smtp").strip().lower()
    if provider == "verifalia":
        if not _enabled():
            result["provider_state"] = "disabled"
            result["reason"] = "External mailbox validation is not configured. Mail-domain availability is recorded."
            return result
        return await _external(raw, actor_id=actor_id, manual=manual, fallback=result)
    return await _inhouse_smtp(raw, actor_id=actor_id, fallback=result)


async def _inhouse_smtp(email: str, *, actor_id: int, fallback: dict) -> dict:
    """Catch-all + RCPT. Port 25 blocked returns unavailable, never invalid."""
    check = await verify_email_deliverability(email, smtp_probe=True, force_smtp=True)
    mailbox = mailbox_from_legacy(check)
    probe = check.get("smtp_probe")
    domain = email.rsplit("@", 1)[-1]
    if probe in (None, "unknown") and check.get("catch_all") is not True:
        return {
            **fallback,
            "mailbox": Mailbox.INCONCLUSIVE.value,
            "method": "smtp_rcpt",
            "provider_state": "unavailable",
            "reason": "Mail server did not answer a recipient probe",
        }
    from app.services.mail_domain_map import mark_catch_all, store_mx_result

    if check.get("mx_valid") is not None:
        await store_mx_result(domain, bool(check.get("mx_valid")))
    if check.get("catch_all"):
        await mark_catch_all(domain)
    result = _result(
        mailbox,
        "smtp_rcpt",
        check.get("reason") or "Recipient probe",
        provider_state="complete",
        expires_at=(datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
    )
    if mailbox == Mailbox.RECIPIENT_REJECTED.value:
        db = await get_db()
        try:
            from app.services.roster_email import apply_provider_verdict

            await apply_provider_verdict(db, email, "rejected")
            await db.commit()
        finally:
            await db.close()
    return result


async def _external(email: str, *, actor_id: int, manual: bool, fallback: dict) -> dict:
    reservation_id = None
    db = await get_db()
    try:
        await _ensure(db)
        await db.execute("BEGIN IMMEDIATE")
        control = await (await db.execute("SELECT disabled FROM verification_provider_control WHERE provider='verifalia'")).fetchone()
        if control and control["disabled"]:
            await db.rollback()
            return {**fallback, "provider_state": "disabled", "reason": "External validation is turned off by an administrator"}
        cap = min(DAILY_CAP, max(0, int(os.getenv("VERIFALIA_DAILY_CREDIT_CAP", str(DAILY_CAP)) or DAILY_CAP)))
        await db.execute(
            "INSERT INTO verification_provider_days(day,used,allowance) VALUES(?,0,?) ON CONFLICT(day) DO NOTHING",
            (_day(), cap),
        )
        day = await (await db.execute("SELECT used, allowance FROM verification_provider_days WHERE day=?", (_day(),))).fetchone()
        if int(day["used"]) >= int(day["allowance"]):
            await db.rollback()
            return {**fallback, "provider_state": "exhausted", "reason": "Daily free validation credits are used. Local mail-domain results are saved."}
        reservation_id = hashlib.sha256(f"{_day()}:{actor_id}:{_hash(email)}:{int(manual)}".encode()).hexdigest()
        try:
            await db.execute(
                """INSERT INTO verification_reservations(id,day,actor_id,address_hash,person_id,manual,reason,status,created_at)
                   VALUES(?,?,?,?,NULL,?,?,'reserved',?)""",
                (reservation_id, _day(), actor_id, _hash(email), 1 if manual else 0, None, now_iso()),
            )
        except Exception:
            await db.rollback()
            cached = await (await db.execute(
                "SELECT result_json FROM verification_cache WHERE address_hash=? AND actor_id=? AND expires_at>?",
                (_hash(email), actor_id, now_iso()),
            )).fetchone()
            if cached:
                payload = json.loads(cached["result_json"])
                payload["provider_state"] = "cached"
                return payload
            return {**fallback, "provider_state": "cached"}
        await db.execute("UPDATE verification_provider_days SET used=used+1 WHERE day=?", (_day(),))
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()

    try:
        provider = await _verifalia(email)
    except Exception:
        logger.info("external mailbox check inconclusive")
        provider = {**fallback, "mailbox": Mailbox.INCONCLUSIVE.value, "provider_state": "unavailable",
                    "reason": "External validation did not complete. Local mail-domain results are saved.",
                    "method": "verifalia"}
    db = await get_db()
    try:
        state = str(provider.get("provider_state") or "")
        await db.execute(
            "UPDATE verification_reservations SET status=? WHERE id=?",
            ("complete" if state not in ("unavailable", "exhausted") else "inconclusive", reservation_id),
        )
        if state not in ("unavailable", "exhausted"):
            expires = (datetime.now(timezone.utc) + timedelta(days=CACHE_DAYS)).isoformat()
            provider["expires_at"] = expires
            await db.execute(
                """INSERT INTO verification_cache(address_hash,actor_id,result_json,expires_at) VALUES(?,?,?,?)
                   ON CONFLICT(address_hash,actor_id) DO UPDATE SET result_json=excluded.result_json,expires_at=excluded.expires_at""",
                (_hash(email), actor_id, json.dumps(provider), expires),
            )
        if provider.get("mailbox") == Mailbox.RECIPIENT_REJECTED.value:
            from app.services.roster_email import apply_provider_verdict

            await apply_provider_verdict(db, email, "rejected")
        await db.commit()
    finally:
        await db.close()
    return provider


def _verifalia_payload(response) -> dict:
    try:
        data = response.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _verifalia_job_id(data: dict) -> str | None:
    overview = data.get("overview")
    if isinstance(overview, dict):
        job_id = str(overview.get("id") or "").strip()
        if job_id:
            return job_id
    job_id = str(data.get("id") or "").strip()
    return job_id or None


def _verifalia_in_progress(status_code: int, data: dict) -> bool:
    if status_code == 202:
        return True
    overview = data.get("overview") if isinstance(data.get("overview"), dict) else data
    return str((overview or {}).get("status") or "") == "InProgress"


def _verifalia_entry(data: dict) -> dict | None:
    def first(box):
        if isinstance(box, list) and box and isinstance(box[0], dict):
            return box[0]
        if isinstance(box, dict):
            inner = box.get("data")
            if isinstance(inner, list) and inner and isinstance(inner[0], dict):
                return inner[0]
            if box.get("classification") or box.get("status"):
                return box
        return None

    return first(data.get("entries")) or first(data.get("data"))


def _verifalia_poll_url(location: str | None, job_id: str | None) -> str | None:
    loc = (location or "").strip()
    if loc:
        if loc.startswith("/"):
            loc = urljoin("https://api.verifalia.com/", loc.lstrip("/"))
        parsed = urlparse(loc)
        host = (parsed.hostname or "").lower()
        if parsed.scheme == "https" and host == "api.verifalia.com" and "/email-validations" in (parsed.path or ""):
            return loc.split("#", 1)[0]
    job = (job_id or "").strip()
    if job and 8 <= len(job) <= 80 and all(c.isalnum() or c in "-_" for c in job):
        return f"{VERIFALIA_ENDPOINT}/{job}"
    return None


def _with_wait(url: str) -> str:
    if "waitTime=" in url:
        return url
    return f"{url}{'&' if '?' in url else '?'}waitTime={VERIFALIA_WAIT_MS}"


def _retry_delay(response) -> float:
    raw = str(response.headers.get("Retry-After") or "").strip()
    if raw:
        try:
            return min(8.0, max(0.0, float(raw)))
        except ValueError:
            pass
    return 0.5


def _from_verifalia_entry(entry: dict, job_id: str | None) -> dict:
    classification = str(entry.get("classification") or "").strip()
    status = str(entry.get("status") or "").strip()
    mailbox = _CLASSIFICATION.get(classification.lower()) or _STATUS.get(status.lower()) or Mailbox.INCONCLUSIVE
    if classification and status and status.lower() != classification.lower():
        reason = f"Provider classification: {classification} ({status})"
    else:
        reason = f"Provider classification: {classification or status or 'unknown'}"
    return _result(
        mailbox.value,
        "verifalia",
        reason,
        cost=1,
        provider_state="complete",
        provider_request_id=(job_id or "")[:80] or None,
    )


async def _verifalia(email: str) -> dict:
    import httpx

    key = (os.getenv("VERIFALIA_API_KEY") or "").strip()
    unavailable = _result(
        Mailbox.INCONCLUSIVE.value,
        "verifalia",
        "External validation returned an error",
        provider_state="unavailable",
    )
    async with httpx.AsyncClient(timeout=25.0, trust_env=False) as client:
        response = await client.post(
            _with_wait(VERIFALIA_ENDPOINT),
            auth=(key, ""),
            json={"entries": [{"inputData": email}]},
        )
        if response.status_code == 402:
            return _result(
                Mailbox.INCONCLUSIVE.value,
                "verifalia",
                "Free validation credits were refused by the provider",
                provider_state="exhausted",
            )
        if response.status_code >= 400:
            return unavailable
        data = _verifalia_payload(response)
        job_id = _verifalia_job_id(data)
        polls = 0
        while _verifalia_in_progress(response.status_code, data) and polls < 4:
            poll_url = _verifalia_poll_url(response.headers.get("Location"), job_id)
            if not poll_url:
                break
            await asyncio.sleep(_retry_delay(response))
            response = await client.get(_with_wait(poll_url), auth=(key, ""))
            polls += 1
            if response.status_code >= 400:
                return unavailable
            data = _verifalia_payload(response)
            job_id = job_id or _verifalia_job_id(data)
        if _verifalia_in_progress(response.status_code, data):
            return _result(
                Mailbox.INCONCLUSIVE.value,
                "verifalia",
                "External validation did not finish in time. Local mail-domain results are saved.",
                provider_state="unavailable",
                provider_request_id=(job_id or "")[:80] or None,
            )
        entry = _verifalia_entry(data)
        if not entry:
            return _result(
                Mailbox.INCONCLUSIVE.value,
                "verifalia",
                "External validation returned no mailbox result",
                provider_state="unavailable",
                provider_request_id=(job_id or "")[:80] or None,
            )
        return _from_verifalia_entry(entry, job_id)
