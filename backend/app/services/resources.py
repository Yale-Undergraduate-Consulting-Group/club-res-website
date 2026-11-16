"""Club Resources: past projects, templates and guides, and who may see them.

The access rules live here and nowhere else; the page API, the Slack tool and
the import all go through them (docs/product/RESOURCES-PLAN.md):

* guides and templates (any kind but ``past_project``): every member;
* past project, ``public``: every member, files included;
* past project, ``name_only``: every member sees the metadata; files only the
  project's team and admins;
* past project, ``private``: only the team and admins see it at all;
* revenue, client contacts and files marked ``admin_only`` (statements of
  work, which state the fee): admins only;
* the resource's SharePoint or Google Drive link: whoever may open its files.

The team is the member accounts a resource's names were matched to when it was
imported (or re-matched by an admin); a later name change grants nothing.
"""
from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from typing import Any
from urllib.parse import urlsplit

from app.services import document_storage
from app.services.transaction_audit import audit

KINDS = ("past_project", "template", "interview", "survey", "analytics", "other")
DISCLOSURES = ("public", "name_only", "private")
ROLES = ("Engagement director", "Project manager", "Associate")
_SEASON_RANK = {"spring": 1, "summer": 2, "fall": 3}
_TOKEN = re.compile(r"\w+", re.UNICODE)
_TOKENIZER = "porter unicode61 remove_diacritics 2"
_SEMESTER = re.compile(r"(?i)^\s*(spring|summer|fall)\s+(\d{4})\s*$")
_SHAREPOINT_HOST = re.compile(r"[a-z0-9-]+(\.[a-z0-9-]+)*\.sharepoint\.com")
_GOOGLE_DRIVE_HOSTS = ("drive.google.com", "docs.google.com")
# A SharePoint link as stored from the table: SharePoint's sharing prefix (":f:/r"
# for a folder, ":p:/r" and the like for a file), which goes straight after the
# host, then the site path. Older links have no prefix.
_SHAREPOINT_PATH = re.compile(r"(?:/:[a-z]:/r)?/sites/[^?#]+")
_UNSAFE = re.compile(r"[\s\\<>\"]")
#: Object-key prefixes of stored file versions. The import stores server-side
#: under IMPORT_KEY_PREFIX, and those versions are not charged to anyone's
#: storage quota (workspace.storage_usage); a member's page upload is stored
#: under UPLOAD_KEY_PREFIX and is charged to the uploader like a register upload.
IMPORT_KEY_PREFIX = "documents/resource/"
UPLOAD_KEY_PREFIX = "documents/resource-upload/"


async def create_tables(db) -> None:
    """Migration resources:1. Additive; nothing here is read by the previous release."""
    kinds = ", ".join(f"'{k}'" for k in KINDS)
    disclosures = ", ".join(f"'{d}'" for d in DISCLOSURES)
    statements = (
        f"""CREATE TABLE resources (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              kind TEXT NOT NULL CHECK (kind IN ({kinds})),
              title TEXT NOT NULL, client TEXT, semester TEXT, industry TEXT, areas TEXT,
              description TEXT, code_name TEXT, team_size INTEGER,
              disclosure TEXT NOT NULL DEFAULT 'private' CHECK (disclosure IN ({disclosures})),
              status TEXT, project_id INTEGER REFERENCES projects(id),
              source_key TEXT UNIQUE,
              created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)""",
        # Revenue and client contacts: read only by the admin branch of the serializer.
        """CREATE TABLE resource_sensitive (
              resource_id INTEGER PRIMARY KEY REFERENCES resources(id) ON DELETE CASCADE,
              revenue REAL, contacts TEXT NOT NULL DEFAULT '[]')""",
        # Each file is a register document; this table says which resource it belongs to.
        """CREATE TABLE resource_files (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              resource_id INTEGER NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
              document_id INTEGER NOT NULL UNIQUE REFERENCES workspace_documents(id),
              source_key TEXT NOT NULL, source_crc INTEGER,
              admin_only INTEGER NOT NULL DEFAULT 0,
              UNIQUE (resource_id, source_key))""",
        """CREATE TABLE resource_file_text (
              document_id INTEGER PRIMARY KEY,
              resource_id INTEGER NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
              text TEXT NOT NULL)""",
        """CREATE TABLE resource_team (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              resource_id INTEGER NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
              role TEXT NOT NULL, name TEXT NOT NULL, name_key TEXT NOT NULL,
              user_id INTEGER REFERENCES users(id),
              UNIQUE (resource_id, role, name_key))""",
        "CREATE INDEX resource_team_user ON resource_team(user_id, resource_id)",
        "CREATE INDEX resource_files_resource ON resource_files(resource_id)",
        # Metadata and file text are separate indexes so file text only ever
        # matches for a member who may open the files.
        f"""CREATE VIRTUAL TABLE resource_fts USING fts5(
              title, client, industry, areas, description, code_name,
              content='', contentless_delete=1, tokenize='{_TOKENIZER}')""",
        f"""CREATE VIRTUAL TABLE resource_body_fts USING fts5(
              body, content='', contentless_delete=1, tokenize='{_TOKENIZER}')""",
    )
    for statement in statements:
        await db.execute(statement)


