"""
Web-wide employee discovery for the main scraper (Tavily + LinkedIn snippets).
Complements domain crawl — searches the open web, not just company subpages.
"""
from __future__ import annotations

import asyncio
import os
import re
from contextvars import ContextVar, Token
from typing import Any, Awaitable, Callable, Optional

import httpx

from app.services.company_email_cache import build_email_for_person_sync, text_names_brand
from app.services.contact_scraper import (
    extract_employee_emails_from_text,
    is_valid_person_contact,
    looks_like_person_name,
    normalize_domain,
    person_name_key,
    sanitize_email,
    confidence_for_contact_dict,
)
from app.services.linkedin_scraper import extract_linkedin_profile_slug

ProgressHook = Optional[Callable[[str, float], Awaitable[None]]]

LINKEDIN_IN_URL = re.compile(r"https?://(?:[\w.]+)?linkedin\.com/in/([a-zA-Z0-9_-]+)/?", re.I)
# The title stops at the end of its sentence. Running on past it made one
# match swallow the next person: "Copilot Extensibility - Patrick Rodgers. John
# Nguyen - Principal Engineering Manager" yielded the product and hid the two
# real people behind it.
NAME_TITLE_RE = re.compile(
    r"\b([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+)\s+(?:[-–—|]\s*|[,]\s*)"
    r"([A-Za-z][^|\n]{3,60}?)(?=\.\s|\.$|[|\n]|$)",
)
NAME_AT_COMPANY_RE = re.compile(
    r"\b([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+)\s+(?:at|@)\s+",
    re.I,
)
TAVILY_SEARCH_DEPTH = os.getenv("TAVILY_SEARCH_DEPTH", "basic").strip() or "basic"
# The wave is network-bound: each query is one HTTP round trip, so the
# worker count is what turns a list of queries into one wait.
WEB_DISCOVERY_WORKERS = int(os.getenv("WEB_DISCOVERY_WORKERS", "12"))
# One results page per query. TinyFish returns 8 results a page and fetches
# pages one after another, so asking for 25 made every query four sequential
# calls. Breadth comes from asking more different questions at once instead.
SEARCH_PAGE_RESULTS = int(os.getenv("WEB_DISCOVERY_PAGE_RESULTS", "8"))

#: The search bookkeeping of one discovery run: how many queries were asked,
#: how many failed and why, and the queries already in flight. A failed search
#: used to come back as an empty list, so a run whose every query hit the
#: provider's limit said "Large-company sites rarely publish person emails".
#: Two stages of one run asking the same query now share one request.
_SEARCH_RUN: ContextVar[dict[str, Any] | None] = ContextVar("web_search_run", default=None)


def begin_search_run() -> tuple[dict[str, Any], Token]:
    run: dict[str, Any] = {"queries": 0, "failed": 0, "first_error": None, "limit": False, "inflight": {}}
    return run, _SEARCH_RUN.set(run)


def end_search_run(token: Token) -> None:
    _SEARCH_RUN.reset(token)


def search_errors_summary(run: dict[str, Any] | None) -> dict[str, Any] | None:
    """research_json["search_errors"], or None when every query answered."""
    if not run or not run.get("failed"):
        return None
    return {
        "count": run["failed"],
        "queries": run["queries"],
        "first_message": run.get("first_error"),
        "all_failed": run["failed"] >= run["queries"],
        "limit_reached": bool(run.get("limit")),
    }


def _record_search_error(message: str, *, limit: bool) -> None:
    run = _SEARCH_RUN.get()
    if run is None:
        return
    run["failed"] += 1
    run["limit"] = run["limit"] or limit
    if not run["first_error"]:
        run["first_error"] = message[:300]


async def _tavily_search(query: str, max_results: int = 8) -> list[dict[str, Any]]:
    run = _SEARCH_RUN.get()
    if run is None:
        return await _search_once(query, max_results)
    key = (query, max_results)
    task = run["inflight"].get(key)
    if task is None:
        run["queries"] += 1
        task = asyncio.ensure_future(_search_once(query, max_results))
        run["inflight"][key] = task
    return list(await task)


