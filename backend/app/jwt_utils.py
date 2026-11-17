"""JWT helpers - no imports from routers to avoid circular deps."""
import os
import jwt
from datetime import datetime, timedelta

_WEAK_JWT_SECRETS = frozenset({
    "",
    "dev-jwt-secret-not-for-production",
    "your_jwt_secret_here",
    "changeme",
    "secret",
})
_raw_jwt = (os.getenv("JWT_SECRET") or "").strip()
if _raw_jwt in _WEAK_JWT_SECRETS:
    raise RuntimeError(
        "JWT_SECRET is missing or is a known insecure default. "
        "Set a long random value in backend/.env "
        '(python -c "import secrets; print(secrets.token_hex(32))").'
    )
JWT_SECRET = _raw_jwt
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24 * 7  # 7 days
COOKIE_NAME = "yucg_session"
PENDING_2FA_COOKIE = "yucg_2fa_pending"


def session_cookie_kwargs() -> dict:
    return {
        "key": COOKIE_NAME,
        "httponly": True,
        "samesite": "lax",
        "secure": (os.getenv("FRONTEND_URL") or "").startswith("https"),
        "max_age": JWT_EXPIRY_HOURS * 3600,
        "path": "/",
    }


def create_token(
    user_id: int,
    email: str,
    name: str | None = None,
    picture: str | None = None,
    role: str = "standard",
    extra: dict | None = None,
    expiry_hours: float | None = None,
) -> str:
    now = datetime.utcnow()
    hours = JWT_EXPIRY_HOURS if expiry_hours is None else expiry_hours
    payload = {
        "sub": str(user_id),
        "email": email,
        "name": name,
        "picture": picture,
        "role": role,
        "exp": now + timedelta(hours=hours),
        "iat": now,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict | None:
    """A member session. A token with an audience (the alumni session) is
    refused here: PyJWT rejects an ``aud`` claim when none is expected."""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except Exception:
        return None


# --- Alumni sessions (app/routers/alumni.py) ----------------------------------
# A separate cookie, scoped to the alumni API, holding a token with its own
# audience: it is never a member session anywhere (decode_token refuses it).
ALUMNI_COOKIE = "yucg_alumni"
ALUMNI_AUDIENCE = "yucg-alumni"
ALUMNI_EXPIRY_HOURS = 24


def alumni_cookie_kwargs() -> dict:
    return {
        "key": ALUMNI_COOKIE,
        "httponly": True,
        "samesite": "lax",
        "secure": (os.getenv("FRONTEND_URL") or "").startswith("https"),
        "max_age": ALUMNI_EXPIRY_HOURS * 3600,
        "path": "/api/alumni",
    }


def create_alumni_token(account_id: int) -> str:
    now = datetime.utcnow()
    payload = {"sub": str(account_id), "aud": ALUMNI_AUDIENCE, "iat": now,
               "exp": now + timedelta(hours=ALUMNI_EXPIRY_HOURS)}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_alumni_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM], audience=ALUMNI_AUDIENCE)
    except Exception:
        return None


def end_sessions(response):
    """Sign-out from either area ends both: one YUCG Connect sign-in set them
    (app/routers/auth.py), so neither outlives the other on a shared computer."""
    response.delete_cookie(COOKIE_NAME, path="/")
    response.delete_cookie(PENDING_2FA_COOKIE, path="/")
    response.delete_cookie(ALUMNI_COOKIE, path="/api/alumni")
    return response
