"""Club Resources: who sees what, the import, and search. Synthetic data only:
the zip, table and Office files are built here with made-up clients."""
import asyncio
import functools
import io
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JWT_SECRET", "resources-test-secret-value-1234567890")
os.environ.pop("DOCUMENTS_BUCKET", None)
os.environ.pop("CATALOG_BUCKET", None)
os.environ.pop("APP_ENV", None)

import openpyxl  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app.auth_deps import get_current_admin  # noqa: E402
from app.database import get_db, init_db  # noqa: E402
from app.routers import resources as api, workspace  # noqa: E402
from app.services import document_storage, resource_import, resources  # noqa: E402
from app.services.slack_tools import run_tool  # noqa: E402

HEADER = ["Semester", "Client", "Completed?", "Public Disclosure?", "Industry", "Type/Areas", "Brief Project Description",
          "Code Name", "Team Size", "Points of Contact", "POC Emails", "Engagement Director(s)", "Project Manager(s)", "Associate(s)"]
ROWS = [
    ["Fall 2025", "Acme Corp", "Complete", "Yes - Deliverable & Name", "Healthcare; Technology", "Market sizing", "Size the widget market",
     "Rocket", 5, "Pat Contact", "pat@acme-example.test", "Team Person", "Ghost Name", "Twin Name"],
    ["Spring 2025", "Beta Foods", "Complete", "Yes - Name Only", "Consumer Goods", "Customer analysis", "Understand sprout buyers",
     "Sprout", 6, "Bo Contact", "bo@beta-example.test", "Team Person", "", ""],
    ["Spring 2024", "Gamma Bank", "Complete", "No", "Financial Services", "", "Vault strategy",
     "Vault", 4, "Gil Contact", "gil@gamma-example.test", "Team Person", "", ""],
    ["Fall 2023", "Delta Co", "N/A (No Records)", "TBD", "Retail", "", "Old retail work", "Comet", 3, "", "", "", "", ""],
    ["Fall 2024", "Epsilon Air", "Complete", "Yes - Name Only (Need to Confirm First)", "Aviation", "", "Seat study", "Jet", 3, "", "", "", "", ""],
]
REVENUE = {"rocket": 12345, "sprout": 777, "vault": 4242}


def table(description_suffix=""):
    workbook = openpyxl.Workbook()
    main = workbook.active
    main.title = "Detailed Project Database"
    main.append(HEADER)
    for row in ROWS:
        row = list(row)
        row[6] += description_suffix
        main.append(row)
    info = workbook.create_sheet("Detailed Project Info")
    info.append(["Semester", "Client", "Code Name", "Revenue"])
    for row in ROWS:
        if row[7].lower() in REVENUE:
            info.append([row[0], row[1], row[7], REVENUE[row[7].lower()]])
    out = io.BytesIO()
    workbook.save(out)
    return out.getvalue()


@functools.lru_cache
def pptx(text):
    from pptx import Presentation

    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    box = slide.shapes.add_textbox(0, 0, 1_000_000, 1_000_000)
    box.text_frame.text = text
    out = io.BytesIO()
    presentation.save(out)
    return out.getvalue()


@functools.lru_cache
def docx(text):
    from docx import Document

    document = Document()
    document.add_paragraph(text)
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def make_zip(path, description_suffix="", acme_text="giraffe market sizing"):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(resource_import.TOC_MEMBER, table(description_suffix))
        base = "Past Project Database/"
        z.writestr(base + "2025 Fall/Acme Corp (Rocket)/Final Deliverable.pptx", pptx(acme_text))
        z.writestr(base + "2025 Fall/Acme Corp (Rocket)/Acme SOW.pdf", b"fee 99999 hidden-fee-text")
        z.writestr(base + "2025 Spring/Beta Foods (Sprout)/Beta Final.pptx", pptx("zebra crossing analysis"))
        z.writestr(base + "2024 Spring/Gamma Bank (Vault)/Gamma Final.pptx", pptx("quokka vault study"))
        z.writestr(base + "2024 Fall/Epsilon Air (Jet)/Seats.pptx", pptx("window seat survey"))
        z.writestr(base + "2025 Fall/Orphan Inc (Zed)/orphan.pptx", pptx("lemur orphan notes"))
        z.writestr("Interview Resources/Interview Guidelines.docx", docx("wombat interview protocol"))
        z.writestr("Slide Templates/Baseline.pptx", pptx("baseline template"))
        z.writestr("Slide Templates/Exemplary Deliverables/Copy of client work.pptx", pptx("narwhal client copy"))


async def count(db, table_name):
    return (await (await db.execute(f"SELECT COUNT(*) AS n FROM {table_name}")).fetchone())["n"]


