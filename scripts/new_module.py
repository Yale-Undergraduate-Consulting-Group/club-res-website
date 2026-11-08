#!/usr/bin/env python3
"""Create a new app on the platform (docs/PLATFORM.md, "Adding an app").

    python3 scripts/new_module.py <id> "<Title>" "<what it is for>"

The new app works end to end and is open to all members until an admin
restricts it (Admin -> Apps):

- backend/app/modules/<id>.py       manifest: signed-in router at /api/<id>, migration 1
- backend/app/routers/<id>.py       each member's own items: GET and POST /api/<id>
- backend/tests/test_<id>.py        smoke test: sign-in, per-member data, app access
- frontend/src/pages/<Name>.tsx     the page at /<id>, with loading, error and empty states
- registered in all_modules(), frontend/src/modules, the header menu and
  the docs table of apps

Replace the item list with the app's real data and keep the smoke test's
access checks. Nothing is written when the id, its URL or a file is taken.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ID = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
#: URL segments the platform already serves or reserves.
RESERVED = {"api", "admin", "auth", "core", "health", "ai", "assets", "static", "settings", "profile", "track", "slack"}


def names(app_id: str) -> tuple[str, str]:
    parts = app_id.split("_")
    pascal = "".join(p.capitalize() for p in parts)
    return pascal, parts[0] + "".join(p.capitalize() for p in parts[1:])


def insert(text: str, pattern: str, replace: str, what: str) -> str:
    """One anchored change; a moved anchor stops the generator, not a guess."""
    new, count = re.subn(pattern, replace, text, count=1, flags=re.M)
    if count != 1:
        raise SystemExit(f"Could not find {what}; edit it by hand (docs/PLATFORM.md).")
    return new


def plan(root: Path, app_id: str, title: str, purpose: str) -> dict[Path, str]:
    pascal, camel = names(app_id)
    backend, frontend = root / "backend" / "app", root / "frontend" / "src"
    new_files = {
        backend / "modules" / f"{app_id}.py": MANIFEST,
        backend / "routers" / f"{app_id}.py": ROUTER,
        root / "backend" / "tests" / f"test_{app_id}.py": SMOKE_TEST,
        frontend / "pages" / f"{pascal}.tsx": PAGE,
    }
    taken = [str(p.relative_to(root)) for p in new_files if p.exists()]
    if taken:
        raise SystemExit("Already exists: " + ", ".join(taken))
    fill = {"id": app_id, "title": title, "purpose": purpose, "pascal": pascal, "camel": camel}
    out = {path: template.format(**fill) for path, template in new_files.items()}

    registry = backend / "modules" / "__init__.py"
    text = registry.read_text()
    text = insert(text, r"^(    from app\.modules import [\w, ]+)$", rf"\1, {app_id}", "the imports in all_modules()")
    text = insert(text, r"^(    return \[core\.MODULE[\w., ]*)\]$", rf"\1, {app_id}.MODULE]", "the list in all_modules()")
    out[registry] = text

    pages = frontend / "modules" / "pages.ts"
    out[pages] = pages.read_text().rstrip("\n") + f"\nexport const {pascal} = lazy(() => import('../pages/{pascal}'));\n"

    modules = frontend / "modules" / "index.tsx"
    text = modules.read_text()
    if re.search(rf"path: '{app_id}'|id: '{app_id}'", text):
        raise SystemExit(f"The browser already has a page or app named {app_id}.")
    text = insert(text, r"^(import \{ [\w, ]+)( \} from '\./pages';)$", rf"\1, {pascal}\2", "the page imports in modules/index.tsx")
    text = insert(text, r"^(/\*\* Every app, core first\. \*/)$", APP_MODULE.format(**fill) + r"\1", "MODULES in modules/index.tsx")
    text = insert(text, r"^(export const MODULES: AppModule\[\] = \[[\w, ]+)\];$", rf"\1, {camel}];", "the MODULES list")
    out[modules] = text

    nav = frontend / "lib" / "navConfig.ts"
    out[nav] = insert(nav.read_text(), r"^(export const TOP_LEVEL_IDS: NavItemId\[\] = \[[^\]]*)\];$",
                      rf"\1, '{app_id}'];", "TOP_LEVEL_IDS in lib/navConfig.ts")

    docs = root / "docs" / "PLATFORM.md"
    out[docs] = insert(docs.read_text(), r"^(\| `projects` \|.*\|\n(?:\|.*\|\n)*)",
                       rf"\1| `{app_id}` | `/api/{app_id}` | {title} | — |\n", "the table of apps in docs/PLATFORM.md")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("id", help="lower-case id: URL segment, table prefix and Python module, e.g. events")
    parser.add_argument("title", help='menu and page title, e.g. "Events"')
    parser.add_argument("purpose", help="one sentence on what the app is for")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    app_id, title, purpose = args.id, " ".join(args.title.split()), " ".join(args.purpose.split())
    if not ID.match(app_id) or len(app_id) > 24:
        parser.error("id: lower-case letters and digits, words joined by _, at most 24 characters")
    if app_id in RESERVED:
        parser.error(f"id: {app_id} is used by the platform")
    if not title or not purpose or any(c in title + purpose for c in "\"'\\{}<>|`"):
        parser.error("title and purpose: plain text without quotes, braces, | or <>")
    files = plan(args.root.resolve(), app_id, title, purpose)
    for path, text in files.items():
        path.write_text(text)
        print("wrote", path.relative_to(args.root.resolve()))
    print(f"\nNext: from backend/, python tests/test_{app_id}.py and python tests/test_modules.py;"
          " in frontend/, npm run test:unit and npm run lint. Then open /" + app_id + ".")
    return 0


MANIFEST = '''"""{title}: {purpose}"""
from app.migrations import Migration
from app.modules import Module, Route
from app.routers import {id}

MODULE = Module(
    id="{id}",
    title="{title}",
    routes=(Route({id}.router, "/api/{id}", "{id}", signed_in=True),),
    migrations=(
        Migration(1, "{title} items", {id}.create_tables),
    ),
)
'''

ROUTER = '''"""{title}: {purpose}

