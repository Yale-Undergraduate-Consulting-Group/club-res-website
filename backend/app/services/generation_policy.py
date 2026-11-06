"""Charge model calls to the club's monthly AI budget; store no prompt text."""
import os
import json
import sqlite3
from contextlib import closing
from fastapi import HTTPException
from app.database import get_db

DEFAULT_MONTHLY_BUDGET_USD = 50.0
# Spend this calendar month (UTC): the provider-reported cost of finished calls,
# and the worst-case estimate of calls still running or of uncertain outcome.
_MONTH_SPEND_SQL = """SELECT COALESCE(SUM(COALESCE(
        json_extract(details_json,'$.cost_usd'),
        json_extract(details_json,'$.estimated_cost_usd'),0)),0.0)
    FROM usage_events WHERE event_type='bedrock_reserved'
    AND created_at >= strftime('%Y-%m-01 00:00:00','now')"""


def monthly_budget_usd() -> float:
    try:
        budget = float(os.getenv('AI_MONTHLY_BUDGET_USD', str(DEFAULT_MONTHLY_BUDGET_USD)))
        if not 0 <= budget <= 100_000:
            raise ValueError()
    except ValueError as exc:
        raise HTTPException(503, 'AI budget configuration is invalid') from exc
    return budget


def _connect():
    from app.database import sqlite_file_path
    # mode=rw refuses to invent an empty database when its durable mount is absent.
    uri = sqlite_file_path().resolve().as_uri() + '?mode=rw'
    return closing(sqlite3.connect(uri, uri=True, timeout=5, isolation_level=None))


def reserve_bedrock_invocation(
    model: str,
    user_id: int | None = None,
    purpose: str = 'inference',
    estimated_input_tokens: int | None = None,
    max_output_tokens: int | None = None,
) -> int:
    """Reserve a call's worst-case cost against the month before the call is made.

    The worst case (estimated input plus the full output allowance) is what the
    reservation holds until the provider reports the real token counts, so
    concurrent calls across worker processes cannot overrun the budget. This
    synchronous boundary runs in inference threads and never nests an event loop.
    """
    from app.database import is_postgres
    from app.services.llm import cost_usd
    if is_postgres():
        raise HTTPException(503, 'Paid inference requires a verified transactional quota store')
    budget = monthly_budget_usd()
    estimate = cost_usd(model, estimated_input_tokens or 0, max_output_tokens or 0)
    try:
        with _connect() as db:
            try:
                db.execute('BEGIN IMMEDIATE')
                spent = float(db.execute(_MONTH_SPEND_SQL).fetchone()[0] or 0.0)
                if spent + estimate > budget:
                    raise HTTPException(429, "The club's AI budget for this month is used up.")
                cursor = db.execute("""INSERT INTO usage_events(user_id,event_type,resource_type,details_json)
                    VALUES(?,'bedrock_reserved',?,?)""", (user_id, purpose, json.dumps({
                        'model_id': model,
                        'estimated_input_tokens': estimated_input_tokens,
                        'max_output_tokens': max_output_tokens,
                        'estimated_cost_usd': estimate,
                        'status': 'reserved',
                    })))
                db.commit()
                return int(cursor.lastrowid)
            finally:
                if db.in_transaction:
                    db.rollback()
    except sqlite3.Error as exc:
        raise HTTPException(503, 'Paid inference could not be recorded') from exc


def complete_bedrock_invocation(event_id: int, input_tokens: int | None, output_tokens: int | None) -> None:
    """Replace the reserved estimate with the cost the provider's token counts imply."""
    from app.database import is_postgres
    from app.services.llm import cost_usd
    if is_postgres():
        return
    try:
        with _connect() as db:
            row = db.execute("SELECT details_json FROM usage_events WHERE id=? AND event_type='bedrock_reserved'", (event_id,)).fetchone()
            if not row:
                return
            details = json.loads(row[0] or '{}')
            details.update({'input_tokens': input_tokens, 'output_tokens': output_tokens, 'status': 'completed'})
            if input_tokens is not None and output_tokens is not None:
                details['cost_usd'] = cost_usd(details.get('model_id') or '', int(input_tokens), int(output_tokens))
            db.execute('UPDATE usage_events SET details_json=? WHERE id=?', (json.dumps(details), event_id))
    except (sqlite3.Error, ValueError, TypeError):
        # Accounting enrichment must never turn a completed model response into a retry.
        return


def release_bedrock_invocation(event_id: int) -> None:
    """Drop a reservation for a call the provider refused before any inference."""
    from app.database import is_postgres
    if is_postgres():
        return
    try:
        with _connect() as db:
            db.execute("DELETE FROM usage_events WHERE id=? AND event_type='bedrock_reserved'", (event_id,))
    except sqlite3.Error:
        return  # a kept reservation over-counts; it never under-counts


