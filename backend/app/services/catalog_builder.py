"""The catalog builder: the box working down the company register day and
night, running the same Find people search a member would on each company,
so the people at most companies a member opens are already waiting.

Order: SEC-listed companies in market-cap order (the first 500 stand in for
the S&P 500), then Companies House GROUP and then FULL accounts filers, then
everything else in the register's value order. A company crawled by anyone,
member or builder, within BUILDER_RECRAWL_DAYS is skipped.

Each company becomes an ordinary discovery run (origin='builder') owned by a
system account that cannot sign in. The run's own per-round verification and
AI review trim it exactly as they trim a member's run; the drain only takes
it when no member run is waiting; lease recovery covers it across restarts.
People land in yucgoutreach_prospects, never in contacts: a member still
keeps or imports them, and every member sees them on the Find page.

It spends shared allowances, so it only takes a new company while the club
is under BUILDER_MAX_SEARCHES_PER_HOUR / _PER_DAY real upstream searches and
under BUILDER_MAX_AI_SHARE of the hourly Bedrock budget, which every model
call (the builder's runs included) reserves through generation_policy.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import HTTPException

from app.database import get_db
from app.services.company_register import REGISTER_VALUE_ORDER

logger = logging.getLogger(__name__)

SYSTEM_EMAIL = "catalog-builder@system.invalid"
ENABLED_KEY = "builder_enabled"

# ponytail: the first 500 SEC-listed rows by market-cap order are not S&P 500
# membership, and the register carries no exchange (company_tickers.json has
# none), so "then Nasdaq" is the rest of SEC-listed by rank. Upgrade path:
# load an index constituents file or company_tickers_exchange.json into
# metadata and add those buckets here.
_TARGET_ORDER = (
    "tier = 'us_public' AND prominence_rank IS NOT NULL",
    # tier 'uk' is Companies House; tier is indexed, source is not.
    "tier = 'uk' AND json_extract(metadata_json, '$.account_category') = 'GROUP'",
    "tier = 'uk' AND json_extract(metadata_json, '$.account_category') = 'FULL'",
    "1 = 1",
)
# Companies looked at per tick whose resolved name turns out to be crawled
# already; bounds the resolver calls one tick can make.
_PICKS_PER_TICK = 5


def _env_number(name: str, default: float) -> float:
    try:
        return float(os.getenv(name) or default)
    except ValueError:
        return default


def _limits() -> dict[str, float]:
    return {
        "searches_per_hour": _env_number("BUILDER_MAX_SEARCHES_PER_HOUR", 250),
        "searches_per_day": _env_number("BUILDER_MAX_SEARCHES_PER_DAY", 6000),
        "ai_share": _env_number("BUILDER_MAX_AI_SHARE", 0.5),
        "recrawl_days": _env_number("BUILDER_RECRAWL_DAYS", 30),
        "target": _env_number("BUILDER_TARGET_PEOPLE", 60),
    }


async def builder_enabled() -> bool:
    from app.services.settings_service import get_setting

    return (await get_setting(ENABLED_KEY) or "") == "1"


async def set_builder_enabled(enabled: bool) -> None:
    from app.services.settings_service import set_setting

    await set_setting(ENABLED_KEY, "1" if enabled else "0")


async def ensure_system_user(db) -> int:
    """The account builder runs belong to. Role 'system' is refused by every
    session and API-key lookup (auth_deps), the address cannot pass Google
    sign-in (yale.edu only), and admin user management does not list it.
    It stays active because the drain only claims runs of active users."""
    await db.execute(
        "INSERT OR IGNORE INTO users (email, name, role, is_active) VALUES (?, 'Catalog builder', 'system', 1)",
        (SYSTEM_EMAIL,),
    )
    row = await (await db.execute("SELECT id FROM users WHERE email = ?", (SYSTEM_EMAIL,))).fetchone()
    return int(row["id"])


async def _usage() -> dict[str, float]:
    from app.services.generation_policy import bedrock_hour_share
    from app.services.web_fetch import search_usage_last_hour

    return {
        "searches_last_hour": await search_usage_last_hour(),
        "searches_last_day": await search_usage_last_hour(86400),
        "ai_share": await bedrock_hour_share(),
    }


def _pause_reason(usage: dict[str, float], limits: dict[str, float]) -> str | None:
    if usage["searches_last_hour"] >= limits["searches_per_hour"]:
        return (f"Search budget: {int(usage['searches_last_hour'])} of {int(limits['searches_per_hour'])}"
                " club searches used in the last hour")
    if usage["searches_last_day"] >= limits["searches_per_day"]:
        return (f"Daily search budget: {int(usage['searches_last_day'])} of {int(limits['searches_per_day'])}"
                " club searches used in the last 24 hours")
    if usage["ai_share"] > limits["ai_share"]:
        return (f"AI budget: {round(usage['ai_share'] * 100)}% of the club's hourly Bedrock allowance in use"
                f" (builder yields above {round(limits['ai_share'] * 100)}%)")
    return None


async def _sync_progress(db) -> None:
    """Copy each open builder company's run outcome into its progress row."""
    await db.execute(
        """UPDATE catalog_builder_companies SET
               status = COALESCE((SELECT r.status FROM yucgoutreach_discovery_runs r
                                  WHERE r.id = catalog_builder_companies.run_id), 'failed'),
               people_found = COALESCE((SELECT r.prospects_count FROM yucgoutreach_discovery_runs r
                                        WHERE r.id = catalog_builder_companies.run_id), 0),
               crawled_at = COALESCE((SELECT r.completed_at FROM yucgoutreach_discovery_runs r
                                      WHERE r.id = catalog_builder_companies.run_id), crawled_at)
           WHERE status IN ('queued', 'running')"""
    )


