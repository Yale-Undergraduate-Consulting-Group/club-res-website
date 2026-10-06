"""
Usage / telemetry service - log events for Operations Intelligence.
All data is private and internal only (admin-only access to analytics).
"""
import json
from app.database import get_db


async def log_event(
    user_id: int | None,
    event_type: str,
    resource_type: str | None = None,
    details: dict | None = None,
) -> None:
    """Log a usage event. Called from API (frontend) and from backend after actions."""
    db = await get_db()
    try:
        details_json = json.dumps(details) if details else None
        await db.execute(
            """INSERT INTO usage_events (user_id, event_type, resource_type, details_json)
               VALUES (?, ?, ?, ?)""",
            (user_id, event_type, resource_type or "", details_json),
        )
        await db.commit()
    finally:
        await db.close()
