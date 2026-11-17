"""The alumni portal: news posts, events, an opt-in directory and the board
inbox (docs/product/ALUMNI-PORTAL.md). Alumni get no club data here.

Two doors to the same content:

* ``router`` at ``/api/alumni``, behind the alumni cookie (alumni.get_current_alumni,
  which re-checks the account on every request). Every signed-in alumnus reads
  published posts, events and the listed directory, keeps their own profile and
  writes to the board. Alumni board members, marked by a club admin, also write
  posts and events and work the inbox.
* ``admin_router`` at ``/api/admin/alumni``, behind a club admin's member session:
  the same board work for club admins, who are members, not alumni.

A member session never reaches the first door and an alumni session never the
second. An actor is ``{"alumni_id", "user_id", "board"}`` with exactly one id set.
"""
from __future__ import annotations

import functools
import time
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, available_timezones

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.auth_deps import get_current_admin
from app.database import get_db
from app.routers import alumni
from app.routers.alumni import get_current_alumni
from app.services import alumni_covers, document_storage
from app.services.safe_html import sanitize_rich_text
from app.services.transaction_audit import audit

# The portal adds its routes to the sign-in module's routers: one mount per
# prefix (app.modules), and the same session checks guard both.
router = alumni.router
admin_router = alumni.admin_router

ALUMNI_BASE = "/api/alumni"
ADMIN_BASE = "/api/admin/alumni"
#: The byline of a post whose author account was deleted.
BOARD_BYLINE = "YUCG Alumni Board"
#: Per actor, per rolling hour. ponytail: fixed limits; a busy board can ask for more.
CONTACT_PER_HOUR = 5
WRITES_PER_HOUR = 60
#: Sanitised HTML larger than this is refused rather than cut.
RICH_TEXT_MAX = 60_000
#: An eligible, not revoked account (alias ``a``): the same rule as alumni.allowed.
ACTIVE_ACCOUNT = """a.revoked_at IS NULL AND (
    EXISTS (SELECT 1 FROM alumni_allowlist l WHERE l.email = a.email)
    OR EXISTS (SELECT 1 FROM alumni_domains d WHERE d.domain = substr(a.email, instr(a.email, '@') + 1)))"""


async def create_tables(db) -> None:
    """Migration core:7. Additive: new tables and two nullable columns the
    previous release never reads."""
    for statement in (
        "ALTER TABLE alumni_accounts ADD COLUMN board_since INTEGER",
        "ALTER TABLE alumni_accounts ADD COLUMN board_set_by INTEGER",
        """CREATE TABLE alumni_post_images (
               id INTEGER PRIMARY KEY AUTOINCREMENT, object_key TEXT UNIQUE NOT NULL,
               storage_bucket TEXT NOT NULL, s3_version_id TEXT, byte_size INTEGER NOT NULL,
               declared_type TEXT NOT NULL, verified_type TEXT, state TEXT NOT NULL DEFAULT 'pending',
               uploaded_by_alumni INTEGER, uploaded_by_user INTEGER,
               created_at INTEGER NOT NULL, presign_expires_at INTEGER)""",
        """CREATE TABLE alumni_posts (
               id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, body_html TEXT NOT NULL,
               status TEXT NOT NULL DEFAULT 'draft', pinned INTEGER NOT NULL DEFAULT 0,
               cover_image_id INTEGER UNIQUE, author_alumni_id INTEGER, author_user_id INTEGER,
               created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, published_at INTEGER)""",
        "CREATE INDEX alumni_posts_feed ON alumni_posts(status, pinned, published_at)",
        """CREATE TABLE alumni_events (
               id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, starts_at INTEGER NOT NULL,
               timezone TEXT NOT NULL, place TEXT, online_url TEXT, description_html TEXT NOT NULL DEFAULT '',
               author_alumni_id INTEGER, author_user_id INTEGER,
               created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)""",
        "CREATE INDEX alumni_events_start ON alumni_events(starts_at)",
        """CREATE TABLE alumni_profiles (
               account_id INTEGER PRIMARY KEY, display_name TEXT NOT NULL, class_year INTEGER,
               company TEXT, role TEXT, city TEXT, linkedin_url TEXT, note TEXT,
               coffee_chats INTEGER NOT NULL DEFAULT 0, listed INTEGER NOT NULL DEFAULT 0,
               show_email INTEGER NOT NULL DEFAULT 0, updated_at INTEGER NOT NULL)""",
        """CREATE TABLE alumni_board_messages (
               id INTEGER PRIMARY KEY AUTOINCREMENT, sender_account_id INTEGER, subject TEXT NOT NULL,
               body TEXT NOT NULL, created_at INTEGER NOT NULL, read_at INTEGER, archived_at INTEGER)""",
        "CREATE INDEX alumni_board_messages_inbox ON alumni_board_messages(archived_at, id)",
        """CREATE TABLE alumni_rate_events (actor TEXT NOT NULL, kind TEXT NOT NULL, at INTEGER NOT NULL)""",
        "CREATE INDEX alumni_rate_events_actor ON alumni_rate_events(actor, kind, at)",
    ):
        await db.execute(statement)


