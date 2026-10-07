"""
Analytics API - Intelligence Dashboard
"""
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends
from app.auth_deps import get_current_user_optional
from app.database import get_db

router = APIRouter()



@router.get("/leaderboard")
async def get_leaderboard():
    """Per-member send quality, not volume. Ranks replies and calls above raw
    sent count, and only counts a bounce against a member when the address
    was independently verified (contacts.confidence='high') - a bounce on an
    AI-derived guess (confidence 'low'/'medium', set when a member accepts a
    predicted address) reflects the prediction, not the member's outreach,
    and must not be held against them.
    """
    db = await get_db()
    try:
        rows = await (await db.execute(
            """SELECT
                 u.id AS user_id,
                 u.name,
                 u.picture,
                 COUNT(*) FILTER (WHERE cc.status IN ('sent','replied','bounced')) AS sent,
                 COUNT(*) FILTER (WHERE cc.status = 'replied') AS replied,
                 COUNT(*) FILTER (WHERE cc.status = 'bounced' AND c.confidence = 'high') AS penalized_bounces,
                 COUNT(*) FILTER (WHERE cc.status = 'bounced' AND c.confidence != 'high') AS forgiven_bounces,
                 COUNT(DISTINCT c.company) FILTER (WHERE cc.status IN ('sent','replied','bounced')) AS companies_reached
               FROM campaign_contacts cc
               JOIN users u ON u.id = cc.sent_by_user_id
               JOIN contacts c ON c.id = cc.contact_id
               WHERE cc.sent_by_user_id IS NOT NULL
               GROUP BY u.id
               HAVING sent > 0"""
        )).fetchall()
        board = []
        for r in rows:
            d = dict(r)
            # Replies and calls are the point; volume alone earns nothing.
            # A verified-address bounce costs more than a raw send is worth,
            # so spamming unverified volume cannot outscore fewer, real replies.
            d["quality_score"] = d["replied"] * 10 - d["penalized_bounces"] * 4
            board.append(d)
        board.sort(key=lambda d: (-d["quality_score"], -d["replied"]))
        return {"leaderboard": board}
    finally:
        await db.close()


@router.get("/companies-reached")
async def get_companies_reached(user_id: int | None = None):
    """Companies a member (or, for an admin query, anyone) has already
    contacted - so a member can check before starting outreach somewhere
    someone else already covered."""
    db = await get_db()
    try:
        params: list = []
        scope = ""
        if user_id is not None:
            scope = "AND cc.sent_by_user_id = ?"
            params.append(user_id)
        rows = await (await db.execute(
            f"""SELECT c.company, c.company_domain,
                      COUNT(DISTINCT c.id) AS contacts_reached,
                      SUM(CASE WHEN cc.status = 'replied' THEN 1 ELSE 0 END) AS replies,
                      MAX(cc.sent_at) AS last_sent_at
               FROM campaign_contacts cc
               JOIN contacts c ON c.id = cc.contact_id
               WHERE cc.status IN ('sent','replied','bounced') {scope}
               GROUP BY c.company
               ORDER BY last_sent_at DESC""",
            params,
        )).fetchall()
        return {"companies": [dict(r) for r in rows]}
    finally:
        await db.close()


async def _outcome_split(db, user_id: int | None) -> dict:
    """How outreach actually turned out, for one member or the whole club.

    Split by person rather than by message: "we mailed 40 people, 3 replied,
    1 bounced" is the sentence a member wants, and it is also the shape a
    chart needs.
    """
    scope = "AND cc.sent_by_user_id = ?" if user_id else ""
    args = [user_id] if user_id else []
    row = await (await db.execute(
        f"""SELECT
              COUNT(DISTINCT CASE WHEN cc.sent_at IS NOT NULL THEN cc.contact_id END) AS mailed,
              COUNT(DISTINCT CASE WHEN cc.replied_at IS NOT NULL THEN cc.contact_id END) AS replied,
              COUNT(DISTINCT CASE WHEN cc.status = 'bounced' THEN cc.contact_id END) AS bounced,
              COUNT(DISTINCT CASE WHEN cc.status IN ('pending','sending') THEN cc.contact_id END) AS queued
            FROM campaign_contacts cc WHERE 1=1 {scope}""",
        args,
    )).fetchone()
    mailed = int(row["mailed"] or 0)
    replied = int(row["replied"] or 0)
    bounced = int(row["bounced"] or 0)
    return {
        "mailed": mailed,
        "replied": replied,
        "bounced": bounced,
        "queued": int(row["queued"] or 0),
        # Awaiting is what is left of the people mailed: the slice that is
        # neither a reply nor a bounce, so the parts sum to the whole.
        "awaiting": max(0, mailed - replied - bounced),
        "reply_rate": round(replied / mailed * 100, 1) if mailed else 0.0,
    }


