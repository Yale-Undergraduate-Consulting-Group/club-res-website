"""Bulk company register from free public sources.

The club's target pool was a 500-row hand-maintained spreadsheet plus
whatever anyone typed into Find people. This builds a browsable register
from registers that are free, bulk-downloadable, and legitimate to use:

- us_public: SEC's company_tickers.json (~10k US listed companies). SIC
  sector is filled in lazily from data.sec.gov/submissions on demand,
  because SEC publishes no bulk SIC file and per-company calls are rate
  limited to 10/sec by their fair-access policy.
- us_private: SEC Form D quarterly data sets (~3 MB per quarter, TSV).
  Every US company that raised under Reg D - the closest thing to a free
  startup register - with its industry group, revenue range, offering
  amount, and the executive officers named on the filing.
- uk: Companies House bulk BasicCompanyData, filtered at ingest to active
  companies in target SIC divisions. Officers need COMPANIES_HOUSE_API_KEY
  and are fetched on demand elsewhere.

Headcount honesty: no free US source publishes employee counts, so
`employees` stays NULL for US rows and the UI shows revenue range / last
raise instead. Only Companies House accounts carry a real headcount; it
is recorded with employees_source so a filter can say where it came from.
"""
from __future__ import annotations

import asyncio
import csv
import html
import io
import json
import logging
import os
import re
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from app.database import get_db

logger = logging.getLogger(__name__)

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FORM_D_URL = "https://www.sec.gov/files/structureddata/data/form-d-data-sets/{quarter}_d.zip"
UK_BULK_URL = "https://download.companieshouse.gov.uk/BasicCompanyDataAsOneFile-{date}.zip"

# Companies House files no headcount in this product, but AccountCategory is
# the statutory size band a company filed under: SMALL/MICRO sit below the
# small-company thresholds, so FULL, GROUP and MEDIUM are the companies above
# them (medium is >50 employees or >GBP 10.2M turnover; full/group larger).
# That is the honest version of "above a certain employee count" from a free
# source - 112,450 active companies of 5.69M rows in the 2026-09 file.
UK_SIZE_BANDS = frozenset({"FULL", "GROUP", "MEDIUM"})
UK_BAND_LABEL = {"MEDIUM": "medium (>50 staff or >£10.2M turnover)", "FULL": "large (full accounts)", "GROUP": "group (consolidated accounts)"}
# SIC divisions that are holding, financing or property vehicles rather than
# operating companies - the same reason Form D pooled funds are excluded.
UK_SKIP_SIC_PREFIXES = ("64", "65", "66", "68", "74990", "99999", "98000", "98100", "98200")

# A Form D filing this small is usually a single-property LLC or a friends
# and family round, not a company with a consulting budget.
MIN_OFFERING_USD = float(os.getenv("REGISTER_FORMD_MIN_USD", "2000000"))
FORM_D_MONTHS = int(os.getenv("REGISTER_FORMD_MONTHS", "36"))
# Form D industry groups worth club outreach; pooled/real-estate vehicles are
# funds, not operating companies with projects for students.
# Compared case- and ampersand-insensitively: the live 2026Q1 file writes
# "REITS and Finance" and "Other Banking and Financial Services", so an
# ampersand spelling silently let every REIT and fund through.
FORM_D_SKIP_GROUPS = frozenset(_skip.lower() for _skip in (
    "Pooled Investment Fund", "Real Estate", "Residential", "Commercial",
    "REITS and Finance", "Other Real Estate", "Oil and Gas", "Coal Mining",
    "Other Banking and Financial Services", "Investing", "Investment Banking",
))

# SEC's company_tickers.json has no industry classification to filter on the
# way Form D and Form 5500 do, so exchange-traded funds are excluded by name
# instead - the only signal this feed carries. "ETF" is unambiguous: no
# operating company is named that. "Trust" is not included here on its own,
# since real operating companies (REITs above all) legitimately use it.
_SEC_FUND_NAME = re.compile(r"\bETF\b", re.I)


def _group_key(group: str) -> str:
    return re.sub(r"\s+", " ", (group or "").replace("&", "and")).strip().lower()
_OFFICER_RELATIONSHIPS = ("executive officer", "director")


def _sec_headers() -> dict[str, str]:
    from app.services.roster_watch import sec_user_agent

    return {"User-Agent": sec_user_agent(), "Accept-Encoding": "gzip, deflate"}


