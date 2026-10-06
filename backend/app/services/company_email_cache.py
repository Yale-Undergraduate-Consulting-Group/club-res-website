"""
Per-company verified email pattern cache — learn from corroborated name+email pairs.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

from app.database import get_db
from app.services.contact_scraper import (
    _apply_custom_pattern,
    infer_email_from_name,
    normalize_domain,
    strict_email_name_alignment,
)


@dataclass
class ReconcileContext:
    """Preloaded DB + MX state for a batch reconcile (avoids per-contact DB round-trips)."""

    custom_patterns: list[str] = field(default_factory=list)
    domain_patterns: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    mx_cache: dict[str, tuple[bool, list[str]]] = field(default_factory=dict)
    mail_hosts: dict[str, list[str]] = field(default_factory=dict)


async def load_reconcile_context(domains: set[str]) -> ReconcileContext:
    from app.services.mail_domain_map import list_mail_hosts

    ctx = ReconcileContext()
    db = await get_db()
    try:
        cur = await db.execute("SELECT pattern FROM custom_email_formats ORDER BY priority DESC")
        ctx.custom_patterns = [r["pattern"] for r in await cur.fetchall() if r.get("pattern")]
        wanted: set[str] = set()
        for raw in domains:
            site = normalize_domain(raw)
            if not site or site in ctx.mail_hosts:
                continue
            hosts = await list_mail_hosts(site, db=db)
            ctx.mail_hosts[site] = hosts
            wanted.update(hosts)
            wanted.add(site)
        for dom in wanted:
            if not dom or dom in ctx.domain_patterns:
                continue
            cur = await db.execute(
                """SELECT company_domain, company_name, pattern_key, pattern_template, confidence,
                          sample_count, verified_samples, sources_json, updated_at
                   FROM company_email_patterns WHERE company_domain = ? ORDER BY confidence DESC, verified_samples DESC""",
                (dom,),
            )
            rows = await cur.fetchall()
            out: list[dict[str, Any]] = []
            for r in rows:
                d = dict(r)
                try:
                    d["sources"] = json.loads(d.pop("sources_json") or "[]")
                except Exception:
                    d["sources"] = []
                out.append(d)
            ctx.domain_patterns[dom] = out
    finally:
        await db.close()
    return ctx


def _host_usable(host: str, ctx: ReconcileContext | None) -> bool:
    if not ctx:
        return True
    mx = ctx.mx_cache.get(host)
    if mx is None:
        return True
    return mx[0] is not False


def build_email_candidates(
    full_name: str,
    domain: str,
    ctx: ReconcileContext | None = None,
    custom_patterns: list[str] | None = None,
) -> list[str]:
    """Ranked work-mail guesses: exception mailbox hosts first, then first.last."""
    from app.services.mail_domain_map import preferred_mail_hosts_sync

    site = normalize_domain(domain)
    if not site or not full_name:
        return []
    first, last = _split_name(full_name)
    if not last:
        return []
    hosts = list((ctx.mail_hosts.get(site) if ctx else None) or preferred_mail_hosts_sync(site) or [site])
    if site not in hosts:
        hosts.append(site)
    custom = custom_patterns if custom_patterns is not None else (ctx.custom_patterns if ctx else [])
    seen: set[str] = set()
    out: list[str] = []

    def _add(candidate: str) -> None:
        email = (candidate or "").strip().lower()
        if not email or email in seen:
            return
        if not strict_email_name_alignment(full_name, email):
            return
        seen.add(email)
        out.append(email)

    for host in hosts:
        if not host or not _host_usable(host, ctx):
            continue
        for p in (ctx.domain_patterns.get(host) if ctx else None) or []:
            tpl = p.get("pattern_template") or ""
            if not tpl:
                continue
            local = _apply_custom_pattern(tpl, first.lower(), last.lower())
            if local and "@" not in local:
                _add(f"{local}@{host}")
        for pattern in custom or []:
            try:
                local = _apply_custom_pattern(pattern, first.lower(), last.lower())
            except Exception:
                continue
            if local and "@" not in local:
                _add(f"{local}@{host}")
        fallback = infer_email_from_name(full_name, host, None)
        if fallback:
            _add(fallback)
    return out


def build_email_for_person_sync(
    full_name: str,
    domain: str,
    ctx: ReconcileContext | None = None,
    custom_patterns: list[str] | None = None,
) -> str | None:
    """Build email using cached company patterns (no I/O when ctx is provided)."""
    candidates = build_email_candidates(full_name, domain, ctx, custom_patterns=custom_patterns)
    return candidates[0] if candidates else None


async def build_email_for_person(
    full_name: str,
    domain: str,
    ctx: ReconcileContext | None = None,
) -> str | None:
    """Build email using cached company patterns, then global defaults."""
    if ctx is not None:
        return build_email_for_person_sync(full_name, domain, ctx)
    loaded = await load_reconcile_context({normalize_domain(domain)})
    return build_email_for_person_sync(full_name, domain, loaded)




def _split_name(full_name: str) -> tuple[str, str]:
    parts = [p for p in re.split(r"[\s,]+", (full_name or "").strip()) if p]
    if len(parts) >= 2:
        return parts[0], parts[-1]
    return (parts[0], "") if parts else ("", "")


# One table drives all three uses of a layout: recognising it in an observed
# pair, rendering it into a guess, and crediting a send outcome to it. Adding
# a layout here therefore teaches the learner, the generator and the feedback
# loop at once - they previously shared a six-entry vocabulary, so any company
# outside it could never learn a pattern no matter how many real addresses we
# saw. Mapped in order: the first exact match wins, so separator-bearing
# layouts are listed before the bare concatenations they could shadow.
_PATTERN_LAYOUTS: tuple[tuple[str, str], ...] = (
    ("first.last", "{first}.{last}"),
    ("first_last", "{first}_{last}"),
    ("fi.last", "{first_initial}.{last}"),
    ("fi_last", "{first_initial}_{last}"),
    ("last.first", "{last}.{first}"),
    ("last_first", "{last}_{first}"),
    ("firstlast", "{first}{last}"),
    ("flast", "{first_initial}{last}"),
    ("lastfi", "{last}{first_initial}"),
    ("firstli", "{first}{last_initial}"),
    ("first", "{first}"),
)


def _render_layout(template: str, first: str, last: str) -> str:
    fi = first[:1]
    li = last[:1]
    return (
        template.replace("{first_initial}", fi)
        .replace("{last_initial}", li)
        .replace("{first}", first)
        .replace("{last}", last)
    )


def infer_pattern_from_pair(email: str, first: str, last: str) -> tuple[str, str] | None:
    """Return (pattern_key, template) e.g. ('first.last', '{first}.{last}')."""
    if not email or "@" not in email or not last:
        return None
    local = email.split("@")[0].lower()
    f = first.lower()
    l = last.lower()
    for key, template in _PATTERN_LAYOUTS:
        if local == _render_layout(template, f, l):
            return key, template
    return None


# The learner emits these keys from observed pairs. A member-stated format has
# to canonicalise to the same key, or the identical layout is stored twice and
# neither row accumulates evidence.
_CANONICAL_TEMPLATES: dict[str, str] = {template: key for key, template in _PATTERN_LAYOUTS}


def canonical_pattern(template: str) -> tuple[str, str]:
    """Normalise a stated format to (pattern_key, template).

    Accepts the placeholder form "{first}.{last}" and the shorthand
    "first.last" that the admin format list already uses.
    """
    tpl = (template or "").strip()
    if not tpl:
        raise ValueError("An email format is required")
    if "{" not in tpl:
        shorthand = {key: canonical for canonical, key in _CANONICAL_TEMPLATES.items()}
        match = shorthand.get(tpl.lower())
        if not match:
            raise ValueError("Use a format such as {first}.{last} or first.last")
        tpl = match
    known = _CANONICAL_TEMPLATES.get(tpl)
    if known:
        return known, tpl
    if not any(token in tpl for token in ("{first}", "{last}", "{first_initial}")):
        raise ValueError("Use a format such as {first}.{last} or {first_initial}{last}")
    probe = _apply_custom_pattern(tpl, "jane", "doe")
    if not probe or "@" in probe or len(probe) > 64:
        raise ValueError("That format does not produce a usable mailbox name")
    derived = (
        tpl.replace("{first_initial}", "fi")
        .replace("{first}", "first")
        .replace("{last}", "last")
    )
    return derived[:60], tpl


def _mail_host(email: str) -> str:
    if not email or "@" not in email:
        return ""
    return normalize_domain(email.rsplit("@", 1)[-1])


async def _upsert_pattern(
    db,
    *,
    mail_host: str,
    pattern_key: str,
    template: str,
    source: str,
    company_name: str | None,
    inferred: bool,
) -> None:
    cur = await db.execute(
        "SELECT id, sample_count, verified_samples, sources_json FROM company_email_patterns WHERE company_domain = ? AND pattern_key = ?",
        (mail_host, pattern_key),
    )
    row = await cur.fetchone()
    sources: list[str] = []
    if row:
        try:
            sources = json.loads(row["sources_json"] or "[]")
        except Exception:
            sources = []
        if source not in sources:
            sources.append(source)
        sample_count = int(row["sample_count"] or 0) + 1
        verified_samples = int(row["verified_samples"] or 0) + (0 if inferred else 1)
        confidence = min(0.98, 0.35 + verified_samples * 0.12 + sample_count * 0.03)
        await db.execute(
            """UPDATE company_email_patterns SET
               pattern_template = ?, sample_count = ?, verified_samples = ?,
               confidence = ?, sources_json = ?, company_name = COALESCE(?, company_name),
               updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (template, sample_count, verified_samples, confidence, json.dumps(sources[-20:]), company_name, row["id"]),
        )
    else:
        sources = [source]
        confidence = 0.45 if inferred else 0.62
        await db.execute(
            """INSERT INTO company_email_patterns
               (company_domain, company_name, pattern_key, pattern_template, confidence, sample_count, verified_samples, sources_json)
               VALUES (?, ?, ?, ?, ?, 1, ?, ?)""",
            (mail_host, company_name, pattern_key, template, confidence, 0 if inferred else 1, json.dumps(sources)),
        )


