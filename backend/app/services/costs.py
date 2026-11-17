"""Admin cost reporting and the one-per-day Cost Explorer cache.

Paid provider calls are counted locally; the UI never calls a provider to learn
its balance. Cost Explorer is read by the leader-only scheduled job, not by an
HTTP request, so opening Admin cannot create billing API charges.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.database import get_db

log = logging.getLogger(__name__)
CE_REQUEST_USD = 0.01

# Public list-price estimates. ``None`` means the provider does not publish a
# per-call price that can be inferred from the app's counter. Sources and the
# assumptions behind these numbers live in docs/night/costs.md.
UNIT_COST_USD: dict[str, float | None] = {
    "tinyfish": 0.005,
    "firecrawl": 0.01,
    "tavily": 0.008,
    "verifalia": 0.0079,
    "serpapi": 0.015,
}


def _float_env(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, str(default))))
    except ValueError:
        return default


def _int_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default))))
    except ValueError:
        return default


async def create_tables(db: Any) -> None:
    """Migration core:4. One SQLite database belongs to one environment."""
    await db.execute(
        """CREATE TABLE IF NOT EXISTS cost_explorer_cache (
               id INTEGER PRIMARY KEY CHECK (id = 1),
               month TEXT NOT NULL,
               fetched_at TEXT NOT NULL,
               payload_json TEXT NOT NULL,
               request_usd REAL NOT NULL DEFAULT 0.01)"""
    )


async def record_paid_call(provider: str, user_id: int | None = None, **details: Any) -> None:
    """Count one attempted paid call before network I/O.

    Failed calls can still consume provider quota, so reservations deliberately
    count rather than being released on an HTTP error.
    """
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO usage_events (user_id, event_type, resource_type, details_json)
               VALUES (?, 'paid_api_call', ?, ?)""",
            (user_id, provider, json.dumps(details, separators=(",", ":")) if details else None),
        )
        await db.commit()
    finally:
        await db.close()


def _month_keys(today: date) -> list[str]:
    keys: list[str] = []
    year, month = today.year, today.month
    for _ in range(3):
        keys.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            year -= 1
            month = 12
    return list(reversed(keys))


async def _counts_by_month(db: Any, sql: str, params: tuple[Any, ...] = ()) -> dict[str, float]:
    rows = await (await db.execute(sql, params)).fetchall()
    return {str(row["month"]): float(row["amount"] or 0) for row in rows}


