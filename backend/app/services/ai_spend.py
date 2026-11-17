"""Local model-call ledger and optional Anthropic organization cost report.

The app ledger is authoritative for the club's in-app budget and is available
without provider credentials. The provider report is reconciliation only: it is
off unless a separate Admin API key is configured, and a report failure never
breaks model calls or the Costs page.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.database import get_db
from app.services.generation_policy import monthly_budget_usd
from app.services.llm import anthropic_key_status, llm_provider

ANTHROPIC_COST_URL = "https://api.anthropic.com/v1/organizations/analytics/cost"
ANTHROPIC_COST_BETA = "analytics-usage-cost-2026-08-24"
ANTHROPIC_ADMIN_VERSION = "2026-10-21"


async def anthropic_cost_report() -> dict[str, Any]:
    """Read the current month's organization cost, or report why it is absent.

    ``ANTHROPIC_ADMIN_API_KEY`` is intentionally separate from the inference
    key. Console Admin API keys have organization-wide privileges, so this path
    stays disabled unless an administrator accepts that blast radius. Set
    ``ANTHROPIC_COST_WORKSPACE_ID`` to reconcile only the app workspace.
    """
    key = (os.getenv("ANTHROPIC_ADMIN_API_KEY") or "").strip()
    if not key:
        return {"enabled": False, "usd": None, "as_of": None, "error": None}
    now = datetime.now(timezone.utc)
    params: list[tuple[str, str]] = [
        ("starting_at", now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()),
        ("ending_at", now.isoformat()),
        ("limit", "20"),
    ]
    workspace_id = (os.getenv("ANTHROPIC_COST_WORKSPACE_ID") or "").strip()
    if workspace_id:
        params.append(("workspace_ids[]", workspace_id))
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0)) as client:
            response = await client.get(
                ANTHROPIC_COST_URL,
                params=params,
                headers={
                    "x-api-key": key,
                    "anthropic-version": ANTHROPIC_ADMIN_VERSION,
                    "anthropic-beta": ANTHROPIC_COST_BETA,
                    "User-Agent": "YUCG-Club-Platform/1.0",
                },
            )
        response.raise_for_status()
        payload = response.json()
        if payload.get("has_more"):
            raise ValueError("unexpected paginated total")
        cents = Decimal("0")
        for row in payload.get("data") or []:
            cost = row.get("cost") or {}
            if cost.get("currency") != "USD":
                raise ValueError("unexpected cost currency")
            cents += Decimal(str(cost.get("amount") or "0"))
        return {
            "enabled": True,
            "usd": float(cents / 100),
            "as_of": payload.get("data_refreshed_at"),
            "error": None,
        }
    except (httpx.HTTPError, ValueError, TypeError, InvalidOperation):
        return {"enabled": True, "usd": None, "as_of": None, "error": "Anthropic cost report unavailable"}


async def month_summary() -> dict[str, Any]:
    """Current-month model usage for the admin Costs dashboard."""
    db = await get_db()
    try:
        rows = await (await db.execute(
            """SELECT json_extract(details_json,'$.model_id') AS model_id,
                      COUNT(*) AS calls,
                      COALESCE(SUM(COALESCE(json_extract(details_json,'$.cost_usd'),
                                            json_extract(details_json,'$.estimated_cost_usd'),0)),0) AS usd
               FROM usage_events
               WHERE event_type='bedrock_reserved'
                 AND created_at >= strftime('%Y-%m-01 00:00:00','now')
               GROUP BY model_id ORDER BY usd DESC"""
        )).fetchall()
    finally:
        await db.close()
    by_model = [
        {"model_id": row["model_id"] or "unknown", "calls": int(row["calls"]), "usd": float(row["usd"] or 0)}
        for row in rows
    ]
    provider_report = await anthropic_cost_report()
    return {
        "provider": llm_provider(),
        "calls": sum(item["calls"] for item in by_model),
        "month_usd_logged": sum(item["usd"] for item in by_model),
        "budget_usd": monthly_budget_usd(),
        "by_model": by_model,
        "anthropic_report": provider_report,
        "anthropic_key": anthropic_key_status(),
    }
