"""
Auth API - Google OAuth 2.0 + JWT
"""
import logging
import os
import secrets

logger = logging.getLogger(__name__)
from urllib.parse import urlencode
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
import httpx
from datetime import datetime, timedelta
from app.database import get_db, row_to_dict
from app.auth_deps import get_current_user, get_current_user_optional
from app.jwt_utils import JWT_SECRET, PENDING_2FA_COOKIE, create_token, decode_token, session_cookie_kwargs
from app.token_crypto import encrypt_token

router = APIRouter()

GOOGLE_CLIENT_ID = (os.getenv("GOOGLE_CLIENT_ID") or "").strip()
GOOGLE_CLIENT_SECRET = (os.getenv("GOOGLE_CLIENT_SECRET") or "").strip()
BACKEND_URL = (os.getenv("BACKEND_URL") or "http://localhost:8000").strip().rstrip("/")
GOOGLE_REDIRECT_URI = (
    os.getenv("GOOGLE_REDIRECT_URI") or f"{BACKEND_URL}/api/auth/google/callback"
).strip()
FRONTEND_URL = (os.getenv("FRONTEND_URL") or "http://localhost:5173").strip()


async def _start_oauth(purpose: str = "identity", user_id: int | None = None, invitation: str | None = None):
    import time
    from app.routers.invitations import digest
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(503, "Google sign-in is not configured")
    state, browser = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    db = await get_db()
    try:
        invite_id = None
        if invitation:
            row = await (await db.execute("SELECT id FROM membership_invitations WHERE token_hash=? AND state='pending' AND expires_at>?", (digest(invitation),int(time.time())))).fetchone()
            if not row:
                raise HTTPException(400, "Invitation expired, revoked or already accepted")
            invite_id = row["id"]
        await db.execute("DELETE FROM oauth_challenges WHERE expires_at<=?", (int(time.time()),))
        await db.execute("INSERT INTO oauth_challenges(state_hash,browser_hash,purpose,user_id,invitation_id,expires_at) VALUES (?,?,?,?,?,?)", (digest(state),digest(browser),purpose,user_id,invite_id,int(time.time())+600))
        await db.commit()
    finally:
        await db.close()
    scope = "openid email profile"
    if purpose == "gmail":
        scope += " https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/gmail.readonly"
    params = {"client_id":GOOGLE_CLIENT_ID,"redirect_uri":GOOGLE_REDIRECT_URI,
              "response_type":"code","scope":scope,"state":state}
    if purpose == "gmail":
        params.update(access_type="offline",prompt="consent")
    response = RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params))
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie("yucg_oauth_browser",browser,max_age=600,httponly=True,samesite="lax",secure=BACKEND_URL.startswith("https"),path="/api/auth")
    return response


@router.get("/google")
async def google_login(invitation: str | None = None):
    return await _start_oauth(invitation=invitation)


@router.get("/gmail/connect")
async def connect_gmail(user: dict = Depends(get_current_user)):
    # Return the URL and cookie together so fetch can initiate a browser-bound flow.
    from fastapi.responses import JSONResponse
    redirect = await _start_oauth("gmail", user["id"])
    response = JSONResponse({"redirect_url":redirect.headers["location"]})
    response.headers.append("set-cookie",redirect.headers["set-cookie"])
    return response


