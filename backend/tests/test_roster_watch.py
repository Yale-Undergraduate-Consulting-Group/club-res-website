"""Hermetic SEC roster watch: parse, match, weekly still-there, no live EDGAR.
From backend/: python3 tests/test_roster_watch.py"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.name}"
os.environ["JWT_SECRET"] = "p-roster-watch-secret-not-a-known-default-xx"
os.environ["ROSTER_SEC_PAUSE_SEC"] = "0"
os.environ["ROSTER_VERIFY_DAYS"] = "7"
os.environ["ROSTER_LEFT_AFTER_MISSES"] = "2"
os.environ["ROSTER_DRAIN_LIMIT"] = "8"
os.environ["ROSTER_WEB_ON_ENROLL"] = "0"
os.environ["ROSTER_DOMAIN_WEB"] = "0"
os.environ.pop("COMPANIES_HOUSE_API_KEY", None)
os.environ.pop("TAVILY_API_KEY", None)

from app.database import get_db, init_db  # noqa: E402
from app.services import roster_watch as R  # noqa: E402
from app.routers.yucg_prospects import get_roster, get_rosters  # noqa: E402

FORM4_WANG = """<?xml version="1.0"?>
<ownershipDocument>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>Wang Cheng-Wei</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship>
      <isDirector>false</isDirector>
      <isOfficer>true</isOfficer>
      <officerTitle>General Manager - Garmin Corp.</officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
</ownershipDocument>
"""
FORM4_PEMBLE = """<?xml version="1.0"?>
<ownershipDocument>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>PEMBLE CLIFTON A</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship>
      <isDirector>true</isDirector>
      <isOfficer>true</isOfficer>
      <officerTitle>President and CEO</officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
</ownershipDocument>
"""
TENK = """
<html><body>
Information about our Executive Officers
Clifton A. Pemble President and Chief Executive Officer 60 Douglas G. Boessen Chief Financial Officer and Treasurer 63
</body></html>
"""
TICKERS = {
    "0": {"cik_str": 1121788, "ticker": "GRMN", "title": "GARMIN LTD"},
    "1": {"cik_str": 773840, "ticker": "HON", "title": "HONEYWELL INTERNATIONAL INC"},
    "2": {"cik_str": 790070, "ticker": "SKYW", "title": "SKYWEST INC"},
}


def test_parsers_and_match() -> None:
    wang = R.parse_form4_xml(FORM4_WANG)
    assert wang and wang["full_name"] == "Cheng-Wei Wang"
    assert wang["title"].startswith("General Manager")
    assert wang["role_type"] == "officer"
    pemble = R.parse_form4_xml(FORM4_PEMBLE)
    assert pemble and pemble["full_name"] == "Clifton A. Pemble"
    officers = R.parse_10k_officers(TENK)
    names = {row["full_name"] for row in officers}
    assert "Clifton A. Pemble" in names
    assert "Douglas G. Boessen" in names
    garmin = R.match_public_company("Garmin", TICKERS)
    assert garmin and garmin["ticker"] == "GRMN" and garmin["cik"] == "0001121788"
    honey = R.match_public_company("Honeywell Aerospace", TICKERS)
    assert honey and honey["ticker"] == "HON"
    sky = R.match_public_company("SkyWest Airlines", TICKERS)
    assert sky and sky["ticker"] == "SKYW"
    assert R.match_public_company("ATR", TICKERS) is None


async def _http(url: str) -> bytes:
    if "company_tickers" in url:
        return json.dumps(TICKERS).encode()
    if "submissions" in url:
        return json.dumps(
            {
                "filings": {
                    "recent": {
                        "form": ["4", "4", "10-K"],
                        "accessionNumber": [
                            "0001193125-26-000001",
                            "0001193125-26-000002",
                            "0001193125-26-000003",
                        ],
                        "primaryDocument": ["ownership.xml", "ownership.xml", "grmn.htm"],
                    }
                }
            }
        ).encode()
    if "000119312526000001" in url:
        return FORM4_WANG.encode()
    if "000119312526000002" in url:
        return FORM4_PEMBLE.encode()
    if url.endswith("grmn.htm"):
        return TENK.encode()
    raise AssertionError(url)


async def _run() -> None:
    R._TICKERS = None
    R._TICKERS_AT = None
    await init_db()
    db = await get_db()
    try:
        await db.execute("INSERT INTO users (id, email, role, is_active) VALUES (1, 'a@yale.edu', 'standard', 1)")
        await db.execute("INSERT INTO yucg_prospect_targets (company, sector) VALUES ('Garmin', 'Aviation')")
        await db.execute("INSERT INTO yucg_prospect_targets (company, sector) VALUES ('ATR', 'Regional Turboprop OEM')")
        await db.commit()
    finally:
        await db.close()

    with patch.object(R, "_http_get", side_effect=_http):
        result = await R.drain_roster_queue()
    assert result["claimed"] >= 1, result
    garmin = next(item for item in result["refreshed"] if item["status"] == "public")
    detail = await R.roster_detail(garmin["id"])
    assert detail and detail["ticker"] == "GRMN"
    names = {p["full_name"] for p in detail["people"]}
    assert "Cheng-Wei Wang" in names
    assert "Clifton A. Pemble" in names
    assert all(p["employment"] == "current" for p in detail["people"])
    wang = next(p for p in detail["people"] if "Wang" in p["full_name"])
    assert wang["inferred_email"] is None  # no domain on spreadsheet enroll

    listed = await get_rosters({"id": 1, "role": "standard"}, q="Garmin", limit=10)
    assert listed["rosters"]
    api = await get_roster(garmin["id"], {"id": 1, "role": "standard"})
    assert api["cik"] == "0001121788"

    await R.remember_discovery_people(
        "Garmin",
        "garmin.com",
        [{"name": "Ada Lovelace", "title": "VP Engineering", "email": "ada.lovelace@garmin.com"}],
    )
    detail = await R.roster_detail(garmin["id"])
    ada = next(p for p in detail["people"] if p["full_name"] == "Ada Lovelace")
    assert ada["source"] == "discovery"
    assert ada["inferred_email"] == "ada.lovelace@garmin.com"

    # First miss keeps current; second miss marks left. Discovery names stay.
    await R._upsert_people(
        garmin["id"],
        [
            {
                "full_name": "Clifton A. Pemble",
                "normalized_name": "clifton a pemble",
                "title": "President and CEO",
                "role_type": "officer",
                "source": "sec_form4",
            }
        ],
        domain="garmin.com",
        mark_missing=True,
    )
    detail = await R.roster_detail(garmin["id"])
    wang = next(p for p in detail["people"] if "Wang" in p["full_name"])
    assert wang["employment"] == "current" and wang["missed_checks"] == 1
    ada = next(p for p in detail["people"] if p["full_name"] == "Ada Lovelace")
    assert ada["employment"] == "current"
    await R._upsert_people(
        garmin["id"],
        [
            {
                "full_name": "Clifton A. Pemble",
                "normalized_name": "clifton a pemble",
                "title": "President and CEO",
                "role_type": "officer",
                "source": "sec_form4",
            }
        ],
        domain="garmin.com",
        mark_missing=True,
    )
    detail = await R.roster_detail(garmin["id"])
    wang = next(p for p in detail["people"] if "Wang" in p["full_name"])
    assert wang["employment"] == "left"
    pemble = next(p for p in detail["people"] if "Pemble" in p["full_name"])
    assert pemble["employment"] == "current"
    assert pemble["inferred_email"] == "clifton.pemble@garmin.com"


def test_roster_watch() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    test_parsers_and_match()
    test_roster_watch()
    print("ok")