# --- Who is asking ---------------------------------------------------------------

async def alumni_reader(alumnus: dict = Depends(get_current_alumni)) -> dict:
    return {"alumni_id": alumnus["id"], "user_id": None, "board": alumnus["board"],
            "email": alumnus["email"], "name": alumnus["name"]}


async def alumni_board(actor: dict = Depends(alumni_reader)) -> dict:
    if not actor["board"]:
        raise HTTPException(403, "Only the alumni board can do this")
    return actor


async def admin_actor(admin: dict = Depends(get_current_admin)) -> dict:
    return {"alumni_id": None, "user_id": admin["id"], "board": True}


def _actor_key(actor: dict) -> str:
    return f"a:{actor['alumni_id']}" if actor["alumni_id"] else f"m:{actor['user_id']}"


async def _audit(db, actor: dict, action: str, resource_type: str, resource_id, details: dict | None = None) -> None:
    """audit_log names members by user_id; an alumni actor goes in the details."""
    details = dict(details or {})
    if actor["alumni_id"]:
        details["alumni_account_id"] = actor["alumni_id"]
    await audit(db, actor["user_id"], action, resource_type, resource_id, details)


async def _rate(db, actor: dict, kind: str, limit: int) -> None:
    """Count one ``kind`` action against the actor's hourly allowance. Runs in
    the caller's write transaction, so concurrent requests cannot both pass."""
    now = int(time.time())
    await db.execute("DELETE FROM alumni_rate_events WHERE at < ?", (now - 3600,))
    key = _actor_key(actor)
    used = await (await db.execute(
        "SELECT COUNT(*) AS n FROM alumni_rate_events WHERE actor = ? AND kind = ?", (key, kind))).fetchone()
    if used["n"] >= limit:
        raise HTTPException(429, "Too many requests in the last hour. Please try again later.")
    await db.execute("INSERT INTO alumni_rate_events (actor, kind, at) VALUES (?, ?, ?)", (key, kind, now))


def _rich_text(value: str) -> str:
    html = sanitize_rich_text(value)
    if len(html) > RICH_TEXT_MAX:
        raise HTTPException(413, "The text is too long")
    return html


def _text(value: str | None) -> str | None:
    return (value or "").strip() or None


def _https_url(value: str | None, label: str, hosts: tuple[str, ...] = ()) -> str | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
    except ValueError:
        parts, host = None, ""
    if parts is None or (parts.scheme.lower() != "https" or not host or any(c.isspace() for c in value)
            or (hosts and not any(host == h or host.endswith("." + h) for h in hosts))):
        raise HTTPException(422, f"{label} must be an https:// link" + (f" on {hosts[0]}" if hosts else ""))
    return value


# --- Posts -------------------------------------------------------------------------

class PostIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body_html: str = Field("", max_length=200_000)
    status: Literal["draft", "published"] = "draft"
    pinned: bool = False
    cover_image_id: int | None = None


_POST_SELECT = """SELECT p.*, COALESCE(a.name, u.name) AS author_name FROM alumni_posts p
    LEFT JOIN alumni_accounts a ON a.id = p.author_alumni_id
    LEFT JOIN users u ON u.id = p.author_user_id"""
_POST_ORDER = "ORDER BY p.pinned DESC, COALESCE(p.published_at, p.updated_at) DESC, p.id DESC"


def _post(row, base: str) -> dict:
    return {
        "id": row["id"], "title": row["title"], "body_html": row["body_html"], "status": row["status"],
        "pinned": bool(row["pinned"]), "author": row["author_name"] or BOARD_BYLINE,
        "created_at": row["created_at"], "updated_at": row["updated_at"], "published_at": row["published_at"],
        "cover": ({"id": row["cover_image_id"], "url": alumni_covers.cover_url(base, row["cover_image_id"])}
                  if row["cover_image_id"] else None),
    }


async def _posts(db, base: str, *, drafts: bool, limit: int, offset: int = 0) -> tuple[list[dict], int]:
    where = "" if drafts else "WHERE p.status = 'published'"
    total = (await (await db.execute(f"SELECT COUNT(*) AS n FROM alumni_posts p {where}")).fetchone())["n"]
    rows = await (await db.execute(f"{_POST_SELECT} {where} {_POST_ORDER} LIMIT ? OFFSET ?", (limit, offset))).fetchall()
    return [_post(row, base) for row in rows], total


async def _visible_post(db, post_id: int, actor: dict):
    row = await (await db.execute(f"{_POST_SELECT} WHERE p.id = ?", (post_id,))).fetchone()
    if not row or (row["status"] != "published" and not actor["board"]):
        raise HTTPException(404, "Not found")
    return row


