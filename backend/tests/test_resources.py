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
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from app.auth_deps import get_current_admin  # noqa: E402
from app.database import get_db, init_db  # noqa: E402
from app.jwt_utils import create_token  # noqa: E402
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
# The table's "Project File Link" hyperlinks, in the relative shapes SharePoint
# writes when the workbook sits in the same library. Jet's is only on the Info sheet.
LINKS = {
    "rocket": "../../../../:f:/r/sites/ClubSite/Shared%20Documents/Fall%202025/Rocket?csf=1&web=1&e=Tok3n",
    "sprout": "../../../../:p:/r/sites/ClubSite/Shared%20Documents/Spring%202025/Sprout.pptx?d=w123&csf=1&web=1&e=x",
    "vault": "../../../../:f:/r/sites/ClubSite/Shared%20Documents/Spring%202024/Vault?csf=1&web=1",
}
INFO_LINKS = {"jet": "../../../../:f:/r/sites/ClubSite/Shared%20Documents/Fall%202024/Jet?csf=1&web=1"}
BASE = "https://club-example.sharepoint.com"


def table(description_suffix=""):
    workbook = openpyxl.Workbook()
    main = workbook.active
    main.title = "Detailed Project Database"
    main.append([*HEADER, "Project File Link"])
    for row in ROWS:
        row = list(row)
        row[6] += description_suffix
        code = row[7].lower()
        main.append([*row, "Click Here" if code in LINKS else None])
        if code in LINKS:
            main.cell(main.max_row, len(HEADER) + 1).hyperlink = LINKS[code]
    info = workbook.create_sheet("Detailed Project Info")
    info.append(["Semester", "Client", "Code Name", "Revenue", "Project File Link"])
    for row in ROWS:
        code = row[7].lower()
        if code in REVENUE or code in INFO_LINKS:
            info.append([row[0], row[1], row[7], REVENUE.get(code), "Click Here" if code in INFO_LINKS else None])
            if code in INFO_LINKS:
                info.cell(info.max_row, 5).hyperlink = INFO_LINKS[code]
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


def facet(listed, name):
    return {entry["value"]: entry["count"] for entry in listed["facets"][name]}


def client_for(user_id, role):
    client = TestClient(main.app, raise_server_exceptions=False)
    client.cookies.set("yucg_session", create_token(user_id, f"{user_id}@yale.edu", None, None, role))
    return client


def check_link_paths():
    os.environ.pop("SHAREPOINT_BASE_URL", None)
    path = resources.sharepoint_path
    expected = "/:f:/r/sites/ClubSite/Shared%20Documents/Fall%202025/Rocket"
    assert path(LINKS["rocket"]) == expected, "the sharing prefix is kept, the query dropped"
    assert path("../../:p:/r/sites/A/B/deck.pptx?d=w1") == "/:p:/r/sites/A/B/deck.pptx"
    assert path("/sites/A/B") == "/sites/A/B"
    absolute = f"{BASE}/:f:/r/sites/ClubSite/Shared%20Documents/Fall%202025/Rocket?web=1"
    assert path(absolute) is None, "without the setting an absolute link has no host to match"
    os.environ["SHAREPOINT_BASE_URL"] = BASE
    assert path(absolute) == expected
    for bad in (None, "", "Click Here", f"{BASE}/:f:/s/ClubSite/EaBc", "../other/sites/A", "../../:f:/r/sites/A/../../admin",
                "/sites/A/has space", "javascript:/sites/A", "http://club-example.sharepoint.com/sites/A",
                "https://other-tenant.sharepoint.com/:f:/r/sites/ClubSite/x"):
        assert path(bad) is None, bad

    resolve = resources.resolve_link
    rocket_url = "https://club-example.sharepoint.com/:f:/r/sites/ClubSite/Shared%20Documents/Fall%202025/Rocket"
    assert resolve(expected) == {"url": rocket_url, "provider": "sharepoint"}
    os.environ["SHAREPOINT_BASE_URL"] = BASE + "/sites/ClubSite/"
    assert resolve(expected)["url"] == rocket_url, "the sharing prefix goes straight after the host, not after a site path"
    assert resolve("https://evil.test/x") is None and resolve("http://drive.google.com/x") is None

    clean = resources.clean_link
    assert clean(f"{BASE}/sites/ClubSite/x") == "/sites/ClubSite/x", "stored from the host on, like the table's"
    assert clean("https://Club-Example.SharePoint.com/:f:/s/ClubSite/EaBc?e=1") == "/:f:/s/ClubSite/EaBc?e=1"
    for drive in ("https://drive.google.com/drive/folders/abc", "https://docs.google.com/presentation/d/x/edit"):
        assert clean(drive) == drive
    rejected = ("https://other-tenant.sharepoint.com/sites/A", "http://club-example.sharepoint.com/sites/A", "https://example.com/sites/A",
                "https://sharepoint.com/sites/A", "https://club-example.sharepoint.com.evil.test/sites/A",
                "https://club-example.sharepoint.com@evil.test/", "javascript:alert(1)", "https://club-example.sharepoint.com:8443/sites/A",
                "https://drive.google.com.evil.test/x", "https://club-example.sharepoint.com/a b", "", None)
    for bad in rejected:
        try:
            clean(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad!r}")

    os.environ.pop("SHAREPOINT_BASE_URL")
    assert resolve(expected) is None, "no setting: no link, never a broken one"
    assert resolve("https://drive.google.com/x") == {"url": "https://drive.google.com/x", "provider": "google_drive"}
    try:
        clean(f"{BASE}/sites/ClubSite/x")
        raise AssertionError("a SharePoint link needs the setting's host to compare with")
    except ValueError:
        pass
    for bad_base in ("http://club-example.sharepoint.com", "https://evil.test", "not a url"):
        os.environ["SHAREPOINT_BASE_URL"] = bad_base
        assert resolve(expected) is None, bad_base
    os.environ.pop("SHAREPOINT_BASE_URL")