Each member's own items. Sign-in and the app-access check come from the
manifest (app/modules/{id}.py); this file only adds the member's own scope.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth_deps import get_current_user
from app.database import get_db

router = APIRouter()


async def create_tables(db: Any) -> None:
    """Migration {id}:1 - additive and quick (app/migrations.py)."""
    await db.execute(
        """CREATE TABLE IF NOT EXISTS {id}_items (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
               title TEXT NOT NULL,
               created_at TEXT DEFAULT CURRENT_TIMESTAMP)"""
    )
    await db.execute("CREATE INDEX IF NOT EXISTS idx_{id}_items_user ON {id}_items(user_id)")


class ItemIn(BaseModel):
    title: str = Field(max_length=200)


@router.get("")
async def list_items(user: dict = Depends(get_current_user)) -> list[dict]:
    db = await get_db()
    try:
        rows = await (await db.execute(
            "SELECT id, title, created_at FROM {id}_items WHERE user_id = ? ORDER BY id DESC",
            (user["id"],))).fetchall()
    finally:
        await db.close()
    return [dict(row) for row in rows]


@router.post("", status_code=201)
async def add_item(body: ItemIn, user: dict = Depends(get_current_user)) -> dict:
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="Title is required")
    db = await get_db()
    try:
        cursor = await db.execute("INSERT INTO {id}_items (user_id, title) VALUES (?, ?)", (user["id"], title))
        await db.commit()
    finally:
        await db.close()
    return {{"id": cursor.lastrowid, "title": title}}
