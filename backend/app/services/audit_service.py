"""Audit log service - track who changed what, when."""
from app.database import get_db
from typing import Optional


async def log_audit(
    user_id: Optional[int],
    action: str,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    details: Optional[str] = None,
) -> None:
    """Append an audit log entry."""
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO audit_log (user_id, action, resource_type, resource_id, details)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, action, resource_type, resource_id, details),
        )
        await db.commit()
    finally:
        await db.close()