async def _cover_for(db, image_id: int | None, post_id: int | None) -> None:
    """A cover a post may use: a finished upload no other post uses."""
    if image_id is None:
        return
    image = await (await db.execute("SELECT state FROM alumni_post_images WHERE id = ?", (image_id,))).fetchone()
    if not image or image["state"] != "ready":
        raise HTTPException(409, "Upload the cover image again")
    other = await (await db.execute(
        "SELECT 1 FROM alumni_posts WHERE cover_image_id = ? AND id != ?", (image_id, post_id or 0))).fetchone()
    if other:
        raise HTTPException(409, "That image is another post's cover")


async def _save_post(actor: dict, base: str, payload: PostIn, post_id: int | None = None) -> dict:
    title = payload.title.strip()
    if not title:
        raise HTTPException(422, "A post needs a title")
    body = _rich_text(payload.body_html)
    if payload.status == "published" and not body:
        raise HTTPException(422, "Write the post before publishing it")
    now = int(time.time())
    dropped: list[dict] = []
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        await _rate(db, actor, "write", WRITES_PER_HOUR)
        existing = None
        if post_id is not None:
            existing = await (await db.execute("SELECT * FROM alumni_posts WHERE id = ?", (post_id,))).fetchone()
            if not existing:
                raise HTTPException(404, "Not found")
        await _cover_for(db, payload.cover_image_id, post_id)
        if payload.status == "published":
            published_at = existing["published_at"] if existing and existing["status"] == "published" else now
        else:
            published_at = None
        if existing is None:
            cursor = await db.execute(
                """INSERT INTO alumni_posts (title, body_html, status, pinned, cover_image_id, author_alumni_id,
                   author_user_id, created_at, updated_at, published_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (title, body, payload.status, int(payload.pinned), payload.cover_image_id, actor["alumni_id"],
                 actor["user_id"], now, now, published_at))
            post_id = cursor.lastrowid
            await _audit(db, actor, "alumni_post_create", "alumni_post", post_id,
                         {"status": payload.status, "pinned": payload.pinned})
        else:
            await db.execute(
                """UPDATE alumni_posts SET title = ?, body_html = ?, status = ?, pinned = ?, cover_image_id = ?,
                   updated_at = ?, published_at = ? WHERE id = ?""",
                (title, body, payload.status, int(payload.pinned), payload.cover_image_id, now, published_at, post_id))
            if existing["cover_image_id"] and existing["cover_image_id"] != payload.cover_image_id:
                dropped = await _drop_images(db, [existing["cover_image_id"]])
            await _audit(db, actor, "alumni_post_update", "alumni_post", post_id,
                         {"status": payload.status, "pinned": payload.pinned,
                          "was": {"status": existing["status"], "pinned": bool(existing["pinned"])}})
        await db.commit()
        row = await (await db.execute(f"{_POST_SELECT} WHERE p.id = ?", (post_id,))).fetchone()
    finally:
        await db.close()
    await alumni_covers.delete_objects(dropped)
    return _post(row, base)


async def _drop_images(db, image_ids: list[int]) -> list[dict]:
    """Delete these cover rows; returns them so their objects go after commit."""
    rows = []
    for image_id in image_ids:
        row = await (await db.execute("SELECT * FROM alumni_post_images WHERE id = ?", (image_id,))).fetchone()
        if row:
            rows.append(dict(row))
            await db.execute("DELETE FROM alumni_post_images WHERE id = ?", (image_id,))
    return rows


async def _delete_post(actor: dict, post_id: int) -> dict:
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        await _rate(db, actor, "write", WRITES_PER_HOUR)
        row = await (await db.execute("SELECT title, cover_image_id FROM alumni_posts WHERE id = ?", (post_id,))).fetchone()
        if not row:
            raise HTTPException(404, "Not found")
        await db.execute("DELETE FROM alumni_posts WHERE id = ?", (post_id,))
        dropped = await _drop_images(db, [row["cover_image_id"]] if row["cover_image_id"] else [])
        await _audit(db, actor, "alumni_post_delete", "alumni_post", post_id, {"title": row["title"]})
        await db.commit()
    finally:
        await db.close()
    await alumni_covers.delete_objects(dropped)
    return {"ok": True}


# --- Cover images ------------------------------------------------------------------

class CoverUpload(BaseModel):
    byte_size: int = Field(gt=0)
    content_type: str = Field(min_length=1, max_length=100)


async def _reserve_cover(actor: dict, base: str, payload: CoverUpload) -> dict:
    alumni_covers.check_request(payload.byte_size, payload.content_type)
    try:
        target = document_storage.storage_target()
    except RuntimeError:
        raise HTTPException(503, "Image storage is not configured") from None
    key = alumni_covers.new_key()
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        await _rate(db, actor, "write", WRITES_PER_HOUR)
        stale = await alumni_covers.stale_rows(db)
        await _drop_images(db, [row["id"] for row in stale])
        cursor = await db.execute(
            """INSERT INTO alumni_post_images (object_key, storage_bucket, byte_size, declared_type,
               uploaded_by_alumni, uploaded_by_user, created_at) VALUES (?,?,?,?,?,?,?)""",
            (key, target, payload.byte_size, payload.content_type, actor["alumni_id"], actor["user_id"], int(time.time())))
        image_id = cursor.lastrowid
        await _audit(db, actor, "alumni_cover_reserve", "alumni_cover", image_id,
                     {"byte_size": payload.byte_size, "content_type": payload.content_type,
                      "removed_unused": len(stale)})
        await db.commit()
    finally:
        await db.close()
    await alumni_covers.delete_objects(stale)
    try:
        if target == document_storage.LOCAL:
            url, headers = alumni_covers.upload_url(base, image_id), {"Content-Type": payload.content_type}
        else:
            url, headers = await alumni_covers.presign(target, key, payload.byte_size, payload.content_type)
    except Exception:
        # No URL reached the browser, so nothing can be stored under this key.
        db = await get_db()
        try:
            await db.execute("DELETE FROM alumni_post_images WHERE id = ? AND state = 'pending'", (image_id,))
            await db.commit()
        finally:
            await db.close()
        raise
    db = await get_db()
    try:
        await db.execute("UPDATE alumni_post_images SET presign_expires_at = ? WHERE id = ?",
                         (int(time.time()) + alumni_covers.UPLOAD_SECONDS, image_id))
        await db.commit()
    finally:
        await db.close()
    return {"image_id": image_id, "upload": {"url": url, "method": "PUT", "headers": headers}}


def _uploader_clause(actor: dict) -> tuple[str, int]:
    return ("uploaded_by_alumni = ?", actor["alumni_id"]) if actor["alumni_id"] else ("uploaded_by_user = ?", actor["user_id"])


async def _own_upload(db, actor: dict, image_id: int):
    clause, owner = _uploader_clause(actor)
    row = await (await db.execute(f"SELECT * FROM alumni_post_images WHERE id = ? AND {clause}", (image_id, owner))).fetchone()
    if not row:
        raise HTTPException(404, "Not found")
    return dict(row)


async def _receive_cover(actor: dict, token: str, request: Request) -> dict:
    image_id = document_storage.read_signed_token(token, alumni_covers.UPLOAD_PURPOSE)
    if image_id is None:
        raise HTTPException(404, "Not found")
    db = await get_db()
    try:
        row = await _own_upload(db, actor, image_id)
    finally:
        await db.close()
    if row["state"] != "pending" or row["storage_bucket"] != document_storage.LOCAL:
        raise HTTPException(404, "Not found")
    await document_storage.receive_local_put(row["object_key"], row["byte_size"], request)
    return {"ok": True}


async def _complete_cover(actor: dict, base: str, image_id: int) -> dict:
    db = await get_db()
    try:
        row = await _own_upload(db, actor, image_id)
    finally:
        await db.close()
    if row["state"] == "pending":
        verified, version = await alumni_covers.verify(row)
        db = await get_db()
        try:
            await db.execute("BEGIN IMMEDIATE")
            if verified is None or verified != row["declared_type"]:
                await db.execute("DELETE FROM alumni_post_images WHERE id = ? AND state = 'pending'", (image_id,))
                await _audit(db, actor, "alumni_cover_refuse", "alumni_cover", image_id,
                             {"declared": row["declared_type"], "found": verified})
            else:
                await db.execute(
                    "UPDATE alumni_post_images SET state = 'ready', verified_type = ?, s3_version_id = ? WHERE id = ? AND state = 'pending'",
                    (verified, None if version == document_storage.LOCAL else version, image_id))
                await _audit(db, actor, "alumni_cover_complete", "alumni_cover", image_id,
                             {"byte_size": row["byte_size"], "content_type": verified})
            await db.commit()
        finally:
            await db.close()
        if verified is None or verified != row["declared_type"]:
            await alumni_covers.delete_objects([{**row, "s3_version_id": None if version == document_storage.LOCAL else version}])
            raise HTTPException(415, "The file is not a PNG, JPEG or WebP image of the type it claims. It was deleted.")
    return {"id": image_id, "url": alumni_covers.cover_url(base, image_id)}


async def _cover_response(actor: dict, image_id: int, token: str):
    """A cover, to whoever may read its post right now. The link only proves it
    was handed out recently; the post and the session are checked again."""
    if document_storage.read_signed_token(token, alumni_covers.READ_PURPOSE) != image_id:
        raise HTTPException(404, "Not found")
    db = await get_db()
    try:
        row = await (await db.execute(
            """SELECT i.*, p.status AS post_status FROM alumni_post_images i
               LEFT JOIN alumni_posts p ON p.cover_image_id = i.id WHERE i.id = ? AND i.state = 'ready'""",
            (image_id,))).fetchone()
    finally:
        await db.close()
    if not row or (row["post_status"] != "published" and not actor["board"]):
        raise HTTPException(404, "Not found")
    return await alumni_covers.response(dict(row))


# --- Events ------------------------------------------------------------------------

class EventIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    #: Wall-clock time where the event happens, ``YYYY-MM-DDTHH:MM``.
    starts_local: str = Field(min_length=16, max_length=19)
    timezone: str = Field(min_length=1, max_length=64)
    place: str | None = Field(None, max_length=200)
    online_url: str | None = Field(None, max_length=500)
    description_html: str = Field("", max_length=200_000)


@functools.cache
def _zones() -> frozenset[str]:
    return frozenset(available_timezones())


def _zone(name: str) -> ZoneInfo:
    if name not in _zones():
        raise HTTPException(422, "Choose a time zone such as America/New_York")
    return ZoneInfo(name)


def _event(row) -> dict:
    zone = _zone(row["timezone"])
    return {
        "id": row["id"], "title": row["title"], "starts_at": row["starts_at"], "timezone": row["timezone"],
        "starts_local": datetime.fromtimestamp(row["starts_at"], zone).strftime("%Y-%m-%dT%H:%M"),
        "place": row["place"], "online_url": row["online_url"], "description_html": row["description_html"],
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


async def _events(db, *, upcoming_limit: int = 50, past_limit: int = 50) -> dict:
    now = int(time.time())
    upcoming = await (await db.execute(
        "SELECT * FROM alumni_events WHERE starts_at >= ? ORDER BY starts_at, id LIMIT ?", (now, upcoming_limit))).fetchall()
    past = await (await db.execute(
        "SELECT * FROM alumni_events WHERE starts_at < ? ORDER BY starts_at DESC, id DESC LIMIT ?", (now, past_limit))).fetchall()
    return {"upcoming": [_event(r) for r in upcoming], "past": [_event(r) for r in past]}


def _event_values(payload: EventIn) -> tuple:
    title = payload.title.strip()
    if not title:
        raise HTTPException(422, "An event needs a title")
    zone = _zone(payload.timezone.strip())
    try:
        local = datetime.fromisoformat(payload.starts_local)
    except ValueError:
        raise HTTPException(422, "Enter the date and time of the event") from None
    if local.tzinfo is not None or not 2000 <= local.year <= 2100:
        raise HTTPException(422, "Enter the date and time of the event")
    place = _text(payload.place)
    online_url = _https_url(payload.online_url, "The online link")
    if not place and not online_url:
        raise HTTPException(422, "Give a place, an online link, or both")
    starts_at = int(local.replace(tzinfo=zone).timestamp())
    return title, starts_at, zone.key, place, online_url, _rich_text(payload.description_html)


async def _save_event(actor: dict, payload: EventIn, event_id: int | None = None) -> dict:
    values = _event_values(payload)
    now = int(time.time())
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        await _rate(db, actor, "write", WRITES_PER_HOUR)
        if event_id is None:
            cursor = await db.execute(
                """INSERT INTO alumni_events (title, starts_at, timezone, place, online_url, description_html,
                   author_alumni_id, author_user_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (*values, actor["alumni_id"], actor["user_id"], now, now))
            event_id = cursor.lastrowid
            action = "alumni_event_create"
        else:
            cursor = await db.execute(
                """UPDATE alumni_events SET title = ?, starts_at = ?, timezone = ?, place = ?, online_url = ?,
                   description_html = ?, updated_at = ? WHERE id = ?""", (*values, now, event_id))
            if not cursor.rowcount:
                raise HTTPException(404, "Not found")
            action = "alumni_event_update"
        await _audit(db, actor, action, "alumni_event", event_id, {"title": values[0], "starts_at": values[1]})
        await db.commit()
        row = await (await db.execute("SELECT * FROM alumni_events WHERE id = ?", (event_id,))).fetchone()
    finally:
        await db.close()
    return _event(row)


async def _delete_event(actor: dict, event_id: int) -> dict:
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        await _rate(db, actor, "write", WRITES_PER_HOUR)
        row = await (await db.execute("SELECT title FROM alumni_events WHERE id = ?", (event_id,))).fetchone()
        if not row:
            raise HTTPException(404, "Not found")
        await db.execute("DELETE FROM alumni_events WHERE id = ?", (event_id,))
        await _audit(db, actor, "alumni_event_delete", "alumni_event", event_id, {"title": row["title"]})
        await db.commit()
    finally:
        await db.close()
    return {"ok": True}


# --- Directory ---------------------------------------------------------------------

def _directory_entry(row) -> dict:
    return {
        "id": row["account_id"], "display_name": row["display_name"], "class_year": row["class_year"],
        "company": row["company"], "role": row["role"], "city": row["city"], "linkedin_url": row["linkedin_url"],
        "note": row["note"], "coffee_chats": bool(row["coffee_chats"]),
        # The address only when its owner chose to show it.
        "email": row["email"] if row["show_email"] else None,
    }


async def _directory(q: str) -> dict:
    """Listed profiles of accounts that may sign in right now: revocation or
    removal from the lists hides a profile at once."""
    needle = q.strip().lower()
    db = await get_db()
    try:
        rows = await (await db.execute(
            f"""SELECT p.*, a.email FROM alumni_profiles p JOIN alumni_accounts a ON a.id = p.account_id
                WHERE p.listed = 1 AND {ACTIVE_ACCOUNT}
                AND (? = '' OR instr(lower(p.display_name || ' ' || COALESCE(p.company, '') || ' ' || COALESCE(p.role, '')
                     || ' ' || COALESCE(p.city, '') || ' ' || COALESCE(p.note, '') || ' ' || COALESCE(p.class_year, '')), ?) > 0)
                ORDER BY lower(p.display_name), p.account_id LIMIT 500""",
            (needle, needle))).fetchall()
        return {"items": [_directory_entry(row) for row in rows]}
    finally:
        await db.close()


class ProfileIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)
    class_year: int | None = Field(None, ge=1950, le=2100)
    company: str | None = Field(None, max_length=120)
    role: str | None = Field(None, max_length=120)
    city: str | None = Field(None, max_length=120)
    linkedin_url: str | None = Field(None, max_length=300)
    note: str | None = Field(None, max_length=500)
    coffee_chats: bool = False
    listed: bool = False
    show_email: bool = False


