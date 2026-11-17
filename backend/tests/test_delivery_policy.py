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
    finally:
        await db.close()


def test_recipient_suppression_blocks_outreach() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    test_recipient_suppression_blocks_outreach()
    print("delivery policy: ok")
