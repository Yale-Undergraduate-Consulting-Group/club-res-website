"""
Resolve scraped contacts to canonical person identity (title / LinkedIn vs junk names).
Rebuild misaligned emails using per-company pattern cache.
Parallel batch reconcile with shared MX cache and worker pool.
"""
from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from typing import Any

from app.services.company_email_cache import (
    ReconcileContext,
    batch_record_verified_samples,
    build_email_for_person_sync,
    load_reconcile_context,
    pattern_for_email,
)
from app.services.contact_scraper import (
    extract_person_name_from_title,
    looks_like_person_name,
    name_from_linkedin_url,
    sanitize_email,
    strict_email_name_alignment,
)
from app.services.email_verifier import (
    VERIFY_AGENTS,
    preload_mx_for_domains,
    verify_emails_parallel,
)

RECONCILE_WORKERS = int(os.getenv("RECONCILE_WORKERS", "20"))
# Scraped-from-page emails with aligned local part skip slow SMTP (MX still required).
TRUSTED_SOURCES = frozenset({"domain_scrape"})


def _pick_canonical_name(contact: dict, company_name: str | None) -> str | None:
    title_name = extract_person_name_from_title(contact.get("title"))
    li_name = name_from_linkedin_url(contact.get("linkedin_url"))
    scraped = (contact.get("name") or "").strip()

    ordered: list[str | None] = [title_name, li_name, scraped]
    for cand in ordered:
        if cand and looks_like_person_name(cand, company_name):
            return cand.strip()
    return None


def _resolve_identity(
    contact: dict,
    *,
    company_name: str | None,
    domain: str | None,
    ctx: ReconcileContext,
) -> tuple[str, str, str, bool, bool] | None:
    """
    Local identity pass (no network). Returns canonical, final_email, dom, rebuilt, trusted_source.
    """
    dom = (domain or contact.get("company_domain") or "").strip().lower()
    if not dom:
        return None

    email = sanitize_email(contact.get("email") or "")
    if not email or "@" not in email:
        return None

    canonical = _pick_canonical_name(contact, company_name)
    if not canonical:
        return None

    trusted = bool(
        contact.get("_email_verified")
        or contact.get("contact_source") in TRUSTED_SOURCES
    )
    rebuilt = False

    if strict_email_name_alignment(canonical, email):
        final_email = email
    else:
        built = build_email_for_person_sync(canonical, dom, ctx)
        if not built or not strict_email_name_alignment(canonical, built):
            return None
        final_email = built
        rebuilt = True
        trusted = False

    return canonical, final_email, dom, rebuilt, trusted


async def reconcile_contact(
    contact: dict,
    *,
    company_name: str | None = None,
    domain: str | None = None,
    verify_deliverability: bool = True,
    ctx: ReconcileContext | None = None,
) -> dict | None:
    """
    Fix name/email mismatches (e.g. name 'been different' + title 'Lauren Anderholm - …').
    Returns None if the row cannot be tied to a real person inbox.
    """
    if ctx is None:
        dom_guess = (domain or contact.get("company_domain") or "").strip().lower()
        email = contact.get("email") or ""
        if "@" in email:
            dom_guess = dom_guess or email.split("@", 1)[1].lower()
        ctx = await load_reconcile_context({dom_guess} if dom_guess else set())

    resolved = _resolve_identity(contact, company_name=company_name, domain=domain, ctx=ctx)
    if not resolved:
        return None

    canonical, final_email, dom, rebuilt, trusted = resolved

    if verify_deliverability:
        skip_smtp = trusted and not rebuilt
        verifications = await verify_emails_parallel(
            [{"email": final_email, "full_name": canonical, "smtp_probe": not skip_smtp}],
            mx_cache=ctx.mx_cache,
            workers=1,
        )
        verification = verifications[0] if verifications else {"status": "unknown", "mx_valid": False}
    else:
        verification = {"status": "unknown", "mx_valid": False}

    if verification.get("status") == "invalid":
        return None

    out = dict(contact)
    out["name"] = canonical
    out["email"] = final_email
    out["company_domain"] = dom
    out["email_verification_status"] = verification.get("status", "unknown")
    out["email_pattern"] = verification.get("matched_pattern") or pattern_for_email(final_email, canonical)
    out.pop("_email_verified", None)

    if rebuilt:
        src = (out.get("contact_source") or "inferred").replace("_pattern", "")
        out["contact_source"] = f"{src}_pattern" if src else "pattern_inferred"
        if out.get("confidence") == "high" and verification.get("status") not in ("valid", "likely_valid"):
            out["confidence"] = "medium"

    out["_pattern_sample"] = _pattern_sample_payload(
        dom, final_email, canonical, contact, company_name, rebuilt, verification
    )
    return out


