"""
YUCGoutreach company discovery — same multi-source pipeline as the Scraper:
domain crawl + LinkedIn (Apify) + Tavily web discovery → merge → verify (MX + AI).
Stores verified prospects in yucgoutreach_prospects for SQL queries and Excel export.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from contextvars import ContextVar
from typing import Any
import logging

logger = logging.getLogger(__name__)

from app.database import get_db, row_to_dict
from app.services.contact_ai_review import _log_row
from app.services.contact_merge import merge_contacts
from app.services.company_email_cache import build_email_for_person_sync
from app.services.contact_scraper import (
    is_employee_outreach_email,
    is_heuristic_junk_contact,
    is_valid_person_contact,
    looks_like_person_name,
    normalize_domain,
    sanitize_email,
    scrape_contacts_from_domain,
)
from app.services.contact_verify_pipeline import run_contact_verify_pipeline
from app.services.web_contact_discovery import discover_contacts_from_web


_active_lease: ContextVar[str | None] = ContextVar("yucgoutreach_lease", default=None)


class DiscoveryLeaseLost(RuntimeError):
    """This worker no longer owns the durable discovery run."""

YUCG_MAX_PROSPECTS = int(os.getenv("YUCG_MAX_PROSPECTS", "800"))


async def _llm_json(prompt: str) -> dict[str, Any]:
    from app.services.llm import complete_json, rank_model_id

    return await asyncio.to_thread(complete_json, prompt, rank_model_id()) or {}


async def _tavily_search(query: str, max_results: int = 8) -> list[dict[str, Any]]:
    # One search helper for the whole run, so a query the web stage already
    # asked is shared rather than sent again, and a failed one is counted.
    from app.services.web_contact_discovery import _tavily_search as search

    return await search(query, max_results=max_results)


_NAME_SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv", "v", "phd", "md", "mba", "cpa", "cfa", "esq"})


def _clean_title(title: str | None, name: str, company: str) -> str | None:
    """A job title out of a LinkedIn headline.

    Search results hand back the page title, "Matthew McGowan - Director,
    Content Strategy & Analysis, HBO Max | LinkedIn", and that whole string was
    saved as the title. Keep the first part that is neither the person's name,
    the company's name, nor LinkedIn; a headline that is only name and company
    ("Dana Lichtenstein - HBO & HBO Max") has no title at all.
    """
    from app.services.company_email_cache import text_names_brand

    text = re.sub(r"\s*\|\s*LinkedIn\s*$", "", (title or "").strip(), flags=re.I)
    if not text:
        return None
    people = {n.lower() for n in (name, " ".join(_split_first_last(name))) if n and n.strip()}
    for part in re.split(r"\s+[-|–—·]\s+", text):
        part = part.strip(" -|,")
        if not part or part.lower() in people or part.lower() == "linkedin":
            continue
        # The company's own name, alone or with its sub-brands ("HBO & HBO
        # Max", "Warner Bros. Discovery"), is where they work, not what they do.
        if company and text_names_brand(part, company) and len(part.split()) <= 5 \
                and not re.search(r"\b(?:head|director|manager|lead|officer|president|vp|chief|coordinator|analyst|engineer|associate|specialist|partner|producer|editor)\b", part, re.I):
            continue
        # A headline cut short leaves "Director of"; the dangling words go.
        part = re.sub(r"(?:\s+(?:of|and|&|for|at|in|the|to|,))+\s*$", "", part, flags=re.I).strip(" ,")
        return part[:300] or None
    return None


def _split_first_last(full_name: str) -> tuple[str, str]:
    name = (full_name or "").strip()
    if not name:
        return "", ""
    # A word with a digit in it is never part of a person's name: it is the id
    # LinkedIn adds to a taken profile address ("maggie-schumann-55937b5a"),
    # which otherwise became the surname "55937b5a".
    parts = [p for p in re.split(r"[\s,]+", name) if p and not re.search(r"\d", p)]
    # A generation or a degree is not a surname: "David King, III" was saved
    # as David / III and shown as "David Iii".
    while len(parts) > 1 and parts[-1].strip(".").lower() in _NAME_SUFFIXES:
        parts.pop()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[-1]


async def _company_meta(company_name: str, domain: str) -> dict[str, str]:
    results = await _tavily_search(
        f"{company_name} company industry headquarters employee count {domain}".strip(),
        max_results=5,
    )
    if not results:
        return {"country": "", "employees": "", "industry": "", "keywords": "", "keywords_2": ""}
    snippet = "\n".join(f"{r.get('title')} — {r.get('content', '')[:300]}" for r in results[:4])
    try:
        data = await _llm_json(
            f"""From text about "{company_name}", return ONLY JSON:
{{"country":"","employees":"","industry":"","keywords_1":"","keywords_2":""}}