async def _search_once(query: str, max_results: int) -> list[dict[str, Any]]:
    from app.services.web_fetch import web_search_configured, web_search

    if web_search_configured():
        from app.services.web_fetch import last_search_failure

        try:
            results = await web_search(query, max_results=max_results)
            failure = last_search_failure()
            if not results and failure:
                _record_search_error(failure, limit="limit" in failure.lower() or "429" in failure)
            return results
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            _record_search_error(f"{status or type(exc).__name__}: {getattr(exc, 'detail', None) or exc}",
                                 limit=status in (429, 432))
            return []
    key = (os.getenv("TAVILY_API_KEY") or "").strip()
    if not key:
        return []
    try:
        async with httpx.AsyncClient(timeout=28.0) as client:
            r = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": key,
                    "query": query,
                    "search_depth": TAVILY_SEARCH_DEPTH,
                    "max_results": max_results,
                },
            )
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPStatusError as exc:
        # Tavily answers 432 when the plan's credits are spent and 429 when
        # rate limited; both mean every other query of the run fails too.
        code = exc.response.status_code
        _record_search_error(f"Tavily HTTP {code}: {exc.response.text[:200]}", limit=code in (429, 432))
        return []
    except Exception as exc:
        _record_search_error(f"Tavily {type(exc).__name__}: {exc}", limit=False)
        return []
    out: list[dict[str, Any]] = []
    for x in data.get("results") or []:
        out.append(
            {
                "title": x.get("title") or "",
                "url": x.get("url") or "",
                "content": (x.get("content") or "")[:1500],
            }
        )
    return out


async def _tavily_parallel(queries: list[str], max_results: int = 8) -> list[dict[str, Any]]:
    if not queries:
        return []
    sem = asyncio.Semaphore(max(1, WEB_DISCOVERY_WORKERS))

    async def _one(q: str) -> list[dict[str, Any]]:
        async with sem:
            return await _tavily_search(q, max_results=max_results)

    batches = await asyncio.gather(*[_one(q) for q in queries])
    seen_urls: set[str] = set()
    merged: list[dict[str, Any]] = []
    for batch in batches:
        for row in batch:
            url = row.get("url") or ""
            if url and url in seen_urls:
                continue
            if url:
                seen_urls.add(url)
            merged.append(row)
    return merged


_LINKEDIN_DEDUP_SUFFIX_RE = re.compile(r"(?=[0-9a-f]*\d)[0-9a-f]{5,10}$", re.I)


def _slug_to_name(slug: str) -> str | None:
    """LinkedIn appends a short auto-generated suffix to a profile slug when
    the readable one is already taken - either as its own hyphenated segment
    (e.g. don-gross-25b76b8) or fused onto the last word with no separator
    (e.g. rachel-hutter60). Both are hex-digit strings containing at least
    one digit, so real names (which never mix digits into a surname) are
    never mistaken for one."""
    slug = (slug or "").strip().strip("/")
    if not slug or len(slug) < 4:
        return None
    parts = [p for p in re.split(r"[-_]+", slug) if p]
    while parts and _LINKEDIN_DEDUP_SUFFIX_RE.fullmatch(parts[-1]):
        parts.pop()
    if parts:
        parts[-1] = re.sub(r"\d+$", "", parts[-1]) or parts[-1]
    parts = [p for p in parts if p and not p.isdigit()]
    if len(parts) < 2:
        return None
    name = " ".join(p.capitalize() for p in parts[:3])
    return name if looks_like_person_name(name) else None


# A found name is only a lead if the page says it belongs to this company.
# Searching "Chewy" returns pet-industry politics, and a Denver Post story that
# links a congresswoman's LinkedIn profile used to become
# brittany.pettersen@chewy.com: the slug produced a name, the company was
# stamped on because the search was about the company, and the address was
# invented from the house pattern. Three guesses, no evidence, one plausible
# person who has never worked there.

#: Titles that are public office or press framing, never a job at a company.
PUBLIC_OFFICE_TITLE = re.compile(
    r"\b(congress(?:wo)?m[ae]n|u\.?s\.? representative|state representative|"
    r"senator|sen\.|rep\.|governor|gov\.|mayor|council ?member|assembly ?member|"
    r"attorney general|secretary of state|ambassador|lawmaker|candidate|"
    r"judge|justice|sheriff|commissioner of)\b",
    re.I,
)
#: Words that make a phrase read as a job rather than a sentence about one.
COMPANY_ROLE_HINT = re.compile(
    r"\b(ceo|cfo|coo|cto|cio|cmo|chief|president|founder|owner|partner|"
    r"vp|svp|evp|vice president|head of|director|manager|lead|principal|officer|"
    r"engineer|analyst|scientist|designer|buyer|merchandis\w*|recruiter|"
    r"marketing|operations|supply chain|people|talent|product|sales)\b",
    re.I,
)
#: Words that a page uses about itself, never a person's name. Each of these
#: was a live contact in this club's own list: "Choose People" (from the title
#: of a Microsoft Support article), "Activity Image" (alt text beside a profile
#: link), "Transformation Leader" and "Copilot Extensibility" (a role blurb and
#: a product, each parsed as the person in "X - Y").
NON_PERSON_NAME_WORD = re.compile(
    r"\b(choose|find|learn|explore|discover|get|see|read|watch|download|manage|"
    r"create|view|browse|start|join|sign|contact|support|overview|solutions|"
    r"services|platform|careers|activity|image|video|photo|session|webinar|blog|"
    r"docs|documentation|copilot|extensibility|leader|leadership|team|people|"
    r"staff|management|department|division|office|headquarters|about|home|"
    r"privacy|cookies|terms|news|press|events|resources|pricing|products)\b",
    re.I,
)


