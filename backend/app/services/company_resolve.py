"""Turn what a member typed or pasted into one company: a display name, a
mail domain only when it is verified, and a few alternatives to pick from.

Two inputs arrive through the same box. A LinkedIn company page URL names
the company exactly, but LinkedIn's terms prohibit scraping, so the page is
never fetched (not directly, not through fetch_page, TinyFish fetch or
Firecrawl): one web search restricted to linkedin.com reads the page title a
search engine already indexed, and a readable vanity slug is the fallback.
A typed name is matched against what the club already works with first,
then the company register, and otherwise kept exactly as typed.

The domain is the part that must never be a guess. A wrong domain looks like
a company with no findable people and sends mail to strangers, so it comes
back null unless a stored record or discover_company_domain (name match plus
MX) vouches for it.

Each answer also carries the company as an entity (company_entity): HQ
country, the mail domain its staff use, and the offshoots that are not it.
A known entity's mail domain is THE domain, and its subsidiaries and
regional variants come back as alternatives, so a member who means the
Barclays Global Service Centre picks it instead of getting it by accident.
"""
from __future__ import annotations

import copy
import logging
import re
import time
from typing import Any

from fastapi import HTTPException

from app.database import get_db
from app.services.company_claims import company_key
from app.services.linkedin_scraper import extract_linkedin_company_slug

logger = logging.getLogger(__name__)

# A member presses Enter on the same company several times while building a
# list; each press after the first should not spend another web search.
# ponytail: in-process dict, lost on restart and per worker; a shared cache
# (the DB) is the upgrade if more than one API process ever runs.
_CACHE_TTL_S = 24 * 3600
_CACHE_MAX = 2000
_cache: dict[str, tuple[float, dict[str, Any]]] = {}

_HOSTNAME_RE = re.compile(r"^(?=.{4,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")
# Search engines index a company page as "HBO | LinkedIn" (sometimes with a
# hyphen); a sub-page adds its tab name ("HBO: Jobs | LinkedIn").
_LINKEDIN_TITLE_SUFFIX = re.compile(r"\s*[|\-–]\s*linkedin\s*$", re.I)
_LINKEDIN_TAB_SUFFIX = re.compile(r"\s*:\s*(overview|about|jobs|people|posts|life|employees|insights)\b.*$", re.I)
_MAX_ALTERNATIVES = 4
# Offshoots and regional variants of a known entity, listed ahead of the
# name-alike alternatives and counted separately.
_MAX_ENTITY_ALTERNATIVES = 4
# Bumped when the response shape changes, so no cached answer from before
# the entity fields is served.
_CACHE_VERSION = "v2:"


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _cache_get(key: str) -> dict[str, Any] | None:
    hit = _cache.get(key)
    if not hit or time.monotonic() - hit[0] > _CACHE_TTL_S:
        _cache.pop(key, None)
        return None
    # A copy, so a caller mutating the response cannot edit the cache.
    return copy.deepcopy(hit[1])


def _cache_put(key: str, value: dict[str, Any]) -> None:
    if len(_cache) >= _CACHE_MAX:
        # Oldest entry out; dicts keep insertion order.
        _cache.pop(next(iter(_cache)))
    _cache[key] = (time.monotonic(), copy.deepcopy(value))


def _slug_display_name(slug: str) -> str | None:
    """A vanity slug is the company's own chosen handle, so it reads as the
    name ("warner-bros-discovery" -> "Warner Bros Discovery"). A numeric slug
    is LinkedIn's internal id and says nothing about the company."""
    if not slug or slug.isdigit():
        return None
    words = [w for w in re.split(r"[-_\s]+", slug) if w]
    # A lone handle of three letters or fewer is an acronym far more often than
    # a word: "hbo" is HBO and "ibm" IBM, where title case gave "Hbo".
    if len(words) == 1 and len(words[0]) <= 3 and words[0].isalpha():
        return words[0].upper()
    return " ".join(w[:1].upper() + w[1:] for w in words) or None