async def _sector_split(db, user_id: int | None) -> list[dict]:
    """Where a member's own contacts are, by sector. This is the half of the
    picture leadership cannot dictate: it shows what someone has chosen to
    work on, which is the thing worth building on."""
    scope = "AND c.owner_id = ?" if user_id else ""
    args = [user_id] if user_id else []
    rows = await (await db.execute(
        f"""SELECT COALESCE(NULLIF(TRIM(r.sector_label), ''), 'Unclassified') AS sector,
                   COUNT(DISTINCT c.id) AS n
            FROM contacts c
            LEFT JOIN company_register r
              ON LOWER(TRIM(r.company_name)) = LOWER(TRIM(c.company))
            WHERE TRIM(COALESCE(c.company, '')) <> '' {scope}
            GROUP BY sector ORDER BY n DESC LIMIT 8""",
        args,
    )).fetchall()
    return [{"sector": r["sector"], "count": int(r["n"])} for r in rows]


@router.get("/dashboard")
async def get_dashboard(user: dict | None = Depends(get_current_user_optional)):
    """Home dashboard metrics: active campaigns, contacts discovered, emails in queue."""
    db = await get_db()
    try:
        # Contacts discovered today
        cursor = await db.execute(
            """SELECT COUNT(*) as count FROM contacts 
               WHERE date(created_at) = date('now')"""
        )
        contacts_today = (await cursor.fetchone())["count"]

        # Emails in queue (pending)
        cursor = await db.execute(
            """SELECT COUNT(*) as count FROM campaign_contacts WHERE status = 'pending'"""
        )
        emails_queued = (await cursor.fetchone())["count"]

        # Active campaigns
        cursor = await db.execute(
            """SELECT COUNT(*) as count FROM campaigns WHERE status = 'releasing'"""
        )
        active_campaigns = (await cursor.fetchone())["count"]

        # Keep sent messages in totals after reply or bounce status changes.
        cursor = await db.execute(
            """SELECT 
                 COUNT(*) as total_sent,
                 SUM(CASE WHEN opened_at IS NOT NULL THEN 1 ELSE 0 END) as opened,
                 SUM(CASE WHEN replied_at IS NOT NULL THEN 1 ELSE 0 END) as replied
               FROM campaign_contacts WHERE sent_at IS NOT NULL"""
        )
        row = await cursor.fetchone()
        total_sent = row["total_sent"] or 0
        opened = row["opened"] or 0
        replied = row["replied"] or 0
        open_rate = (opened / total_sent * 100) if total_sent else 0
        reply_rate = (replied / total_sent * 100) if total_sent else 0

        user_id = int(user["id"]) if user else None
        return {
            "contacts_discovered_today": contacts_today,
            "emails_in_queue": emails_queued,
            "active_campaigns": active_campaigns,
            "total_sent": total_sent,
            "open_rate": round(open_rate, 1),
            "reply_rate": round(reply_rate, 1),
            "opened": opened,
            "replied": replied,
            # Both halves, because a club total hides whether anyone is doing
            # anything and a personal total hides whether the club is.
            "mine": await _outcome_split(db, user_id) if user_id else None,
            "club": await _outcome_split(db, None),
            "my_sectors": await _sector_split(db, user_id) if user_id else [],
            "club_sectors": await _sector_split(db, None),
        }
    finally:
        await db.close()


