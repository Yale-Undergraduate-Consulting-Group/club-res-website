"""Admin → Costs: local paid-call counters and once-daily Cost Explorer cache.

All data is synthetic. No provider or AWS call leaves this process.
From backend/: python tests/test_costs.py
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JWT_SECRET", "costs-test-secret-0123456789abcdef")
os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.mkdtemp()) / 't.db'}"
os.environ["APP_ENV"] = "beta"
os.environ["AI_MONTHLY_BUDGET_USD"] = "15"

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from app.database import get_db, init_db  # noqa: E402
from app.jwt_utils import create_token  # noqa: E402
from app.services.costs import record_paid_call, refresh_cost_explorer  # noqa: E402

ADMIN, MEMBER = 1, 2


async def run(sql: str, args: tuple = ()) -> list[tuple]:
    db = await get_db()
    try:
        rows = [tuple(row) for row in await (await db.execute(sql, args)).fetchall()]
        await db.commit()
        return rows
    finally:
        await db.close()


async def seed() -> None:
    await init_db()
    await run("INSERT INTO users (id,email,name,role,is_active) VALUES (1,'admin@yale.edu','Admin','admin',1),(2,'member@yale.edu','Member','standard',1)")
    await run("""INSERT INTO usage_events (user_id,event_type,resource_type,details_json,created_at) VALUES
        (2,'bedrock_reserved','llm',?,datetime('now')),
        (2,'bedrock_reserved','llm',?,datetime('now','-1 month')),
        (2,'tinyfish_reserved','search',NULL,datetime('now')),
        (2,'tinyfish_reserved','fetch',NULL,datetime('now')),
        (2,'firecrawl_reserved','search',NULL,datetime('now'))""",
        (json.dumps({"cost_usd": 1.25}), json.dumps({"estimated_cost_usd": .4})))
    await run("INSERT INTO verification_provider_days (day,used,allowance) VALUES (date('now'),7,25)")
    await record_paid_call("tavily", MEMBER, search_depth="basic")
    await record_paid_call("serpapi", None, engine="google_maps")


def client(uid: int, role: str) -> TestClient:
    result = TestClient(main.app, raise_server_exceptions=False)
    result.cookies.set("yucg_session", create_token(uid, f"{uid}@yale.edu", None, None, role))
    return result


def tests() -> None:
    asyncio.run(seed())
    admin = client(ADMIN, "admin")
    assert client(MEMBER, "standard").get("/api/admin/costs").status_code == 403
    body = admin.get("/api/admin/costs").json()
    assert body["aws"]["connected"] is False and "not connected" in body["aws"]["message"]
    providers = {row["id"]: row for row in body["providers"]}
    assert (providers["llm"]["calls"], providers["llm"]["estimated_usd"]) == (1, 1.25), providers["llm"]
    assert (providers["tinyfish"]["calls"], providers["tinyfish"]["estimated_usd"]) == (2, .01)
    assert (providers["firecrawl"]["calls"], providers["firecrawl"]["estimated_usd"]) == (1, .01)
    assert providers["tavily"]["calls"] == 1, providers["tavily"]
    assert providers["verifalia"]["calls"] == 7
    assert providers["serpapi"]["calls"] == 1
    assert len(providers["llm"]["trend"]) == 3 and providers["llm"]["trend"][-2]["usd"] == .4

    response = {
        "ResultsByTime": [{"Groups": [
            {"Keys": ["Amazon EC2 - Compute", "Site$club-res-website-dev"], "Metrics": {"UnblendedCost": {"Amount": "6.25"}}},
            {"Keys": ["Amazon EC2 - Compute", "Site$club-res-website-prod"], "Metrics": {"UnblendedCost": {"Amount": "7.50"}}},
            {"Keys": ["AWS Cost Explorer", "Site$"], "Metrics": {"UnblendedCost": {"Amount": "0.01"}}},
        ]}],
    }
    ce = MagicMock()
    ce.get_cost_and_usage.return_value = response
    with patch("boto3.client", return_value=ce):
        asyncio.run(refresh_cost_explorer())
        asyncio.run(refresh_cost_explorer())
    assert ce.get_cost_and_usage.call_count == 1, "the job must never charge more than once per UTC day"
    kwargs = ce.get_cost_and_usage.call_args.kwargs
    assert kwargs["GroupBy"] == [{"Type": "DIMENSION", "Key": "SERVICE"}, {"Type": "TAG", "Key": "Site"}]

    body = admin.get("/api/admin/costs").json()
    assert body["aws"]["connected"] is True and body["aws"]["total_usd"] == 13.76, body["aws"]
    assert {row["name"] for row in body["aws"]["by_environment"]} == {"club-res-website-dev", "club-res-website-prod", "untagged"}
    assert asyncio.run(run("SELECT app,version FROM schema_migrations WHERE app='core' AND version=4")) == [("core", 4)]


if __name__ == "__main__":
    tests()
    print("costs: ok")