Text:
{snippet[:2500]}"""
        )
        if not data:
            raise RuntimeError("company meta empty")
    except Exception:
        return {"country": "", "employees": "", "industry": "", "keywords": "", "keywords_2": ""}
    return {
        "country": str(data.get("country") or ""),
        "employees": str(data.get("employees") or ""),
        "industry": str(data.get("industry") or ""),
        "keywords": str(data.get("keywords_1") or data.get("keywords") or ""),
        "keywords_2": str(data.get("keywords_2") or ""),
    }


async def _load_custom_patterns() -> list[str]:
    db = await get_db()
    try:
        cur = await db.execute("SELECT pattern FROM custom_email_formats ORDER BY priority DESC")
        rows = await cur.fetchall()
        return [r["pattern"] for r in rows if r.get("pattern")]
    except Exception:
        return []
    finally:
        await db.close()


def _title_hints_from_spec(spec: dict[str, Any]) -> str:
    raw = spec.get("research_json") or ""
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = {}
    if isinstance(data, dict):
        return str(data.get("title_hints") or "").strip()[:500]
    return ""


def _entity_from_spec(spec: dict[str, Any], company: str | None = None) -> dict[str, Any]:
    """The run's entity profile from research_json["entity"].

    Runs made before the profile existed carry none. They get one built from
    the company name alone - its brand words, no exclusions, no target
    country - under which entity_gate keeps everyone, as before."""
    raw = spec.get("research_json") or ""
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = {}
    entity = data.get("entity") if isinstance(data, dict) else None
    if isinstance(entity, dict) and (entity.get("legal_name") or entity.get("display_name")):
        return entity
    from app.services.company_email_cache import company_brand_words

    name = (company if company is not None else spec.get("company_name") or "").strip()
    return {
        "legal_name": name, "display_name": name, "brand_words": company_brand_words(name),
        "hq_country": None, "hq_city": None, "mail_domain": None, "mail_domain_evidence": 0,
        "alt_mail_domains": [], "exclude": [], "target_country": None, "source": "fallback",
    }


def _gate_rows(rows: list[dict], entity: dict[str, Any], skips: dict, company: str) -> list[dict]:
    """People from a source, less the ones that are not a person or not the
    target entity. Every source passes through here before the merge, and the
    save loop runs it once more on what is about to be stored."""
    from app.services.web_contact_discovery import entity_gate, note_skip

    kept: list[dict] = []
    for c in rows:
        name = (c.get("name") or "").strip()
        if not looks_like_person_name(name, company):
            note_skip(skips, "not_a_person", name)
            continue
        keep, reason = entity_gate(
            c.get("title") or "", c.get("discovery_context") or "",
            c.get("source_url") or c.get("linkedin_url") or "", c.get("email") or "", entity,
        )
        if not keep:
            note_skip(skips, reason, name)
            continue
        kept.append(c)
    return kept


def _grounded_at_company(
    name: str, title: str, results: list[dict[str, Any]], company_name: str, domain: str,
) -> str:
    """Why this search reading believes the person works at the company.

    Returns the evidence kind, or "" when the results only prove that the name
    and the company appeared on the same page. The model does not get to be the
    evidence: the page it read has to say so.
    """
    from app.services.web_contact_discovery import (
        MENTION_WINDOW, PUBLIC_OFFICE_TITLE, _host_is_company, _names_the_company,
    )

    if PUBLIC_OFFICE_TITLE.search(f"{title} {name}"):
        return ""
    needle = re.sub(r"\s+", " ", name).strip().lower()
    if not needle:
        return ""
    for r in results:
        url = str(r.get("url") or "")
        blob = f"{r.get('title') or ''}\n{r.get('content') or ''}"
        low = blob.lower()
        at = low.find(needle)
        if at < 0:
            continue
        if PUBLIC_OFFICE_TITLE.search(blob[max(0, at - MENTION_WINDOW):at + MENTION_WINDOW]):
            continue
        if _host_is_company(url, domain):
            return "company_site"
        on_profile = "linkedin.com/in/" in url.lower()
        near = blob if on_profile else blob[max(0, at - MENTION_WINDOW):at + MENTION_WINDOW]
        if _names_the_company(near, company_name, domain):
            return "linkedin_profile" if on_profile else "press_mention"
    return ""


async def _tavily_name_seeds(
    company_name: str, domain: str, max_n: int, custom_patterns: list[str], title_hints: str = "",
    *, entity: dict[str, Any] | None = None, skips: dict | None = None,
) -> list[dict]:
    """Supplement merged list with Tavily+LLM name extraction when Apify/web yield few rows."""
    from app.services.web_contact_discovery import SEARCH_PAGE_RESULTS, entity_gate, note_skip
    from app.services.web_fetch import web_search_configured
    if max_n <= 0 or not (web_search_configured() or (os.getenv("TAVILY_API_KEY") or "").strip()):
        return []
    # The web stage's first query, word for word: the run asks it once and
    # both stages read the answer (a second copy used to go out in parallel).
    hints = (title_hints or "").strip()
    query = (f'{company_name} {hints} site:linkedin.com/in' if hints
             else f'{company_name} leadership OR executives site:linkedin.com/in')
    results = await _tavily_search(query, max_results=SEARCH_PAGE_RESULTS)
    if not results:
        return []
    snippet = "\n".join(
        f"{r.get('title')}\n{r.get('url')}\n{r.get('content', '')[:400]}" for r in results[:20]
    )
    try:
        data = await _llm_json(
            f"""Extract up to {max_n} people at "{company_name}"{f' prioritizing {title_hints}' if title_hints else ''}. JSON only:
{{"people":[{{"full_name":"","title":"","linkedin_url":""}}]}}

