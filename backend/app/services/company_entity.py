"""Which company a name means, where it is headquartered, which mail domain
its staff use, and which of its offshoots are NOT it.

A name alone was not specific enough. "Barclays" found people at the Barclays
Global Service Centre in Pune and addressed them at barclays.bank.in, because
every Barclays domain shares one mail tenant (MX cannot tell them apart) and
the first search hit that matched the name and had MX won. A member who types
"Barclays" means Barclays PLC in London and its people at barclays.com.

So a name now resolves to an EntityProfile: the parent's legal name, HQ
country, the mail domain chosen on evidence, and an exclude list of
subsidiaries, captives and service centres in other countries. Other code
filters people and picks domains with it. The sources run in order and each
one that fails hands over to the next; nothing here raises to the caller:

  override  a reviewed table of large multinationals with offshore captives
            or confusing domains (ENTITY_OVERRIDES)
  wikidata  one search plus two or three wbgetentities calls: websites,
            HQ, country, LEI, subsidiaries
  gleif     the LEI's record and direct children: HQ country and children
            in other countries (no websites; GLEIF has none)
  register  the company register's own country for the name
  heuristic the name as typed, with the brand words

EntityProfile (a plain dict, the shared contract with the run pipeline and
the frontend):
  legal_name, display_name: str; brand_words: [str]
  hq_country: ISO-3166 alpha-2 or None; hq_city: str or None
  mail_domain: str or None; mail_domain_evidence: int ("@domain" citations)
  alt_mail_domains: [{"domain", "country"}]
  exclude: [{"name", "country", "domains": [str], "kind": subsidiary|captive|region}]
  target_country: ISO-2, "*" (any country) or None; defaults to hq_country
  source: override|wikidata|gleif|register|heuristic
"""
from __future__ import annotations

import asyncio
import copy
import logging
import re
import time
from typing import Any

logger = logging.getLogger(__name__)

KINDS = ("subsidiary", "captive", "region")
SOURCES = ("override", "wikidata", "gleif", "register", "heuristic")

_HTTP_TIMEOUT_S = 3.0
_TOTAL_BUDGET_S = 6.0
_USER_AGENT = "YUCG-client-affairs/1.0 (company entity lookup; https://github.com/Andylol111)"
_WIKIDATA_API = "https://www.wikidata.org/w/api.php"
_GLEIF_API = "https://api.gleif.org/api/v1"

# Entity lookups hit two public APIs and one metered search, and a company's
# HQ and subsidiaries change over years, not days.
# ponytail: in-process dict, per worker and lost on restart; the upgrade is a
# company_entities table keyed by the normalised name.
_CACHE_TTL_S = 7 * 24 * 3600
_CACHE_MAX = 2000
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


# --- domains ------------------------------------------------------------