@router.get("/campaigns/{campaign_id}/metrics")
async def get_campaign_metrics(campaign_id: int):
    """Per-campaign metrics."""
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT 
                 COUNT(*) as total,
                 SUM(CASE WHEN sent_at IS NOT NULL THEN 1 ELSE 0 END) as sent,
                 SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending,
                 SUM(CASE WHEN opened_at IS NOT NULL THEN 1 ELSE 0 END) as opened,
                 SUM(CASE WHEN replied_at IS NOT NULL THEN 1 ELSE 0 END) as replied
               FROM campaign_contacts WHERE campaign_id = ?""",
            (campaign_id,),
        )
        row = await cursor.fetchone()
        d = dict(row)
        total = d["total"] or 0
        sent = d["sent"] or 0
        d["open_rate"] = round((d["opened"] or 0) / sent * 100, 1) if sent else 0
        d["reply_rate"] = round((d["replied"] or 0) / sent * 100, 1) if sent else 0
        return d
    finally:
        await db.close()


@router.get("/due-follow-ups")
async def get_due_follow_ups_count():
    """Count campaign contacts whose next sequence step is due today (for dashboard widget)."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, sequence_id FROM campaigns WHERE sequence_id IS NOT NULL AND status = 'sent'"
        )
        campaigns = await cursor.fetchall()
        today = datetime.now(timezone.utc).date()
        count = 0
        for camp in campaigns:
            cursor = await db.execute(
                "SELECT days_after FROM follow_up_steps WHERE sequence_id = ? ORDER BY step_order, days_after",
                (camp["sequence_id"],),
            )
            steps = await cursor.fetchall()
            if not steps:
                continue
            cursor = await db.execute(
                """SELECT cc.id, cc.sequence_step_sent, cc.last_sequence_sent_at
                   FROM campaign_contacts cc
                   WHERE cc.campaign_id = ? AND cc.status = 'sent'
                     AND cc.sequence_step_sent < ? AND cc.last_sequence_sent_at IS NOT NULL""",
                (camp["id"], len(steps)),
            )
            for cc in await cursor.fetchall():
                step_idx = cc["sequence_step_sent"]
                days_after = steps[step_idx]["days_after"] or 0
                last_sent = cc["last_sequence_sent_at"]
                try:
                    if hasattr(last_sent, "date"):
                        last_date = last_sent.date()
                    else:
                        last_date = datetime.fromisoformat(str(last_sent).replace("Z", "+00:00")).date()
                except Exception:
                    continue
                due_date = last_date + timedelta(days=days_after)
                if due_date <= today:
                    count += 1
        return {"count": count}
    finally:
        await db.close()


@router.get("/time-series")
async def get_time_series(days: int = 30):
    """Daily counts of sent, opened, replied for the last N days (for charts)."""
    db = await get_db()
    try:
        cursor = await db.execute(
            f"""SELECT date(sent_at) as d,
                 COUNT(*) as sent,
                 SUM(CASE WHEN opened_at IS NOT NULL THEN 1 ELSE 0 END) as opened,
                 SUM(CASE WHEN replied_at IS NOT NULL THEN 1 ELSE 0 END) as replied
               FROM campaign_contacts
               WHERE sent_at IS NOT NULL AND date(sent_at) >= date('now', '-{days} days')
               GROUP BY date(sent_at)
               ORDER BY d"""
        )
        rows = await cursor.fetchall()
        by_date = {str(r["d"]): {"sent": r["sent"], "opened": r["opened"], "replied": r["replied"]} for r in rows}
        labels = []
        sent_list = []
        opened_list = []
        replied_list = []
        for i in range(days):
            d = (datetime.now(timezone.utc) - timedelta(days=days - 1 - i)).date().strftime("%Y-%m-%d")
            labels.append(d)
            row = by_date.get(d, {"sent": 0, "opened": 0, "replied": 0})
            sent_list.append(row["sent"])
            opened_list.append(row["opened"])
            replied_list.append(row["replied"])
        return {"labels": labels, "sent": sent_list, "opened": opened_list, "replied": replied_list}
    finally:
        await db.close()


@router.get("/export")
async def export_analytics_csv():
    """Export analytics summary as CSV (sent, opened, replied by day; campaign breakdown)."""
    from fastapi.responses import StreamingResponse
    import io
    import csv as csv_module

    db = await get_db()
    try:
        buf = io.StringIO()
        w = csv_module.writer(buf)
        w.writerow(["Metric", "Value"])
        cursor = await db.execute(
            """SELECT COUNT(*) as total_sent,
                 SUM(CASE WHEN opened_at IS NOT NULL THEN 1 ELSE 0 END) as opened,
                 SUM(CASE WHEN replied_at IS NOT NULL THEN 1 ELSE 0 END) as replied
               FROM campaign_contacts WHERE sent_at IS NOT NULL"""
        )
        row = await cursor.fetchone()
        total_sent = row["total_sent"] or 0
        opened = row["opened"] or 0
        replied = row["replied"] or 0
        w.writerow(["Total sent", total_sent])
        w.writerow(["Opened", opened])
        w.writerow(["Replied", replied])
        w.writerow(["Open rate %", round(opened / total_sent * 100, 1) if total_sent else 0])
        w.writerow(["Reply rate %", round(replied / total_sent * 100, 1) if total_sent else 0])
        w.writerow([])
        w.writerow(["Campaign", "Total", "Sent", "Opened", "Replied", "Open rate %", "Reply rate %"])
        cursor = await db.execute(
            """SELECT c.name, c.id,
                 COUNT(cc.id) as total,
                 SUM(CASE WHEN cc.sent_at IS NOT NULL THEN 1 ELSE 0 END) as sent,
                 SUM(CASE WHEN cc.opened_at IS NOT NULL THEN 1 ELSE 0 END) as opened,
                 SUM(CASE WHEN cc.replied_at IS NOT NULL THEN 1 ELSE 0 END) as replied
               FROM campaigns c
               LEFT JOIN campaign_contacts cc ON cc.campaign_id = c.id
               GROUP BY c.id"""
        )
        for r in await cursor.fetchall():
            sent = r["sent"] or 0
            open_rate = round((r["opened"] or 0) / sent * 100, 1) if sent else 0
            reply_rate = round((r["replied"] or 0) / sent * 100, 1) if sent else 0
            w.writerow([r["name"], r["total"], r["sent"], r["opened"], r["replied"], open_rate, reply_rate])
        buf.seek(0)
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=analytics_export.csv"},
        )
    finally:
        await db.close()


