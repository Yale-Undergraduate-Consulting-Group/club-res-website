"""Company-specific role suggestions for Find people.

Title vocabulary differs by company - one firm's "Product Manager" is
another's "Product Lead" or "Member of Technical Staff" - so a member's
title hints only find people when they match how that company actually
labels roles. This module answers two questions for a typed company:

1. Which roles are *observed* there? Grounded, in priority order, in what
   the system has already seen: prospects from earlier Find people runs,
   SEC officers in the club roster, and the shared contacts catalog. When
   those are thin, one free LinkedIn-restricted web search fills in.
2. If the member typed hints, what are the *equivalents* at this company?
   The rank model maps each asked role to the closest observed titles and
   says when the company simply does not use that label. It only ever
   chooses among titles that were actually observed - it cannot invent one.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import Counter
from typing import Any

from app.database import get_db

logger = logging.getLogger(__name__)

MAX_CHIPS = 12
_SEARCH_FILL_THRESHOLD = 6
_TITLE_MAX = 80
# "Jane Doe - VP, Product at Acme | LinkedIn" / "Jane Doe – Head of X – Acme".
# Segment separators are dashes with whitespace on both sides; an intra-word
# hyphen ("Go-To-Market") is part of the title.
_SEP = r"\s+[-–—]\s+"
_LINKEDIN_TITLE_RE = re.compile(
    rf"^(?P<name>.+?){_SEP}(?P<title>.+?)(?:{_SEP}.*|\s+(?:at|@)\s+.*|\s*\|.*)?$",
    re.I,
)
# Site and legal-form noise only. The company's own name is stripped in
# normalize_title from the company argument, so no single company needs a
# hardcoded entry here.
_NOISE = re.compile(r"\b(linkedin|profile|professional|inc\.?|llc|ltd\.?)\b", re.I)


_JUNK_TITLES = frozenset({"youtube", "linkedin", "twitter", "x", "facebook", "instagram", "wikipedia", "news", "home"})
_ROLE_WORD = re.compile(
    r"\b(chief|officer|president|vice|vp|svp|evp|avp|head|director|manager|lead|leader|partner|principal|"
    r"founder|co-founder|owner|analyst|associate|engineer|scientist|researcher|staff|member|fellow|advisor|"
    r"adviser|consultant|counsel|attorney|controller|treasurer|secretary|chair|chairman|chairwoman|dean|"
    r"professor|specialist|coordinator|strategist|architect|designer|producer|editor|recruiter|executive|"
    r"intern|general|managing|senior|sr\.?|jr\.?|global|regional|national|team|operations|product|marketing|"
    r"sales|finance|legal|people|talent|growth|strategy|technology|technical|data|policy|communications|"
    r"partnerships|business|development|program|project|account|customer|success|supply|clinical|medical|"
    r"health|healthcare|cto|cfo|ceo|coo|cmo|cpo|cio|ciso|gm)\b",
    re.I,
)


def normalize_title(raw: str | None, company: str | None = None) -> str:
    """Reduce a stored or scraped string to a role title, or '' when it is
    not one. Stored prospect titles are often the raw search headline
    ('Jane Doe - VP Product at Acme | LinkedIn'), so headline shapes are
    parsed first; then company/site noise is stripped; then the result must
    look like a role (contain a role word, be short, not be truncated)."""
    text = (raw or "").strip()
    if not text:
        return ""
    if re.search(_SEP, text):
        match = _LINKEDIN_TITLE_RE.match(text)
        if match:
            text = match.group("title")
    text = re.split(r"\s+(?:at|@)\s+|\s*\|\s*", text, maxsplit=1)[0]
    if text.endswith(("...", "…")):
        # Search engines cut long headlines mid-word ("Head of Global
        # Partnerships and Gro…"). Everything before the cut is still the
        # person's own words, so keep it: drop the ellipsis, the word it
        # truncated, and any connector left dangling. Rejecting the whole
        # headline threw away a real title for every long one.
        text = re.sub(r"\s*\S*$", "", text.rstrip(".… "))
        text = re.sub(r"(?:\s+(?:of|and|&|for|the|in|at|to)|\s*[,/&-])+$", "", text, flags=re.I)
        if len(text.split()) < 2:
            return ""  # "Head" alone says nothing about the role
    if company and company.strip():
        # "VP Product, Acme Corp" -> "VP Product". Lookarounds, not \b, so
        # names ending in punctuation ("Acme Inc.") still match.
        text = re.sub(rf"(?<!\w){re.escape(company.strip())}(?!\w)", "", text, flags=re.I)
    text = _NOISE.sub("", text)
    text = re.sub(r"\s*[-–—,/]\s*$", "", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" -–—,")
    if len(text) < 3 or len(text) > _TITLE_MAX:
        return ""
    low = text.lower()
    if low in _JUNK_TITLES:
        return ""
    if company:
        c = company.strip().lower()
        if c and (low == c or low.replace(",", "") == c):
            return ""
    if not re.search(r"[a-z]", text, re.I) or not _ROLE_WORD.search(text):
        return ""
    # Sentences and article headlines are not titles.
    if len(text.split()) > 9 or re.search(r"\b(vs\.?|what|why|how|when)\b", text, re.I):
        return ""
    return text


def _key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def title_from_search_result(result_title: str | None, company: str | None = None) -> str:
    text = (result_title or "").strip()
    match = _LINKEDIN_TITLE_RE.match(text)
    if not match:
        return ""
    return normalize_title(match.group("title"), company)


# "Product Lead at OpenAI · Experience: OpenAI · Education: ..." - a profile
# snippet often states the current role even when the result title is cut
# short or is only the person's name. Segments are split on the separators
# snippets use, so a match never spans two facts.
_SNIPPET_ROLE_RE = re.compile(r"(?P<title>[^.·|•\n]{3,80}?)\s+(?:at|@)\s+(?P<company>[^.·|•\n,]{2,80})", re.I)
# First-person prose and past roles are not the title someone holds now.
_SNIPPET_NOT_TITLE = re.compile(r"^(?:i|we|my|our|he|she|they|currently|working|worked|former|formerly|ex|previously)\b", re.I)


def title_from_snippet(snippet: str | None, company: str | None) -> str:
    """The first 'Title at Company' statement in a result snippet, only when
    the company named is the one searched - a snippet also lists past
    employers and education, and those are not roles at this company."""
    want = _key(company or "")
    if not want:
        return ""
    for match in _SNIPPET_ROLE_RE.finditer(snippet or ""):
        if not _key(match.group("company")).startswith(want):
            continue
        # Drop a "Experience:"-style label that shares the segment.
        raw = re.sub(r"^[^:]{1,20}:\s*", "", match.group("title").strip())
        if _SNIPPET_NOT_TITLE.search(raw):
            continue
        title = normalize_title(raw, company)
        if title:
            return title
    return ""


async def observed_titles(company: str, domain: str | None) -> list[dict[str, Any]]:
    """Titles the system has already seen at this company, with counts and
    the strongest source each came from."""
    company_like = f"%{company.strip().lower()}%"
    dom = (domain or "").strip().lower()
    counts: Counter[str] = Counter()
    display: dict[str, str] = {}
    source: dict[str, str] = {}
    rank = {"roster": 0, "run": 1, "catalog": 2}

    def add(title_raw: str | None, src: str) -> None:
        title = normalize_title(title_raw, company)
        if not title:
            return
        key = _key(title)
        counts[key] += 1
        display.setdefault(key, title)
        if key not in source or rank[src] < rank[source[key]]:
            source[key] = src

    db = await get_db()
    try:
        rows = await (await db.execute(
            """SELECT p.title FROM yucgoutreach_prospects p
               JOIN yucgoutreach_discovery_runs r ON r.id = p.run_id
               WHERE p.title IS NOT NULL AND p.title != ''
                 AND (lower(r.company_name) LIKE ? OR (? != '' AND lower(COALESCE(r.company_domain,'')) = ?))
               ORDER BY p.id DESC LIMIT 400""",
            (company_like, dom, dom),
        )).fetchall()
        for row in rows:
            add(row["title"], "run")
        rows = await (await db.execute(
            """SELECT pe.title FROM company_roster_people pe
               JOIN company_rosters ro ON ro.id = pe.roster_id
               WHERE pe.title != '' AND pe.employment = 'current'
                 AND (lower(ro.company_name) LIKE ? OR (? != '' AND lower(COALESCE(ro.company_domain,'')) = ?))
               LIMIT 200""",
            (company_like, dom, dom),
        )).fetchall()
        for row in rows:
            add(row["title"], "roster")
        rows = await (await db.execute(
            """SELECT title FROM contacts
               WHERE title IS NOT NULL AND title != ''
                 AND (lower(COALESCE(company,'')) LIKE ? OR (? != '' AND lower(COALESCE(company_domain,'')) = ?))
               LIMIT 400""",
            (company_like, dom, dom),
        )).fetchall()
        for row in rows:
            add(row["title"], "catalog")
    finally:
        await db.close()

    return [
        {"title": display[key], "count": count, "source": source[key]}
        for key, count in counts.most_common()
    ]


_GENERIC_ROLE_QUERY = "leadership director manager lead"
_JOB_TITLE_RE = re.compile(r"^(?P<title>.+?)\s+at\s+(?P<company>.+?)(?:\s+[—–-]\s+.*)?(?:\s*[-|]\s*(?:Jobs\s*[-|]\s*)?LinkedIn)?\s*$", re.I)


def title_from_job_posting(result_title: str | None, company: str | None = None) -> str:
    """'Product Manager, Business Technology at Anthropic - LinkedIn' ->
    'Product Manager, Business Technology'. Postings are the company's own
    words for a role, so they are strong vocabulary evidence. A posting
    headline without an 'at Company' segment is the title itself - the URL
    already scoped it to this company."""
    text = re.sub(r"\s*[-|]\s*(?:Jobs\s*[-|]\s*)?LinkedIn\s*$", "", (result_title or "").strip(), flags=re.I)
    # "Anthropic hiring Product Management, Research in San Francisco, CA"
    hiring = re.match(r"^(?P<company>.+?)\s+hiring\s+(?P<title>.+?)(?:\s+in\s+[A-Z][^,]*(?:,\s*[A-Z]{2})?)?\s*$", text, re.I)
    if hiring:
        return normalize_title(hiring.group("title"), company)
    match = _JOB_TITLE_RE.match(text)
    if match:
        return normalize_title(match.group("title"), company)
    if re.search(_SEP, text) or ":" in text:
        return ""  # "Anthropic: Jobs", "Name - Title" shapes are not postings
    return normalize_title(text, company)


# --- Web search budget and cache -------------------------------------------
#
# Every web search is charged against the member's hourly allowance
# (reserve_tinyfish_call: 30/member/hour by default) once PER RESULTS PAGE,
# and TinyFish returns about one page of ~8 results per call. Asking for 25
# results used to cost up to 4 slots per query, so one load of this panel
# could spend 8 slots, and 20 with the seniority fallback - two or three
# reloads and the member hit "Web search limit reached". So: every query
# asks for exactly one page, a load spends at most _PAGE_BUDGET pages, and
# what a query found is remembered for a day so a reload costs nothing.
_PAGE_BUDGET = 8
_HINT_QUERY_MAX = 3  # hint terms searched per load; the rest rely on the mapping step
_CACHE_TTL_S = 24 * 3600
# An empty answer is remembered for less time: it can be a transient
# upstream failure (Firecrawl returns [] on error), not a real "nobody".
_EMPTY_CACHE_TTL_S = 3600
# (company key, query term key) -> (expires at, [(url, title, source)]).
# Keyed on the company name and the term only: the domain never enters the
# query text, so it cannot change what the search returns - adding it to
# the key would only turn identical searches into cache misses. Results are
# public web data, so members share entries; each fetch is charged to the
# member who made it.
# ponytail: per-process memory, lost on restart and not shared between
# workers; move to a table if the app runs several workers.
_search_cache: dict[tuple[str, str], tuple[float, list[tuple[str, str, str]]]] = {}


def _page_size() -> int:
    # Read from web_fetch so this stays one page if TinyFish's page changes.
    from app.services.web_fetch import _TINYFISH_SEARCH_PAGE_SIZE
    return _TINYFISH_SEARCH_PAGE_SIZE


def _title_from_item(item: dict[str, Any], company: str) -> tuple[str, str]:
    """(title, source) for one search result, ('', '') when it names no role
    held at this company."""
    url = str(item.get("url") or "")
    if "/jobs/view/" in url:
        return title_from_job_posting(item.get("title"), company), "jobs"
    if "/in/" in url:
        # The headline first; the snippet only when the headline gave
        # nothing (cut short, or just a name), so one profile counts once.
        title = title_from_search_result(item.get("title"), company) or title_from_snippet(item.get("content"), company)
        return title, "search"
    return "", ""  # company pages, posts, news: no role held by a person


async def _linkedin_titles(company: str, term: str, *, user_id: int | None) -> list[tuple[str, str, str]]:
    """One LinkedIn-restricted query, one results page, remembered for a day.
    Raises what web_search raises (quota, transport); the caller decides."""
    from app.services.web_fetch import web_search

    key = (_key(company), _key(term))
    hit = _search_cache.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    results = await web_search(f"{company} {term} site:linkedin.com/in", max_results=_page_size(), user_id=user_id)
    rows: list[tuple[str, str, str]] = []
    for item in results or []:
        title, src = _title_from_item(item, company)
        if title:
            rows.append((str(item.get("url") or ""), title, src))
    _search_cache[key] = (time.monotonic() + (_CACHE_TTL_S if rows else _EMPTY_CACHE_TTL_S), rows)
    return rows


def _tally(rows: list[tuple[str, str, str]], seen_urls: set[str], counts: Counter[str],
           display: dict[str, str], source: dict[str, str]) -> None:
    """Count each person once per load: the hint and generic queries often
    return the same profile, and counting it twice would rank a title by
    how many queries found it rather than how many people hold it."""
    for url, title, src in rows:
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)
        key = _key(title)
        counts[key] += 1
        display.setdefault(key, title)
        if src == "jobs" or key not in source:
            source[key] = src


def _quota_note(exc: Exception) -> str:
    detail = getattr(exc, "detail", None)
    return (str(detail) if detail else "Web search is unavailable right now") + "; showing only roles already on record."


async def search_titles(company: str, hints: str | None, *, user_id: int | None = None) -> tuple[list[dict[str, Any]], str | None]:
    """LinkedIn-restricted search; titles parsed from profile headlines and
    snippets (/in/) and job postings (/jobs/view/). Plain words only -
    quoting the company or chaining OR terms made the engine return
    unrelated pages in live testing, and for the same reason each typed hint
    term is its own query: the whole hint string as one query ("healthcare
    PMs, VPs") matched nobody. Every planned query runs and the results are
    merged - stopping at the first query that found anyone left one or two
    titles on screen. The generic query runs last so a hint the company does
    not use still leaves its real vocabulary to map onto."""
    from app.services.web_fetch import web_search_configured

    if not web_search_configured():
        return [], "Web search is not configured; showing only roles already on record."
    c = company.strip()
    terms = _parse_hints(hints)[:_HINT_QUERY_MAX] + [_GENERIC_ROLE_QUERY]
    note: str | None = None
    counts: Counter[str] = Counter()
    display: dict[str, str] = {}
    source: dict[str, str] = {}
    seen_urls: set[str] = set()
    for term in terms:
        try:
            rows = await _linkedin_titles(c, term, user_id=user_id)
        except Exception as exc:  # quota or transport; suggestions are best-effort
            # Keep what the earlier queries found; the next query would hit
            # the same limit, so stop asking.
            logger.info("role suggestion search skipped for %r/%r: %s", company, term, exc)
            note = _quota_note(exc)
            break
        _tally(rows, seen_urls, counts, display, source)
    return [{"title": display[k], "count": n, "source": source[k]} for k, n in counts.most_common()], note


# The people who answer a cold email are one or two levels below the board:
# they own a budget and a problem. Each is asked for separately because these
# search engines match a headline literally - a single query stringing the
# terms together scored zero against A24 and Garmin in live testing, while the
# looser query returned results dominated by directors in the board sense.
_SENIORITY_BANDS = ("VP", "Vice President", "Head of", "Director of", "Manager", "Chief of Staff")


async def titles_by_seniority(company: str, *, user_id: int | None = None,
                              max_queries: int = len(_SENIORITY_BANDS), have: int = 0) -> list[dict[str, Any]]:
    """Ask LinkedIn per band when the broad queries found too few titles.

    Same system, sharper questions: the search is still restricted to
    /in/ profiles at this company, which is where the people who would reply
    actually describe themselves. A company website's team page is not a
    substitute - it lists a handful of executives and no one below them.
    `have` is how many distinct titles the caller already holds; asking
    stops once the total is useful, so a top-up spends only what it needs.
    """
    c = company.strip()
    counts: Counter[str] = Counter()
    display: dict[str, str] = {}
    source: dict[str, str] = {}
    seen_urls: set[str] = set()
    for band in _SENIORITY_BANDS[:max(0, max_queries)]:
        if have + len(counts) >= _SEARCH_FILL_THRESHOLD:
            break
        try:
            rows = await _linkedin_titles(c, band, user_id=user_id)
        except Exception as exc:  # quota or transport; the bands are best-effort
            logger.info("seniority band search skipped for %r/%r: %s", company, band, exc)
            break
        _tally([r for r in rows if r[2] == "search"], seen_urls, counts, display, source)
    return [{"title": display[k], "count": n, "source": "band"} for k, n in counts.most_common(25)]


def merge_titles(*groups: list[dict[str, Any]], limit: int | None = MAX_CHIPS) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    display: dict[str, str] = {}
    source: dict[str, str] = {}
    rank = {"roster": 0, "run": 1, "catalog": 2, "jobs": 3, "search": 4}
    for group in groups:
        for row in group:
            key = _key(row["title"])
            counts[key] += int(row.get("count") or 1)
            display.setdefault(key, row["title"])
            src = row.get("source") or "search"
            if key not in source or rank.get(src, 9) < rank.get(source[key], 9):
                source[key] = src
    return [{"title": display[k], "count": c, "source": source[k]} for k, c in counts.most_common(limit)]


def _parse_hints(hints: str | None) -> list[str]:
    parts = re.split(r"[,;/]|\band\b|\bor\b", hints or "", flags=re.I)
    seen: list[str] = []
    for part in parts:
        p = re.sub(r"\s+", " ", part).strip(" .")
        if len(p) >= 2 and p.lower() not in {s.lower() for s in seen}:
            seen.append(p[:60])
    return seen[:6]


async def map_equivalents(company: str, hints: str | None, observed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ask the rank model which observed titles correspond to each asked
    role at this company. Constrained to observed titles; anything else in
    the reply is dropped so the model cannot introduce a title nobody holds."""
    asked = _parse_hints(hints)
    if not asked or not observed:
        return []
    from app.services.llm import complete_json, rank_model_id

    allowed = {_key(o["title"]): o["title"] for o in observed[:40]}
    prompt = (
        f"Company: {company}\n"
        f"Titles actually observed at this company (choose ONLY from these, verbatim):\n"
        + "\n".join(f"- {t}" for t in allowed.values())
        + "\n\nA member wants to reach these kinds of people:\n"
        + "\n".join(f"- {a}" for a in asked)
        + "\n\nFor each asked role, list the observed titles that are the closest equivalent at this company "
          "(empty list if none fit), and one short note when the company labels the role differently "
          "or does not appear to have it. JSON only:\n"
          '{"equivalents":[{"asked":"","at_company":[""],"note":""}]}'
    )
    try:
        data = await asyncio.to_thread(complete_json, prompt, rank_model_id(),
                                       "You map job-title vocabulary between companies. Never invent titles.")
    except Exception as exc:
        logger.info("role equivalence mapping unavailable: %s", exc)
        return []
    rows = (data or {}).get("equivalents") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        asked_role = str(row.get("asked") or "").strip()
        if not asked_role:
            continue
        matches = [allowed[_key(str(t))] for t in (row.get("at_company") or []) if _key(str(t)) in allowed]
        out.append({
            "asked": asked_role[:60],
            "at_company": list(dict.fromkeys(matches))[:5],
            "note": str(row.get("note") or "").strip()[:200],
        })
    return out


async def suggest_roles(*, user_id: int, company: str, domain: str | None, hints: str | None) -> dict[str, Any]:
    company = (company or "").strip()
    if len(company) < 2:
        return {"company": company, "roles": [], "equivalents": [], "sources": {}, "note": None}
    seen = await observed_titles(company, domain)
    searched: list[dict[str, Any]] = []
    escalated: list[dict[str, Any]] = []
    note: str | None = None
    if len(seen) < _SEARCH_FILL_THRESHOLD or (hints or "").strip():
        searched, note = await search_titles(company, hints, user_id=user_id)
    # Too few titles to choose from: ask LinkedIn again, one seniority band
    # at a time, rather than handing back one or two chips or a generic
    # guess. Not after a quota stop - every band would hit the same limit.
    # The bands get whatever of the page budget the broad queries could
    # have used (hint terms + the generic query), so a cold load stays
    # within _PAGE_BUDGET pages even when nothing was cached.
    have = len(merge_titles(seen, searched, limit=None))
    if (have < _SEARCH_FILL_THRESHOLD and note is None) or (not seen and not searched):
        spent = min(len(_parse_hints(hints)), _HINT_QUERY_MAX) + 1
        escalated = await titles_by_seniority(company, user_id=user_id, max_queries=_PAGE_BUDGET - spent, have=have)
        if escalated and not seen and not searched:
            note = note or "Found by asking LinkedIn for each seniority band separately."
        elif not escalated and not seen and not searched and not note:
            note = ("LinkedIn search returns nothing for this company. Type the role you want "
                    "and Find people will search for it directly.")
    # The mapping sees every title found, not only the chips that fit on
    # screen: the closest equivalent to a typed role can be a rarer title.
    pool = merge_titles(seen, searched, escalated, limit=None)
    roles = pool[:MAX_CHIPS]
    equivalents = await map_equivalents(company, hints, pool) if (hints or "").strip() else []
    return {
        "company": company,
        "roles": roles,
        "equivalents": equivalents,
        "note": note,
        "sources": {
            "run": sum(1 for r in seen if r["source"] == "run"),
            "roster": sum(1 for r in seen if r["source"] == "roster"),
            "catalog": sum(1 for r in seen if r["source"] == "catalog"),
            "search": sum(1 for r in searched if r["source"] == "search"),
            "jobs": sum(1 for r in searched if r["source"] == "jobs"),
            "by_band": len(escalated),
        },
    }