# Public suffixes a company registers UNDER, so "barclays.co.uk" and
# "barclays.bank.in" are registrable domains of their own, not subdomains of
# "co.uk" and "bank.in".
# ponytail: a hand list of the second-level suffixes seen in company mail;
# a missing one makes a ccTLD company look like a subdomain. The upgrade is
# the Public Suffix List (tldextract) if one ever shows up wrong here.
_SECOND_LEVEL_SUFFIXES = frozenset({
    "co.uk", "org.uk", "ac.uk", "gov.uk", "ltd.uk", "plc.uk", "me.uk",
    "com.au", "net.au", "org.au", "edu.au", "gov.au",
    "co.in", "bank.in", "net.in", "org.in", "firm.in", "gen.in", "ind.in", "ac.in", "gov.in",
    "co.nz", "org.nz", "co.za", "org.za", "co.jp", "or.jp", "ne.jp", "ac.jp",
    "co.kr", "or.kr", "com.sg", "edu.sg", "com.hk", "org.hk", "com.cn", "net.cn", "org.cn",
    "com.tw", "co.id", "com.my", "com.ph", "com.br", "com.mx", "com.ar", "com.co",
    "com.tr", "co.il", "com.sa", "com.eg", "com.ng", "co.ke", "com.pk", "com.vn",
})
# Two-letter TLDs that companies use as generic names, not as a country.
_GENERIC_CCTLDS = frozenset({"io", "co", "ai", "me", "tv", "ly", "fm", "gg", "to", "cc", "ws", "eu"})
_HOST_RE = re.compile(r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_EMAIL_DOMAIN_RE = re.compile(r"[a-z0-9._%+-]+@((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63})", re.I)


def registrable_domain(host_or_url: str) -> str:
    """The domain a company registers: "mail.barclays.co.uk" -> "barclays.co.uk",
    "https://www.barclays.com/x" -> "barclays.com", "in.ibm.com" -> "ibm.com".
    "" for anything that is not a hostname."""
    text = (host_or_url or "").strip().lower()
    text = re.sub(r"^[a-z][a-z0-9+.-]*://", "", text).split("/")[0].split("?")[0].split(":")[0]
    text = text.strip(".")
    if not _HOST_RE.match(text):
        return ""
    labels = text.split(".")
    keep = 3 if len(labels) >= 3 and ".".join(labels[-2:]) in _SECOND_LEVEL_SUFFIXES else 2
    return ".".join(labels[-keep:])


def domain_country(domain: str) -> str | None:
    """ISO alpha-2 country a domain is coded for ("barclays.co.uk" -> "GB",
    "barclays.bank.in" -> "IN"), None for a generic one (.com, .io, .barclays)."""
    tld = (domain or "").rsplit(".", 1)[-1].lower()
    if len(tld) != 2 or tld in _GENERIC_CCTLDS:
        return None
    return "GB" if tld == "uk" else tld.upper()


def _brand_label_ok(brand_name: str, domain: str) -> bool:
    """The domain's name label IS the company's name, opening words or
    initials (roster_watch's rule), read on the registrable domain so the
    label of "barclays.bank.in" is "barclays", not "bank"."""
    from app.services.roster_watch import _domain_matches_company

    reg = registrable_domain(domain)
    return bool(reg) and _domain_matches_company(brand_name, reg)


def choose_mail_domain(
    candidates: list[str],
    brand: str,
    target_country: str | None = None,
    *,
    hq_country: str | None = None,
    exclude: list[dict[str, Any]] | tuple = (),
    alt_mail_domains: list[dict[str, Any]] | tuple = (),
    citations: dict[str, int] | None = None,
    stored: set[str] | frozenset | tuple = (),
    curated: str | None = None,
) -> tuple[str | None, int]:
    """(the mail domain to use, how many "@domain" citations back it).

    Pure scoring over candidates that already passed MX; the caller gathers
    them. Scores, as the product owner set them:
      +3 per "@domain" citation (at most three count), +3 a stored pattern
      with verified samples, +3 the reviewed override's domain, +2 a .com
      whose label is the brand; -3 a country-coded domain (co.uk, bank.in,
      .de) unless it is coded for the target country; an exclude[] domain is
      never chosen unless its entity is in the target country.
    One addition: when a member explicitly targets a country other than the
    HQ, the domain declared for that country (alt or exclude entry) gets +12,
    which is the point of asking for that country.
    "*" and None both score against the HQ country.
    """
    citations = citations or {}
    target = target_country if target_country and target_country != "*" else hq_country
    excluded: dict[str, str | None] = {}
    for item in exclude or ():
        for d in item.get("domains") or ():
            excluded[registrable_domain(d) or d] = item.get("country")
    declared = {**{registrable_domain(a.get("domain") or ""): a.get("country") for a in alt_mail_domains or ()},
                **excluded}
    scored: list[tuple[int, int, str]] = []
    for order, raw in enumerate(dict.fromkeys(registrable_domain(c) or "" for c in candidates or ())):
        d = raw
        if not d:
            continue
        if d in excluded and excluded[d] != target:
            continue
        country = domain_country(d)
        score = 3 * min(int(citations.get(d, 0)), 3)
        if d in stored:
            score += 3
        if curated and d == registrable_domain(curated):
            score += 3
        if not country and d.endswith(".com") and _brand_label_ok(brand, d):
            score += 2
        if country and country != target:
            score -= 3
        if target and hq_country and target != hq_country and declared.get(d) == target:
            score += 12
        # Ties keep candidate order, which is the caller's evidence order.
        scored.append((-score, order, d))
    if not scored:
        return None, 0
    best = min(scored)[2]
    return best, int(citations.get(best, 0))


def parent_domain_rank(name: str, domain: str) -> int | None:
    """How acceptable a stored domain is for a lookup of ``name`` itself (the
    parent, not a subsidiary): 0 generic (.com), 1 coded for the known HQ
    country, 2 country-coded with the HQ unknown, None never (listed under
    the entity's exclude[], or coded for another country than a known HQ).
    Callers take the lowest rank, so a stored barclays.bank.in pattern no
    longer answers for "Barclays"."""
    reg = registrable_domain(domain) or (domain or "").lower()
    profile = override_profile(name)
    hq = profile["hq_country"] if profile else None
    if profile:
        for item in profile["exclude"]:
            if reg in item["domains"] and item.get("country") != hq:
                return None
    country = domain_country(reg)
    if not country:
        return 0
    if hq:
        return 1 if country == hq else None
    return 2


# --- names --------------------------------------------------------------

_TRAILING_NOISE = frozenset({"private", "pvt", "pte", "llp", "ltd", "limited", "inc", "plc", "co", "company"})


def _norm(name: str) -> str:
    from app.services.company_email_cache import company_brand_words

    words = company_brand_words(name or "")
    while len(words) > 1 and words[-1] in _TRAILING_NOISE:
        words.pop()
    return " ".join(words)


def _brand_words(name: str) -> list[str]:
    from app.services.company_email_cache import company_brand_words

    return company_brand_words(name or "")[:10]


# --- the reviewed override table ----------------------------------------

def _o(legal: str, display: str, country: str, city: str, mail: str, *,
       alt: tuple = (), exclude: tuple = (), aliases: tuple = (), target: str | None = None) -> dict[str, Any]:
    return {
        "legal_name": legal, "display_name": display, "hq_country": country, "hq_city": city,
        "mail_domain": mail, "alt": alt, "exclude": exclude, "aliases": aliases, "target": target,
    }


# The review bar is BRAND_ALIASES' in company_register.py: add an entity only
# when its HQ, mail domain and offshoots are public and stable, because a
# stale row sends a whole run to the wrong people. Every row is recalled
# unless its comment says otherwise; "verify" marks a mail domain nobody has
# yet confirmed from a published staff address or a people-data vendor.
# exclude entries: (name, ISO country, own mail domains, kind). A captive that
# mails from the parent's domain (Goldman Sachs Services India uses gs.com)
# lists no domain, so the parent's domain is never excluded by accident; its
# name is still there for filtering people.
# target="*": a network of member firms (the Big Four, the strategy houses),
# where the HQ country says nothing about where a member's contacts sit.
ENTITY_OVERRIDES: dict[str, dict[str, Any]] = {
    # Checked 2026-09-22: one Proofpoint MX tenant serves barclays.com,
    # barclays.co.uk, barclays.bank.in and barcap.com; Barclays' own broker
    # contact page publishes a @barclays.com address; RocketReach, LeadIQ and
    # Clay all give first.last@barclays.com. home.barclays has no MX.
    "barclays": _o("Barclays PLC", "Barclays", "GB", "London", "barclays.com",
                   alt=(("barclays.co.uk", "GB"),),
                   exclude=(("Barclays Global Service Centre", "IN", ("barclays.bank.in",), "captive"),
                            ("Barclays Bank India", "IN", ("barclays.bank.in",), "subsidiary"),
                            ("Barclays Technology Centre India", "IN", (), "captive")),
                   aliases=("barclays plc", "barclays bank", "barclays bank plc", "barclays uk", "barclays capital", "barcap")),
    # hsbc.com verify; hsbc.co.in is HSBC India's site (recalled, verify).
    "hsbc": _o("HSBC Holdings plc", "HSBC", "GB", "London", "hsbc.com",
               exclude=(("HSBC Global Service Centre", "IN", (), "captive"),
                        ("HSBC Electronic Data Processing India", "IN", (), "captive"),
                        ("HSBC Software Development India", "IN", (), "captive"),
                        ("HSBC India", "IN", ("hsbc.co.in",), "subsidiary")),
               aliases=("hsbc holdings", "hsbc bank", "hsbc bank plc")),
    # Checked 2026-09-22 (LeadIQ, RocketReach): J.P. Morgan staff mostly
    # first.last@jpmorgan.com; JPMorgan Chase & Co. as a whole jpmchase.com.
    "jpmorgan chase": _o("JPMorgan Chase & Co.", "JPMorgan Chase", "US", "New York", "jpmorgan.com",
                         alt=(("jpmchase.com", None),),
                         exclude=(("JPMorgan Services India", "IN", (), "captive"),
                                  ("JPMorgan Chase Corporate Centre India", "IN", (), "captive")),
                         aliases=("jpmorgan", "jp morgan", "j p morgan", "jpmorganchase", "jpmc", "chase", "jp morgan chase")),
    "deutsche bank": _o("Deutsche Bank AG", "Deutsche Bank", "DE", "Frankfurt", "db.com",
                        exclude=(("Deutsche India", "IN", (), "captive"),
                                 ("DB Global Technology", None, (), "captive")),
                        aliases=("deutsche bank ag",)),
    "goldman sachs": _o("The Goldman Sachs Group, Inc.", "Goldman Sachs", "US", "New York", "gs.com",
                        exclude=(("Goldman Sachs Services India", "IN", (), "captive"),),
                        aliases=("goldman sachs group", "goldman")),
    "morgan stanley": _o("Morgan Stanley", "Morgan Stanley", "US", "New York", "morganstanley.com",
                         exclude=(("Morgan Stanley Advantage Services", "IN", (), "captive"),)),
    "wells fargo": _o("Wells Fargo & Company", "Wells Fargo", "US", "San Francisco", "wellsfargo.com",
                      exclude=(("Wells Fargo India Solutions", "IN", (), "captive"),
                               ("Wells Fargo International Solutions", "IN", (), "captive"))),
    "citi": _o("Citigroup Inc.", "Citi", "US", "New York", "citi.com",
               exclude=(("Citicorp Services India", "IN", (), "captive"),
                        ("Citi Service Center", "IN", (), "captive")),
               aliases=("citigroup", "citibank", "citicorp")),
    "bank of america": _o("Bank of America Corporation", "Bank of America", "US", "Charlotte", "bofa.com",  # verify
                          exclude=(("BA Continuum India", "IN", (), "captive"),),
                          aliases=("bofa", "bank of america merrill lynch", "merrill lynch")),
    "ubs": _o("UBS Group AG", "UBS", "CH", "Zurich", "ubs.com",
              exclude=(("UBS Business Solutions", "IN", (), "captive"),), aliases=("ubs group",)),
    "bnp paribas": _o("BNP Paribas SA", "BNP Paribas", "FR", "Paris", "bnpparibas.com",
                      exclude=(("BNP Paribas India Solutions", "IN", (), "captive"),)),
    "societe generale": _o("Société Générale S.A.", "Société Générale", "FR", "Paris", "socgen.com",  # verify
                           exclude=(("Societe Generale Global Solution Centre", "IN", (), "captive"),),
                           aliases=("socgen", "societe generale sa")),
    "nomura": _o("Nomura Holdings, Inc.", "Nomura", "JP", "Tokyo", "nomura.com",
                 exclude=(("Nomura Structured Finance Services", "IN", (), "captive"),)),
    "standard chartered": _o("Standard Chartered PLC", "Standard Chartered", "GB", "London", "sc.com",
                             exclude=(("Standard Chartered Global Business Services", "IN", (), "captive"),
                                      ("Standard Chartered GBS", "IN", (), "captive")),
                             aliases=("standard chartered bank",)),
    # Checked 2026-09-22 (LeadIQ, RocketReach): first.last@aexp.com.
    "american express": _o("American Express Company", "American Express", "US", "New York", "aexp.com",
                           exclude=(("American Express India", "IN", (), "subsidiary"),
                                    ("American Express Global Business Travel", None, (), "subsidiary")),
                           aliases=("amex",)),
    "blackrock": _o("BlackRock, Inc.", "BlackRock", "US", "New York", "blackrock.com",
                    exclude=(("BlackRock Services India", "IN", (), "captive"),)),
    "fidelity investments": _o("FMR LLC", "Fidelity Investments", "US", "Boston", "fmr.com",  # verify
                               exclude=(("Fidelity Business Services India", "IN", (), "captive"),),
                               aliases=("fidelity", "fmr")),
    "capital one": _o("Capital One Financial Corporation", "Capital One", "US", "McLean", "capitalone.com",
                      aliases=("capital one financial",)),
    "mastercard": _o("Mastercard Incorporated", "Mastercard", "US", "Purchase", "mastercard.com"),
    "visa": _o("Visa Inc.", "Visa", "US", "San Francisco", "visa.com"),
    "accenture": _o("Accenture plc", "Accenture", "IE", "Dublin", "accenture.com", target="*"),
    "deloitte": _o("Deloitte Touche Tohmatsu Limited", "Deloitte", "GB", "London", "deloitte.com",
                   alt=(("deloitte.co.uk", "GB"),),  # verify
                   exclude=(("Deloitte USI", "IN", (), "captive"),
                            ("Deloitte US India", "IN", (), "captive"),
                            ("Deloitte Shared Services India", "IN", (), "captive")),
                   aliases=("deloitte touche tohmatsu", "deloitte llp"), target="*"),
    "ey": _o("Ernst & Young Global Limited", "EY", "GB", "London", "ey.com",
             exclude=(("EY Global Delivery Services India", "IN", (), "captive"),
                      ("EY GDS", "IN", (), "captive")),
             aliases=("ernst young", "ernst and young", "ernst young global"), target="*"),
    "pwc": _o("PricewaterhouseCoopers International Limited", "PwC", "GB", "London", "pwc.com",
              exclude=(("PwC Acceleration Centers", "IN", (), "captive"),
                       ("PwC Service Delivery Center", "IN", (), "captive"),
                       ("PwC SDC", "IN", (), "captive")),
              aliases=("pricewaterhousecoopers", "price waterhouse coopers"), target="*"),
    "kpmg": _o("KPMG International Limited", "KPMG", "GB", "London", "kpmg.com",
               alt=(("kpmg.co.uk", "GB"),),  # verify
               exclude=(("KPMG Global Services", "IN", (), "captive"),
                        ("KGS", "IN", (), "captive")),
               aliases=("kpmg international", "kpmg llp"), target="*"),
    "mckinsey": _o("McKinsey & Company", "McKinsey & Company", "US", "New York", "mckinsey.com",
                   exclude=(("McKinsey Knowledge Centre India", "IN", (), "captive"),),
                   aliases=("mckinsey company", "mckinsey and company"), target="*"),
    "boston consulting group": _o("The Boston Consulting Group, Inc.", "Boston Consulting Group", "US", "Boston",
                                  "bcg.com", aliases=("bcg",), target="*"),
    "bain": _o("Bain & Company, Inc.", "Bain & Company", "US", "Boston", "bain.com",
               aliases=("bain company", "bain and company"), target="*"),
    "microsoft": _o("Microsoft Corporation", "Microsoft", "US", "Redmond", "microsoft.com",
                    exclude=(("Microsoft India Development Center", "IN", (), "captive"),)),
    "google": _o("Google LLC", "Google", "US", "Mountain View", "google.com",
                 aliases=("alphabet", "google llc", "alphabet inc")),
    # Checked 2026-09-22 (RocketReach, mailsfinder): meta.com; fb.com is the
    # pre-rename domain still seen on older records.
    "meta": _o("Meta Platforms, Inc.", "Meta", "US", "Menlo Park", "meta.com",
               alt=(("fb.com", None),), aliases=("meta platforms", "facebook")),
    "amazon": _o("Amazon.com, Inc.", "Amazon", "US", "Seattle", "amazon.com",
                 exclude=(("Amazon Development Centre India", "IN", (), "captive"),),
                 aliases=("amazon com", "aws", "amazon web services")),
    "apple": _o("Apple Inc.", "Apple", "US", "Cupertino", "apple.com"),
    "ibm": _o("International Business Machines Corporation", "IBM", "US", "Armonk", "ibm.com",
              exclude=(("IBM India", "IN", (), "subsidiary"),),
              aliases=("international business machines",)),
    # HQ moved to Austin in 2020; a later move to Nashville was announced
    # (recalled, verify the city).
    "oracle": _o("Oracle Corporation", "Oracle", "US", "Austin", "oracle.com",
                 exclude=(("Oracle India", "IN", (), "subsidiary"),)),
    "sap": _o("SAP SE", "SAP", "DE", "Walldorf", "sap.com",
              exclude=(("SAP Labs India", "IN", (), "captive"),)),
    "siemens": _o("Siemens AG", "Siemens", "DE", "Munich", "siemens.com",
                  exclude=(("Siemens Technology and Services", "IN", (), "captive"),)),
    "shell": _o("Shell plc", "Shell", "GB", "London", "shell.com",
                exclude=(("Shell Business Operations", "IN", (), "captive"),)),
    "unilever": _o("Unilever PLC", "Unilever", "GB", "London", "unilever.com"),
    "walmart": _o("Walmart Inc.", "Walmart", "US", "Bentonville", "walmart.com",
                  exclude=(("Walmart Global Tech India", "IN", (), "captive"),)),
    "target": _o("Target Corporation", "Target", "US", "Minneapolis", "target.com",
                 exclude=(("Target Corporation India", "IN", (), "captive"),)),
    "lowe s": _o("Lowe's Companies, Inc.", "Lowe's", "US", "Mooresville", "lowes.com",
                 exclude=(("Lowe's India", "IN", (), "captive"),), aliases=("lowes",)),
    "warner bros discovery": _o("Warner Bros. Discovery, Inc.", "Warner Bros. Discovery", "US", "New York", "wbd.com",
                                aliases=("wbd", "warner bros discovery inc")),
    # A brand of its own inside Warner Bros. Discovery; hbo.com verify (some
    # staff moved to wbd.com after the merger).
    "hbo": _o("Home Box Office, Inc.", "HBO", "US", "New York", "hbo.com",
              aliases=("home box office", "hbo max")),
}

# Every spelling (key, aliases, excluded offshoots) -> (entry key, offshoot index or None).
_OVERRIDE_INDEX: dict[str, tuple[str, int | None]] = {}


def _build_index() -> None:
    for key, entry in ENTITY_OVERRIDES.items():
        for spelling in (key, entry["legal_name"], entry["display_name"], *entry["aliases"]):
            _OVERRIDE_INDEX.setdefault(_norm(spelling), (key, None))
    for key, entry in ENTITY_OVERRIDES.items():
        for i, ex in enumerate(entry["exclude"]):
            _OVERRIDE_INDEX.setdefault(_norm(ex[0]), (key, i))


def _exclude_item(name: str, country: str | None, domains, kind: str) -> dict[str, Any]:
    return {"name": name, "country": country, "domains": list(domains), "kind": kind}


def _parent_from_override(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "legal_name": entry["legal_name"], "display_name": entry["display_name"],
        "brand_words": _brand_words(entry["display_name"]),
        "hq_country": entry["hq_country"], "hq_city": entry["hq_city"],
        "mail_domain": entry["mail_domain"], "mail_domain_evidence": 0,
        "alt_mail_domains": [{"domain": d, "country": c} for d, c in entry["alt"]],
        "exclude": [_exclude_item(*ex) for ex in entry["exclude"]],
        "target_country": entry["target"] or entry["hq_country"],
        "source": "override",
    }


def subsidiary_profile(parent: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    """An exclude[] entry as an entity of its own, for a member who means the
    service centre itself. It mails from its own domain when it has one, else
    from the parent's (most captives do)."""
    domains = [d for d in item.get("domains") or [] if d]
    return {
        "legal_name": item["name"], "display_name": item["name"], "brand_words": _brand_words(item["name"]),
        "hq_country": item.get("country"), "hq_city": None,
        "mail_domain": domains[0] if domains else parent.get("mail_domain"),
        "mail_domain_evidence": 0, "alt_mail_domains": [], "exclude": [],
        "target_country": item.get("country") or parent.get("target_country"),
        "source": parent.get("source") or "heuristic",
    }


def override_profile(name: str) -> dict[str, Any] | None:
    """The reviewed profile for this name (a parent, or one of its listed
    offshoots as its own entity), or None. No network."""
    if not _OVERRIDE_INDEX:
        _build_index()
    hit = _OVERRIDE_INDEX.get(_norm(name))
    if not hit:
        return None
    key, index = hit
    parent = _parent_from_override(ENTITY_OVERRIDES[key])
    if index is None:
        return parent
    return subsidiary_profile(parent, parent["exclude"][index])


def heuristic_profile(name: str) -> dict[str, Any]:
    text = re.sub(r"\s+", " ", (name or "").strip())[:200]
    return {
        "legal_name": text, "display_name": text, "brand_words": _brand_words(text),
        "hq_country": None, "hq_city": None, "mail_domain": None, "mail_domain_evidence": 0,
        "alt_mail_domains": [], "exclude": [], "target_country": None, "source": "heuristic",
    }


def profile_without_network(name: str) -> dict[str, Any]:
    """Override or heuristic, for a caller that must not spend anything."""
    return override_profile(name) or heuristic_profile(name)


# --- network sources ----------------------------------------------------

class _Budget:
    """One wall-clock allowance for every external call in a resolution, so
    a slow API eats into the next one's time instead of adding to it."""

    def __init__(self, seconds: float) -> None:
        self.deadline = time.monotonic() + seconds
        self.failed = False

    def timeout(self) -> float:
        return max(0.0, min(_HTTP_TIMEOUT_S, self.deadline - time.monotonic()))


async def _http_get_json(url: str, params: dict[str, Any] | None, timeout: float) -> Any:
    """GET -> parsed JSON; None for 404. Raises on anything else. Isolated so
    tests replace the network here."""
    import httpx

    async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": _USER_AGENT,
                                                            "Accept": "application/json"}) as client:
        response = await client.get(url, params=params)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


