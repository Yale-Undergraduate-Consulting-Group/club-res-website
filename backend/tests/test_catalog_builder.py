"""The catalog builder: target order, recrawl skip, budget pauses, member-first
drain, club-wide visibility of what it finds, and its non-login account.

From backend/: python tests/test_catalog_builder.py
"""
import asyncio
import hashlib
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["JWT_SECRET"] = "catalog-builder-test-secret-xxxxxxxxxx"

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.auth_deps import _resolve_user
from app.database import get_db, init_db
from app.jwt_utils import create_token
from app.routers.admin import update_user_status, UserStatusUpdate
from app.routers.yucgoutreach import delete_run, get_run, list_prospects, list_runs
from app.services.catalog_builder import (
    SYSTEM_EMAIL,
    builder_status,
    builder_tick,
    set_builder_enabled,
)
from app.services.yucgoutreach_discovery import drain_queued_yucgoutreach_runs

MEMBER_ONE = {"id": 1, "role": "standard"}
MEMBER_TWO = {"id": 2, "role": "standard"}


def usage(hour: int = 0, day: int = 0):
    async def fake(seconds: float = 3600) -> int:
        return hour if seconds <= 3600 else day
    return AsyncMock(side_effect=fake)


async def resolved(name: str, *, user_id: int | None):
    return {"name": name, "domain": None, "entity": None}


async def execute(sql: str, args: tuple = ()) -> None:
    db = await get_db()
    try:
        await db.execute(sql, args)
        await db.commit()
    finally:
        await db.close()


async def fetchall(sql: str, args: tuple = ()) -> list[dict]:
    db = await get_db()
    try:
        return [dict(r) for r in await (await db.execute(sql, args)).fetchall()]
    finally:
        await db.close()


async def tick(hour: int = 0, day: int = 0) -> dict:
    with patch("app.services.web_fetch.search_usage_last_hour", usage(hour, day)), \
            patch("app.services.company_resolve.resolve_company", AsyncMock(side_effect=resolved)):
        return await builder_tick()


async def drain() -> int | None:
    with patch("app.services.yucgoutreach_discovery._yucgoutreach_run_guard", AsyncMock()):
        return (await drain_queued_yucgoutreach_runs()).get("run_id")


async def queue_member_run(user_id: int, company: str) -> int:
    db = await get_db()
    try:
        cur = await db.execute(
            "INSERT INTO yucgoutreach_discovery_runs(user_id, company_name, status) VALUES (?, ?, 'queued')",
            (user_id, company),
        )
        await db.commit()
        return int(cur.lastrowid)
    finally:
        await db.close()