async def record_verified_sample(
    domain: str,
    email: str,
    full_name: str,
    *,
    source: str = "scrape",
    company_name: str | None = None,
    inferred: bool = False,
) -> None:
    website = normalize_domain(domain)
    mail = _mail_host(email)
    if not mail or not email or not full_name:
        return
    first, last = _split_name(full_name)
    if not last or not strict_email_name_alignment(full_name, email):
        return
    pair = infer_pattern_from_pair(email, first, last)
    if not pair:
        return
    pattern_key, template = pair
    db = await get_db()
    try:
        await _upsert_pattern(
            db,
            mail_host=mail,
            pattern_key=pattern_key,
            template=template,
            source=source,
            company_name=company_name,
            inferred=inferred,
        )
        if website and website != mail:
            from app.services.mail_domain_map import record_observed_alias

            await record_observed_alias(website, mail, source=source, company_name=company_name, db=db)
        await db.commit()
    finally:
        await db.close()


async def batch_record_verified_samples(samples: list[dict[str, Any]]) -> None:
    """Persist learned patterns from a reconcile batch in one DB connection."""
    if not samples:
        return
    from app.services.mail_domain_map import record_observed_alias

    db = await get_db()
    try:
        for sample in samples:
            website = normalize_domain(sample.get("domain") or "")
            email = sample.get("email") or ""
            full_name = sample.get("full_name") or ""
            source = sample.get("source") or "scrape"
            company_name = sample.get("company_name")
            inferred = bool(sample.get("inferred"))
            mail = _mail_host(email)
            if not mail or not full_name:
                continue
            first, last = _split_name(full_name)
            if not last or not strict_email_name_alignment(full_name, email):
                continue
            pair = infer_pattern_from_pair(email, first, last)
            if not pair:
                continue
            pattern_key, template = pair
            await _upsert_pattern(
                db,
                mail_host=mail,
                pattern_key=pattern_key,
                template=template,
                source=source,
                company_name=company_name,
                inferred=inferred,
            )
            if website and website != mail:
                await record_observed_alias(website, mail, source=source, company_name=company_name, db=db)
        await db.commit()
    finally:
        await db.close()