@router.get("/gmail/status")
async def gmail_status(user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        row = await (await db.execute("SELECT access_token,refresh_token FROM users WHERE id=?",(user["id"],))).fetchone()
        return {"connected":bool(row and (row["access_token"] or row["refresh_token"]))}
    finally:
        await db.close()


@router.delete("/gmail/disconnect")
async def disconnect_gmail(user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        await db.execute("UPDATE users SET access_token=NULL,refresh_token=NULL,token_expires_at=NULL WHERE id=?",(user["id"],))
        await db.execute("INSERT INTO audit_log(user_id,action,resource_type,resource_id,details) VALUES (?,'gmail_disconnect','user',?,'Disconnected Gmail locally')",(user["id"],str(user["id"])))
        await db.commit()
        return {"ok":True}
    finally:
        await db.close()


@router.get("/google/callback")
async def google_callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None):
    import time
    from app.routers.invitations import digest
    browser = request.cookies.get("yucg_oauth_browser", "")
    challenge = None
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        row = await (await db.execute("SELECT * FROM oauth_challenges WHERE state_hash=? AND browser_hash=? AND expires_at>?",(digest(state or ""),digest(browser),int(time.time())))).fetchone()
        if row and browser:
            challenge = dict(row)
            await db.execute("DELETE FROM oauth_challenges WHERE state_hash=?",(row["state_hash"],))
        await db.commit()
    finally:
        await db.close()
    if not challenge or not code or error:
        return RedirectResponse(f"{FRONTEND_URL}/login?error=invalid_callback")
    try:
        response = await _do_google_callback(code, challenge)
    except Exception:
        logger.exception("Google callback failed")
        response = RedirectResponse(f"{FRONTEND_URL}/login?error=callback_failed")
    response.delete_cookie("yucg_oauth_browser",path="/api/auth")
    return response


async def _do_google_callback(code: str, challenge: dict | None = None):
    challenge = challenge or {"purpose": "identity"}
    async with httpx.AsyncClient(timeout=15.0) as client:
        token_res = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": GOOGLE_REDIRECT_URI,
                "grant_type": "authorization_code",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if token_res.status_code != 200:
            try:
                err_body = token_res.text[:500]
            except Exception:
                err_body = ""
            logger.warning(
                "Google token exchange failed: status=%s body=%s",
                token_res.status_code,
                err_body,
            )
            return RedirectResponse(url=f"{FRONTEND_URL}/login?error=token_exchange_failed")

        tokens = token_res.json()
        access_token = tokens.get("access_token")
        refresh_token = tokens.get("refresh_token")
        expires_in = tokens.get("expires_in", 3600)
        if not access_token:
            return RedirectResponse(url=f"{FRONTEND_URL}/login?error=no_access_token")

        user_res = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if user_res.status_code != 200:
            return RedirectResponse(url=f"{FRONTEND_URL}/login?error=userinfo_failed")

        user_info = user_res.json()
        email = (user_info.get("email") or "").strip().lower()
        name = user_info.get("name")
        picture = user_info.get("picture")
        google_id = user_info.get("id")

        if user_info.get("verified_email") is not True:
            return RedirectResponse(url=f"{FRONTEND_URL}/login?error=email_not_verified")

        if not google_id:
            return RedirectResponse(f"{FRONTEND_URL}/login?error=identity_missing")

        if not email:
            return RedirectResponse(url=f"{FRONTEND_URL}/login?error=no_email")

        if not email.lower().endswith("@yale.edu"):
            return RedirectResponse(url=f"{FRONTEND_URL}/login?error=domain_not_allowed")

    from datetime import datetime as dt
    token_expires_at = (dt.utcnow().timestamp() + expires_in) if expires_in else None
    stored_access = encrypt_token(access_token)
    stored_refresh = encrypt_token(refresh_token)

    totp_secret = None
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute(
            "SELECT id, email, name, picture, google_id, role, is_active, totp_secret FROM users WHERE email = ?",
            (email,),
        )
        row = await cursor.fetchone()
        invitation = None
        if challenge.get("invitation_id"):
            import time
            invitation = await (await db.execute("SELECT * FROM membership_invitations WHERE id=? AND email=? AND state='pending' AND expires_at>?", (challenge["invitation_id"],email,int(time.time())))).fetchone()
            if not invitation:
                return RedirectResponse(f"{FRONTEND_URL}/login?error=invitation_invalid")
        if row:
            row = row_to_dict(row)
            if not row.get("is_active"):
                return RedirectResponse(f"{FRONTEND_URL}/login?error=account_deactivated")
            if row.get("google_id") and row["google_id"] != google_id:
                return RedirectResponse(f"{FRONTEND_URL}/login?error=identity_mismatch")
            user_id, role = row["id"], row.get("role") or "standard"
        elif invitation and challenge["purpose"] == "identity":
            cursor = await db.execute("INSERT INTO users(email,name,role,google_id,picture) VALUES (?,?,?,?,?)",(email,name,'standard',google_id,picture))
            user_id, role = cursor.lastrowid, 'standard'
            row = None
        else:
            return RedirectResponse(f"{FRONTEND_URL}/login?error=invitation_required")
        if challenge["purpose"] == "gmail":
            if challenge.get("user_id") != user_id:
                return RedirectResponse(f"{FRONTEND_URL}/profile?error=gmail_account_mismatch")
            required = {"https://www.googleapis.com/auth/gmail.send", "https://www.googleapis.com/auth/gmail.readonly"}
            if not required.issubset(set(tokens.get("scope", "").split())):
                return RedirectResponse(f"{FRONTEND_URL}/profile?error=gmail_scopes_required")
            await db.execute("UPDATE users SET access_token=?,refresh_token=COALESCE(?,refresh_token),token_expires_at=? WHERE id=?",(stored_access,stored_refresh,token_expires_at,user_id))
        await db.execute("UPDATE users SET name=?,picture=?,google_id=? WHERE id=?",(name,picture,google_id,user_id))
        if invitation:
            import json
            import time
            for project_id in json.loads(invitation["project_ids"]):
                await db.execute("INSERT OR IGNORE INTO user_project_assignments(user_id,project_id,role_in_project) VALUES (?,?,'member')",(user_id,project_id))
            await db.execute("UPDATE membership_invitations SET state='accepted',accepted_at=?,accepted_user_id=? WHERE id=?",(int(time.time()),user_id,invitation["id"]))
        if invitation:
            await db.execute("INSERT INTO audit_log(user_id,action,resource_type,resource_id,details) VALUES (?,'invitation_accept','invitation',?,'Verified invited Google identity accepted membership')",(user_id,str(invitation['id'])))
        if challenge["purpose"] == "gmail":
            await db.execute("INSERT INTO audit_log(user_id,action,resource_type,resource_id,details) VALUES (?,'gmail_connect','user',?,'Connected own verified Google account')",(user_id,str(user_id)))
        totp_secret = (row or {}).get("totp_secret")
        await db.execute(
            "INSERT INTO login_log (user_id, email, name) VALUES (?, ?, ?)",
            (user_id, email, name),
        )
        await db.commit()
    finally:
        await db.close()

    if challenge["purpose"] == "gmail":
        return RedirectResponse(f"{FRONTEND_URL}/profile?tab=integrations")

    if totp_secret:
        pending = create_token(user_id, email, name, picture, role, extra={"2fa": "pending"}, expiry_hours=0.25)
        resp = RedirectResponse(url=f"{FRONTEND_URL}/login?need_2fa=1")
        resp.set_cookie(
            PENDING_2FA_COOKIE,
            pending,
            httponly=True,
            samesite="lax",
            secure=(FRONTEND_URL.startswith("https")),
            max_age=900,
            path="/",
        )
        return resp
    token = create_token(user_id, email, name, picture, role)
    resp = RedirectResponse(url=f"{FRONTEND_URL}/")
    ck = session_cookie_kwargs()
    resp.set_cookie(ck.pop("key"), token, **ck)
    return resp


def _dev_login_email() -> str:
    """The account a local dev server signs in as, or empty when disabled.

    Google will only redirect to the registered production callback, so a
    frontend served from localhost can never complete a real sign-in and the
    whole app is unreviewable locally. This is the way in, and it is fenced
    twice: the variable is unset everywhere except a developer's own machine,
    and the route additionally refuses any request that did not arrive on a
    loopback host. Production sets neither.
    """
    return (os.getenv("DEV_LOGIN_EMAIL") or "").strip()


@router.post("/dev-login")
async def dev_login(request: Request):
    """Sign in as DEV_LOGIN_EMAIL, on a loopback host only."""
    email = _dev_login_email()
    if not email:
        raise HTTPException(404, "Not found")
    host = (request.headers.get("host") or "").split(":")[0].lower()
    if host not in {"localhost", "127.0.0.1", "[::1]", "::1"}:
        # A misconfigured deployment that set the variable still cannot be
        # signed into from outside the machine it runs on.
        raise HTTPException(404, "Not found")

    db = await get_db()
    try:
        row = await (await db.execute(
            "SELECT id, email, name, picture, role FROM users WHERE email = ? AND is_active = 1",
            (email,),
        )).fetchone()
        if not row:
            cur = await db.execute(
                "INSERT INTO users (email, name, role, is_active) VALUES (?, ?, 'admin', 1)",
                (email, email.split("@")[0]),
            )
            await db.commit()
            row = await (await db.execute(
                "SELECT id, email, name, picture, role FROM users WHERE id = ?", (cur.lastrowid,),
            )).fetchone()
    finally:
        await db.close()

    token = create_token(row["id"], row["email"], row["name"], row["picture"], row["role"])
    resp = JSONResponse({"ok": True, "email": row["email"], "dev": True})
    ck = session_cookie_kwargs()
    resp.set_cookie(ck.pop("key"), token, **ck)
    return resp


@router.get("/me")
async def get_me(user: dict | None = Depends(get_current_user_optional)):
    """Current user from Authorization: Bearer or X-API-Key."""
    if not user:
        return {"authenticated": False, "user": None}
    return {
        "authenticated": True,
        "user": {
            "id": user["id"],
            "email": user.get("email"),
            "name": user.get("name"),
            "picture": user.get("picture"),
            "role": user.get("role") or "standard",
        },
    }


class TwoFactorLogin(BaseModel):
    code: str


@router.post("/2fa/login")
async def complete_2fa_login(payload: TwoFactorLogin, request: Request):
    import pyotp
    from fastapi.responses import JSONResponse

    pending = request.cookies.get(PENDING_2FA_COOKIE)
    decoded = decode_token(pending) if pending else None
    if not decoded or decoded.get("2fa") != "pending" or not decoded.get("sub"):
        raise HTTPException(401, "2FA session expired. Sign in again.")
    user_id = int(decoded["sub"])
    db = await get_db()
    try:
        cur = await db.execute("SELECT totp_secret, email, name, picture, role FROM users WHERE id = ? AND is_active=1", (user_id,))
        row = row_to_dict(await cur.fetchone())
    finally:
        await db.close()
    if not row or not row.get("totp_secret"):
        raise HTTPException(400, "2FA is not enabled")
    if not pyotp.TOTP(row["totp_secret"]).verify(payload.code.strip(), valid_window=1):
        raise HTTPException(400, "Invalid code")
    token = create_token(user_id, row["email"], row.get("name"), row.get("picture"), row.get("role") or "standard")
    resp = JSONResponse({"ok": True})
    ck = session_cookie_kwargs()
    resp.set_cookie(ck.pop("key"), token, **ck)
    resp.delete_cookie(PENDING_2FA_COOKIE, path="/")
    return resp


@router.post("/logout")
async def logout():
    from fastapi.responses import JSONResponse
    from app.jwt_utils import COOKIE_NAME

    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE_NAME, path="/")
    resp.delete_cookie(PENDING_2FA_COOKIE, path="/")
    return resp


