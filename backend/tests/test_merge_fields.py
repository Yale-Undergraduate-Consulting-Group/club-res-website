"""One written message becomes one message per recipient.

The app has told members to write {first}, {last}, {company} and {title} in
templates for as long as templates have existed, and nothing substituted them.
A follow-up step written as "Hi {first}," would have arrived in the recipient's
inbox exactly like that. Nothing had ever been sent, so nobody found out.
"""
import asyncio
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JWT_SECRET", "merge-fields-test-secret-xxxxxxxxxx")

from fastapi import HTTPException

from app.database import get_db, init_db
from app.routers.campaigns import BuildFromTemplate, build_campaign_from_template
from app.services.merge_fields import MergeError, render, render_for_each, unknown_fields

CONTACT = {
    "id": 1, "name": "Jean Bartik", "email": "jean.bartik@a24films.com",
    "title": "Head of Acquisitions", "company": "A24",
}


def tests() -> None:
    assert render("Hi {first}, about {company}.", CONTACT) == "Hi Jean, about A24."
    assert render("{full_name} - {title}", CONTACT) == "Jean Bartik - Head of Acquisitions"
    assert render("Sent {date}", CONTACT) == f"Sent {date.today().strftime('%-d %B %Y')}"
    assert render("No fields here", CONTACT) == "No fields here"

    # A typo must not be mailed verbatim.
    assert unknown_fields("Hi {firstname} at {compnay}") == ["compnay", "firstname"]
    try:
        render("Hi {firstname}", CONTACT)
        raise AssertionError("an unknown field was rendered")
    except MergeError as exc:
        assert "{firstname} is not a field" in str(exc)

    # "Hi ," is worse than not sending: it is visibly generated and cannot be
    # taken back, so a missing value stops that one message.
    nameless = {"id": 2, "email": "info@a24films.com", "name": "", "company": "A24"}
    try:
        render("Hi {first}", nameless)
        raise AssertionError("a blank first name was rendered")
    except MergeError as exc:
        assert "info@a24films.com has no first" in str(exc)
    # The same recipient is fine for a message that does not use the field.
    assert render("About {company}", nameless) == "About A24"

    ready, held = render_for_each("For {company}", "Hi {first}.", [CONTACT, nameless])
    assert [r["contact_id"] for r in ready] == [1]
    assert ready[0]["subject"] == "For A24" and ready[0]["body"] == "Hi Jean."
    assert [h["contact_id"] for h in held] == [2], held


def build_endpoint_tests() -> None:
    """Preview and build share one path, so what the member reads before
    pressing the button is written by the code that creates the drafts."""
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["DATABASE_URL"] = "sqlite:///" + tmp + "/build.db"

        async def scenario() -> None:
            await init_db()
            db = await get_db()
            await db.execute("INSERT INTO users(id,email,role,is_active) VALUES (1,'me@yale.edu','standard',1)")
            for cid, name, email in [
                (1, "Jean Bartik", "jean.bartik@a24films.com"),
                (2, "Klara Dan", "klara.dan@a24films.com"),
                (3, "", "info@a24films.com"),
            ]:
                await db.execute(
                    "INSERT INTO contacts(id,name,email,title,company,owner_id) VALUES (?,?,?,'Lead','A24',1)",
                    (cid, name, email))
            await db.commit()
            await db.close()

            actor = {"id": 1, "role": "standard"}
            preview = await build_campaign_from_template(
                BuildFromTemplate(companies=["A24"], subject="For {company}",
                                  body="Hi {first}.", preview_only=True), actor)
            assert preview["recipients"] == 3 and preview["ready"] == 2, preview
            assert preview["sample"]["body"] == "Hi Jean."
            assert len(preview["held"]) == 1

            # Previewing creates nothing.
            db = await get_db()
            count = await (await db.execute("SELECT COUNT(*) n FROM campaigns")).fetchone()
            await db.close()
            assert count["n"] == 0, "preview created a campaign"

            built = await build_campaign_from_template(
                BuildFromTemplate(name="A24 run", companies=["A24"],
                                  subject="For {company}", body="Hi {first}."), actor)
            assert built["created"] == 2

            db = await get_db()
            rows = [dict(r) for r in await (await db.execute(
                "SELECT contact_id, email_subject, email_body, status FROM campaign_contacts ORDER BY contact_id")).fetchall()]
            campaign = await (await db.execute("SELECT name, status FROM campaigns")).fetchone()
            await db.close()
            # Each row holds that person's own rendered text, not the template.
            assert rows[0]["email_body"] == "Hi Jean." and rows[1]["email_body"] == "Hi Klara."
            assert all(r["status"] == "pending" for r in rows)
            # Building is not sending: the campaign is a draft to review.
            assert campaign["status"] == "draft" and campaign["name"] == "A24 run"

            # An unknown field is refused before anything is written.
            try:
                await build_campaign_from_template(
                    BuildFromTemplate(companies=["A24"], subject="x", body="Hi {firstname}"), actor)
                raise AssertionError("an unknown field was accepted")
            except HTTPException as exc:
                assert exc.status_code == 422 and "firstname" in str(exc.detail)

        asyncio.run(scenario())