Results:
{snippet[:5000]}"""
        )
        if not data:
            return []
    except Exception:
        return []
    people = data.get("people") if isinstance(data, dict) else None
    if not isinstance(people, list):
        return []
    dom = normalize_domain(domain) if domain else ""
    out: list[dict] = []
    for p in people:
        if not isinstance(p, dict):
            continue
        name = str(p.get("full_name") or p.get("name") or "").strip()
        if not looks_like_person_name(name, company_name):
            continue
        title = str(p.get("title") or "")[:300]
        # The model is reading search snippets, and a search for a company
        # returns pages that merely mention it - a bill, a lawsuit, a rival's
        # hire. Without this check every name in that reading became
        # first.last@<company>, which is how a US Congresswoman turned into a
        # chewy.com contact. The page has to tie this person to this company.
        evidence = _grounded_at_company(name, title, results, company_name, dom)
        if not evidence:
            continue
        # The page that names them decides the entity and the country, as it
        # does for the web stage.
        needle = re.sub(r"\s+", " ", name).strip().lower()
        page = next((r for r in results
                     if needle in f"{r.get('title') or ''}\n{r.get('content') or ''}".lower()), {})
        keep, reason = entity_gate(str(page.get("title") or ""), str(page.get("content") or ""),
                                   str(page.get("url") or p.get("linkedin_url") or ""), "", entity)
        if not keep:
            note_skip(skips, reason, name)
            continue
        email = ""
        if dom:
            email = build_email_for_person_sync(name, dom, custom_patterns=custom_patterns) or ""
        if not email:
            continue
        out.append(
            {
                "name": name,
                "email": sanitize_email(email),
                "title": title,
                "company": company_name,
                "company_domain": dom,
                "linkedin_url": str(p.get("linkedin_url") or "").strip() or None,
                "contact_source": "web_discovery",
                "affiliation_evidence": evidence,
                "discovery_context": f"Named in search results, tied to {company_name} ({evidence})",
            }
        )
        if len(out) >= max_n:
            break
    return out


def _quality_score(contact: dict, title_hints: str = "") -> float:
    score = 40.0
    ev = contact.get("email_verification_status") or ""
    if ev == "valid":
        score += 28
    elif ev == "likely_valid":
        score += 18
    elif ev == "invalid":
        score -= 25
    ai = contact.get("ai_verdict") or ""
    if ai == "real":
        score += 22
    elif ai == "suspicious":
        score += 6
    elif ai == "junk":
        score -= 40
    src = contact.get("contact_source") or ""
    if src in ("domain_scrape", "roster_sec", "roster_cache"):
        score += 12
    elif src == "web_discovery":
        score += 8
    if contact.get("linkedin_url"):
        score += 10
    if contact.get("confidence") == "high":
        score += 8
    blob = f"{contact.get('title') or ''} {contact.get('name') or ''}".lower()
    for token in re.split(r"[^a-z0-9]+", (title_hints or "").lower()):
        if len(token) >= 2 and token in blob:
            score += 10
            break
    return max(0.0, min(100.0, score))


def _fit_status(score: float, contact: dict) -> str:
    if contact.get("ai_verdict") == "junk" or contact.get("email_verification_status") == "invalid":
        return "weak"
    if score >= 78:
        return "strong"
    if score >= 55:
        return "medium"
    return "weak"


def _is_verified(contact: dict) -> int:
    ev = contact.get("email_verification_status") or ""
    ai = contact.get("ai_verdict") or ""
    if ai == "junk" or ev == "invalid":
        return 0
    # "suspicious" is the reviewer declining to vouch for the person. It used
    # to count as verified, which is how "West London" became a strong
    # prospect. Such a row is still saved, as unverified, rather than dropped:
    # the smaller change, and the member can still see and judge it.
    if ev in ("valid", "likely_valid") and ai in ("real", ""):
        return 1
    return 0


#: How long a cold roster refresh may hold up a search (see _roster).
ROSTER_STAGE_TIMEOUT_SEC = float(os.getenv("ROSTER_STAGE_TIMEOUT_SEC", "12") or 12)
#: Roster refreshes that outlived their search. Held so the event loop does
#: not drop them half way; each removes itself when done.
_BACKGROUND: set[asyncio.Future] = set()


def _background_done(task: asyncio.Future) -> None:
    _BACKGROUND.discard(task)
    if not task.cancelled() and task.exception() is not None:
        logger.warning("background roster refresh failed: %s", task.exception())


_SKIP_LABELS = {
    "other_entity": "other {brand} entities",
    "other_country": "outside the chosen country",
    "other_domain": "at other mail domains",
    "not_a_person": "not people",
}


def _skipped_clause(skipped: dict[str, dict[str, Any]], entity: dict[str, Any]) -> str:
    """" · 30 skipped (26 other Barclays entities, 4 outside UK)", or "" when
    nothing was skipped."""
    from app.services.web_contact_discovery import _LEGAL_TAIL

    total = sum(int(v.get("count") or 0) for v in skipped.values())
    if not total:
        return ""
    words = (entity.get("display_name") or entity.get("legal_name") or "").split()
    while words and words[-1].lower().strip(".,") in _LEGAL_TAIL:
        words.pop()
    def _say(code: str) -> str:
        return "UK" if code == "GB" else code

    # With a country chosen, "outside UK" is what was enforced. Without one,
    # only the excluded entities' countries were skipped, so name those
    # ("in IN") - "outside UK" would claim a limit the search did not apply.
    target = str(entity.get("target_country") or "").upper()
    if target and target != "*":
        where = f"outside {_say(target)}"
    else:
        hq = str(entity.get("hq_country") or "").upper()
        places = sorted({str(x.get("country") or "").upper() for x in entity.get("exclude") or []
                         if isinstance(x, dict) and x.get("country")} - {hq, ""})
        where = f"in {', '.join(_say(c) for c in places)}" if places else "elsewhere"
    labels = dict(_SKIP_LABELS, other_country=where)
    parts = [
        f"{skipped[r]['count']} " + labels[r].format(brand=" ".join(words) or "company")
        for r in _SKIP_LABELS if r in skipped
    ]
    return f" · {total} skipped ({', '.join(parts)})"


def _search_unavailable(errors: dict[str, Any] | None) -> str:
    """The line a run shows when no web search answered at all."""
    if not errors or not errors.get("all_failed"):
        return ""
    return "web search unavailable (limit reached)" if errors.get("limit_reached") \
        else "web search unavailable (provider error)"


async def _run_update(
    run_id: int,
    *,
    status: str | None = None,
    progress_pct: float | None = None,
    progress_message: str | None = None,
    prospects_count: int | None = None,
    research_json: str | None = None,
    error_message: str | None = None,
    completed: bool = False,
) -> None:
    db = await get_db()
    try:
        sets = ["updated_at = CURRENT_TIMESTAMP"]
        args: list[Any] = []
        if status is not None:
            sets.append("status = ?")
            args.append(status)
        if progress_pct is not None:
            sets.append("progress_pct = ?")
            args.append(progress_pct)
        if progress_message is not None:
            sets.append("progress_message = ?")
            args.append(progress_message[:2000])
        if prospects_count is not None:
            sets.append("prospects_count = ?")
            args.append(prospects_count)
        if research_json is not None:
            # Merged into what the run already recorded, not written over it.
            # The closing update used to replace the per-source counts (website,
            # roster, web) with the totals, so a run that came back empty could
            # no longer say which source had failed.
            cur = await db.execute(
                "SELECT research_json FROM yucgoutreach_discovery_runs WHERE id = ?", (run_id,)
            )
            prior_row = await cur.fetchone()
            try:
                prior = json.loads((prior_row["research_json"] if prior_row else None) or "{}")
                incoming = json.loads(research_json)
            except (TypeError, ValueError):
                prior, incoming = None, None
            if isinstance(prior, dict) and isinstance(incoming, dict):
                research_json = json.dumps({**prior, **incoming})
            sets.append("research_json = ?")
            args.append(research_json)
        if error_message is not None:
            sets.append("error_message = ?")
            args.append(error_message[:2000])
        if completed:
            sets.extend(["completed_at = CURRENT_TIMESTAMP", "lease_token = NULL", "lease_expires_at = NULL"])
        elif status == "running" or progress_pct is not None:
            lease_minutes = max(5, min(int(os.getenv("DISCOVERY_LEASE_MINUTES", "30") or 30), 180))
            sets.append("lease_expires_at = datetime('now', ?)")
            args.append(f"+{lease_minutes} minutes")
        lease_token = _active_lease.get()
        args.append(run_id)
        where = "id = ?" if lease_token is None else "id = ? AND lease_token = ? AND status = 'running'"
        if lease_token is not None:
            args.append(lease_token)
        cursor = await db.execute(
            f"UPDATE yucgoutreach_discovery_runs SET {', '.join(sets)} WHERE {where}",
            tuple(args),
        )
        if lease_token is not None and cursor.rowcount != 1:
            await db.rollback()
            raise DiscoveryLeaseLost("Discovery lease was replaced")
        await db.commit()
    finally:
        await db.close()


async def execute_yucgoutreach_run(run_id: int) -> None:
    db = await get_db()
    try:
        cur = await db.execute(
            """SELECT r.*, u.is_active AS member_is_active
               FROM yucgoutreach_discovery_runs r
               JOIN users u ON u.id=r.user_id
               WHERE r.id=? AND r.lease_token=? AND r.status='running'""",
            (run_id, _active_lease.get()),
        )
        row = await cur.fetchone()
        if not row:
            return
        spec = row_to_dict(row)
        if spec.get("status") != "running":
            return
        if not spec.get("member_is_active"):
            await _run_update(
                run_id,
                status="failed",
                progress_pct=100.0,
                error_message="Member account is inactive; create a new search after reactivation",
                completed=True,
            )
            return
    finally:
        await db.close()

    company = (spec.get("company_name") or "").strip()
    domain_in = (spec.get("company_domain") or "").strip()
    title_hints = _title_hints_from_spec(spec)
    entity = _entity_from_spec(spec, company)
    profiled = entity.get("source") != "fallback"
    max_prospects = max(1, min(int(spec.get("max_prospects") or 100), YUCG_MAX_PROSPECTS))
    # The entity's mail domain is the only host an address may be built on.
    # The run used to build them on whatever domain it had, which for Barclays
    # included barclays.bank.in, the Indian bank's.
    mail_domain = normalize_domain(entity.get("mail_domain") or "")
    domain = mail_domain or (normalize_domain(domain_in) if domain_in else "")
    skips: dict[str, dict[str, str]] = {}

    await _run_update(
        run_id,
        status="running",
        progress_pct=3.0,
        progress_message="Website, web search, and club roster first; then inbox checks…",
    )

    # A run with an entity profile was resolved before it was queued, and the
    # resolver already asked discover_company_domain; asking again cost a
    # second full lookup whenever it had found nothing (HSBC, Wells Fargo).
    if not domain and not profiled:
        # This used to take the host of the first search hit, unchecked, so
        # "Meta Platforms, Inc." became globaldata.com (a data vendor's profile
        # page) and the run saved jeff.kim@globaldata.com as a Meta prospect.
        # The resolver only answers with a domain that looks like the company,
        # is not a platform or data vendor, and accepts mail. When nothing
        # verifies the run goes on without a domain: web search, the roster and
        # the seeds still find people, they just get no invented mailbox.
        from app.services.company_email_cache import discover_company_domain

        try:
            domain = await discover_company_domain(company)
        except Exception:
            logger.exception("company domain lookup failed; running without a domain")
            domain = ""

    custom_patterns = await _load_custom_patterns()
    web_max = min(max_prospects * 2, int(os.getenv("SCRAPE_WEB_MAX_PEOPLE", "80")))

    async def _domain() -> list[dict]:
        if not domain:
            return []

        async def on_page(_i: int, _n: int, url: str) -> None:
            if url == "queued":
                await _run_update(
                    run_id,
                    progress_message="Waiting for website crawl slot (web search keeps going)",
                )

        return await scrape_contacts_from_domain(
            domain=domain, company_name=company, on_page=on_page
        )

    async def _web() -> list[dict]:
        from app.services.web_fetch import web_search_configured
        cn = company or (domain.split(".")[0].title() if domain else "")
        if not cn or not (web_search_configured() or (os.getenv("TAVILY_API_KEY") or "").strip()):
            return []
        return await discover_contacts_from_web(
            cn, domain or None, max_people=web_max, title_hints=title_hints, entity=entity, skips=skips,
        )

    async def _roster() -> tuple[list[dict], str]:
        """The club's memory of this company: SEC and Companies House officers.
        It used to run after the crawl and the web search had both finished,
        which is a cold SEC fetch added to the end of every search rather than
        alongside it."""
        try:
            from app.services.roster_email import (
                cached_roster_contacts, refresh_roster_on_demand, roster_refresh_note,
            )

            # Only a domain the member typed, or the entity's mail domain, may
            # replace the one the roster already holds; a looked-up domain
            # just fills a gap.
            pin = bool(domain_in) or bool(mail_domain)
            rows = await cached_roster_contacts(company, domain, pin_domain=pin)
            if not rows:
                # A cold SEC refresh took 36s for Barclays, longer than every
                # other source. It gets ROSTER_STAGE_TIMEOUT_SEC; past that it
                # finishes in the background and the next search reads it
                # from the cache, and this run goes on without it.
                refresh = asyncio.ensure_future(refresh_roster_on_demand(company, domain, pin_domain=pin))
                try:
                    rows = await asyncio.wait_for(asyncio.shield(refresh), ROSTER_STAGE_TIMEOUT_SEC)
                except asyncio.TimeoutError:
                    _BACKGROUND.add(refresh)
                    refresh.add_done_callback(_background_done)
                    rows = []
            return rows, await roster_refresh_note(company, domain)
        except Exception:
            logger.exception("roster cache-first lookup failed")
            return [], ""

    async def _seeds() -> list[dict]:
        """A second reading of the search results, which used to start only
        after the merge decided the other sources had come back thin. The point
        of the step is more people, so it runs with the rest and what it finds
        is used if it is new."""
        return await _tavily_name_seeds(
            company, domain, max_prospects, custom_patterns, title_hints, entity=entity, skips=skips,
        )

    from app.services.web_contact_discovery import (
        begin_search_run, end_search_run, person_name_key, search_errors_summary, skipped_summary,
    )

    search_run, search_token = begin_search_run()
    try:
        domain_contacts, web_contacts, meta, (roster_contacts, roster_note), seeded = await asyncio.gather(
            _domain(),
            _web(),
            _company_meta(company, domain),
            _roster(),
            _seeds(),
        )
    finally:
        end_search_run(search_token)
    search_errors = search_errors_summary(search_run)
    # The crawl and the roster are read through the same gate the web stage
    # applies to each search result, before anything is merged.
    domain_contacts = _gate_rows(domain_contacts, entity, skips, company)
    roster_contacts = _gate_rows(roster_contacts, entity, skips, company)
    kw1 = meta.get("keywords") or ""
    kw2 = meta.get("keywords_2") or ""
    if roster_contacts:
        domain_contacts = domain_contacts + roster_contacts
    n_site = max(0, len(domain_contacts) - len(roster_contacts))
    n_web = len(web_contacts)

    await _run_update(
        run_id,
        progress_pct=22.0,
        progress_message=(
            f"Sources: website {n_site} · roster {len(roster_contacts)} · web {n_web} — merging…"
        ),
        research_json=json.dumps(
            {
                "title_hints": title_hints or None,
                "domain_contacts": n_site,
                "roster_contacts": len(roster_contacts),
                "web_contacts": n_web,
                "roster_note": roster_note or None,
                "search_errors": search_errors,
            }
        ),
    )

    merged = merge_contacts(
        domain_contacts,
        company,
        domain,
        custom_patterns,
        web_contacts=web_contacts,
    )

    # Seeds were fetched alongside the other sources; anybody they found who is
    # not already here is a person the crawl and the roster missed.
    if seeded:
        seen = {sanitize_email(c.get("email") or "").lower() for c in merged if c.get("email")}
        for row in seeded:
            em = sanitize_email(row.get("email") or "").lower()
            if em and em not in seen and is_valid_person_contact(row, company_name=company, domain=domain):
                seen.add(em)
                merged.append(row)

    to_verify: list[dict] = []
    junk_log: list[dict] = []
    for c in merged:
        junk, reason = is_heuristic_junk_contact(c, company)
        if junk:
            row = dict(c)
            row["ai_verdict"] = "junk"
            row["ai_reason"] = reason
            junk_log.append(row)
        else:
            to_verify.append(c)

    if not to_verify and not junk_log:
        skipped = skipped_summary(skips)
        await _run_update(
            run_id,
            status="completed",
            progress_pct=100.0,
            progress_message=(
                f"No contacts saved. Website {n_site} · roster {len(roster_contacts)} · web {n_web}"
                + (f" ({roster_note})" if roster_note else "")
                + _skipped_clause(skipped, entity)
                + (f". {_search_unavailable(search_errors)}." if _search_unavailable(search_errors)
                   else ". Large-company sites rarely publish person emails; officers appear when the SEC fetch succeeds.")
            ),
            prospects_count=0,
            research_json=json.dumps({"skipped": skipped, "search_errors": search_errors}),
            completed=True,
        )
        return

    await _run_update(
        run_id,
        progress_pct=32.0,
        progress_message=f"Reviewing {len(to_verify)} contact address(es) — mail-domain and source evidence…",
    )

    async def _pipe_progress(phase: str, done: int, total: int, detail: str = "") -> None:
        base = {"identity": 32.0, "verify": 38.0, "ai": 52.0, "done": 82.0}.get(phase, 35.0)
        span = {"identity": 4.0, "verify": 12.0, "ai": 28.0, "done": 4.0}.get(phase, 10.0)
        pct = base + (done / max(total, 1)) * span
        label = {
            "identity": "Identity gate",
            "verify": "Inbox agents",
            "ai": "AI agents",
            "done": "Verification done",
        }.get(phase, phase)
        await _run_update(
            run_id,
            progress_pct=min(90.0, pct),
            progress_message=f"{label} {done}/{total}" + (f" — {detail}" if detail else ""),
        )

    verified, discovery_log = await run_contact_verify_pipeline(
        to_verify,
        company_name=company,
        domain=domain,
        on_progress=_pipe_progress if to_verify else None,
    )
    for c in junk_log:
        discovery_log.append(
            _log_row(c, verdict="junk", reason=c.get("ai_reason") or "", model=None, source_note="heuristic")
        )

    # The last gate before storage: whatever a source let through, the row
    # about to be saved is a person at the target entity, in the target
    # country, with an address on its mail domain.
    candidates = _gate_rows(
        [c for c in verified if c.get("ai_verdict") != "junk"], entity, skips, company,
    )
    candidates.sort(key=lambda row: _quality_score(row, title_hints), reverse=True)
    candidates = candidates[:max_prospects]

    inserted = 0
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        owns_run = await (await db.execute(
            """SELECT 1 FROM yucgoutreach_discovery_runs
               WHERE id=? AND lease_token=? AND status='running'""",
            (run_id, _active_lease.get()),
        )).fetchone()
        if not owns_run:
            await db.rollback()
            raise DiscoveryLeaseLost("Discovery lease was replaced before results were saved")
        for idx, c in enumerate(candidates):
            name = (c.get("name") or "").strip()
            first, last = _split_first_last(name)
            email = sanitize_email(c.get("email") or "")
            if not email or not is_employee_outreach_email(email):
                continue
            score = _quality_score(c, title_hints)
            secondary = score - 3 if c.get("contact_source") == "linkedin_inferred" else score
            linkedin = c.get("linkedin_url") or ""
            evidence_obj = {
                "contact_source": c.get("contact_source"),
                "email_verification_status": c.get("email_verification_status"),
                "ai_verdict": c.get("ai_verdict"),
                "ai_reason": c.get("ai_reason"),
                "email_pattern": c.get("email_pattern"),
                "discovery_context": (c.get("discovery_context") or "")[:500],
            }
            await db.execute(
                """INSERT INTO yucgoutreach_prospects (
                    run_id, first_name, last_name, email, company, contact_url, title,
                    account_url, photo_url, account_link, phone, phone_code, verified,
                    qualification_notes, contact_profile_url, linkedin_url, fit_status,
                    score, country, employees, industry, keywords_1, keywords_2,
                    yucgoutreach_score, evidence_json,
                    email_verification_status, ai_verdict, ai_reason, contact_source
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id,
                    first,
                    last,
                    email,
                    company,
                    linkedin or c.get("source_url"),
                    _clean_title(c.get("title"), name, company),
                    None,
                    None,
                    None,
                    None,
                    None,
                    _is_verified(c),
                    (c.get("ai_reason") or c.get("discovery_context") or "")[:2000],
                    linkedin or None,
                    linkedin or None,
                    _fit_status(score, c),
                    score,
                    meta.get("country") or None,
                    meta.get("employees") or None,
                    meta.get("industry") or None,
                    kw1 or None,
                    kw2 or None,
                    secondary,
                    json.dumps(evidence_obj)[:8000],
                    c.get("email_verification_status"),
                    c.get("ai_verdict"),
                    c.get("ai_reason"),
                    c.get("contact_source"),
                ),
            )
            inserted += 1
            if idx % 10 == 0:
                lease_minutes = max(5, min(int(os.getenv("DISCOVERY_LEASE_MINUTES", "30") or 30), 180))
                progress = 90.0 + (idx / max(len(candidates), 1)) * 8.0
                updated = await db.execute(
                    """UPDATE yucgoutreach_discovery_runs
                       SET progress_pct=?, progress_message=?, prospects_count=?,
                           updated_at=CURRENT_TIMESTAMP, lease_expires_at=datetime('now', ?)
                       WHERE id=? AND lease_token=? AND status='running'""",
                    (
                        progress,
                        f"Saving prospects {idx + 1}/{len(candidates)}…",
                        inserted,
                        f"+{lease_minutes} minutes",
                        run_id,
                        _active_lease.get(),
                    ),
                )
                if updated.rowcount != 1:
                    await db.rollback()
                    raise DiscoveryLeaseLost("Discovery lease was replaced while results were saved")
        await db.commit()
    finally:
        await db.close()

    junk_total = len(junk_log) + sum(1 for c in verified if c.get("ai_verdict") == "junk")
    skipped = skipped_summary(skips, {person_name_key(c.get("name") or "") for c in candidates})
    unavailable = _search_unavailable(search_errors)
    await _run_update(
        run_id,
        status="completed",
        progress_pct=100.0,
        progress_message=(
            f"Done — {inserted} saved"
            + (f" ({junk_total} junk filtered)" if junk_total else "")
            + _skipped_clause(skipped, entity)
            + (f" · {unavailable}" if unavailable else "")
        ),
        prospects_count=inserted,
        research_json=json.dumps(
            {
                "merged": len(merged),
                "verified": len(verified),
                "saved": inserted,
                "junk_filtered": junk_total,
                "discovery_log_count": len(discovery_log),
                "skipped": skipped,
                "search_errors": search_errors,
            }
        ),
        completed=True,
    )
    try:
        from app.services.roster_watch import remember_discovery_people

        await remember_discovery_people(company, domain, candidates)
    except Exception:
        logger.exception("roster remember after Find people failed")