async def _active_run(db) -> dict[str, Any] | None:
    row = await (await db.execute(
        """SELECT id, company_name, status, progress_message FROM yucgoutreach_discovery_runs
           WHERE origin = 'builder' AND status IN ('queued', 'running') ORDER BY id DESC LIMIT 1"""
    )).fetchone()
    return dict(row) if row else None


async def _next_company(db, recrawl: str) -> dict[str, Any] | None:
    """The first register company in target order that nobody has crawled
    within the recrawl window, by the builder's own record or by any run
    under the same company key the Find page groups by."""
    for where in _TARGET_ORDER:
        row = await (await db.execute(
            f"""SELECT id, company_name, company_domain FROM company_register
                WHERE {where}
                  AND NOT EXISTS (SELECT 1 FROM catalog_builder_companies b
                                  WHERE b.register_id = company_register.id
                                    AND b.crawled_at >= datetime('now', ?))
                  AND NOT EXISTS (SELECT 1 FROM yucgoutreach_discovery_runs d
                                  WHERE lower(trim(d.company_name)) = lower(trim(company_register.company_name))
                                    AND d.created_at >= datetime('now', ?))
                ORDER BY {REGISTER_VALUE_ORDER} LIMIT 1""",
            (recrawl, recrawl),
        )).fetchone()
        if row:
            return dict(row)
    return None


async def _record(db, register_id: int, name: str, run_id: int | None, status: str) -> None:
    await db.execute(
        """INSERT INTO catalog_builder_companies (register_id, company_name, run_id, status, people_found, crawled_at)
           VALUES (?, ?, ?, ?, 0, CURRENT_TIMESTAMP)
           ON CONFLICT(register_id) DO UPDATE SET company_name = excluded.company_name,
               run_id = excluded.run_id, status = excluded.status, people_found = 0,
               crawled_at = CURRENT_TIMESTAMP""",
        (register_id, name, run_id, status),
    )


async def _resolve(name: str, user_id: int) -> tuple[str, str | None, dict[str, Any] | None]:
    """The same resolution a member's Find page asks for before searching,
    so the run's company name, domain and entity match what a member would
    get for this company. A 429 is a budget pause and propagates."""
    from app.services.company_resolve import resolve_company

    try:
        res = await resolve_company(name, user_id=user_id)
    except HTTPException as exc:
        if exc.status_code == 429:
            raise
        logger.warning("catalog builder: could not resolve %s: %s", name, exc.detail)
        return name, None, None
    except Exception:
        # The run resolves a domain itself when it has no profile.
        logger.exception("catalog builder: could not resolve %s", name)
        return name, None, None
    entity = res.get("entity")
    if isinstance(entity, dict):
        # As the Find page sends it: no country unless a member chose one.
        entity = {**entity, "target_country": None}
    return (res.get("name") or name), res.get("domain"), entity


