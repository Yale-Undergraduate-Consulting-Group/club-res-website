"""POST /api/yucgoutreach/officers: a company's filed officers become the
member's people with no web search. From backend/: python tests/test_register_officers_lookup.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JWT_SECRET", "officers-test-secret-0123456789abcdef")
os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.mkdtemp()) / 't.db'}"

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from app.database import get_db, init_db  # noqa: E402
from app.jwt_utils import create_token  # noqa: E402
from app.services.register_names import derive  # noqa: E402

ANA, BEN = 2, 3


async def seed() -> None:
    await init_db()
    db = await get_db()
    try:
        for uid, name in ((ANA, "Ana"), (BEN, "Ben")):
            await db.execute(
                "INSERT INTO users (id, email, name, role, is_active) VALUES (?, ?, ?, 'standard', 1)",
                (uid, f"{name.lower()}@yale.edu", name),
            )

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
                      [("Dana Whitfield", "Chief Executive Officer"), ("Priya Raman", "Chief Financial Officer")])
        await company("Quiet Holdings Inc", "sec_form_d", "qh1", "quietholdings.com", [])
        await company("Sitewise Corp", "sec_form_d", "sw1", None, [("Maya Okafor", "Chief Operating Officer")])
        await company("Lookup Pharma Inc", "sec_public", "0000000123", "lookuppharma.com", [])
        await db.commit()
    finally:
        await db.close()


def client(uid: int) -> TestClient:
    c = TestClient(main.app, raise_server_exceptions=False)
    c.cookies.set("yucg_session", create_token(uid, f"{uid}@yale.edu", None, None, "standard"))
    return c


async def contacts(email: str) -> list[dict]:
    db = await get_db()
    try:
        rows = await (await db.execute("SELECT * FROM contacts WHERE email = ?", (email,))).fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


def tests() -> None:
    asyncio.run(seed())
    ana, ben = client(ANA), client(BEN)
    url = "/api/yucgoutreach/officers"

    assert TestClient(main.app, raise_server_exceptions=False).post(url, json={"company_name": "Harbor Robotics"}).status_code == 401
    assert ana.post(url, json={"company_name": ""}).status_code == 422

    # A company the register has never heard of says so, and creates nothing.
    unknown = ana.post(url, json={"company_name": "Nowhere Holdings Corp"})
    assert unknown.status_code == 404 and "No company filing" in unknown.json()["detail"], unknown.text

    # A filing that names nobody is a clear, successful nothing.
    quiet = ana.post(url, json={"company_name": "Quiet Holdings"})
    assert quiet.status_code == 200 and quiet.json()["created"] == 0 and "no current officers" in quiet.json()["note"], quiet.text

    # The officers land as the member's own contacts, under the name they chose.
    first = ana.post(url, json={"company_name": "Harbor Robotics", "company_domain": "harborrobotics.com"})
    assert first.status_code == 200, first.text
    body = first.json()
    assert (body["officers"], body["created"], body["updated"]) == (2, 2, 0), body
    rows = asyncio.run(contacts("dana.whitfield@harborrobotics.com"))
    assert len(rows) == 1, rows
    row = rows[0]
    assert (row["owner_id"], row["company"], row["title"], row["contact_source"]) == (
        ANA, "Harbor Robotics", "Chief Executive Officer", "roster_register"), row
    assert {r["outcome"] for r in body["results"]} == {"created"} and all(r["contact_id"] for r in body["results"])

    # Again is idempotent; the register's own website is used when none is sent.
    again = ana.post(url, json={"company_name": "Harbor Robotics"}).json()
    assert (again["created"], again["updated"]) == (0, 2), again

    # An address another member already works stays theirs, and the answer says whose.
    held = ben.post(url, json={"company_name": "Harbor Robotics"}).json()
    assert (held["created"], held["updated"], held["skipped"]) == (0, 0, 2), held
    assert "already worked by Ana" in held["held_by_note"], held
    assert asyncio.run(contacts("dana.whitfield@harborrobotics.com"))[0]["owner_id"] == ANA

    # Officers with no website to derive an address from are counted, not invented.
    nosite = ana.post(url, json={"company_name": "Sitewise"})
    assert nosite.status_code == 200 and nosite.json()["created"] == 0 and nosite.json()["without_address"] == 1, nosite.text
    assert "no website" in nosite.json()["note"]

    # A listed company with no stored officers is read from its register in the same action.
    async def fake_fetch(register_id: int) -> dict:
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

    with patch("app.services.company_register.fetch_officers_for", fake_fetch):
        fetched = ana.post(url, json={"company_name": "Lookup Pharma"})
    assert fetched.status_code == 200 and fetched.json()["created"] == 1, fetched.text
    assert asyncio.run(contacts("elena.voss@lookuppharma.com"))[0]["owner_id"] == ANA

    # A register that cannot be read is an error with its reason, not an empty list.
    async def broken(db_id: int) -> dict:
        return {"ok": False, "error": "SEC did not answer"}

    async def reset() -> None:
        db = await get_db()
        try:
            await db.execute("DELETE FROM company_register_people WHERE full_name = 'Elena Voss'")
            await db.execute("UPDATE company_register SET officer_count = 0 WHERE source = 'sec_public'")
            await db.commit()
        finally:
            await db.close()

    asyncio.run(reset())
    with patch("app.services.company_register.fetch_officers_for", broken):
        failed = ana.post(url, json={"company_name": "Lookup Pharma"})
    assert failed.status_code == 502 and "SEC did not answer" in failed.json()["detail"], failed.text


if __name__ == "__main__":
    tests()
    print("register officers lookup tests passed")