async def _get(url: str, params: dict[str, Any] | None, budget: _Budget) -> Any:
    wait = budget.timeout()
    if wait <= 0.05:
        budget.failed = True
        return None
    try:
        return await asyncio.wait_for(_http_get_json(url, params, wait), timeout=wait)
    except Exception as exc:
        budget.failed = True
        logger.info("entity lookup %s failed: %s", url, exc.__class__.__name__)
        return None


_CAPTIVE_WORDS = re.compile(
    r"\b(service|services|solutions|technology|technologies|operations|shared|centre|center|"
    r"delivery|business services|global business|gbs|gcc|capability|processing|data process\w*)\b", re.I)
_SKIP_CHILD = re.compile(r"\b(nominees?|funding|trustees?)\b", re.I)


def _kind_for(name: str) -> str:
    return "captive" if _CAPTIVE_WORDS.search(name or "") else "subsidiary"


def _claims(entity: dict[str, Any], prop: str) -> list[Any]:
    """Values of one Wikidata property: strings as-is, items as their Q-id."""
    out = []
    for statement in (entity.get("claims") or {}).get(prop) or []:
        if statement.get("rank") == "deprecated":
            continue
        value = ((statement.get("mainsnak") or {}).get("datavalue") or {}).get("value")
        if isinstance(value, dict):
            value = value.get("id")
        if value:
            out.append(value)
    return out