async def run():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        os.environ["DATABASE_URL"] = f"sqlite:///{tmp}/test.db"
        os.environ["DOCUMENTS_LOCAL_DIR"] = str(tmp / "files")
        archive = tmp / "export.zip"
        make_zip(archive)

        # A dry run before the database exists writes and stores nothing.
        check_link_paths()
        dry = await resource_import.run_import(archive, dry_run=True)
        assert dry["table_rows"] == 5 and dry["matched"] == 4
        assert dry["links"] == 4, dry  # three on the Database sheet, one only on the Info sheet
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
        assert summary["links_filled"] == 4, summary
        # Team: "Team Person" matched on three resources; the ghost and the ambiguous twins grant nothing.
        assert summary["team"] == {"matched": 3, "unmatched": 2}, summary["team"]
        counts = {t: await count(db, t) for t in ("resources", "resource_files", "workspace_documents", "workspace_document_versions", "resource_team")}
        assert counts["resources"] == 9 and counts["resource_files"] == 9, counts

        # Idempotent: the same zip changes nothing; a changed row and file update in place.
        again = await resource_import.run_import(archive, log=lambda _line: None)
        assert again["file_results"] == {"unchanged": 9}, again
        assert again["links_filled"] == 0, again
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
        assert "Financial Services" not in facet(listed, "industries") and facet(listed, "industries")["Healthcare"] == 1
        assert listed["facets"]["semesters"][0] == {"value": "Fall 2025", "count": 1}
        assert facet(listed, "kinds") == {"past_project": 2, "template": 2, "interview": 1}
        assert facet(listed, "disclosures") == {"public": 1, "name_only": 1}, "a private project is not even counted"
        assert facet(await listing(team), "disclosures") == {"public": 1, "name_only": 1, "private": 1}
        assert facet(await listing(admin), "kinds")["past_project"] == 6
        # A facet is counted with the other filters applied, not its own.
        templates = await listing(member, kind="template")
        assert facet(templates, "kinds") == facet(listed, "kinds") and facet(templates, "semesters") == {}
        assert facet(await listing(member, q="sprout"), "kinds") == {"past_project": 1}
        assert set(by_title(await listing(member, disclosure="name_only"))) == {"Beta Foods (Sprout)"}
        assert facet(await listing(member, disclosure="name_only"), "semesters") == {"Spring 2025": 1}
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

        # --- Links ---------------------------------------------------------------
        rocket_path = "/:f:/r/sites/ClubSite/Shared%20Documents/Fall%202025/Rocket"
        seen = {name: by_title(await listing(u)) for name, u in (("member", member), ("team", team), ("admin", admin))}
        assert all(item["link"] is None for who in seen.values() for item in who.values()), "no SHAREPOINT_BASE_URL: no button"
        assert seen["admin"]["Acme Corp (Rocket)"]["stored_link"] == rocket_path
        assert seen["admin"]["Epsilon Air (Jet)"]["stored_link"].endswith("/Fall%202024/Jet")
        assert "stored_link" not in json.dumps(seen["member"]) and "ClubSite" not in json.dumps(seen["member"])
        assert (await listing(admin))["sharepoint_configured"] is False and "sharepoint_configured" not in await listing(member)
        os.environ["SHAREPOINT_BASE_URL"] = BASE
        seen = {name: by_title(await listing(u)) for name, u in (("member", member), ("team", team))}
        assert seen["member"]["Acme Corp (Rocket)"]["link"] == {"url": BASE + rocket_path, "provider": "sharepoint"}
        assert (await listing(admin))["sharepoint_configured"] is True
        assert seen["member"]["Beta Foods (Sprout)"]["link"] is None, "name only: the link follows the files"
        assert seen["team"]["Beta Foods (Sprout)"]["link"]["url"].endswith("/Spring%202025/Sprout.pptx")
        assert seen["member"]["Baseline"]["link"] is None

        # Over HTTP: only an admin edits links or adds a link-only resource, and the address is checked on the server.
        acme_id = acme["id"]
        as_member, as_admin = client_for(1, "standard"), client_for(3, "admin")
        drive = "https://drive.google.com/drive/folders/club-folder"
        assert as_member.put(f"/api/resources/{acme_id}/link", json={"url": drive}).status_code == 403
        assert as_member.post("/api/resources", json={"kind": "template", "title": "T", "url": drive}).status_code == 403
        for bad in ("https://evil.test/x", "http://drive.google.com/x", "javascript:alert(1)", "https://other-tenant.sharepoint.com/sites/ClubSite/x"):
            response = as_admin.put(f"/api/resources/{acme_id}/link", json={"url": bad})
            assert response.status_code == 422, (bad, response.status_code)
        assert as_admin.put("/api/resources/99999/link", json={"url": drive}).status_code == 404
        response = as_admin.put(f"/api/resources/{acme_id}/link", json={"url": drive})
        assert response.status_code == 200 and response.json()["link"] == {"url": drive, "provider": "google_drive"}
        assert (await api.get_resource(acme_id, member))["link"]["url"] == drive
        assert as_member.get(f"/api/resources/{acme_id}").json()["link"]["url"] == drive

        # Link-only resources follow the same rules as any other.
        private = as_admin.post("/api/resources", json={
            "kind": "past_project", "title": "Linked private project", "semester": "fall 2026", "industry": "Energy; Utilities",
            "disclosure": "private", "url": BASE + "/sites/ClubSite/Shared%20Documents/Linked"})
        assert private.status_code == 200, private.text
        assert private.json()["semester"] == "Fall 2026" and private.json()["files"] == [] and private.json()["industries"] == ["Energy", "Utilities"]
        assert private.json()["stored_link"] == "/sites/ClubSite/Shared%20Documents/Linked"
        assert private.json()["link"] == {"url": BASE + "/sites/ClubSite/Shared%20Documents/Linked", "provider": "sharepoint"}
        private_id = private.json()["id"]
        guide = as_admin.post("/api/resources", json={"kind": "survey", "title": "Linked survey guide", "url": drive + "2"})
        assert guide.status_code == 200, guide.text
        named = as_admin.post("/api/resources", json={"kind": "past_project", "title": "Linked named project", "disclosure": "name_only", "url": drive + "3"})
        assert named.status_code == 200, named.text
        assert as_admin.post("/api/resources", json={"kind": "template", "title": "x", "semester": "Autumn", "url": drive}).status_code == 422
        assert as_admin.post("/api/resources", json={"kind": "template", "title": "x", "url": "https://evil.test"}).status_code == 422
        assert as_admin.post("/api/resources", json={"kind": "nonsense", "title": "x", "url": drive}).status_code == 422
        assert as_admin.post("/api/resources", json={"kind": "template", "title": "   ", "url": drive}).status_code == 422
        mine = by_title(await listing(member))
        assert "Linked private project" not in mine and mine["Linked survey guide"]["link"]["url"] == drive + "2"
        assert mine["Linked named project"]["link"] is None and mine["Linked named project"]["files_open"] is False
        assert as_member.get(f"/api/resources/{private_id}").status_code == 404
        assert facet(await listing(member), "semesters").get("Fall 2026") is None, "facets count only what the viewer sees"
        assert facet(await listing(admin), "semesters")["Fall 2026"] == 1
        assert await found(admin, "linked private") == {"Linked private project"}
        assert as_admin.put(f"/api/resources/{private_id}/link", json={"url": None}).json()["link"] is None
        assert as_admin.put(f"/api/resources/{private_id}/link", json={"url": "  "}).json()["stored_link"] is None

        # Re-importing keeps the link an admin set and fills only empty ones (Gamma was deleted above, so it comes back).
        refill = await resource_import.run_import(archive, log=lambda _line: None)
        assert refill["links_filled"] == 1, refill
        assert (await api.get_resource(acme_id, admin))["stored_link"] == drive
        os.environ.pop("SHAREPOINT_BASE_URL")
        link_audit = [json.loads(r["details"]) for r in await (await db.execute(
            "SELECT details FROM audit_log WHERE action IN ('resource_link_set', 'resource_create') ORDER BY id")).fetchall()]
        assert link_audit[0] == {"link": "google_drive"} and "drive.google" not in json.dumps(link_audit)

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