async def _enqueue_next(limits: dict[str, float]) -> dict[str, Any]:
    from app.services.dispatch_service import begin_write
    from app.services.yucgoutreach_import import enqueue_discovery_run

    recrawl = f"-{int(limits['recrawl_days'])} days"
    for _ in range(_PICKS_PER_TICK):
        db = await get_db()
        try:
            user_id = await ensure_system_user(db)
            await db.commit()
            picked = await _next_company(db, recrawl)
        finally:
            await db.close()
        if not picked:
            return {"action": "exhausted"}
        try:
            name, domain, entity = await _resolve(picked["company_name"], user_id)
        except HTTPException as exc:
            return {"action": "paused", "reason": str(exc.detail)}
        db = await get_db()
        try:
            await begin_write(db)
            recent = await (await db.execute(
                """SELECT 1 FROM yucgoutreach_discovery_runs
                   WHERE lower(trim(company_name)) = lower(trim(?)) AND created_at >= datetime('now', ?)""",
                (name, recrawl),
            )).fetchone()
            if recent:
                # A member already searched this company under its resolved
                # name; note it so the next pick moves on.
                await _record(db, picked["id"], name, None, "skipped")
                await db.commit()
                continue
            try:
                run_id = await enqueue_discovery_run(
                    db,
                    user_id=user_id,
                    company_name=name,
                    company_domain=domain or picked.get("company_domain"),
                    title_hints=None,
                    max_prospects=int(limits["target"]),
                    entity=entity,
                    origin="builder",
                )
            except HTTPException as exc:
                # The club queue is full: members are busy, try next tick.
                await db.rollback()
                return {"action": "paused", "reason": str(exc.detail)}
            await _record(db, picked["id"], name, run_id, "queued")
            await db.commit()
        finally:
            await db.close()
        # ponytail: no immediate drain kick; the 10s discovery drain claims
        # it, and seconds do not matter to an unattended crawl.
        return {"action": "enqueued", "run_id": run_id, "company": name, "register_id": picked["id"]}
    return {"action": "skipped"}


async def builder_tick() -> dict[str, Any]:
    """One scheduler step: when on, idle and within budget, queue the next
    company. Safe to run anywhere at any time: the queue and progress are
    rows, so a restart resumes where it was."""
    if not await builder_enabled():
        return {"action": "disabled"}
    db = await get_db()
    try:
        await _sync_progress(db)
        await db.commit()
        if await _active_run(db):
            return {"action": "busy"}
    finally:
        await db.close()
    limits = _limits()
    try:
        reason = _pause_reason(await _usage(), limits)
    except Exception:
        logger.exception("catalog builder: usage could not be read; pausing")
        return {"action": "paused", "reason": "Usage could not be read"}
    if reason:
        return {"action": "paused", "reason": reason}
    return await _enqueue_next(limits)


async def builder_status() -> dict[str, Any]:
    """What the admin card shows."""
    limits = _limits()
    enabled = await builder_enabled()
    try:
        usage: dict[str, float] | None = await _usage()
    except Exception:
        logger.exception("catalog builder: usage could not be read")
        usage = None
    db = await get_db()
    try:
        await _sync_progress(db)
        await db.commit()
        active = await _active_run(db)
        today = await (await db.execute(
            """SELECT COUNT(*) AS n, COALESCE(SUM(people_found), 0) AS people FROM catalog_builder_companies
               WHERE status IN ('completed', 'failed') AND date(crawled_at) = date('now')"""
        )).fetchone()
        total = await (await db.execute(
            "SELECT COUNT(*) AS n FROM catalog_builder_companies WHERE status IN ('completed', 'failed')"
        )).fetchone()
    finally:
        await db.close()
    if not enabled:
        paused = None
    elif usage is None:
        paused = "Usage could not be read"
    elif active:
        paused = None
    else:
        paused = _pause_reason(usage, limits)
    return {
        "enabled": enabled,
        "paused_reason": paused,
        "current_company": active["company_name"] if active else None,
        "current_status": active["status"] if active else None,
        "current_progress": active["progress_message"] if active else None,
        "companies_done_today": int(today["n"] or 0),
        "people_found_today": int(today["people"] or 0),
        "companies_done_total": int(total["n"] or 0),
        "searches_last_hour": int(usage["searches_last_hour"]) if usage else None,
        "searches_last_day": int(usage["searches_last_day"]) if usage else None,
        "ai_share": round(usage["ai_share"], 3) if usage else None,
        "limits": {k: limits[k] for k in ("searches_per_hour", "searches_per_day", "ai_share", "recrawl_days")},
    }