def recent_form_d_quarters(count: int | None = None) -> list[str]:
    """Completed quarters to ingest, newest first, e.g. ['2026q1', '2025q4'].

    SEC publishes a quarter's data set only after that quarter closes, so the
    quarter in progress is always a 404 and is never listed."""
    months = FORM_D_MONTHS if count is None else count * 3
    now = datetime.now(timezone.utc)
    quarters: list[str] = []
    year, quarter = now.year, (now.month - 1) // 3 + 1
    quarter -= 1                     # the current quarter is not published yet
    if quarter == 0:
        year, quarter = year - 1, 4
    for _ in range(max(1, months // 3)):
        quarters.append(f"{year}q{quarter}")
        quarter -= 1
        if quarter == 0:
            year, quarter = year - 1, 4
    return quarters


def _norm_name(name: str) -> str:
    text = re.sub(r"\s+", " ", (name or "").strip())
    text = re.sub(r"[,.]?\s*(inc|llc|l\.l\.c|ltd|limited|corp|corporation|co|plc|lp|l\.p)\.?$", "", text, flags=re.I)
    return text.strip(" ,.")


def _title_case(name: str) -> str:
    """Registers publish shouty names ('DICKERSON PIKE LLC'); a member should
    see 'Dickerson Pike LLC'. Existing mixed-case names are left alone."""
    text = (name or "").strip()
    if not text or text != text.upper():
        return text
    return re.sub(r"\b([A-Z])([A-Z']*)\b", lambda m: m.group(1) + m.group(2).lower(), text)


async def _http_get(url: str, *, timeout: float = 120.0) -> bytes:
    import httpx

    pause = float(os.getenv("ROSTER_SEC_PAUSE_SEC", "0.12") or 0)
    if pause > 0:
        await asyncio.sleep(pause)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(url, headers=_sec_headers())
        response.raise_for_status()
        return response.content


async def _record_ingest(source: str, batch_key: str, seen: int, written: int, status: str, detail: str = "") -> None:
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO company_register_ingests (source, batch_key, rows_seen, rows_written, status, detail)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(source, batch_key) DO UPDATE SET
                 rows_seen=excluded.rows_seen, rows_written=excluded.rows_written,
                 status=excluded.status, detail=excluded.detail, completed_at=CURRENT_TIMESTAMP""",
            (source, batch_key, seen, written, status, detail[:500]),
        )
        await db.commit()
    finally:
        await db.close()


async def _already_ingested(source: str, batch_key: str) -> bool:
    db = await get_db()
    try:
        row = await (await db.execute(
            "SELECT 1 FROM company_register_ingests WHERE source=? AND batch_key=? AND status='ok'",
            (source, batch_key),
        )).fetchone()
        return bool(row)
    finally:
        await db.close()


async def upsert_companies(rows: Iterable[dict[str, Any]]) -> int:
    """Insert or refresh register rows. A row already present keeps its
    first_seen_at and only gains newer facts."""
    written = 0
    db = await get_db()
    try:
        for row in rows:
            await db.execute(
                """INSERT INTO company_register (
                       source, source_key, tier, country, company_name, company_domain,
                       sector_code, sector_label, region, employees, employees_source,
                       last_event_at, last_event_amount, last_event_kind, metadata_json,
                       prominence_rank
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(source, source_key) DO UPDATE SET
                       company_name=excluded.company_name,
                       tier=excluded.tier,
                       company_domain=COALESCE(excluded.company_domain, company_register.company_domain),
                       sector_code=COALESCE(excluded.sector_code, company_register.sector_code),
                       sector_label=COALESCE(excluded.sector_label, company_register.sector_label),
                       region=COALESCE(excluded.region, company_register.region),
                       employees=COALESCE(excluded.employees, company_register.employees),
                       employees_source=COALESCE(excluded.employees_source, company_register.employees_source),
                       last_event_at=MAX(COALESCE(excluded.last_event_at, ''), COALESCE(company_register.last_event_at, '')),
                       last_event_amount=COALESCE(excluded.last_event_amount, company_register.last_event_amount),
                       last_event_kind=COALESCE(excluded.last_event_kind, company_register.last_event_kind),
                       metadata_json=COALESCE(excluded.metadata_json, company_register.metadata_json),
                       prominence_rank=COALESCE(excluded.prominence_rank, company_register.prominence_rank),
                       updated_at=CURRENT_TIMESTAMP""",
                (
                    row["source"], row["source_key"], row["tier"], row.get("country") or "US",
                    row["company_name"], row.get("company_domain"),
                    row.get("sector_code"), row.get("sector_label"), row.get("region"),
                    row.get("employees"), row.get("employees_source"),
                    row.get("last_event_at"), row.get("last_event_amount"), row.get("last_event_kind"),
                    json.dumps(row["metadata"]) if row.get("metadata") else None,
                    row.get("prominence_rank"),
                ),
            )
            written += 1
        await db.commit()
    finally:
        await db.close()
    return written


# Who actually reads a cold email is not who a filing names. A statutory
# filing lists the people the law requires - the board above all - so the
# register is an index of companies and of doors into them, not of the people
# who would run a project. These four levels let the app say which it is
# holding, and specifically keep a Form 990 "Director" (a board seat) apart
# from a "Director of Operations" (a job).
_BOARD_TITLE = re.compile(
    r"\b(trustee|board member|board of directors|chair(man|person|woman)?|vice chair|governor|"
    r"regent|overseer|incorporator)\b|^director$|^member$|^board\b", re.I)
_WORKING_TITLE = re.compile(
    r"\b(vice president|vp|svp|evp|avp|manager|managing|head of|director of|lead|"
    r"associate|analyst|coordinator|specialist|partner|principal of)\b", re.I)
_EXEC_TITLE = re.compile(
    r"\b(ceo|cfo|coo|cto|cio|cmo|chro|chief|president|executive director|treasurer|"
    r"secretary|general counsel|executive officer|head of school|superintendent)\b", re.I)


def classify_person_level(title: str | None) -> str:
    """board | executive | working | unknown.

    Order matters. A working phrase is checked before the board pattern so
    "Director of Finance" is a job, while a bare "Director" falls through to
    the board rule - which is what a 990 means by it 97,292 times over.
    """
    text = (title or "").strip()
    if not text:
        return "unknown"
    if _WORKING_TITLE.search(text) and not _BOARD_TITLE.match(text):
        return "working"
    if _EXEC_TITLE.search(text):
        return "executive"
    if _BOARD_TITLE.search(text):
        return "board"
    return "unknown"


async def _attach_people(people_by_key: dict[tuple[str, str], list[dict[str, Any]]]) -> int:
    if not people_by_key:
        return 0
    attached = 0
    db = await get_db()
    try:
        for (source, source_key), people in people_by_key.items():
            row = await (await db.execute(
                "SELECT id FROM company_register WHERE source=? AND source_key=?", (source, source_key),
            )).fetchone()
            if not row:
                continue
            register_id = int(row["id"])
            for person in people:
                await db.execute(
                    """INSERT INTO company_register_people
                           (register_id, full_name, relationship, observed_at, source_url, person_level)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(register_id, full_name) DO UPDATE SET
                         relationship=COALESCE(excluded.relationship, company_register_people.relationship),
                         person_level=COALESCE(excluded.person_level, company_register_people.person_level),
                         observed_at=MAX(COALESCE(excluded.observed_at, ''), COALESCE(company_register_people.observed_at, ''))""",
                    (register_id, person["full_name"], person.get("relationship"),
                     person.get("observed_at"), person.get("source_url"),
                     classify_person_level(person.get("relationship"))),
                )
                attached += 1
            await db.execute(
                "UPDATE company_register SET officer_count=(SELECT COUNT(*) FROM company_register_people WHERE register_id=?) WHERE id=?",
                (register_id, register_id),
            )
        await db.commit()
    finally:
        await db.close()
    return attached


async def ingest_sec_public() -> dict[str, Any]:
    """Every US listed company from SEC's ticker file. Sector is filled in
    later by backfill_sec_sectors; SEC publishes no bulk SIC file.

    SEC serves this file with the companies already ordered by market cap,
    largest first - a fact the browsing endpoint has nothing else to rank
    listed companies by, since none of them carry a filing date the way a
    DOL, IRS or Companies House row does. Losing that order was why Meta and
    every other big listed company sorted no differently from an obscure
    micro-cap: alphabetically, wherever that happened to land among 200k+
    rows with real recent activity. Keeping the file's own order as
    ``prominence_rank`` is what lets search_register put them back where a
    member expects to find them.
    """
    # SEC's ticker file lists every registered security, not just operating
    # companies: ETFs ("21Shares Dogecoin ETF") sit alongside real companies
    # ("1stDibs.com, Inc.") with nothing distinguishing them in this feed
    # except the name. Excluded the same way Form D and Form 5500 already
    # exclude pooled/financial vehicles - just by name here, since this file
    # carries no industry classification to filter on instead.
    batch = datetime.now(timezone.utc).strftime("%Y-%m")
    try:
        raw = await _http_get(SEC_TICKERS_URL, timeout=60.0)
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        await _record_ingest("sec_public", batch, 0, 0, "failed", f"{type(exc).__name__}: {exc}")
        return {"ok": False, "source": "sec_public", "error": str(exc)}
    rows = []
    skipped_etf = 0
    rank = 0
    for item in (data or {}).values():
        if not isinstance(item, dict):
            continue
        cik = str(item.get("cik_str") or "").strip()
        name = _title_case(str(item.get("title") or "").strip())
        if not cik or not name:
            continue
        if _SEC_FUND_NAME.search(name):
            skipped_etf += 1
            continue
        rows.append({
            "source": "sec_public", "source_key": cik.zfill(10), "tier": "us_public", "country": "US",
            "company_name": name,
            "metadata": {"ticker": str(item.get("ticker") or "").strip(), "cik": cik},
            "prominence_rank": rank,
        })
        rank += 1
    written = await upsert_companies(rows)
    await _record_ingest("sec_public", batch, len(rows), written, "ok", f"{skipped_etf} fund/ETF ticker(s) excluded")
    return {"ok": True, "source": "sec_public", "seen": len(rows), "written": written}


def parse_form_d_zip(payload: bytes) -> tuple[list[dict[str, Any]], dict[tuple[str, str], list[dict[str, Any]]]]:
    """Issuers meeting the size/recency/industry bar, plus their named
    executive officers. Pure: the network and database stay outside."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30 * FORM_D_MONTHS)).strftime("%Y-%m-%d")

    def table(zf: zipfile.ZipFile, name: str) -> list[dict[str, str]]:
        member = next((n for n in zf.namelist() if n.upper().endswith(f"{name}.TSV")), None)
        if not member:
            return []
        with zf.open(member) as handle:
            text = io.TextIOWrapper(handle, encoding="utf-8", errors="replace")
            return list(csv.DictReader(text, delimiter="\t"))

    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        issuers = table(zf, "ISSUERS")
        offerings = {row["ACCESSIONNUMBER"]: row for row in table(zf, "OFFERING") if row.get("ACCESSIONNUMBER")}
        persons = table(zf, "RELATEDPERSONS")

    kept: dict[str, dict[str, Any]] = {}
    for row in issuers:
        accession = (row.get("ACCESSIONNUMBER") or "").strip()
        if not accession or (row.get("IS_PRIMARYISSUER_FLAG") or "").upper() != "YES":
            continue
        offering = offerings.get(accession) or {}
        group = (offering.get("INDUSTRYGROUPTYPE") or "").strip()
        if _group_key(group) in FORM_D_SKIP_GROUPS:
            continue
        try:
            amount = float(offering.get("TOTALOFFERINGAMOUNT") or 0)
        except ValueError:
            amount = 0.0
        sold = offering.get("TOTALAMOUNTSOLD")
        try:
            amount = max(amount, float(sold or 0))
        except ValueError:
            pass
        if amount < MIN_OFFERING_USD:
            continue
        sale_date = (offering.get("SALE_DATE") or "").strip()
        iso_date = ""
        for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%m/%d/%Y"):
            try:
                iso_date = datetime.strptime(sale_date, fmt).strftime("%Y-%m-%d")
                break
            except ValueError:
                continue
        if iso_date and iso_date < cutoff:
            continue
        name = _title_case((row.get("ENTITYNAME") or "").strip())
        cik = (row.get("CIK") or "").strip().zfill(10)
        if not name or not cik:
            continue
        existing = kept.get(cik)
        if existing and (existing.get("last_event_at") or "") >= iso_date:
            continue
        kept[cik] = {
            "source": "sec_form_d", "source_key": cik, "tier": "us_private", "country": "US",
            "company_name": name,
            "sector_label": group or None,
            "region": (row.get("STATEORCOUNTRYDESCRIPTION") or "").strip().title() or None,
            "last_event_at": iso_date or None,
            "last_event_amount": amount,
            "last_event_kind": "reg_d_offering",
            "metadata": {
                "accession": accession,
                "revenue_range": (offering.get("REVENUERANGE") or "").strip() or None,
                "entity_type": (row.get("ENTITYTYPE") or "").strip() or None,
                "year_of_inc": (row.get("YEAROFINC_VALUE_ENTERED") or "").strip() or None,
                "city": (row.get("CITY") or "").strip().title() or None,
            },
        }

    accession_to_cik = {item["metadata"]["accession"]: cik for cik, item in kept.items()}
    people: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in persons:
        cik = accession_to_cik.get((row.get("ACCESSIONNUMBER") or "").strip())
        if not cik:
            continue
        relationships = [
            (row.get(f"RELATIONSHIP_{i}") or "").strip()
            for i in (1, 2, 3)
        ]
        relationship = next((r for r in relationships if r), "")
        if not any(r.lower() in _OFFICER_RELATIONSHIPS for r in relationships if r):
            continue
        full_name = " ".join(part for part in [
            (row.get("FIRSTNAME") or "").strip(),
            (row.get("MIDDLENAME") or "").strip(),
            (row.get("LASTNAME") or "").strip(),
        ] if part)
        if len(full_name.split()) < 2:
            continue
        accession = (row.get("ACCESSIONNUMBER") or "").strip()
        people.setdefault(("sec_form_d", cik), []).append({
            "full_name": _title_case(full_name),
            "relationship": relationship or None,
            "observed_at": kept[cik].get("last_event_at"),
            "source_url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/",
        })
    return list(kept.values()), people


async def ingest_form_d(quarter: str, *, force: bool = False) -> dict[str, Any]:
    if not force and await _already_ingested("sec_form_d", quarter):
        return {"ok": True, "source": "sec_form_d", "quarter": quarter, "skipped": "already ingested"}
    try:
        payload = await _http_get(FORM_D_URL.format(quarter=quarter), timeout=180.0)
        companies, people = await asyncio.to_thread(parse_form_d_zip, payload)
    except Exception as exc:
        # 404 means SEC has not published that quarter; record it so the log
        # reads honestly, and let the caller move on to an older quarter.
        missing = "404" in str(exc)
        await _record_ingest("sec_form_d", quarter, 0, 0, "unpublished" if missing else "failed",
                             f"{type(exc).__name__}: {exc}")
        return {"ok": False, "source": "sec_form_d", "quarter": quarter,
                "unpublished": missing, "error": str(exc)}
    written = await upsert_companies(companies)
    attached = await _attach_people(people)
    await _record_ingest("sec_form_d", quarter, len(companies), written, "ok", f"{attached} officer(s)")
    return {"ok": True, "source": "sec_form_d", "quarter": quarter,
            "seen": len(companies), "written": written, "officers": attached}



# Form 5500 carries the sponsor's NAICS business code. The two-digit sector is
# the only part with a stable published name, and it is the level the register
# filters on, so the full code is kept for evidence and the sector is labelled
# from its prefix rather than shipping a 1,000-row NAICS table.
_NAICS_SECTORS: dict[str, str] = {
    "11": "Agriculture, Forestry, Fishing and Hunting",
    "21": "Mining, Quarrying, and Oil and Gas Extraction",
    "22": "Utilities",
    "23": "Construction",
    "31": "Manufacturing", "32": "Manufacturing", "33": "Manufacturing",
    "42": "Wholesale Trade",
    "44": "Retail Trade", "45": "Retail Trade",
    "48": "Transportation and Warehousing", "49": "Transportation and Warehousing",
    "51": "Information",
    "52": "Finance and Insurance",
    "53": "Real Estate and Rental and Leasing",
    "54": "Professional, Scientific, and Technical Services",
    "55": "Management of Companies and Enterprises",
    "56": "Administrative and Support and Waste Management",
    "61": "Educational Services",
    "62": "Health Care and Social Assistance",
    "71": "Arts, Entertainment, and Recreation",
    "72": "Accommodation and Food Services",
    "81": "Other Services (except Public Administration)",
    "92": "Public Administration",
}

# A plan sponsor below this has too few employees to run a ten-week
# engagement with a student team.
_FORM_5500_MIN_PARTICIPANTS = 50

# 525 is Funds, Trusts and Other Financial Vehicles: the pooled-vehicle
# analogue of the Form D funds already excluded. They sponsor plans without
# being an operating employer anyone can pitch.
_FORM_5500_SKIP_SECTORS = {"525"}


def parse_form_5500(handle: Any) -> tuple[list[dict[str, Any]], dict[tuple[str, str], list[dict[str, Any]]]]:
    """Turn one year of Form 5500 filings into register rows keyed by EIN.

    Every US employer sponsoring a benefit plan files this, which is the
    closest thing the US has to the UK's universal register: the SEC only ever
    sees companies that listed shares or raised under Reg D, while this sees
    ordinary private employers. It also carries the one number no free US
    source publishes - TOT_ACTIVE_PARTCP_CNT, the active participant count,
    filed by the employer itself.

    A sponsor files one row per plan and may file for several plan years at
    once, so rows are folded to one company per EIN, keeping the filing with
    the most recent plan year.
    """
    reader = csv.DictReader(io.TextIOWrapper(handle, encoding="latin-1", errors="replace"))
    best: dict[str, dict[str, Any]] = {}
    people: dict[tuple[str, str], list[dict[str, Any]]] = {}

    for row in reader:
        name = (row.get("SPONSOR_DFE_NAME") or "").strip()
        ein = re.sub(r"\D", "", row.get("SPONS_DFE_EIN") or "")
        if not name or not ein:
            continue
        # Line A of the form: 1 multiemployer, 2 single-employer,
        # 3 multiple-employer, 4 direct filing entity. Only a single-employer
        # plan means the participant count describes this company's own staff.
        # The others are union and association trusts whose participants come
        # from many employers - the National Education Association's 2.5M, for
        # instance, which would otherwise top the register as one "company".
        if (row.get("TYPE_PLAN_ENTITY_CD") or "").strip() != "2":
            continue
        count = (row.get("TOT_ACTIVE_PARTCP_CNT") or "").strip()
        participants = int(count) if count.isdigit() else 0
        if participants < _FORM_5500_MIN_PARTICIPANTS:
            continue
        code = re.sub(r"\D", "", row.get("BUSINESS_CODE") or "")
        if code[:3] in _FORM_5500_SKIP_SECTORS:
            continue
        plan_year = (row.get("FORM_PLAN_YEAR_BEGIN_DATE") or "").strip()[:10] or None
        prior = best.get(ein)
        if prior and (prior.get("last_event_at") or "") >= (plan_year or ""):
            continue
        state = (row.get("SPONS_DFE_MAIL_US_STATE") or "").strip().upper() or None
        city = _title_case((row.get("SPONS_DFE_MAIL_US_CITY") or "").strip())
        best[ein] = {
            "source": "dol_5500",
            "source_key": ein,
            "tier": "us_employer",
            "country": "US",
            "company_name": _title_case(name),
            "sector_code": code or None,
            "sector_label": _NAICS_SECTORS.get(code[:2]),
            "region": state,
            # Filed by the employer under penalty of perjury, so it is
            # evidence rather than an estimate - but it counts plan
            # participants, not staff, which employees_source records.
            "employees": participants,
            "employees_source": "form_5500_active_participants",
            "last_event_at": plan_year,
            "last_event_kind": "benefit_plan_filed",
            "metadata": {
                "city": city or None,
                "plan_name": (row.get("PLAN_NAME") or "").strip()[:120] or None,
                "ein": ein,
            },
        }
        signer = (row.get("SPONS_SIGNED_NAME") or "").strip()
        if signer and len(signer.split()) >= 2:
            people[("dol_5500", ein)] = [{
                "full_name": _title_case(signer),
                "relationship": "Signed the plan filing",
                "observed_at": (row.get("SPONS_SIGNED_DATE") or "").strip()[:10] or None,
                "source_url": "https://www.efast.dol.gov/5500Search/",
            }]
    return list(best.values()), people


async def ingest_form_5500(year: int, *, force: bool = False) -> dict[str, Any]:
    """One year of Form 5500 filings. The file is ~10 MB zipped."""
    batch = f"{year}"
    if not force and await _already_ingested("dol_5500", batch):
        return {"ok": True, "source": "dol_5500", "batch": batch, "skipped": "already ingested"}
    url = f"https://askebsa.dol.gov/FOIA%20Files/{year}/Latest/F_5500_{year}_latest.zip"
    try:
        payload = await _http_get(url, timeout=300.0)
    except Exception as exc:
        # The current year is published as it is filed; a year with nothing
        # yet simply is not there, exactly like an unpublished Form D quarter.
        await _record_ingest("dol_5500", batch, 0, 0, "unpublished", str(exc)[:200])
        return {"ok": False, "source": "dol_5500", "batch": batch, "error": str(exc)[:200]}
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
        if not names:
            await _record_ingest("dol_5500", batch, 0, 0, "empty", "no csv in archive")
            return {"ok": False, "source": "dol_5500", "batch": batch, "error": "no csv in archive"}
        with archive.open(names[0]) as handle:
            rows, people = parse_form_5500(handle)
    written = await upsert_companies(rows)
    attached = await _attach_people(people)
    await _record_ingest("dol_5500", batch, len(rows), written, "ok")
    return {"ok": True, "source": "dol_5500", "batch": batch, "seen": len(rows),
            "written": written, "officers": attached}


def parse_uk_bulk(handle: Any) -> Iterable[dict[str, Any]]:
    """Stream the Companies House one-file CSV (2.8 GB uncompressed, 5.7M
    rows) and yield only active companies above the small-company accounts
    thresholds that are operating businesses. Streaming keeps peak memory at
    one row on a t3.small."""
    reader = csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8", errors="replace"))
    columns = {name.strip(): name for name in (reader.fieldnames or [])}

    def field(row: dict[str, str], key: str) -> str:
        return (row.get(columns.get(key, key)) or "").strip()

    for row in reader:
        if field(row, "CompanyStatus") != "Active":
            continue
        band = field(row, "Accounts.AccountCategory").upper()
        if band not in UK_SIZE_BANDS:
            continue
        sic = field(row, "SICCode.SicText_1")
        code = sic.split(" - ", 1)[0].strip()
        if any(code.startswith(prefix) for prefix in UK_SKIP_SIC_PREFIXES):
            continue
        number = field(row, "CompanyNumber")
        name = _title_case(field(row, "CompanyName"))
        if not number or not name:
            continue
        yield {
            "source": "companies_house", "source_key": number, "tier": "uk", "country": "GB",
            "company_name": name,
            "sector_code": code or None,
            "sector_label": (sic.split(" - ", 1)[1].strip() if " - " in sic else sic) or None,
            "region": field(row, "RegAddress.PostTown").title() or None,
            "employees_source": "companies_house_account_category",
            "last_event_at": _uk_date(field(row, "Accounts.LastMadeUpDate")),
            "last_event_kind": "accounts_filed",
            "metadata": {
                "size_band": UK_BAND_LABEL.get(band, band.lower()),
                "account_category": band,
                "company_category": field(row, "CompanyCategory"),
                "incorporated": _uk_date(field(row, "IncorporationDate")),
                "uri": field(row, "URI") or None,
            },
        }


def _uk_date(value: str) -> str | None:
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


async def ingest_companies_house(*, force: bool = False, batch_rows: int = 2000) -> dict[str, Any]:
    """Monthly Companies House bulk file. Downloaded to a temp file and
    streamed out of the zip, never held in memory or left on disk."""
    import tempfile

    import httpx

    batch_key = datetime.now(timezone.utc).strftime("%Y-%m-01")
    if not force and await _already_ingested("companies_house", batch_key):
        return {"ok": True, "source": "companies_house", "batch": batch_key, "skipped": "already ingested"}
    url = UK_BULK_URL.format(date=batch_key)
    seen = written = 0
    try:
        with tempfile.NamedTemporaryFile(suffix=".zip") as archive:
            async with httpx.AsyncClient(timeout=600.0, follow_redirects=True) as client:
                async with client.stream("GET", url, headers={"User-Agent": _sec_headers()["User-Agent"]}) as response:
                    response.raise_for_status()
                    async for chunk in response.aiter_bytes(1 << 20):
                        archive.write(chunk)
            archive.flush()
            with zipfile.ZipFile(archive.name) as zf:
                member = zf.namelist()[0]
                with zf.open(member) as handle:
                    batch: list[dict[str, Any]] = []
                    for company in parse_uk_bulk(handle):
                        seen += 1
                        batch.append(company)
                        if len(batch) >= batch_rows:
                            written += await upsert_companies(batch)
                            batch = []
                            await asyncio.sleep(0)
                    if batch:
                        written += await upsert_companies(batch)
    except Exception as exc:
        missing = "404" in str(exc)
        await _record_ingest("companies_house", batch_key, seen, written,
                             "unpublished" if missing else "failed", f"{type(exc).__name__}: {exc}")
        return {"ok": False, "source": "companies_house", "batch": batch_key,
                "unpublished": missing, "error": str(exc)}
    await _record_ingest("companies_house", batch_key, seen, written, "ok")
    return {"ok": True, "source": "companies_house", "batch": batch_key, "seen": seen, "written": written}


# NTEE major group. The letter is the only part of the code with a published
# name, and it is the level a member filters on.
_NTEE_GROUPS: dict[str, str] = {
    "A": "Arts, Culture and Humanities", "B": "Education", "C": "Environment",
    "D": "Animal Related", "E": "Health Care", "F": "Mental Health",
    "G": "Disease and Disorders", "H": "Medical Research", "I": "Crime and Legal",
    "J": "Employment", "K": "Food and Agriculture", "L": "Housing and Shelter",
    "M": "Public Safety and Disaster Relief", "N": "Recreation and Sports",
    "O": "Youth Development", "P": "Human Services", "Q": "International Affairs",
    "R": "Civil Rights and Advocacy", "S": "Community Improvement",
    "T": "Philanthropy and Grantmaking", "U": "Science and Technology",
    "V": "Social Science", "W": "Public and Societal Benefit", "X": "Religion",
    "Y": "Mutual Benefit", "Z": "Unknown",
}

IRS_EXTRACT_URL = "https://www.irs.gov/pub/irs-soi/{yy}eoextract990.zip"
IRS_BMF_URLS = [f"https://www.irs.gov/pub/irs-soi/eo{n}.csv" for n in (1, 2, 3, 4)]

# Being large is not the same as being able to buy anything. These three
# thresholds together say: big enough to fund a project, already in the habit
# of paying outside firms for advice, and not a pass-through whose "fees" are
# really programme costs.
_IRS_MIN_REVENUE = 5_000_000
_IRS_MIN_ADVICE_SPEND = 50_000
_IRS_MAX_ADVICE_SHARE = 0.25


def _irs_money(row: dict[str, str], key: str) -> int:
    value = (row.get(key) or "0").strip()
    try:
        return int(float(value))
    except ValueError:
        return 0


def qualifying_990_filers(handle: Any) -> dict[str, dict[str, int]]:
    """EINs that can pay for advice and already do, from the IRS 990 extract.

    The extract carries the money but no names, so this returns a lookup the
    Business Master File is then joined onto.

    Spend counts management, legal and accounting fees only.
    feesforsrvcothr is deliberately excluded: at health plans and insurers it
    absorbs medical claims, and including it put UCare Minnesota at $5.8bn of
    "consulting" - a pass-through, not a buyer of advice.
    """
    out: dict[str, dict[str, int]] = {}
    reader = csv.DictReader(io.TextIOWrapper(handle, encoding="latin-1", errors="replace"))
    for row in reader:
        ein = re.sub(r"\D", "", row.get("EIN") or "").zfill(9)
        if len(ein) != 9:
            continue
        revenue = _irs_money(row, "totrevenue")
        if revenue < _IRS_MIN_REVENUE:
            continue
        advice = (_irs_money(row, "feesforsrvcmgmt")
                  + _irs_money(row, "legalfees")
                  + _irs_money(row, "accntingfees"))
        if advice < _IRS_MIN_ADVICE_SPEND or advice > revenue * _IRS_MAX_ADVICE_SHARE:
            continue
        out[ein] = {
            "revenue": revenue,
            "advice": advice,
            # Part V line 2a: employees on the organisation's own W-3s.
            "employees": _irs_money(row, "noemplyeesw3cnt"),
        }
    return out


def _bmf_rows_for(handle: Any, wanted: dict[str, dict[str, int]]) -> Iterable[dict[str, Any]]:
    """Join Business Master File names onto the qualifying EINs, streamed."""
    reader = csv.DictReader(io.TextIOWrapper(handle, encoding="latin-1", errors="replace"))
    for row in reader:
        ein = re.sub(r"\D", "", row.get("EIN") or "").zfill(9)
        money = wanted.get(ein)
        if not money:
            continue
        name = (row.get("NAME") or "").strip()
        if not name:
            continue
        ntee = (row.get("NTEE_CD") or "").strip().upper()
        period = (row.get("TAX_PERIOD") or "").strip()
        filed = f"{period[:4]}-{period[4:6]}-01" if len(period) >= 6 and period.isdigit() else None
        revenue, advice = money["revenue"], money["advice"]
        yield {
            "source": "irs_990",
            "source_key": ein,
            "tier": "us_nonprofit",
            "country": "US",
            "company_name": _title_case(name),
            "sector_code": ntee or None,
            "sector_label": _NTEE_GROUPS.get(ntee[:1]) if ntee else None,
            "region": (row.get("STATE") or "").strip().upper() or None,
            "employees": money["employees"] or None,
            "employees_source": "form_990_w3_employee_count" if money["employees"] else None,
            "last_event_at": filed,
            "last_event_kind": "form_990_filed",
            "metadata": {
                "city": _title_case((row.get("CITY") or "").strip()) or None,
                "revenue_range": f"${revenue / 1_000_000:,.1f}M revenue",
                "buys_outside_advice": (
                    f"${advice / 1_000_000:,.1f}M/yr on management, legal and accounting fees"
                    if advice >= 1_000_000 else f"${advice / 1_000:,.0f}k/yr on outside professional fees"
                ),
                "ein": ein,
            },
        }


async def _stream_to_temp(url: str, suffix: str):
    """Download a large file to a temp handle without holding it in memory."""
    import tempfile

    import httpx

    handle = tempfile.NamedTemporaryFile(suffix=suffix)
    async with httpx.AsyncClient(timeout=600.0, follow_redirects=True) as client:
        async with client.stream("GET", url, headers={"User-Agent": _sec_headers()["User-Agent"]}) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes(1 << 20):
                handle.write(chunk)
    handle.flush()
    return handle


async def ingest_irs_990(year: int | None = None, *, force: bool = False,
                         batch_rows: int = 2000) -> dict[str, Any]:
    """US nonprofits that can pay for outside advice and demonstrably do.

    Two free IRS files joined on EIN: the 990 financial extract, which has the
    money but no names, and the Exempt Organizations Business Master File,
    which has the names. Both are streamed to temp files, so peak memory is
    the qualifying set rather than the 330 MB of source data.
    """
    target = year or (datetime.now(timezone.utc).year - 1)
    batch_key = str(target)
    if not force and await _already_ingested("irs_990", batch_key):
        return {"ok": True, "source": "irs_990", "batch": batch_key, "skipped": "already ingested"}
    seen = written = 0
    try:
        extract = await _stream_to_temp(IRS_EXTRACT_URL.format(yy=f"{target % 100:02d}"), ".zip")
        try:
            with zipfile.ZipFile(extract.name) as archive:
                member = next(n for n in archive.namelist() if n.lower().endswith(".csv"))
                with archive.open(member) as handle:
                    qualifying = qualifying_990_filers(handle)
        finally:
            extract.close()
        if not qualifying:
            await _record_ingest("irs_990", batch_key, 0, 0, "empty", "no qualifying filers")
            return {"ok": False, "source": "irs_990", "batch": batch_key, "error": "no qualifying filers"}

        for url in IRS_BMF_URLS:
            bmf = await _stream_to_temp(url, ".csv")
            try:
                with open(bmf.name, "rb") as handle:
                    batch: list[dict[str, Any]] = []
                    for company in _bmf_rows_for(handle, qualifying):
                        seen += 1
                        batch.append(company)
                        if len(batch) >= batch_rows:
                            written += await upsert_companies(batch)
                            batch = []
                            await asyncio.sleep(0)
                    if batch:
                        written += await upsert_companies(batch)
            finally:
                bmf.close()
    except Exception as exc:
        missing = "404" in str(exc)
        await _record_ingest("irs_990", batch_key, seen, written,
                             "unpublished" if missing else "failed", f"{type(exc).__name__}: {exc}")
        return {"ok": False, "source": "irs_990", "batch": batch_key, "error": str(exc)[:200]}
    await _record_ingest("irs_990", batch_key, seen, written, "ok")
    return {"ok": True, "source": "irs_990", "batch": batch_key,
            "qualifying": len(qualifying), "seen": seen, "written": written}


# Part VII Section A of Form 990: the officers, directors, trustees and key
# employees the organisation named on its own return, each with the title it
# gave them. Parsed with regex rather than an XML tree because the batches
# hold ~17,000 filings each and only a handful of fields are wanted.
_PART_VII = re.compile(r"<Form990PartVIISectionAGrp>(.*?)</Form990PartVIISectionAGrp>", re.S)
_PERSON_NM = re.compile(r"<PersonNm>(.*?)</PersonNm>")
_TITLE_TXT = re.compile(r"<TitleTxt>(.*?)</TitleTxt>")
_FILING_EIN = re.compile(r"<EIN>(\d{9})</EIN>")
_IRS_XML_BATCH = "https://apps.irs.gov/pub/epostcard/990/xml/{year}/{year}_TEOS_XML_{month:02d}{letter}.zip"
# stored, deflate, bzip2, lzma. Deflate64 (9) appears in some IRS batches and
# the standard library cannot decompress it.
_READABLE_ZIP_METHODS = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA}


def _clean_xml_text(value: str) -> str:
    """XML escapes are not part of the text. "PRESIDENT &amp; CEO" is a title
    nobody wrote and nobody should read."""
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def parse_part_vii(xml: str) -> tuple[str, list[dict[str, Any]]]:
    """Return (EIN, named officers) for one filing."""
    found = _FILING_EIN.search(xml)
    if not found:
        return "", []
    people: list[dict[str, Any]] = []
    seen: set[str] = set()
    for block in _PART_VII.findall(xml):
        name = _PERSON_NM.search(block)
        if not name:
            continue
        full = _clean_xml_text(name.group(1))
        key = full.casefold()
        if not full or key in seen or len(full.split()) < 2:
            continue
        title = _TITLE_TXT.search(block)
        role = _clean_xml_text(title.group(1))[:120] if title else ""
        # "FORMER" is the organisation saying this person has left, the same
        # evidence a Companies House resignation date gives.
        if role.upper().startswith("FORMER"):
            continue
        seen.add(key)
        people.append({
            "full_name": _title_case(full),
            "relationship": role or None,
            "observed_at": None,
            "source_url": "https://www.irs.gov/charities-non-profits/tax-exempt-organization-search",
        })
    return found.group(1), people[:40]


def _batch_filings(archive_path: str) -> Iterable[str]:
    """Yield every filing in one IRS batch, whatever it was compressed with.

    About half of a year's filings are written with deflate64, which the
    standard library cannot decompress - skipping them silently lost 243,823
    filings on the first live run. The system unzip reads them, but spawning
    it per filing meant a quarter of a million processes and the run timed
    out. So a batch containing any unreadable member is extracted once, in a
    single process, into a temp directory that is deleted immediately after.
    """
    import shutil
    import subprocess
    import tempfile

    with zipfile.ZipFile(archive_path) as zf:
        members = [i for i in zf.infolist() if i.filename.endswith(".xml")]
        if all(i.compress_type in _READABLE_ZIP_METHODS for i in members):
            for info in members:
                yield zf.read(info.filename).decode("utf-8", "replace")
            return

    if not shutil.which("unzip"):
        return
    workdir = tempfile.mkdtemp(prefix="irs990-")
    try:
        done = subprocess.run(["unzip", "-q", "-o", archive_path, "-d", workdir],
                              capture_output=True, timeout=900, check=False)
        # unzip returns 1 for warnings it recovered from, which is still a
        # usable extraction.
        if done.returncode > 1:
            return
        for entry in sorted(Path(workdir).rglob("*.xml")):
            try:
                yield entry.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
    except (OSError, subprocess.SubprocessError):
        return
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


async def ingest_990_officers(year: int | None = None, *, force: bool = False) -> dict[str, Any]:
    """Name the people running every nonprofit already in the register.

    Finding contacts is the slowest part of outreach, and Part VII is a list
    of them with titles that the organisation filed itself. The IRS retired
    per-filing XML downloads, so the only route is the annual batches - about
    fifteen zips of 100 MB. Each is streamed to a temp file, scanned for the
    EINs already in the register, and deleted, so nothing accumulates on the
    data volume and only matching filings are parsed.
    """
    target = year or (datetime.now(timezone.utc).year - 1)
    batch_key = f"officers-{target}"
    if not force and await _already_ingested("irs_990", batch_key):
        return {"ok": True, "source": "irs_990", "batch": batch_key, "skipped": "already ingested"}

    db = await get_db()
    try:
        rows = await (await db.execute(
            "SELECT source_key FROM company_register WHERE source='irs_990' AND officer_count=0"
        )).fetchall()
    finally:
        await db.close()
    wanted = {row["source_key"] for row in rows}
    if not wanted:
        return {"ok": True, "source": "irs_990", "batch": batch_key, "skipped": "every filer already has officers"}

    scanned = matched = attached = batches = 0
    for month in range(1, 13):
        for letter in ("A", "B", "C", "D"):
            url = _IRS_XML_BATCH.format(year=target, month=month, letter=letter)
            try:
                archive = await _stream_to_temp(url, ".zip")
            except Exception:
                # Batches are not evenly numbered - some months have only an
                # A, others run to D. A missing one is normal, not a failure.
                break
            batches += 1
            try:
                found: dict[tuple[str, str], list[dict[str, Any]]] = {}
                for raw in _batch_filings(archive.name):
                    scanned += 1
                    ein, people = parse_part_vii(raw)
                    if not people or ein not in wanted:
                        continue
                    found[("irs_990", ein)] = people
                    matched += 1
                if found:
                    attached += await _attach_people(found)
                    wanted -= {key[1] for key in found}
            finally:
                archive.close()
            await asyncio.sleep(0)
    await _record_ingest("irs_990", batch_key, scanned, matched, "ok",
                         f"{batches} batches, {attached} officers")
    return {"ok": True, "source": "irs_990", "batch": batch_key, "batches": batches,
            "filings_scanned": scanned, "companies_named": matched, "officers": attached}


async def backfill_sec_sectors(limit: int | None = None) -> dict[str, Any]:
    """Fill SIC sector for listed companies. SEC's fair-access limit is 10
    requests/second and each pass already pauses ROSTER_SEC_PAUSE_SEC
    (0.12s) between calls, so a few hundred per pass stays well inside it
    and finishes the ~8k listed companies in hours, not days."""
    limit = int(os.getenv("REGISTER_SECTOR_BATCH", "250") or 250) if limit is None else limit
    db = await get_db()
    try:
        rows = await (await db.execute(
            """SELECT id, source_key FROM company_register
               WHERE tier='us_public' AND sector_label IS NULL
               ORDER BY id LIMIT ?""",
            (max(1, min(limit, 500)),),
        )).fetchall()
        targets = [(int(r["id"]), str(r["source_key"])) for r in rows]
    finally:
        await db.close()
    if not targets:
        return {"ok": True, "filled": 0, "remaining": 0}
    filled = 0
    for register_id, cik in targets:
        try:
            raw = await _http_get(f"https://data.sec.gov/submissions/CIK{cik}.json", timeout=30.0)
            payload = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            logger.info("sector backfill skipped CIK %s: %s", cik, exc)
            continue
        db = await get_db()
        try:
            await db.execute(
                """UPDATE company_register
                   SET sector_code=?, sector_label=?, region=COALESCE(?, region),
                       company_domain=COALESCE(?, company_domain), updated_at=CURRENT_TIMESTAMP
                   WHERE id=?""",
                (
                    str(payload.get("sic") or "").strip() or None,
                    str(payload.get("sicDescription") or "").strip() or None,
                    str(payload.get("stateOfIncorporationDescription") or "").strip().title() or None,
                    str(payload.get("website") or "").strip().lower() or None,
                    register_id,
                ),
            )
            await db.commit()
            filled += 1
        finally:
            await db.close()
    db = await get_db()
    try:
        row = await (await db.execute(
            "SELECT COUNT(*) AS n FROM company_register WHERE tier='us_public' AND sector_label IS NULL"
        )).fetchone()
        remaining = int(row["n"] or 0)
    finally:
        await db.close()
    return {"ok": True, "filled": filled, "remaining": remaining}


async def fetch_officers_for(register_id: int) -> dict[str, Any]:
    """Pull the officers a company filed itself, on demand for one row.

    The bulk files carry officers only for Form D: the listed-company ticker
    file and the Companies House bulk product do not include people, which is
    why those two tiers show zero on file. Both publish them per company
    instead, and the register already stores the identifier each one needs -
    the padded CIK for a listed company, the company number for a UK one - so
    this looks them up directly rather than searching by name, which is both
    an extra request and a fuzzy match.

    Nothing is fetched twice: a row that already has officers returns them.
    """
    db = await get_db()
    try:
        row = await (await db.execute(
            "SELECT id, source, source_key, tier, company_name, officer_count FROM company_register WHERE id=?",
            (register_id,),
        )).fetchone()
    finally:
        await db.close()
    if not row:
        return {"ok": False, "error": "Unknown company"}
    if int(row["officer_count"] or 0) > 0:
        return {"ok": True, "cached": True, "attached": 0, "officer_count": int(row["officer_count"])}

    source = row["source"]
    key = (row["source_key"] or "").strip()
    people: list[dict[str, Any]] = []
    try:
        if source == "sec_public" and key:
            from app.services.roster_watch import fetch_form4_people

            # Form 4 names the insiders who actually file, with the date they
            # last did, so a departed officer is visible. The 10-K parse is a
            # fallback for companies whose insiders file rarely.
            # SEC's submissions file is keyed by the zero-padded CIK, which is
            # exactly how the register stores it: stripping the padding 404s.
            cik = key.zfill(10)
            people = await fetch_form4_people(cik)
            if not people:
                from app.services.roster_watch import fetch_10k_people

                people = await fetch_10k_people(cik)
        elif source == "companies_house" and key:
            from app.services.roster_watch import ch_key, map_ch_officers, _http_get

            if not ch_key():
                return {"ok": False, "error": "Companies House key is not configured"}
            items: list[dict[str, Any]] = []
            page = 1
            while page <= 3:
                chunk = json.loads((await _http_get(
                    f"https://api.company-information.service.gov.uk/company/{key}/officers"
                    f"?items_per_page=100&page={page}",
                    basic_auth=(ch_key(), ""),
                )).decode("utf-8"))
                items.extend(chunk.get("items") or [])
                if not (chunk.get("links") or {}).get("next"):
                    break
                page += 1
            people = map_ch_officers(items, key)
        else:
            return {"ok": False, "error": "This company's register publishes no officer list"}
    except Exception as exc:  # network, rate limit, or a company with no filings
        return {"ok": False, "error": str(exc)[:200]}

    if not people:
        return {"ok": True, "attached": 0, "officer_count": 0,
                "note": "The register lists no current officers for this company."}
    # The roster fetchers describe a person with title/role_type; the register
    # stores that one description as relationship. Mapping it here is what
    # keeps "Chief Financial Officer" from landing as a blank role - the same
    # field-name mismatch that once dropped every UK officer.
    mapped = [
        {
            "full_name": person["full_name"],
            "relationship": (person.get("title") or "").strip() or person.get("role_type") or None,
            "observed_at": person.get("observed_at"),
            "source_url": person.get("source_url"),
        }
        for person in people
        if person.get("full_name")
        # A resigned officer is direct evidence of departure, not a contact.
        and person.get("employment") != "left"
    ]
    if not mapped:
        return {"ok": True, "attached": 0, "officer_count": 0,
                "note": "Every officer on record has resigned."}
    attached = await _attach_people({(source, row["source_key"]): mapped})
    return {"ok": True, "attached": attached, "officer_count": len(mapped)}


async def drain_company_register() -> dict[str, Any]:
    """One scheduler pass: refresh the listed-company list monthly, pull any
    Form D quarter not yet ingested, then trickle sector backfill."""
    if (os.getenv("REGISTER_INGEST_ENABLED", "1") or "1").strip().lower() not in {"1", "true", "yes"}:
        return {"ok": True, "skipped": "disabled"}
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    result: dict[str, Any] = {"ok": True}
    if not await _already_ingested("sec_public", month):
        result["sec_public"] = await ingest_sec_public()
        return result
    for quarter in recent_form_d_quarters():
        if await _already_ingested("sec_form_d", quarter):
            continue
        outcome = await ingest_form_d(quarter)
        result["form_d"] = outcome
        if outcome.get("ok"):
            return result
        # A quarter SEC has not published (or a transient failure) must not
        # block the older quarters behind it; try the next one this pass.
        continue
    # Form 5500 is the widest US source the register has: the SEC only sees
    # listed companies and Reg D filers, while every employer with a benefit
    # plan files this one. Two years, because a sponsor that filed last year
    # and not yet this one is still a company.
    now_year = datetime.now(timezone.utc).year
    for year in (now_year, now_year - 1):
        if not await _already_ingested("dol_5500", str(year)):
            outcome = await ingest_form_5500(year)
            result["form_5500"] = outcome
            if outcome.get("ok"):
                return result
            continue
    # Nonprofits that can pay and already buy outside advice, then the people
    # they named on those same returns.
    if not await _already_ingested("irs_990", str(now_year - 1)):
        outcome = await ingest_irs_990(now_year - 1)
        result["irs_990"] = outcome
        if outcome.get("ok"):
            return result
    if not await _already_ingested("irs_990", f"officers-{now_year - 1}"):
        result["irs_990_officers"] = await ingest_990_officers(now_year - 1)
        return result
    if (os.getenv("REGISTER_UK_ENABLED", "1") or "1").strip().lower() in {"1", "true", "yes"}:
        month = datetime.now(timezone.utc).strftime("%Y-%m-01")
        if not await _already_ingested("companies_house", month):
            result["companies_house"] = await ingest_companies_house()
            return result
    result["sectors"] = await backfill_sec_sectors()
    return result


# Brand -> registered-name aliases. Members type the name on the product, the
# register holds the name on the filing: "HBO" is a Warner Bros. Discovery
# brand with no SEC listing of its own, so a plain name search for "hbo" can
# only ever find unrelated UK companies that happen to start with those
# letters ("Hbos Uk Limited"). This is a deliberately small, hand-curated list
# of well-known consumer brands whose owner a member would not guess from the
# brand - not an attempt at a corporate-ownership graph. Values are lowercase
# registered-name prefixes, matched with LIKE 'prefix%' so "alphabet inc"
# finds "Alphabet Inc." and a UK "Alphabet Inc Ltd" alike; where SEC's title
# is spelled more than one way (with and without the dot), both are listed.
# A target that is not in the register (ByteDance, X Corp are private) simply
# matches nothing. Add an entry only when the brand -> owner link is public
# and stable; an alias that goes stale ranks the wrong company first.
BRAND_ALIASES: dict[str, tuple[str, ...]] = {
    "hbo": ("warner bros. discovery", "warner bros discovery"),
    "hbo max": ("warner bros. discovery", "warner bros discovery"),
    "cnn": ("warner bros. discovery", "warner bros discovery"),
    "google": ("alphabet inc",),
    "youtube": ("alphabet inc",),
    "waymo": ("alphabet inc",),
    "deepmind": ("alphabet inc",),
    "facebook": ("meta platforms",),
    "instagram": ("meta platforms",),
    "whatsapp": ("meta platforms",),
    "tiktok": ("bytedance",),
    "snapchat": ("snap inc",),
    "twitter": ("x corp",),
    "x": ("x corp",),
    "aws": ("amazon com", "amazon.com"),
    "amazon web services": ("amazon com", "amazon.com"),
    "whole foods": ("amazon com", "amazon.com"),
    "linkedin": ("microsoft corp",),
    "github": ("microsoft corp",),
    "xbox": ("microsoft corp",),
    "espn": ("walt disney", "the walt disney"),
    "pixar": ("walt disney", "the walt disney"),
    "hulu": ("walt disney", "the walt disney"),
    "nbc": ("comcast corp",),
    "nbcuniversal": ("comcast corp",),
    "peacock": ("comcast corp",),
    "chase": ("jpmorgan chase",),
    "old navy": ("gap inc",),
    "kfc": ("yum brands", "yum! brands"),
    "taco bell": ("yum brands", "yum! brands"),
    "tj maxx": ("tjx companies", "tjx cos"),
    "p&g": ("procter & gamble", "procter and gamble"),
    "j&j": ("johnson & johnson",),
    "bofa": ("bank of america",),
}


def brand_alias_names(q: str | None) -> tuple[str, ...]:
    """Registered-name prefixes a brand query stands for (empty when it is
    not a curated brand). Shared with the company resolver so "HBO" typed in
    Find people lands on the same company the register search puts first."""
    key = re.sub(r"\s+", " ", (q or "").strip().lower())
    return BRAND_ALIASES.get(key, ())


# Punctuation that separates words in a registered name. "Frelif
# (Loughborough) Llp" must count "loughborough" as a word start, and the same
# replacement is applied to the query so "at&t" still finds "At&T Inc".
_WORD_BREAK_CHARS = "(-./&"


def _word_break_sql(column: str) -> str:
    expr = f"lower({column})"
    for ch in _WORD_BREAK_CHARS:
        expr = f"replace({expr}, '{ch}', ' ')"
    return f"(' ' || {expr})"


async def search_register(
    *,
    q: str | None = None,
    tier: str | None = None,
    country: str | None = None,
    sector: str | None = None,
    min_amount: float | None = None,
    with_officers: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    where: list[str] = []
    args: list[Any] = []
    # A query ranked only by recency/amount treats "contains these letters
    # anywhere" as good as "is this company". Typing "meta" must not bury
    # Meta Platforms under every UK company with "metal" in its name or
    # sector, just because "meta" is a literal prefix of "metal". Match
    # quality is ranked first, in tiers a plain substring search cannot
    # express: exact name / exact ticker / curated brand alias, name starts
    # with the query as a whole word, the query as a whole word elsewhere in
    # the name, a plain prefix, a plain substring in the name, and last a
    # sector-label-only match.
    relevance_case = "0.0"
    relevance_args: list[Any] = []
    searching = bool(q and q.strip())
    if searching:
        raw = q.strip().lower()
        # Escape the query's own literal % and _ so a member searching for
        # them is not exposed to SQL LIKE wildcard behaviour.
        escaped = raw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        aliases = brand_alias_names(raw)
        alias_sql = " OR ".join(["lower(company_name) LIKE ? ESCAPE '\\'"] * len(aliases))
        alias_args = [
            a.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%" for a in aliases
        ]
        # A ticker is only on SEC-listed rows, so the source test keeps
        # json_extract off the millions of other rows. "GOOGL" appears
        # nowhere in "Alphabet Inc.", which is why it needs its own clause.
        ticker_sql = "(source = 'sec_public' AND lower(json_extract(metadata_json, '$.ticker')) = ?)"
        if len(raw) < 5:
            # Three or four letters occur inside thousands of unrelated
            # words: "hbo" is in "Loug-hbo-rough", so "Frelif (Loughborough)
            # Llp" came back for HBO. A short query has to start a word of
            # the name; a sector-label hit is dropped for the same reason
            # ("meta" in "fabricated metal products" names no company).
            boundary = raw
            for ch in _WORD_BREAK_CHARS:
                boundary = boundary.replace(ch, " ")
            boundary = boundary.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            name_sql = f"{_word_break_sql('company_name')} LIKE ? ESCAPE '\\'"
            name_args: list[Any] = [f"% {boundary}%"]
        else:
            name_sql = (
                "lower(company_name) LIKE ? ESCAPE '\\' OR lower(COALESCE(sector_label,'')) LIKE ? ESCAPE '\\'"
            )
            name_args = [f"%{escaped}%", f"%{escaped}%"]
        where.append(f"({name_sql} OR {ticker_sql}{' OR ' + alias_sql if aliases else ''})")
        args.extend([*name_args, raw, *alias_args])
        relevance_case = f"""CASE
            WHEN lower(company_name) = ? THEN 0
            WHEN {ticker_sql} THEN 0
            {f"WHEN {alias_sql} THEN 0" if aliases else ""}
            WHEN lower(company_name) LIKE ? ESCAPE '\\' THEN 1
            WHEN lower(company_name) LIKE ? ESCAPE '\\' OR lower(company_name) LIKE ? ESCAPE '\\' THEN 2
            WHEN lower(company_name) LIKE ? ESCAPE '\\' THEN 3
            WHEN lower(company_name) LIKE ? ESCAPE '\\' THEN 4
            ELSE 5
        END"""
        relevance_args = [
            raw,
            raw,
            *alias_args,
            f"{escaped} %",
            f"% {escaped} %", f"% {escaped}",
            f"{escaped}%",
            f"%{escaped}%",
        ]
    if tier and tier.strip():
        where.append("tier = ?")
        args.append(tier.strip())
    if country and country.strip():
        where.append("country = ?")
        args.append(country.strip().upper())
    if sector and sector.strip():
        where.append("lower(COALESCE(sector_label,'')) LIKE ?")
        args.append(f"%{sector.strip().lower()}%")
    if min_amount:
        where.append("COALESCE(last_event_amount, 0) >= ?")
        args.append(float(min_amount))
    if with_officers:
        where.append("officer_count > 0")
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    limit = max(1, min(int(limit), 200))
    if searching:
        # A member who types a name wants the company they mean, and within
        # one match level that is almost always the biggest one: "hbo" found
        # three dated UK rows before any listed company because a listed
        # company carries no filing date, so recency-first put every UK
        # accounts filing ahead of it. Size decides first - SEC's market-cap
        # order, then headcount, then the Companies House statutory band
        # (group > full > medium), then how many officers are on record -
        # and recency only breaks what is left.
        order_by = """match_level ASC,
                         CASE WHEN tier = 'us_public' AND prominence_rank IS NOT NULL THEN 0 ELSE 1 END,
                         CASE WHEN prominence_rank IS NOT NULL THEN prominence_rank ELSE 999999999 END ASC,
                         COALESCE(employees, -1) DESC,
                         CASE WHEN source = 'companies_house' THEN
                             CASE json_extract(metadata_json, '$.account_category')
                                 WHEN 'GROUP' THEN 0 WHEN 'FULL' THEN 1 WHEN 'MEDIUM' THEN 2 ELSE 3 END
                         ELSE 3 END,
                         officer_count DESC,
                         COALESCE(last_event_at, '') DESC, COALESCE(last_event_amount, 0) DESC,
                         company_name"""
    else:
        # Browsing with no query is a "what is new" feed: the newest filing
        # first is the point of it, so the original order stays.
        order_by = """COALESCE(last_event_at, '') DESC, COALESCE(last_event_amount, 0) DESC,
                         officer_count DESC,
                         -- A listed company has no filing date or dollar amount to
                         -- rank by - nothing above this line ever distinguishes it
                         -- from any other listed company. Without this, Meta sorted
                         -- no differently from a micro-cap nobody has heard of:
                         -- alphabetically, wherever that fell among 200k+ rows with
                         -- real recent activity. This is SEC's own market-cap order,
                         -- so the companies a member actually wants surface first.
                         CASE WHEN prominence_rank IS NOT NULL THEN prominence_rank ELSE 999999999 END ASC,
                         company_name"""
    db = await get_db()
    try:
        total = await (await db.execute(f"SELECT COUNT(*) AS n FROM company_register {clause}", tuple(args))).fetchone()
        # match_level is returned with each row so a caller (the company
        # resolver) can tell "this is the company" (0-1) from "these letters
        # appear in its name" without re-deriving the tiers.
        rows = await (await db.execute(
            f"""SELECT r.*, {relevance_case} AS match_level,
                       (SELECT COUNT(*) FROM company_register_people p
                         WHERE p.register_id = r.id AND p.person_level = 'working') AS working_count
                FROM company_register r {clause}
                ORDER BY {order_by}
                LIMIT ? OFFSET ?""",
            (*relevance_args, *args, limit, max(0, int(offset))),
        )).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            items.append(item)
        # Who in the club is already on each of these, so a member does not
        # start a company a colleague is halfway through.
        from app.services.company_claims import claims_for, company_key

        claims = await claims_for(items)
        for item in items:
            held = claims.get(company_key(item.get("company_name"), item.get("company_domain")))
            item["claimed_by"] = held["member"] if held else None
        return {"items": items, "total": int(total["n"] or 0), "limit": limit, "offset": max(0, int(offset))}
    finally:
        await db.close()


async def register_summary() -> dict[str, Any]:
    db = await get_db()
    try:
        tiers = await (await db.execute(
            "SELECT tier, country, COUNT(*) AS n, SUM(officer_count > 0) AS with_officers FROM company_register GROUP BY tier, country"
        )).fetchall()
        sectors = await (await db.execute(
            """SELECT sector_label AS sector, COUNT(*) AS n FROM company_register
               WHERE sector_label IS NOT NULL AND sector_label != ''
               GROUP BY sector_label ORDER BY n DESC LIMIT 40"""
        )).fetchall()
        ingests = await (await db.execute(
            "SELECT source, batch_key, rows_written, status, detail, completed_at FROM company_register_ingests ORDER BY completed_at DESC LIMIT 10"
        )).fetchall()
        return {
            "tiers": [dict(r) for r in tiers],
            "sectors": [dict(r) for r in sectors],
            "recent_ingests": [dict(r) for r in ingests],
        }
    finally:
        await db.close()
