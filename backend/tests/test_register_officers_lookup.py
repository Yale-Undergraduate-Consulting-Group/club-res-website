"""A search reads its company's officers from the company register itself.

There is no separate "Look up officers" action: a Find people run reads the
officers stored for the company's register entry and, when an entry that
publishes them (SEC listed, Companies House) has none stored, fetches them
first. From backend/: python tests/test_register_officers_lookup.py
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JWT_SECRET", "officers-test-secret-0123456789abcdef")
os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.mkdtemp()) / 't.db'}"
os.environ.pop("TAVILY_API_KEY", None)

from app.database import get_db, init_db  # noqa: E402
from app.services import roster_email as RE  # noqa: E402
from app.services import roster_watch as R  # noqa: E402
from app.services import yucgoutreach_discovery as D  # noqa: E402
from app.services.register_names import derive  # noqa: E402


async def seed() -> None:
    await init_db()
    db = await get_db()
    try:
        await db.execute("INSERT INTO users (id, email, name, role, is_active) VALUES (2, 'ana@yale.edu', 'Ana', 'standard', 1)")

        async def company(name: str, source: str, key: str, domain: str | None, people: list[tuple[str, str]]) -> int:
            d = derive(name, "US")
            cur = await db.execute(
                """INSERT INTO company_register (source, source_key, tier, country, company_name, company_domain,
                                                 display_name, entity_key, officer_count)
                   VALUES (?, ?, 'us_private', 'US', ?, ?, ?, ?, ?)""",
                (source, key, name, domain, d.display_name, d.entity_key, len(people)),
            )
            for person, role in people:
                await db.execute(
                    "INSERT INTO company_register_people (register_id, full_name, relationship, person_level, source_url)"
                    " VALUES (?, ?, ?, 'executive', 'https://sec.example/filing')",
                    (cur.lastrowid, person, role),
                )
            return cur.lastrowid

        await company("Harbor Robotics Inc", "sec_form_d", "hr1", "harborrobotics.com",
                      [("Dana Whitfield", "Chief Executive Officer")])
        await company("Lookup Pharma Inc", "sec_public", "0000000123", "lookuppharma.com", [])
        await db.commit()
    finally:
        await db.close()


async def store_elena(register_id: int) -> dict:
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO company_register_people (register_id, full_name, relationship, person_level, source_url)"
            " VALUES (?, 'Elena Voss', 'Chief Financial Officer', 'executive', 'https://sec.example/lp')",
            (register_id,),
        )
        await db.execute("UPDATE company_register SET officer_count = 1 WHERE id = ?", (register_id,))
        await db.commit()
    finally:
        await db.close()
    return {"ok": True, "attached": 1, "officer_count": 1}


async def the_fetch_reads_only_registers_that_publish_officers_and_have_none() -> None:
    fetch = AsyncMock(side_effect=store_elena)
    with patch("app.services.company_register.fetch_officers_for", fetch):
        # Officers already stored, a private filer, an unknown company: nothing to read.
        for name in ("Harbor Robotics", "Nowhere Holdings Corp"):
            assert await RE.fetch_register_officers(name, None) == []
        fetch.assert_not_awaited()
        # A listed company with none stored is read from its register, and found after.
        assert await RE.fetch_register_officers("Lookup Pharma", None) == []
        assert fetch.await_count == 1
        people = await RE.register_officer_contacts("Lookup Pharma", "lookuppharma.com")
        assert [(p["name"], p["email"]) for p in people] == [("Elena Voss", "elena.voss@lookuppharma.com")], people
        # Stored now: asking again reads nothing.
        assert await RE.fetch_register_officers("Lookup Pharma", None) == []
        assert fetch.await_count == 1

    # A register that cannot be read says why instead of reporting an empty list.
    async def reset() -> None:
        db = await get_db()
        try:
            await db.execute("DELETE FROM company_register_people WHERE full_name = 'Elena Voss'")
            await db.execute("UPDATE company_register SET officer_count = 0 WHERE source = 'sec_public'")
            await db.commit()
        finally:
            await db.close()

    await reset()
    broken = AsyncMock(return_value={"ok": False, "error": "SEC did not answer"})
    with patch("app.services.company_register.fetch_officers_for", broken):
        assert await RE.fetch_register_officers("Lookup Pharma", None) == ["SEC did not answer"]
    await reset()


async def a_search_includes_the_officers_it_had_to_fetch() -> None:
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO yucgoutreach_discovery_runs
               (id, user_id, company_name, company_domain, max_prospects, status, lease_token, research_json, origin)
               VALUES (1, 2, 'Lookup Pharma', 'lookuppharma.com', 10, 'running', 'lease-1', ?, 'member')""",
            (json.dumps({"levels": ["working", "executive"]}),),
        )
        await db.commit()
    finally:
        await db.close()

    async def verify(rows, **_kw):
        return [dict(r, ai_verdict="real", email_verification_status="valid") for r in rows], []

    token = D._active_lease.set("lease-1")
    try:
        with patch("app.services.company_register.fetch_officers_for", AsyncMock(side_effect=store_elena)), \
             patch.object(D, "scrape_contacts_from_domain", AsyncMock(return_value=[])), \
             patch.object(D, "_llm_json", AsyncMock(return_value={})), \
             patch.object(D, "_company_meta", AsyncMock(return_value={})), \
             patch.object(D, "_tavily_name_seeds", AsyncMock(return_value=[])), \
             patch.object(D, "run_contact_verify_pipeline", verify), \
             patch.object(RE, "cached_roster_contacts", AsyncMock(return_value=[])), \
             patch.object(RE, "refresh_roster_on_demand", AsyncMock(return_value=[])), \
             patch.object(RE, "roster_refresh_note", AsyncMock(return_value="")), \
             patch.object(R, "remember_discovery_people", AsyncMock()):
            await D.execute_yucgoutreach_run(1)
    finally:
        D._active_lease.reset(token)
    db = await get_db()
    try:
        rows = [dict(r) for r in await (await db.execute(
            "SELECT email, title, contact_source FROM yucgoutreach_prospects WHERE run_id = 1")).fetchall()]
    finally:
        await db.close()
    assert rows == [{"email": "elena.voss@lookuppharma.com", "title": "Chief Financial Officer",
                     "contact_source": "roster_register"}], rows


async def main() -> None:
    await seed()
    await the_fetch_reads_only_registers_that_publish_officers_and_have_none()
    await a_search_includes_the_officers_it_had_to_fetch()


def test_register_officers_lookup() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    asyncio.run(main())
    print("register officers lookup tests passed")