def _clean_person_name(name: str) -> str:
    """Drop the auto-generated suffix LinkedIn adds to a taken profile slug.

    Names lifted from page text carry it too - "Steve Mathias B1a579" is a real
    person with his slug printed after his name - and only the slug parser used
    to strip it."""
    parts = [p for p in (name or "").split() if p]
    while parts and _LINKEDIN_DEDUP_SUFFIX_RE.fullmatch(parts[-1]):
        parts.pop()
    return " ".join(parts)


def _name_agrees_with_profile(name: str, linkedin_url: str) -> bool:
    """A profile link attached to a name has to be that person's.

    "Copilot Extensibility" carrying linkedin.com/in/msjonguy, and "Activity
    Image" carrying linkedin.com/in/judsonalthoff, are pages pairing whatever
    text sat nearest the link - not people."""
    slug = extract_linkedin_profile_slug(linkedin_url or "")
    if not slug:
        return True
    slug_words = {w for w in re.split(r"[-_0-9]+", slug.lower()) if len(w) > 2}
    compact = re.sub(r"[^a-z]", "", slug.lower())
    for word in re.findall(r"[A-Za-z]{3,}", name.lower()):
        if word in slug_words or word in compact:
            return True
    return False


#: A page saying the company took this person on, rather than merely printing
#: their name in the same paragraph as its own.
EMPLOYMENT_PHRASE = re.compile(
    r"\b(joins?|joined|joining|hire[sd]?|hiring|names?|named|appoint(?:s|ed|ment)?|"
    r"promote[sd]?|elevated|welcomes?|welcomed|has brought on|steps into)\b",
    re.I,
)
#: How close the company has to be named for a mention to count as evidence.
MENTION_WINDOW = 220


def _names_the_company(text: str, company_name: str, domain: str | None) -> bool:
    """Whether the text names the company by its brand, or by its domain's name.

    Brand, not legal name: "Engineering Manager at Meta" is Meta Platforms, Inc.
    evidence, and requiring "platforms" too threw it away. Whole words, both
    ways: "meta" inside "Metadata Engineer" is not the company. The brand rule
    and its one known over-match live in text_names_brand."""
    if text_names_brand(text, company_name):
        return True
    base = (normalize_domain(domain or "") or "").split(".")[0]
    return len(base) > 2 and text_names_brand(text, base)


#: "CEO & Founding Trainer @ Warner Digital", "Senior Buyer at Amazon" - the
#: title itself says who they work for.
EMPLOYER_IN_TITLE = re.compile(r"(?:\bat\b|@)\s+([A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,3})")


def _title_names_another_employer(role: str, company_name: str, domain: str | None) -> bool:
    """A stored Microsoft contact reading "David Warner II - CEO & Founding
    Trainer @ Warner Digital" is how a LinkedIn snippet for somebody else's
    employee ends up in this company's list. The title names the employer;
    believe it over the search that surfaced the page."""
    for match in EMPLOYER_IN_TITLE.finditer(role or ""):
        named = match.group(1).strip().rstrip(".,")
        if not named or named.lower() in {"work", "large", "scale", "home"}:
            continue
        if not _names_the_company(named, company_name, domain):
            return True
    return False


def _host_is_company(url: str, domain: str | None) -> bool:
    dom = normalize_domain(domain or "")
    if not dom:
        return False
    host = re.sub(r"^https?://", "", (url or "").strip().lower()).split("/")[0].split("@")[-1]
    host = host.split(":")[0]
    return host == dom or host.endswith("." + dom)


def _mention_affiliation(near: str, role: str, company_name: str, domain: str | None) -> str:
    """What a page that is not the company's, and not a profile, can prove.

    ``press_appointment`` - it names the company, a job, and says the company
    took this person on ("Chewy has hired Priya Raman - Director of Supply
    Chain"). Good enough to work out their address from the house pattern.
    ``press_mention``     - the company and a plausible job title merely appear
    near the name. Shown as a lead, never given an invented mailbox.
    ``""``                - no tie at all; not a lead.
    """
    if not (_names_the_company(near, company_name, domain) and COMPANY_ROLE_HINT.search(role)):
        return ""
    return "press_appointment" if EMPLOYMENT_PHRASE.search(near) else "press_mention"