def _label(entity: dict[str, Any] | None) -> str:
    return str((((entity or {}).get("labels") or {}).get("en") or {}).get("value") or "")


async def _entities(ids: list[str], budget: _Budget) -> dict[str, Any]:
    """wbgetentities for up to 50 ids. The Action API, not SPARQL: the query
    service timed out at 15s for Barclays' plain properties on 2026-09-22,
    while this answers from cache in well under a second."""
    ids = list(dict.fromkeys(i for i in ids if re.fullmatch(r"Q\d+", str(i or ""))))[:50]
    if not ids:
        return {}
    data = await _get(_WIKIDATA_API, {"action": "wbgetentities", "ids": "|".join(ids),
                                      "props": "claims|labels", "languages": "en", "format": "json"}, budget)
    return (data or {}).get("entities") or {}


async def _wikidata(name: str, budget: _Budget) -> dict[str, Any] | None:
    """{label, websites, hq_city, hq_country, leis, subs: [{name, country, domains}]} or None.
    Properties: P856 website, P159 HQ, P17 country (-> P297 ISO code), P1278
    LEI, P355 subsidiaries; P1082 population marks the HQ item that is a city
    rather than a building."""
    found = await _get(_WIKIDATA_API, {
        "action": "wbsearchentities", "search": name[:200], "language": "en",
        "type": "item", "limit": 7, "format": "json",
    }, budget)
    want = _norm(name)
    # Only items whose label IS the name: "Barclays" must not take
    # "Barclays Center" (an arena) for the bank.
    ids = [r["id"] for r in ((found or {}).get("search") or [])
           if want and r.get("id") and _norm(str(r.get("label") or "")) == want][:3]
    items = await _entities(ids, budget)
    # The search's order among same-label items, first one that looks like
    # an organisation (a website, an LEI or an HQ; "apple" the fruit has none).
    item = next((items[i] for i in ids if i in items and (
        _claims(items[i], "P856") or _claims(items[i], "P1278") or _claims(items[i], "P159"))), None)
    if not item:
        return None
    hq_ids, country_ids = _claims(item, "P159"), _claims(item, "P17")
    sub_ids = _claims(item, "P355")[:40]
    related = await _entities([*hq_ids, *country_ids, *sub_ids], budget)
    sub_country_ids = [c for s in sub_ids for c in _claims(related.get(s) or {}, "P17")[:1]]
    related.update(await _entities([c for c in sub_country_ids if c not in related], budget))

    def iso(qid: str | None) -> str | None:
        code = next(iter(_claims(related.get(qid or "") or {}, "P297")), None)
        return str(code).upper() if code else None

    def first_iso(qids: list[str]) -> str | None:
        return next((code for code in map(iso, qids) if code), None)

    hq_country = first_iso([c for h in hq_ids for c in _claims(related.get(h) or {}, "P17")[:1]])
    city = next((_label(related.get(h)) for h in hq_ids if _claims(related.get(h) or {}, "P1082")), None)
    subs = []
    for sid in sub_ids:
        sub = related.get(sid) or {}
        label = _label(sub)
        if not label:
            continue
        country = first_iso(_claims(sub, "P17")[:1])
        domains = list(dict.fromkeys(d for d in (registrable_domain(w) for w in _claims(sub, "P856")) if d))
        subs.append({"name": label, "country": country, "domains": domains})
    return {
        "label": _label(item) or name, "websites": [str(w) for w in _claims(item, "P856")][:10],
        "hq_city": city or None, "hq_country": hq_country or first_iso(country_ids),
        "leis": [str(x) for x in _claims(item, "P1278")][:3], "subs": subs,
    }


