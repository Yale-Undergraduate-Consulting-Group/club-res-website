"""LLM adjudication of roster people: does this person exist, who exactly, and what's their title?

Regex over EDGAR gets candidates; only reading filing prose, IR pages, and press can
reconcile scrambled filer-typed names ("Salles Pedro Moreira" vs "Pedro Moreira
Salles"), reject ghosts, and attach titles. Haiku-class model via the existing capped
Bedrock path; it may ONLY verdict the names we already hold — it can never invent a
person, an email, or a source. Verdicts carry evidence URLs or get ignored.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from app.database import get_db
from app.services import roster_watch as RW
from app.services.contact_scraper import looks_like_person_name

RECHECK_DAYS = 45
GHOST_STATES = {"ghost"}


def _rename_ok(candidate: str) -> bool:
    """Rename acceptance for evidence-corrected names: looser than the scrape gate,
    which caps at 4 tokens — Lusophone names legitimately run to 5-6."""
    parts = (candidate or "").split()
    if not (2 <= len(parts) <= 6):
        return False
    return all(re.fullmatch(r"[A-Za-zÀ-ÿ'.]+", w) for w in parts)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    return (dt or _now()).isoformat()


def _adj_limit() -> int:
    return max(0, min(int(os.getenv("ROSTER_ADJ_LIMIT", "2") or 2), 10))


def token_key(name: str) -> str:
    """Order-insensitive identity: handles filer-typed surname rotations."""
    return " ".join(sorted(re.findall(r"[a-z0-9]+", name.lower())))


async def fetch_20f_section(cik: str, max_chars: int = 6000) -> str:
    """Item 6A 'Directors and Senior Management' prose from the latest 20-F, if any."""
    raw = await RW._http_get(RW.SEC_SUBMISSIONS_URL.format(cik=cik))
    payload = json.loads(raw.decode("utf-8"))
    recent = (payload.get("filings") or {}).get("recent") or {}
    for form, accession, document in zip(
        recent.get("form") or [],
        recent.get("accessionNumber") or [],
        recent.get("primaryDocument") or [],
    ):
        if form != "20-F":
            continue
        url = RW._archive_url(cik, accession, document)
        html = (await RW._http_get(url)).decode("utf-8", "ignore")
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"&#8217;|&rsquo;", "'", text)
        text = re.sub(r"&[a-z#0-9]+;", " ", text)
        text = re.sub(r"\s+", " ", text)
        low = text.lower()
        matches = [m.start() for m in re.finditer(r"directors and senior management", low)]
        if not matches:
            matches = [m.start() for m in re.finditer(r"senior management", low)]
        # The table of contents repeats the heading with bare page numbers; the
        # real Item 6A body follows with prose about people. Pick the last
        # occurrence that is followed by people-talk, not a contents line.
        chosen = -1
        for i in reversed(matches):
            seg = text[i : i + 1200].lower()
            if re.search(r"(\bmr\.|\bmrs\.|\bms\.|\bbeneficial owner|\bofficer since|\bdirector since|\bis a member of|\bbeen a|\bexecutive officer)", seg):
                chosen = i
                break
        if chosen < 0:
            chosen = matches[-1] if matches else -1
        if chosen < 0:
            return ""
        return text[chosen : chosen + max_chars]
    return ""


async def _fetch_page(url: str) -> str:
    import httpx

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        response = await client.get(url, headers={"User-Agent": RW.sec_user_agent()})
        if response.status_code != 200:
            return ""
        body = response.text[:400_000]
    text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", body, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text)[:2600]


def _allowed_evidence(url: str, domain: str) -> bool:
    if not url.startswith("https://") or not domain:
        return False
    host = re.sub(r"^www\.", "", url[8:].split("/")[0])
    return host == domain or host.endswith("." + domain)


async def build_dossier(roster: dict[str, Any], people: list[dict[str, Any]]) -> dict[str, Any]:
    from app.services.web_contact_discovery import _tavily_search

    domain = roster.get("company_domain") or ""
    web_text = ""
    web_names: list[str] = []
    from app.services.web_fetch import web_search_configured
    if web_search_configured() or (os.getenv("TAVILY_API_KEY") or "").strip():
        try:
            results = await _tavily_search(
                f"{roster['company_name']} directors senior management leadership team",
                max_results=6,
            )
        except Exception:
            results = []
        pages = 0
        for item in results:
            url = str((item or {}).get("url") or "")
            if item.get("content"):
                web_text += str(item["content"])[:700] + "\n"
            if pages < 2 and _allowed_evidence(url, domain):
                try:
                    web_text += await _fetch_page(url) + "\n"
                except Exception:
                    pass
                pages += 1
            m = re.search(r"linkedin\.com/in/([a-zA-Z0-9_-]+)", url)
            if m:
                web_names.append(m.group(1).replace("-", " ").title())
    filing_text = ""
    if roster.get("cik"):
        try:
            filing_text = await fetch_20f_section(str(roster["cik"]))
        except Exception:
            filing_text = ""
    return {"web_text": web_text[:5200], "web_names": web_names[:30], "filing_text": filing_text}


def _prompt(company: str, people: list[dict[str, Any]], dossier: dict[str, Any]) -> str:
    rows = [{"name": p["full_name"], "title": p.get("title") or "", "source": p.get("source")} for p in people]
    return f"""You audit a contact roster for {company}. Candidates come from SEC filings and may have
