"""
Unified contact verification pipeline — identity → parallel inbox + AI agent pools.
Inbox verification and Bedrock name review run concurrently after local identity pass.
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
from app.services.contact_ai_review import (
    AI_REVIEW_ENABLED,
    _log_row,
    _review_model,
    run_ai_review_agents,
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

TRUSTED_SOURCES = frozenset({"domain_scrape"})
VERIFY_AGENTS = int(os.getenv("VERIFY_AGENTS", os.getenv("RECONCILE_WORKERS", "20")))

PipelineProgress = Callable[[str, int, int, str], Awaitable[None] | None]


def _pick_canonical_name(contact: dict, company_name: str | None) -> str | None:
    title_name = extract_person_name_from_title(contact.get("title"))
    li_name = name_from_linkedin_url(contact.get("linkedin_url"))
    scraped = (contact.get("name") or "").strip()
    for cand in (title_name, li_name, scraped):
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
        contact.get("_email_verified") or contact.get("contact_source") in TRUSTED_SOURCES
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


def _pattern_sample(
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


async def run_contact_verify_pipeline(
    contacts: list[dict],
    *,
    company_name: str | None = None,
    domain: str | None = None,
    on_progress: PipelineProgress | None = None,
) -> tuple[list[dict], list[dict]]:
    """
    Identity pass → parallel inbox agents + AI agents → merged verified contacts + discovery log.
    """
    if not contacts:
        return [], []

    async def _emit(phase: str, done: int, total: int, detail: str = "") -> None:
        if on_progress:
            maybe = on_progress(phase, done, total, detail)
            if maybe is not None:
                await maybe

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
    await preload_mx_for_domains(domains, ctx.mx_cache)

    staged: list[dict[str, Any]] = []
    dropped: list[dict] = []
    for c in contacts:
        co = c.get("company") or company_name
        resolved = _resolve_identity(c, company_name=co, domain=domain, ctx=ctx)
        if not resolved:
            dropped.append(dict(c))
            continue
        canonical, final_email, dom, rebuilt, trusted = resolved
        staged.append(
            {
                "raw": c,
                "canonical": canonical,
                "email": final_email,
                "domain": dom,
                "rebuilt": rebuilt,
                "trusted": trusted,
                "company": co,
            }
        )

    if not staged and not dropped:
        return [], []

    await _emit(
        "identity",
        len(staged),
        len(contacts),
        f"{len(staged)} passed identity gate · {len(dropped)} held for AI-only review",
    )

    pre_rows: list[dict] = []
    for s in staged:
        row = dict(s["raw"])
        row["name"] = s["canonical"]
        row["email"] = s["email"]
        row["company_domain"] = s["domain"]
        row.pop("_email_verified", None)
        pre_rows.append(row)

    verify_items = [
        {
            "email": s["email"],
            "full_name": s["canonical"],
            "smtp_probe": not (s["trusted"] and not s["rebuilt"]),
        }
        for s in staged
    ] if staged else []

    async def _verify_track() -> list[dict[str, Any]]:
        async def _tick(done: int, total: int) -> None:
            await _emit("verify", done, total, f"Inbox agent batch {done}/{total}")

        return await verify_emails_parallel(
            verify_items,
            workers=VERIFY_AGENTS,
            mx_cache=ctx.mx_cache,
            on_progress=_tick if verify_items else None,
        )

    async def _ai_track() -> dict[str, dict[str, Any]]:
        ai_inputs = list(pre_rows)
        if not AI_REVIEW_ENABLED or not ai_inputs:
            return {}

        async def _tick(done: int, total: int) -> None:
            await _emit("ai", done, total, f"AI review {done}/{total}")

        return await run_ai_review_agents(
            ai_inputs,
            company_name=company_name,
            on_progress=_tick,
        )

    verifications, ai_reviews = await asyncio.gather(_verify_track(), _ai_track())

    pattern_samples: list[dict[str, Any]] = []
    out_rows: list[dict] = []
    discovery_log: list[dict] = []
    seen: set[str] = set()

    for s, verification in zip(staged, verifications):
        if verification.get("status") == "invalid":
            continue
        em = s["email"].lower()
        if em in seen:
            continue
        seen.add(em)

        row = dict(s["raw"])
        row["name"] = s["canonical"]
        row["email"] = s["email"]
        row["company_domain"] = s["domain"]
        row["email_verification_status"] = verification.get("status", "unknown")
        row["email_pattern"] = verification.get("matched_pattern") or pattern_for_email(
            s["email"], s["canonical"]
        )
        row.pop("_email_verified", None)

        if s["rebuilt"]:
            src = (row.get("contact_source") or "inferred").replace("_pattern", "")
            row["contact_source"] = f"{src}_pattern" if src else "pattern_inferred"
            if row.get("confidence") == "high" and verification.get("status") not in (
                "valid",
                "likely_valid",
            ):
                row["confidence"] = "medium"

        rev = ai_reviews.get(em)
        if rev:
            verdict = str(rev.get("verdict") or "suspicious").lower()
            if verdict not in ("real", "suspicious", "junk", "unreviewed"):
                verdict = "suspicious"
            row["ai_verdict"] = verdict
            row["ai_reason"] = str(rev.get("reason") or "")[:500]
            row["ai_source_note"] = str(rev.get("source_note") or "")[:500]
            row["ai_name_plausible"] = bool(rev.get("name_plausible"))
            model = _review_model()
        elif not AI_REVIEW_ENABLED:
            row["ai_verdict"] = "unreviewed"
            row["ai_reason"] = "AI review disabled"
            row["ai_source_note"] = ""
            model = None
        else:
            row["ai_verdict"] = "unreviewed"
            row["ai_reason"] = "Review model unavailable or parse failed for this contact"
            row["ai_source_note"] = ""
            model = _review_model()

        discovery_log.append(
            _log_row(
                row,
                verdict=row["ai_verdict"],
                reason=row["ai_reason"],
                model=model,
                source_note=row.get("ai_source_note") or "",
            )
        )

        sample = _pattern_sample(
            s["domain"], s["email"], s["canonical"], s["raw"], s["company"], s["rebuilt"], verification
        )
        if sample:
            pattern_samples.append(sample)

        out_rows.append(row)

    for c in dropped:
        em = sanitize_email(c.get("email") or "").lower()
        if not em or em in seen:
            continue
        seen.add(em)
        row = dict(c)
        row["email"] = em
        row["email_verification_status"] = row.get("email_verification_status") or "unknown"
        rev = ai_reviews.get(em)
        if rev:
            verdict = str(rev.get("verdict") or "suspicious").lower()
            if verdict not in ("real", "suspicious", "junk", "unreviewed"):
                verdict = "suspicious"
            row["ai_verdict"] = verdict
            row["ai_reason"] = str(rev.get("reason") or "")[:500]
            row["ai_source_note"] = str(rev.get("source_note") or "")[:500]
            row["ai_name_plausible"] = bool(rev.get("name_plausible"))
            model = _review_model()
        elif not AI_REVIEW_ENABLED:
            row["ai_verdict"] = "unreviewed"
            row["ai_reason"] = "AI review disabled"
            row["ai_source_note"] = ""
            model = None
        else:
            row["ai_verdict"] = "unreviewed"
            row["ai_reason"] = "Review model unavailable or parse failed for this contact"
            row["ai_source_note"] = ""
            model = _review_model()
        discovery_log.append(
            _log_row(
                row,
                verdict=row["ai_verdict"],
                reason=row["ai_reason"],
                model=model,
                source_note=row.get("ai_source_note") or "",
            )
        )
        out_rows.append(row)

    if pattern_samples:
        await batch_record_verified_samples(pattern_samples)

    await _emit("done", len(out_rows), len(staged), "Verification complete")
    return out_rows, discovery_log
