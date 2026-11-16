"""Import the club's OneDrive export (a zip) into Resources.

Driven by backend/scripts/import_resources.py (admin-only, run on the server
or locally). The zip is read member by member and never extracted into the
repo; each file goes through a temp file (for text extraction) and then
app.services.document_storage, the register's storage path.

Re-running updates what is there and never duplicates: a resource is keyed by
its table row (or folder / file path), a file by its path and CRC.

Data rules (docs/product/RESOURCES-PLAN.md): the club's own "Public Disclosure?"
column decides visibility; anything not clearly "yes" is private. Nothing read
from the zip - text, revenue, contacts, file names beyond folder and client
names - is logged.
"""
from __future__ import annotations

import io
import itertools
import json
import re
import secrets
import shutil
import tempfile
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from app.database import get_db
from app.services import document_storage, resource_text, resources
from app.services.transaction_audit import audit

PROJECTS = "Past Project Database/"
TOC_MEMBER = PROJECTS + "YUCG Project Database Table of Contents.xlsx"
SECTIONS = {
    "Slide Templates": "template",
    "Interview Resources": "interview",
    "Survey Resources": "survey",
    "YUCG Analytics Resources": "analytics",
}
REDACTED_SAMPLES = PROJECTS + "Public Disclosure Deliverables (Redacted)/"
_SEMESTER_FOLDER = re.compile(r"^(\d{4}) (Spring|Summer|Fall)$")
_FOLDER_NAME = re.compile(r"^(.*?)\s*\(([^()]*)\)\s*$")
_SOW = re.compile(r"(?<![a-z])sow(?![a-z])|statement of work", re.I)
_PLACEHOLDERS = {"", "na", "n a", "tbd", "none", "no sow"}


# --- The table of contents ---------------------------------------------------

def _clean(value: Any) -> str | None:
    text = " ".join(str(value).split()) if value is not None else ""
    return None if resources.name_key(text) in _PLACEHOLDERS else text


def match_key(value: str | None) -> str:
    """A client, code or folder name as a comparison key."""
    return re.sub(r"[^a-z0-9]+", "", resources.name_key(value))


def map_disclosure(raw: Any) -> str:
    """The club's "Public Disclosure?" answer as a visibility. Only a clear yes
    opens anything; a caveat ("Need to Confirm", "Caution") steps down one
    level, and "No", "TBD" and blank are private."""
    text = " ".join(str(raw or "").lower().split())
    plain = re.sub(r"\([^)]*\)", " ", text)
    if not plain.startswith("yes"):
        return "private"
    caveat = "confirm" in text or "caution" in text
    if "name only" in plain:
        return "private" if caveat else "name_only"
    if "deliverable" in plain:
        return "name_only" if caveat else "public"
    return "private"


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"\d[\d,]*(?:\.\d+)?", str(value or ""))
    return float(match.group().replace(",", "")) if match else None


def _names(value: Any) -> list[str]:
    seen, names = set(), []
    for part in re.split(r"[;\n]", str(value or "")):
        name = " ".join(part.split())
        key = resources.name_key(name)
        if key and key not in _PLACEHOLDERS and key not in seen:
            seen.add(key)
            names.append(name)
    return names


def _contacts(names: Any, emails: Any) -> list[dict]:
    people = _names(names)
    addresses = [e.strip() for e in re.split(r"[;,\n]", str(emails or "")) if e.strip()]
    return [{"name": n or "", "email": e or ""} for n, e in itertools.zip_longest(people, addresses)]


@dataclass
class TocRow:
    semester: str
    client: str | None
    code_name: str | None
    fields: dict[str, Any] = field(default_factory=dict)
    folder: str | None = None
    files: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.semester.lower()}|{match_key(self.code_name) or match_key(self.client)}"


def read_toc(data: bytes) -> list[TocRow]:
    """The table's rows, the sheets merged by (semester, code name or client):
    the "Database" sheet wins, the "Info" sheet fills what it leaves blank
    (revenue lives only there)."""
    import openpyxl

    workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    sheets = sorted(workbook.worksheets, key=lambda ws: "database" not in ws.title.lower())
    merged: dict[str, TocRow] = {}
    for sheet in sheets:
        rows = sheet.iter_rows(values_only=True)
        header = [str(h or "").strip() for h in next(rows, ())]
        for values in rows:
            record = dict(zip(header, values))
            semester, client, code = _clean(record.get("Semester")), _clean(record.get("Client")), _clean(record.get("Code Name"))
            if not semester or not (client or code):
                continue
            row = TocRow(semester, client, code, record)
            known = merged.get(row.key)
            if known is None:
                merged[row.key] = row
                continue
            for column, value in record.items():
                if known.fields.get(column) in (None, "") and value not in (None, ""):
                    known.fields[column] = value
            known.client = known.client or client
    return list(merged.values())


