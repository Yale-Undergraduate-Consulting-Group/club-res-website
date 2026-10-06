"""Recurring public-company roster: SEC officers/directors, optional IR snippets, weekly still-there.

This is not Apollo. Weekly verify is Form 3/4/5 XML for companies we can map to a CIK.
Private / unmatched names are enrolled and retried monthly, not hammered.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from urllib.parse import quote

import httpx

from app.database import get_db
from app.services.company_email_cache import build_email_for_person_sync, company_brand_words
from app.services.contact_scraper import names_entity_or_place, normalize_domain, person_name_key

logger = logging.getLogger(__name__)

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
OWNERSHIP_FORMS = frozenset({"3", "3/A", "4", "4/A", "5", "5/A"})
LEGAL_DROP = frozenset(
    {
        "inc", "corp", "ltd", "llc", "co", "company", "companies", "plc", "sa", "nv", "se",
        "lp", "llp", "limited", "incorporated", "corporation", "holdings", "holding",
        "group", "the", "of", "and",
    }
)
CH_BASE = "https://api.company-information.service.gov.uk"
CH_PUBLIC_BASE = "https://find-and-update.company-information.service.gov.uk"
CH_DIRECTOR_ROLES = {"director", "nilp-member", "llp-member", "member"}
CH_EXTRA_DROP = {"bank", "holdings", "europe", "uk", "britain", "brands", "international", "americas"}
CH_HONORIFIC = re.compile(r"\b(Mr|Mrs|Ms|Dr|Rev|Sir|Dame|Prof)\.?\b", re.I)
PLATFORM_HOSTS = frozenset({
    "linkedin.com", "facebook.com", "x.com", "twitter.com", "youtube.com", "instagram.com",
    "wikipedia.org", "crunchbase.com", "bloomberg.com", "reuters.com", "glassdoor.com",
    "indeed.com", "zoominfo.com", "apollo.io", "rocketreach.co", "opencorporates.com",
    "blogspot.com", "wordpress.com", "wixsite.com", "weebly.com", "squarespace.com", "github.io",
    "substack.com", "medium.com", "notion.site", "carrd.co", "cargo.site", "webflow.io",
    
    "companieshouse.gov.uk", "gov.uk", "sec.gov", "yelp.com", "mapquest.com", "yellowpages.com",
    # Company-profile and contact-data vendors. A search for a company's
    # official site often ranks their profile page first; "Meta Platforms,
    # Inc." resolved to globaldata.com that way.
    "globaldata.com", "dnb.com", "owler.com", "craft.co", "signalhire.com", "pitchbook.com",
    "cbinsights.com", "lusha.com", "contactout.com", "theorg.com", "leadiq.com",
    "wikipedia.com", "wikidata.org",
})


def ch_key() -> str:
    return (os.getenv("COMPANIES_HOUSE_API_KEY") or "").strip()


_TICKERS: dict[str, Any] | None = None
_TICKERS_AT: datetime | None = None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    return (dt or utcnow()).isoformat()


def sec_user_agent() -> str:
    configured = (os.getenv("SEC_USER_AGENT") or "").strip()
    if configured:
        return configured
    contact = (os.getenv("SEC_CONTACT_EMAIL") or "outreach@localhost").strip()
    return f"YUCGOutreach roster-watch {contact}"


def verify_days() -> int:
    return max(1, min(int(os.getenv("ROSTER_VERIFY_DAYS", "7") or 7), 90))


def drain_limit() -> int:
    return max(1, min(int(os.getenv("ROSTER_DRAIN_LIMIT", "8") or 8), 40))


def left_after_misses() -> int:
    return max(1, min(int(os.getenv("ROSTER_LEFT_AFTER_MISSES", "2") or 2), 8))


def _company_tokens(name: str) -> list[str]:
    # NFKD-fold accents BEFORE tokenizing: "Itaú" must yield 'itau', not 'ita'.
    cleaned = re.sub(r"\([^)]*\)", " ", name or "")
    cleaned = unicodedata.normalize("NFKD", cleaned.lower())
    cleaned = "".join(ch for ch in cleaned if not unicodedata.combining(ch))
    parts = re.findall(r"[a-z0-9]+", cleaned)
    return [p for p in parts if p not in LEGAL_DROP and len(p) > 1]


def company_key(name: str) -> str:
    tokens = _company_tokens(name)
    return " ".join(tokens[:8])[:80] or (name or "").strip().lower()[:80]


def _form4_display_name(raw: str) -> str:
    text = re.sub(r"\s+", " ", (raw or "").replace(",", " ")).strip()
    if not text:
        return ""
    parts = text.split()
    if len(parts) == 1:
        return parts[0].title()
    last, rest = parts[0], parts[1:]
    ordered = rest + [last]
    out = []
    for part in ordered:
        if re.fullmatch(r"[A-Z]\.?", part):
            out.append(part[0].upper() + ".")
        elif part.isupper() or part.islower():
            out.append(part.title())
        else:
            out.append(part)
    return " ".join(out)


def parse_form4_xml(xml_text: str) -> dict[str, Any] | None:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None

    def local(tag: str) -> str:
        return tag.split("}")[-1]

    name = title = ""
    officer = director = False
    for el in root.iter():
        tag = local(el.tag)
        text = (el.text or "").strip()
        if tag == "rptOwnerName" and text:
            name = _form4_display_name(text)
        elif tag == "officerTitle" and text:
            title = re.sub(r"\s+", " ", text)[:160]
        elif tag == "isOfficer" and text.lower() in {"true", "1"}:
            officer = True
        elif tag == "isDirector" and text.lower() in {"true", "1"}:
            director = True
    if not name:
        return None
    role = "director" if director and not officer else "officer"
    return {
        "full_name": name,
        "normalized_name": person_name_key(name),
        "title": title,
        "role_type": role,
        "source": "sec_form4",
    }


_OFFICER_TITLE = re.compile(
    r"\b(President|Chief|Vice President|Executive Vice|Managing Director|"
    r"General Counsel|General Manager|Co-Chief|Treasurer|Secretary|Director)\b",
    re.I,
)


def parse_10k_officers(html: str) -> list[dict[str, Any]]:
    text = re.sub(r"<[^>]+>", " ", html or "")
    text = re.sub(r"&nbsp;", " ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text)
    low = text.lower()
    idx = low.find("information about our executive officers")
    if idx < 0:
        idx = low.find("our executive officers")
    if idx < 0:
        return []
    window = text[idx : idx + 6000]
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    pattern = re.compile(
        r"([A-Z][a-z]+(?:\s+[A-Z]\.)?(?:\s+[A-Z][a-z]+)+)\s+"
        r"((?:Executive Chairman|President|Chief|Co-Chief|Vice President|"
        r"Executive Vice President|Managing Director|General Counsel|"
        r"General Manager|Treasurer)[^0-9,]{6,90}?)\s+\d{2}"
    )
    for match in pattern.finditer(window):
        name = re.sub(r"\s+", " ", match.group(1)).strip()
        title = re.sub(r"\s+", " ", match.group(2)).strip(" ,")
        key = person_name_key(name)
        if not key or key in seen or not _OFFICER_TITLE.search(title):
            continue
        seen.add(key)
        found.append(
            {
                "full_name": name,
                "normalized_name": key,
                "title": title[:160],
                "role_type": "officer",
                "source": "sec_10k",
            }
        )
    return found


def match_public_company(company_name: str, tickers: dict[str, Any]) -> dict[str, str] | None:
    tokens = _company_tokens(company_name)
    if not tokens:
        return None
    query_key = " ".join(tokens)
    token_hit: dict[str, str] | None = None
    prefix: dict[str, str] | None = None
    head_hits: list[dict[str, str]] = []
    # "Meta" against SEC's "Meta Platforms, Inc.": the token keys differ
    # ("meta" vs "meta platforms") and "Meta Critical Minerals Inc." also starts
    # with "meta", so neither rule below picked one and Meta came back
    # unmatched. Comparing brands (the name without its legal and listing
    # suffixes, the same reading web discovery uses) settles it when exactly
    # one company carries that brand.
    query_brand = company_brand_words(company_name)
    brand_hits: dict[str, dict[str, str]] = {}
    for row in tickers.values() if isinstance(tickers, dict) else []:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "")
        title_tokens = _company_tokens(title)
        if not title_tokens:
            continue
        title_key = " ".join(title_tokens)
        cik = str(row.get("cik_str") or "").zfill(10)
        ticker = str(row.get("ticker") or "").upper()
        hit = {"ticker": ticker, "cik": cik, "title": title}
        if query_key == title_key:
            return hit
        if tokens[0] == title_tokens[0] and len(tokens[0]) >= 6:
            prefix = prefix or hit
        if len(tokens[0]) >= 4 and tokens[0] == title_tokens[0]:
            head_hits.append(hit)
        if tokens[0] == title_tokens[0] and company_brand_words(title) == query_brand:
            brand_hits.setdefault(cik, hit)
        if len(tokens) >= 2 and all(t in title_tokens for t in tokens[:3]):
            token_hit = token_hit or hit
        elif len(title_tokens) >= 2 and all(t in tokens for t in title_tokens[:2]) and len(title_tokens[0]) >= 5:
            token_hit = token_hit or hit
    if len(brand_hits) == 1:
        return next(iter(brand_hits.values()))
    if len(head_hits) == 1:
        return head_hits[0]
    return token_hit or prefix


def _ch_tokens(name: str) -> set[str]:
    return {tok for tok in _company_tokens(name) if tok not in CH_EXTRA_DROP}


def match_uk_company(query_name: str, items: list[dict]) -> str | None:
    want = _ch_tokens(query_name)
    if not want:
        return None
    best: str | None = None
    for item in items or []:
        if not isinstance(item, dict) or item.get("company_status") != "active":
            continue
        number = str(item.get("company_number") or "").strip()
        if not number:
            continue
        if _ch_tokens(str(item.get("title") or "")) == want:
            best = number
            break
    return best


def map_ch_officers(items: list[dict], company_number: str, *, limit: int = 60) -> list[dict[str, Any]]:
    """Natural-person officers; resigned_on is direct evidence of departure —
    absence from this page never decays anyone."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    url = f"{CH_PUBLIC_BASE}/company/{company_number}/officers"
    for officer in items or []:
        if not isinstance(officer, dict):
            continue
        role = str(officer.get("officer_role") or "").lower()
        if "corporate" in role or officer.get("identification"):
            continue
        # The officers endpoint returns the person as "name" ("KUUSI, Mikko
        # Akseli"); officer_name is not a field it sends, so reading only
        # that dropped every officer of every UK company.
        raw = CH_HONORIFIC.sub(" ", str(officer.get("name") or officer.get("officer_name") or ""))
        full = _form4_display_name(raw)
        norm = person_name_key(full)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        occupation = re.sub(r"\s+", " ", str(officer.get("occupation") or ""))[:160]
        out.append({
            "full_name": full,
            "normalized_name": norm,
            "title": occupation,
            "role_type": "director" if role in CH_DIRECTOR_ROLES else "officer",
            "source": "companies_house",
            "source_url": url,
            "accession": company_number,
            "employment": "left" if str(officer.get("resigned_on") or "").strip() else "current",
        })
        if len(out) >= limit:
            break
    return out


