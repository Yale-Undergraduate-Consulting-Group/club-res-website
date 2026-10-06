"""Loading the bulk registers from another machine.

The sources are too big to be comfortable on the box that serves the site: a
year of IRS 990 filings is ~1.5 GB across sixteen archives, half of them
needing scratch disk to decompress. This lets a second machine do the fetching
and parsing and post the rows, while the SQLite file here stays the only thing
members read.
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JWT_SECRET", "register-loader-test-secret-xxxxxxxx")

from fastapi import HTTPException

from app.database import get_db, init_db
from app.routers.yucgoutreach import RegisterLoad, load_register


class _Request:
    def __init__(self, token: str | None) -> None:
        self.headers = {"authorization": f"Bearer {token}"} if token else {}


def tests() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["DATABASE_URL"] = "sqlite:///" + tmp + "/loader.db"

        async def scenario() -> None:
            await init_db()

            company = {
                "source": "dol_5500", "source_key": "953261426", "tier": "us_employer",
                "country": "US", "company_name": "The Home Depot, Inc.", "employees": 419778,
                "employees_source": "form_5500_active_participants", "region": "GA",
            }

            # Off by default. With no token configured the route denies that it
            # exists, so turning the feature on is a deliberate act rather than
            # a new door that opens itself.
            os.environ.pop("REGISTER_LOADER_TOKEN", None)
            try:
                await load_register(RegisterLoad(source="dol_5500", batch="2025"), _Request("anything"))
                raise AssertionError("the loader route answered while disabled")
            except HTTPException as exc:
                assert exc.status_code == 404, exc.status_code

            os.environ["REGISTER_LOADER_TOKEN"] = "s3cret-loader-token"

            # Surrounding whitespace is stripped, as any HTTP layer would:
            # the rejected cases are a missing, empty or wrong token.
            for offered in (None, "", "wrong-token", "s3cret-loader-token-x"):
                try:
                    await load_register(
                        RegisterLoad(source="dol_5500", batch="2025", companies=[company]),
                        _Request(offered),
                    )
                    raise AssertionError(f"token {offered!r} was accepted")
                except HTTPException as exc:
                    assert exc.status_code == 401, (offered, exc.status_code)

            good = _Request("s3cret-loader-token")
            result = await load_register(
                RegisterLoad(source="dol_5500", batch="2025", companies=[company]), good)
            assert result == {"ok": True, "written": 1, "people": 0}, result

            # A chunk that is not the last one must not record the batch as
            # done: a run that dies halfway has to repeat, not be remembered
            # as complete.
            db = await get_db()
            recorded = await (await db.execute("SELECT COUNT(*) n FROM company_register_ingests")).fetchone()
            await db.close()
            assert recorded["n"] == 0, "an unfinished batch was recorded as done"

            # The final chunk carries the people and closes the batch.
            closing = await load_register(
                RegisterLoad(source="dol_5500", batch="2025", done=True,
                             people={"953261426": [{"full_name": "Jane Roe",
                                                    "relationship": "Signed the plan filing"}]}),
                good)
            assert closing["people"] == 1, closing

            db = await get_db()
            row = await (await db.execute(
                "SELECT company_name, employees, officer_count FROM company_register")).fetchone()
            person = await (await db.execute(
                "SELECT full_name, relationship FROM company_register_people")).fetchone()
            batch = await (await db.execute(
                "SELECT source, batch_key, status FROM company_register_ingests")).fetchone()
            await db.close()
            assert row["company_name"] == "The Home Depot, Inc." and row["employees"] == 419778
            assert row["officer_count"] == 1
            assert person["full_name"] == "Jane Roe"
            assert (batch["source"], batch["batch_key"], batch["status"]) == ("dol_5500", "2025", "ok")

            # Re-sending the same batch is safe: rows upsert on
            # (source, source_key), so a repeated cron run does not duplicate.
            await load_register(
                RegisterLoad(source="dol_5500", batch="2025", companies=[company]), good)
            db = await get_db()
            count = await (await db.execute("SELECT COUNT(*) n FROM company_register")).fetchone()
            await db.close()
            assert count["n"] == 1, count["n"]

            # A batch with no source is refused rather than recorded namelessly.
            try:
                await load_register(RegisterLoad(source="", batch="2025"), good)
                raise AssertionError("a sourceless batch was accepted")
            except HTTPException as exc:
                assert exc.status_code == 422

        asyncio.run(scenario())


def target_list_from_register_tests() -> None:
    """A target list is how the rest of the app receives companies, and until
    now only the legacy spreadsheet could produce one - so none of the 200k+
    companies in the register could be worked on."""
    from fastapi import HTTPException

    from app.routers.releases import ReleaseCreate, create_release
    from app.services.company_register import upsert_companies

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["DATABASE_URL"] = "sqlite:///" + tmp + "/release.db"

        async def scenario() -> None:
            await init_db()
            await upsert_companies([
                {"source": "irs_990", "source_key": "135562308", "tier": "us_nonprofit",
                 "country": "US", "company_name": "Cheekwood Botanical Garden",
                 "company_domain": "cheekwood.org", "sector_label": "Arts, Culture and Humanities"},
                {"source": "dol_5500", "source_key": "953261426", "tier": "us_employer",
                 "country": "US", "company_name": "The Home Depot, Inc.",
                 "sector_label": "Retail Trade"},
            ])
            db = await get_db()
            ids = [int(r["id"]) for r in await (await db.execute(
                "SELECT id FROM company_register ORDER BY id")).fetchall()]
            await db.close()

            out = await create_release(
                ReleaseCreate(name="Register picks", register_ids=ids), {"id": 1, "role": "standard"})
            assert out["targets"] == 2, out

            db = await get_db()
            targets = [dict(r) for r in await (await db.execute(
                "SELECT row_index, company, company_domain, contact_type, find_status "
                "FROM outreach_release_targets ORDER BY id")).fetchall()]
            await db.close()

            # The register already knows the domain. Re-deriving it from a
            # source URL these rows do not have would throw it away.
            assert targets[0]["company_domain"] == "cheekwood.org", targets
            # These have no spreadsheet line, and inventing one would collide
            # with a real row index.
            assert all(t["row_index"] is None for t in targets), targets
            assert all(t["find_status"] == "pending" for t in targets)
            assert {t["contact_type"] for t in targets} == {"us_nonprofit", "us_employer"}

            # Selecting nothing makes no list.
            try:
                await create_release(ReleaseCreate(name="empty"), {"id": 1, "role": "standard"})
                raise AssertionError("an empty target list was created")
            except HTTPException as exc:
                assert exc.status_code == 400

        asyncio.run(scenario())


def company_claim_tests() -> None:
    """Two members of one society working the same company, each unaware, is
    the failure a client notices. Before this there was no record of who was
    on what - a contact owned by someone else was dropped from an import with
    no name attached."""
    from app.services.company_claims import (
        claim_company, claims_for, company_key, release_company,
    )

    # One company, several spellings, one key. The register holds all three.
    assert (company_key("The Walt Disney Company", "disney.com")
            == company_key("Walt Disney Co", "www.disney.com")
            == company_key("Disney (Studios)", "https://disney.com/about"))
    # Without a domain, the legal form is not identity.
    assert company_key("The Walt Disney Company, Inc.") == company_key("Walt Disney")
    assert company_key("") == "n:"

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["DATABASE_URL"] = "sqlite:///" + tmp + "/claims.db"

        async def scenario() -> None:
            await init_db()
            db = await get_db()
            await db.execute(
                """INSERT INTO users(id,email,name,role,is_active)
                   VALUES (1,'andre@yale.edu','Andre','admin',1),
                          (2,'aaron@yale.edu','Aaron','standard',1)""")
            await db.commit()
            await db.close()

            first = await claim_company(1, "Cheekwood Botanical Garden", "cheekwood.org")
            assert first["held_by_other"] is False

            # The second member is told who, not refused: they may have a
            # reason, and the point is that they find out now.
            second = await claim_company(2, "Cheekwood Botanical Garden", "cheekwood.org")
            assert second["held_by_other"] is True and second["member"] == "Andre"

            # The holder returning to their own company is not a collision.
            assert (await claim_company(1, "Cheekwood", "cheekwood.org"))["held_by_other"] is False

            found = await claims_for([
                {"company_name": "Cheekwood Botanical Garden", "company_domain": "cheekwood.org"},
                {"company_name": "Nobody Ltd"},
            ])
            assert list(found) == ["d:cheekwood.org"], found

            # Only the holder may hand it back.
            assert await release_company(2, "Cheekwood", "cheekwood.org") is False
            assert await release_company(1, "Cheekwood", "cheekwood.org") is True
            assert (await claim_company(2, "Cheekwood", "cheekwood.org"))["held_by_other"] is False

        asyncio.run(scenario())


if __name__ == "__main__":
    tests()
    target_list_from_register_tests()
    company_claim_tests()
    print("register loader: ok")
