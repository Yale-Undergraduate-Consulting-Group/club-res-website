"""Reserve draft requests transactionally before paid inference; store no prompt text."""
import os
import json
import sqlite3
from contextlib import closing
from fastapi import HTTPException
from app.database import get_db
from app.services.llm import validate_model

# Cheap models weigh a fraction of a unit, so a weighted budget alone would
# permit tens of thousands of calls an hour. Bound raw volume at this multiple
# of the unit budget to keep a runaway loop from hammering the provider.
_CALL_CEILING_MULTIPLE = 10


def reserve_bedrock_invocation(
    model: str,
    user_id: int | None = None,
    purpose: str = 'inference',
    estimated_input_tokens: int | None = None,
    max_output_tokens: int | None = None,
) -> int:
    """Charge every paid attempt, including ranking/fanout, across worker processes.

    The budget is denominated in Haiku-class units rather than raw calls, so a
    cheap triage fanout cannot consume the hour that member-facing generation
    needs. A separate call ceiling still bounds request volume, since a runaway
    loop of cheap calls is a throttling and cost risk even when each is nearly
    free.

    This synchronous boundary runs in inference threads and never nests an event
    loop. Failed/uncertain provider calls retain reservations to avoid undercounts.
    """
    from app.database import is_postgres, sqlite_file_path
    from app.services.llm import model_quota_weight
    if is_postgres():
        raise HTTPException(503,'Paid inference requires a verified transactional quota store')
    try:
        limit=int(os.getenv('BEDROCK_CALLS_PER_CLUB_PER_HOUR','200'))
        if not 0 <= limit <= 2000:
            raise ValueError()
    except ValueError as exc:
        raise HTTPException(503,'Paid inference quota configuration is invalid') from exc
    weight=model_quota_weight(model)
    call_ceiling=limit*_CALL_CEILING_MULTIPLE
    try:
        # mode=rw refuses to invent an empty database when its durable mount is absent.
        uri=sqlite_file_path().resolve().as_uri()+'?mode=rw'
        with closing(sqlite3.connect(uri,uri=True,timeout=5,isolation_level=None)) as db:
            try:
                db.execute('BEGIN IMMEDIATE')
                # Reservations written before weighting existed count as one unit.
                spent,calls=db.execute("""SELECT
                        COALESCE(SUM(COALESCE(json_extract(details_json,'$.quota_weight'),1.0)),0.0),
                        COUNT(*)
                    FROM usage_events WHERE event_type='bedrock_reserved'
                    AND created_at >= datetime('now','-1 hour')""").fetchone()
                if float(spent)+weight > limit:
                    raise HTTPException(429,'Club inference limit reached. Please try again later.')
                if int(calls) >= call_ceiling:
                    raise HTTPException(429,'Club inference request rate reached. Please try again later.')
                cursor = db.execute("""INSERT INTO usage_events(user_id,event_type,resource_type,details_json)
                    VALUES(?,'bedrock_reserved',?,?)""",(user_id,purpose,json.dumps({
                        'model_id':model,
                        'quota_weight':weight,
                        'estimated_input_tokens':estimated_input_tokens,
                        'max_output_tokens':max_output_tokens,
                        'status':'reserved',
                    })))
                db.commit()
                return int(cursor.lastrowid)
            finally:
                if db.in_transaction:
                    db.rollback()
    except sqlite3.Error as exc:
        raise HTTPException(503,'Paid inference quota could not be recorded') from exc


def complete_bedrock_invocation(event_id: int, input_tokens: int | None, output_tokens: int | None) -> None:
    """Attach provider token counts to a reservation without storing prompts or responses."""
    from app.database import is_postgres, sqlite_file_path
    if is_postgres():
        return
    try:
        uri=sqlite_file_path().resolve().as_uri()+'?mode=rw'
        with closing(sqlite3.connect(uri,uri=True,timeout=5,isolation_level=None)) as db:
            row=db.execute("SELECT details_json FROM usage_events WHERE id=? AND event_type='bedrock_reserved'",(event_id,)).fetchone()
            if not row:
                return
            details=json.loads(row[0] or '{}')
            details.update({'input_tokens':input_tokens,'output_tokens':output_tokens,'status':'completed'})
            db.execute('UPDATE usage_events SET details_json=? WHERE id=?',(json.dumps(details),event_id))
    except (sqlite3.Error,ValueError,TypeError):
        # Accounting enrichment must never turn a completed model response into a retry.
        return


async def reserve_generation(user_id: int, model: str | None):
    validate_model(model)
    member_limit = min(100, max(1, int(os.getenv('DRAFTS_PER_MEMBER_PER_HOUR', '20'))))
    club_limit = min(2000, max(1, int(os.getenv('DRAFTS_PER_CLUB_PER_HOUR', '200'))))
    db = await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        row = await (await db.execute("""SELECT count(*) AS total,
            coalesce(sum(CASE WHEN user_id = ? THEN 1 ELSE 0 END), 0) AS member
            FROM usage_events WHERE event_type = 'draft_reserved'
            AND created_at >= datetime('now', '-1 hour')""", (user_id,))).fetchone()
        if row['total'] >= club_limit or row['member'] >= member_limit:
            raise HTTPException(429, 'Draft generation limit reached. Please try again later.')
        cur = await db.execute("INSERT INTO usage_events(user_id,event_type,resource_type) VALUES(?,'draft_reserved','email')", (user_id,))
        await db.commit()
        return cur.lastrowid
    finally:
        await db.close()


async def release_generation(reservation_id: int | None):
    """Give back a draft the model never wrote.

    A reservation is taken before the call so a burst cannot overrun the hour.
    When the call is then refused before reaching the model - every inference
    slot busy, the provider throttling - nothing was spent, and keeping the
    reservation charged the member a draft for nothing: a batch of twenty
    could exhaust the hour on refusals alone.
    """
    if not reservation_id:
        return
    db = await get_db()
    try:
        await db.execute("DELETE FROM usage_events WHERE id = ? AND event_type = 'draft_reserved'", (reservation_id,))
        await db.commit()
    finally:
        await db.close()


async def reserve_assistant_request(user_id: int):
    """Apply a smaller per-member allowance before building or sending document context."""
    try:
        member_limit=min(100,max(1,int(os.getenv('ASSISTANT_REQUESTS_PER_MEMBER_PER_HOUR','15'))))
        club_limit=min(1000,max(1,int(os.getenv('ASSISTANT_REQUESTS_PER_CLUB_PER_HOUR','120'))))
    except ValueError as exc:
        raise HTTPException(503,'Assistant quota configuration is invalid') from exc
    db=await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        row=await (await db.execute("""SELECT count(*) AS total,
            coalesce(sum(CASE WHEN user_id=? THEN 1 ELSE 0 END),0) AS member
            FROM usage_events WHERE event_type='assistant_reserved'
            AND created_at>=datetime('now','-1 hour')""",(user_id,))).fetchone()
        if row['total']>=club_limit or row['member']>=member_limit:
            raise HTTPException(429,'Assistant limit reached. Please try again later.')
        await db.execute("INSERT INTO usage_events(user_id,event_type,resource_type) VALUES(?,'assistant_reserved','assistant')",(user_id,))
        await db.commit()
    finally:
        await db.close()


async def reserve_firecrawl_call(user_id: int):
    """Same allowance shape as reserve_assistant_request: 15/hr/member, 120/hr club.

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
