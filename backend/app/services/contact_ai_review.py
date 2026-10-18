"""
AI review of scraped contacts via Bedrock rank — flag real vs junk names, assess provenance.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from app.database import get_db
from app.services import llm
from app.services.batch_agents import run_batch_agents
from app.services.contact_scraper import is_heuristic_junk_contact

AI_REVIEW_BATCH = int(os.getenv("AI_REVIEW_BATCH", "5"))
AI_REVIEW_WORKERS = int(os.getenv("AI_REVIEW_WORKERS", "6"))
AI_REVIEW_ENABLED = os.getenv("AI_REVIEW_ENABLED", "true").lower() not in ("0", "false", "no")


def _contact_for_prompt(c: dict) -> dict[str, Any]:
    return {
        "email": (c.get("email") or "").strip().lower(),
        "name": c.get("name"),
        "title": c.get("title"),
        "contact_source": c.get("contact_source"),
        "source_url": c.get("source_url"),
        "linkedin_url": c.get("linkedin_url"),
        "discovery_context": (c.get("discovery_context") or "")[:400],
    }


def _build_review_prompt(batch: list[dict], company_name: str | None) -> str:
    company = company_name or "unknown company"
    payload = json.dumps([_contact_for_prompt(c) for c in batch], indent=2)
    return f"""You validate B2B outreach contacts scraped for {company}.

For EVERY contact below, output exactly one review with the same email field (lowercase).

Rules:
- verdict: "real" | "suspicious" | "junk"
- junk = nav labels (Gift Cards, Mac Studio), prose fragments, company names, not a person
- real = plausible human name with reasonable source
- suspicious = unclear or weak evidence

Return JSON only:
{{"reviews":[{{"email":"x@y.com","verdict":"real|suspicious|junk","name_plausible":true,"reason":"...","source_note":"..."}}]}}

Must include {len(batch)} review(s), one per input email.