async def _provider_rows(db: Any, months: list[str]) -> list[dict[str, Any]]:
    start = months[0] + "-01"
    event_counts: dict[str, dict[str, float]] = {}
    for provider, event_type in (("tinyfish", "tinyfish_reserved"), ("firecrawl", "firecrawl_reserved")):
        event_counts[provider] = await _counts_by_month(
            db,
            """SELECT strftime('%Y-%m', created_at) month, COUNT(*) amount
               FROM usage_events WHERE event_type = ? AND created_at >= ?
               GROUP BY strftime('%Y-%m', created_at)""",
            (event_type, start),
        )

    for provider in ("tavily", "serpapi"):
        event_counts[provider] = await _counts_by_month(
            db,
            """SELECT strftime('%Y-%m', created_at) month, COUNT(*) amount
               FROM usage_events
               WHERE event_type = 'paid_api_call' AND resource_type = ? AND created_at >= ?
               GROUP BY strftime('%Y-%m', created_at)""",
            (provider, start),
        )

    verifalia = await _counts_by_month(
        db,
        """SELECT substr(day, 1, 7) month, SUM(used) amount
           FROM verification_provider_days WHERE day >= ? GROUP BY substr(day, 1, 7)""",
        (start,),
    )
    event_counts["verifalia"] = verifalia

    llm = await _counts_by_month(
        db,
        """SELECT strftime('%Y-%m', created_at) month, COUNT(*) amount
           FROM usage_events WHERE event_type = 'bedrock_reserved' AND created_at >= ?
           GROUP BY strftime('%Y-%m', created_at)""",
        (start,),
    )
    llm_usd = await _counts_by_month(
        db,
        """SELECT strftime('%Y-%m', created_at) month,
                  SUM(COALESCE(json_extract(details_json, '$.cost_usd'),
                               json_extract(details_json, '$.estimated_cost_usd'), 0)) amount
           FROM usage_events WHERE event_type = 'bedrock_reserved' AND created_at >= ?
           GROUP BY strftime('%Y-%m', created_at)""",
        (start,),
    )

    meta: dict[str, tuple[str, str, str | None, str]] = {
        "tinyfish": ("Monid / TinyFish", "calls", f"{_int_env('TINYFISH_CALLS_PER_CLUB_PER_HOUR', 400)}/club/hour", "Conservative estimate uses Monid's $0.005 TinyFish search price; fetch calls cost $0.001."),
        "firecrawl": ("Firecrawl", "calls", f"{_int_env('FIRECRAWL_CALLS_PER_CLUB_PER_HOUR', 120)}/club/hour; 1,000 free credits/month", "Conservative estimate uses two $0.005 pay-as-you-go credits per call; the free tier may make actual spend zero."),
        "tavily": ("Tavily", "searches", f"{_int_env('RESEARCH_TAVILY_DAILY_LIMIT', 200)}/club/day; 1,000 free credits/month", "Estimate uses pay-as-you-go basic-search credits; the free tier may make actual spend zero."),
        "verifalia": ("Verifalia", "credits", f"{_int_env('VERIFALIA_DAILY_CREDIT_CAP', 25)}/day (free plan)", "Estimate uses the smallest credit pack; 25 daily free credits may make actual spend zero."),
        "serpapi": ("SerpAPI", "searches", f"{_int_env('SERPAPI_MONTHLY_SEARCH_LIMIT', 250)}/month provider plan (not enforced)", "Estimate uses the lowest paid plan; 250 free searches/month may make actual spend zero."),
    }
    rows: list[dict[str, Any]] = []
    current = months[-1]
    for provider in ("tinyfish", "firecrawl", "tavily", "verifalia", "serpapi"):
        name, unit, limit, note = meta[provider]
        unit_cost = UNIT_COST_USD[provider]
        trend = [{"month": m, "calls": int(event_counts[provider].get(m, 0)),
                  "usd": (round(event_counts[provider].get(m, 0) * unit_cost, 4) if unit_cost is not None else None)}
                 for m in months]
        rows.append({"id": provider, "name": name, "calls": trend[-1]["calls"], "estimated_usd": trend[-1]["usd"],
                     "budget_or_limit": limit, "unit": unit, "unit_cost_usd": unit_cost,
                     "trend": trend, "note": note})

    llm_trend = [{"month": m, "calls": int(llm.get(m, 0)), "usd": round(llm_usd.get(m, 0), 4)} for m in months]
    rows.insert(0, {"id": "llm", "name": "Claude (Anthropic API / Bedrock)", "calls": int(llm.get(current, 0)),
                    "estimated_usd": round(llm_usd.get(current, 0), 4),
                    "budget_or_limit": f"${_float_env('AI_MONTHLY_BUDGET_USD', 15):.2f}/month",
                    "unit": "calls", "unit_cost_usd": None, "trend": llm_trend,
                    "note": "Uses the actual input/output token count recorded by the shared LLM gateway."})
    return rows


def _empty_aws() -> dict[str, Any]:
    return {"connected": False, "month": datetime.now(timezone.utc).strftime("%Y-%m"),
            "fetched_at": None, "total_usd": None, "by_service": [], "by_environment": [],
            "message": "AWS costs not connected. A reviewed Terraform apply must grant ce:GetCostAndUsage; the daily job will then fill this cache."}