def _profile(row) -> dict | None:
    if not row:
        return None
    return {key: row[key] for key in ("display_name", "class_year", "company", "role", "city", "linkedin_url", "note")} | {
        "coffee_chats": bool(row["coffee_chats"]), "listed": bool(row["listed"]), "show_email": bool(row["show_email"]),
        "updated_at": row["updated_at"]}


# --- Board inbox -------------------------------------------------------------------

class MessageIn(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=5000)


_MESSAGE_SELECT = """SELECT m.*, a.email AS sender_email, COALESCE(pr.display_name, a.name) AS sender_name
    FROM alumni_board_messages m LEFT JOIN alumni_accounts a ON a.id = m.sender_account_id
    LEFT JOIN alumni_profiles pr ON pr.account_id = m.sender_account_id"""


def _message(row, body: bool = False) -> dict:
    item = {
        "id": row["id"], "subject": row["subject"], "created_at": row["created_at"],
        "read": row["read_at"] is not None, "archived": row["archived_at"] is not None,
        "sender": {"name": row["sender_name"], "email": row["sender_email"]} if row["sender_email"] else None,
    }
    if body:
        item["body"] = row["body"]
    return item


async def _inbox(archived: bool) -> dict:
    db = await get_db()
    try:
        rows = await (await db.execute(
            f"{_MESSAGE_SELECT} WHERE m.archived_at IS {'NOT ' if archived else ''}NULL ORDER BY m.id DESC LIMIT 200")).fetchall()
        unread = (await (await db.execute(
            "SELECT COUNT(*) AS n FROM alumni_board_messages WHERE read_at IS NULL AND archived_at IS NULL")).fetchone())["n"]
        return {"items": [_message(row) for row in rows], "unread": unread}
    finally:
        await db.close()


