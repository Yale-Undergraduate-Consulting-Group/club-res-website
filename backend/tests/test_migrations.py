"""Versioned migrations apply once, in order, all-or-nothing (app/migrations.py).

A migration and its record commit together, so a failure applies nothing and
is retried on the next start; two containers starting at once (blue/green)
apply it once. From backend/: python tests/test_migrations.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JWT_SECRET", "migrations-test-secret-0123456789abcd")
os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.mkdtemp()) / 't.db'}"

from app.database import get_db  # noqa: E402
from app.migrations import Migration, migrate  # noqa: E402
from app.modules import Module  # noqa: E402

calls: list[str] = []
#: Set once the first migration is running, so the second starter is
#: certain to arrive while the first holds the lock.
inside = asyncio.Event()


def step(name: str, sql: str):
    async def up(db):
        calls.append(name)
        inside.set()
        await asyncio.sleep(0.3)   # the racing starter arrives meanwhile
        await db.execute(sql)
    return up


async def tables() -> set[str]:
    db = await get_db()
    try:
        return {r[0] for r in await (await db.execute("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()}
    finally:
        await db.close()


async def recorded() -> list[tuple]:
    db = await get_db()
    try:
        return [tuple(r) for r in await (await db.execute(
            "SELECT app, version, name FROM schema_migrations ORDER BY app, version")).fetchall()]
    finally:
        await db.close()


async def main() -> None:
    # The real database already exists in WAL mode when containers start; a
    # brand-new file would race on switching its journal mode instead.
    await (await get_db()).close()
    # In version order whatever the declared order, each app on its own.
    alpha = Module("alpha", "Alpha", migrations=(
        Migration(2, "add notes", step("a2", "ALTER TABLE alpha_items ADD COLUMN note TEXT")),
        Migration(1, "items", step("a1", "CREATE TABLE alpha_items (id INTEGER PRIMARY KEY)")),
    ))
    beta = Module("beta", "Beta", migrations=(Migration(1, "things", step("b1", "CREATE TABLE beta_things (id INTEGER)")),))
    # Two containers starting together apply each migration once: the second
    # arrives while the first is inside its first migration and holds the lock.
    racing = asyncio.create_task(migrate([alpha, beta]))
    await inside.wait()
    second = await migrate([alpha, beta])
    first = await racing
    assert sorted(first + second) == ["alpha:1 items", "alpha:2 add notes", "beta:1 things"], (first, second)
    assert calls == ["a1", "a2", "b1"], calls
    assert await recorded() == [("alpha", 1, "items"), ("alpha", 2, "add notes"), ("beta", 1, "things")]
    assert await migrate([alpha, beta]) == []    # nothing left to do

    # A failure applies nothing, records nothing, stops later versions, and
    # stops the start - then a fixed release applies it.
    async def half_then_fail(db):
        await db.execute("CREATE TABLE gamma_half (id INTEGER)")
        raise ValueError("bad migration")

    broken = Module("gamma", "Gamma", migrations=(
        Migration(1, "half", half_then_fail),
        Migration(2, "after", step("g2", "CREATE TABLE gamma_after (id INTEGER)")),
    ))
    try:
        await migrate([broken])
        raise AssertionError("a failing migration must stop the start")
    except ValueError:
        pass
    assert not {"gamma_half", "gamma_after"} & await tables()
    assert not [r for r in await recorded() if r[0] == "gamma"]
    fixed = Module("gamma", "Gamma", migrations=(
        Migration(1, "half", step("g1", "CREATE TABLE gamma_half (id INTEGER)")),
        Migration(2, "after", step("g2", "CREATE TABLE gamma_after (id INTEGER)")),
    ))
    assert await migrate([fixed]) == ["gamma:1 half", "gamma:2 after"]

    # A migration cannot commit part of itself: commit and executescript refuse.
    for sneaky in ("commit", "script"):
        async def up(db, sneaky=sneaky):
            await db.execute(f"CREATE TABLE delta_{sneaky} (id INTEGER)")
            if sneaky == "commit":
                await db.commit()
            else:
                await db.executescript("CREATE TABLE delta_more (id INTEGER);")
        try:
            await migrate([Module("delta", "Delta", migrations=(Migration(1, sneaky, up),))])
            raise AssertionError(f"{sneaky} inside a migration must refuse")
        except RuntimeError:
            pass
        assert f"delta_{sneaky}" not in await tables() and "delta_more" not in await tables()


if __name__ == "__main__":
    asyncio.run(main())
    print("migrations: ok")
