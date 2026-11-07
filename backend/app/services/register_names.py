"""Public names, entity keys and hide rules for the company register.

The register keeps one row per government record under the legal name as
filed: "Apple Inc.", "Apple Operations LLC", "Boy Scouts Of America" once per
state. Members work with companies, so every row also carries three derived
facts, computed here and nowhere else:

  display_name  the public name: legal suffixes dropped, casing repaired
                ("Apple (Uk) Limited" -> "Apple (UK)"), a reviewed override
                from company_entity applied ("PricewaterhouseCoopers" -> "PwC")
  entity_key    "<country>:<normalised public name>". Rows sharing it are one
                company, whatever tier, source or state they were filed in.
  hidden_reason why the row is left out of browsing (an investment fund, an
                acquisition vehicle, a nominee/trustee shell, an IRS group
                return), or None. The row stays stored and searchable.

``derive`` is the single entry point; every ingest path reaches it through
``company_register.upsert_companies`` and the backfill job calls it for rows
stored before it existed. Bump DERIVATION_VERSION whenever any rule here
changes, and the backfill re-derives every row that was derived under an older
version.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

DERIVATION_VERSION = 1

# Legal forms that name the kind of entity, not the company. Compared on the
# lowercase letters of a word, so "L.L.C." and "LLC" are both "llc".
_LEGAL_SUFFIXES = frozenset({
    "inc", "incorporated", "corp", "corporation", "co", "company",
    "llc", "ltd", "limited", "lp", "llp", "plc", "pllc", "pc",
})

# Lowercased inside a name ("Bank of America"), never at its start.
_SMALL_WORDS = frozenset({"of", "the", "and", "for", "in", "by", "with"})

# Real acronyms, on their letters only. A name stored title-cased
# ("Ibew Local 134", "Apple (Uk)") has lost the capitals, so these are put
# back; in an ALL-CAPS name they are the tokens kept as they are.
_ACRONYMS = frozenset({
    "uk", "usa", "us", "nyc", "ibew", "ymca", "ywca", "naacp", "aarp", "nfl", "nba", "nhl",
    "mlb", "ncaa", "ai", "ibm", "hbo", "cvs", "ups", "bp", "ge", "gm", "hp", "hsbc", "ubs",
    "bmw", "amc", "nbc", "cbs", "cnn", "espn", "usaa", "pnc", "td", "aig", "dhl", "nrg",
    "kkr", "tpg", "bbva", "pwc", "kpmg", "ey", "sap", "afl", "cio", "naacp", "seiu", "afscme",
})

# EDGAR conformed names carry the state of incorporation: "FOO INC /DE/".
_STATE_TAG = re.compile(r"\s*[/\\][A-Za-z]{2,5}[/\\]\s*$")


@dataclass(frozen=True)
class Derived:
    display_name: str
    entity_key: str
    hidden_reason: str | None


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _key_words(name: str) -> list[str]:
    """Lowercase alphanumeric words. Periods and apostrophes join letters
    ("L.L.C." -> "llc", "Macy's" -> "macys"); every other punctuation mark
    separates them; "&" reads as "and" so "Procter & Gamble" and "Procter and
    Gamble" agree."""
    text = _STATE_TAG.sub("", _fold(name or "")).lower().replace("&", " and ")
    text = re.sub(r"[.'\u2019`]", "", text)
    return re.findall(r"[^\W_]+", text)


def _strip_words(words: list[str]) -> list[str]:
    """Drop a leading "the" and trailing legal forms ("co inc" both go) and a
    dangling "and" ("Smith & Co" -> "smith"). A name made only of suffixes
    keeps its last word rather than becoming empty."""
    words = list(words)
    if len(words) > 1 and words[0] == "the":
        words = words[1:]
    changed = True
    while changed and len(words) > 1:
        changed = False
        if words[-1] in _LEGAL_SUFFIXES or words[-1] == "and":
            words.pop()
            changed = True
            continue
        # "L L C" / "L. P." written with spaces.
        for k in (3, 2):
            if len(words) > k and all(len(w) == 1 for w in words[-k:]) and "".join(words[-k:]) in _LEGAL_SUFFIXES:
                del words[-k:]
                changed = True
                break
    return words


# --- public name --------------------------------------------------------------

def _letters(token: str) -> str:
    return re.sub(r"[^a-z]", "", token.lower())