async def _open_message(actor: dict, message_id: int) -> dict:
    """The full message; opening it marks it read and is recorded."""
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        row = await (await db.execute(f"{_MESSAGE_SELECT} WHERE m.id = ?", (message_id,))).fetchone()
        if not row:
            raise HTTPException(404, "Not found")
        await db.execute("UPDATE alumni_board_messages SET read_at = COALESCE(read_at, ?) WHERE id = ?",
                         (int(time.time()), message_id))
        await _audit(db, actor, "alumni_message_read", "alumni_message", message_id)
        await db.commit()
        row = await (await db.execute(f"{_MESSAGE_SELECT} WHERE m.id = ?", (message_id,))).fetchone()
        return _message(row, body=True)
    finally:
        await db.close()


_MESSAGE_CHANGES = {
    # action: (SQL, audit action or None)
    "unread": ("UPDATE alumni_board_messages SET read_at = NULL WHERE id = ?", None),
    "archive": ("UPDATE alumni_board_messages SET archived_at = ? WHERE id = ? AND archived_at IS NULL", "alumni_message_archive"),
    "restore": ("UPDATE alumni_board_messages SET archived_at = NULL WHERE id = ? AND archived_at IS NOT NULL", "alumni_message_restore"),
}


async def _change_message(actor: dict, message_id: int, change: str) -> dict:
    sql, action = _MESSAGE_CHANGES[change]
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        if not await (await db.execute("SELECT 1 FROM alumni_board_messages WHERE id = ?", (message_id,))).fetchone():
            raise HTTPException(404, "Not found")
        cursor = await db.execute(sql, (int(time.time()), message_id) if change == "archive" else (message_id,))
        if cursor.rowcount and action:
            await _audit(db, actor, action, "alumni_message", message_id)
        await db.commit()
        return {"ok": True}
    finally:
        await db.close()


