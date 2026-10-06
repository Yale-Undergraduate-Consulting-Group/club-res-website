"""Connecting Slack survives a restart, and the install asks for scopes it uses.

Two failures this guards against. The OAuth state lived in a process
dictionary, so a container replacement between pressing Connect and Slack
redirecting back turned a completed install into "slack=error" with no way to
tell why. And the install requested read-only scopes, so every digest DM
failed with missing_scope after an apparently successful connection.
"""
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JWT_SECRET", "slack-integration-test-secret-value")
os.environ["SLACK_CLIENT_ID"] = "4581805345394.10700497468198"
os.environ["SLACK_CLIENT_SECRET"] = "test-client-secret"

from urllib.parse import parse_qs, urlparse  # noqa: E402
import httpx  # noqa: E402
from fastapi import FastAPI  # noqa: E402

from app.database import get_db, init_db  # noqa: E402
from app.jwt_utils import create_token  # noqa: E402

MEMBER = {"id": 1, "email": "member@yale.edu"}


async def connect_stores_state_in_the_database_and_asks_for_send_scopes(client) -> str:
    from app.routers.auth import SLACK_SCOPES

    response = await client.get("/api/auth/slack/connect")
    assert response.status_code == 200, response.text
    url = urlparse(response.json()["redirect_url"])
    query = parse_qs(url.query)
    assert url.netloc == "slack.com" and url.path == "/oauth/v2/authorize"
    assert query["client_id"] == ["4581805345394.10700497468198"]
    assert query["redirect_uri"][0].endswith("/api/auth/slack/callback")
    # Without these the DM in the daily digest cannot be sent.
    assert "chat:write" in SLACK_SCOPES and "im:write" in SLACK_SCOPES
    assert query["scope"] == [SLACK_SCOPES]
    assert "yucg_slack_browser" in response.cookies

    db = await get_db()
    try:
        row = await (await db.execute(
            "SELECT user_id, purpose FROM oauth_challenges WHERE purpose='slack'"
        )).fetchone()
    finally:
        await db.close()
    assert row is not None and row["user_id"] == MEMBER["id"]
    return query["state"][0]