# Which entity, and which country, a person works for.
#
# A Barclays run saved three people and none of them worked for Barclays in the
# UK: "Barclays Global Service Centre Private Limited" profiles pass every
# brand check because they say "Barclays". The run's entity profile (see
# yucgoutreach_discovery._entity_from_spec) names the entities that share the
# brand but are not the target, the country the member wants, and the mail
# domains that belong to it. A person is dropped only on something the page
# states; an unknown location never rejects anyone.

#: Captive and service-centre wording. Checked only when the run's entity is a
#: parent with exclusions of its own, and never when the entity is one of these.
CAPTIVE_ENTITY = re.compile(
    r"\bglobal services? cent(?:re|er)s?\b|"
    r"\bglobal (?:capability|business services|technology) cent(?:re|er)s?\b|"
    # "Head of Shared Services" is a job at the parent; only a shared-services
    # company or centre is a different employer.
    r"\bshared services? (?:cent(?:re|er)|company|pvt|private|ltd|limited)\b|"
    r"\b(?:india|philippines|poland) (?:pvt|private) (?:ltd|limited)\b|"
    r"\btechnology cent(?:re|er) india\b",
    re.I,
)
#: Written in capitals as an entity, never as a word: "Barclays GSC".
CAPTIVE_ACRONYM = re.compile(r"(?<![A-Za-z])(?:GSC|GCC)(?![A-Za-z])")

_LEGAL_TAIL = frozenset({
    "private", "pvt", "limited", "ltd", "plc", "llc", "inc", "corp", "corporation",
    "company", "co", "gmbh", "sa", "ag", "llp", "lp", "bv", "nv",
})

#: Where a profile says it is. Two-letter LinkedIn subdomains are countries
#: (in.linkedin.com, de.linkedin.com); uk is the one that is not ISO.
_LINKEDIN_COUNTRY_HOST = re.compile(r"^(?:https?://)?([a-z]{2})\.linkedin\.com/", re.I)

#: Places that settle the country when a snippet names them. Longest names
#: first, so "Northern Ireland" is not read as Ireland.
_PLACE_COUNTRY: tuple[tuple[str, str], ...] = (
    ("united arab emirates", "AE"), ("new south wales", "AU"), ("northern ireland", "GB"), ("united kingdom", "GB"),
    ("united states", "US"), ("czech republic", "CZ"), ("south africa", "ZA"),
    ("philippines", "PH"), ("netherlands", "NL"), ("switzerland", "CH"), ("singapore", "SG"),
    ("hong kong", "HK"), ("new york", "US"), ("new delhi", "IN"), ("australia", "AU"),
    ("bengaluru", "IN"), ("bangalore", "IN"), ("hyderabad", "IN"), ("gurugram", "IN"),
    ("maharashtra", "IN"), ("karnataka", "IN"), ("tamil nadu", "IN"), ("telangana", "IN"),
    ("scotland", "GB"), ("england", "GB"), ("germany", "DE"), ("ireland", "IE"),
    ("canada", "CA"), ("france", "FR"), ("brazil", "BR"), ("mexico", "MX"), ("poland", "PL"),
    ("czechia", "CZ"), ("chennai", "IN"), ("kolkata", "IN"), ("gurgaon", "IN"), ("mumbai", "IN"),
    ("india", "IN"), ("spain", "ES"), ("italy", "IT"), ("japan", "JP"), ("china", "CN"),
    ("wales", "GB"), ("delhi", "IN"), ("noida", "IN"), ("pune", "IN"), ("manila", "PH"),
    ("makati", "PH"), ("taguig", "PH"), ("krakow", "PL"), ("kraków", "PL"), ("warsaw", "PL"),
    ("wroclaw", "PL"), ("glasgow", "GB"), ("london", "GB"), ("manchester", "GB"),
    ("edinburgh", "GB"), ("northampton", "GB"), ("knutsford", "GB"), ("chicago", "US"),
    ("san francisco", "US"), ("boston", "US"), ("dublin", "IE"), ("paris", "FR"),
    ("frankfurt", "DE"), ("tokyo", "JP"), ("toronto", "CA"), ("sydney", "AU"),
    ("dubai", "AE"), ("johannesburg", "ZA"), ("prague", "CZ"), ("usa", "US"), ("uae", "AE"),
)
_PLACE_RE = re.compile(
    r"(?<![a-z])(" + "|".join(re.escape(p) for p, _ in _PLACE_COUNTRY) + r")(?![a-z])"
)
_PLACE_TO_COUNTRY = dict(_PLACE_COUNTRY)