async def _delete_message(actor: dict, message_id: int) -> dict:
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        row = await (await db.execute("SELECT archived_at FROM alumni_board_messages WHERE id = ?", (message_id,))).fetchone()
        if not row:
            raise HTTPException(404, "Not found")
        if row["archived_at"] is None:
            raise HTTPException(409, "Archive the message before deleting it")
        await db.execute("DELETE FROM alumni_board_messages WHERE id = ?", (message_id,))
        await _audit(db, actor, "alumni_message_delete", "alumni_message", message_id)
        await db.commit()
        return {"ok": True}
    finally:
        await db.close()


# --- Routes shared by both doors ---------------------------------------------------
# Defined once and mounted on each router with its own session check, so the
# board's alumni and the club admins run exactly the same code.

def _mount(r: APIRouter, reader, board, base: str) -> None:
    @r.get("/posts")
    async def list_posts(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), actor: dict = Depends(reader)):
        db = await get_db()
        try:
            items, total = await _posts(db, base, drafts=actor["board"], limit=limit, offset=offset)
            return {"items": items, "total": total}
        finally:
            await db.close()

    @r.get("/posts/{post_id}")
    async def get_post(post_id: int, actor: dict = Depends(reader)):
        db = await get_db()
        try:
            return _post(await _visible_post(db, post_id, actor), base)
        finally:
            await db.close()

    @r.post("/posts", status_code=201)
    async def create_post(payload: PostIn, actor: dict = Depends(board)):
        return await _save_post(actor, base, payload)

    @r.put("/posts/{post_id}")
    async def update_post(post_id: int, payload: PostIn, actor: dict = Depends(board)):
        return await _save_post(actor, base, payload, post_id)

    @r.delete("/posts/{post_id}")
    async def delete_post(post_id: int, actor: dict = Depends(board)):
        return await _delete_post(actor, post_id)

    @r.post("/images", status_code=201)
    async def reserve_cover(payload: CoverUpload, actor: dict = Depends(board)):
        return await _reserve_cover(actor, base, payload)

    @r.put("/images/local-upload")
    async def receive_cover(token: str, request: Request, actor: dict = Depends(board)):
        """The upload link a no-bucket environment hands out instead of a presigned S3 PUT."""
        return await _receive_cover(actor, token, request)

    @r.post("/images/{image_id}/complete")
    async def complete_cover(image_id: int, actor: dict = Depends(board)):
        return await _complete_cover(actor, base, image_id)

    @r.get("/images/{image_id}")
    async def read_cover(image_id: int, token: str = Query(..., max_length=300), actor: dict = Depends(reader)):
        return await _cover_response(actor, image_id, token)

    @r.get("/events")
    async def list_events(actor: dict = Depends(reader)):
        db = await get_db()
        try:
            return await _events(db)
        finally:
            await db.close()

    @r.post("/events", status_code=201)
    async def create_event(payload: EventIn, actor: dict = Depends(board)):
        return await _save_event(actor, payload)

    @r.put("/events/{event_id}")
    async def update_event(event_id: int, payload: EventIn, actor: dict = Depends(board)):
        return await _save_event(actor, payload, event_id)

    @r.delete("/events/{event_id}")
    async def delete_event(event_id: int, actor: dict = Depends(board)):
        return await _delete_event(actor, event_id)

    @r.get("/directory")
    async def directory(q: str = Query("", max_length=100), actor: dict = Depends(reader)):
        return await _directory(q)

    @r.get("/inbox")
    async def inbox(archived: bool = False, actor: dict = Depends(board)):
        return await _inbox(archived)

    @r.get("/inbox/{message_id}")
    async def open_message(message_id: int, actor: dict = Depends(board)):
        return await _open_message(actor, message_id)

    @r.post("/inbox/{message_id}/{change}")
    async def change_message(message_id: int, change: Literal["unread", "archive", "restore"], actor: dict = Depends(board)):
        return await _change_message(actor, message_id, change)

    @r.delete("/inbox/{message_id}")
    async def delete_message(message_id: int, actor: dict = Depends(board)):
        return await _delete_message(actor, message_id)