def _title_to_name(title: str) -> str | None:
    name = _LINKEDIN_TITLE_SUFFIX.sub("", title or "").strip()
    if name == (title or "").strip():
        # No "| LinkedIn" suffix: not a LinkedIn page title, so not a name.
        return None
    name = _LINKEDIN_TAB_SUFFIX.sub("", name).strip()
    return name or None


async def _linkedin_name(kind: str, slug: str, *, user_id: int | None) -> tuple[str | None, bool]:
    """(name from the indexed page title or None, whether search was
    unavailable). Exactly one web search; the result is only trusted when
    its URL is this same page, since a search for one slug can surface a
    neighbouring company's page."""
    from app.services.web_fetch import web_search

    # The site: operator becomes TinyFish's include_domains; the page path
    # stays in the query text, because the operator's own path is dropped.
    query = f"site:linkedin.com linkedin.com/{kind}/{slug}"
    try:
        results = await web_search(query, max_results=5, user_id=user_id)
    except HTTPException as exc:
        # 429 is the per-member web quota. The slug still names the company,
        # so the member gets that instead of an error.
        logger.info("resolve-company linkedin search unavailable (%s) for %s", exc.status_code, slug)
        return None, True
    except Exception as exc:  # a search outage must not fail the Add button
        logger.warning("resolve-company linkedin search failed for %s: %s", slug, exc)
        return None, True
    for item in results or []:
        found = extract_linkedin_company_slug(str((item or {}).get("url") or ""))
        if found and found.lower() == slug.lower():
            name = _title_to_name(str(item.get("title") or ""))
            if name:
                return name, False
    return None, False


async def _verified_domain(name: str) -> str | None:
    """discover_company_domain vouches for a domain by the club's own records
    or by name match plus MX. Two guards on top: the result must be a real
    hostname, and a name that is itself a dotted word ("Amazon.com") comes
    back from that resolver as-is without any check, so it must accept mail
    before it counts."""
    from app.services.company_email_cache import discover_company_domain

    lookup = name
    if " " in name:
        # "Warner Bros. Discovery" contains a dot, which the resolver takes as
        # "already a domain" and echoes back. Dropping the dots from a
        # multi-word name keeps it on the real lookup path.
        lookup = _clean(name.replace(".", " "))
    try:
        domain = (await discover_company_domain(lookup) or "").strip().lower()
    except Exception as exc:
        logger.warning("resolve-company domain lookup failed for %r: %s", name, exc)
        return None
    if not domain or not _HOSTNAME_RE.match(domain):
        return None
    if "." in lookup and domain == lookup.lower():
        from app.services.email_verifier import get_mx_cached

        try:
            mx_ok, _ = await get_mx_cached(domain, None)
        except Exception:
            mx_ok = False
        if not mx_ok:
            return None
    return domain


