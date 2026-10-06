"""LLM roster adjudication: ghosts tombstoned, scrambles reconciled, LLM-down harmless.
From backend/: python3 tests/test_roster_adjudicate.py"""
from __future__ import annotations

import asyncio
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
os.environ["JWT_SECRET"] = "p-adjudicate-secret-not-a-known-default-xx"
os.environ["ROSTER_ADJ_LIMIT"] = "2"
os.environ["ROSTER_SEC_PAUSE_SEC"] = "0"
os.environ.pop("TAVILY_API_KEY", None)
os.environ.pop("COMPANIES_HOUSE_API_KEY", None)

from app.database import get_db, init_db  # noqa: E402
from app.services import roster_adjudicate as A  # noqa: E402
from app.services import roster_email as RE  # noqa: E402
from app.services import roster_watch as R  # noqa: E402

PEOPLE = [
    {"full_name": "Salles Pedro Moreira", "normalized_name": "salles pedro moreira", "title": "", "role_type": "officer", "source": "sec_form4"},
    {"full_name": "Setubal Roberto Egydio", "normalized_name": "setubal roberto egydio", "title": "", "role_type": "officer", "source": "sec_form4"},
    {"full_name": "De Moraes Pedro Luiz Bodin", "normalized_name": "de moraes pedro luiz bodin", "title": "", "role_type": "officer", "source": "sec_form4"},
    # An entity whose name gives it away never reaches the roster (ingestion
    # refuses legal-form words); one that does not is left to the model.
    {"full_name": "Matrix Holdings LLC", "normalized_name": "matrix holdings llc", "title": "", "role_type": "officer", "source": "sec_form4"},
    {"full_name": "Aurora Ventures", "normalized_name": "aurora ventures", "title": "", "role_type": "officer", "source": "sec_form4"},
]

MODEL_VERDICTS = {
    "verdicts": [
        {"name": "Salles Pedro Moreira", "status": "real", "corrected_name": "Pedro Moreira Salles", "title": "Chief Executive Officer", "reason": "20-F item 6A lists him as CEO"},
        {"name": "Setubal Roberto Egydio", "status": "real", "corrected_name": "", "title": "Chairman", "reason": "20-F chairman"},
        {"name": "De Moraes Pedro Luiz Bodin", "status": "real", "corrected_name": "Pedro Luiz Bodin de Moraes", "title": "", "reason": "IR bio"},
        {"name": "Aurora Ventures", "status": "ghost", "corrected_name": "", "title": "", "reason": "entity, not a person"},
        {"name": "Nobody Invento", "status": "real", "corrected_name": "", "title": "", "reason": "must be ignored: not among candidates"},
    ]
}


async def _run() -> None:
    await init_db()
    db = await get_db()
    try:
        await db.execute("INSERT INTO users (id, email, role, is_active) VALUES (1, 'a@yale.edu', 'standard', 1)")
        await db.commit()
    finally:
        await db.close()
    roster = await R._ensure_roster("Banco Exemplo")
    await R._upsert_people(int(roster["id"]), PEOPLE, domain="bancoexemplo.com.br", mark_missing=False)

    with patch("app.services.llm.complete_json", return_value=MODEL_VERDICTS):
        result = await A.adjudicate_roster(roster)
    assert result["verdicts"] >= 3, result  # 'Nobody Invento' matched nothing

    detail = await R.roster_detail(int(roster["id"]))
    by_name = {p["full_name"]: p for p in detail["people"]}
    assert "Pedro Moreira Salles" in by_name, sorted(by_name)
    assert by_name["Pedro Moreira Salles"]["title"] == "Chief Executive Officer"
    assert by_name["Pedro Moreira Salles"]["verdict"] == "real"
    assert by_name["Setubal Roberto Egydio"]["title"] == "Chairman"
    assert by_name["Pedro Luiz Bodin de Moraes"]["verdict"] == "real"
    assert "Matrix Holdings LLC" not in by_name, sorted(by_name)
    ghost = next(p for p in detail["people"] if p["full_name"] == "Aurora Ventures")
    assert ghost["employment"] == "ghost" and ghost["verdict"] == "ghost"
    assert detail["current_count"] == 3 and detail["people_count"] == 4

    # Ghosts never surface in cache reads.
    warm = await RE.cached_roster_contacts("Banco Exemplo", "bancoexemplo.com.br")
    names = {row["name"] for row in warm}
    assert "Aurora Ventures" not in names and "Pedro Moreira Salles" in names

    # LLM down: zero verdicts, no crash, no ghosting.
    def boom(*args, **kwargs):
        raise RuntimeError("bedrock unavailable")

    db = await get_db()
    try:
        await db.execute("UPDATE company_roster_people SET adjudicated_at=''")
        await db.commit()
    finally:
        await db.close()
    with patch("app.services.llm.complete_json", side_effect=boom):
        second = await A.adjudicate_roster(roster)
    assert second["verdicts"] == 0, second
    detail = await R.roster_detail(int(roster["id"]))
    assert all(p["employment"] != "ghost" or p["full_name"] == "Aurora Ventures" for p in detail["people"])

    # Drain claims due rosters, then rests for 45 days.
    with patch("app.services.llm.complete_json", return_value=MODEL_VERDICTS):
        drained = await A.drain_roster_adjudication()
    assert drained["adjudicated"] == 1, drained
    again = await A.drain_roster_adjudication()
    assert again["adjudicated"] == 0, again


def test_roster_adjudicate() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    test_roster_adjudicate()
    print("ok")