async def fetch_companies_house_people(company_name: str) -> list[dict[str, Any]]:
    """UK & Ireland register officers. Free key, basic auth. No key => disabled (no calls)."""
    key = ch_key()
    if not key:
        return []
    search = json.loads((await _http_get(
        f"{CH_BASE}/search/companies?q={quote(company_name)}&items_per_page=10",
        basic_auth=(key, ""),
    )).decode("utf-8"))
    number = match_uk_company(company_name, search.get("items") or [])
    if not number:
        return []
    items: list[dict[str, Any]] = []
    page = 1
    while page <= 3:
        chunk = json.loads((await _http_get(
            f"{CH_BASE}/company/{number}/officers?items_per_page=100&page={page}",
            basic_auth=(key, ""),
        )).decode("utf-8"))
        items.extend(chunk.get("items") or [])
        if not (chunk.get("links") or {}).get("next"):
            break
        page += 1
    return map_ch_officers(items, number)


async def _http_get(url: str, *, basic_auth: tuple[str, str] | None = None) -> bytes:
    pause = float(os.getenv("ROSTER_SEC_PAUSE_SEC", "0.12") or 0)
    if pause > 0:
        await asyncio.sleep(pause)
    headers = {"User-Agent": sec_user_agent(), "Accept-Encoding": "gzip, deflate"}
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        response = await client.get(url, headers=headers, auth=basic_auth)
        response.raise_for_status()
        return response.content