@router.get("/insights")
async def get_insights():
    """Reply-rate observations for campaigns that have sent, compared against the member's own average."""
    db = await get_db()
    try:
        insights = []
        cursor = await db.execute(
            """SELECT c.name, 
                 SUM(CASE WHEN cc.replied_at IS NOT NULL THEN 1 ELSE 0 END) as replied,
                 COUNT(*) as total
               FROM campaigns c
               JOIN campaign_contacts cc ON cc.campaign_id = c.id AND cc.sent_at IS NOT NULL
               GROUP BY c.id"""
        )
        rows = [r for r in await cursor.fetchall() if r["total"]]
        rates = [(r["name"], r["replied"] / r["total"] * 100) for r in rows]
        if rates:
            average = sum(rate for _, rate in rates) / len(rates)
            for name, rate in sorted(rates, key=lambda item: item[1], reverse=True):
                if rate > average:
                    insights.append(
                        f"Campaign '{name}' has a {rate:.1f}% reply rate, above your {average:.1f}% average."
                    )
        return {"insights": insights[:5]}
    finally:
        await db.close()


# --- Full breakdown -----------------------------------------------------
#
# One endpoint, many sections. Every section is (title, rows) with the same
# row shape, so a new measurement is added by appending one query here and
# nothing changes on the page: it renders whatever sections arrive. That is
# what "built to contain the data as it comes in" has to mean in practice,
# otherwise every new number needs a new component.

async def _rows(db, sql: str, args: tuple = ()) -> list[dict]:
    rows = await (await db.execute(sql, args)).fetchall()
    out = []
    for r in rows:
        label = r["label"]
        out.append({
            "label": str(label) if label is not None else "Unattributed",
            "value": int(r["value"] or 0),
            # Optional second measure; sections that have none simply omit it.
            "secondary": int(r["secondary"]) if "secondary" in r.keys() and r["secondary"] is not None else None,
        })
    return out