def _title_token(core: str) -> str:
    """Title-case one ALL-CAPS token, keeping internal punctuation:
    "COCA-COLA" -> "Coca-Cola", "MACY'S" -> "Macy's", "O'BRIEN" -> "O'Brien"."""
    def cap(match: re.Match) -> str:
        word = match.group(0)
        prev = core[match.start() - 1] if match.start() else ""
        if prev in ("'", "\u2019") and len(word) == 1 and match.start() > 2:
            return word.lower()
        return word.capitalize()

    return re.sub(r"[A-Za-z]+", cap, core)


def _case_token(token: str, *, first: bool, shouty: bool) -> str:
    match = re.match(r"^([^\w&]*)(.*?)([^\w&]*)$", token)
    lead, core, trail = match.groups() if match else ("", token, "")
    if not core:
        return token
    letters = _letters(core)
    low = core.lower()
    if letters in _ACRONYMS:
        return lead + core.upper() + trail
    if not first and low in _SMALL_WORDS:
        return lead + low + trail
    if re.fullmatch(r"[A-Za-z]{1,3}&[A-Za-z]{1,3}", core):      # AT&T, P&G
        return lead + core.upper() + trail
    if not shouty:
        return token
    if re.fullmatch(r"\d+(?:ST|ND|RD|TH)", core):
        return lead + low + trail
    if re.search(r"\d", core) or len(core) == 1:
        return lead + core + trail
    if re.fullmatch(r"(?:[A-Za-z]\.)+[A-Za-z]?\.?", core):         # J.P.
        return lead + core.upper() + trail
    if 2 <= len(letters) <= 5 and not re.search(r"[aeiouy]", letters):   # AFL, NRG
        return lead + core + trail
    return lead + _title_token(core) + trail


def public_name(name: str) -> str:
    """The name a member would say: legal suffixes dropped, casing repaired."""
    text = _STATE_TAG.sub("", re.sub(r"\s+", " ", (name or "").strip()))
    tokens = text.split(" ") if text else []
    if len(tokens) > 2 and tokens[0].lower() == "the":
        tokens = tokens[1:]          # "The Salvation Army"; "The Gap" keeps its article
    while len(tokens) > 1:
        last = tokens[-1].strip(",;:")
        if _letters(last) in _LEGAL_SUFFIXES or last in ("&", "and", "And", "AND", "-"):
            tokens.pop()
            continue
        for k in (3, 2):
            if len(tokens) > k and all(len(_letters(t)) == 1 for t in tokens[-k:]) \
                    and "".join(_letters(t) for t in tokens[-k:]) in _LEGAL_SUFFIXES:
                del tokens[-k:]
                break
        else:
            break
    if not tokens:
        return ""
    tokens[-1] = tokens[-1].rstrip(",;:")
    cased_source = " ".join(tokens)
    shouty = any(c.isalpha() for c in cased_source) and cased_source == cased_source.upper()
    out = [_case_token(t, first=i == 0, shouty=shouty) for i, t in enumerate(tokens)]
    return " ".join(w for w in out if w)


# --- reviewed overrides (company_entity) --------------------------------------

@lru_cache(maxsize=1)
def _curated_index() -> tuple[dict[str, tuple[str, str, bool, bool]], dict[str, tuple[str, str, bool, bool]]]:
    """(exact, loose): normalised spelling -> (public name, HQ country, worldwide,
    is_alias), read from company_entity's reviewed table so there is one alias list.

    A rename moves a record into the curated company's entry, so a spelling
    that also names unrelated companies must not rename. ``exact`` holds the
    curated legal name with its legal ending ("target corporation", so a small
    "Target Inc" keeps its own identity). ``loose`` holds suffix-stripped
    nicknames and display names of two or more words ("barclays bank"), plus,
    for the worldwide professional networks, distinctive one-word firm names of
    five letters or more ("pricewaterhousecoopers"). One-word nicknames such as
    "chase", "fidelity" or "citi", and the table's internal keys, never rename;
    suffix stripping already gives those companies their public name.
    """
    from app.services.company_entity import ENTITY_OVERRIDES

    exact: dict[str, tuple[str, str, bool, bool]] = {}
    loose: dict[str, tuple[str, str, bool, bool]] = {}
    for entry in ENTITY_OVERRIDES.values():
        worldwide = entry["target"] == "*"
        value = (entry["display_name"], entry["hq_country"], worldwide, False)
        legal = " ".join(_key_words(entry["legal_name"]))
        if legal:
            exact.setdefault(legal, value)
        for spelling, is_alias in [(entry["display_name"], False), *((a, True) for a in entry["aliases"])]:
            norm = " ".join(_strip_words(_key_words(spelling)))
            if norm and (" " in norm or (worldwide and len(norm) >= 5)):
                loose.setdefault(norm, (entry["display_name"], entry["hq_country"], worldwide, is_alias))
    return exact, loose