async def add_link_column(db) -> None:
    """Migration resources:2. A nullable column; the previous release never reads it.
    Holds either a SharePoint path from the host on ("/:f:/r/sites/...", put
    after the SHAREPOINT_BASE_URL host when read) or a Google Drive address."""
    await db.execute("ALTER TABLE resources ADD COLUMN link TEXT")


# --- Links -------------------------------------------------------------------

def sharepoint_path(target: str | None) -> str | None:
    """How a table hyperlink is stored: its path from the host on
    ("/:f:/r/sites/..."), from the relative form SharePoint writes
    ("../../../../:f:/r/sites/...") or an absolute link on the configured host;
    None for anything else. ponytail: the query (view flags, a sharing token)
    is dropped; the path alone opens the folder or file for whoever SharePoint
    already lets in."""
    try:
        parts = urlsplit(str(target or "").strip())
        port = parts.port
    except ValueError:
        return None
    if (parts.scheme or parts.netloc) and (
        parts.scheme != "https" or parts.username or parts.password or port not in (None, 443)
        or parts.hostname != sharepoint_host()
    ):
        return None
    path = "/" + re.sub(r"^(?:\.\./)*/?", "", parts.path)
    if not _SHAREPOINT_PATH.fullmatch(path) or _UNSAFE.search(path) or any(part in (".", "..") for part in path.split("/")):
        return None
    return path


def sharepoint_base() -> str | None:
    """https://<tenant>.sharepoint.com from SHAREPOINT_BASE_URL (its host
    only); None when it is unset or not a SharePoint address, so no link is
    built against it."""
    raw = (os.getenv("SHAREPOINT_BASE_URL") or "").strip()
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme != "https" or parts.username or parts.password or port not in (None, 443):
        return None
    if not _SHAREPOINT_HOST.fullmatch(parts.hostname or ""):
        return None
    return f"https://{parts.hostname}"


def sharepoint_host() -> str | None:
    base = sharepoint_base()
    return base[len("https://"):] if base else None