async def run() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["DATABASE_URL"] = "sqlite:///" + tmp + "/builder.db"
        os.environ["BEDROCK_CALLS_PER_CLUB_PER_HOUR"] = "200"
        await init_db()
        await execute(
            "INSERT INTO users(id, email, role, is_active) VALUES "
            "(1, 'one@yale.edu', 'admin', 1), (2, 'two@yale.edu', 'standard', 1)"
        )
        await execute(
            """INSERT INTO company_register(source, source_key, tier, country, company_name, prominence_rank, metadata_json)
               VALUES ('companies_house', '00445790', 'uk', 'GB', 'Tesco Plc', NULL, '{"account_category": "GROUP"}'),
                      ('sec_public', '0000002', 'us_public', 'US', 'Microsoft Corp', 1, '{}'),
                      ('sec_public', '0000003', 'us_public', 'US', 'Nvidia Corp', 2, '{}'),
                      ('sec_public', '0000001', 'us_public', 'US', 'Apple Inc.', 0, '{}')"""
        )
        # Apple was searched by a member 40 days ago (outside the 30-day
        # window); Microsoft two days ago (inside it).
        await execute(
            """INSERT INTO yucgoutreach_discovery_runs(user_id, company_name, status, created_at)
               VALUES (1, 'apple inc.', 'completed', datetime('now', '-40 days')),
                      (1, ' Microsoft Corp', 'completed', datetime('now', '-2 days'))"""
        )

        # Off by default: nothing is queued.
        assert (await tick())["action"] == "disabled"
        await set_builder_enabled(True)

        # Over the builder's share of searches (hour, then day) or of the
        # club's Bedrock hour: paused, nothing queued.
        paused = await tick(hour=250)
        assert paused["action"] == "paused" and "last hour" in paused["reason"], paused
        paused = await tick(day=6000)
        assert paused["action"] == "paused" and "24 hours" in paused["reason"], paused
        db = await get_db()
        await db.executemany(
            "INSERT INTO usage_events(event_type, resource_type, details_json) VALUES ('bedrock_reserved', 'inference', '{}')",
            [()] * 101,
        )
        await db.commit()
        await db.close()
        paused = await tick()
        assert paused["action"] == "paused" and "AI budget" in paused["reason"], paused
        await execute("DELETE FROM usage_events")
        assert not await fetchall("SELECT id FROM yucgoutreach_discovery_runs WHERE origin = 'builder'")

        # Enabled and within budget: the top-ranked listed company nobody
        # crawled in the window, as a builder run under the system account.
        first = await tick()
        assert first["action"] == "enqueued" and first["company"] == "Apple Inc.", first
        system = (await fetchall("SELECT id, role, is_active FROM users WHERE email = ?", (SYSTEM_EMAIL,)))[0]
        assert system["role"] == "system" and system["is_active"] == 1
        builder_run = (await fetchall("SELECT * FROM yucgoutreach_discovery_runs WHERE id = ?", (first["run_id"],)))[0]
        assert builder_run["origin"] == "builder" and builder_run["user_id"] == system["id"]
        assert builder_run["status"] == "queued" and builder_run["max_prospects"] == 60
        # At most one builder run open at a time.
        assert (await tick())["action"] == "busy"

        # A member run queued after the builder's is still claimed first, and
        # so is the next one; the builder run only when no member waits.
        member_two = await queue_member_run(2, "Acme")
        assert await drain() == member_two
        member_one = await queue_member_run(1, "Globex")
        assert await drain() == member_one
        assert await drain() == first["run_id"]

        # The builder run finishes with two people.
        await execute(
            """UPDATE yucgoutreach_discovery_runs SET status = 'completed', prospects_count = 2,
               completed_at = CURRENT_TIMESTAMP, lease_token = NULL WHERE id = ?""",
            (first["run_id"],),
        )
        await execute(
            """INSERT INTO yucgoutreach_prospects(run_id, first_name, last_name, email, company, score)
               VALUES (?, 'Tim', 'Cook', 'tcook@apple.com', 'Apple Inc.', 90),
                      (?, 'Jeff', 'Williams', 'jwilliams@apple.com', 'Apple Inc.', 80)""",
            (first["run_id"], first["run_id"]),
        )

        # Any member sees it for that company, read-only.
        seen = await list_runs(MEMBER_TWO, limit=40, companies=["apple inc."])
        shared = [r for r in seen if r["id"] == first["run_id"]]
        assert shared and shared[0]["origin"] == "builder", seen
        assert first["run_id"] not in {r["id"] for r in await list_runs(MEMBER_TWO, limit=40, companies=["nvidia corp"])}
        assert first["run_id"] not in {r["id"] for r in await list_runs(MEMBER_TWO, limit=40, companies=[])}
        assert (await get_run(first["run_id"], MEMBER_TWO))["origin"] == "builder"
        people = await list_prospects(first["run_id"], MEMBER_TWO)
        assert {p["last_name"] for p in people} == {"Cook", "Williams"}
        try:
            await delete_run(first["run_id"], MEMBER_TWO)
            raise AssertionError("a member deleted the builder's run")
        except HTTPException as exc:
            assert exc.status_code == 404

        # Next pick skips Microsoft (a member crawled it two days ago).
        second = await tick()
        assert second["action"] == "enqueued" and second["company"] == "Nvidia Corp", second
        progress = {r["company_name"]: r for r in await fetchall("SELECT * FROM catalog_builder_companies")}
        assert progress["Apple Inc."]["status"] == "completed" and progress["Apple Inc."]["people_found"] == 2
        with patch("app.services.web_fetch.search_usage_last_hour", usage(hour=12)):
            status = await builder_status()
        assert status["enabled"] and status["current_company"] == "Nvidia Corp", status
        assert status["companies_done_today"] == 1 and status["people_found_today"] == 2
        assert status["searches_last_hour"] == 12

        # The system account cannot be signed into or managed.
        assert not SYSTEM_EMAIL.endswith("@yale.edu")
        token = create_token(system["id"], SYSTEM_EMAIL, "Catalog builder", None, "system")
        bearer = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        assert await _resolve_user(bearer, None) is None
        member_token = create_token(2, "two@yale.edu", None, None, "standard")
        assert (await _resolve_user(HTTPAuthorizationCredentials(scheme="Bearer", credentials=member_token), None))["id"] == 2
        await execute(
            "INSERT INTO api_keys(user_id, key_hash, key_prefix) VALUES (?, ?, 'sys')",
            (system["id"], hashlib.sha256(b"system-key").hexdigest()),
        )
        assert await _resolve_user(None, "system-key") is None
        try:
            await update_user_status(system["id"], UserStatusUpdate(is_active=False), {"id": 1, "role": "admin"})
            raise AssertionError("an admin deactivated the builder's account")
        except HTTPException as exc:
            assert exc.status_code == 404

        # Turned off, it queues nothing more.
        await set_builder_enabled(False)
        await execute("UPDATE yucgoutreach_discovery_runs SET status = 'completed' WHERE origin = 'builder'")
        assert (await tick())["action"] == "disabled"
    print("catalog builder: ok")


if __name__ == "__main__":
    asyncio.run(run())