@router.get("/breakdown")
async def get_breakdown(user: dict | None = Depends(get_current_user_optional)):
    """Everything measurable about outreach, grouped into chartable sections."""
    db = await get_db()
    try:
        sections: list[dict] = []

        # The funnel, in the order the work actually happens. Counted by
        # person so the numbers are comparable down the column.
        funnel = await (await db.execute(
            """SELECT
                 (SELECT COUNT(*) FROM contacts) AS known,
                 (SELECT COUNT(*) FROM contacts WHERE TRIM(COALESCE(email,'')) <> '') AS addressable,
                 (SELECT COUNT(DISTINCT contact_id) FROM campaign_contacts) AS in_campaign,
                 (SELECT COUNT(DISTINCT contact_id) FROM campaign_contacts WHERE sent_at IS NOT NULL) AS mailed,
                 (SELECT COUNT(DISTINCT contact_id) FROM campaign_contacts WHERE replied_at IS NOT NULL) AS replied"""
        )).fetchone()
        sections.append({
            "id": "funnel",
            "title": "From contact to reply",
            "note": "Each step counts people, so the drop between rows is the real loss.",
            "chart": "bar",
            "rows": [
                {"label": "known", "value": int(funnel["known"] or 0), "secondary": None},
                {"label": "with an address", "value": int(funnel["addressable"] or 0), "secondary": None},
                {"label": "in a campaign", "value": int(funnel["in_campaign"] or 0), "secondary": None},
                {"label": "mailed", "value": int(funnel["mailed"] or 0), "secondary": None},
                {"label": "replied", "value": int(funnel["replied"] or 0), "secondary": None},
            ],
        })

        # Who is doing the work. Named, because a club total hides the answer.
        sections.append({
            "id": "members",
            "title": "By member",
            "note": "People mailed, and how many replied.",
            "chart": "bar",
            "rows": await _rows(db, """
                SELECT COALESCE(NULLIF(TRIM(u.name), ''), u.email) AS label,
                       COUNT(DISTINCT cc.contact_id) AS value,
                       COUNT(DISTINCT CASE WHEN cc.replied_at IS NOT NULL THEN cc.contact_id END) AS secondary
                FROM campaign_contacts cc
                JOIN users u ON u.id = cc.sent_by_user_id
                WHERE cc.sent_at IS NOT NULL
                GROUP BY label ORDER BY value DESC LIMIT 20"""),
        })

        sections.append({
            "id": "sectors",
            "title": "By sector",
            "note": "Where the club's contacts are, using the public register's classification.",
            "chart": "pie",
            "rows": await _rows(db, """
                SELECT COALESCE(NULLIF(TRIM(r.sector_label), ''), 'Unclassified') AS label,
                       COUNT(DISTINCT c.id) AS value
                FROM contacts c
                LEFT JOIN company_register r
                  ON LOWER(TRIM(r.company_name)) = LOWER(TRIM(c.company))
                WHERE TRIM(COALESCE(c.company, '')) <> ''
                GROUP BY label ORDER BY value DESC LIMIT 10"""),
        })

        sections.append({
            "id": "companies",
            "title": "By company",
            "note": "Contacts on file, and how many have been mailed.",
            "chart": "bar",
            "rows": await _rows(db, """
                SELECT TRIM(c.company) AS label,
                       COUNT(DISTINCT c.id) AS value,
                       COUNT(DISTINCT CASE WHEN cc.sent_at IS NOT NULL THEN c.id END) AS secondary
                FROM contacts c
                LEFT JOIN campaign_contacts cc ON cc.contact_id = c.id
                WHERE TRIM(COALESCE(c.company, '')) <> ''
                GROUP BY label ORDER BY value DESC LIMIT 15"""),
        })

        sections.append({
            "id": "stage",
            "title": "By pipeline stage",
            "chart": "pie",
            "rows": await _rows(db, """
                SELECT COALESCE(NULLIF(TRIM(pipeline_status), ''), 'cold') AS label, COUNT(*) AS value
                FROM contacts GROUP BY label ORDER BY value DESC"""),
        })

        # Why sending stopped. An empty section here is the good case.
        sections.append({
            "id": "failures",
            "title": "Why sending stopped",
            "note": "Bounces and errors, grouped by the reason the server recorded.",
            "chart": "bar",
            "rows": await _rows(db, """
                SELECT COALESCE(NULLIF(TRIM(SUBSTR(last_error, 1, 60)), ''), status) AS label,
                       COUNT(*) AS value
                FROM campaign_contacts
                WHERE status IN ('bounced', 'failed')
                GROUP BY label ORDER BY value DESC LIMIT 10"""),
        })

        sections.append({
            "id": "sources",
            "title": "Where contacts came from",
            "chart": "pie",
            "rows": await _rows(db, """
                SELECT COALESCE(NULLIF(TRIM(contact_source), ''), 'unrecorded') AS label, COUNT(*) AS value
                FROM contacts GROUP BY label ORDER BY value DESC LIMIT 10"""),
        })

        # Activity by week rather than by day: at club volumes a daily series
        # is mostly zeros, which reads as "nothing works" instead of "weekly".
        sections.append({
            "id": "weeks",
            "title": "Sent by week",
            "note": "Mail actually sent, and replies received, over the last twelve weeks.",
            "chart": "bar",
            "rows": await _rows(db, """
                SELECT strftime('%Y-W%W', sent_at) AS label,
                       COUNT(*) AS value,
                       SUM(CASE WHEN replied_at IS NOT NULL THEN 1 ELSE 0 END) AS secondary
                FROM campaign_contacts
                WHERE sent_at IS NOT NULL AND sent_at >= datetime('now', '-84 days')
                GROUP BY label ORDER BY label"""),
        })

        user_id = int(user["id"]) if user else None
        return {
            "sections": [s for s in sections if s["rows"]],
            "mine": await _outcome_split(db, user_id) if user_id else None,
            "club": await _outcome_split(db, None),
        }
    finally:
        await db.close()