def _curated(name: str, country: str) -> str | None:
    exact, loose = _curated_index()
    hit = exact.get(" ".join(_key_words(name)))
    if hit:
        return hit[0]
    hit = loose.get(" ".join(_strip_words(_key_words(name))))
    if not hit:
        return None
    display, hq, worldwide, is_alias = hit
    # A nickname is the curated company in its home country, or anywhere for a
    # network of member firms (the Big Four).
    if is_alias and not worldwide and country != hq:
        return None
    return display


# --- hide rules ---------------------------------------------------------------

_ROMAN = r"[ivx]{1,5}"
_FUND_TYPE = (
    r"income|equity|bond|credit|hedge|index|mezzanine|buyout|feeder|master|strategy|"
    r"high yield|senior loan|multi strategy|private equity|dividend|tax credit"
)
_COLLECTIVE = (
    r"credit unions|affiliates|chapters|organizations|churches|congregations|parishes|"
    r"posts|lodges|units|members|subordinates|affiliated organizations"
)

# (reason, pattern) over the lowercase word string of the LEGAL name, suffixes
# included ("harbor capital fund ii lp"). Every rule needs a signal only an
# investment vehicle or an IRS collective has: a false hide drops a real
# organisation from browsing, a miss only leaves clutter, so a bare word like
# "Fund" or "Trustees" never hides on its own.
_HIDE_RULES: tuple[tuple[str, re.Pattern], ...] = (
    ("investment_fund", re.compile(rf"\bfunds? (?:no )?(?:{_ROMAN}|\d+[a-z]?)\b")),
    ("investment_fund", re.compile(rf"\b(?:{_FUND_TYPE}) funds?\b")),
    ("investment_fund", re.compile(r"\bfunds? (?:lp|llp|lllp|ltd|limited|plc|sicav)$")),
    ("acquisition_vehicle", re.compile(r"\b(?:bidco|holdco|topco|midco|finco|propco|newco)\b")),
    ("acquisition_vehicle", re.compile(r"\bmerger sub\b|\bspv\b|\bco invest(?:ment)?\b")),
    ("acquisition_vehicle", re.compile(
        rf"\bacquisition (?:corp|corporation|co|company|inc|llc|ltd|limited|holdings)(?: (?:{_ROMAN}|\d+))?$")),
    ("nominee_or_trustee", re.compile(r"\bnominees\b")),
    ("nominee_or_trustee", re.compile(
        r"\btrustees?(?: (?:company|co|corporation|corp|limited|ltd|plc|llc|inc|services))+$")),
    ("group_return", re.compile(r"\bchartered (?:in|under the laws of) the state of\b")),
    ("group_return", re.compile(rf"^(?:all |the )?(?:{_COLLECTIVE})\b.*\bin the state of\b")),
    ("group_return", re.compile(r"\bgroup (?:return|ruling|exemption)\b")),
)


def hidden_reason(name: str) -> str | None:
    """Why a legal name is not worth browsing, or None.

    investment_fund, acquisition_vehicle, nominee_or_trustee, group_return.
    "Fund for the Public Interest", "Children's Defense Fund" and "Trustees of
    Boston College" are operating organisations and stay visible."""
    words = " ".join(_key_words(name))
    for reason, pattern in _HIDE_RULES:
        if pattern.search(words):
            return reason
    return None


# --- the one entry point ------------------------------------------------------

def derive(name: str, country: str | None = "US") -> Derived:
    country = (country or "US").strip().upper() or "US"
    curated = _curated(name, country)
    display = curated or public_name(name) or (name or "").strip()
    words = _strip_words(_key_words(display)) or _key_words(name) or [(name or "").strip().lower()]
    return Derived(
        display_name=display,
        entity_key=f"{country}:{' '.join(words)}",
        hidden_reason=None if curated else hidden_reason(name),
    )
