"""The club's one Intro, its per-company line, and the send warnings.

Every member sends Amber's Intro: the recipient's first name, the sender's
first name from their sign-off, the company, and one line per company the
member confirms. The line is suggested from material every member may see and
the club may name - never a private past project - one company at a time.
A send that writes to many people at one company, or to one team there, is
warned about (never blocked).
From backend/: python tests/test_outreach_intro.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JWT_SECRET", "outreach-intro-test-secret-0123456789abc")
os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.mkdtemp()) / 'intro.db'}"
for name in ("SEND_WARN_PEOPLE_PER_COMPANY", "SEND_WARN_PEOPLE_PER_TEAM"):
    os.environ.pop(name, None)

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from app.database import get_db, init_db  # noqa: E402
from app.jwt_utils import create_token  # noqa: E402
from app.services import personalisation  # noqa: E402
from app.services.email_templates import CLUB_TEMPLATE_BODY, CLUB_TEMPLATE_SUBJECT, one_club_template  # noqa: E402
from app.services.send_warnings import batch_warnings  # noqa: E402

ADMIN, ANA = 1, 2


async def run(sql: str, args: tuple = ()) -> list:
    db = await get_db()
    try:
        rows = [tuple(r) for r in await (await db.execute(sql, args)).fetchall()]
        await db.commit()
        return rows
    finally:
        await db.close()


def client(uid: int, role: str) -> TestClient:
    c = TestClient(main.app, raise_server_exceptions=False)
    c.cookies.set("yucg_session", create_token(uid, f"{uid}@yale.edu", None, None, role))
    return c


async def seed() -> None:
    await init_db()
    for uid, name, role in [(ADMIN, "Admin Person", "admin"), (ANA, "Ana Lopez", "standard")]:
        await run("INSERT INTO users (id, email, name, role, is_active) VALUES (?, ?, ?, ?, 1)",
                  (uid, f"{uid}@yale.edu", name, role))
    for cid, name, title, company in [
        (1, "Dana Fox", "Director of Operations", "Acme Health"),
        (2, "Eli Park", "VP Strategy", "Globex Energy"),
    ]:
        await run("INSERT INTO contacts (id, name, email, title, company) VALUES (?, ?, ?, ?, ?)",
                  (cid, name, f"p{cid}@example.com", title, company))
    # What the register knows: Acme is a hospital operator, Globex an energy company.
    for key, company, sector, why in [
        ("acme", "Acme Health", "Healthcare providers", "Regional hospital network expanding outpatient clinics"),
        ("globex", "Globex Energy", "Renewable energy", "Utility-scale solar developer entering storage"),
    ]:
        await run("""INSERT INTO company_register (source, source_key, tier, company_name, sector_label, metadata_json)
                     VALUES ('test', ?, 'A', ?, ?, ?)""", (key, company, sector, f'{{"why_attractive": "{why}"}}'))
    # Past projects in the same industry as Acme, one of each disclosure.
    stamp = 1_700_000_000
    for title, client_name, disclosure in [
        ("Clinic demand study", "Harbor Clinics", "public"),
        ("Payer strategy", "Northfield Hospitals", "name_only"),
        ("Confidential turnaround", "Secret Healthcare Co", "private"),
    ]:
        await run("""INSERT INTO resources (kind, title, client, semester, industry, areas, description, disclosure,
                                            created_at, updated_at)
                     VALUES ('past_project', ?, ?, 'Fall 2025', 'Healthcare providers', 'outpatient clinics', ?, ?, ?, ?)""",
                  (title, client_name, f"{title} findings", disclosure, stamp, stamp))


def the_club_has_one_template_and_it_is_ambers() -> None:
    rows = asyncio.run(run("SELECT name, subject, body, user_id FROM email_templates"))
    assert rows == [("Intro", CLUB_TEMPLATE_SUBJECT, CLUB_TEMPLATE_BODY, None)], rows
    listed = client(ANA, "standard").get("/api/outreach/templates").json()
    assert [(t["name"], t["scope"]) for t in listed] == [("Intro", "club")], listed


def the_migration_keeps_the_seeded_intro_and_every_saved_template() -> None:
    """An older database holds the four starters, an administrator's club
    template and a member's own. Only the starters other than Intro go; Intro
    keeps its id and subject and takes Amber's body."""
    async def scenario() -> list:
        await run("DELETE FROM email_templates")
        for name, subject in [("Intro", "Old intro subject"), ("Alumni connection", "s"), ("Project pitch", "s"), ("Follow-up", "s")]:
            await run("INSERT INTO email_templates (name, subject, body, user_id) VALUES (?, ?, 'old body {first}', NULL)",
                      (name, subject))
        await run("INSERT INTO email_templates (name, subject, body, user_id) VALUES ('Admin pitch', 's', 'b', ?)", (ADMIN,))
        await run("INSERT INTO email_templates (name, subject, body, user_id) VALUES ('Ana own', 's', 'b', ?)", (ANA,))
        intro_id = (await run("SELECT id FROM email_templates WHERE name = 'Intro'"))[0][0]
        db = await get_db()
        try:
            await one_club_template(db)
            await db.commit()
        finally:
            await db.close()
        rows = await run("SELECT id, name, subject, body, user_id FROM email_templates ORDER BY id")
        return [intro_id, rows]

    intro_id, rows = asyncio.run(scenario())
    assert rows == [
        (intro_id, "Intro", "Old intro subject", CLUB_TEMPLATE_BODY, None),
        (rows[1][0], "Admin pitch", "s", "b", ADMIN),
        (rows[2][0], "Ana own", "s", "b", ANA),
    ], rows
    # Back to the fresh state for the rest of the file.
    asyncio.run(run("DELETE FROM email_templates WHERE user_id IS NOT NULL"))