def per_company_message_tests() -> None:
    """A campaign to several companies is usually several things to say. One
    message per company, each recipient rendered from their own company's -
    and a company nobody wrote for is held and named, never quietly sent the
    wrong pitch."""
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["DATABASE_URL"] = "sqlite:///" + tmp + "/per-company.db"

        async def scenario() -> None:
            await init_db()
            db = await get_db()
            await db.execute("INSERT INTO users(id,email,role,is_active) VALUES (1,'me@yale.edu','standard',1)")
            for cid, name, email, company in [
                (1, "Jean Bartik", "jean.bartik@a24films.com", "A24"),
                (2, "Klara Dan", "klara.dan@neon.com", "NEON"),
                (3, "Ada Lovelace", "ada@mubi.com", "MUBI"),
            ]:
                await db.execute(
                    "INSERT INTO contacts(id,name,email,title,company,owner_id) VALUES (?,?,?,'Lead',?,1)",
                    (cid, name, email, company))
            await db.commit()
            await db.close()

            actor = {"id": 1, "role": "standard"}
            messages = {
                "A24": {"subject": "Distribution at {company}", "body": "Hi {first}, about your slate."},
                "NEON": {"subject": "Awards run at {company}", "body": "Hi {first}, about the festival push."},
            }
            built = await build_campaign_from_template(
                BuildFromTemplate(name="Studios", companies=["A24", "NEON", "MUBI"],
                                  messages=messages), actor)
            # MUBI has nobody's message, so MUBI's person is held with the reason.
            assert built["created"] == 2, built
            assert [h["email"] for h in built["held"]] == ["ada@mubi.com"], built["held"]
            assert "MUBI" in built["held"][0]["reason"], built["held"]

            db = await get_db()
            rows = {r["contact_id"]: dict(r) for r in await (await db.execute(
                "SELECT contact_id, email_subject, email_body FROM campaign_contacts")).fetchall()}
            await db.close()
            assert rows[1]["email_subject"] == "Distribution at A24", rows
            assert rows[2]["email_body"] == "Hi Klara, about the festival push.", rows

            # With a group message written too, it covers the companies that
            # have none of their own rather than holding them.
            covered = await build_campaign_from_template(
                BuildFromTemplate(companies=["A24", "NEON", "MUBI"], messages=messages,
                                  subject="Working with {company}", body="Hi {first}.",
                                  preview_only=True), actor)
            assert covered["ready"] == 3 and covered["held"] == [], covered

            # A typo in one company's message is refused before anything is
            # written, exactly like the group message.
            try:
                await build_campaign_from_template(
                    BuildFromTemplate(companies=["A24"],
                                      messages={"A24": {"subject": "x", "body": "Hi {firstname}"}}), actor)
                raise AssertionError("an unknown field was accepted")
            except HTTPException as exc:
                assert exc.status_code == 422 and "firstname" in str(exc.detail)

        asyncio.run(scenario())


if __name__ == "__main__":
    tests()
    build_endpoint_tests()
    per_company_message_tests()
    print("merge fields: ok")
