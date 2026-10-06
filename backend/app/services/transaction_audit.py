"""Audit records that commit or roll back with the protected mutation."""
import json


async def audit(db, user_id, action, resource_type, resource_id, details=None):
    await db.execute(
        """INSERT INTO audit_log(user_id,action,resource_type,resource_id,details)
        VALUES (?,?,?,?,?)""",
        (user_id, action, resource_type, str(resource_id), json.dumps(details or {}, sort_keys=True)),
    )
