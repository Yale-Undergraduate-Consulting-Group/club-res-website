"""A teammate's recent finished search is shared with every member: it shows in
the runs list for the company, names who found it and when, and its people are
readable - but not a failed, empty, stale or deactivated member's run, and not
another member's run that is still working.

From backend/: python tests/test_shared_runs.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["JWT_SECRET"] = "shared-runs-test-secret-xxxxxxxxxxxxxx"

from fastapi import HTTPException

from app.database import get_db, init_db
from app.routers.yucgoutreach import delete_run, get_run, list_prospects, list_runs

ME = {"id": 1, "role": "standard"}
ANA = {"id": 2, "role": "standard"}


async def execute(sql: str, args: tuple = ()) -> int:
    db = await get_db()
    try:
        cur = await db.execute(sql, args)
        await db.commit()
        return int(cur.lastrowid)
    finally:
        await db.close()


async def run_by(user_id: int, company: str, *, status: str = "completed", people: int = 1,
                 age_days: float = 1, origin: str = "member") -> int:
    run_id = await execute(
        """INSERT INTO yucgoutreach_discovery_runs
           (user_id, company_name, status, prospects_count, origin, created_at, completed_at)
           VALUES (?, ?, ?, ?, ?, datetime('now', ?), datetime('now', ?))""",
        (user_id, company, status, people, origin, f"-{age_days} days", f"-{age_days} days"),
    )
    for n in range(people):
        await execute(
            "INSERT INTO yucgoutreach_prospects(run_id, first_name, last_name, email, score) VALUES (?, 'P', ?, ?, 50)",
            (run_id, f"N{n}", f"p{run_id}-{n}@co.com"),
        )
    return run_id


async def shared_ids(user: dict, company: str) -> list[int]:
    """Runs of someone else that the list hands back for this company."""
    own = {r["id"] for r in await list_runs(user, limit=50, companies=[])}
    return [r["id"] for r in await list_runs(user, limit=50, companies=[company]) if r["id"] not in own]


async def denied(coro, status: int = 404) -> None:
    try:
        await coro
    except HTTPException as exc:
        assert exc.status_code == status, exc.status_code
        return
    raise AssertionError("expected the request to be refused")


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["DATABASE_URL"] = "sqlite:///" + tmp + "/shared.db"
        await init_db()
        await execute(
            "INSERT INTO users(id, email, name, role, is_active) VALUES "
            "(1, 'me@yale.edu', 'Me', 'standard', 1), (2, 'ana@yale.edu', 'Ana Lee', 'standard', 1), "
            "(3, 'gone@yale.edu', 'Gone', 'standard', 0), (4, 'bo@yale.edu', 'Bo Park', 'standard', 1)"
        )

        # Ana's finished search of Acme two days ago: shared, with who and when.
        acme = await run_by(2, "Acme Corp", people=3, age_days=2)
        listed = await list_runs(ME, limit=50, companies=["acme corp"])
        shared = [r for r in listed if r["id"] == acme]
        assert shared and shared[0]["shared_from"]["name"] == "Ana Lee", listed
        assert shared[0]["shared_from"]["completed_at"], shared
        assert (await get_run(acme, ME))["status"] == "completed"
        assert len(await list_prospects(acme, ME)) == 3
        # Only for a company the page has open.
        assert acme not in await shared_ids(ME, "globex")
        assert acme not in {r["id"] for r in await list_runs(ME, limit=50, companies=[])}
        # Read-only: no one deletes a teammate's run.
        await denied(delete_run(acme, ME))
        assert (await get_run(acme, ANA))["id"] == acme

        # Newest first, one per company: Bo's newer run replaces Ana's.
        bo = await run_by(4, "Acme Corp", people=2, age_days=1)
        assert await shared_ids(ME, "acme corp") == [bo]

        # The caller's own run is never listed twice.
        mine = await run_by(1, "Initech", people=1)
        assert [r["id"] for r in await list_runs(ME, limit=50, companies=["initech"])].count(mine) == 1
        assert not any("shared_from" in r for r in await list_runs(ME, limit=50, companies=["initech"]))

        # Not shared: older than 30 days, failed, no people, still running,
        # and a member who is no longer active.
        stale = await run_by(2, "Hooli", age_days=31)
        failed = await run_by(2, "Hooli", status="failed", people=2)
        empty = await run_by(2, "Hooli", people=0)
        running = await run_by(2, "Hooli", status="running", people=2)
        gone = await run_by(3, "Hooli", people=2)
        assert await shared_ids(ME, "hooli") == []
        for run_id in (stale, failed, empty, running, gone):
            await denied(get_run(run_id, ME))
            await denied(list_prospects(run_id, ME))
        # The owner still reads her own.
        assert len(await list_prospects(stale, ANA)) == 1
        # Within the window it is shared, just inside the 30 days.
        recent = await run_by(2, "Hooli", age_days=29)
        assert await shared_ids(ME, "hooli") == [recent]

        # The catalog builder's run is shared as before, without attribution.
        builder = await run_by(4, "Pied Piper", people=2, origin="builder")
        piper = [r for r in await list_runs(ME, limit=50, companies=["pied piper"]) if r["id"] == builder]
        assert piper and "shared_from" not in piper[0]
    print("shared runs: ok")


if __name__ == "__main__":
    asyncio.run(main())