'''

SMOKE_TEST = '''"""{title} (app/modules/{id}.py): sign-in, each member's own items, and
per-app access. From backend/: python tests/test_{id}.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JWT_SECRET", "{id}-test-secret-0123456789abcdefghij")
os.environ["DATABASE_URL"] = f"sqlite:///{{Path(tempfile.mkdtemp()) / 't.db'}}"

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from app.database import get_db, init_db  # noqa: E402
from app.jwt_utils import create_token  # noqa: E402

ADMIN, ANA, BEN = 1, 2, 3


async def setup() -> list:
    await init_db()
    db = await get_db()
    try:
        await db.executemany(
            "INSERT INTO users (id, email, name, role, is_active) VALUES (?, ?, ?, ?, 1)",
            [(ADMIN, "admin@yale.edu", "Admin", "admin"), (ANA, "ana@yale.edu", "Ana", "standard"),
             (BEN, "ben@yale.edu", "Ben", "standard")],
        )
        await db.commit()
        return [tuple(r) for r in await (await db.execute(
            "SELECT app, version FROM schema_migrations WHERE app = '{id}'")).fetchall()]
    finally:
        await db.close()


def as_member(user_id: int, role: str = "standard") -> TestClient:
    client = TestClient(main.app, raise_server_exceptions=False)
    client.cookies.set("yucg_session", create_token(user_id, f"{{user_id}}@yale.edu", None, None, role))
    return client


def tests() -> None:
    assert asyncio.run(setup()) == [("{id}", 1)]
    admin, ana, ben = as_member(ADMIN, "admin"), as_member(ANA), as_member(BEN)

    assert TestClient(main.app).get("/api/{id}").status_code == 401
    assert ana.post("/api/{id}", json={{"title": "  "}}).status_code == 422
    assert ana.post("/api/{id}", json={{"title": "First"}}).status_code == 201
    assert [i["title"] for i in ana.get("/api/{id}").json()] == ["First"]
    assert ben.get("/api/{id}").json() == [], "a member saw another member's items"
    assert "{id}" in ana.get("/api/auth/me").json()["user"]["apps"]

    # Restricted to Ben: Ana is refused and loses the menu entry; admins keep it.
    assert admin.put("/api/admin/apps/{id}", json={{"restricted": True, "member_ids": [BEN]}}).status_code == 200
    assert ana.get("/api/{id}").status_code == 403
    assert "{id}" not in ana.get("/api/auth/me").json()["user"]["apps"]
    assert ben.get("/api/{id}").status_code == 200
    assert admin.get("/api/{id}").status_code == 200


if __name__ == "__main__":
    tests()
    print("{id}: ok")
'''

PAGE = """import {{ useEffect, useState, type FormEvent }} from 'react';
import {{ fetchApi }} from '../api';
import PageHeader from '../components/PageHeader';
import {{ Button, EmptyState, Notice }} from '../components/ui/Primitives';

type Item = {{ id: number; title: string; created_at?: string }};

/** {title}: {purpose} */
export default function {pascal}() {{
  const [items, setItems] = useState<Item[] | null>(null);
  const [error, setError] = useState('');
  const [title, setTitle] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {{
    fetchApi<Item[]>('/api/{id}')
      .then(setItems)
      .catch((e: Error) => setError(e.message));
  }}, []);

  const add = async (event: FormEvent) => {{
    event.preventDefault();
    setSaving(true);
    setError('');
    try {{
      const item = await fetchApi<Item>('/api/{id}', {{ method: 'POST', body: JSON.stringify({{ title }}) }});
      setItems((rows) => [item, ...(rows ?? [])]);
      setTitle('');
    }} catch (e) {{
      setError((e as Error).message);
    }} finally {{
      setSaving(false);
    }}
  }};

  return (
    <div className="app-workspace max-w-5xl space-y-4">
      <PageHeader title="{title}" subtitle="{purpose}" />
      {{error && <Notice tone="danger">{{error}}</Notice>}}
      <form className="surface-card rounded-xl p-6 flex flex-wrap items-end gap-3" onSubmit={{(e) => void add(e)}}>
        <label className="flex-1 min-w-[12rem] text-sm">
          <span className="block mb-1 text-slate-600">New item</span>
          <input className="ui-input w-full" value={{title}} maxLength={{200}} onChange={{(e) => setTitle(e.target.value)}} />
        </label>
        <Button type="submit" disabled={{saving || !title.trim()}}>{{saving ? 'Adding…' : 'Add'}}</Button>
      </form>
      {{items === null && !error && <p className="text-sm text-slate-500">Loading…</p>}}
      {{items?.length === 0 && <EmptyState title="Nothing here yet" body="Items you add appear here." />}}
      {{!!items?.length && (
        <ul className="surface-card rounded-xl divide-y divide-[var(--border)]">
          {{items.map((item) => (
            <li key={{item.id}} className="px-6 py-3 text-sm">{{item.title}}</li>
          ))}}
        </ul>
      )}}
    </div>
  );
}}
"""

APP_MODULE = """const {camel}: AppModule = {{
  id: '{id}',
  title: '{title}',
  routes: [{{ path: '{id}', element: <{pascal} /> }}],
  nav: [{{ id: '{id}', to: '/{id}', label: '{title}', shortLabel: '{title}' }}],
}};

"""


if __name__ == "__main__":
    sys.exit(main())