def clean_link(value: str | None) -> str:
    """An admin's link as stored, else ValueError. A SharePoint link must be
    https on exactly the configured SHAREPOINT_BASE_URL host and is stored from
    the host on (path and query), like the table's; a Google Drive or Docs
    link is stored whole."""
    text = str(value or "").strip()
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        parts, port = None, None
    host = sharepoint_host()
    message = (f"Use an https link on {host} or Google Drive" if host
               else "Use an https Google Drive link (SharePoint links need SHAREPOINT_BASE_URL)")
    if (
        parts is None or len(text) > 2000 or _UNSAFE.search(text)
        or parts.scheme != "https" or parts.username or parts.password or port not in (None, 443)
    ):
        raise ValueError(message)
    if parts.hostname in _GOOGLE_DRIVE_HOSTS:
        return parts.geturl()
    if host and parts.hostname == host:
        return (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    raise ValueError(message)


def link_provider(stored: str | None) -> str | None:
    """"sharepoint" or "google_drive" for a stored link (for the audit log)."""
    if not stored:
        return None
    return "sharepoint" if stored.startswith("/") else "google_drive"


def resolve_link(stored: str | None) -> dict | None:
    """{url, provider} to open, or None. A stored SharePoint path is put after
    the SHAREPOINT_BASE_URL host; without that setting there is no link rather
    than a broken one. Anything else must still be an https Google Drive link."""
    if not stored:
        return None
    if stored.startswith("/"):
        base = sharepoint_base()
        return {"url": base + stored, "provider": "sharepoint"} if base else None
    try:
        parts = urlsplit(stored)
    except ValueError:
        return None
    if parts.scheme == "https" and parts.hostname in _GOOGLE_DRIVE_HOSTS:
        return {"url": stored, "provider": "google_drive"}
    return None


def clean_semester(value: str | None) -> str | None:
    """"fall 2025" -> "Fall 2025"; blank -> None; anything else ValueError."""
    text = str(value or "").strip()
    if not text:
        return None
    match = _SEMESTER.match(text)
    if not match:
        raise ValueError("Semester looks like Fall 2025")
    return f"{match.group(1).capitalize()} {match.group(2)}"


# --- Names -------------------------------------------------------------------

def name_key(name: str | None) -> str:
    """A person's name as a comparison key: no accents, case, punctuation or extra spaces."""
    text = unicodedata.normalize("NFKD", str(name or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", text.lower()).split())


async def match_team(db, resource_ids: list[int] | None = None) -> dict[str, int]:
    """Point each unmatched team name at the one active member whose name has
    the same key. A name that fits no member, or several, stays text and grants
    nothing. Returns counts; never names."""
    users: dict[str, list[int]] = {}
    for row in await (await db.execute("SELECT id, name FROM users WHERE is_active = 1 AND role != 'system'")).fetchall():
        if key := name_key(row["name"]):
            users.setdefault(key, []).append(row["id"])
    sql = "SELECT id, name_key FROM resource_team WHERE user_id IS NULL"
    params: list[Any] = []
    if resource_ids is not None:
        sql += f" AND resource_id IN ({','.join('?' * len(resource_ids)) or 'NULL'})"
        params = list(resource_ids)
    matched = 0
    pending = await (await db.execute(sql, params)).fetchall()
    for row in pending:
        ids = users.get(row["name_key"], [])
        if len(ids) == 1:
            await db.execute("UPDATE resource_team SET user_id = ? WHERE id = ?", (ids[0], row["id"]))
            matched += 1
    return {"matched": matched, "unmatched": len(pending) - matched}


# --- Access ------------------------------------------------------------------

def is_admin(viewer: dict) -> bool:
    return viewer.get("role") == "admin"


def _team_sql(viewer_id: int) -> tuple[str, list]:
    return (
        """(EXISTS (SELECT 1 FROM resource_team t WHERE t.resource_id = r.id AND t.user_id = ?)
            OR (r.project_id IS NOT NULL AND EXISTS
                (SELECT 1 FROM user_project_assignments a WHERE a.project_id = r.project_id AND a.user_id = ?)))""",
        [viewer_id, viewer_id],
    )


def visible_sql(viewer: dict) -> tuple[str, list]:
    """WHERE condition (alias ``r``): resources the viewer may see at all."""
    if is_admin(viewer):
        return "1 = 1", []
    team, params = _team_sql(viewer["id"])
    return f"(r.kind != 'past_project' OR r.disclosure != 'private' OR {team})", params


def files_open_sql(viewer: dict) -> tuple[str, list]:
    """SQL expression (alias ``r``): the viewer may open the resource's files."""
    if is_admin(viewer):
        return "1", []
    team, params = _team_sql(viewer["id"])
    return f"(r.kind != 'past_project' OR r.disclosure = 'public' OR {team})", params


async def managed_ids(db, viewer: dict, resource_ids: list[int]) -> set[int]:
    """Of ``resource_ids`` (already visible to the viewer), those whose files
    the viewer may add to, replace, restore and delete: any resource for an
    admin; for a past project, a member of its team (the team that sees its files)."""
    if not resource_ids:
        return set()
    if is_admin(viewer):
        return set(resource_ids)
    team, params = _team_sql(viewer["id"])
    marks = ",".join("?" * len(resource_ids))
    rows = await (await db.execute(
        f"SELECT r.id FROM resources r WHERE r.kind = 'past_project' AND r.id IN ({marks}) AND {team}",
        [*resource_ids, *params],
    )).fetchall()
    return {row["id"] for row in rows}


def open_to_all_members(resource: dict, admin_only_file: bool = False) -> bool:
    """True when every member may download the file: it is the open case the
    audit log does not need to record."""
    return not admin_only_file and (resource["kind"] != "past_project" or resource["disclosure"] == "public")


# --- Reading -----------------------------------------------------------------

def _industries(value: str | None) -> list[str]:
    return [part.strip() for part in (value or "").split(";") if part.strip()]


def _semester_order(semester: str | None) -> tuple[int, int]:
    match = re.match(r"(?i)\s*(spring|summer|fall)\s+(\d{4})", semester or "")
    return (int(match.group(2)), _SEASON_RANK[match.group(1).lower()]) if match else (0, 0)


def _matches(query: str | None) -> list[str]:
    return ['"%s"*' % token for token in _TOKEN.findall((query or "").lower())[:8]]


async def _search_scores(db, terms: list[str]) -> list[tuple[dict[int, float], dict[int, float]]]:
    """Per query word: score of every resource whose metadata, and whose file
    text, contains a word starting with it (lower bm25 is a better match)."""
    scores = []
    for term in terms:
        meta = {
            row["rowid"]: row["score"]
            for row in await (await db.execute(
                "SELECT rowid, bm25(resource_fts, 10, 6, 3, 3, 2, 5) AS score FROM resource_fts WHERE resource_fts MATCH ?", (term,)
            )).fetchall()
        }
        body = {
            row["rowid"]: row["score"]
            for row in await (await db.execute(
                "SELECT rowid, bm25(resource_body_fts) AS score FROM resource_body_fts WHERE resource_body_fts MATCH ?", (term,)
            )).fetchall()
        }
        scores.append((meta, body))
    return scores


async def _visible_rows(db, viewer: dict) -> list[dict]:
    visible, visible_params = visible_sql(viewer)
    opened, opened_params = files_open_sql(viewer)
    rows = await (await db.execute(
        f"SELECT r.*, {opened} AS files_open FROM resources r WHERE {visible}", [*opened_params, *visible_params],
    )).fetchall()
    return [dict(row) for row in rows]


def _facet_values(row: dict) -> dict[str, list[str]]:
    return {
        "kind": [row["kind"]],
        "semester": [row["semester"]] if row["semester"] else [],
        "industry": _industries(row["industry"]),
        # Disclosure is a past project's; guides and templates are open to every member.
        "disclosure": [row["disclosure"]] if row["kind"] == "past_project" else [],
    }


def _facet_key(name: str, value: str) -> str:
    return value.lower() if name == "industry" else value


async def search(
    db, viewer: dict, *, q: str = "", kind: str = "", semester: str = "", industry: str = "", disclosure: str = "",
    limit: int = 60, offset: int = 0, match_all: bool = True,
) -> tuple[list[dict], int, dict[str, list[dict]]]:
    """(resource rows for the page, total matches, facets), every one visible
    to ``viewer``. File text matches only for resources whose files the viewer
    may open. Every word must match unless ``match_all`` is False (then the
    rows matching the most words come first). Each facet lists its values with
    how many matching resources have each, counted with the search and every
    other filter applied (not its own, so its other values stay on offer).
    ponytail: the library is a few hundred rows, so filtering, counting and
    ordering happen here rather than in a query per combination."""
    rows = await _visible_rows(db, viewer)
    terms = _matches(q)
    if terms:
        per_term = await _search_scores(db, terms)
        scored = []
        for row in rows:
            hits = []
            for meta, body in per_term:
                best = [meta[row["id"]]] if row["id"] in meta else []
                if row["files_open"] and row["id"] in body:
                    best.append(body[row["id"]])
                if best:
                    hits.append(min(best))
            if hits and (len(hits) == len(terms) or not match_all):
                scored.append((-len(hits), sum(hits), row["title"].lower(), row))
        rows = [item[3] for item in sorted(scored, key=lambda item: item[:3])]
    else:
        rows = sorted(rows, key=lambda r: (tuple(-v for v in _semester_order(r["semester"])), r["kind"] != "past_project", r["title"].lower()))

    chosen = {"kind": kind, "semester": semester, "industry": industry, "disclosure": disclosure}
    chosen = {name: _facet_key(name, value) for name, value in chosen.items() if value}
    values = [_facet_values(row) for row in rows]
    passes = [
        {name: value in {_facet_key(name, v) for v in row_values[name]} for name, value in chosen.items()}
        for row_values in values
    ]
    counts: dict[str, dict[str, int]] = {name: {} for name in ("kind", "semester", "industry", "disclosure")}
    for row_values, passed in zip(values, passes):
        for name, found in row_values.items():
            if all(ok for other, ok in passed.items() if other != name):
                for value in found:
                    counts[name][value] = counts[name].get(value, 0) + 1
    rank = {
        "kind": lambda v: (KINDS.index(v) if v in KINDS else len(KINDS), v),
        "semester": lambda v: tuple(-n for n in _semester_order(v)) + (v,),
        "industry": str.lower,
        "disclosure": lambda v: (DISCLOSURES.index(v) if v in DISCLOSURES else len(DISCLOSURES), v),
    }
    facets = {
        plural: [{"value": v, "count": n} for v, n in sorted(counts[name].items(), key=lambda item: rank[name](item[0]))]
        for name, plural in (("kind", "kinds"), ("semester", "semesters"), ("industry", "industries"), ("disclosure", "disclosures"))
    }
    rows = [row for row, passed in zip(rows, passes) if all(passed.values())]
    limit = max(1, min(limit, 500))
    return rows[max(0, offset):max(0, offset) + limit], len(rows), facets


async def get_visible(db, viewer: dict, resource_id: int) -> dict:
    """The resource row if the viewer may see it, else None (never says why)."""
    visible, visible_params = visible_sql(viewer)
    opened, opened_params = files_open_sql(viewer)
    row = await (await db.execute(
        f"SELECT r.*, {opened} AS files_open FROM resources r WHERE r.id = ? AND {visible}",
        [*opened_params, resource_id, *visible_params],
    )).fetchone()
    return dict(row) if row else None


async def present(db, viewer: dict, rows: list[dict]) -> list[dict]:
    """The API shape of ``rows`` (already visible to the viewer). Revenue and
    contacts are added for admins only; the keys do not exist for anyone else."""
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    marks = ",".join("?" * len(ids))
    admin = is_admin(viewer)
    files: dict[int, list[dict]] = {i: [] for i in ids}
    for row in await (await db.execute(
        f"""SELECT f.resource_id, f.document_id, f.admin_only, d.current_version AS version_id, v.filename, v.byte_size, v.content_type
            FROM resource_files f JOIN workspace_documents d ON d.id = f.document_id
            JOIN workspace_document_versions v ON v.id = d.current_version
            WHERE f.resource_id IN ({marks}) ORDER BY v.filename COLLATE NOCASE""", ids,
    )).fetchall():
        files[row["resource_id"]].append(dict(row))
    team: dict[int, list[dict]] = {i: [] for i in ids}
    for row in await (await db.execute(
        f"SELECT resource_id, role, name, user_id FROM resource_team WHERE resource_id IN ({marks}) ORDER BY id", ids,
    )).fetchall():
        team[row["resource_id"]].append({"name": row["name"], "role": row["role"], "member": row["user_id"] is not None})
    sensitive: dict[int, Any] = {}
    if admin:
        for row in await (await db.execute(
            f"SELECT resource_id, revenue, contacts FROM resource_sensitive WHERE resource_id IN ({marks})", ids,
        )).fetchall():
            sensitive[row["resource_id"]] = row
    managed = await managed_ids(db, viewer, ids)
    items = []
    for r in rows:
        # A statement of work (admin_only) is not mentioned to anyone else, so it is not counted either.
        every = files[r["id"]] if admin else [f for f in files[r["id"]] if not f["admin_only"]]
        shown = every if r["files_open"] else []
        hidden = len(every) - len(shown)
        item = {
            "id": r["id"], "kind": r["kind"], "title": r["title"],
            # Only resources the viewer may see reach here, and the rules show the client name on every one of them.
            "client": r["client"], "client_hidden": False,
            "semester": r["semester"], "industry": r["industry"], "industries": _industries(r["industry"]),
            "areas": r["areas"], "description": r["description"], "code_name": r["code_name"],
            "team_size": r["team_size"], "disclosure": r["disclosure"], "status": r["status"],
            "files_open": bool(r["files_open"]), "can_manage": r["id"] in managed,
            "files": [
                {"id": f["document_id"], "version_id": f["version_id"], "filename": f["filename"], "byte_size": f["byte_size"],
                 "content_type": f["content_type"], "admin_only": bool(f["admin_only"])}
                for f in shown
            ],
            "files_hidden_count": hidden,
            "team": team[r["id"]], "created_at": r["created_at"],
            # The folder or file behind the link is the deliverable, so it follows the files' rule.
            "link": resolve_link(r["link"]) if r["files_open"] else None,
        }
        if admin:
            # What is stored, so an admin can see a SharePoint path waiting for SHAREPOINT_BASE_URL.
            item["stored_link"] = r["link"]
            row = sensitive.get(r["id"])
            item["revenue"] = row["revenue"] if row else None
            item["contacts"] = json.loads(row["contacts"]) if row else []
        items.append(item)
    return items


async def file_for_download(db, viewer: dict, resource_id: int, document_id: int) -> tuple[dict, dict]:
    """(resource, file) when the viewer may open this file, else None. The same
    answer for a missing file and a forbidden one."""
    resource = await get_visible(db, viewer, resource_id)
    if not resource or not resource["files_open"]:
        return None
    row = await (await db.execute(
        "SELECT document_id, admin_only FROM resource_files WHERE resource_id = ? AND document_id = ?",
        (resource_id, document_id),
    )).fetchone()
    if not row or (row["admin_only"] and not is_admin(viewer)):
        return None
    return resource, dict(row)


async def audit_download(db, viewer: dict, resource: dict, file: dict) -> None:
    """Record a download of a file not open to every member (caller commits)."""
    if open_to_all_members(resource, bool(file["admin_only"])):
        return
    await audit(db, viewer["id"], "resource_file_download", "resource", resource["id"],
                {"document_id": file["document_id"], "disclosure": resource["disclosure"]})


# --- Writing -----------------------------------------------------------------

async def reindex(db, resource_id: int) -> None:
    """Rebuild one resource's two search rows from its columns and file text."""
    row = await (await db.execute("SELECT * FROM resources WHERE id = ?", (resource_id,))).fetchone()
    if not row:
        return
    await db.execute(
        "INSERT OR REPLACE INTO resource_fts(rowid, title, client, industry, areas, description, code_name) VALUES (?,?,?,?,?,?,?)",
        (resource_id, row["title"], row["client"], row["industry"], row["areas"], row["description"], row["code_name"]),
    )
    parts = []
    for file in await (await db.execute(
        """SELECT v.filename, f.admin_only, t.text FROM resource_files f
           JOIN workspace_documents d ON d.id = f.document_id
           JOIN workspace_document_versions v ON v.id = d.current_version
           LEFT JOIN resource_file_text t ON t.document_id = f.document_id
           WHERE f.resource_id = ? ORDER BY f.id""", (resource_id,),
    )).fetchall():
        if not file["admin_only"]:
            parts.append(file["filename"])
            parts.append(file["text"] or "")
    await db.execute("INSERT OR REPLACE INTO resource_body_fts(rowid, body) VALUES (?, ?)", (resource_id, "\n".join(parts)))


async def delete_stored_versions(db, documents: list[int]) -> None:
    """Delete every stored version object of these register documents (a
    failure raises and leaves the rows, so the delete can be retried)."""
    marks = ",".join("?" * len(documents)) or "NULL"
    versions = await (await db.execute(
        f"SELECT object_key, storage_bucket, s3_version_id FROM workspace_document_versions WHERE document_id IN ({marks})", documents,
    )).fetchall()
    for version in versions:
        if version["storage_bucket"]:
            await document_storage.delete_version(version["storage_bucket"], version["object_key"], version["s3_version_id"])


async def delete_resource(db, admin: dict, resource_id: int) -> bool:
    """Remove a resource: its stored file versions first (a failure leaves the
    rows so the delete can be retried), then its rows, atomically."""
    from app.services import resource_ask

    resource = await (await db.execute("SELECT id, title FROM resources WHERE id = ?", (resource_id,))).fetchone()
    if not resource:
        return False
    documents = [r["document_id"] for r in await (await db.execute(
        "SELECT document_id FROM resource_files WHERE resource_id = ?", (resource_id,))).fetchall()]
    marks = ",".join("?" * len(documents)) or "NULL"
    await delete_stored_versions(db, documents)
    await db.execute("BEGIN IMMEDIATE")
    for document_id in documents:
        await resource_ask.drop_text(db, document_id)
    await db.execute("DELETE FROM resource_fts WHERE rowid = ?", (resource_id,))
    await db.execute("DELETE FROM resource_body_fts WHERE rowid = ?", (resource_id,))
    await db.execute("DELETE FROM resources WHERE id = ?", (resource_id,))
    await db.execute(f"DELETE FROM workspace_document_shares WHERE document_id IN ({marks})", documents)
    await db.execute(f"DELETE FROM workspace_document_versions WHERE document_id IN ({marks})", documents)
    await db.execute(f"DELETE FROM workspace_documents WHERE id IN ({marks})", documents)
    await audit(db, admin["id"], "resource_delete", "resource", resource_id, {"files": len(documents)})
    await db.commit()
    return True


async def create_link_resource(db, admin: dict, *, kind: str, title: str, semester: str | None, industry: str | None,
                               disclosure: str, url: str) -> int:
    """Add a resource that is only a link (no stored file). Arguments are
    already validated; the access rules are the same as for any resource."""
    industry = "; ".join(_industries(industry)) or None
    stamp = now()
    await db.execute("BEGIN IMMEDIATE")
    cursor = await db.execute(
        """INSERT INTO resources(kind, title, semester, industry, disclosure, link, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (kind, title, semester, industry, disclosure, url, stamp, stamp),
    )
    rid = cursor.lastrowid
    await reindex(db, rid)
    await audit(db, admin["id"], "resource_create", "resource", rid, {"kind": kind, "link": link_provider(url)})
    await db.commit()
    return rid


async def set_link(db, admin: dict, resource_id: int, url: str | None) -> bool:
    """Set (or, with None, remove) a resource's link. False when there is no such resource."""
    await db.execute("BEGIN IMMEDIATE")
    cursor = await db.execute("UPDATE resources SET link = ?, updated_at = ? WHERE id = ?", (url, now(), resource_id))
    if not cursor.rowcount:
        await db.rollback()
        return False
    await audit(db, admin["id"], "resource_link_set", "resource", resource_id,
                {"link": link_provider(url)})
    await db.commit()
    return True


def now() -> int:
    return int(time.time())