async def _gleif(leis: list[str], legal_name: str | None, budget: _Budget) -> dict[str, Any] | None:
    """{legal_name, hq_country, hq_city, children: [{name, country}]} or None."""
    want = _norm(legal_name or "")
    if leis:
        got = await asyncio.gather(*(_get(f"{_GLEIF_API}/lei-records/{lei}", None, budget) for lei in leis))
        records = [(r or {}).get("data") for r in got if (r or {}).get("data")]
    else:
        records = ((await _get(f"{_GLEIF_API}/lei-records", {
            "filter[entity.legalName]": (legal_name or "")[:200], "page[size]": 10,
        }, budget)) or {}).get("data") or []

    def ent(r: dict[str, Any]) -> dict[str, Any]:
        return (r.get("attributes") or {}).get("entity") or {}

    def exact(r: dict[str, Any]) -> bool:
        return bool(want) and _norm(str((ent(r).get("legalName") or {}).get("name") or "")) == want

    # GLEIF's name filter is fuzzy ("Barclays" returns "BARCLAYS FUNDS -
    # BARCLAYS EQUITY ASIA"), and Wikidata can list a subsidiary's LEI next
    # to the parent's. The company is the GENERAL entity whose legal name IS
    # the name; from Wikidata's own LEIs, the first one when none is exact.
    general = [r for r in records if ent(r).get("category") in (None, "GENERAL")]
    record = next((r for r in general if exact(r)), None)
    if record is None and leis:
        record = general[0] if general else None
    if not record:
        return None
    entity = ent(record)
    lei = record.get("id")
    hq = entity.get("headquartersAddress") or {}
    children_raw = ((await _get(f"{_GLEIF_API}/lei-records/{lei}/direct-children",
                                {"page[size]": 100}, budget)) or {}).get("data") or [] if lei else []
    children = []
    for child in children_raw:
        c = ent(child)
        cname = str((c.get("legalName") or {}).get("name") or "").strip()
        if not cname or c.get("category") == "FUND" or _SKIP_CHILD.search(cname):
            continue
        children.append({"name": cname, "country": (c.get("headquartersAddress") or {}).get("country")})
    return {
        "legal_name": str((entity.get("legalName") or {}).get("name") or "").strip() or None,
        "hq_country": hq.get("country"), "hq_city": (hq.get("city") or "").title() or None,
        "children": children,
    }


