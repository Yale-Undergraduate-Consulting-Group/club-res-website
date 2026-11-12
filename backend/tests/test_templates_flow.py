"""Saved templates, one-click target-list builds, and "what earns replies".

A template is the club's when an administrator owns it (or it is a starter),
and private to its author otherwise; every route that reads or uses one goes
through the same rule. /build renders a template per recipient and explains the
people it cannot render for, can take its recipients from a target list the
member may read, and files each row under its template and approach so Admin →
Metrics can compare them. Without a model, drafting answers with what to do.
From backend/: python tests/test_templates_flow.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JWT_SECRET", "templates-flow-test-secret-0123456789abc")
os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.mkdtemp()) / 't.db'}"

from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from app.database import get_db, init_db  # noqa: E402
from app.jwt_utils import create_token  # noqa: E402

ADMIN, ANA, BEN = 1, 2, 3


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
    for uid, name, role in [(ADMIN, "Admin", "admin"), (ANA, "Ana", "standard"), (BEN, "Ben", "standard")]:
        await run("INSERT INTO users (id, email, name, role, is_active) VALUES (?, ?, ?, ?, 1)",
                  (uid, f"{uid}@yale.edu", name, role))
    # Acme: two people who can be written to, one with no name (a {first}
    # message cannot be rendered for her), one with no email, and one that
    # belongs to Ben alone.
    for cid, name, email, owner in [(1, "Dana Fox", "dana@acme.com", None), (2, "", "info@acme.com", None),
                                    (3, "No Mail", "", None), (4, "Private Pat", "pat@acme.com", BEN),
                                    (5, "Eli Park", "eli@globex.com", None)]:
        await run("INSERT INTO contacts (id, name, email, title, company, owner_id) VALUES (?, ?, ?, 'Director', ?, ?)",
                  (cid, name, email, "Acme" if cid < 5 else "Globex", owner))
    # Ana's own list, and a club list.
    await run("INSERT INTO outreach_campaigns (id, name, type, owner_id) VALUES (1, 'Ana list', 'individual', ?)", (ANA,))
    await run("INSERT INTO outreach_campaigns (id, name, type, owner_id) VALUES (2, 'Club list', 'community', NULL)")
    for list_id, cid in [(1, 1), (1, 2), (1, 3), (1, 4), (2, 5)]:
        await run("INSERT INTO outreach_campaign_contacts (campaign_id, contact_id) VALUES (?, ?)", (list_id, cid))


def make_template(c: TestClient, name: str, subject: str, body: str, use_case: str | None = "Intro") -> int:
    r = c.post("/api/outreach/templates", json={"name": name, "subject": subject, "body": body, "use_case": use_case})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def tests() -> None:
    asyncio.run(seed())
    admin, ana, ben = client(ADMIN, "admin"), client(ANA, "standard"), client(BEN, "standard")

    # Starter templates exist for everyone to use, and are the club's.
    starters = ana.get("/api/outreach/templates").json()
    assert len(starters) == 4 and {t["scope"] for t in starters} == {"club"}, starters
    assert not any(t["editable"] for t in starters), "a member cannot remove the club's starters"

    # Private versus club.
    private = make_template(ana, "Ana pitch", "Hello {first}", "About {company}.")
    shared = make_template(admin, "Admin pitch", "For {company}", "Hi {first}.", use_case="Project Pitch")
    assert private not in [t["id"] for t in ben.get("/api/outreach/templates").json()]
    assert private not in [t["id"] for t in admin.get("/api/outreach/templates").json()], "private means private, to admins too"
    seen = {t["id"]: t for t in ben.get("/api/outreach/templates").json()}
    assert shared in seen and seen[shared]["scope"] == "club" and not seen[shared]["editable"]
    mine = {t["id"]: t for t in ana.get("/api/outreach/templates").json()}
    assert mine[private]["scope"] == "mine" and mine[private]["editable"]
    assert ben.delete(f"/api/outreach/templates/{private}").status_code == 404
    assert ben.delete(f"/api/outreach/templates/{shared}").status_code == 403

    # A typo is refused when saved, not mailed later.
    typo = ana.post("/api/outreach/templates", json={"name": "t", "subject": "Hi {firstname}", "body": "b"})
    assert typo.status_code == 422 and "{firstname}" in typo.json()["detail"], typo.text

    # Preview: the merge, and the same refusal, plus the fields on offer.
    preview = ana.post("/api/outreach/templates/preview", json={"subject": "Hi {first}", "body": "At {company}"}).json()
    assert preview["subject"] == "Hi Alex" and preview["body"] == "At Northwind Health" and preview["error"] is None
    assert {f["name"] for f in preview["fields"]} >= {"first", "company", "title"}
    broken = ana.post("/api/outreach/templates/preview", json={"subject": "Hi {firstname}", "body": "b"}).json()
    assert broken["error"] and "firstname" in broken["error"]

    # /build with a template: preview explains who is held and why.
    shown = ana.post("/api/campaigns/build", json={"template_id": private, "companies": ["Acme"], "preview_only": True}).json()
    assert shown["recipients"] == 2 and shown["ready"] == 1 and shown["skipped"] == 0, shown
    assert shown["sample"]["subject"] == "Hello Dana" and "Acme" in shown["sample"]["body"]
    assert len(shown["held"]) == 1 and "first" in shown["held"][0]["reason"], shown["held"]
    # Someone else's private template, and a template plus typed text, are refused.
    assert ben.post("/api/campaigns/build", json={"template_id": private, "companies": ["Acme"]}).status_code == 404
    both = ana.post("/api/campaigns/build", json={"template_id": private, "subject": "s", "body": "b", "companies": ["Acme"]})
    assert both.status_code == 422

    # Building files every row under the template and its approach.
    built = ana.post("/api/campaigns/build", json={"template_id": private, "companies": ["Acme"], "name": "Acme intro"})
    assert built.status_code == 200 and built.json()["created"] == 1, built.text
    rows = asyncio.run(run("SELECT template_id, angle, email_subject FROM campaign_contacts WHERE campaign_id = ?",
                           (built.json()["campaign_id"],)))
    assert rows == [(private, "intro", "Hello Dana")], rows
    # The Directory says which contacts row stands behind a person, so a selection can be queued.
    people = ana.get("/api/directory", params={"q": "dana@acme.com"}).json()["items"]
    assert [(r["email"], r["contact_id"], r["on_file"]) for r in people] == [("dana@acme.com", 1, True)], people
    typed = ana.post("/api/campaigns/build", json={"subject": "Hi {first}", "body": "b", "companies": ["Globex"], "angle": "Social Proof"}).json()
    assert asyncio.run(run("SELECT template_id, angle FROM campaign_contacts WHERE campaign_id = ?", (typed["campaign_id"],))) == [(None, "social proof")]
    # A club template's own use case is its approach.
    club_build = ben.post("/api/campaigns/build", json={"template_id": shared, "companies": ["Globex"]}).json()
    assert asyncio.run(run("SELECT template_id, angle FROM campaign_contacts WHERE campaign_id = ?", (club_build["campaign_id"],))) == [(shared, "project pitch")]

    # A target list: resolved server-side, behind the list's own access rule.
    listed = ana.post("/api/campaigns/build", json={"template_id": private, "target_list_id": 1, "preview_only": True}).json()
    # Four people on the list; the one without an email and Ben's private contact are left out and counted.
    assert (listed["recipients"], listed["skipped"], listed["ready"]) == (2, 2, 1), listed
    assert ben.post("/api/campaigns/build", json={"template_id": shared, "target_list_id": 1, "preview_only": True}).status_code == 404, \
        "another member's list must not be readable through /build"
    assert ben.post("/api/campaigns/build", json={"template_id": shared, "target_list_id": 2, "preview_only": True}).json()["recipients"] == 1
    assert ana.post("/api/campaigns/build", json={"template_id": shared, "target_list_id": 99, "preview_only": True}).status_code == 404
    assert ana.post("/api/campaigns/build", json={"template_id": shared, "target_list_id": 1, "contact_ids": [1], "preview_only": True}).status_code == 422

    # Adding contacts to an existing campaign: the template fills what is empty, held rows are explained.
    draft = ana.post("/api/campaigns", json={"name": "By hand"}).json()["id"]
    added = ana.post(f"/api/campaigns/{draft}/contacts", json={"contact_ids": [1, 2], "template_id": private}).json()
    assert added["added"] == 1 and [h["contact_id"] for h in added["held"]] == [2], added
    assert asyncio.run(run("SELECT template_id, angle, email_subject FROM campaign_contacts WHERE campaign_id = ?", (draft,))) == [(private, "intro", "Hello Dana")]
    other = ben.post("/api/campaigns", json={"name": "Ben"}).json()["id"]
    assert ben.post(f"/api/campaigns/{other}/contacts", json={"contact_ids": [5], "template_id": private}).status_code == 404

    # No model: drafting says what to do instead, and other failures are left alone.
    with patch("app.services.ollama_email_service.generate_group_template",
               side_effect=HTTPException(503, "The language model is unavailable right now.")):
        down = ana.post("/api/campaigns/draft-template", json={"companies": ["Acme"], "goal": "intro"})
    assert down.status_code == 503, down.text
    assert down.json()["detail"] == {"message": "AI drafting is unavailable right now — use a template.", "fallback": "template"}
    with patch("app.services.ollama_email_service.generate_group_template", side_effect=HTTPException(429, "busy")):
        assert ana.post("/api/campaigns/draft-template", json={"companies": ["Acme"], "goal": "intro"}).status_code == 429

    metrics()


def metrics() -> None:
    """Approach attribution reaches Admin → Metrics, judged only with enough cases."""
    admin = client(ADMIN, "admin")
    tid = asyncio.run(run("SELECT id FROM email_templates WHERE name = 'Ana pitch'"))[0][0]

    async def send(count: int, replied: int, template_id, angle) -> None:
        for n in range(count):
            # One person each: a campaign holds a contact once.
            cid = (await run("SELECT COALESCE(MAX(id), 0) + 1 FROM contacts"))[0][0]
            await run("INSERT INTO contacts (id, name, email) VALUES (?, 'Sent', ?)", (cid, f"s{cid}@x.com"))
            await run("""INSERT INTO campaign_contacts (campaign_id, contact_id, status, sent_at, replied_at, template_id, angle)
                         VALUES (1, ?, 'sent', datetime('now', '-1 days'), ?, ?, ?)""",
                      (cid, "2026-01-01" if n < replied else None, template_id, angle))

    asyncio.run(send(5, 1, tid, "intro"))     # judged: 20% against a 5% target
    asyncio.run(send(5, 0, None, "pain_point"))  # judged: no replies
    asyncio.run(send(2, 2, None, None))       # too few cases to judge, however good it looks
    body = admin.get("/api/analytics/club-metrics?days=30").json()
    by = {(a["template"], a["angle"]): a for a in body["approaches"]}
    ana_pitch = by[("Ana pitch", "intro")]
    assert (ana_pitch["sent"], ana_pitch["replied"], ana_pitch["value"], ana_pitch["met"]) == (5, 1, 20.0, True), ana_pitch
    assert by[(None, "pain_point")]["met"] is False
    assert by[(None, None)]["met"] is None and by[(None, None)]["sent"] == 2
    # The earlier builds sent nothing, so they are not cases.
    assert sum(a["sent"] for a in body["approaches"]) == 12
    assert client(ANA, "standard").get("/api/analytics/club-metrics").status_code == 403


if __name__ == "__main__":
    tests()
    print("templates flow: ok")
