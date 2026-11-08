"""Who may use which app (docs/PLATFORM.md, step 3).

Every app is open to all active members unless an admin restricts it; a
restricted app is open to the members listed for it and to admins. The
platform itself (``core``: sign-in, profile, admin) is never restricted.
Background accounts (role ``system``, the catalog builder) act for the club
and are never restricted either.

The check runs on the server: on every route of an app (``require_app``, added
by ``app.modules.mount``) and in every job that acts for a member (``may_use``
in Python, ``may_use_sql`` inside a query), next to the existing
inactive-member check.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth_deps import get_current_admin, get_current_user_optional
from app.database import get_db

CORE = "core"
_ALWAYS = ("admin", "system")


async def create_tables(db: Any) -> None:
    """Migration core:1."""
    await db.execute(
        """CREATE TABLE IF NOT EXISTS app_access (
               app TEXT PRIMARY KEY,
               restricted INTEGER NOT NULL DEFAULT 0,
               updated_by INTEGER,
               updated_at TEXT DEFAULT CURRENT_TIMESTAMP)"""
    )
    await db.execute(
        """CREATE TABLE IF NOT EXISTS app_members (
               app TEXT NOT NULL,
               user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
               added_by INTEGER,
               added_at TEXT DEFAULT CURRENT_TIMESTAMP,
               PRIMARY KEY (app, user_id))"""
    )


def may_use_sql(app: str, user_column: str, role_column: str) -> tuple[str, tuple]:
    """A WHERE condition (and its parameters) true when the member in
    ``user_column`` may use ``app``."""
    if app == CORE:
        return "1", ()
    return (
        f"""({role_column} IN ('admin', 'system')
             OR NOT EXISTS (SELECT 1 FROM app_access a WHERE a.app = ? AND a.restricted = 1)
             OR EXISTS (SELECT 1 FROM app_members m WHERE m.app = ? AND m.user_id = {user_column}))""",
        (app, app),
    )


async def may_use(db: Any, user_id: int | None, app: str) -> bool:
    """Whether member ``user_id`` may use ``app``, on an open connection."""
    if app == CORE:
        return True
    if user_id is None:
        return False
    condition, params = may_use_sql(app, "u.id", "u.role")
    row = await (await db.execute(
        f"SELECT 1 FROM users u WHERE u.id = ? AND {condition}", (user_id, *params),
    )).fetchone()
    return row is not None


async def apps_for(user: dict, apps: list[str]) -> list[str]:
    """The apps among ``apps`` this member may use."""
    db = await get_db()
    try:
        return [app for app in apps if await may_use(db, int(user["id"]), app)]
    finally:
        await db.close()


def require_app(app: str, title: str):
    """Route dependency for every route of ``app``. A signed-in member
    without access gets 403. A request with no session is left to the route's
    own check (the tracking pixel and Slack's signed requests carry none)."""

    async def dependency(user: dict | None = Depends(get_current_user_optional)) -> None:
        if user is None:
            return
        db = await get_db()
        try:
            allowed = await may_use(db, int(user["id"]), app)
        finally:
            await db.close()
        if not allowed:
            raise HTTPException(status_code=403, detail=f"You don't have access to {title}. Ask an admin.")

    return dependency


# --- Admin: who may use each app ---------------------------------------------

router = APIRouter()


class AppAccessUpdate(BaseModel):
    restricted: bool
    member_ids: list[int] = []


def _apps() -> dict[str, str]:
    from app.modules import all_modules

    return {m.id: m.title for m in all_modules() if m.id != CORE}


@router.get("")
async def list_app_access(admin: dict = Depends(get_current_admin)) -> list[dict]:
    apps = _apps()
    db = await get_db()
    try:
        restricted = {r["app"] for r in await (await db.execute(
            "SELECT app FROM app_access WHERE restricted = 1")).fetchall()}
        members: dict[str, list[int]] = {}
        for r in await (await db.execute("SELECT app, user_id FROM app_members ORDER BY user_id")).fetchall():
            members.setdefault(r["app"], []).append(int(r["user_id"]))
    finally:
        await db.close()
    return [{"app": app, "title": title, "restricted": app in restricted, "member_ids": members.get(app, [])}
            for app, title in apps.items()]


@router.put("/{app}")
async def update_app_access(app: str, body: AppAccessUpdate, admin: dict = Depends(get_current_admin)) -> dict:
    if app not in _apps():
        raise HTTPException(404, "No such app")
    from app.services.audit_service import log_audit
    from app.services.dispatch_service import begin_write

    db = await get_db()
    try:
        await begin_write(db)
        known = {int(r["id"]) for r in await (await db.execute(
            f"SELECT id FROM users WHERE id IN ({','.join('?' * len(body.member_ids)) or 'NULL'})",
            tuple(body.member_ids),
        )).fetchall()}
        if set(body.member_ids) - known:
            await db.rollback()
            raise HTTPException(400, "Unknown member")
        await db.execute(
            """INSERT INTO app_access (app, restricted, updated_by, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(app) DO UPDATE SET restricted = excluded.restricted,
                   updated_by = excluded.updated_by, updated_at = CURRENT_TIMESTAMP""",
            (app, int(body.restricted), admin["id"]),
        )
        await db.execute("DELETE FROM app_members WHERE app = ?", (app,))
        await db.executemany(
            "INSERT INTO app_members (app, user_id, added_by) VALUES (?, ?, ?)",
            [(app, uid, admin["id"]) for uid in sorted(known)],
        )
        await db.commit()
    finally:
        await db.close()
    await log_audit(admin["id"], "app_access_update", "app", app,
                    f"Restricted to {len(known)} member(s)" if body.restricted else "Open to all members")
    return {"ok": True}