_mount(router, alumni_reader, alumni_board, ALUMNI_BASE)
_mount(admin_router, admin_actor, admin_actor, ADMIN_BASE)


# --- Alumni only: home, own profile, contact the board -----------------------------

@router.get("/home")
async def home(actor: dict = Depends(alumni_reader)):
    db = await get_db()
    try:
        posts, _ = await _posts(db, ALUMNI_BASE, drafts=False, limit=3)
        events = await _events(db, upcoming_limit=3, past_limit=0)
        profile = await (await db.execute(
            "SELECT listed FROM alumni_profiles WHERE account_id = ?", (actor["alumni_id"],))).fetchone()
        unread = None
        if actor["board"]:
            unread = (await (await db.execute(
                "SELECT COUNT(*) AS n FROM alumni_board_messages WHERE read_at IS NULL AND archived_at IS NULL")).fetchone())["n"]
        return {
            "me": {"email": actor["email"], "name": actor["name"], "board": actor["board"]},
            "profile": {"exists": profile is not None, "listed": bool(profile and profile["listed"])},
            "posts": posts, "events": events["upcoming"], "unread": unread,
        }
    finally:
        await db.close()


@router.get("/profile")
async def my_profile(actor: dict = Depends(alumni_reader)):
    db = await get_db()
    try:
        row = await (await db.execute("SELECT * FROM alumni_profiles WHERE account_id = ?", (actor["alumni_id"],))).fetchone()
        return {"profile": _profile(row), "email": actor["email"]}
    finally:
        await db.close()