def the_intro_merges_names_company_and_each_companys_line() -> None:
    ana = client(ANA, "standard")
    intro = ana.get("/api/outreach/templates").json()[0]["id"]
    shown = ana.post("/api/campaigns/build", json={
        "template_id": intro, "contact_ids": [1, 2], "preview_only": True,
        "personalisation": {"Acme Health": "Your outpatient expansion is exactly the kind of work we enjoy.",
                            "Globex Energy": ""},
    }).json()
    assert shown["ready"] == 2 and shown["held"] == [], shown
    # The companies a line is asked for, once each (the Queue panel has no contact rows to read them from).
    assert shown["companies"] == ["Acme Health", "Globex Energy"], shown
    assert shown["sample"]["body"] == (
        "Hi Dana,\n\nI hope you're doing well!\n\n"
        "I'm Ana and I'm reaching out from the Yale Undergraduate Consulting Group. We're a team of undergraduate "
        "students who work with companies to better understand their customers, explore new markets, and identify "
        "opportunities for growth.\n\n"
        "I'd love to work with Acme Health on a project in the new year. Your outpatient expansion is exactly the kind "
        "of work we enjoy.\n\n"
        "If this sounds interesting, I'd love to hop on a 15 minute call to talk about specific project ideas further "
        "and answer any questions. Please do let me know when you'd be available!\n\n"
        "Thank you!\n\nBest,\nAna"), shown["sample"]["body"]

    built = ana.post("/api/campaigns/build", json={
        "template_id": intro, "contact_ids": [1, 2], "name": "Intro test",
        "personalisation": {"Acme Health": "Your outpatient expansion is exactly the kind of work we enjoy.",
                            "Globex Energy": ""},
    })
    assert built.status_code == 200, built.text
    bodies = dict(asyncio.run(run("SELECT contact_id, email_body FROM campaign_contacts WHERE campaign_id = ?",
                                  (built.json()["campaign_id"],))))
    # A line confirmed empty leaves the sentence before it standing alone.
    assert "with Globex Energy on a project in the new year.\n\nIf this" in bodies[2], bodies[2]
    assert "outpatient" not in bodies[2] and "outpatient expansion" in bodies[1]
    # The sign-off's name, not the account's, is the sender's.
    asyncio.run(run("INSERT INTO settings (key, value) VALUES ('member:2:sign_off_name', 'Amber Q. Stone')"))
    again = ana.post("/api/campaigns/build", json={"template_id": intro, "contact_ids": [2], "preview_only": True}).json()
    assert again["sample"]["body"].startswith("Hi Eli,") and "I'm Amber and" in again["sample"]["body"]
    assert again["sample"]["body"].endswith("Best,\nAmber")
    too_long = ana.post("/api/campaigns/build", json={"template_id": intro, "contact_ids": [1], "preview_only": True,
                                                       "personalisation": {"Acme Health": "x" * 600}})
    assert too_long.status_code == 422


def private_projects_never_reach_the_model() -> None:
    """Same industry as Acme, so only the disclosure keeps it out."""
    disclosed = asyncio.run(_disclosed())
    assert {p["client"] for p in disclosed} == {"Harbor Clinics", "Northfield Hospitals"}, disclosed

    prompts: list[str] = []

    def fake(prompt, *_args, **_kwargs):
        prompts.append(prompt)
        return "We would love to build on our clinic work."

    with patch("app.services.llm.complete_text", side_effect=fake):
        answer = client(ANA, "standard").post("/api/campaigns/personalisation", json={"company": "Acme Health"})
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert "Secret Healthcare" not in prompts[0] and "Confidential turnaround" not in prompts[0]
    assert not any("Secret" in s for s in body["sources"]), body
    # Both disclosed projects match the industry; a name-only one gives its client, never its deliverable.
    assert "Harbor Clinics" in prompts[0] and "Clinic demand study" in prompts[0]
    assert "Northfield Hospitals" in prompts[0] and "Payer strategy" not in prompts[0]