async def get_domain_patterns(domain: str) -> list[dict[str, Any]]:
    from app.services.mail_domain_map import list_mail_hosts

    dom = normalize_domain(domain)
    if not dom:
        return []
    hosts = list(dict.fromkeys((await list_mail_hosts(dom)) or [dom]))
    placeholders = ",".join("?" * len(hosts))
    db = await get_db()
    try:
        cur = await db.execute(
            f"""SELECT company_domain, company_name, pattern_key, pattern_template, confidence,
                      sample_count, verified_samples, failed_samples, sources_json, updated_at
               FROM company_email_patterns WHERE company_domain IN ({placeholders})
               ORDER BY confidence DESC, verified_samples DESC""",
            hosts,
        )
        rows = await cur.fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["sources"] = json.loads(d.pop("sources_json") or "[]")
            except Exception:
                d["sources"] = []
            out.append(d)
        return out
    finally:
        await db.close()


# A hostname: dot-separated labels ending in an alphabetic TLD, no spaces.
# normalize_domain only strips URL syntax, so "Warner Bros. Discovery" came
# through it as the "domain" 'warner bros. discovery' just for holding a dot.
_HOSTNAME = re.compile(r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def is_hostname(text: str) -> bool:
    return bool(_HOSTNAME.match(text or ""))


async def resolve_company_domain(text: str, *, member_supplied: bool = True) -> str:
    """Best-effort mail domain for a free-text company reference.

    Text that is a real hostname is taken as the domain. A member who typed
    "meta.com" meant it, so that is the default; a caller passing a company
    NAME (member_supplied=False) only gets its dotted text back as a domain
    when the host accepts mail, the rule discover_company_domain applies to a
    search hit. Anything else is treated as a company *name* and matched
    against domains already on record. Returns "" when the domain is
    genuinely unknown, so callers can decline to guess instead of inventing a
    hostname ("Bain" must not become jane.doe@bain).
    """
    raw = (text or "").strip()
    if not raw:
        return ""
    dom = normalize_domain(raw)
    if is_hostname(dom):
        if member_supplied:
            return dom
        from app.services.email_verifier import get_mx_cached

        try:
            mx_ok, _ = await get_mx_cached(dom, None)
        except Exception:
            mx_ok = False
        if mx_ok:
            return dom
    from app.services.company_claims import company_key

    name = raw.lower()
    # Stored contacts count only when their company IS this company once legal
    # forms and punctuation are set aside. The old LIKE '%meta%' also took
    # "Metadata Systems" rows, so a short name could come back with another
    # company's domain. The LIKE below only narrows the rows to read.
    want = company_key(raw)
    # "barclays.%" matches barclays.com, barclays.co.uk and barclays.bank.in
    # alike, and the one with the most verified samples used to win, so a run
    # at the Indian service centre made barclays.bank.in the answer for
    # "Barclays". A lookup by the parent's name only takes a generic domain
    # or one coded for its HQ country, never an excluded offshoot's.
    from app.services.company_entity import parent_domain_rank

    db = await get_db()
    try:
        cur = await db.execute(
            """SELECT company_domain FROM company_email_patterns
               WHERE LOWER(company_name) = ? OR LOWER(company_domain) LIKE ?
               ORDER BY verified_samples DESC LIMIT 25""",
            (name, f"{name}.%"),
        )
        ranked = []
        for order, row in enumerate(await cur.fetchall()):
            rank = parent_domain_rank(raw, str(row["company_domain"] or ""))
            if row["company_domain"] and rank is not None:
                ranked.append((rank, order, str(row["company_domain"])))
        if ranked:
            return min(ranked)[2]
        first_word = (re.findall(r"[a-z0-9]+", name) or [name])[0]
        cur = await db.execute(
            """SELECT company, company_domain, COUNT(*) AS n FROM contacts
               WHERE company_domain IS NOT NULL AND company_domain != ''
                 AND LOWER(company) LIKE ?
               GROUP BY company, company_domain ORDER BY n DESC""",
            (f"%{first_word}%",),
        )
        counts: dict[str, int] = {}
        for r in await cur.fetchall():
            if company_key(r["company"]) == want:
                counts[str(r["company_domain"])] = counts.get(str(r["company_domain"]), 0) + int(r["n"])
        ranks = {d: parent_domain_rank(raw, d) for d in counts}
        usable = [d for d in counts if ranks[d] is not None]
        if usable:
            return min(usable, key=lambda d: (ranks[d], -counts[d]))
    finally:
        await db.close()
    return ""


async def discover_company_domain(name: str) -> str:
    """Find a company's real mail domain from its name alone.

    Tiered so the free tier answers most calls: what the club already knows,
    then one official-website web search whose result must both look like the
    company and resolve MX. Returns "" when nothing verifies.

    This replaces extract_domain_from_company, which concatenated the first
    three letters of the first two words ("Yale Undergraduate Consulting" ->
    "yalund.com"). That fabricated a hostname and then crawled and mail-checked
    against it, so a wrong domain looked like a company with no findable people.
    """
    company = (name or "").strip()
    if not company:
        return ""
    from app.services.company_entity import choose_mail_domain, override_profile, registrable_domain
    from app.services.roster_watch import _domain_matches_company, _registrable_domain

    # A reviewed entity answers first: Barclays' domains share one mail
    # tenant, so no search hit plus MX can tell barclays.com from
    # barclays.bank.in; the override table records which one the staff use.
    reviewed = override_profile(company)
    if reviewed and reviewed.get("mail_domain"):
        return reviewed["mail_domain"]
    known = await resolve_company_domain(company, member_supplied=False)
    # What the club has on record came from earlier runs, and an earlier run
    # with an unchecked guess could have stored a data vendor's host (Meta's
    # people saved at globaldata.com). A platform or vendor host is never a
    # company's mail domain, so it does not count as known.
    if known and _registrable_domain(known):
        return known
    from app.services.web_fetch import web_search_configured
    web_ok = web_search_configured() or bool((os.getenv("TAVILY_API_KEY") or "").strip())
    if not web_ok:
        return ""
    # Same evidence rule the roster resolver uses: the hit must look like the
    # company and accept mail, otherwise it is just a search result.
    from app.services.email_verifier import get_mx_cached
    from app.services.web_contact_discovery import _tavily_search

    try:
        results = await _tavily_search(f"{company} official website", max_results=5)
    except Exception:
        return ""
    # Every hit that is the company's and accepts mail is a candidate, and
    # choose_mail_domain ranks them: the first hit used to win, so a regional
    # site ranked first by the search engine (barclays.co.uk, a .bank.in)
    # became the mail domain for the whole group.
    candidates: list[str] = []
    for item in results or []:
        host = _registrable_domain(str((item or {}).get("url") or ""))
        candidate = registrable_domain(host) if host else ""
        if not candidate or candidate in candidates or not _domain_matches_company(company, candidate):
            continue
        try:
            mx_ok, _ = await get_mx_cached(candidate, None)
        except Exception:
            mx_ok = False
        if mx_ok:
            candidates.append(candidate)
    chosen, _ = choose_mail_domain(candidates, company, None)
    return chosen or ""


MEMBER_ASSERTED_CONFIDENCE = 0.80


async def list_all_domain_patterns(
    *, q: str | None = None, limit: int = 100, offset: int = 0
) -> dict[str, Any]:
    """Browse the institutional log of learned/asserted company formats.

    get_domain_patterns answers for one known domain. Rendering "every company
    whose format we know" needs this listing instead.
    """
    limit = max(1, min(int(limit or 100), 500))
    offset = max(0, int(offset or 0))
    where, params = "", []
    term = (q or "").strip().lower()
    if term:
        where = "WHERE lower(company_domain) LIKE ? OR lower(IFNULL(company_name,'')) LIKE ?"
        params = [f"%{term}%", f"%{term}%"]
    db = await get_db()
    try:
        total = (await (await db.execute(
            f"SELECT COUNT(*) AS n FROM company_email_patterns {where}", params
        )).fetchone())["n"]
        rows = await (await db.execute(
            f"""SELECT company_domain, company_name, pattern_key, pattern_template, confidence,
                       sample_count, verified_samples, failed_samples, sources_json, updated_at
                FROM company_email_patterns {where}
                ORDER BY verified_samples DESC, confidence DESC, company_domain
                LIMIT ? OFFSET ?""",
            [*params, limit, offset],
        )).fetchall()
        items = []
        for r in rows:
            d = dict(r)
            try:
                d["sources"] = json.loads(d.pop("sources_json") or "[]")
            except Exception:
                d["sources"] = []
            d["member_asserted"] = any(str(s).startswith("member:") for s in d["sources"])
            items.append(d)
        return {"items": items, "total": int(total), "limit": limit, "offset": offset}
    finally:
        await db.close()


async def set_member_asserted_pattern(
    domain: str,
    template: str,
    *,
    member_id: int,
    company_name: str | None = None,
) -> dict[str, Any]:
    """Record a format a member knows first-hand.

    A stated format is not an observed sample, so verified_samples is left
    alone and confidence sits below a corroborated pattern. Learned evidence
    therefore still outranks a human guess that turns out to be wrong.
    """
    dom = await resolve_company_domain(domain or "")
    if not dom:
        raw = (domain or "").strip()
        if not raw:
            raise ValueError("A company domain is required")
        # Writing a format against "bain" would key the shared registry to a
        # hostname that can never receive mail.
        raise ValueError(
            f"'{raw}' is not a mail domain and no domain is on record for it. "
            "Give the domain itself, e.g. bain.com"
        )
    pattern_key, tpl = canonical_pattern(template)
    source = f"member:{int(member_id)}"
    db = await get_db()
    try:
        row = await (await db.execute(
            "SELECT id, sources_json FROM company_email_patterns WHERE company_domain=? AND pattern_key=?",
            (dom, pattern_key),
        )).fetchone()
        if row:
            try:
                sources = json.loads(row["sources_json"] or "[]")
            except Exception:
                sources = []
            if source not in sources:
                sources.append(source)
            await db.execute(
                """UPDATE company_email_patterns
                   SET pattern_template=?, sources_json=?,
                       confidence=MAX(confidence, ?),
                       company_name=COALESCE(?, company_name),
                       updated_at=CURRENT_TIMESTAMP
                   WHERE id=?""",
                (tpl, json.dumps(sources[-20:]), MEMBER_ASSERTED_CONFIDENCE, company_name, row["id"]),
            )
        else:
            await db.execute(
                """INSERT INTO company_email_patterns
                   (company_domain, company_name, pattern_key, pattern_template,
                    confidence, sample_count, verified_samples, sources_json)
                   VALUES (?,?,?,?,?,0,0,?)""",
                (dom, company_name, pattern_key, tpl, MEMBER_ASSERTED_CONFIDENCE, json.dumps([source])),
            )
        await db.commit()
    finally:
        await db.close()
    return {"company_domain": dom, "pattern_key": pattern_key, "pattern_template": tpl}


async def record_send_outcome(
    db,
    *,
    email: str,
    full_name: str,
    delivered: bool,
    source: str,
) -> bool:
    """Feed a real send outcome back into the format it was derived from.

    A reply proves the mailbox exists, which is stronger than any crawl, so it
    counts as a verified sample. A permanent failure is evidence against the
    format, but only weak evidence: the person may simply have left. It is
    therefore recorded as a failure that discounts confidence rather than
    deleting a pattern that many other addresses still match.

    Returns False when the address does not match a known format for the
    domain, since an outcome then says nothing about any stored pattern.
    """
    mail = _mail_host(email)
    if not mail or not full_name:
        return False
    key = pattern_for_email(email, full_name)
    if not key:
        return False
    row = await (await db.execute(
        """SELECT id, sample_count, verified_samples, failed_samples, sources_json
           FROM company_email_patterns WHERE company_domain=? AND pattern_key=?""",
        (mail, key),
    )).fetchone()
    if not row:
        return False
    try:
        sources = json.loads(row["sources_json"] or "[]")
    except Exception:
        sources = []
    if source not in sources:
        sources.append(source)
    verified = int(row["verified_samples"] or 0) + (1 if delivered else 0)
    failed = int(row["failed_samples"] or 0) + (0 if delivered else 1)
    samples = int(row["sample_count"] or 0)
    # The learned curve alone would demote a member-asserted format on a
    # successful reply, since a stated format carries no observed samples. Take
    # the stronger of the two bases, then discount per observed failure. Derived
    # from stored counts, so the result is idempotent rather than drifting.
    asserted_floor = (
        MEMBER_ASSERTED_CONFIDENCE
        if any(str(s).startswith("member:") for s in sources)
        else 0.0
    )
    base = max(0.35 + verified * 0.12 + samples * 0.03, asserted_floor)
    confidence = min(0.98, base) - failed * 0.15
    await db.execute(
        """UPDATE company_email_patterns
           SET verified_samples=?, failed_samples=?, confidence=?, sources_json=?,
               updated_at=CURRENT_TIMESTAMP
           WHERE id=?""",
        (verified, failed, max(0.05, confidence), json.dumps(sources[-20:]), row["id"]),
    )
    return True


def pattern_for_email(email: str, full_name: str) -> str | None:
    first, last = _split_name(full_name)
    pair = infer_pattern_from_pair(email, first, last)
    return pair[0] if pair else None


# Suffixes a company legally carries but never puts in its domain. Matched
# against the last word(s) after stripping punctuation, so "Inc.", "Inc",
# and "Incorporated" all fall away the same way.
_LEGAL_SUFFIXES = frozenset({
    "inc", "incorporated", "corp", "corporation", "llc", "ltd", "limited",
    "plc", "co", "company", "group",
})

# What a company's name carries besides its brand: the legal form, and the
# descriptors a listing adds ("Platforms", "Holdings"). People write "Engineer
# at Meta", not "at Meta Platforms, Inc.", so matching on every word of the
# legal name rejected the evidence that was actually there.
_BRAND_SUFFIXES = _LEGAL_SUFFIXES | frozenset({
    "llp", "lp", "companies", "sa", "nv", "se", "ag", "gmbh", "bv", "ab", "asa",
    "oyj", "spa", "srl", "kk", "holdings", "holding", "platforms", "technologies",
    "international",
})
# Words too common to be a brand on their own. "American International Group"
# would otherwise shrink to "american" and match American Express, American
# Airlines and every page with the adjective, so a name is never cut down to
# one of these. ponytail: a hand list, so a generic word missing from it can
# still over-match; the upgrade is word frequencies instead of a list.
_GENERIC_BRAND_WORDS = frozenset({
    "american", "national", "general", "united", "first", "global", "new", "north",
    "south", "east", "west", "western", "eastern", "northern", "southern", "pacific",
    "atlantic", "royal", "standard", "universal", "federal", "central", "capital",
    "great", "world", "us", "usa", "china", "japan", "british", "european", "asia",
    "international", "bank", "energy", "health", "financial", "digital", "media",
})


def _fold_company_text(text: str) -> str:
    """Lowercase, accents folded ("Itaú" -> "itau"), and short letters joined by
    "&" kept as one word ("AT&T" -> "att"). Split, "AT&T" became the brand words
    "at" and "t", which every sentence contains."""
    import unicodedata

    folded = unicodedata.normalize("NFKD", (text or "").lower())
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return re.sub(r"\b([a-z0-9]{1,2})\s*&\s*([a-z0-9]{1,2})\b", r"\1\2", folded)


def company_brand_words(name: str) -> list[str]:
    """The words that identify a company, as people write it: "Meta Platforms,
    Inc." -> ["meta"], "The Walt Disney Company" -> ["walt", "disney"].

    Only trailing suffixes and a leading "The" are dropped, so a descriptor that
    IS the brand's first word survives ("International Paper" keeps both words,
    rather than shrinking to "paper" and matching every page about paper). When
    everything is a suffix ("Group Holdings Ltd") the full word list comes back
    instead of nothing, so a caller never matches on an empty brand.
    """
    cleaned = _fold_company_text(re.sub(r"\([^)]*\)", " ", name or ""))
    # "S.A." and "N.V." are one suffix each, not two stray letters.
    cleaned = re.sub(r"\b([a-z])\.([a-z])\b\.?", r"\1\2", cleaned)
    words = re.findall(r"[a-z0-9]+", cleaned)
    brand = list(words)
    if brand and brand[0] == "the":
        brand.pop(0)
    while brand and brand[-1] in _BRAND_SUFFIXES:
        if len(brand) == 2 and brand[0] in _GENERIC_BRAND_WORDS:
            break
        brand.pop()
    return brand or words


def text_names_brand(text: str, name: str) -> bool:
    """True when every brand word of ``name`` appears in ``text`` as a whole word.

    Whole words, because a substring test let "Meta" match "Metadata Engineer".
    The trade-off, chosen on purpose: a one-word brand still matches any page
    using that word, so "Apple Inc." accepts "Apple Bank for Savings". Rejecting
    it would need a list of every other company sharing a brand word; recall is
    preferred here because callers only treat this as one piece of evidence and
    keep their other gates (profile or company-site source, role wording, the
    title naming another employer).
    """
    brand = company_brand_words(name)
    if not brand:
        return False
    low = _fold_company_text(text)
    return all(re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", low) for w in brand)


def guess_domain_from_name(name: str) -> str | None:
    """Best-effort .com guess from a company's legal name.

    Pure and unverified: strips legal suffixes and punctuation, lowercases,
    and concatenates what is left. Never checked to actually resolve - that
    is the caller's job, so a guess never gets suggested as if it were fact.
    """
    cleaned = re.sub(r"[.,]", " ", (name or "")).strip()
    words = [w for w in cleaned.split() if w]
    while words and words[-1].lower() in _LEGAL_SUFFIXES:
        words.pop()
    if not words:
        return None
    stem = re.sub(r"[^a-z0-9]", "", "".join(words).lower())
    return f"{stem}.com" if stem else None


async def _head_ok(url: str) -> bool:
    """Live 2xx/3xx check, isolated so tests can mock the network without
    touching httpx itself."""
    import httpx

    async with httpx.AsyncClient(timeout=3.0, follow_redirects=True) as client:
        response = await client.head(url)
    return 200 <= response.status_code < 400


async def verify_domain_guess(name: str) -> dict[str, Any]:
    """Guess a domain from a company name and confirm it actually answers
    before handing it back. A member sees this as a suggestion to confirm
    or correct, never as something the search silently trusts on its own -
    so an unreachable guess comes back null rather than fabricated.
    """
    guess = guess_domain_from_name(name)
    if not guess:
        return {"domain": None, "verified": False}
    try:
        if await _head_ok(f"https://{guess}"):
            return {"domain": guess, "verified": True}
    except Exception:
        pass
    return {"domain": None, "verified": False}