def _merge_excludes(brand: str, hq_country: str | None, own_domains: set[str], *groups) -> list[dict[str, Any]]:
    """Offshoots in another country than the HQ, deduplicated by name. Only
    ones that carry the brand or read as a captive ("... Services", "...
    Technology Centre"): GLEIF lists every direct child, so HSBC's came back
    with Bermuda insurers, "Joy Group Limited" and Chinese-script names, which
    crowded the real service centres out of the cap and the alternatives. A
    domain the parent itself uses, or any generic (.com) domain, is never
    listed: excluding it would exclude the parent."""
    from app.services.company_email_cache import text_names_brand

    out: dict[str, dict[str, Any]] = {}
    for group in groups:
        for item in group:
            country = (item.get("country") or "").upper() or None
            if not item.get("name") or not country or country == hq_country:
                continue
            if not (text_names_brand(item["name"], brand) or _CAPTIVE_WORDS.search(item["name"])):
                continue
            domains = [d for d in item.get("domains") or [] if d not in own_domains and domain_country(d)]
            key = _norm(item["name"]) or item["name"].lower()
            if key in out:
                out[key]["domains"] = list(dict.fromkeys(out[key]["domains"] + domains))
                continue
            out[key] = _exclude_item(item["name"][:200], country, domains, _kind_for(item["name"]))
    return list(out.values())[:30]


async def _register_row(name: str) -> dict[str, Any] | None:
    from app.services.company_register import search_register

    try:
        page = await search_register(q=name, limit=3)
    except Exception as exc:
        logger.info("entity register lookup failed for %r: %s", name, exc)
        return None
    items = page.get("items") or []
    best = items[0] if items and items[0].get("match_level") is not None and items[0]["match_level"] <= 1 else None
    return best


# --- mail domain evidence -----------------------------------------------