def _norm_words(text: str) -> str:
    """Lowercase words separated by single spaces, padded, for phrase tests."""
    return " " + " ".join(re.findall(r"[a-z0-9]+", (text or "").lower())) + " "


def _entity_phrase(name: str) -> str:
    """An entity's name as a headline writes it: legal form dropped."""
    words = re.findall(r"[a-z0-9]+", (name or "").lower())
    while words and words[-1] in _LEGAL_TAIL:
        words.pop()
    return " ".join(words)


def _country_code(value: str | None) -> str | None:
    code = (value or "").strip().upper()
    if not code:
        return None
    return "GB" if code == "UK" else code


def profile_country(url: str) -> str | None:
    """The country a LinkedIn profile URL is filed under, or None for www."""
    m = _LINKEDIN_COUNTRY_HOST.match((url or "").strip())
    return _country_code(m.group(1)) if m else None


def text_countries(text: str) -> set[str]:
    """Countries a snippet places the person in, by country or city name."""
    return {_PLACE_TO_COUNTRY[m.group(1)] for m in _PLACE_RE.finditer((text or "").lower())}


def _allowed_mail_domains(entity: dict[str, Any], target: str) -> set[str]:
    allowed = {normalize_domain(entity.get("mail_domain") or "")}
    for alt in entity.get("alt_mail_domains") or []:
        if isinstance(alt, dict) and _country_code(alt.get("country")) == target:
            allowed.add(normalize_domain(alt.get("domain") or ""))
    return {d for d in allowed if d}