# --- The zip -----------------------------------------------------------------

@dataclass
class PlanFile:
    member: str
    name: str
    admin_only: bool


@dataclass
class Plan:
    source_key: str
    kind: str
    title: str
    files: list[PlanFile]
    client: str | None = None
    semester: str | None = None
    industry: str | None = None
    areas: str | None = None
    description: str | None = None
    code_name: str | None = None
    team_size: int | None = None
    disclosure: str = "public"
    status: str | None = None
    revenue: float | None = None
    contacts: list[dict] = field(default_factory=list)
    team: list[tuple[str, str]] = field(default_factory=list)
    alt_key: str | None = None
    folder: str | None = None


def _plan_file(member: str, force_admin_only: bool = False) -> PlanFile:
    name = member.rsplit("/", 1)[-1]
    return PlanFile(member, name, force_admin_only or bool(_SOW.search(name)))


def _project_plan(row: TocRow, files: list[str], folder: str | None) -> Plan:
    f = row.fields
    client = row.client
    title = f"{client} ({row.code_name})" if client and row.code_name else (client or row.code_name or "")
    team = [(role, name) for role, column in zip(resources.ROLES, ("Engagement Director(s)", "Project Manager(s)", "Associate(s)"))
            for name in _names(f.get(column))]
    size = _number(f.get("Team Size"))
    return Plan(
        source_key="row:" + row.key, kind="past_project", title=title, client=client, semester=row.semester,
        industry=_clean(f.get("Industry")), areas=_clean(f.get("Type/Areas")), description=_clean(f.get("Brief Project Description")),
        code_name=row.code_name, team_size=int(size) if size is not None else None,
        disclosure=map_disclosure(f.get("Public Disclosure?")), status=_clean(f.get("Completed?")),
        revenue=_number(f.get("Revenue")), contacts=_contacts(f.get("Points of Contact"), f.get("POC Emails")),
        team=team, files=[_plan_file(m) for m in files], alt_key=f"folder:{row.semester}/{folder}" if folder else None, folder=folder,
    )


def _title_of(name: str) -> str:
    return re.sub(r"\s+", " ", Path(name).stem.replace("_", " ")).strip()


def build_plan(archive: zipfile.ZipFile) -> dict[str, Any]:
    """Read the table and the zip's names and decide every resource. Reads the
    table (50 KB) and the member list only."""
    members = [i.filename for i in archive.infolist() if not i.filename.endswith("/")]
    rows = read_toc(archive.read(TOC_MEMBER)) if TOC_MEMBER in members else []

    folders: dict[tuple[str, str], list[str]] = {}
    other: list[str] = []
    for member in members:
        if member == TOC_MEMBER:
            continue  # carries revenue and contacts; it is the table, not a file to publish
        if member.startswith(PROJECTS):
            parts = member[len(PROJECTS):].split("/")
            if len(parts) >= 3 and (m := _SEMESTER_FOLDER.match(parts[0])):
                folders.setdefault((f"{m.group(2)} {m.group(1)}", parts[1]), []).append(member)
                continue
        other.append(member)

    # Match each folder to a row of the same semester: code name first (it
    # survives a misspelt client), then a client name that is unique there.
    by_semester: dict[str, list[TocRow]] = {}
    for row in rows:
        by_semester.setdefault(row.semester.lower(), []).append(row)
    unmatched_folders: list[str] = []
    for (semester, folder), files in sorted(folders.items()):
        candidates = [r for r in by_semester.get(semester.lower(), []) if r.folder is None]
        parsed = _FOLDER_NAME.match(folder)
        client_part, code_part = (parsed.group(1), parsed.group(2)) if parsed else (folder, "")
        found = [r for r in candidates if code_part and match_key(code_part) == match_key(r.code_name)]
        if not found:
            found = [r for r in candidates if match_key(client_part) == match_key(r.client)]
        if len(found) == 1:
            found[0].folder = folder
            found[0].files = files
        else:
            unmatched_folders.append(f"{semester}/{folder}")

    plans = [_project_plan(r, r.files, r.folder) for r in rows]
    for (semester, folder), files in sorted(folders.items()):
        if f"{semester}/{folder}" in unmatched_folders:
            parsed = _FOLDER_NAME.match(folder)
            plans.append(Plan(
                source_key=f"folder:{semester}/{folder}", kind="past_project", title=folder, semester=semester,
                client=parsed.group(1) if parsed else folder, code_name=parsed.group(2) if parsed else None,
                disclosure="private", files=[_plan_file(m) for m in files], folder=folder,
            ))

    held_back: list[str] = []
    for member in other:
        top = member.split("/", 1)[0]
        if member.startswith(REDACTED_SAMPLES):
            kind, note = "other", "Redacted sample deliverable, cleared for public disclosure"
        elif top in SECTIONS:
            kind = SECTIONS[top]
            subfolder = member.split("/")[1:-1]
            note = " / ".join(subfolder) or top
        else:
            held_back.append(member)
            continue
        plans.append(Plan(
            source_key=f"file:{member}", kind=kind, title=_title_of(member), description=note,
            disclosure="public", files=[_plan_file(member)],
        ))
    return {
        "plans": plans, "rows": rows, "unmatched_folders": unmatched_folders,
        "rows_without_folder": [f"{r.semester}: {r.client or r.code_name}" for r in rows if r.folder is None],
        "held_back": held_back,
    }


