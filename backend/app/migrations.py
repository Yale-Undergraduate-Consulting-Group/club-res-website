"""Versioned schema changes, per app (docs/PLATFORM.md, step 2).

``app.database._baseline_schema`` is the frozen baseline: it still runs on
every start, exactly as before, because it also does start-time upkeep (the
first member becomes admin when there is none). Every schema change after it
is a numbered ``Migration`` in its app's manifest, applied once and recorded
in ``schema_migrations``. ``app.database.init_db`` runs both, so the server,
scripts and tests all reach the same schema.

Each migration runs in one ``BEGIN IMMEDIATE`` transaction together with its
record, so two containers starting at once (a blue/green overlap) apply it
once, and a failure leaves neither a half-made change nor a record. A failure
stops the new container from starting, which leaves the previous one serving.

The previous release keeps running while the new one starts, so a migration
only adds: new tables, new nullable or defaulted columns, new indexes. It never
drops, renames or narrows something the running release reads.

A migration holds the database's write lock until it commits, and the
running release's writes wait at most five seconds (busy_timeout) for it. So
a migration is quick schema work; a backfill over many rows is a batched,
leader-only job instead (see company_register.backfill_register_entities).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from app.database import get_db, is_postgres

if TYPE_CHECKING:
    from app.modules import Module


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    up: Callable[[Any], Awaitable[None]]


class _InTransaction:
    """The connection a migration gets. ``commit`` would split the change
    from its record, and ``executescript`` commits by itself, so both refuse."""

    def __init__(self, db: Any):
        self._db = db

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        return await self._db.execute(*args, **kwargs)

    async def executemany(self, *args: Any, **kwargs: Any) -> Any:
        return await self._db.executemany(*args, **kwargs)

    async def commit(self) -> None:
        raise RuntimeError("A migration must not commit; the runner commits it with its record.")

    async def executescript(self, *_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("A migration must not use executescript (it commits); use execute per statement.")


async def migrate(modules: list[Module]) -> list[str]:
    """Apply every app's pending migrations in version order. Returns
    "app:version name" for each one applied by this call."""
    if is_postgres():
        raise RuntimeError("Migrations need the transactional SQLite adapter; Postgres is not supported here.")
    applied: list[str] = []
    db = await get_db()
    try:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                   app TEXT NOT NULL,
                   version INTEGER NOT NULL,
                   name TEXT NOT NULL,
                   applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                   PRIMARY KEY (app, version))"""
        )
        await db.commit()
        for module in modules:
            for migration in sorted(module.migrations, key=lambda m: m.version):
                await db.execute("BEGIN IMMEDIATE")
                done = await (await db.execute(
                    "SELECT 1 FROM schema_migrations WHERE app = ? AND version = ?",
                    (module.id, migration.version),
                )).fetchone()
                if done:
                    await db.rollback()
                    continue
                try:
                    await migration.up(_InTransaction(db))
                    await db.execute(
                        "INSERT INTO schema_migrations (app, version, name) VALUES (?, ?, ?)",
                        (module.id, migration.version, migration.name),
                    )
                    await db.commit()
                except BaseException:
                    await db.rollback()
                    raise
                applied.append(f"{module.id}:{migration.version} {migration.name}")
    finally:
        await db.close()
    return applied