Contacts:
{payload}"""


def _normalize_reviews(data: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not data or not isinstance(data.get("reviews"), list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in data["reviews"]:
        if not isinstance(row, dict):
            continue
        email = (row.get("email") or "").strip().lower()
        if email:
            out[email] = row
    return out


async def _review_batch(batch: list[dict], company_name: str | None) -> dict[str, dict[str, Any]]:
    if not batch:
        return {}
    from app.services.llm import complete_json, rank_model_id

    prompt = _build_review_prompt(batch, company_name)
    # Bedrock process inference slots are fewer than the review agents; back off
    # and retry rather than failing the whole discovery run. Exhausted retries
    # degrade to "unreviewed", never a lost contact.
    data: dict[str, Any] | None = None
    delay = 0.5
    for attempt in range(4):
        try:
            data = await asyncio.to_thread(complete_json, prompt, rank_model_id())
            break
        except Exception:
            data = None
            if attempt < 3:
                await asyncio.sleep(delay)
                delay *= 2
    result = _normalize_reviews(data)

    missing = [c for c in batch if (c.get("email") or "").strip().lower() not in result]
    if missing and len(missing) < len(batch):
        singles = await asyncio.gather(
            *[_review_batch([c], company_name) for c in missing],
        )
        for part in singles:
            result.update(part)
    elif missing and len(missing) == len(batch) and len(batch) > 1:
        mid = len(batch) // 2
        left, right = await asyncio.gather(
            _review_batch(batch[:mid], company_name),
            _review_batch(batch[mid:], company_name),
        )
        result = {**left, **right}

    return result


def _merge_review_maps(parts: list[dict[str, dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for part in parts:
        merged.update(part)
    return merged


async def run_ai_review_agents(
    contacts: list[dict],
    *,
    company_name: str | None = None,
    on_progress: Any = None,
) -> dict[str, dict[str, Any]]:
    """Multi-agent parallel review — returns email -> review map."""
    if not contacts:
        return {}

    reviews: dict[str, dict[str, Any]] = {}
    to_review: list[dict] = []
    for c in contacts:
        email = (c.get("email") or "").strip().lower()
        if not email:
            continue
        junk, reason = is_heuristic_junk_contact(c, company_name)
        if junk:
            reviews[email] = {
                "email": email,
                "verdict": "junk",
                "reason": reason,
                "source_note": "heuristic pre-filter",
                "name_plausible": False,
            }
        else:
            to_review.append(c)

    if not to_review or not AI_REVIEW_ENABLED:
        return reviews

    ranked = await run_batch_agents(
        to_review,
        batch_size=AI_REVIEW_BATCH,
        # More reviewers than model slots only queue behind each other: six
        # workers on two slots had 48 of 58 calls refused in one Barclays run.
        agents=min(AI_REVIEW_WORKERS, llm.INFERENCE_SLOTS),
        worker=lambda batch: _review_batch(batch, company_name),
        merge=_merge_review_maps,
        on_progress=on_progress,
    )
    reviews.update(ranked)
    return reviews


async def ai_review_contacts(
    contacts: list[dict],
    *,
    company_name: str | None = None,
    on_progress: Any = None,
) -> tuple[list[dict], list[dict]]:
    if not contacts:
        return [], []

    if not AI_REVIEW_ENABLED:
        log = [_log_row(c, verdict="unreviewed", reason="AI review disabled", model=None) for c in contacts]
        return [dict(c) for c in contacts], log

    reviews = await run_ai_review_agents(
        contacts,
        company_name=company_name,
        on_progress=on_progress,
    )

    reviewed: list[dict] = []
    log: list[dict] = []
    for c in contacts:
        row = dict(c)
        email = (row.get("email") or "").strip().lower()
        rev = reviews.get(email)
        if rev:
            verdict = str(rev.get("verdict") or "suspicious").lower()
            if verdict not in ("real", "suspicious", "junk"):
                verdict = "suspicious"
            row["ai_verdict"] = verdict
            row["ai_reason"] = str(rev.get("reason") or "")[:500]
            row["ai_source_note"] = str(rev.get("source_note") or "")[:500]
            row["ai_name_plausible"] = bool(rev.get("name_plausible"))
            log.append(
                _log_row(
                    row,
                    verdict=verdict,
                    reason=row["ai_reason"],
                    model=_review_model(),
                    source_note=row["ai_source_note"],
                )
            )
        else:
            row["ai_verdict"] = "unreviewed"
            row["ai_reason"] = "Review model did not return a result for this email"
            log.append(_log_row(row, verdict="unreviewed", reason=row["ai_reason"], model=_review_model()))
        reviewed.append(row)

    return reviewed, log


def _log_row(
    c: dict,
    *,
    verdict: str,
    reason: str,
    model: str | None,
    source_note: str = "",
) -> dict[str, Any]:
    return {
        "email": c.get("email"),
        "name": c.get("name"),
        "title": c.get("title"),
        "company": c.get("company"),
        "contact_source": c.get("contact_source"),
        "source_url": c.get("source_url"),
        "linkedin_url": c.get("linkedin_url"),
        "discovery_context": (c.get("discovery_context") or "")[:800],
        "ai_verdict": verdict,
        "ai_reason": reason,
        "ai_source_note": source_note,
        "ai_model": model,
        "email_verification_status": c.get("email_verification_status"),
        "confidence": c.get("confidence"),
    }


async def persist_discovery_log(scrape_run_id: str, entries: list[dict[str, Any]]) -> None:
    if not scrape_run_id or not entries:
        return
    db = await get_db()
    try:
        for e in entries:
            await db.execute(
                """INSERT INTO contact_discovery_logs
                   (scrape_run_id, email, name, title, company, contact_source, source_url,
                    linkedin_url, discovery_context, ai_verdict, ai_reason, ai_source_note, ai_model,
                    email_verification_status, confidence)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    scrape_run_id,
                    e.get("email"),
                    e.get("name"),
                    e.get("title"),
                    e.get("company"),
                    e.get("contact_source"),
                    e.get("source_url"),
                    e.get("linkedin_url"),
                    e.get("discovery_context"),
                    e.get("ai_verdict"),
                    e.get("ai_reason"),
                    e.get("ai_source_note"),
                    e.get("ai_model"),
                    e.get("email_verification_status"),
                    e.get("confidence"),
                ),
            )
        await db.commit()
    finally:
        await db.close()


async def get_discovery_log(scrape_run_id: str, limit: int = 500) -> list[dict[str, Any]]:
    db = await get_db()
    try:
        cur = await db.execute(
            """SELECT * FROM contact_discovery_logs
               WHERE scrape_run_id = ? ORDER BY id ASC LIMIT ?""",
            (scrape_run_id, limit),
        )
        return [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()


def _review_model() -> str:
    from app.services.llm import rank_model_id

    return rank_model_id()


async def check_review_backend() -> tuple[bool, str]:
    from app.services.llm import default_model_id, rank_model_id

    return True, f"bedrock rank={rank_model_id()} draft={default_model_id()}"
