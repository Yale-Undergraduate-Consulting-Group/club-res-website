"""Adding one found person must bring exactly that person on file.

The picker imports a subset of a run's prospects and moves rows by the
outcome the server reports, so the contract here is per-prospect: ids
outside the run are skipped and named, an empty choice is refused, and a
caller that sends no body still imports everyone eligible."""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["JWT_SECRET"] = "run-import-subset-test-secret-xxxxxxxx"

from fastapi import HTTPException
from app.database import get_db, init_db
from app.routers.yucgoutreach import YucgOutreachImportBody, import_run_to_contacts


async def fake_assess(email: str, **_: object) -> dict:
    return {"mailbox": "inconclusive", "method": "local", "reason": "test"}


async def rejected(coro, status: int) -> None:
    try:
        await coro
        raise AssertionError("Expected request to be rejected")
    except HTTPException as exc:
        assert exc.status_code == status, exc


async def run() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["DATABASE_URL"] = "sqlite:///" + tmp + "/subset.db"
        await init_db()
        db = await get_db()
        await db.execute(
            "INSERT INTO users(id,email,role,is_active) VALUES "
            "(1,'one@yale.edu','standard',1),(2,'two@yale.edu','standard',1)"
        )
        await db.execute(
            "INSERT INTO yucgoutreach_discovery_runs(id,user_id,company_name,company_domain,status) VALUES "
            "(1,1,'A24','a24films.com','completed'),(2,1,'NEON','neonrated.com','completed')"
        )
        await db.execute(
            "INSERT INTO yucgoutreach_prospects(id,run_id,first_name,last_name,email,title,score) VALUES "
            "(10,1,'Jean','Bartik','jean.bartik@a24films.com','Director of Operations',90),"
            "(11,1,'Klara','Dan','klara.dan@a24films.com','VP Partnerships',80),"
            "(12,1,'Grace','Hopper',NULL,'Head of Insight',70),"
            "(20,2,'Margaret','Hamilton','margaret.hamilton@neonrated.com','Head of Partnerships',85)"
        )
        # Klara is already another member's contact: importing her is a skip
        # that names the owner, never a silent overwrite.
        await db.execute(
            "INSERT INTO contacts(id,name,email,owner_id) VALUES (5,'Klara Dan','klara.dan@a24films.com',2)"
        )
        await db.commit()
        await db.close()
        user = {"id": 1, "role": "standard"}

        with patch("app.services.yucgoutreach_import.assess_address", side_effect=fake_assess):
            # Subset: one id from this run, one from another run, one with no
            # address, one that does not exist.
            partial = await import_run_to_contacts(
                1, YucgOutreachImportBody(prospect_ids=[10, 20, 12, 999]), user,
            )
            by_id = {r["prospect_id"]: r for r in partial["results"]}
            assert partial["created"] == 1 and partial["updated"] == 0, partial
            assert by_id[10]["outcome"] == "created" and by_id[10]["contact_id"], by_id
            assert by_id[20] == {"prospect_id": 20, "contact_id": None, "outcome": "skipped", "reason": "not in this run"}
            assert by_id[999]["reason"] == "not in this run"
            assert by_id[12]["outcome"] == "skipped" and by_id[12]["reason"] == "no usable address", by_id
            assert 11 not in by_id, "an id that was not asked for must not be touched"
            assert partial["skipped"] == 3, partial

            db = await get_db()
            rows = await (await db.execute("SELECT email FROM contacts WHERE owner_id = 1")).fetchall()
            await db.close()
            assert [r["email"] for r in rows] == ["jean.bartik@a24films.com"], rows
            # Margaret stayed off file: a foreign id is reported, not imported.
            db = await get_db()
            gone = await (await db.execute(
                "SELECT COUNT(*) AS n FROM contacts WHERE email = 'margaret.hamilton@neonrated.com'"
            )).fetchone()
            await db.close()
            assert gone["n"] == 0

            await rejected(import_run_to_contacts(1, YucgOutreachImportBody(prospect_ids=[]), user), 422)
            await rejected(import_run_to_contacts(2, YucgOutreachImportBody(prospect_ids=[20]), {"id": 2, "role": "standard"}), 404)

            # No body: everyone eligible, as before. Jean is now an update,
            # Klara is held by member two, Grace has no address so is never
            # in the eligible set and gets no result row.
            everyone = await import_run_to_contacts(1, None, user)
            outcomes = {r["prospect_id"]: r["outcome"] for r in everyone["results"]}
            assert outcomes == {10: "updated", 11: "skipped"}, outcomes
            klara = next(r for r in everyone["results"] if r["prospect_id"] == 11)
            assert "already worked by" in klara["reason"], klara
            assert everyone["updated"] == 1 and everyone["skipped"] == 1, everyone

            # A caller passing the other run's own id gets its person.
            neon = await import_run_to_contacts(2, YucgOutreachImportBody(prospect_ids=[20]), user)
            assert neon["created"] == 1 and neon["results"][0]["outcome"] == "created", neon


def test_run_import_subset() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    test_run_import_subset()
    print("run import subset: ok")