async def bedrock_budget_share() -> float:
    """The fraction of this month's AI budget already spent or reserved. The
    catalog builder stops taking new companies above its share so member work
    keeps the rest."""
    budget = monthly_budget_usd()
    db = await get_db()
    try:
        row = await (await db.execute(_MONTH_SPEND_SQL)).fetchone()
    finally:
        await db.close()
    return float(row[0] or 0.0) / budget if budget > 0 else 1.0


async def reserve_firecrawl_call(user_id: int):
    """15 calls/hr/member and 120/hr for the club.

    Per the Firecrawl expansion plan (docs/FIRECRAWL-SEARCH-EXPANSION-PLAN.md,
    section 1.6): the OCI VM is a shared 2 OCPU/12GB box already serving another
    tenant. Reuse this exact mechanism/numbers rather than inventing a new cap -
    a second budget with different math is a second place to get the math wrong.
    """
    try:
        member_limit = min(100, max(1, int(os.getenv('FIRECRAWL_CALLS_PER_MEMBER_PER_HOUR', '15'))))
        club_limit = min(1000, max(1, int(os.getenv('FIRECRAWL_CALLS_PER_CLUB_PER_HOUR', '120'))))
    except ValueError as exc:
        raise HTTPException(503, 'Firecrawl quota configuration is invalid') from exc
    db = await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        row = await (await db.execute("""SELECT count(*) AS total,
            coalesce(sum(CASE WHEN user_id=? THEN 1 ELSE 0 END),0) AS member
            FROM usage_events WHERE event_type='firecrawl_reserved'
            AND created_at>=datetime('now','-1 hour')""", (user_id,))).fetchone()
        if row['total'] >= club_limit or row['member'] >= member_limit:
            raise HTTPException(429, 'Web fetch limit reached. Please try again later.')
        await db.execute(
            "INSERT INTO usage_events(user_id,event_type,resource_type) VALUES(?,'firecrawl_reserved','web_fetch')",
            (user_id,))
        await db.commit()
    finally:
        await db.close()

async def reserve_tinyfish_call(user_id: int):
    """Same shape as reserve_firecrawl_call. TinyFish is reached through
    Monid (MONID_API_KEY, see app/services/web_fetch.py) rather than a
    direct TinyFish account, so its exact upstream ceiling for this key is
    opaque - these defaults stay comfortably under TinyFish's own published
    free-tier numbers (500 search requests/hour, 1,000 fetch urls/day, see
    https://www.tinyfish.ai/pricing) with real margin, and use a per-member
    limit that still leaves every other active member headroom in the same
    hour.
    """
    try:
        member_limit = min(200, max(1, int(os.getenv('TINYFISH_CALLS_PER_MEMBER_PER_HOUR', '30'))))
        club_limit = min(2000, max(1, int(os.getenv('TINYFISH_CALLS_PER_CLUB_PER_HOUR', '400'))))
    except ValueError as exc:
        raise HTTPException(503, 'TinyFish quota configuration is invalid') from exc
    db = await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        row = await (await db.execute("""SELECT count(*) AS total,
            coalesce(sum(CASE WHEN user_id=? THEN 1 ELSE 0 END),0) AS member
            FROM usage_events WHERE event_type='tinyfish_reserved'
            AND created_at>=datetime('now','-1 hour')""", (user_id,))).fetchone()
        if row['total'] >= club_limit or row['member'] >= member_limit:
            raise HTTPException(429, 'Web search limit reached. Please try again later.')
        await db.execute(
            "INSERT INTO usage_events(user_id,event_type,resource_type) VALUES(?,'tinyfish_reserved','web_fetch')",
            (user_id,))
        await db.commit()
    finally:
        await db.close()


async def draft_evidence(db, contact: dict, actor_id: int) -> dict:
    """Use member-accepted public/project facts, never another member's private research."""
    from app.services.contact_intelligence import contact_evidence
    from app.services.delivery_policy import require_recipient_allowed
    await require_recipient_allowed(db, contact.get("email") or "")
    evidence = await contact_evidence(db, contact["id"], actor_id)
    if not evidence:
        return {"sources": [], "context_origin": "catalog_unreviewed"}
    if evidence.get("identity") in {"rejected", "conflicted"} or evidence.get("employment") in {"former", "stale"}:
        raise HTTPException(409, "Review current identity and employment evidence before drafting.")
    # A shared catalog entry is usable for an honest introduction. Its fields
    # are not permission to assert another member's research as established fact.
    if evidence.get("disposition") != "accepted":
        return {**evidence, "sources": [], "source_ids": [], "context_origin": "catalog_unreviewed"}
    accepted_ids = {str(value) for value in evidence.get("accepted_source_ids", [])}
    sources = [source for source in evidence.get("sources", []) if str(source.get("id")) in accepted_ids]
    return {**evidence, "sources": sources, "source_ids": [source["id"] for source in sources],
            "context_origin": "accepted_evidence"}