async def _mx_ok(domain: str) -> bool:
    """Real mail servers. An implicit MX (the A record standing in, so the
    hosts are the domain itself) is a website, not a mail domain: that is
    how home.barclays would otherwise pass. A DNS failure is inconclusive
    and keeps the candidate."""
    from app.services.email_verifier import get_mx_cached

    try:
        ok, hosts = await get_mx_cached(domain, None)
    except Exception:
        return True
    if ok is None:
        return True
    return bool(ok) and bool(hosts) and [h.lower().rstrip(".") for h in hosts] != [domain]


async def _mx_all(domains: list[str], budget: _Budget) -> list[bool]:
    """MX for every candidate at once, inside what is left of the budget
    (half a second at least). A lookup still running then is inconclusive
    and keeps its candidate, like any other DNS failure."""
    if not domains:
        return []
    wait = max(budget.deadline - time.monotonic(), 0.5)
    tasks = [asyncio.ensure_future(_mx_ok(d)) for d in domains]
    await asyncio.wait(tasks, timeout=wait)
    out = []
    for task in tasks:
        if task.done() and not task.cancelled() and task.exception() is None:
            out.append(task.result())
        else:
            task.cancel()
            out.append(True)
    return out


async def _stored_domains(brand_name: str) -> set[str]:
    """Registrable domains the club already has verified patterns for, whose
    name label is this brand."""
    from app.database import get_db

    words = _brand_words(brand_name)
    if not words:
        return set()
    likes = {f"{''.join(words)}.%", f"{words[0]}.%", f"%.{''.join(words)}.%"}
    db = await get_db()
    try:
        clause = " OR ".join("LOWER(company_domain) LIKE ?" for _ in likes)
        rows = await (await db.execute(
            f"""SELECT company_domain FROM company_email_patterns
                WHERE verified_samples > 0 AND ({clause}) LIMIT 50""",
            tuple(likes),
        )).fetchall()
    finally:
        await db.close()
    return {d for d in (registrable_domain(r["company_domain"] or "") for r in rows)
            if d and _brand_label_ok(brand_name, d)}


async def _citations(brand_name: str, user_id: int, budget: _Budget) -> dict[str, int]:
    """"@domain" citations in ONE web search on the member's quota: how often
    each brand-labelled domain appears in a published address."""
    from app.services.web_fetch import web_search

    words = _brand_words(brand_name)
    if not words:
        return {}
    label = "".join(words)
    query = f'"@{label}." OR "{" ".join(words)}" email "@"'
    wait = max(budget.deadline - time.monotonic(), 0)
    if wait <= 0.2:
        return {}
    try:
        results = await asyncio.wait_for(web_search(query, max_results=8, user_id=user_id), timeout=wait)
    except Exception as exc:  # quota (429), outage or timeout: no citations
        logger.info("entity citation search skipped for %r: %s", brand_name, exc.__class__.__name__)
        budget.failed = True
        return {}
    counts: dict[str, int] = {}
    for item in results or []:
        text = f"{(item or {}).get('title') or ''} {(item or {}).get('content') or ''}"
        seen = {registrable_domain(m.group(1)) for m in _EMAIL_DOMAIN_RE.finditer(text)}
        for dom in seen:
            if dom and _brand_label_ok(brand_name, dom):
                counts[dom] = counts.get(dom, 0) + 1
    return counts


async def _pick_mail_domain(profile: dict[str, Any], websites: list[str], extra: list[str],
                            *, user_id: int | None, budget: _Budget) -> None:
    brand = profile["display_name"]
    try:
        stored = await _stored_domains(brand)
    except Exception as exc:
        logger.info("entity stored-pattern lookup failed: %s", exc)
        stored = set()
    candidates = list(dict.fromkeys(
        d for d in (registrable_domain(x) for x in [*websites, *extra, *sorted(stored)]) if d))
    # A website candidate need not carry the brand (Alphabet's abc.xyz), but a
    # stored or cited one must, or any @gmail.com would compete.
    mx = await _mx_all(candidates, budget)
    live = [d for d, ok in zip(candidates, mx) if ok]
    citations: dict[str, int] = {}
    # One live generic (.com) candidate is the answer already; the search
    # runs when there is a choice to make, nothing to choose from, or only a
    # country-coded domain (Wikidata lists barclays.co.uk, not barclays.com).
    settled = len(live) == 1 and not domain_country(live[0])
    if user_id is not None and not settled:
        citations = await _citations(brand, user_id, budget)
        new = [d for d in citations if d not in candidates]
        if new:
            new_mx = await _mx_all(new, budget)
            live += [d for d, ok in zip(new, new_mx) if ok]
    chosen, evidence = choose_mail_domain(
        live, brand, profile.get("target_country"), hq_country=profile.get("hq_country"),
        exclude=profile["exclude"], alt_mail_domains=profile["alt_mail_domains"],
        citations=citations, stored=stored,
    )
    profile["mail_domain"], profile["mail_domain_evidence"] = chosen, evidence
    # Other live country-coded domains of the brand are regional variants.
    known = {a["domain"] for a in profile["alt_mail_domains"]} | {d for e in profile["exclude"] for d in e["domains"]}
    for d in live:
        if d != chosen and d not in known and domain_country(d) and _brand_label_ok(brand, d):
            profile["alt_mail_domains"].append({"domain": d, "country": domain_country(d)})
    profile["alt_mail_domains"] = profile["alt_mail_domains"][:10]


def _apply_target(profile: dict[str, Any], target_country: str | None) -> dict[str, Any]:
    """An explicit target country picks that country's declared domain, and
    that country's offshoots stop being excluded: the member asked for them."""
    if not target_country:
        return profile
    profile["target_country"] = target_country
    hq = profile.get("hq_country")
    if target_country == "*" or target_country == hq:
        return profile
    candidates = [profile["mail_domain"], *(a["domain"] for a in profile["alt_mail_domains"]),
                  *(d for e in profile["exclude"] for d in e["domains"])]
    chosen, _ = choose_mail_domain(
        [c for c in candidates if c], profile["display_name"], target_country, hq_country=hq,
        exclude=profile["exclude"], alt_mail_domains=profile["alt_mail_domains"],
        curated=profile["mail_domain"],
    )
    if chosen and chosen != profile["mail_domain"]:
        profile["mail_domain"], profile["mail_domain_evidence"] = chosen, 0
    profile["exclude"] = [e for e in profile["exclude"] if e.get("country") != target_country]
    return profile