def _on_domain(host: str, domains: set[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def entity_gate(
    title: str, snippet: str, url: str, email: str, entity: dict[str, Any] | None,
) -> tuple[bool, str | None]:
    """(keep, reason) for one person against the run's entity profile.

    Reasons: ``other_entity`` (the headline names a sibling, subsidiary or
    captive), ``other_domain`` (the address is at a mail domain that is not the
    target's), ``other_country`` (the profile or snippet places them outside
    the country the member chose, or in an excluded entity's country when they
    chose none). "*" turns the country checks off.
    """
    if not entity:
        return True, None
    text = f"{title or ''}\n{snippet or ''}"
    words = _norm_words(text)
    chosen = {_entity_phrase(entity.get("legal_name") or ""), _entity_phrase(entity.get("display_name") or "")}
    chosen.discard("")
    brand = {w.lower() for w in entity.get("brand_words") or []}

    exclude = [x for x in entity.get("exclude") or [] if isinstance(x, dict)]
    for item in exclude:
        phrase = _entity_phrase(item.get("name") or "")
        # An exclusion that is the run's own entity, or that shrinks to the
        # bare brand, would reject every person the run is looking for.
        if not phrase or phrase in chosen or set(phrase.split()) <= brand:
            continue
        if f" {phrase} " in words:
            return False, "other_entity"
    chosen_is_captive = any(CAPTIVE_ENTITY.search(c) for c in chosen)
    if exclude and not chosen_is_captive and (CAPTIVE_ENTITY.search(text) or CAPTIVE_ACRONYM.search(text)):
        return False, "other_entity"

    # Where the member did not choose a country, only what is known to be
    # wrong is skipped: another entity's mail domain, and places where the
    # excluded entities sit (India, for Barclays' service centre). Requiring
    # the HQ country by default would also drop Barclays' New York bankers.
    # A country the member chose ("UK only") is enforced strictly; "*" turns
    # every country check off.
    raw_target = entity.get("target_country")
    anywhere = raw_target == "*"
    target = _country_code(raw_target) if raw_target and not anywhere else None
    hq = _country_code(entity.get("hq_country") or "") if entity.get("hq_country") else None
    excluded_domains = {
        normalize_domain(d) for x in exclude for d in (x.get("domains") or []) if d
    }
    excluded_countries = {
        _country_code(x.get("country")) for x in exclude if x.get("country")
    } - {hq, None}

    host = (email or "").rsplit("@", 1)[-1].strip().lower() if "@" in (email or "") else ""
    if host:
        if _on_domain(host, excluded_domains):
            return False, "other_domain"
        allowed = _allowed_mail_domains(entity, target) if target else None
        if allowed and not _on_domain(host, allowed):
            return False, "other_domain"

    if anywhere:
        return True, None
    country = profile_country(url)
    places = {country} if country else text_countries(text)
    if not places:
        return True, None
    if target:
        return (True, None) if target in places else (False, "other_country")
    if places & excluded_countries and not (hq and hq in places):
        return False, "other_country"
    return True, None


def note_skip(skips: dict[str, dict[str, str]] | None, reason: str | None, name: str) -> None:
    """Remember one dropped person under its reason, once per person."""
    if skips is None or not reason:
        return
    key = person_name_key(name) or (name or "").strip().lower()
    if key:
        skips.setdefault(reason, {}).setdefault(key, (name or "").strip())


def skipped_summary(
    skips: dict[str, dict[str, str]], saved_keys: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """research_json["skipped"]: count and up to five names per reason. A
    person dropped on one page and saved from another was not skipped."""
    out: dict[str, dict[str, Any]] = {}
    for reason, people in skips.items():
        names = [n for k, n in people.items() if k not in (saved_keys or set())]
        if names:
            out[reason] = {"count": len(names), "examples": names[:5]}
    return out


def _extract_people_from_results(
    results: list[dict[str, Any]],
    company_name: str,
    domain: str | None,
    *,
    entity: dict[str, Any] | None = None,
    skips: dict[str, dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Names from search results, each carrying why it is believed to work here.

    ``affiliation`` is the evidence, and the caller uses it to decide whether an
    address may be guessed:

    * ``company_site``     - the page is on the company's own domain.
    * ``linkedin_profile`` - the person's own profile, naming the company.
    * ``press_mention``    - a page that names them, a job title, and the
      company close together. Enough to show to a member, not enough to invent
      a mailbox from.
    """
    seen_keys: set[str] = set()
    people: list[dict[str, Any]] = []
    # The result being read. Its URL is the original one: the country in
    # in.linkedin.com is gone once the link is rewritten to www below.
    page: dict[str, str] = {}

    def add_person(
        name: str,
        title: str = "",
        linkedin_url: str = "",
        email: str = "",
        source_url: str = "",
        discovery_context: str = "",
        affiliation: str = "",
    ) -> None:
        name = _clean_person_name(name.strip())
        if not affiliation:
            return
        if not looks_like_person_name(name, company_name):
            return
        # A page describing itself: "Choose People", "Activity Image",
        # "Transformation Leader". Each of these was a real stored contact with
        # an invented address at the company.
        if NON_PERSON_NAME_WORD.search(name):
            return
        # A profile link belongs to one person. When it disagrees with the name
        # beside it, the page paired them, not the person.
        if not _name_agrees_with_profile(name, linkedin_url):
            return
        # A lawmaker quoted in a story about the industry is not a lead, and is
        # the exact shape the open web keeps offering.
        if PUBLIC_OFFICE_TITLE.search(f"{title} {discovery_context}"):
            return
        # The title naming a different employer outranks the search that found
        # the page: it is the page saying where this person actually works.
        if _title_names_another_employer(title, company_name, domain):
            return
        keep, reason = entity_gate(page.get("title", ""), page.get("content", ""),
                                   page.get("url", ""), email, entity)
        if not keep:
            note_skip(skips, reason, name)
            return
        key = person_name_key(name)
        if not key or key in seen_keys:
            return
        seen_keys.add(key)
        people.append(
            {
                "name": name,
                "title": title.strip()[:120],
                "linkedin_url": linkedin_url.strip(),
                "email": sanitize_email(email.strip()) if email else "",
                "source_url": source_url.strip(),
                "discovery_context": discovery_context.strip()[:400],
                "affiliation": affiliation,
            }
        )

    for r in results:
        title = r.get("title") or ""
        content = r.get("content") or ""
        url = r.get("url") or ""
        blob = f"{title}\n{content}\n{url}"
        ctx = f"{title} — {content[:200]}".strip()
        on_company_site = _host_is_company(url, domain)
        page.update(title=title, content=content, url=url)

        # What the page itself proves about anyone it names. A profile result
        # that mentions the company is that person's own page; a page on the
        # company's domain is the company talking about its own staff. A
        # profile link merely quoted inside someone else's article proves only
        # what the text beside it says.
        li_url = ""
        url_match = LINKEDIN_IN_URL.search(url)
        body_match = None if url_match else LINKEDIN_IN_URL.search(content)
        li_match = url_match or body_match
        if li_match:
            li_url = f"https://www.linkedin.com/in/{li_match.group(1)}/"

        page_affiliation = ""
        if on_company_site:
            page_affiliation = "company_site"
        elif url_match and _names_the_company(f"{title}\n{content}", company_name, domain):
            page_affiliation = "linkedin_profile"

        slug_name = _slug_to_name(li_match.group(1)) if li_match else None
        if li_match:
            if body_match:
                start = max(0, li_match.start() - MENTION_WINDOW)
                near = content[start:li_match.end() + MENTION_WINDOW]
                link_affiliation = (
                    "linkedin_profile" if _names_the_company(near, company_name, domain) else ""
                )
            else:
                link_affiliation = page_affiliation
            if slug_name and link_affiliation:
                add_person(
                    slug_name, title=title, linkedin_url=li_url, source_url=url or li_url,
                    discovery_context=ctx, affiliation=link_affiliation,
                )

        def page_evidence_for(name: str, role: str) -> str:
            """What the page proves about *this* name, not about the page.

            A profile page is about one person: the slug's. A company page is
            about the company, and the rest of it is navigation, support copy
            and shipping notices - "Order Status - Track a shipment at
            Microsoft" parses exactly like a person and a job. So a name that
            is not the profile's owner has to carry a job title of its own
            before the page's own standing is lent to it."""
            if not page_affiliation:
                return ""
            if slug_name and person_name_key(name) == person_name_key(slug_name):
                return page_affiliation
            return page_affiliation if COMPANY_ROLE_HINT.search(role or "") else ""

        # A profile link belongs to the profile's owner. Attaching it to every
        # other name on the page is how three different names ended up carrying
        # one man's LinkedIn URL.
        def link_for(name: str) -> str:
            if not li_url:
                return ""
            if slug_name and person_name_key(name) == person_name_key(slug_name):
                return li_url
            # Some slugs are one run of letters (katygeorge1), so no name can be
            # read out of them; the link is still hers if her name is in it.
            return li_url if not slug_name and _name_agrees_with_profile(name, li_url) else ""

        for m in NAME_TITLE_RE.finditer(blob):
            role = m.group(2)
            near = blob[max(0, m.start() - MENTION_WINDOW):m.end() + MENTION_WINDOW]
            affiliation = (page_evidence_for(m.group(1), role)
                           or _mention_affiliation(near, role, company_name, domain))
            if not affiliation:
                continue
            add_person(
                m.group(1), title=role, linkedin_url=link_for(m.group(1)), source_url=url,
                discovery_context=ctx, affiliation=affiliation,
            )

        for m in NAME_AT_COMPANY_RE.finditer(blob):
            # "Name at <something>" only counts when the something is this
            # company, not whichever employer the sentence happens to name -
            # and the page still has to be saying what they do there.
            after = blob[m.end():m.end() + 60]
            affiliation = page_evidence_for(m.group(1), title)
            if not affiliation and _names_the_company(after, company_name, domain):
                affiliation = (
                    "press_appointment"
                    if EMPLOYMENT_PHRASE.search(blob[max(0, m.start() - MENTION_WINDOW):m.end() + MENTION_WINDOW])
                    else "press_mention"
                )
            if not affiliation:
                continue
            add_person(
                m.group(1), title=title, linkedin_url=link_for(m.group(1)), source_url=url,
                discovery_context=ctx, affiliation=affiliation,
            )

        for email in extract_employee_emails_from_text(blob, domain):
            for person in people:
                first, *rest = person["name"].split()
                last = rest[-1] if rest else ""
                if email.split("@")[0].lower().find(last.lower()[:3]) >= 0:
                    if not person.get("email"):
                        person["email"] = email
                    break

    return people


async def discover_contacts_from_web(
    company_name: str,
    domain: str | None = None,
    *,
    max_people: int = 30,
    custom_patterns: list[str] | None = None,
    title_hints: str | None = None,
    cancel_event: asyncio.Event | None = None,
    on_progress: ProgressHook = None,
    entity: dict[str, Any] | None = None,
    skips: dict[str, dict[str, str]] | None = None,
) -> list[dict]:
    """
    Search the web for named employees (LinkedIn, press, directories).
    Returns contact dicts compatible with the scrape merge pipeline.

    ``entity`` is the run's entity profile: it adds searches aimed at the
    target entity and drops people entity_gate rejects, noting them in
    ``skips``.
    """
    company = (company_name or "").strip()
    if not company:
        return []

    dom = normalize_domain(domain or "") if domain else ""
    from app.services.web_fetch import web_search_configured
    if not web_search_configured() and not (os.getenv("TAVILY_API_KEY") or "").strip():
        return []

    async def emit(msg: str, pct: float) -> None:
        if on_progress:
            await on_progress(msg, pct)

    # Every query is one round trip, and they all run at once, so the cost of
    # the wave is its slowest query rather than their sum. What decides how
    # many reachable people come back is how many different places are asked:
    # profiles, the company's own pages, appointment notices, and the places
    # addresses are actually published.
    queries = [
        f'{company} leadership OR executives site:linkedin.com/in',
        f'{company} employees VP OR Director OR Manager site:linkedin.com/in',
        f'"{company}" team member site:linkedin.com/in',
        f'{company} CEO OR CFO OR CTO OR "head of" site:linkedin.com/in',
        f'"{company}" senior manager OR director biography',
        f'"{company}" press release appointed OR joins OR named',
        # People are reachable when a page prints their address or the company
        # publishes who does what, so ask for those pages directly.
        f'"{company}" "our team" OR "leadership team" OR "meet the team"',
        f'"{company}" spokesperson OR "media contact" OR "press contact" email',
        f'"{company}" conference speaker OR panelist OR webinar "{company}"',
    ]
    hints = (title_hints or "").strip()
    if hints:
        queries[0:0] = [
            f'{company} {hints} site:linkedin.com/in',
            f'"{company}" {hints} email OR contact OR LinkedIn',
            # The hint as the company would write it on its own pages.
            f'"{company}" "{hints.split(",")[0].strip()}" site:{dom}' if dom else f'"{company}" "{hints.split(",")[0].strip()}"',
        ]
    if dom:
        queries.extend(
            [
                f'"{company}" "@{dom}" email employee',
                f'site:linkedin.com/in "{company}" {dom.split(".")[0]}',
                f'site:{dom} team OR leadership OR people OR about',
                f'"@{dom}" contact OR email -jobs -careers',
            ]
        )
    # Searches aimed at the entity itself: its home city and its legal name.
    # They take the places of the two broadest queries, so the run asks no
    # more questions than before. The negative terms keep sibling entities
    # out of the results where the engine honours them; entity_gate is what
    # guarantees it.
    targeted = _entity_queries(company, hints, entity)
    if targeted:
        dropped = (f'"{company}" team member site:linkedin.com/in',
                   f'"{company}" conference speaker OR panelist OR webinar "{company}"')
        queries = [q for q in queries if q not in dropped[: len(targeted)]] + targeted

    await emit(f"Running {len(queries)} web searches at once…", 15)
    all_results = await _tavily_parallel(queries, max_results=SEARCH_PAGE_RESULTS)
    if cancel_event and cancel_event.is_set():
        return []

    people = _extract_people_from_results(
        all_results, company, dom or None, entity=entity, skips=skips,
    )[: max_people * 3]

    contacts: list[dict] = []
    for p in people[: max_people * 2]:
        if cancel_event and cancel_event.is_set():
            break
        name = p.get("name") or ""
        email = p.get("email") or ""
        email_verified = bool(email)
        affiliation = p.get("affiliation") or ""
        if not email and dom:
            # Inventing an address is only defensible where the page showed
            # the person works here: their own profile, the company's own site,
            # or a report that the company took them on. A name that merely
            # appeared near the company in an article keeps whatever address
            # the page published and is dropped without one - a guessed mailbox
            # for a guessed employee is how a stranger ends up in a campaign.
            if affiliation in ("company_site", "linkedin_profile", "press_appointment"):
                email = build_email_for_person_sync(name, dom, custom_patterns=custom_patterns) or ""
        if not email:
            continue
        row = {
            "name": name,
            "email": sanitize_email(email),
            "title": p.get("title"),
            "company": company,
            "company_domain": dom,
            "linkedin_url": p.get("linkedin_url") or None,
            "contact_source": "web_discovery",
            "source_url": p.get("source_url"),
            "discovery_context": p.get("discovery_context"),
            "affiliation_evidence": affiliation,
            "_email_verified": email_verified,
        }
        row["confidence"] = confidence_for_contact_dict(
            row,
            company_name=company,
            domain=dom,
            email_verified=email_verified,
        )
        row.pop("_email_verified", None)
        if is_valid_person_contact(row, company_name=company, domain=dom):
            contacts.append(row)

    await emit(f"Web discovery found {len(contacts)} person(s)", 85)
    return contacts


def _entity_queries(company: str, hints: str, entity: dict[str, Any] | None) -> list[str]:
    """At most two searches for the target entity: brand plus its home city,
    and its legal name with the roles asked for."""
    if not entity:
        return []
    brand = (entity.get("display_name") or company).strip()
    negatives = " ".join(
        f'-"{_entity_phrase(x.get("name") or "")}"'
        for x in (entity.get("exclude") or [])[:2]
        if isinstance(x, dict) and _entity_phrase(x.get("name") or "")
    )
    out: list[str] = []
    city = (entity.get("hq_city") or "").strip()
    if city:
        out.append(f'"{brand}" "{city}" site:linkedin.com/in {negatives}'.strip())
    legal = (entity.get("legal_name") or "").strip()
    if legal and _entity_phrase(legal) != _entity_phrase(company):
        out.append(f'"{legal}" {hints or "director OR head"} site:linkedin.com/in {negatives}'.strip())
    return out


def linkedin_profile_key(url: str | None) -> str | None:
    slug = extract_linkedin_profile_slug(url or "")
    return slug.lower() if slug else None