async def denied(coro, status=404):
    try:
        await coro
    except HTTPException as exc:
        assert exc.status_code == status, exc
        return
    raise AssertionError("unexpectedly allowed")


async def listing(user, **query):
    return await api.list_resources(user=user, **{"q": "", "kind": "", "semester": "", "industry": "", "limit": 100, "offset": 0, **query})


def by_title(listed):
    return {item["title"]: item for item in listed["items"]}


async def run():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        os.environ["DATABASE_URL"] = f"sqlite:///{tmp}/test.db"
        os.environ["DOCUMENTS_LOCAL_DIR"] = str(tmp / "files")
        archive = tmp / "export.zip"
        make_zip(archive)

        # A dry run before the database exists writes and stores nothing.
        dry = await resource_import.run_import(archive, dry_run=True)
        assert dry["table_rows"] == 5 and dry["matched"] == 4
        assert dry["folders_without_row"] == ["Fall 2025/Orphan Inc (Zed)"]
        assert dry["rows_without_folder"] == ["Fall 2023: Delta Co"]
        assert dry["held_back_files"] == 0
        assert dry["visibility"] == {
            "interview/public": 1, "past_project/name_only": 1, "past_project/private": 4, "past_project/public": 1,
            "template/public": 2,
        }, dry["visibility"]  # the caveated "Name Only" steps down to private; the orphan folder is private
        assert dry["admin_only_files"] == 1
        assert not (tmp / "files").exists()

        await init_db()
        db = await get_db()
        await db.executemany(
            "INSERT INTO users(id,email,name,role,is_active) VALUES (?,?,?,?,1)",
            [(1, "member@yale.edu", "Plain Member", "standard"), (2, "team@yale.edu", "Team Person", "standard"),
             (3, "admin@yale.edu", "Club Admin", "admin"), (4, "twin1@yale.edu", "Twin Name", "standard"),
             (5, "twin2@yale.edu", "twin  NAME", "standard")],
        )
        await db.commit()

        summary = await resource_import.run_import(archive, log=lambda _line: None)
        assert summary["file_results"] == {"added": 9}, summary
        # Team: "Team Person" matched on three resources; the ghost and the ambiguous twins grant nothing.
        assert summary["team"] == {"matched": 3, "unmatched": 2}, summary["team"]
        counts = {t: await count(db, t) for t in ("resources", "resource_files", "workspace_documents", "workspace_document_versions", "resource_team")}
        assert counts["resources"] == 9 and counts["resource_files"] == 9, counts

        # Idempotent: the same zip changes nothing; a changed row and file update in place.
        again = await resource_import.run_import(archive, log=lambda _line: None)
        assert again["file_results"] == {"unchanged": 9}, again
        assert {t: await count(db, t) for t in counts} == counts
        make_zip(archive, description_suffix=" (revised)", acme_text="giraffe market sizing revised")
        changed = await resource_import.run_import(archive, log=lambda _line: None)
        assert changed["file_results"] == {"unchanged": 8, "updated": 1}, changed
        after = {t: await count(db, t) for t in counts}
        assert after["resources"] == counts["resources"] and after["workspace_documents"] == counts["workspace_documents"]
        assert after["workspace_document_versions"] == counts["workspace_document_versions"] + 1
        row = await (await db.execute("SELECT description FROM resources WHERE code_name='Rocket'")).fetchone()
        assert row["description"] == "Size the widget market (revised)"

        member, team, admin = (
            {"id": 1, "email": "member@yale.edu", "role": "standard"},
            {"id": 2, "email": "team@yale.edu", "role": "standard"},
            {"id": 3, "email": "admin@yale.edu", "role": "admin"},
        )

        # --- Visibility matrix -------------------------------------------------
        seen = {name: by_title(await listing(u)) for name, u in (("member", member), ("team", team), ("admin", admin))}
        assert set(seen["member"]) == {"Acme Corp (Rocket)", "Beta Foods (Sprout)", "Interview Guidelines", "Baseline", "Copy of client work"}, set(seen["member"])
        assert set(seen["team"]) == set(seen["member"]) | {"Gamma Bank (Vault)"}, set(seen["team"])
        assert len(seen["admin"]) == 9
        acme, beta = seen["member"]["Acme Corp (Rocket)"], seen["member"]["Beta Foods (Sprout)"]
        assert acme["files_open"] and [f["filename"] for f in acme["files"]] == ["Final Deliverable.pptx"]  # the SOW is not mentioned
        assert acme["files_hidden_count"] == 0
        assert not beta["files_open"] and beta["files"] == [] and beta["files_hidden_count"] == 1
        assert beta["client"] == "Beta Foods"  # name_only: members see the client
        assert seen["member"]["Baseline"]["files_open"] and seen["member"]["Interview Guidelines"]["files_open"]
        assert seen["team"]["Beta Foods (Sprout)"]["files_open"]
        assert seen["team"]["Gamma Bank (Vault)"]["files_open"]
        assert not seen["team"]["Acme Corp (Rocket)"]["files"][0]["admin_only"] and len(seen["team"]["Acme Corp (Rocket)"]["files"]) == 1
        assert {f["filename"] for f in seen["admin"]["Acme Corp (Rocket)"]["files"]} == {"Final Deliverable.pptx", "Acme SOW.pdf"}
        assert [t["member"] for t in seen["member"]["Acme Corp (Rocket)"]["team"]] == [True, False, False]
        # Resource documents never leak through the generic document register,
        # even to the importing owner; its shares would bypass disclosure.
        assert await workspace.documents(user=admin) == []
        resource_document = seen["admin"]["Acme Corp (Rocket)"]["files"][0]["id"]
        await denied(workspace.versions(resource_document, user=admin))
        assert (await workspace.storage_usage(db, admin["id"]))["reserved_bytes"] == 0

        # Revenue and contacts: admin only, on every shape the page API returns.
        for who in ("member", "team"):
            payload = json.dumps(seen[who])
            for secret in ("revenue", "contacts", "12345", "pat@acme-example.test", "Pat Contact", "gil@gamma-example.test"):
                assert secret not in payload, (who, secret)
        assert seen["admin"]["Acme Corp (Rocket)"]["revenue"] == 12345.0
        assert seen["admin"]["Acme Corp (Rocket)"]["contacts"] == [{"name": "Pat Contact", "email": "pat@acme-example.test"}]
        single = await api.get_resource(acme["id"], member)
        assert "revenue" not in single and "contacts" not in single
        assert (await api.get_resource(acme["id"], admin))["revenue"] == 12345.0

        gamma_id = seen["admin"]["Gamma Bank (Vault)"]["id"]
        await denied(api.get_resource(gamma_id, member))
        await denied(api.get_resource(9999, member))
        assert (await api.get_resource(gamma_id, team))["title"] == "Gamma Bank (Vault)"

        # Facets and filters only cover what the viewer may see.
        listed = await listing(member)
        assert "Financial Services" not in listed["facets"]["industries"] and "Healthcare" in listed["facets"]["industries"]
        assert listed["facets"]["semesters"][0] == "Fall 2025"
        assert set(by_title(await listing(member, kind="template"))) == {"Baseline", "Copy of client work"}
        assert set(by_title(await listing(member, semester="Spring 2025"))) == {"Beta Foods (Sprout)"}
        assert set(by_title(await listing(member, industry="healthcare"))) == {"Acme Corp (Rocket)"}
        assert (await listing(member, limit=1, offset=1))["total"] == 5

        # --- Downloads and the audit log ----------------------------------------
        async def audits():
            return [dict(r) for r in await (await db.execute(
                "SELECT user_id, action, resource_id, details FROM audit_log WHERE action='resource_file_download' ORDER BY id")).fetchall()]

        file_id = lambda item, name: next(f["id"] for f in item["files"] if f["filename"] == name)  # noqa: E731
        open_file = file_id(acme, "Final Deliverable.pptx")
        link = await api.download_file(acme["id"], open_file, member)
        assert link["filename"] == "Final Deliverable.pptx"
        assert await audits() == [], "a file open to every member needs no audit row"
        served = await api.local_file(link["url"].split("token=")[1], member)
        assert Path(served.path).read_bytes().startswith(b"PK")
        await denied(api.local_file("not-a-token", member))

        beta_file = file_id(seen["team"]["Beta Foods (Sprout)"], "Beta Final.pptx")
        await denied(api.download_file(beta["id"], beta_file, member))
        assert await audits() == [], "a refused download is not a download"
        team_link = await api.download_file(beta["id"], beta_file, team)
        await denied(api.local_file(team_link["url"].split("token=")[1], member))
        assert Path((await api.local_file(team_link["url"].split("token=")[1], team)).path).is_file()
        sow = file_id(seen["admin"]["Acme Corp (Rocket)"], "Acme SOW.pdf")
        await denied(api.download_file(acme["id"], sow, member))
        await denied(api.download_file(acme["id"], sow, team))
        await api.download_file(acme["id"], sow, admin)
        await denied(api.download_file(acme["id"], beta_file, admin))  # a file of another resource
        assert [(a["user_id"], a["resource_id"]) for a in await audits()] == [(2, str(beta["id"])), (3, str(acme["id"]))]
        assert "Beta" not in json.dumps(await audits())

        # --- Search --------------------------------------------------------------
        async def found(user, q):
            return set(by_title(await listing(user, q=q)))

        assert await found(member, "giraffe") == {"Acme Corp (Rocket)"}, "text inside a pptx"
        assert await found(member, "Giraffe market") == {"Acme Corp (Rocket)"}
        assert await found(member, "sprout") == {"Beta Foods (Sprout)"}, "metadata, name_only"
        assert await found(member, "zebra") == set(), "text of files the member may not open"
        assert await found(team, "zebra") == {"Beta Foods (Sprout)"}
        assert await found(member, "quokka") == set() and await found(team, "quokka vault") == {"Gamma Bank (Vault)"}
        assert await found(admin, "lemur") == {"Orphan Inc (Zed)"}
        assert await found(member, "wombat") == {"Interview Guidelines"}
        assert await found(member, "hidden-fee-text") == set() and await found(admin, "99999") == set(), "statements of work are not indexed"
        assert await found(admin, "12345") == set() and await found(admin, "pat@acme-example.test") == set(), "revenue and contacts are not indexed"
        assert await found(member, "revised") == {"Acme Corp (Rocket)", "Beta Foods (Sprout)"}, "every row's description was revised; only the visible ones match"
        assert await found(member, "giraffe zebra") == set(), "every word must match"
        assert await found(member, "market*()\"") == {"Acme Corp (Rocket)"}, "query syntax is neutralised"
        assert await found(member, "   ") == set(by_title(await listing(member)))

        # --- Slack / AI tool -----------------------------------------------------
        slack_member = await run_tool("search_resources", {"query": "healthcare market sizing"}, member)
        assert [r["title"] for r in slack_member["resources"]] == ["Acme Corp (Rocket)"], slack_member
        encoded = json.dumps(slack_member)
        assert "revenue" not in encoded and "pat@acme" not in encoded and "12345" not in encoded
        assert slack_member["resources"][0]["files"] == ["Final Deliverable.pptx"]
        assert (await run_tool("search_resources", {"query": "vault"}, member))["resources"] == []
        assert (await run_tool("search_resources", {"query": "vault"}, team))["resources"][0]["title"] == "Gamma Bank (Vault)"
        slack_beta = (await run_tool("search_resources", {"query": "sprout"}, member))["resources"][0]
        assert slack_beta["files"] == [] and "files_for_team" in slack_beta
        slack_admin = (await run_tool("search_resources", {"query": "rocket"}, admin))["resources"][0]
        assert slack_admin["revenue"] == 12345.0 and slack_admin["contacts"][0]["email"] == "pat@acme-example.test"
        await db.execute("INSERT INTO app_access(app, restricted) VALUES ('resources', 1)")
        await db.commit()
        assert "error" in await run_tool("search_resources", {"query": "rocket"}, member), "an app restricted away from the member"
        await db.execute("DELETE FROM app_access WHERE app = 'resources'")
        await db.commit()

        # --- Rematching after a member joins; deletion ----------------------------
        await denied(get_current_admin(member), 403)
        await db.execute("INSERT INTO users(id,email,name,role,is_active) VALUES (6,'ghost@yale.edu','Ghost Name','standard',1)")
        await db.commit()
        await db.execute("BEGIN IMMEDIATE")
        assert (await resources.match_team(db))["matched"] == 1
        await db.commit()
        assert (await api.rematch_team(admin)) == {"matched": 0, "unmatched": 1}
        await denied(api.get_resource(gamma_id, {"id": 6, "role": "standard"}))

        stored = len([p for p in (tmp / "files").rglob("*") if p.is_file()])
        assert stored
        assert await api.delete_resource(gamma_id, admin) == {"ok": True}
        await denied(api.delete_resource(gamma_id, admin))
        assert await count(db, "resource_files") == 8 and await count(db, "workspace_documents") == 8
        assert await found(admin, "quokka") == set()
        assert len([p for p in (tmp / "files").rglob("*") if p.is_file()]) == stored - 1
        delete_audit = await (await db.execute("SELECT details FROM audit_log WHERE action='resource_delete'")).fetchone()
        assert json.loads(delete_audit["details"]) == {"files": 1}

        # --- Storage mode --------------------------------------------------------
        assert document_storage.local_enabled()
        os.environ["APP_ENV"] = "production"
        assert not document_storage.local_enabled()
        try:
            document_storage.storage_target()
            raise AssertionError("production without a bucket must not fall back to local files")
        except RuntimeError:
            pass
        os.environ.pop("APP_ENV")
        await db.close()


if __name__ == "__main__":
    asyncio.run(run())
    print("resources: ok")