async def load_tickers(*, force: bool = False) -> dict[str, Any]:
    global _TICKERS, _TICKERS_AT
    if _TICKERS is not None and not force and _TICKERS_AT and utcnow() - _TICKERS_AT < timedelta(hours=24):
        return _TICKERS
    raw = await _http_get(SEC_TICKERS_URL)
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        return _TICKERS or {}
    _TICKERS = data
    _TICKERS_AT = utcnow()
    return data


async def load_tickers_or_reason() -> tuple[dict[str, Any], str | None]:
    """The SEC ticker list, or what is left of it plus why it could not load.

    Both roster paths used to swallow this failure into an empty list, so every
    company came back "unmatched" as if it were private. The usual cause is SEC
    answering 403 to a User-Agent without a real contact (the built-in default
    is one). The reason goes on the roster as last_error and into the log."""
    try:
        return await load_tickers(), None
    except Exception as exc:
        configured = bool((os.getenv("SEC_USER_AGENT") or "").strip())
        # httpx appends a "For more information check: <mdn url>" line that
        # members then saw in the search's status; keep the first line only.
        status = getattr(getattr(exc, "response", None), "status_code", None)
        detail = f"HTTP {status}" if status else str(exc).splitlines()[0][:160]
        reason = f"SEC company list unavailable ({detail})"
        if not configured:
            reason += " (SEC_USER_AGENT is not set; SEC wants 'Name contact@example.org')"
        logger.warning("roster: %s", reason)
        return _TICKERS or {}, reason


def _archive_url(cik: str, accession: str, document: str) -> str:
    acc = (accession or "").replace("-", "")
    doc = (document or "").split("/")[-1]
    return SEC_ARCHIVE.format(cik=int(cik), acc=acc, doc=quote(doc))