@router.put("/profile")
async def save_profile(payload: ProfileIn, actor: dict = Depends(alumni_reader)):
    name = payload.display_name.strip()
    if not name:
        raise HTTPException(422, "Enter the name to show")
    values = (name, payload.class_year, _text(payload.company), _text(payload.role), _text(payload.city),
              _https_url(payload.linkedin_url, "The LinkedIn link", ("linkedin.com",)), _text(payload.note),
              int(payload.coffee_chats), int(payload.listed), int(payload.show_email), int(time.time()))
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        await db.execute(
            """INSERT INTO alumni_profiles (account_id, display_name, class_year, company, role, city, linkedin_url, note,
                   coffee_chats, listed, show_email, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(account_id) DO UPDATE SET display_name = excluded.display_name, class_year = excluded.class_year,
                   company = excluded.company, role = excluded.role, city = excluded.city, linkedin_url = excluded.linkedin_url,
                   note = excluded.note, coffee_chats = excluded.coffee_chats, listed = excluded.listed,
                   show_email = excluded.show_email, updated_at = excluded.updated_at""",
            (actor["alumni_id"], *values))
        await _audit(db, actor, "alumni_profile_save", "alumni_profile", actor["alumni_id"],
                     {"listed": payload.listed, "show_email": payload.show_email})
        await db.commit()
        row = await (await db.execute("SELECT * FROM alumni_profiles WHERE account_id = ?", (actor["alumni_id"],))).fetchone()
        return {"profile": _profile(row), "email": actor["email"]}
    finally:
        await db.close()


@router.post("/profile/hide")
async def hide_profile(actor: dict = Depends(alumni_reader)):
    """Takes the profile out of the directory at once; it stays for the owner to edit."""
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute("UPDATE alumni_profiles SET listed = 0, updated_at = ? WHERE account_id = ?",
                                  (int(time.time()), actor["alumni_id"]))
        if not cursor.rowcount:
            raise HTTPException(404, "You have no profile")
        await _audit(db, actor, "alumni_profile_hide", "alumni_profile", actor["alumni_id"])
        await db.commit()
        return {"ok": True}
    finally:
        await db.close()


@router.delete("/profile")
async def delete_profile(actor: dict = Depends(alumni_reader)):
    """Deletes the profile row now. Daily backups keep a copy for up to 30 days."""
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute("DELETE FROM alumni_profiles WHERE account_id = ?", (actor["alumni_id"],))
        if not cursor.rowcount:
            raise HTTPException(404, "You have no profile")
        await _audit(db, actor, "alumni_profile_delete", "alumni_profile", actor["alumni_id"])
        await db.commit()
        return {"ok": True}
    finally:
        await db.close()


@router.post("/messages", status_code=201)
async def contact_board(payload: MessageIn, actor: dict = Depends(alumni_reader)):
    """A message to the board's inbox, in the portal only: no email is sent.
    The sender gets this acknowledgement, never the inbox."""
    subject, body = payload.subject.strip(), payload.body.strip()
    if not subject or not body:
        raise HTTPException(422, "Write a subject and a message")
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        await _rate(db, actor, "contact", CONTACT_PER_HOUR)
        cursor = await db.execute(
            "INSERT INTO alumni_board_messages (sender_account_id, subject, body, created_at) VALUES (?,?,?,?)",
            (actor["alumni_id"], subject, body, int(time.time())))
        await _audit(db, actor, "alumni_message_create", "alumni_message", cursor.lastrowid)
        await db.commit()
        return {"ok": True}
    finally:
        await db.close()

