#!/usr/bin/env python3
"""Download and parse the bulk company registers on one machine, load them on another.

Why this exists as a separate entrypoint: the sources are large and the web
box is small. A year of IRS 990 filings is about 1.5 GB across sixteen
archives, the Companies House file is 2.8 GB uncompressed, and extracting the
archives that use deflate64 needs hundreds of megabytes of scratch disk. Doing
that on the t3.small that serves the site works, but it competes with the site
for a 2 GB machine's memory and disk.

So the heavy half - fetch, decompress, parse - runs wherever you point this
script, and only the parsed rows travel. The SQLite file on the web box stays
the one thing members read.

Usage on the loader machine (cron-friendly, safe to repeat):

    export REGISTER_API=https://<app-url>
    export REGISTER_LOADER_TOKEN=<same value as on the web box>
    python3 scripts/register_loader.py --source all

    # or one at a time
    python3 scripts/register_loader.py --source form_5500 --year 2025
    python3 scripts/register_loader.py --source irs_990_officers --year 2025

With no REGISTER_API it writes straight to the local database instead, which
is how it behaves when run on the web box itself.

Every source is idempotent: rows upsert on (source, source_key), and a batch is
only recorded as done after its final chunk, so an interrupted run repeats
rather than being remembered as complete.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import zipfile
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from app.services import company_register as cr  # noqa: E402

CHUNK = 2000


class Sink:
    """Where parsed rows go: a remote app, or the local database."""

    def __init__(self, api: str | None, token: str | None) -> None:
        self.api = (api or "").rstrip("/")
        self.token = token or ""
        if self.api and not self.token:
            raise SystemExit("REGISTER_LOADER_TOKEN is required when REGISTER_API is set")

    async def send(self, source: str, batch: str, *, companies: list[dict[str, Any]] | None = None,
                   people: dict[str, list[dict[str, Any]]] | None = None, done: bool = False) -> int:
        companies = companies or []
        people = people or {}
        if not companies and not people and not done:
            return 0
        if not self.api:
            written = await cr.upsert_companies(companies) if companies else 0
            if people:
                await cr._attach_people({(source, key): rows for key, rows in people.items()})
            if done:
                await cr._record_ingest(source, batch, len(companies), written, "ok", "loaded locally")
            return written
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{self.api}/api/yucgoutreach/register/load",
                headers={"Authorization": f"Bearer {self.token}"},
                json={"source": source, "batch": batch, "companies": companies,
                      "people": people, "done": done},
            )
            response.raise_for_status()
            return int(response.json().get("written") or 0)


async def _push_in_chunks(sink: Sink, source: str, batch: str, rows: Iterable[dict[str, Any]]) -> int:
    written = 0
    pending: list[dict[str, Any]] = []
    for row in rows:
        pending.append(row)
        if len(pending) >= CHUNK:
            written += await sink.send(source, batch, companies=pending)
            pending = []
    if pending:
        written += await sink.send(source, batch, companies=pending)
    return written


async def load_form_5500(sink: Sink, year: int) -> dict[str, Any]:
    url = f"https://askebsa.dol.gov/FOIA%20Files/{year}/Latest/F_5500_{year}_latest.zip"
    archive = await cr._stream_to_temp(url, ".zip")
    try:
        with zipfile.ZipFile(archive.name) as zf:
            member = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
            with zf.open(member) as handle:
                companies, people = cr.parse_form_5500(handle)
    finally:
        archive.close()
    written = await _push_in_chunks(sink, "dol_5500", str(year), companies)
    await sink.send("dol_5500", str(year),
                    people={key[1]: rows for key, rows in people.items()}, done=True)
    return {"source": "dol_5500", "year": year, "companies": len(companies), "written": written,
            "people": len(people)}


async def load_irs_990(sink: Sink, year: int) -> dict[str, Any]:
    extract = await cr._stream_to_temp(cr.IRS_EXTRACT_URL.format(yy=f"{year % 100:02d}"), ".zip")
    try:
        with zipfile.ZipFile(extract.name) as zf:
            member = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
            with zf.open(member) as handle:
                qualifying = cr.qualifying_990_filers(handle)
    finally:
        extract.close()
    written = 0
    for url in cr.IRS_BMF_URLS:
        bmf = await cr._stream_to_temp(url, ".csv")
        try:
            with open(bmf.name, "rb") as handle:
                written += await _push_in_chunks(sink, "irs_990", str(year),
                                                 cr._bmf_rows_for(handle, qualifying))
        finally:
            bmf.close()
    await sink.send("irs_990", str(year), done=True)
    return {"source": "irs_990", "year": year, "qualifying": len(qualifying), "written": written}


async def load_990_officers(sink: Sink, year: int) -> dict[str, Any]:
    """Part VII officers. This is the source that most wants its own machine:
    sixteen archives, half of them deflate64, extracted to scratch disk."""
    batch_key = f"officers-{year}"
    scanned = named = 0
    for month in range(1, 13):
        for letter in ("A", "B", "C", "D"):
            url = cr._IRS_XML_BATCH.format(year=year, month=month, letter=letter)
            try:
                archive = await cr._stream_to_temp(url, ".zip")
            except Exception:
                break
            try:
                found: dict[str, list[dict[str, Any]]] = {}
                for raw in cr._batch_filings(archive.name):
                    scanned += 1
                    ein, people = cr.parse_part_vii(raw)
                    if ein and people:
                        found[ein] = people
                    if len(found) >= 500:
                        await sink.send("irs_990", batch_key, people=found)
                        named += len(found)
                        found = {}
                if found:
                    await sink.send("irs_990", batch_key, people=found)
                    named += len(found)
            finally:
                archive.close()
    await sink.send("irs_990", batch_key, done=True)
    return {"source": "irs_990_officers", "year": year, "filings": scanned, "named": named}


async def load_companies_house(sink: Sink) -> dict[str, Any]:
    from datetime import datetime, timezone

    batch = datetime.now(timezone.utc).strftime("%Y-%m-01")
    archive = await cr._stream_to_temp(cr.UK_BULK_URL.format(date=batch), ".zip")
    try:
        with zipfile.ZipFile(archive.name) as zf:
            with zf.open(zf.namelist()[0]) as handle:
                written = await _push_in_chunks(sink, "companies_house", batch,
                                                cr.parse_uk_bulk(handle))
    finally:
        archive.close()
    await sink.send("companies_house", batch, done=True)
    return {"source": "companies_house", "batch": batch, "written": written}


SOURCES = {
    "form_5500": lambda sink, year: load_form_5500(sink, year),
    "irs_990": lambda sink, year: load_irs_990(sink, year),
    "irs_990_officers": lambda sink, year: load_990_officers(sink, year),
    "companies_house": lambda sink, year: load_companies_house(sink),
}


async def main() -> int:
    from datetime import datetime, timezone

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="all", choices=[*SOURCES, "all"])
    parser.add_argument("--year", type=int, default=None,
                        help="filing year; defaults to last year for IRS, this year for DOL")
    parser.add_argument("--api", default=os.getenv("REGISTER_API"),
                        help="app base URL; omit to write to the local database")
    args = parser.parse_args()

    sink = Sink(args.api, os.getenv("REGISTER_LOADER_TOKEN"))
    now = datetime.now(timezone.utc).year
    chosen = list(SOURCES) if args.source == "all" else [args.source]
    failures = 0
    for name in chosen:
        year = args.year or (now if name == "form_5500" else now - 1)
        try:
            result = await SOURCES[name](sink, year)
            print(f"{name}: {result}", flush=True)
        except Exception as exc:  # one bad source must not stop the rest
            failures += 1
            print(f"{name}: FAILED {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