def summarize(plan: dict[str, Any]) -> dict[str, Any]:
    plans = plan["plans"]
    return {
        "table_rows": len(plan["rows"]),
        "matched": sum(1 for r in plan["rows"] if r.folder),
        "folders_without_row": plan["unmatched_folders"],
        "rows_without_folder": plan["rows_without_folder"],
        "held_back_files": len(plan["held_back"]),
        "resources": len(plans),
        "files": sum(len(p.files) for p in plans),
        "admin_only_files": sum(1 for p in plans for f in p.files if f.admin_only),
        "visibility": {f"{kind}/{disclosure}": n for (kind, disclosure), n in sorted(Counter((p.kind, p.disclosure) for p in plans).items())},
    }


# --- Writing -----------------------------------------------------------------

async def _upsert_resource(db, plan: Plan, now: int) -> int:
    await db.execute("BEGIN IMMEDIATE")
    row = await (await db.execute("SELECT id FROM resources WHERE source_key = ?", (plan.source_key,))).fetchone()
    if row is None and plan.alt_key:
        row = await (await db.execute("SELECT id FROM resources WHERE source_key = ?", (plan.alt_key,))).fetchone()
    values = (plan.kind, plan.title, plan.client, plan.semester, plan.industry, plan.areas, plan.description, plan.code_name,
              plan.team_size, plan.disclosure, plan.status)
    if row:
        rid = row["id"]
        await db.execute(
            """UPDATE resources SET kind=?, title=?, client=?, semester=?, industry=?, areas=?, description=?, code_name=?,
               team_size=?, disclosure=?, status=?, source_key=?, updated_at=? WHERE id=?""",
            (*values, plan.source_key, now, rid),
        )
    else:
        cursor = await db.execute(
            """INSERT INTO resources(kind, title, client, semester, industry, areas, description, code_name,
               team_size, disclosure, status, source_key, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (*values, plan.source_key, now, now),
        )
        rid = cursor.lastrowid
    if plan.kind == "past_project":
        await db.execute("INSERT OR REPLACE INTO resource_sensitive(resource_id, revenue, contacts) VALUES (?,?,?)",
                         (rid, plan.revenue, json.dumps(plan.contacts)))
    await db.execute("DELETE FROM resource_team WHERE resource_id = ?", (rid,))
    for role, name in plan.team:
        await db.execute("INSERT OR IGNORE INTO resource_team(resource_id, role, name, name_key) VALUES (?,?,?,?)",
                         (rid, role, name, resources.name_key(name)))
    await db.commit()
    return rid


async def _import_file(db, archive: zipfile.ZipFile, rid: int, pf: PlanFile, admin_id: int, target: str, now: int) -> str:
    """'added', 'updated' or 'unchanged'."""
    info = archive.getinfo(pf.member)
    existing = await (await db.execute(
        "SELECT document_id, source_crc, admin_only FROM resource_files WHERE resource_id = ? AND source_key = ?", (rid, pf.member),
    )).fetchone()
    if existing and existing["source_crc"] == info.CRC:
        if bool(existing["admin_only"]) != pf.admin_only:
            await db.execute("UPDATE resource_files SET admin_only = ? WHERE resource_id = ? AND source_key = ?",
                             (int(pf.admin_only), rid, pf.member))
            await db.commit()
        return "unchanged"
    content_type = document_storage.guess_content_type(pf.name)
    with tempfile.TemporaryDirectory(prefix="yucg-import-") as tmp:
        path = Path(tmp) / "file"
        with archive.open(info) as source, open(path, "wb") as out:
            shutil.copyfileobj(source, out, 1024 * 1024)
        # A statement of work states the fee, so its text never reaches search.
        text = "" if pf.admin_only else resource_text.extract_text(path, pf.name)
        document_id = existing["document_id"] if existing else None
        key = f"documents/resource/{secrets.token_urlsafe(24)}"
        version = await document_storage.store_file(target, key, path, content_type)
    await db.execute("BEGIN IMMEDIATE")
    if document_id is None:
        cursor = await db.execute(
            "INSERT INTO workspace_documents(title, owner_user_id, visibility, created_at) VALUES (?,?, 'private', ?)",
            (pf.name, admin_id, now))
        document_id = cursor.lastrowid
    cursor = await db.execute(
        """INSERT INTO workspace_document_versions(document_id, uploaded_by, object_key, filename, byte_size, content_type,
           state, s3_version_id, storage_bucket, created_at) VALUES (?,?,?,?,?,?, 'ready', ?, ?, ?)""",
        (document_id, admin_id, key, pf.name, info.file_size, content_type, version, target, now))
    await db.execute("UPDATE workspace_documents SET current_version = ?, revision = revision + 1 WHERE id = ?",
                     (cursor.lastrowid, document_id))
    await db.execute(
        """INSERT INTO resource_files(resource_id, document_id, source_key, source_crc, admin_only) VALUES (?,?,?,?,?)
           ON CONFLICT(resource_id, source_key) DO UPDATE SET source_crc = excluded.source_crc, admin_only = excluded.admin_only""",
        (rid, document_id, pf.member, info.CRC, int(pf.admin_only)))
    await db.execute("INSERT OR REPLACE INTO resource_file_text(document_id, resource_id, text) VALUES (?,?,?)", (document_id, rid, text))
    await db.commit()
    return "updated" if existing else "added"


async def _admin(db, email: str | None) -> int:
    if email:
        row = await (await db.execute("SELECT id, role FROM users WHERE lower(email) = ? AND is_active = 1", (email.lower(),))).fetchone()
        if not row or row["role"] != "admin":
            raise SystemExit("--as must name an active admin account")
    else:
        row = await (await db.execute("SELECT id FROM users WHERE role = 'admin' AND is_active = 1 ORDER BY id LIMIT 1")).fetchone()
        if not row:
            raise SystemExit("There is no admin account to own the imported files")
    return row["id"]


async def team_preview(plan: dict[str, Any]) -> dict[str, int] | None:
    """How many team names would match a member account today (dry run). None
    when the database cannot be read."""
    try:
        db = await get_db()
    except Exception:
        return None
    try:
        users = Counter(resources.name_key(r["name"]) for r in await (await db.execute(
            "SELECT name FROM users WHERE is_active = 1 AND role != 'system'")).fetchall())
    except Exception:
        return None
    finally:
        await db.close()
    names = {resources.name_key(n) for p in plan["plans"] for _, n in p.team}
    return {"names": len(names), "would_match": sum(1 for n in names if users.get(n) == 1)}


async def run_import(
    zip_path: str | Path, *, dry_run: bool = False, admin_email: str | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Import (or, with ``dry_run``, only plan) the export. Returns the summary."""
    with zipfile.ZipFile(zip_path) as archive:
        plan = build_plan(archive)
        summary = summarize(plan)
        if dry_run:
            summary["team_names"] = await team_preview(plan)
            return summary
        from app.database import init_db

        await init_db()
        target = document_storage.storage_target()
        db = await get_db()
        try:
            admin_id = await _admin(db, admin_email)
            now = resources.now()
            counts: Counter = Counter()
            ids = []
            for item in plan["plans"]:
                rid = await _upsert_resource(db, item, now)
                ids.append(rid)
                for pf in item.files:
                    counts[await _import_file(db, archive, rid, pf, admin_id, target, now)] += 1
                await db.execute("BEGIN IMMEDIATE")
                await resources.reindex(db, rid)
                await db.commit()
            await db.execute("BEGIN IMMEDIATE")
            summary["team"] = await resources.match_team(db, ids)
            await audit(db, admin_id, "resources_import", "resource", 0, {"resources": len(ids), **counts})
            await db.commit()
            summary["file_results"] = dict(counts)
            log(f"Imported {len(ids)} resources; files: {dict(counts)}")
        finally:
            await db.close()
    return summary
