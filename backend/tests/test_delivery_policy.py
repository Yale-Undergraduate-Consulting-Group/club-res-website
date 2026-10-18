"""Suppression and environment guards stay independent of mailbox confidence."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

from fastapi import HTTPException

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.name}"
os.environ["JWT_SECRET"] = "p-delivery-policy-secret-not-a-known-default-xx"

from app.database import get_db, init_db  # noqa: E402
from app.services.delivery_policy import (  # noqa: E402
    recipient_blocked_reason,
    require_recipient_allowed,
)


async def _run() -> None:
    await init_db()
    db = await get_db()
    try:
        assert await recipient_blocked_reason(db, "") is None
        await db.execute(
            "INSERT INTO candidate_suppressions(email,state,observed_at) VALUES ('blocked@acme.com','permanent_failure','2026-01-01T00:00:00+00:00')"
        )
        await db.execute(
            "INSERT INTO contacts (name, email, pipeline_status) VALUES ('Pat','legacy@acme.com','unsubscribed')"
        )
        await db.commit()
        assert await recipient_blocked_reason(db, "blocked@acme.com") == "suppressed"
        assert await recipient_blocked_reason(db, "legacy@acme.com") == "permanent failure"
        assert await recipient_blocked_reason(db, "ok@acme.com") is None
        try:
            await require_recipient_allowed(db, "blocked@acme.com")
            raise AssertionError("suppressed address was allowed")
        except HTTPException as exc:
            assert exc.status_code == 409

        from app.services.generation_policy import draft_evidence
        from app.services.contact_intelligence import ingest_contact, now_iso
        await db.execute("INSERT INTO users (id, email) VALUES (1, 'a@yale.edu')")
        cur = await db.execute(
            "INSERT INTO contacts (name, email, title, company, company_domain, owner_id) VALUES ('Ada Lovelace','ada.lovelace@acme.com','CTO','Acme','acme.com',1)"
        )
        contact_id = cur.lastrowid
        snapshot = await ingest_contact(
            db,
            contact={
                "id": contact_id,
                "name": "Ada Lovelace",
                "email": "ada.lovelace@acme.com",
                "title": "CTO",
                "company": "Acme",
                "domain": "acme.com",
                "disposition": "accepted",
                "sources": [{
                    "url": "https://news.example/ada",
                    "excerpt": "Ada Lovelace (ada.lovelace@acme.com) is CTO at Acme",
                    "observed_at": now_iso(),
                    "scope": "public",
                }],
            },
            actor_id=1,
            origin="imported_without_evidence",
        )
        await db.commit()
        drafted = await draft_evidence(db, {"id": contact_id, "email": "ada.lovelace@acme.com"}, 1)
        assert drafted["context_origin"] == "accepted_evidence"
        assert drafted["source_ids"]
        empty = await draft_evidence(db, {"id": 999999, "email": "ok@acme.com"}, 1)
        assert empty == {"sources": [], "context_origin": "catalog_unreviewed"}
        assert snapshot["contact_id"] == contact_id
    finally:
        await db.close()


def test_recipient_suppression_blocks_outreach() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    test_recipient_suppression_blocks_outreach()
    print("delivery policy: ok")