async def _yucgoutreach_run_guard(run_id: int, lease_token: str) -> None:
    context = _active_lease.set(lease_token)
    try:
        await execute_yucgoutreach_run(run_id)
    except DiscoveryLeaseLost:
        return
    except Exception as e:
        await _run_update(
            run_id,
            status="failed",
            progress_pct=100.0,
            error_message=str(e),
            completed=True,
        )
    finally:
        _active_lease.reset(context)


async def recover_interrupted_yucgoutreach_runs() -> int:
    """Requeue expired leases once; active workers keep their jobs."""
    db = await get_db()
    try:
        max_attempts = max(1, min(int(os.getenv("DISCOVERY_MAX_ATTEMPTS", "2") or 2), 5))
        await db.execute(
            """UPDATE yucgoutreach_discovery_runs
               SET status='failed', progress_pct=100,
                   progress_message='Search stopped after repeated worker interruption',
                   error_message='Automatic retry limit reached; create a new search to retry',
                   completed_at=CURRENT_TIMESTAMP, lease_token=NULL, lease_expires_at=NULL,
                   updated_at=CURRENT_TIMESTAMP
               WHERE status='running' AND (lease_expires_at IS NULL OR datetime(lease_expires_at)<=datetime('now'))
                 AND attempt_count>=?""",
            (max_attempts,),
        )
        cursor = await db.execute(
            """UPDATE yucgoutreach_discovery_runs
               SET status='queued', progress_message='Recovered after application restart',
                   updated_at=CURRENT_TIMESTAMP, error_message=NULL, lease_token=NULL, lease_expires_at=NULL
               WHERE status='running' AND (lease_expires_at IS NULL OR datetime(lease_expires_at)<=datetime('now'))
                 AND attempt_count<? RETURNING id""",
            (max_attempts,),
        )
        recovered = await cursor.fetchall()
        await db.commit()
        return len(recovered)
    finally:
        await db.close()