def _xml_candidates(primary: str) -> list[str]:
    """The filing's raw XML. primaryDocument names it (under an xsl folder
    for the rendered copy; the raw file sits at the root under the same
    name). "ownership.xml" used to be tried first, and each wrong guess was a
    404 that took SEC 7-10s: ~25s of a 36s cold Barclays roster. The generic
    names are only guesses for a filing whose primary document is not XML."""
    name = (primary or "").split("/")[-1]
    if name.lower().endswith(".xml"):
        return [name]
    return ["ownership.xml", "primary_doc.xml"]


async def fetch_form4_people(cik: str, *, max_filings: int = 18) -> list[dict[str, Any]]:
    raw = await _http_get(SEC_SUBMISSIONS_URL.format(cik=cik))
    payload = json.loads(raw.decode("utf-8"))
    recent = (payload.get("filings") or {}).get("recent") or {}
    forms = list(
        zip(
            recent.get("form") or [],
            recent.get("accessionNumber") or [],
            recent.get("primaryDocument") or [],
        )
    )
    people: list[dict[str, Any]] = []
    seen: set[str] = set()
    checked = 0
    for form, accession, document in forms:
        if form not in OWNERSHIP_FORMS:
            continue
        checked += 1
        if checked > max_filings:
            break
        parsed = None
        for fname in _xml_candidates(document):
            url = _archive_url(cik, accession, fname)
            try:
                xml = (await _http_get(url)).decode("utf-8", "ignore")
            except Exception:
                continue
            parsed = parse_form4_xml(xml)
            if parsed:
                parsed["source_url"] = url
                parsed["accession"] = accession
                break
        if not parsed:
            continue
        key = parsed["normalized_name"]
        if not key or key in seen:
            continue
        seen.add(key)
        people.append(parsed)
        if len(people) >= 40:
            break
    return people


async def fetch_10k_people(cik: str) -> list[dict[str, Any]]:
    raw = await _http_get(SEC_SUBMISSIONS_URL.format(cik=cik))
    payload = json.loads(raw.decode("utf-8"))
    recent = (payload.get("filings") or {}).get("recent") or {}
    for form, accession, document in zip(
        recent.get("form") or [],
        recent.get("accessionNumber") or [],
        recent.get("primaryDocument") or [],
    ):
        if form != "10-K":
            continue
        url = _archive_url(cik, accession, document)
        html = (await _http_get(url)).decode("utf-8", "ignore")
        people = parse_10k_officers(html)
        for row in people:
            row["source_url"] = url
            row["accession"] = accession
        return people
    return []