async def _club_companies(name: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """(the club company this name is, other club companies it could be).

    Matched on company_key, which drops case, punctuation and trailing
    company forms, so "Acme, Inc." typed finds contacts filed under "ACME".
    The domain is the one most of those contacts carry."""
    key = company_key(name)
    token = key[2:].split(" ")[0] if key[2:] else name.lower()
    if not token:
        return None, []
    db = await get_db()
    try:
        rows = await (await db.execute(
            """SELECT company, company_domain, COUNT(*) AS n FROM contacts
               WHERE company IS NOT NULL AND company != '' AND lower(company) LIKE ?
               GROUP BY company, company_domain ORDER BY n DESC LIMIT 200""",
            (f"%{token}%",),
        )).fetchall()
    finally:
        await db.close()
    # The spelling most contacts use; on a tie, one that is not all capitals
    # (a CSV import's "ACME WIDGETS" is the same company, just shouted).
    exact_rows = sorted(
        (r for r in rows if company_key(r["company"]) == key),
        key=lambda r: (-int(r["n"] or 0), str(r["company"]).isupper()),
    )
    best = None
    if exact_rows:
        with_domain = [r for r in exact_rows if (r["company_domain"] or "").strip()]
        best = {
            "name": exact_rows[0]["company"],
            "domain": (with_domain[0]["company_domain"].strip().lower() if with_domain else None),
        }
    # Other club companies that start with the typed words, as alternatives.
    prefix = key[2:]
    near = []
    for r in rows:
        other = company_key(r["company"])[2:]
        if other != prefix and prefix and other.startswith(prefix + " "):
            near.append({"name": r["company"], "domain": (r["company_domain"] or "").strip().lower() or None})
    return best, near


def _alternatives(chosen: str, *pools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = {company_key(chosen)}
    out: list[dict[str, Any]] = []
    for pool in pools:
        for item in pool:
            key = company_key(item["name"])
            if key in seen:
                continue
            seen.add(key)
            out.append({"name": item["name"], "domain": item.get("domain")})
            if len(out) >= _MAX_ALTERNATIVES:
                return out
    return out


async def _register_candidates(name: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    from app.services.company_register import search_register

    try:
        page = await search_register(q=name, limit=_MAX_ALTERNATIVES + 1)
    except Exception as exc:
        logger.warning("resolve-company register search failed for %r: %s", name, exc)
        return None, []
    items = [
        {"name": i["company_name"], "domain": (i.get("company_domain") or "").strip().lower() or None,
         "match_level": i.get("match_level")}
        for i in page.get("items") or []
    ]
    # Level 0-1 is "this is the company" (exact name, ticker, brand alias,
    # or the name starts with the typed words); anything lower only shares
    # letters with it, which is an alternative, not an answer.
    best = items[0] if items and items[0]["match_level"] is not None and items[0]["match_level"] <= 1 else None
    return best, [i for i in items if i is not best]


def _entity_alternatives(profile: dict[str, Any]) -> list[dict[str, Any]]:
    """The entity's offshoots in other countries, each an entity of its own,
    then the parent itself per region when it mails from regional domains."""
    from app.services.company_entity import subsidiary_profile

    out: list[dict[str, Any]] = []
    for item in profile.get("exclude") or []:
        sub = subsidiary_profile(profile, item)
        out.append({"name": item["name"], "domain": sub["mail_domain"], "country": item.get("country"),
                    "kind": item.get("kind"), "entity": sub})
    for alt in profile.get("alt_mail_domains") or []:
        if not alt.get("country"):
            continue  # another spelling of the same mailbox, not a region
        region = {**copy.deepcopy(profile), "mail_domain": alt["domain"], "mail_domain_evidence": 0,
                  "target_country": alt["country"], "alt_mail_domains": []}
        out.append({"name": f"{profile['display_name']} ({alt['country']})", "domain": alt["domain"],
                    "country": alt["country"], "kind": "region", "entity": region})
    return out[:_MAX_ENTITY_ALTERNATIVES]


def _with_entity(result: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    """Adds country and entity, keeps "domain" equal to the entity's mail
    domain, and puts the entity's offshoots first among the alternatives."""
    if result.get("domain") and not profile.get("mail_domain"):
        # A stored or verified domain the entity lookup could not choose on
        # its own is still the one this answer uses; the two never disagree.
        profile["mail_domain"] = result["domain"]
    if profile.get("mail_domain"):
        result.update(domain=profile["mail_domain"], domain_verified=True)
    entity_alts = _entity_alternatives(profile)
    seen = {company_key(a["name"]) for a in entity_alts} | {company_key(result["name"])}
    rest = [a for a in result.get("alternatives") or [] if company_key(a["name"]) not in seen]
    result["alternatives"] = entity_alts + rest
    result["country"] = profile.get("hq_country")
    result["entity"] = profile
    return result


async def resolve_company(q: str, *, user_id: int | None) -> dict[str, Any]:
    text = _clean(q)[:500]
    if not text:
        raise HTTPException(422, "Type a company name or paste its LinkedIn company page URL.")

    if "linkedin.com/" in text.lower():
        slug = extract_linkedin_company_slug(text)
        if not slug:
            raise HTTPException(
                422, "That LinkedIn link is not a company page. Paste a linkedin.com/company/... link or type the company name.",
            )
        kind = "showcase" if re.search(r"linkedin\.com/showcase/", text, re.I) else "company"
        cache_key = f"{_CACHE_VERSION}li:{kind}:{slug.lower()}"
        cached = _cache_get(cache_key)
        if cached:
            return cached
        name, search_unavailable = await _linkedin_name(kind, slug, user_id=user_id)
        name = name or _slug_display_name(slug)
        if not name:
            raise HTTPException(
                422, "That LinkedIn link uses a numeric company id, so the company name cannot be read from it. Type the company name instead.",
            )
        # A showcase page lives under /showcase/; rewriting it to /company/
        # would point at a page that may not exist.
        result: dict[str, Any] = {
            "name": name, "domain": None, "domain_verified": False,
            "linkedin_url": f"https://www.linkedin.com/{kind}/{slug}",
            "source": "linkedin", "alternatives": [],
        }
        if search_unavailable:
            # Quota hit or search down: the name is all this press can give,
            # plus what the reviewed table knows for free. Not cached, so the
            # next press after the quota resets tries again.
            from app.services.company_entity import profile_without_network

            result.update(country=None, entity=None)
            return _with_entity(result, profile_without_network(name))
        from app.services.company_entity import resolve_entity

        profile = await resolve_entity(name, user_id=user_id)
        club, _ = await _club_companies(name)
        stored = club["domain"] if club else None
        domain = profile.get("mail_domain") or stored or await _verified_domain(name)
        result.update(domain=domain, domain_verified=bool(domain))
        result = _with_entity(result, profile)
        _cache_put(cache_key, result)
        return result

    cache_key = f"{_CACHE_VERSION}n:{text.lower()}"
    cached = _cache_get(cache_key)
    if cached:
        return cached
    club, club_near = await _club_companies(text)
    register_best, register_rest = await _register_candidates(text)
    if club:
        chosen, source = club, "club"
    elif register_best:
        chosen, source = register_best, "register"
    else:
        chosen, source = {"name": text, "domain": None}, "typed"
    from app.services.company_entity import override_profile, resolve_entity

    # What was typed decides the entity when the reviewed table knows it:
    # "Barclays Global Service Centre" is that centre, even though the
    # register's best match for the words is Barclays itself, and "HBO" is
    # HBO, not its parent. Otherwise the chosen record's name is looked up.
    reviewed = override_profile(text)
    profile = await resolve_entity(text if reviewed else chosen["name"], user_id=user_id)
    if reviewed:
        if company_key(chosen["name"]) != company_key(profile["display_name"]):
            chosen_as_alt = [chosen] if source != "typed" else []
        else:
            chosen_as_alt = []
        chosen = {"name": profile["display_name"], "domain": profile.get("mail_domain")}
    else:
        chosen_as_alt = []
    # A stored domain (club contacts, register row) is a record the club
    # already acts on; otherwise only a verified resolution is returned.
    domain = profile.get("mail_domain") or chosen.get("domain") or await _verified_domain(chosen["name"])
    pools = [chosen_as_alt, club_near, ([register_best] if register_best and source != "register" else []),
             register_rest]
    result = {
        "name": chosen["name"], "domain": domain, "domain_verified": bool(domain),
        "linkedin_url": None, "source": source,
        "alternatives": _alternatives(chosen["name"], *pools),
    }
    result = _with_entity(result, profile)
    _cache_put(cache_key, result)
    return result
