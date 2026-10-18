"""Auth dependencies for protected routes."""
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials, APIKeyHeader

from app.jwt_utils import decode_token
from app.database import get_db, row_to_dict

security = HTTPBearer(auto_error=False)
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
COOKIE_NAME = "yucg_session"


async def _user_from_db(user_id: int) -> dict | None:
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, email, name, picture, role, is_active FROM users WHERE id = ?",
            (user_id,),
        )
        row = row_to_dict(await cursor.fetchone())
        if not row:
            return None
        if row.get("is_active") == 0:
            return None
        return {
            "id": row["id"],
            "email": row["email"],
            "name": row.get("name"),
            "picture": row.get("picture"),
            "role": row.get("role") or "standard",
        }
    finally:
        await db.close()


async def _resolve_user(
    credentials: HTTPAuthorizationCredentials | None,
    api_key: str | None,
    request: Request | None = None,
) -> dict | None:
    token = None
    if credentials and credentials.credentials:
        token = credentials.credentials
    elif request is not None:
        token = request.cookies.get(COOKIE_NAME)
    if token:
        payload = decode_token(token)
        if payload and payload.get("sub") and payload.get("2fa") != "pending":
            return await _user_from_db(int(payload["sub"]))
    if api_key:
        db = await get_db()
        try:
            import hashlib
            key_hash = hashlib.sha256(api_key.encode()).hexdigest()
            cursor = await db.execute(
                "SELECT u.id, u.email, u.name, u.role, u.is_active FROM users u JOIN api_keys k ON k.user_id = u.id WHERE k.key_hash = ?",
                (key_hash,),
            )
            row = await cursor.fetchone()
            if row:
                row = row_to_dict(row)
                if row.get("is_active") == 0:
                    return None
                await db.execute(
                    "UPDATE api_keys SET last_used_at = CURRENT_TIMESTAMP WHERE key_hash = ?",
                    (key_hash,),
                )
                await db.commit()
                return {
                    "id": row["id"],
                    "email": row["email"],
                    "name": row.get("name"),
                    "picture": None,
                    "role": row.get("role") or "standard",
                    "api_key": True,
                }
        except Exception:
            return None
        finally:
            await db.close()
    return None


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    api_key: str | None = Depends(api_key_header),
) -> dict:
    user = await _resolve_user(credentials, api_key, request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


async def get_current_user_optional(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    api_key: str | None = Depends(api_key_header),
) -> dict | None:
    return await _resolve_user(credentials, api_key, request)


async def get_current_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