filer-typed scrambled name order (e.g. "SALLES Pedro Moreira" meaning "Pedro Moreira Salles"),
duplicates, or people who are not real individuals.

CANDIDATES (JSON): {json.dumps(rows, ensure_ascii=False)[:3500]}

FILING EXCERPT (20-F senior management section): {dossier['filing_text'][:4500]}

WEB EXCERPTS (company IR/press): {dossier['web_text'][:4500]}

WEB-LINKED PROFILE NAMES (from search-result /in/ URLs): {json.dumps(dossier['web_names'][:30], ensure_ascii=False)}

For EVERY candidate output one verdict. Rules:
- status "real": the person is corroborated as an actual named individual connected to this company
  (filing excerpt, IR page, or press), even if the candidate's name order is scrambled — give corrected_name in natural order.
- status "ghost": no evidence this is a real individual person at this company (fragment, org, unverifiable).
- NEVER invent people. Never output an email. Only fix names/titles from the evidence text.
- title: only from the evidence; empty string if none.
Return JSON only: {{"verdicts":[{{"name":"<candidate name as given>","status":"real|ghost","corrected_name":"","title":"","reason":"<max 90 chars>"}}]}}"""


def _parse_verdicts(data: dict[str, Any] | None, people: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    if not data or not isinstance(data.get("verdicts"), list):
        return out
    by_key = {}
    for p in people:
        by_key[token_key(p["full_name"])] = p
        by_key[p.get("normalized_name") or ""] = p
    for row in data["verdicts"]:
        if not isinstance(row, dict):
            continue
        given = str(row.get("name") or "").strip()
        person = by_key.get(token_key(given)) or by_key.get(
            " ".join(sorted(re.findall(r"[a-z0-9]+", given.lower())))
        )
        if not person:
            continue
        out.append(
            {
                "person": person,
                "status": "real" if str(row.get("status") or "").lower() == "real" else "ghost",
                "corrected_name": str(row.get("corrected_name") or "").strip()[:120],
                "title": str(row.get("title") or "").strip()[:160],
                "reason": str(row.get("reason") or "").strip()[:300],
            }
        )
    return out


async def _apply(verdicts: list[dict[str, Any]]) -> int:
    if not verdicts:
        return 0
    now = _iso()
    db = await get_db()
    touched = 0
    try:
        for v in verdicts:
            person = v["person"]
            corrected = v["corrected_name"]
            rename = ""
            if (
                corrected
                and corrected != person["full_name"]
                and _rename_ok(corrected)
                and set(token_key(corrected).split()) & set(token_key(person["full_name"]).split())
            ):
                rename = corrected
            if v["status"] == "ghost":
                await db.execute(
                    """UPDATE company_roster_people
                       SET employment='ghost', verdict='ghost', verdict_reason=?, adjudicated_at=?
                       WHERE id=?""",
                    (v["reason"][:300], now, person["id"]),
                )
            else:
                if rename:
                    from app.services.contact_scraper import person_name_key

                    await db.execute(
                        """UPDATE company_roster_people
                           SET full_name=?, normalized_name=?, title=CASE WHEN ? != '' THEN ? ELSE title END,
                               verdict='real', verdict_reason=?, adjudicated_at=?, employment='current'
                           WHERE id=? AND NOT EXISTS (
                               SELECT 1 FROM company_roster_people q
                               WHERE q.roster_id=? AND q.normalized_name=? AND q.id != ?
                           )""",
                        (
                            rename,
                            person_name_key(rename),
                            v["title"],
                            v["title"],
                            v["reason"][:300],
                            now,
                            person["id"],
                            person["roster_id"],
                            person_name_key(rename),
                            person["id"],
                        ),
                    )
                else:
                    await db.execute(
                        """UPDATE company_roster_people
                           SET title=CASE WHEN ? != '' THEN ? ELSE title END,
                               verdict='real', verdict_reason=?, adjudicated_at=?, employment='current'
                           WHERE id=?""",
                        (v["title"], v["title"], v["reason"][:300], now, person["id"]),
                    )
            touched += 1
        # recount current vs total
        roster_ids = sorted({v["person"]["roster_id"] for v in verdicts})
        for rid in roster_ids:
            counts = await (
                await db.execute(
                    """SELECT COUNT(*) n, SUM(CASE WHEN employment='current' THEN 1 ELSE 0 END) c
                       FROM company_roster_people WHERE roster_id=?""",
                    (rid,),
                )
            ).fetchone()
            await db.execute(
                "UPDATE company_rosters SET people_count=?, current_count=?, updated_at=? WHERE id=?",
                (int(counts["n"] or 0), int(counts["c"] or 0), now, rid),
            )
        await db.commit()
        return touched
    finally:
        await db.close()


async def adjudicate_roster(roster: dict[str, Any]) -> dict[str, Any]:
    db = await get_db()
    try:
        people = await (
            await db.execute(
                "SELECT id, roster_id, normalized_name, full_name, title, source, employment FROM company_roster_people WHERE roster_id=? AND employment IN ('current','unverified')",
                (int(roster["id"]),),
            )
        ).fetchall()
    finally:
        await db.close()
    rows = [dict(p) for p in people]
    if not rows:
        return {"id": roster["id"], "verdicts": 0}
    from app.services.llm import complete_json, rank_model_id

    dossier = await build_dossier(roster, rows)
    try:
        data = await asyncio.to_thread(complete_json, _prompt(roster["company_name"], rows, dossier), rank_model_id())
    except Exception:
        data = None
    verdicts = _parse_verdicts(data, rows)
    touched = await _apply(verdicts)
    return {"id": int(roster["id"]), "company": roster["company_name"], "verdicts": touched, "ghosts": sum(1 for v in verdicts if v["status"] == "ghost")}


async def drain_roster_adjudication() -> dict[str, Any]:
    limit = _adj_limit()
    if limit == 0:
        return {"ok": True, "adjudicated": 0, "skipped": "disabled"}
    stale = _iso(_now() - timedelta(days=RECHECK_DAYS))
    db = await get_db()
    try:
        rows = await (
            await db.execute(
                """SELECT * FROM company_rosters r
                   WHERE r.current_count > 0
                     AND NOT EXISTS (
                        SELECT 1 FROM company_roster_people p
                        WHERE p.roster_id = r.id AND IFNULL(p.adjudicated_at,'') > ?
                     )
                   ORDER BY r.current_count DESC
                   LIMIT ?""",
                (stale, limit),
            )
        ).fetchall()
        due = [dict(r) for r in rows]
        # Claim immediately so a crashed pass doesn't re-spend Bedrock credits hourly.
        if due:
            ids = [int(d["id"]) for d in due]
            await db.execute(
                f"""UPDATE company_roster_people SET adjudicated_at=?
                    WHERE roster_id IN ({",".join("?" * len(ids))}) AND employment='current'
                      AND IFNULL(adjudicated_at,'') = ''""",
                (_iso(), *ids),
            )
            await db.commit()
    finally:
        await db.close()
    results = []
    for roster in due:
        try:
            results.append(await adjudicate_roster(roster))
        except Exception:
            continue
    return {"ok": True, "adjudicated": len(results), "results": results}