async def enroll_prospect_companies() -> int:
    db = await get_db()
    added = 0
    try:
        rows = await (
            await db.execute("SELECT company, extra_json FROM yucg_prospect_targets ORDER BY id")
        ).fetchall()
        now = iso()
        for row in rows:
            name = row["company"] if not isinstance(row, tuple) else row[0]
            extra = row["extra_json"] if not isinstance(row, tuple) else row[1]
            name = name or ""
            key = company_key(name)
            if not key:
                continue
            existing = await (await db.execute("SELECT id FROM company_rosters WHERE company_key=?", (key,))).fetchone()
            if existing:
                continue
            await db.execute(
                """INSERT INTO company_rosters
                   (company_key, company_name, company_domain, next_verify_at, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (key, name.strip(), _seed_domain(name, extra) or None, now, now, now),
            )
            added += 1
        await db.commit()
        return added
    finally:
        await db.close()


async def _ensure_roster(
    company_name: str, domain: str | None = None, *, pin_domain: bool = False
) -> dict[str, Any]:
    key = company_key(company_name)
    now = iso()
    dom = normalize_domain(domain or "") or None
    db = await get_db()
    try:
        row = await (await db.execute("SELECT * FROM company_rosters WHERE company_key=?", (key,))).fetchone()
        if not row:
            await db.execute(
                """INSERT INTO company_rosters
                   (company_key, company_name, company_domain, next_verify_at, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (key, company_name.strip(), dom, now, now, now),
            )
            await db.commit()
            row = await (await db.execute("SELECT * FROM company_rosters WHERE company_key=?", (key,))).fetchone()
        elif dom and (pin_domain or not row["company_domain"]) and dom != row["company_domain"]:
            # A found domain only fills a gap. A domain the member typed wins
            # over the stored one: otherwise one wrong lookup (globaldata.com
            # for Meta) stuck for good and meta.com never reached the roster.
            # Every address derived from the old domain goes with it, bounce
            # tombstones and provider checks included: those were verdicts on a
            # mailbox at the wrong company, and keeping a bounce would hide the
            # person from ever getting an address at the right one.
            await db.execute(
                "UPDATE company_rosters SET company_domain=?, next_email_check_at=NULL, updated_at=? WHERE company_key=?",
                (dom, now, key),
            )
            if row["company_domain"]:
                await db.execute(
                    """UPDATE company_roster_people
                       SET inferred_email=NULL, email_status=NULL, email_checked_at=NULL,
                           email_provider_checked_at=NULL
                       WHERE roster_id=?""",
                    (row["id"],),
                )
            await db.commit()
            row = await (await db.execute("SELECT * FROM company_rosters WHERE company_key=?", (key,))).fetchone()
        return dict(row)
    finally:
        await db.close()


def _infer_email(name: str, domain: str | None) -> str | None:
    if not domain:
        return None
    try:
        return build_email_for_person_sync(name, domain)
    except Exception:
        return None


def _sticky_source(source: str) -> bool:
    return source.startswith("discovery") or source in {"web_ir", "web_press", "companies_house"}


async def _upsert_people(
    roster_id: int,
    people: Iterable[dict[str, Any]],
    *,
    domain: str | None,
    mark_missing: bool,
) -> dict[str, int]:
    now = iso()
    threshold = left_after_misses()
    # Every roster source lands here (SEC, Companies House, the web, and what
    # Find people saved), so this is where a company or a place posing as a
    # person is kept out: "Plc Barclays" from a Form 4 owned by Barclays PLC,
    # "West London" from a broker listing. Once stored, every later search
    # read them back as people.
    incoming = [p for p in people
                if p.get("normalized_name") and not names_entity_or_place(p.get("full_name") or "")]
    seen = {p["normalized_name"] for p in incoming}
    db = await get_db()
    try:
        for person in incoming:
            email = person.get("inferred_email") or _infer_email(person["full_name"], domain)
            existing = await (
                await db.execute(
                    "SELECT id FROM company_roster_people WHERE roster_id=? AND normalized_name=?",
                    (roster_id, person["normalized_name"]),
                )
            ).fetchone()
            if existing:
                await db.execute(
                    """UPDATE company_roster_people SET
                           full_name=?,
                           title=CASE WHEN ? != '' THEN ? ELSE title END,
                           role_type=?, source=?,
                           source_url=COALESCE(?, source_url),
                           accession=COALESCE(?, accession),
                           inferred_email=COALESCE(?, inferred_email),
                           employment=CASE WHEN company_roster_people.employment='ghost' THEN 'ghost' ELSE ? END,
                           last_seen_at=?, missed_checks=0
                       WHERE id=?""",
                    (
                        person["full_name"],
                        person.get("title") or "",
                        person.get("title") or "",
                        person.get("role_type") or "officer",
                        person.get("source") or "sec_form4",
                        person.get("source_url"),
                        person.get("accession"),
                        email,
                        person.get("employment") or "current",
                        now,
                        existing["id"],
                    ),
                )
            else:
                await db.execute(
                    """INSERT INTO company_roster_people (
                           roster_id, normalized_name, full_name, title, role_type, source,
                           source_url, accession, inferred_email, employment,
                           first_seen_at, last_seen_at, missed_checks
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
                    (
                        roster_id,
                        person["normalized_name"],
                        person["full_name"],
                        person.get("title") or "",
                        person.get("role_type") or "officer",
                        person.get("source") or "sec_form4",
                        person.get("source_url"),
                        person.get("accession"),
                        email,
                        person.get("employment") or "current",
                        now,
                        now,
                    ),
                )
        if mark_missing and seen:
            rows = await (
                await db.execute(
                    """SELECT id, normalized_name, missed_checks, source FROM company_roster_people
                       WHERE roster_id=? AND employment IN ('current', 'unverified')""",
                    (roster_id,),
                )
            ).fetchall()
            for row in rows:
                record = dict(row)
                if record["normalized_name"] in seen or _sticky_source(str(record.get("source") or "")):
                    continue
                missed = int(record.get("missed_checks") or 0) + 1
                employment = "left" if missed >= threshold else "current"
                await db.execute(
                    "UPDATE company_roster_people SET missed_checks=?, employment=? WHERE id=?",
                    (missed, employment, record["id"]),
                )
        counts = await (
            await db.execute(
                """SELECT COUNT(*) AS n,
                          SUM(CASE WHEN employment='current' THEN 1 ELSE 0 END) AS current_n
                   FROM company_roster_people WHERE roster_id=?""",
                (roster_id,),
            )
        ).fetchone()
        people_count = int(counts["n"] if counts else 0)
        current_count = int((counts["current_n"] if counts else 0) or 0)
        await db.execute(
            "UPDATE company_rosters SET people_count=?, current_count=?, updated_at=? WHERE id=?",
            (people_count, current_count, now, roster_id),
        )
        await db.commit()
        return {"people": people_count, "current": current_count}
    finally:
        await db.close()


async def remember_discovery_people(company: str, domain: str | None, contacts: list[dict[str, Any]]) -> None:
    if not company or not contacts:
        return
    roster = await _ensure_roster(company, domain)
    people = []
    for contact in contacts:
        name = (contact.get("name") or "").strip()
        if not name:
            name = f"{(contact.get('first_name') or '').strip()} {(contact.get('last_name') or '').strip()}".strip()
        key = person_name_key(name)
        if not key:
            continue
        people.append(
            {
                "full_name": name,
                "normalized_name": key,
                "title": (contact.get("title") or "")[:160],
                "role_type": "named",
                "source": "discovery",
                "source_url": contact.get("source_url") or contact.get("linkedin_url"),
                "inferred_email": contact.get("email"),
            }
        )
    if people:
        await _upsert_people(int(roster["id"]), people, domain=domain, mark_missing=False)


async def _web_people(company: str, domain: str | None) -> list[dict[str, Any]]:
    from app.services.web_fetch import web_search_configured
    if not (web_search_configured() or (os.getenv("TAVILY_API_KEY") or "").strip()):
        return []
    if (os.getenv("ROSTER_WEB_ON_ENROLL") or "0").strip().lower() not in {"1", "true", "yes"}:
        return []
    from app.services.web_contact_discovery import _extract_people_from_results, _tavily_search

    results = await _tavily_search(f"{company} executive officers OR leadership team", max_results=6)
    extracted = _extract_people_from_results(results, company, domain)
    out = []
    for person in extracted[:20]:
        name = person.get("name") or ""
        key = person_name_key(name)
        if not key:
            continue
        out.append(
            {
                "full_name": name,
                "normalized_name": key,
                "title": (person.get("title") or "")[:160],
                "role_type": "named",
                "source": "web_ir",
                "source_url": person.get("source_url"),
            }
        )
    return out


def _registrable_domain(url: str) -> str:
    """Site domain from a URL, rejecting social/registry/hosting platforms
    (including user subdomains like garmin-fans.blogspot.com)."""
    dom = normalize_domain(url or "")
    if not dom or "." not in dom:
        return ""
    labels = dom.split(".")
    if len(labels) < 2 or labels[0] == "localhost":
        return ""
    root = ".".join(labels[-2:])
    if root in PLATFORM_HOSTS:
        return ""
    return dom


#: Words a company's own domain may carry after its name ("garmin-group",
#: "shopifyinc") without it becoming someone else's site.
_DOMAIN_TAIL_WORDS = frozenset({
    "inc", "corp", "co", "group", "global", "hq", "usa", "us", "uk", "online",
    "official", "company", "world", "intl", "international",
})


def _domain_matches_company(company_name: str, dom: str) -> bool:
    """Whether a domain is plausibly the company's own.

    The domain's name must BE the company's name, its opening words, or its
    initials, with at most a generic tail. Starting with the first word was
    not enough: "warner-access.com" passed for Warner Bros. Discovery and was
    stored as its verified website, and "metacritic.com" would pass for Meta.
    """
    tokens = _company_tokens(company_name)
    if tokens and tokens[0] == "the":
        tokens = tokens[1:]
    if not tokens or not dom:
        return False
    label = re.sub(r"[^a-z0-9]", "", dom.split(".")[0].lower())
    if not label:
        return False
    stems = {"".join(tokens[:k]) for k in range(1, len(tokens) + 1)}
    if len(tokens) >= 2:
        stems.add("".join(t[0] for t in tokens))
    for stem in stems:
        if label == stem:
            return True
        # A tail is allowed only after a real word: "atrgroup" is not ATR's.
        if len(stem) >= 4 and label.startswith(stem) and label[len(stem):] in _DOMAIN_TAIL_WORDS:
            return True
    return False


def _seed_domain(company_name: str, extra_json: str | None) -> str:
    """Free website from the curated seed sheet (verification_source_url etc)."""
    try:
        data = json.loads(extra_json or "{}")
    except Exception:
        return ""
    for value in data.values():
        if not isinstance(value, str) or "://" not in value:
            continue
        dom = _registrable_domain(value)
        if dom and _domain_matches_company(company_name, dom):
            return dom
    return ""


async def resolve_missing_domains(limit: int | None = None) -> int:
    """Fill company_domain for rosters without one: seed URL first (free), then
    one official-website Tavily query per company when allowed, MX-verified.
    Resolved rosters are queued for email minting on the next roster-email pass."""
    cap = limit if limit is not None else max(0, min(int(os.getenv("ROSTER_DOMAIN_LOOKUPS", "4") or 4), 25))
    if cap <= 0:
        return 0
    from app.services.web_fetch import web_search_configured
    web_ok = (web_search_configured() or bool((os.getenv("TAVILY_API_KEY") or "").strip())) and (os.getenv("ROSTER_DOMAIN_WEB") or "1").strip().lower() in {"1", "true", "yes"}
    stale = iso(utcnow() - timedelta(days=30))
    db = await get_db()
    try:
        rows = await (
            await db.execute(
                """SELECT id, company_name FROM company_rosters
                   WHERE IFNULL(company_domain,'') = ''
                     AND IFNULL(domain_checked_at,'') <= ?
                   ORDER BY (IFNULL(cik,'') != '') DESC, id
                   LIMIT ?""",
                (stale, cap),
            )
        ).fetchall()
    finally:
        await db.close()
    resolved = 0
    from app.services.email_verifier import get_mx_cached

    for row in rows:
        record = dict(row)
        roster_id = int(record["id"])
        name = record["company_name"]
        dom = ""
        seed_db = await get_db()
        try:
            seed = await (
                await seed_db.execute(
                    "SELECT extra_json FROM yucg_prospect_targets WHERE lower(company) = lower(?) LIMIT 1",
                    (name,),
                )
            ).fetchone()
        finally:
            await seed_db.close()
        if seed:
            dom = _seed_domain(name, seed["extra_json"] if not isinstance(seed, tuple) else seed[0])
        if not dom and web_ok:
            try:
                from app.services.web_contact_discovery import _tavily_search

                results = await _tavily_search(f"{name} official website", max_results=5)
            except Exception:
                results = []
            for item in results:
                cand = _registrable_domain(str((item or {}).get("url") or ""))
                if cand and _domain_matches_company(name, cand):
                    dom = cand
                    break
        mx_ok = False
        if dom:
            try:
                mx_ok, _ = await get_mx_cached(dom, None)
            except Exception:
                mx_ok = False
            if not mx_ok:
                dom = ""
        touch = await get_db()
        try:
            if dom:
                await touch.execute(
                    """UPDATE company_rosters SET company_domain=?, domain_checked_at=?, updated_at=?,
                              next_email_check_at=''
                       WHERE id=?""",
                    (dom, iso(), iso(), roster_id),
                )
                resolved += 1
            else:
                await touch.execute(
                    "UPDATE company_rosters SET domain_checked_at=? WHERE id=?", (iso(), roster_id)
                )
            await touch.commit()
        finally:
            await touch.close()
    return resolved


async def _claim_due(limit: int) -> list[dict[str, Any]]:
    now = utcnow()
    now_s = iso(now)
    lease = iso(now + timedelta(minutes=20))
    db = await get_db()
    claimed: list[dict[str, Any]] = []
    try:
        await db.execute("BEGIN IMMEDIATE")
        rows = await (
            await db.execute(
                """SELECT * FROM company_rosters
                   WHERE next_verify_at <= ?
                   ORDER BY next_verify_at, id
                   LIMIT ?""",
                (now_s, limit),
            )
        ).fetchall()
        for row in rows:
            record = dict(row)
            await db.execute(
                """UPDATE company_rosters SET next_verify_at=?, updated_at=?
                   WHERE id=? AND next_verify_at <= ?""",
                (lease, now_s, record["id"], now_s),
            )
            claimed.append(record)
        await db.commit()
        return claimed
    finally:
        await db.close()


async def refresh_roster(
    roster: dict[str, Any], tickers: dict[str, Any], *, tickers_error: str | None = None
) -> dict[str, Any]:
    roster_id = int(roster["id"])
    name = roster["company_name"]
    domain = roster.get("company_domain")
    now = iso()
    if roster.get("cik"):
        match = {"cik": roster["cik"], "ticker": roster.get("ticker") or "", "title": name}
        tickers_error = None  # the CIK is already known; the list was not needed
    else:
        match = match_public_company(name, tickers)

    people: list[dict[str, Any]] = []
    status = "unmatched"
    error = None
    mark_missing = False
    try:
        if match and match.get("cik"):
            status = "public"
            people = await fetch_form4_people(match["cik"])
            if len(people) < 3:
                extra = await fetch_10k_people(match["cik"])
                have = {p["normalized_name"] for p in people}
                for row in extra:
                    if row["normalized_name"] not in have:
                        people.append(row)
            mark_missing = bool(people)
        elif ch_key():
            ch_people = await fetch_companies_house_people(name)
            if ch_people:
                people = ch_people
                status = "register"
                # resigned_on is direct evidence; absence from the officers page
                # must NOT decay anyone, so mark_missing stays False here.
            else:
                people = await _web_people(name, domain)
                status = "web" if people else "unmatched"
        else:
            people = await _web_people(name, domain)
            status = "web" if people else "unmatched"
    except Exception as exc:
        error = str(exc)[:500]
        status = roster.get("source_status") or "pending"
    if tickers_error and not match and not error:
        # SEC was never asked, so "unmatched" would claim this is a private
        # company. Keep whatever the other sources found, record why SEC is
        # missing, and retry on the error schedule rather than the monthly one.
        error = tickers_error[:500]
        if status == "unmatched":
            status = roster.get("source_status") or "pending"

    # A failed refresh is retried on the weekly schedule; only a clean
    # "no register knows this company" waits a month.
    nxt = utcnow() + timedelta(days=verify_days() if status in ("public", "register") or error else 30)
    counts = await _upsert_people(roster_id, people, domain=domain, mark_missing=mark_missing)
    db = await get_db()
    try:
        await db.execute(
            """UPDATE company_rosters
               SET ticker=?, cik=?, source_status=?, last_crawled_at=?, last_verified_at=?,
                   next_verify_at=?, last_error=?, updated_at=?,
                   people_count=?, current_count=?
               WHERE id=?""",
            (
                (match or {}).get("ticker"),
                (match or {}).get("cik"),
                status,
                now,
                now if not error else roster.get("last_verified_at"),
                iso(nxt),
                error,
                now,
                counts["people"],
                counts["current"],
                roster_id,
            ),
        )
        await db.commit()
    finally:
        await db.close()
    return {"id": roster_id, "status": status, "people": counts["people"], "error": error}


async def drain_roster_queue() -> dict[str, Any]:
    """Enroll spreadsheet companies, then refresh a bounded due batch."""
    enrolled = await enroll_prospect_companies()
    domains = await resolve_missing_domains()
    due = await _claim_due(drain_limit())
    if not due:
        return {"ok": True, "enrolled": enrolled, "domains": domains, "claimed": 0, "refreshed": []}
    tickers: dict[str, Any] = _TICKERS or {}
    tickers_error = None
    if any(not row.get("cik") for row in due):
        tickers, tickers_error = await load_tickers_or_reason()
    refreshed = [await refresh_roster(row, tickers, tickers_error=tickers_error) for row in due]
    return {"ok": True, "enrolled": enrolled, "domains": domains, "claimed": len(due), "refreshed": refreshed}


_ROSTER_LIST_SQL = """
    SELECT r.*,
           (SELECT COUNT(*) FROM company_roster_people p
             WHERE p.roster_id = r.id AND IFNULL(p.inferred_email,'') != ''
               AND IFNULL(p.email_status,'') != 'bounced') AS emails_ready,
           (CASE WHEN r.people_count = 0 AND r.source_status = 'unmatched' THEN 1 ELSE 0 END) AS coverage_gap
    FROM company_rosters r
"""


async def list_rosters(q: str = "", limit: int = 50, only_gaps: bool = False) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 200))
    db = await get_db()
    try:
        where = "WHERE (r.company_name LIKE ? OR IFNULL(r.ticker,'') LIKE ? OR IFNULL(r.cik,'') LIKE ?)" if q.strip() else ""
        params: list[Any] = [f"%{q.strip()}%", f"%{q.strip()}%", f"%{q.strip()}%"] if q.strip() else []
        if only_gaps:
            where += (" AND" if where else " WHERE") + " r.people_count = 0 AND r.source_status = 'unmatched'"
        rows = await (
            await db.execute(
                _ROSTER_LIST_SQL + where + " ORDER BY current_count DESC, company_name LIMIT ?",
                (*params, limit),
            )
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def source_stats() -> list[dict[str, Any]]:
    """Per-source yield of the accumulated graph — what actually became an
    emailable, surviving, imported, or replied-to person. The boring stats the
    router should order by; no model inventing 'new sources' here."""
    db = await get_db()
    try:
        rows = await (
            await db.execute(
                """SELECT p.source,
                          COUNT(*) AS produced,
                          SUM(CASE WHEN p.employment='current' THEN 1 ELSE 0 END) AS current_now,
                          SUM(CASE WHEN IFNULL(p.inferred_email,'') != '' AND IFNULL(p.email_status,'')='mx_valid' THEN 1 ELSE 0 END) AS mx_emails,
                          SUM(CASE WHEN IFNULL(p.inferred_email,'') != '' AND EXISTS (
                              SELECT 1 FROM contacts c WHERE c.email = p.inferred_email COLLATE NOCASE
                          ) THEN 1 ELSE 0 END) AS imported,
                          SUM(CASE WHEN IFNULL(p.inferred_email,'') != '' AND EXISTS (
                              SELECT 1 FROM email_checks k JOIN email_candidates e ON e.id = k.candidate_id
                              WHERE e.email = p.inferred_email COLLATE NOCASE AND k.result = 'human_reply_observed'
                          ) THEN 1 ELSE 0 END) AS replied
                   FROM company_roster_people p
                   GROUP BY p.source
                   ORDER BY replied DESC, imported DESC, mx_emails DESC, produced DESC"""
            )
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def roster_detail(roster_id: int) -> dict[str, Any] | None:
    db = await get_db()
    try:
        row = await (await db.execute("SELECT * FROM company_rosters WHERE id=?", (roster_id,))).fetchone()
        if not row:
            return None
        people = await (
            await db.execute(
                """SELECT full_name, title, role_type, source, inferred_email, employment,
                          last_seen_at, missed_checks, source_url, email_status, email_checked_at, verdict, verdict_reason, email_provider_checked_at
                   FROM company_roster_people WHERE roster_id=?
                   ORDER BY employment, title, full_name""",
                (roster_id,),
            )
        ).fetchall()
        out = dict(row)
        out["people"] = [dict(p) for p in people]
        return out
    finally:
        await db.close()