@router.get("/notification-preferences")
async def get_my_notification_prefs(user: dict = Depends(get_current_user)):
    """Get current user's notification preferences."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT admin_digest, campaign_summary FROM notification_preferences WHERE user_id = ?",
            (user["id"],),
        )
        row = await cursor.fetchone()
        if row:
            return {"admin_digest": bool(row["admin_digest"]), "campaign_summary": bool(row["campaign_summary"])}
        return {"admin_digest": True, "campaign_summary": False}
    finally:
        await db.close()


class NotificationPrefsBody(BaseModel):
    admin_digest: bool | None = None
    campaign_summary: bool | None = None


@router.put("/notification-preferences")
async def update_my_notification_prefs(
    payload: NotificationPrefsBody,
    user: dict = Depends(get_current_user),
):
    """Update current user's notification preferences."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT admin_digest, campaign_summary FROM notification_preferences WHERE user_id = ?",
            (user["id"],),
        )
        row = await cursor.fetchone()
        row = row_to_dict(row) if row else None
        ad = payload.admin_digest if payload.admin_digest is not None else (bool(row.get("admin_digest")) if row else True)
        cs = payload.campaign_summary if payload.campaign_summary is not None else (bool(row.get("campaign_summary")) if row else False)
        await db.execute(
            "INSERT OR REPLACE INTO notification_preferences (user_id, admin_digest, campaign_summary) VALUES (?, ?, ?)",
            (user["id"], 1 if ad else 0, 1 if cs else 0),
        )
        await db.commit()
        return {"ok": True}
    finally:
        await db.close()