async def drain_queued_yucgoutreach_runs() -> dict:
    """Claim one durable search. Conditional update prevents two schedulers taking it."""
    db = await get_db()
    run_id = None
    try:
        await recover_interrupted_yucgoutreach_runs()
        await db.execute(
            """UPDATE yucgoutreach_discovery_runs
               SET status='failed', progress_pct=100, completed_at=CURRENT_TIMESTAMP,
                   progress_message='Search cancelled because the member account is inactive',
                   error_message='Member account is inactive', updated_at=CURRENT_TIMESTAMP
               WHERE status='queued' AND NOT EXISTS (
                   SELECT 1 FROM users u WHERE u.id=yucgoutreach_discovery_runs.user_id AND u.is_active=1
               )"""
        )
        await db.commit()
        row = await (await db.execute(
            """SELECT r.id FROM yucgoutreach_discovery_runs r
               JOIN users u ON u.id=r.user_id AND u.is_active=1
               WHERE r.status='queued' ORDER BY r.id LIMIT 1"""
        )).fetchone()
        if not row:
            return {"ok": True, "claimed": 0}
        run_id = int(row["id"])
        lease_minutes = max(5, min(int(os.getenv("DISCOVERY_LEASE_MINUTES", "30") or 30), 180))
        lease_token = uuid.uuid4().hex
        claimed = await (await db.execute(
            """UPDATE yucgoutreach_discovery_runs
               SET status='running', progress_message='Starting', updated_at=CURRENT_TIMESTAMP,
                   attempt_count=attempt_count+1, lease_token=?, lease_expires_at=datetime('now', ?)
               WHERE id=? AND status='queued' AND EXISTS (
                   SELECT 1 FROM users u WHERE u.id=yucgoutreach_discovery_runs.user_id AND u.is_active=1
               ) RETURNING id""",
            (lease_token, f"+{lease_minutes} minutes", run_id),
        )).fetchone()
        if not claimed:
            await db.rollback()
            return {"ok": True, "claimed": 0}
        # A recovered job may have committed partial output before the process died.
        await db.execute("DELETE FROM yucgoutreach_prospects WHERE run_id=?", (run_id,))
        await db.commit()
    finally:
        await db.close()
    await _yucgoutreach_run_guard(run_id, lease_token)
    return {"ok": True, "claimed": 1, "run_id": run_id}