def _pattern_sample_payload(
    dom: str,
    email: str,
    canonical: str,
    contact: dict,
    company_name: str | None,
    rebuilt: bool,
    verification: dict[str, Any],
) -> dict[str, Any] | None:
    status = verification.get("status")
    mx_valid = verification.get("mx_valid")
    if status in ("valid", "likely_valid") and not rebuilt:
        return {
            "domain": dom,
            "email": email,
            "full_name": canonical,
            "source": contact.get("contact_source") or "scrape",
            "company_name": company_name,
            "inferred": False,
        }
    if rebuilt and mx_valid:
        return {
            "domain": dom,
            "email": email,
            "full_name": canonical,
            "source": "pattern_inferred",
            "company_name": company_name,
            "inferred": True,
        }
    return None


async def reconcile_contacts(
    contacts: list[dict],
    *,
    company_name: str | None = None,
    domain: str | None = None,
    verify_deliverability: bool = True,
    workers: int | None = None,
    on_progress: Callable[[int, int], Awaitable[None] | None] | None = None,
) -> list[dict]:
    if not contacts:
        return []

    pool = workers or RECONCILE_WORKERS
    domains: set[str] = set()
    if domain:
        domains.add(domain.strip().lower())
    for c in contacts:
        d = (c.get("company_domain") or "").strip().lower()
        if d:
            domains.add(d)
        em = c.get("email") or ""
        if "@" in em:
            domains.add(em.split("@", 1)[1].lower())

    ctx = await load_reconcile_context(domains)
    if verify_deliverability:
        await preload_mx_for_domains(domains, ctx.mx_cache)

    # Phase 1: local identity resolution (instant, no network)
    staged: list[tuple[dict, str, str, str, bool, bool, str | None]] = []
    for c in contacts:
        co = c.get("company") or company_name
        resolved = _resolve_identity(c, company_name=co, domain=domain, ctx=ctx)
        if resolved:
            canonical, final_email, dom, rebuilt, trusted = resolved
            staged.append((c, canonical, final_email, dom, rebuilt, trusted, co))

    if not staged:
        return []

    verify_items = [
        {
            "email": item[2],
            "full_name": item[1],
            "smtp_probe": not (item[4] and not item[3]),
        }
        for item in staged
    ]
    # item = (raw, canonical, final_email, rebuilt, trusted, co)

    verifications: list[dict[str, Any]] = []
    if verify_deliverability:
        async def _tick(done: int, total: int) -> None:
            if on_progress:
                maybe = on_progress(done, total)
                if maybe is not None:
                    await maybe

        verifications = await verify_emails_parallel(
            verify_items,
            workers=pool or VERIFY_AGENTS,
            mx_cache=ctx.mx_cache,
            on_progress=_tick,
        )
    else:
        verifications = [{"status": "unknown", "mx_valid": False} for _ in staged]

    pattern_samples: list[dict[str, Any]] = []
    results: list[dict | None] = []

    for item, verification in zip(staged, verifications):
        raw, canonical, final_email, dom, rebuilt, trusted, row_company = item
        if verify_deliverability and verification.get("status") == "invalid":
            results.append(None)
            continue

        out = dict(raw)
        out["name"] = canonical
        out["email"] = final_email
        out["company_domain"] = dom
        out["email_verification_status"] = verification.get("status", "unknown")
        out["email_pattern"] = verification.get("matched_pattern") or pattern_for_email(final_email, canonical)
        out.pop("_email_verified", None)

        if rebuilt:
            src = (out.get("contact_source") or "inferred").replace("_pattern", "")
            out["contact_source"] = f"{src}_pattern" if src else "pattern_inferred"
            if out.get("confidence") == "high" and verification.get("status") not in ("valid", "likely_valid"):
                out["confidence"] = "medium"

        sample = _pattern_sample_payload(dom, final_email, canonical, raw, row_company, rebuilt, verification)
        if sample:
            pattern_samples.append(sample)
        results.append(out)

    if pattern_samples:
        await batch_record_verified_samples(pattern_samples)

    reconciled: list[dict] = []
    seen: set[str] = set()
    for row in results:
        if not row:
            continue
        em = row["email"].lower()
        if em in seen:
            continue
        seen.add(em)
        reconciled.append(row)
    return reconciled
