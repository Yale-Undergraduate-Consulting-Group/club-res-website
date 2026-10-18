"""Verifalia adapter reads documented job snapshots and polls 202 Accepted.
From backend/: python3 tests/test_email_verification.py"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

os.environ.setdefault("JWT_SECRET", "p-verifalia-adapter-secret-not-a-known-default")
os.environ["EXTERNAL_EMAIL_VERIFICATION_ENABLED"] = "false"
os.environ["VERIFALIA_API_KEY"] = "test-key-not-a-live-credential"

from app.services import email_verification as EV  # noqa: E402


JOB_ID = "18f5a933-af67-421c-b63a-1cbee297fa19"


def snapshot(classification: str, status: str, job_status: str = "Completed") -> dict:
    return {
        "overview": {
            "id": JOB_ID,
            "status": job_status,
            "quality": "Standard",
            "noOfEntries": 1,
        },
        "entries": {
            "data": [
                {
                    "index": 0,
                    "inputData": "ada@vb.com",
                    "emailAddress": "ada@vb.com",
                    "status": status,
                    "classification": classification,
                }
            ]
        },
    }


class FakeResponse:
    def __init__(self, status_code: int, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, responses: list[FakeResponse], calls: list):
        self._responses = list(responses)
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs.get("json")))
        return self._responses.pop(0)

    async def get(self, url, **kwargs):
        self.calls.append(("GET", url, None))
        return self._responses.pop(0)


def fake_client_class(responses: list[FakeResponse], calls: list):
    def factory(*args, **kwargs):
        return FakeClient(responses, calls)

    return factory


async def run_adapter(responses: list[FakeResponse]) -> tuple[dict, list]:
    calls: list = []
    with (
        patch("httpx.AsyncClient", fake_client_class(responses, calls)),
        patch.object(EV.asyncio, "sleep", new_callable=AsyncMock),
    ):
        result = await EV._verifalia("ada@vb.com")
    return result, calls


async def _run() -> None:
    # Documented completed snapshot: entries.data[0].classification, not a flat entries dict.
    result, calls = await run_adapter([FakeResponse(200, snapshot("Deliverable", "Success"))])
    assert result["mailbox"] == "provider_high_confidence", result
    assert result["provider_state"] == "complete", result
    assert result["provider_request_id"] == JOB_ID, result
    assert "Deliverable" in result["reason"] and "Success" in result["reason"], result
    assert calls[0][0] == "POST" and "waitTime=" in calls[0][1], calls[0]
    assert calls[0][2] == {"entries": [{"inputData": "ada@vb.com"}]}, calls[0]

    rejected, _ = await run_adapter([FakeResponse(200, snapshot("Undeliverable", "MailboxDoesNotExist"))])
    assert rejected["mailbox"] == "recipient_rejected", rejected
    assert rejected["provider_state"] == "complete", rejected

    risky, _ = await run_adapter([FakeResponse(200, snapshot("Risky", "ServerIsCatchAll"))])
    assert risky["mailbox"] == "accept_all_or_risky", risky

    # GET /entries page shape also used in the docs.
    entries_page = {
        "data": [
            {
                "index": 0,
                "inputData": "ada@vb.com",
                "status": "Success",
                "classification": "Deliverable",
            }
        ]
    }
    paged, _ = await run_adapter([FakeResponse(200, entries_page)])
    assert paged["mailbox"] == "provider_high_confidence", paged

    # 202 Accepted: poll https Location, then read the completed snapshot.
    accepted, calls = await run_adapter(
        [
            FakeResponse(
                202,
                {"overview": {"id": JOB_ID, "status": "InProgress"}},
                {"Location": f"https://api.verifalia.com/v2.7/email-validations/{JOB_ID}", "Retry-After": "0"},
            ),
            FakeResponse(200, snapshot("Deliverable", "Success")),
        ]
    )
    assert accepted["mailbox"] == "provider_high_confidence", accepted
    assert calls[1][0] == "GET", calls
    assert JOB_ID in calls[1][1] and "waitTime=" in calls[1][1], calls[1]

    # Off-host Location must be ignored; fall back to the job id on api.verifalia.com.
    safe, calls = await run_adapter(
        [
            FakeResponse(
                202,
                {"overview": {"id": JOB_ID, "status": "InProgress"}},
                {"Location": "https://evil.example/steal", "Retry-After": "0"},
            ),
            FakeResponse(200, snapshot("Undeliverable", "DomainDoesNotExist")),
        ]
    )
    assert safe["mailbox"] == "recipient_rejected", safe
    assert calls[1][0] == "GET" and "api.verifalia.com" in calls[1][1], calls[1]
    assert "evil.example" not in calls[1][1], calls[1]

    timed_out, _ = await run_adapter(
        [
            FakeResponse(202, {"overview": {"id": JOB_ID, "status": "InProgress"}}),
            FakeResponse(202, {"overview": {"id": JOB_ID, "status": "InProgress"}}),
            FakeResponse(202, {"overview": {"id": JOB_ID, "status": "InProgress"}}),
            FakeResponse(202, {"overview": {"id": JOB_ID, "status": "InProgress"}}),
            FakeResponse(202, {"overview": {"id": JOB_ID, "status": "InProgress"}}),
        ]
    )
    assert timed_out["mailbox"] == "inconclusive", timed_out
    assert timed_out["provider_state"] == "unavailable", timed_out
    assert timed_out["provider_request_id"] == JOB_ID, timed_out

    refused, _ = await run_adapter([FakeResponse(402, {})])
    assert refused["provider_state"] == "exhausted", refused


def test_verifalia_adapter() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    test_verifalia_adapter()
    print("ok")