async def a_callback_without_the_matching_browser_is_refused(app, state: str) -> None:
    """A fresh client is a different browser: it holds no cookie, so the state
    alone must not be enough to install against the member who started it."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as other_browser:
        response = await other_browser.get(
            f"/api/auth/slack/callback?code=abc&state={state}", follow_redirects=False
        )
    assert response.status_code in (302, 307)
    assert "slack=error" in response.headers["location"]
    db = await get_db()
    try:
        row = await (await db.execute("SELECT 1 FROM user_slack_tokens")).fetchone()
        state_row = await (await db.execute("SELECT 1 FROM oauth_challenges WHERE purpose='slack'")).fetchone()
    finally:
        await db.close()
    assert row is None, "a state without its browser installed anyway"
    assert state_row is not None, "the rejected attempt consumed the real state"


async def the_stored_state_survives_a_restart(client, state: str) -> None:
    """The dictionary is gone; only the row and the cookie decide."""
    import app.routers.auth as auth_module

    assert not hasattr(auth_module, "_slack_oauth_states")

    exchange = AsyncMock(return_value=httpx.Response(
        200,
        json={
            "ok": True, "access_token": "xoxb-installed", "scope": auth_module.SLACK_SCOPES,
            "team": {"id": "T1", "name": "YUCG"}, "authed_user": {"id": "U1"},
            # Rotation is permanently on for this app: the install answers with
            # a refresh token and a lifetime, not a token that lasts forever.
            "refresh_token": "xoxe-1-refresh", "expires_in": 43200,
        },
    ))
    with patch("httpx.AsyncClient.post", exchange):
        response = await client.get(
            f"/api/auth/slack/callback?code=abc&state={state}", follow_redirects=False
        )
    assert response.status_code in (302, 307)
    assert "slack=connected" in response.headers["location"], response.headers["location"]

    db = await get_db()
    try:
        row = await (await db.execute(
            """SELECT user_id, team_name, user_slack_id, access_token, refresh_token, token_expires_at
               FROM user_slack_tokens"""
        )).fetchone()
        left = await (await db.execute("SELECT 1 FROM oauth_challenges WHERE purpose='slack'")).fetchone()
    finally:
        await db.close()
    assert row["user_id"] == MEMBER["id"] and row["team_name"] == "YUCG"
    assert row["user_slack_id"] == "U1"
    assert row["access_token"] != "xoxb-installed", "the token must be stored encrypted"
    assert row["refresh_token"] and row["refresh_token"] != "xoxe-1-refresh", "the refresh token must be stored encrypted"
    assert row["token_expires_at"] and row["token_expires_at"] > time.time(), "the expiry must be recorded"
    assert left is None, "the state must be single use"


async def a_live_token_is_served_without_a_needless_refresh() -> None:
    from app.services import slack_tokens

    refreshed = AsyncMock()
    with patch("httpx.AsyncClient.post", refreshed):
        token = await slack_tokens.slack_access_token(MEMBER["id"])
    assert token == "xoxb-installed"
    refreshed.assert_not_awaited(), "a token good for 12 hours was exchanged anyway"


async def an_expired_token_is_rotated_and_the_new_pair_stored() -> None:
    """Rotation is the whole point: the digest runs long after the install, so
    the stored access token is dead and must be exchanged before use."""
    from app.services import slack_tokens

    db = await get_db()
    try:
        await db.execute(
            "UPDATE user_slack_tokens SET token_expires_at = ? WHERE user_id = ?",
            (time.time() - 60, MEMBER["id"]),
        )
        await db.commit()
    finally:
        await db.close()

    rotate = AsyncMock(return_value=httpx.Response(200, json={
        "ok": True, "access_token": "xoxb-rotated", "refresh_token": "xoxe-2-refresh",
        "expires_in": 43200,
    }))
    with patch("httpx.AsyncClient.post", rotate):
        token = await slack_tokens.slack_access_token(MEMBER["id"])
    assert token == "xoxb-rotated"
    sent = rotate.await_args.kwargs["data"]
    assert sent["grant_type"] == "refresh_token" and sent["refresh_token"] == "xoxe-1-refresh"

    db = await get_db()
    try:
        row = await (await db.execute(
            "SELECT access_token, refresh_token, token_expires_at FROM user_slack_tokens WHERE user_id = ?",
            (MEMBER["id"],),
        )).fetchone()
    finally:
        await db.close()
    from app.token_crypto import decrypt_token

    assert decrypt_token(row["access_token"]) == "xoxb-rotated"
    assert decrypt_token(row["refresh_token"]) == "xoxe-2-refresh", "the next refresh must use the new token"
    assert row["token_expires_at"] > time.time()

    # A second call now serves the rotated token straight from the row.
    quiet = AsyncMock()
    with patch("httpx.AsyncClient.post", quiet):
        assert await slack_tokens.slack_access_token(MEMBER["id"]) == "xoxb-rotated"
    quiet.assert_not_awaited()


async def a_lost_refresh_race_never_overwrites_the_newer_credential() -> None:
    """A refresh token is single use. If another worker (or a re-install) has
    already rotated the row, this caller's answer is stale and must be dropped
    rather than written over a credential that works."""
    from app.services import slack_tokens
    from app.token_crypto import decrypt_token, encrypt_token

    db = await get_db()
    try:
        await db.execute(
            "UPDATE user_slack_tokens SET token_expires_at = ? WHERE user_id = ?",
            (time.time() - 60, MEMBER["id"]),
        )
        await db.commit()
    finally:
        await db.close()

    async def rotate_then_race(*args, **kwargs):
        # Slack answers our refresh; meanwhile another worker has landed a
        # newer pair against the same row.
        other = await get_db()
        try:
            await other.execute(
                "UPDATE user_slack_tokens SET access_token = ?, refresh_token = ?, token_expires_at = ? WHERE user_id = ?",
                (encrypt_token("xoxb-from-other-worker"), encrypt_token("xoxe-3-refresh"),
                 time.time() + 43200, MEMBER["id"]),
            )
            await other.commit()
        finally:
            await other.close()
        return httpx.Response(200, json={
            "ok": True, "access_token": "xoxb-stale", "refresh_token": "xoxe-stale", "expires_in": 43200,
        })

    with patch("httpx.AsyncClient.post", AsyncMock(side_effect=rotate_then_race)):
        assert await slack_tokens.slack_access_token(MEMBER["id"]) is None

    db = await get_db()
    try:
        row = await (await db.execute(
            "SELECT access_token, refresh_token FROM user_slack_tokens WHERE user_id = ?",
            (MEMBER["id"],),
        )).fetchone()
    finally:
        await db.close()
    assert decrypt_token(row["access_token"]) == "xoxb-from-other-worker"
    assert decrypt_token(row["refresh_token"]) == "xoxe-3-refresh"


async def a_dead_refresh_token_asks_the_member_to_reconnect() -> None:
    from app.services import slack_tokens

    db = await get_db()
    try:
        await db.execute(
            "UPDATE user_slack_tokens SET token_expires_at = ? WHERE user_id = ?",
            (time.time() - 60, MEMBER["id"]),
        )
        await db.commit()
    finally:
        await db.close()

    refused = AsyncMock(return_value=httpx.Response(200, json={"ok": False, "error": "invalid_refresh_token"}))
    with patch("httpx.AsyncClient.post", refused):
        try:
            await slack_tokens.slack_access_token(MEMBER["id"])
            raise AssertionError("a dead credential was reported as usable")
        except slack_tokens.SlackReauthorizationRequired:
            pass


async def an_install_without_rotation_still_works() -> None:
    """A workspace that never opted in sends no refresh token; that token is
    used as-is rather than being treated as expired."""
    from app.services import slack_tokens
    from app.token_crypto import encrypt_token

    db = await get_db()
    try:
        await db.execute(
            "UPDATE user_slack_tokens SET access_token = ?, refresh_token = NULL, token_expires_at = NULL WHERE user_id = ?",
            (encrypt_token("xoxb-long-lived"), MEMBER["id"]),
        )
        await db.commit()
    finally:
        await db.close()
    quiet = AsyncMock()
    with patch("httpx.AsyncClient.post", quiet):
        assert await slack_tokens.slack_access_token(MEMBER["id"]) == "xoxb-long-lived"
    quiet.assert_not_awaited()


async def the_state_cannot_be_replayed(client, state: str) -> None:
    response = await client.get(
        f"/api/auth/slack/callback?code=abc&state={state}", follow_redirects=False
    )
    assert "slack=error" in response.headers["location"]


async def the_digest_job_runs_without_a_slack_install() -> None:
    """It used to raise NameError: the module used os.getenv without importing os,
    so the channel fallback crashed the nightly job."""
    from app.services.notification_digest_job import run_notification_digests

    db = await get_db()
    try:
        await db.execute(
            "INSERT OR REPLACE INTO notification_preferences (user_id, admin_digest, campaign_summary) VALUES (1,1,0)"
        )
        await db.commit()
    finally:
        await db.close()
    os.environ["SLACK_BOT_TOKEN"] = ""
    os.environ["SLACK_DIGEST_CHANNEL_ID"] = ""
    result = await run_notification_digests()
    assert result["sent"] == 0 and result["errors"] == []


async def the_channel_digest_posts_with_a_refreshed_install_token() -> None:
    """The channel fallback used to require a pasted SLACK_BOT_TOKEN, which
    rotation kills after twelve hours. It now refreshes an install instead."""
    from app.services.notification_digest_job import run_notification_digests
    from app.token_crypto import encrypt_token

    db = await get_db()
    try:
        await db.execute(
            """UPDATE user_slack_tokens SET access_token = ?, refresh_token = ?, token_expires_at = ?
               WHERE user_id = ?""",
            (encrypt_token("xoxb-old"), encrypt_token("xoxe-9-refresh"), time.time() - 60, MEMBER["id"]),
        )
        await db.execute(
            "INSERT INTO audit_log (user_id, action, resource_type, resource_id, details) VALUES (1,'campaign_release','campaign','7','Released')"
        )
        await db.commit()
    finally:
        await db.close()
    os.environ["SLACK_BOT_TOKEN"] = ""
    os.environ["SLACK_DIGEST_CHANNEL_ID"] = "C0CLUB"
    posted: list[dict] = []

    async def slack(url, *args, **kwargs):
        if url.endswith("oauth.v2.access"):
            return httpx.Response(200, json={
                "ok": True, "access_token": "xoxb-rotated-for-channel",
                "refresh_token": "xoxe-10-refresh", "expires_in": 43200,
            })
        if url.endswith("conversations.open"):
            # No DM channel, so the digest falls back to the club channel.
            return httpx.Response(200, json={"ok": False, "error": "cannot_dm_bot"})
        posted.append({"json": kwargs.get("json"), "auth": kwargs.get("headers", {}).get("Authorization")})
        return httpx.Response(200, json={"ok": True})

    with patch("httpx.AsyncClient.post", AsyncMock(side_effect=slack)):
        result = await run_notification_digests()

    assert result["sent"] == 1, result
    assert posted and posted[0]["json"]["channel"] == "C0CLUB"
    assert posted[0]["auth"] == "Bearer xoxb-rotated-for-channel"
    os.environ["SLACK_DIGEST_CHANNEL_ID"] = ""


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp) / 'slack.db'}"
        await init_db()
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO users(id,email,name,role,is_active) VALUES (1,'member@yale.edu','Member','admin',1)"
            )
            await db.commit()
        finally:
            await db.close()

        from app.routers.auth import router

        app = FastAPI()
        app.include_router(router, prefix="/api/auth")
        token = create_token(MEMBER["id"], MEMBER["email"], "Member", None, "admin")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        ) as client:
            state = await connect_stores_state_in_the_database_and_asks_for_send_scopes(client)
            await a_callback_without_the_matching_browser_is_refused(app, state)
            await the_stored_state_survives_a_restart(client, state)
            await the_state_cannot_be_replayed(client, state)
        await a_live_token_is_served_without_a_needless_refresh()
        await an_expired_token_is_rotated_and_the_new_pair_stored()
        await a_lost_refresh_race_never_overwrites_the_newer_credential()
        await a_dead_refresh_token_asks_the_member_to_reconnect()
        await an_install_without_rotation_still_works()
        await the_digest_job_runs_without_a_slack_install()
        await the_channel_digest_posts_with_a_refreshed_install_token()
        print("slack integration: ok")


asyncio.run(main())