async def build_yucgoutreach_excel_bytes(run_id: int) -> bytes:
    from io import BytesIO

    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    headers = [
        "First Name",
        "Last Name",
        "Email",
        "Company",
        "Title",
        "LinkedIn URL",
        "Source",
        "Inbox Status",
        "AI Verdict",
        "Verified",
        "Fit Status",
        "Score",
        "YUCGoutreach Score",
        "Country",
        "Employees",
        "Industry",
        "Qualification Notes",
    ]

    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT company_name FROM yucgoutreach_discovery_runs WHERE id = ?",
            (run_id,),
        )
        r0 = await cur.fetchone()
        if not r0:
            raise ValueError("Run not found")
        company_name = r0["company_name"]
        cur = await db.execute(
            """SELECT first_name, last_name, email, company, title, linkedin_url,
               contact_source, email_verification_status, ai_verdict, verified,
               fit_status, score, yucgoutreach_score, country, employees, industry,
               qualification_notes
               FROM yucgoutreach_prospects WHERE run_id = ? ORDER BY score DESC, id""",
            (run_id,),
        )
        prospect_rows = [row_to_dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "Prospects"
    hdr_fill = PatternFill("solid", fgColor="1F4E79")
    hdr_font = Font(color="FFFFFF", bold=True)
    for col, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=col, value=h)
        c.fill = hdr_fill
        c.font = hdr_font

    for ri, pr in enumerate(prospect_rows, 2):
        ws.cell(row=ri, column=1, value=pr.get("first_name"))
        ws.cell(row=ri, column=2, value=pr.get("last_name"))
        ws.cell(row=ri, column=3, value=pr.get("email"))
        ws.cell(row=ri, column=4, value=pr.get("company") or company_name)
        ws.cell(row=ri, column=5, value=pr.get("title"))
        ws.cell(row=ri, column=6, value=pr.get("linkedin_url"))
        ws.cell(row=ri, column=7, value=pr.get("contact_source"))
        ws.cell(row=ri, column=8, value=pr.get("email_verification_status"))
        ws.cell(row=ri, column=9, value=pr.get("ai_verdict"))
        ws.cell(row=ri, column=10, value="Yes" if pr.get("verified") else "No")
        ws.cell(row=ri, column=11, value=pr.get("fit_status"))
        ws.cell(row=ri, column=12, value=pr.get("score"))
        ws.cell(row=ri, column=13, value=pr.get("yucgoutreach_score"))
        ws.cell(row=ri, column=14, value=pr.get("country"))
        ws.cell(row=ri, column=15, value=pr.get("employees"))
        ws.cell(row=ri, column=16, value=pr.get("industry"))
        ws.cell(row=ri, column=17, value=pr.get("qualification_notes"))

    bio = BytesIO()
    wb.save(bio)
    return bio.getvalue()