def _norm_target(target_country: str | None) -> str | None:
    t = (target_country or "").strip().upper()
    if t == "*":
        return "*"
    return t if re.fullmatch(r"[A-Z]{2}", t) else None


async def resolve_entity(name: str, *, target_country: str | None = None, user_id: int | None = None) -> dict[str, Any]:
    """The EntityProfile for a company name. Never raises: every failed source
    hands over to the next, and the heuristic always answers."""
    text = re.sub(r"\s+", " ", (name or "").strip())[:200]
    target = _norm_target(target_country)
    key = f"{_norm(text) or text.lower()}|{target or ''}"
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] <= _CACHE_TTL_S:
        return copy.deepcopy(hit[1])
    try:
        profile, complete = await _resolve(text, user_id=user_id)
    except Exception:
        logger.exception("entity resolution failed for %r; using the name as typed", text)
        profile, complete = heuristic_profile(text), False
    profile = _apply_target(profile, target)
    if complete:
        # A degraded answer (timeout, quota) is not kept for a week.
        if len(_cache) >= _CACHE_MAX:
            _cache.pop(next(iter(_cache)))
        _cache[key] = (time.monotonic(), copy.deepcopy(profile))
    return profile


async def _resolve(text: str, *, user_id: int | None) -> tuple[dict[str, Any], bool]:
    """(profile, whether every source answered)."""
    if not text:
        return heuristic_profile(text), False
    reviewed = override_profile(text)
    if reviewed:
        return reviewed, True
    budget = _Budget(_TOTAL_BUDGET_S)
    wiki = await _wikidata(text, budget)
    gleif = await _gleif((wiki or {}).get("leis") or [], (wiki or {}).get("label") or text, budget)
    websites: list[str] = []
    extra: list[str] = []
    if wiki or gleif:
        legal = (gleif or {}).get("legal_name") or (wiki or {}).get("label") or text
        display = (wiki or {}).get("label") or legal
        hq_country = ((gleif or {}).get("hq_country") or (wiki or {}).get("hq_country") or "").upper() or None
        profile = heuristic_profile(display)
        profile.update(legal_name=legal[:200], hq_country=hq_country,
                       hq_city=((gleif or {}).get("hq_city") or (wiki or {}).get("hq_city")),
                       source="wikidata" if wiki else "gleif")
        websites = list((wiki or {}).get("websites") or [])
        own = {registrable_domain(w) for w in websites}
        profile["exclude"] = _merge_excludes(display, hq_country, own, (wiki or {}).get("subs") or [],
                                             (gleif or {}).get("children") or [])
    else:
        row = await _register_row(text)
        if row:
            profile = heuristic_profile(str(row.get("company_name") or text))
            profile.update(hq_country=(row.get("country") or "").upper() or None, source="register")
            if row.get("company_domain"):
                extra.append(str(row["company_domain"]))
        else:
            profile = heuristic_profile(text)
    profile["target_country"] = profile["hq_country"]
    await _pick_mail_domain(profile, websites, extra, user_id=user_id, budget=budget)
    return profile, not budget.failed


# --- member input -------------------------------------------------------

def _s(value: Any, limit: int = 200) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _country(value: Any, *, star: bool = False) -> str | None:
    t = _s(value, 3).upper()
    if star and t == "*":
        return "*"
    return t if re.fullmatch(r"[A-Z]{2}", t) else None


def _host(value: Any) -> str | None:
    t = _s(value, 253).lower()
    return t if _HOST_RE.match(t) else None


def sanitize_entity(raw: Any) -> dict[str, Any] | None:
    """An EntityProfile sent back by a member's browser, cut to the contract
    and to sizes a run can carry. It is member input: it only ever narrows
    who a run keeps and which domain it tries, and nothing else reads it."""
    if not isinstance(raw, dict):
        return None
    alt = []
    for a in (raw.get("alt_mail_domains") or [])[:10] if isinstance(raw.get("alt_mail_domains"), list) else []:
        if isinstance(a, dict) and _host(a.get("domain")):
            alt.append({"domain": _host(a.get("domain")), "country": _country(a.get("country"))})
    exclude = []
    for e in (raw.get("exclude") or [])[:30] if isinstance(raw.get("exclude"), list) else []:
        if not isinstance(e, dict) or not _s(e.get("name")):
            continue
        domains = e.get("domains") if isinstance(e.get("domains"), list) else []
        exclude.append({
            "name": _s(e.get("name")), "country": _country(e.get("country")),
            "domains": [h for h in (_host(d) for d in domains[:5]) if h],
            "kind": e.get("kind") if e.get("kind") in KINDS else "subsidiary",
        })
    words = raw.get("brand_words") if isinstance(raw.get("brand_words"), list) else []
    try:
        evidence = max(0, min(int(raw.get("mail_domain_evidence") or 0), 100))
    except (TypeError, ValueError):
        evidence = 0
    out = {
        "legal_name": _s(raw.get("legal_name")), "display_name": _s(raw.get("display_name")),
        "brand_words": [w for w in (_s(x, 60).lower() for x in words[:10]) if w],
        "hq_country": _country(raw.get("hq_country")), "hq_city": _s(raw.get("hq_city"), 100) or None,
        "mail_domain": _host(raw.get("mail_domain")), "mail_domain_evidence": evidence,
        "alt_mail_domains": alt, "exclude": exclude,
        "target_country": _country(raw.get("target_country"), star=True),
        "source": raw.get("source") if raw.get("source") in SOURCES else "heuristic",
    }
    if not (out["legal_name"] or out["display_name"]):
        return None
    return out
