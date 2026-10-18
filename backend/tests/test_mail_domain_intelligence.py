"""In-house mailbox checks + website vs mail-host intelligence (Itaú → itaubba).
From backend/: python3 tests/test_mail_domain_intelligence.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.name}"
os.environ["JWT_SECRET"] = "p-mail-domain-secret-not-a-known-default-xx"
os.environ["ROSTER_SEC_PAUSE_SEC"] = "0"
os.environ["ROSTER_WEB_ON_ENROLL"] = "0"
os.environ.pop("TAVILY_API_KEY", None)
os.environ.pop("COMPANIES_HOUSE_API_KEY", None)
os.environ.pop("VERIFALIA_API_KEY", None)
os.environ["EXTERNAL_EMAIL_VERIFICATION_ENABLED"] = "false"
os.environ["INBOX_VERIFY_PROVIDER"] = "smtp"

from app.database import get_db, init_db  # noqa: E402
from app.services import email_verifier as verifier  # noqa: E402
from app.services import roster_email as RE  # noqa: E402
from app.services import roster_watch as R  # noqa: E402
from app.services.company_email_cache import (  # noqa: E402
    build_email_for_person_sync,
    get_domain_patterns,
    load_reconcile_context,
    record_verified_sample,
)
from app.services.contact_intelligence import mailbox_from_legacy  # noqa: E402
from app.services.contact_scraper import person_name_key  # noqa: E402
from app.services.mail_domain_map import list_mail_hosts, preferred_mail_hosts_sync, record_mailbox_outcome  # noqa: E402


def person(name: str) -> dict:
    return {
        "full_name": name,
        "normalized_name": person_name_key(name),
        "title": "Director",
        "role_type": "director",
        "source": "sec_form4",
        "source_url": "https://www.sec.gov/example",
        "accession": "0001",
        "employment": "current",
    }


def test_seed_hosts_need_no_db() -> None:
    hosts = preferred_mail_hosts_sync("itau.com.br")
    assert hosts[0] == "itaubba.com.br"
    assert "itau.com.br" in hosts
    assert preferred_mail_hosts_sync("garmin.com") == ["garmin.com"]


async def _core() -> None:
    await init_db()
    hosts = await list_mail_hosts("itau.com.br")
    assert hosts[0] == "itaubba.com.br", hosts
    assert "itau.com.br" in hosts

    ctx = await load_reconcile_context({"itau.com.br"})
    mail = build_email_for_person_sync("Pedro Moreira Salles", "itau.com.br", ctx)
    assert mail == "pedro.salles@itaubba.com.br", mail

    await record_verified_sample(
        "itau.com.br",
        "ada.silva@itaubba.com.br",
        "Ada Silva",
        source="domain_scrape",
        company_name="Itaú Unibanco",
    )
    patterns = await get_domain_patterns("itau.com.br")
    assert any(p["company_domain"] == "itaubba.com.br" and p["pattern_key"] == "first.last" for p in patterns), patterns

    db = await get_db()
    try:
        alias = await (
            await db.execute(
                "SELECT 1 FROM company_mail_domains WHERE website_domain='itau.com.br' AND mail_domain='itaubba.com.br'"
            )
        ).fetchone()
        assert alias
    finally:
        await db.close()

    await record_mailbox_outcome("nobody@itau.com.br", kind="bounce", website_domain="itau.com.br")
    ranked = await list_mail_hosts("itau.com.br")
    assert ranked[0] == "itaubba.com.br"

    roster = await R._ensure_roster("Itau Unibanco", "itau.com.br")
    await R._upsert_people(
        int(roster["id"]),
        [person("Pedro Moreira Salles")],
        domain="itau.com.br",
        mark_missing=False,
    )

    async def mx(domain: str, cache=None):
        if domain == "itaubba.com.br":
            return (True, ["mx.itaubba"])
        if domain == "itau.com.br":
            return (False, [])
        return (True, ["mx"])

    with patch.object(RE, "get_mx_cached", mx):
        touched = await RE._ensure_emails(dict(roster))
    assert touched >= 1, touched
    detail = await R.roster_detail(int(roster["id"]))
    row = next(p for p in detail["people"] if p["full_name"] == "Pedro Moreira Salles")
    assert row["inferred_email"] == "pedro.salles@itaubba.com.br", row
    assert row["email_status"] == "mx_valid", row

    # A person already stamped on the website host is rebuilt onto the
    # exception mailbox host on the next maintenance pass.
    db = await get_db()
    try:
        await db.execute(
            """UPDATE company_roster_people SET inferred_email=?, email_status='mx_valid'
               WHERE roster_id=? AND full_name=?""",
            ("pedro.salles@itau.com.br", int(roster["id"]), "Pedro Moreira Salles"),
        )
        await db.commit()
    finally:
        await db.close()

    async def both_mx(domain: str, cache=None):
        return (True, ["mx"])

    with patch.object(RE, "get_mx_cached", both_mx):
        await RE._ensure_emails(dict(roster))
    detail = await R.roster_detail(int(roster["id"]))
    row = next(p for p in detail["people"] if p["full_name"] == "Pedro Moreira Salles")
    assert row["inferred_email"] == "pedro.salles@itaubba.com.br", row

    # Catch-all: fake local accepted → do not claim the person exists.
    async def both_accepted(email, mx_host, timeout):
        return "accepted"

    with patch.object(verifier, "_smtp_rcpt_probe", new=AsyncMock(side_effect=both_accepted)):
        result = await verifier.verify_email_deliverability(
            "ada.silva@itaubba.com.br",
            smtp_probe=True,
            force_smtp=True,
            mx_cache={"itaubba.com.br": (True, ["mx.itaubba"])},
        )
    assert result["catch_all"] is True
    assert result["smtp_probe"] == "catch_all"
    assert mailbox_from_legacy(result) == "accept_all_or_risky"

    async def selective(email, mx_host, timeout):
        local = str(email).split("@", 1)[0]
        if local.startswith("nombox.") or local.startswith("ghost."):
            return "invalid"
        return "accepted"

    with patch.object(verifier, "_smtp_rcpt_probe", new=AsyncMock(side_effect=selective)):
        ok = await verifier.verify_email_deliverability(
            "ada.silva@itaubba.com.br",
            smtp_probe=True,
            force_smtp=True,
            mx_cache={"itaubba.com.br": (True, ["mx.itaubba"])},
        )
        dead = await verifier.verify_email_deliverability(
            "ghost.nobody@itaubba.com.br",
            smtp_probe=True,
            force_smtp=True,
            mx_cache={"itaubba.com.br": (True, ["mx.itaubba"])},
        )
    assert ok["catch_all"] is False and ok["smtp_probe"] == "accepted"
    assert mailbox_from_legacy(ok) == "provider_medium_confidence"
    assert dead["status"] == "invalid"
    assert mailbox_from_legacy(dead) == "recipient_rejected"

    # Default MX mode still skips SMTP even when smtp_probe=True.
    with patch.object(verifier, "INBOX_VERIFY_MODE", "mx"), patch.object(
        verifier, "_smtp_rcpt_probe", new=AsyncMock(return_value="invalid")
    ) as smtp:
        mx_only = await verifier.verify_email_deliverability(
            "ada.silva@itaubba.com.br",
            mx_cache={"itaubba.com.br": (True, ["mx.itaubba"])},
        )
        smtp.assert_not_called()
    assert mx_only["smtp_probe"] is None

    from app.services.email_verification import assess_address

    async def blocked(*args, **kwargs):
        return {
            "status": "likely_valid",
            "mx_valid": True,
            "smtp_probe": "unknown",
            "catch_all": None,
            "reason": "mail_route_found_mailbox_unconfirmed",
        }

    with patch("app.services.email_verification.verify_email_deliverability", blocked):
        assessed = await assess_address("ada.silva@itaubba.com.br", actor_id=0, external=True)
    assert assessed["provider_state"] == "unavailable"
    assert assessed["mailbox"] == "inconclusive"


def test_mail_domain_intelligence() -> None:
    test_seed_hosts_need_no_db()
    asyncio.run(_core())


if __name__ == "__main__":
    test_mail_domain_intelligence()
    print("ok")