async def _disclosed() -> list:
    db = await get_db()
    try:
        return await personalisation.disclosed_projects(db)
    finally:
        await db.close()


def each_company_gets_its_own_line() -> None:
    prompts: list[str] = []

    def fake(prompt, *_args, **_kwargs):
        prompts.append(prompt)
        company = prompt.splitlines()[0].removeprefix("Company: ")
        return f'"We have ideas for {company} specifically."'

    ana = client(ANA, "standard")
    with patch("app.services.llm.complete_text", side_effect=fake):
        lines = {c: ana.post("/api/campaigns/personalisation", json={"company": c}).json()
                 for c in ("Acme Health", "Globex Energy")}
        retry = ana.post("/api/campaigns/personalisation",
                         json={"company": "Globex Energy", "previous": lines["Globex Energy"]["line"]})
    assert lines["Acme Health"]["line"] != lines["Globex Energy"]["line"], lines
    assert lines["Acme Health"]["line"] == "We have ideas for Acme Health specifically.", "quotes are stripped"
    acme, globex = prompts[0], prompts[1]
    assert "Globex" not in acme and "Acme" not in globex, "one company's material never reaches another's line"
    assert "outpatient" in acme and "solar" in globex
    # Globex shares no industry with the club's projects: none are offered.
    assert "Harbor Clinics" not in globex
    assert "Write a different sentence" in prompts[2] and retry.status_code == 200

    # The model being off is said plainly, and writing the line yourself costs no call.
    from fastapi import HTTPException

    with patch("app.services.llm.complete_text", side_effect=HTTPException(503, "off")):
        down = ana.post("/api/campaigns/personalisation", json={"company": "Acme Health"})
    assert down.status_code == 503 and "write this line yourself" in down.json()["detail"], down.text


def many_people_at_one_company_or_one_team_are_warned_about() -> None:
    def people(titles: list[str], company: str = "Initech") -> list[dict]:
        return [{"id": i + 1, "title": t, "company": company} for i, t in enumerate(titles)]

    marketing = ["Marketing Manager", "Chief Marketing Officer", "Director of Brand", "VP Marketing"]
    warnings = batch_warnings(people(marketing))
    assert [w["kind"] for w in warnings] == ["team"], warnings
    team = warnings[0]
    assert (team["company"], team["team"], team["count"], team["threshold"], team["keep"]) == ("Initech", "Marketing", 4, 3, 2)
    # Most senior first, so "keep the most senior 2" keeps the CMO and the VP.
    assert team["contact_ids"] == [2, 4, 3, 1], team

    # Below the team threshold, and people with no function, are not a team.
    assert batch_warnings(people(["Marketing Manager", "VP Marketing", "Director", "Senior Manager"])) == []
    # Five at one company is the company threshold, whatever their teams.
    five = batch_warnings(people(["CFO", "VP Sales", "Head of HR", "General Counsel", "CTO"]))
    assert [(w["kind"], w["count"], w["threshold"]) for w in five] == [("company", 5, 5)], five
    # Companies are counted apart.
    split = people(marketing[:2]) + [dict(p, id=p["id"] + 10) for p in people(marketing[2:], "Hooli")]
    assert batch_warnings(split) == []

    with patch.dict(os.environ, {"SEND_WARN_PEOPLE_PER_TEAM": "5", "SEND_WARN_PEOPLE_PER_COMPANY": "4"}):
        moved = batch_warnings(people(marketing))
    assert [(w["kind"], w["threshold"]) for w in moved] == [("company", 4)], moved

    # The send preview carries them for the confirm step.
    async def add() -> None:
        for i, title in enumerate(marketing):
            await run("INSERT INTO contacts (id, name, email, title, company) VALUES (?, ?, ?, ?, 'Initech')",
                      (100 + i, f"Person {chr(65 + i)} Smith", f"m{i}@initech.example", title))
    asyncio.run(add())
    shown = client(ANA, "standard").post("/api/campaigns/build", json={
        "subject": "Hello {first}", "body": "Hi {first}", "contact_ids": [100, 101, 102, 103], "preview_only": True,
    }).json()
    assert [(w["kind"], w["team"], w["contact_ids"][:2]) for w in shown["warnings"]] == [("team", "Marketing", [101, 103])], shown


if __name__ == "__main__":
    asyncio.run(seed())
    the_club_has_one_template_and_it_is_ambers()
    the_migration_keeps_the_seeded_intro_and_every_saved_template()
    the_intro_merges_names_company_and_each_companys_line()
    private_projects_never_reach_the_model()
    each_company_gets_its_own_line()
    many_people_at_one_company_or_one_team_are_warned_about()
    print("outreach intro: ok")