# --- User profile ---
class ProfileUpdate(BaseModel):
    projects: str | None = None
    experience: str | None = None
    role_title: str | None = None
    linkedin_url: str | None = None
    slack_handle: str | None = None
    other_handles: str | None = None


@router.get("/profile")
async def get_my_profile(user: dict = Depends(get_current_user)):
    """Get current user's profile (projects, experience, role, handles)."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM user_profiles WHERE user_id = ?",
            (user["id"],),
        )
        row = await cursor.fetchone()
        if row:
            return row_to_dict(row)
        return {"user_id": user["id"], "projects": None, "experience": None, "role_title": None, "linkedin_url": None, "slack_handle": None, "other_handles": None}
    finally:
        await db.close()


@router.put("/profile")
async def update_my_profile(payload: ProfileUpdate, user: dict = Depends(get_current_user)):
    """Update current user's profile."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM user_profiles WHERE user_id = ?", (user["id"],))
        existing = await cursor.fetchone()
        existing = row_to_dict(existing) if existing else {}
        proj = payload.projects if payload.projects is not None else (existing.get("projects") or "")
        exp = payload.experience if payload.experience is not None else (existing.get("experience") or "")
        role = payload.role_title if payload.role_title is not None else (existing.get("role_title") or "")
        li = payload.linkedin_url if payload.linkedin_url is not None else (existing.get("linkedin_url") or "")
        slack = payload.slack_handle if payload.slack_handle is not None else (existing.get("slack_handle") or "")
        other = payload.other_handles if payload.other_handles is not None else (existing.get("other_handles") or "")
        await db.execute(
            """INSERT OR REPLACE INTO user_profiles (user_id, projects, experience, role_title, linkedin_url, slack_handle, other_handles, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
            (user["id"], proj, exp, role, li, slack, other),
        )
        await db.commit()
        return {"ok": True}
    finally:
        await db.close()


# --- Slack OAuth ---
# Bot scopes. chat:write and im:write are what the daily digest needs: without
# them the install succeeds and every DM then fails with missing_scope.
# app_mentions:read plus im:history/im:read are what make the helper a bot a
# member can talk to rather than a slash command they must remember.
SLACK_SCOPES = (
    "chat:write,im:write,im:history,im:read,app_mentions:read,"
    "users:read,users:read.email,team:read"
)
SLACK_STATE_TTL = 600


def _get_slack_credentials() -> tuple[str, str]:
    """Read Slack credentials at request time so they always reflect loaded .env."""
    cid = (os.getenv("SLACK_CLIENT_ID") or "").strip()
    secret = (os.getenv("SLACK_CLIENT_SECRET") or "").strip()
    return cid, secret


@router.get("/slack/connect")
async def slack_connect(user: dict = Depends(get_current_user)):
    """Return Slack OAuth URL for the frontend to redirect to. Requires auth."""
    import time
    from app.routers.invitations import digest

    SLACK_CLIENT_ID, SLACK_CLIENT_SECRET = _get_slack_credentials()
    if not SLACK_CLIENT_ID or not SLACK_CLIENT_SECRET:
        raise HTTPException(
            400,
            "Slack integration not configured. Add SLACK_CLIENT_ID and SLACK_CLIENT_SECRET to backend/.env. "
            f"(Debug: client_id present={bool(SLACK_CLIENT_ID)}, client_secret present={bool(SLACK_CLIENT_SECRET)})"
        )
    # The state lives in the database and is bound to a browser secret, the
    # same way the Google flow works. It used to be a process dictionary, so a
    # container replacement or a second worker between connect and callback
    # turned a completed install into "slack=error".
    state, browser = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    db = await get_db()
    try:
        await db.execute("DELETE FROM oauth_challenges WHERE expires_at<=?", (int(time.time()),))
        await db.execute(
            "INSERT INTO oauth_challenges(state_hash,browser_hash,purpose,user_id,invitation_id,expires_at) VALUES (?,?,?,?,?,?)",
            (digest(state), digest(browser), "slack", user["id"], None, int(time.time()) + SLACK_STATE_TTL),
        )
        await db.commit()
    finally:
        await db.close()
    redirect_uri = f"{BACKEND_URL}/api/auth/slack/callback"
    params = {
        "client_id": SLACK_CLIENT_ID,
        "scope": SLACK_SCOPES,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    url = "https://slack.com/oauth/v2/authorize?" + urlencode(params)
    response = JSONResponse({"redirect_url": url})
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(
        "yucg_slack_browser", browser, max_age=SLACK_STATE_TTL, httponly=True,
        samesite="lax", secure=BACKEND_URL.startswith("https"), path="/api/auth",
    )
    return response


@router.get("/slack/callback")
async def slack_callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None):
    """Exchange Slack OAuth code for token, store, redirect to frontend."""
    import time
    from app.routers.invitations import digest
    def failed(reason: str, detail: str = "") -> RedirectResponse:
        # A silent redirect to ?slack=error costs a round trip to diagnose.
        logger.warning("Slack callback rejected (%s): %s", reason, detail or "no detail")
        response = RedirectResponse(url=f"{FRONTEND_URL}/profile?slack={reason}")
        response.delete_cookie("yucg_slack_browser", path="/api/auth")
        return response

    if error:
        return failed("denied")
    browser = request.cookies.get("yucg_slack_browser", "")
    user_id = None
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        row = await (await db.execute(
            "SELECT * FROM oauth_challenges WHERE state_hash=? AND browser_hash=? AND purpose='slack' AND expires_at>?",
            (digest(state or ""), digest(browser), int(time.time())),
        )).fetchone()
        if row and browser:
            user_id = row["user_id"]
            # Single use: a replayed code cannot install against another member.
            await db.execute("DELETE FROM oauth_challenges WHERE state_hash=?", (row["state_hash"],))
        await db.commit()
    finally:
        await db.close()
    if not code:
        return failed("error", "Slack returned no code")
    if not user_id:
        return failed(
            "error",
            "no live state for this browser — the connect request and this callback must reach "
            f"the same database; callback host is BACKEND_URL={BACKEND_URL}",
        )

    redirect_uri = f"{BACKEND_URL}/api/auth/slack/callback"
    slack_client_id, slack_client_secret = _get_slack_credentials()
    async with httpx.AsyncClient(timeout=15.0) as client:
        res = await client.post(
            "https://slack.com/api/oauth.v2.access",
            data={
                "client_id": slack_client_id,
                "client_secret": slack_client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if res.status_code != 200:
        return failed("error", f"Slack token exchange HTTP {res.status_code}")
    data = res.json()
    if not data.get("ok") or not data.get("access_token"):
        return failed("error", f"Slack token exchange said {data.get('error') or 'no access_token'}")
    access_token = data.get("access_token")
    team = data.get("team") or {}
    team_id = team.get("id")
    team_name = team.get("name")
    authed_user = data.get("authed_user") or {}
    user_slack_id = authed_user.get("id")
    scope = data.get("scope")
    # The app uses token rotation, so the install answers with a refresh token
    # and a lifetime rather than a token that lasts forever. Both are part of
    # the credential; a workspace without rotation simply sends neither.
    refresh_token = data.get("refresh_token") or None
    expires_in = data.get("expires_in")
    expires_at = (time.time() + float(expires_in)) if expires_in else None

    db = await get_db()
    try:
        await db.execute(
            """INSERT OR REPLACE INTO user_slack_tokens
               (user_id, access_token, refresh_token, token_expires_at, team_id, team_name, user_slack_id, scope, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
            (user_id, encrypt_token(access_token),
             encrypt_token(refresh_token) if refresh_token else None, expires_at,
             team_id, team_name, user_slack_id, scope),
        )
        await db.commit()
    finally:
        await db.close()
    response = RedirectResponse(url=f"{FRONTEND_URL}/profile?slack=connected")
    response.delete_cookie("yucg_slack_browser", path="/api/auth")
    return response


@router.get("/slack/status")
async def slack_status(user: dict = Depends(get_current_user)):
    """Check if current user has Slack connected."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT team_name FROM user_slack_tokens WHERE user_id = ?",
            (user["id"],),
        )
        row = await cursor.fetchone()
        if row:
            return {"connected": True, "team_name": row_to_dict(row).get("team_name")}
        return {"connected": False}
    finally:
        await db.close()


@router.delete("/slack/disconnect")
async def slack_disconnect(user: dict = Depends(get_current_user)):
    """Disconnect Slack for current user."""
    db = await get_db()
    try:
        await db.execute("DELETE FROM user_slack_tokens WHERE user_id = ?", (user["id"],))
        await db.commit()
        return {"ok": True}
    finally:
        await db.close()


# --- My project assignments (for profile) ---
@router.get("/my-projects")
async def get_my_projects(user: dict = Depends(get_current_user)):
    """List current user's project assignments (e.g. Spring 2026 - Project Lego)."""
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT p.id, p.name, p.semester, upa.role_in_project
               FROM user_project_assignments upa
               JOIN projects p ON p.id = upa.project_id
               WHERE upa.user_id = ?
               ORDER BY p.semester DESC, p.name""",
            (user["id"],),
        )
        rows = await cursor.fetchall()
        return [row_to_dict(r) for r in rows]
    finally:
        await db.close()


# --- Team / community (who's online, roles) ---
@router.get("/team")
async def get_team(user: dict = Depends(get_current_user)):
    """List team members with roles, last seen, and project assignments. For community sidebar."""
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT u.id, u.email, u.name, u.picture, u.role,
               (SELECT ll.created_at FROM login_log ll WHERE ll.user_id = u.id ORDER BY ll.created_at DESC LIMIT 1) as last_seen
               FROM users u WHERE COALESCE(u.is_active, 1) = 1 ORDER BY u.name, u.email"""
        )
        rows = await cursor.fetchall()
        users = [row_to_dict(r) for r in rows]
    except Exception:
        cursor = await db.execute(
            "SELECT id, email, name, picture, role FROM users ORDER BY name, email"
        )
        rows = await cursor.fetchall()
        users = [row_to_dict(r) for r in rows]

    # Add project assignments for each user
    try:
        for u in users:
            cursor = await db.execute(
                """SELECT p.name, p.semester, upa.role_in_project
                   FROM user_project_assignments upa
                   JOIN projects p ON p.id = upa.project_id
                   WHERE upa.user_id = ?
                   ORDER BY p.semester DESC, p.name""",
                (u["id"],),
            )
            proj_rows = await cursor.fetchall()
            u["project_assignments"] = [row_to_dict(r) for r in proj_rows]
    except Exception:
        for u in users:
            u["project_assignments"] = []
    finally:
        await db.close()
    return users
