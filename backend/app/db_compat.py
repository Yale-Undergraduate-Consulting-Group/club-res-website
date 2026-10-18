"""Postgres adapter that keeps the existing aiosqlite call shape (execute/fetch/commit/?)."""
from __future__ import annotations

import re
from typing import Any

# ponytail: first-column ON CONFLICT. Upgrade: name the real unique key per table.
_OR_REPLACE = re.compile(
    r"^INSERT\s+OR\s+REPLACE\s+INTO\s+(\w+)\s*\(([^)]+)\)\s*(VALUES.*)$",
    re.I | re.S,
)
_OR_IGNORE = re.compile(r"^INSERT\s+OR\s+IGNORE\s+INTO\s+", re.I)


def sqlite_placeholders_to_pg(sql: str) -> str:
    n = 0

    def _sub(_: re.Match[str]) -> str:
        nonlocal n
        n += 1
        return f"${n}"

    return re.sub(r"\?", _sub, sql)


def translate_sqlite_sql(sql: str) -> str:
    text = sql.strip().rstrip(";")
    m = _OR_REPLACE.match(text)
    if m:
        table, cols_raw, rest = m.group(1), m.group(2), m.group(3)
        cols = [c.strip() for c in cols_raw.split(",") if c.strip()]
        pk = cols[0]
        sets = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols[1:]) or f"{pk}=EXCLUDED.{pk}"
        text = f"INSERT INTO {table} ({cols_raw}) {rest} ON CONFLICT ({pk}) DO UPDATE SET {sets}"
    elif _OR_IGNORE.match(text):
        text = re.sub(r"^INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", text, count=1, flags=re.I)
        text = text + " ON CONFLICT DO NOTHING"
    return sqlite_placeholders_to_pg(text)


class _Row(dict):
    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class PgCursor:
    def __init__(self, rows: list[_Row], lastrowid: int | None = None):
        self._rows = rows
        self.lastrowid = lastrowid

    async def fetchone(self) -> _Row | None:
        return self._rows[0] if self._rows else None

    async def fetchall(self) -> list[_Row]:
        return self._rows


class PgConn:
    def __init__(self, raw: Any):
        self._raw = raw

    async def execute(self, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> PgCursor:
        sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        q = translate_sqlite_sql(sql)
        if q.upper().lstrip().startswith("INSERT") and "RETURNING" not in q.upper():
            q = q.rstrip() + " RETURNING id"
        if params:
            recs = await self._raw.fetch(q, *params)
        else:
            recs = await self._raw.fetch(q)
        rows = [_Row(dict(r)) for r in recs]
        last = None
        if rows and "id" in rows[0]:
            try:
                last = int(rows[0]["id"])
            except (TypeError, ValueError):
                last = None
        return PgCursor(rows, lastrowid=last)

    async def executescript(self, sql: str) -> None:
        for part in sql.split(";"):
            chunk = part.strip()
            if chunk:
                await self.execute(chunk)

    async def commit(self) -> None:
        return None

    async def close(self) -> None:
        await self._raw.close()


async def connect_postgres(dsn: str) -> PgConn:
    import asyncpg

    raw = await asyncpg.connect(dsn)
    return PgConn(raw)