async def report() -> dict[str, Any]:
    """The cached account bill plus locally counted paid API use. No network I/O."""
    db = await get_db()
    try:
        months = _month_keys(datetime.now(timezone.utc).date())
        providers = await _provider_rows(db, months)
        row = await (await db.execute("SELECT * FROM cost_explorer_cache WHERE id = 1")).fetchone()
        aws = _empty_aws()
        if row:
            try:
                aws = json.loads(row["payload_json"])
                aws.update(connected=True, month=row["month"], fetched_at=row["fetched_at"], request_usd=float(row["request_usd"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                log.warning("Ignoring malformed Cost Explorer cache row")
    finally:
        await db.close()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "aws": aws,
        "providers": providers,
        "recommendations": [
            {"rank": 1, "title": "Run dev only when it is needed", "saving": "$6–9/month", "risk": "Dev and its scheduled jobs are unavailable while stopped; requires president approval."},
            {"rank": 2, "title": "Keep search and verification caches enabled", "saving": "Avoids duplicate per-call API spend", "risk": "Cached public data can be up to seven days old."},
            {"rank": 3, "title": "Use provider caps as hard ceilings", "saving": "Bounds LLM, search and verification spend", "risk": "A feature pauses after its shared allowance is exhausted."},
            {"rank": 4, "title": "Keep public IPv4 egress", "saving": "$0; cheaper than NAT or the required interface endpoints", "risk": "Removing it would cut off ECR, SSM and arbitrary crawled sites."},
        ],
    }


def _ce_payload(response: dict[str, Any]) -> dict[str, Any]:
    by_service: dict[str, float] = {}
    by_environment: dict[str, float] = {}
    for period in response.get("ResultsByTime") or []:
        for group in period.get("Groups") or []:
            keys = group.get("Keys") or []
            if len(keys) != 2:
                continue
            amount = float(((group.get("Metrics") or {}).get("UnblendedCost") or {}).get("Amount") or 0)
            service, tagged = str(keys[0]), str(keys[1])
            environment = tagged.split("$", 1)[-1] or "untagged"
            by_service[service] = by_service.get(service, 0) + amount
            by_environment[environment] = by_environment.get(environment, 0) + amount
    total = sum(by_service.values())
    return {"total_usd": round(total, 4),
            "by_service": [{"name": k, "usd": round(v, 4)} for k, v in sorted(by_service.items(), key=lambda item: (-item[1], item[0]))],
            "by_environment": [{"name": k, "usd": round(v, 4)} for k, v in sorted(by_environment.items(), key=lambda item: (-item[1], item[0]))],
            "message": "Cost Explorer cached once daily; untagged account costs appear separately."}


async def refresh_cost_explorer() -> None:
    """Refresh at most once per UTC day; failures leave the previous cache intact."""
    if os.getenv("APP_ENV", "").lower() not in {"production", "beta"}:
        return
    db = await get_db()
    try:
        row = await (await db.execute("SELECT fetched_at FROM cost_explorer_cache WHERE id = 1")).fetchone()
        today = datetime.now(timezone.utc).date()
        if row:
            try:
                if datetime.fromisoformat(str(row["fetched_at"])).date() >= today:
                    return
            except ValueError:
                pass
    finally:
        await db.close()

    start = today.replace(day=1).isoformat()
    end = (today + timedelta(days=1)).isoformat()
    try:
        import boto3
        client = boto3.client("ce", region_name="us-east-1")
        response = await asyncio.to_thread(
            client.get_cost_and_usage,
            TimePeriod={"Start": start, "End": end},
            Granularity="MONTHLY",
            Metrics=["UnblendedCost"],
            GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}, {"Type": "TAG", "Key": "Site"}],
            MaxResults=100,
        )
    except Exception as exc:  # Credentials/permission are an operational prerequisite.
        log.warning("Cost Explorer refresh unavailable: %s", type(exc).__name__)
        return

    payload = _ce_payload(response)
    fetched = datetime.now(timezone.utc).isoformat()
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO cost_explorer_cache (id, month, fetched_at, payload_json, request_usd)
               VALUES (1, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET month=excluded.month, fetched_at=excluded.fetched_at,
                   payload_json=excluded.payload_json, request_usd=excluded.request_usd""",
            (today.strftime("%Y-%m"), fetched, json.dumps(payload, separators=(",", ":")), CE_REQUEST_USD),
        )
        await db.commit()
    finally:
        await db.close()
