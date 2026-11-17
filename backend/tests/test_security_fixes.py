"""Regression tests for the 2026-11-17 security review (docs/night/security.md),
each with the forbidden case and the allowed one: API-key scope, development
sign-in in production, two-factor attempt limit, upload and Slack body limits,
and the crawler's refusal of non-public destinations. Synthetic data only.
From backend/: python tests/test_security_fixes.py
"""
import asyncio
import hashlib
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_tmp = Path(tempfile.mkdtemp())
os.environ["JWT_SECRET"] = "security-fixes-test-secret-0123456789ab"
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 't.db'}"

import pyotp  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from app.database import get_db, init_db  # noqa: E402
from app.jwt_utils import PENDING_2FA_COOKIE, create_token  # noqa: E402
from app.routers import attachments  # noqa: E402
from app.services.research_providers import refuse_private_destination  # noqa: E402

ADMIN, MEMBER = 1, 2
SECRET = pyotp.random_base32()
KEY = "yucg_test-admin-key-not-real-0123456789"


async def setup() -> None:
    await init_db()
    db = await get_db()
    try:
        await db.executemany("INSERT INTO users (id, email, name, role, is_active, totp_secret) VALUES (?, ?, ?, ?, 1, ?)",
                             [(ADMIN, "admin@yale.edu", "Admin", "admin", SECRET), (MEMBER, "ben@yale.edu", "Ben", "standard", None)])
        await db.execute("INSERT INTO api_keys (user_id, key_hash, key_prefix, name, scopes) VALUES (?, ?, 'yucg_test', 'Sheet', 'contacts:read')",
                         (ADMIN, hashlib.sha256(KEY.encode()).hexdigest()))
        await db.commit()
    finally:
        await db.close()


def client() -> TestClient:
    return TestClient(main.app, raise_server_exceptions=False)


def api_key_scope() -> None:
    """An admin's contacts:read key reads contacts and does nothing else."""
    keyed = client()
    keyed.headers["X-API-Key"] = KEY
    assert keyed.get("/api/contacts").status_code == 200
    for method, path in (("POST", "/api/contacts"), ("GET", "/api/admin/users"), ("GET", "/api/admin/api-keys"),
                         ("POST", "/api/admin/api-keys"), ("GET", "/api/campaigns"), ("GET", "/api/auth/profile")):
        status = keyed.request(method, path, json={"name": "x"}).status_code
        assert status == 403, (method, path, status)
    # The same admin in a browser session still administers.
    browser = client()
    browser.cookies.set("yucg_session", create_token(ADMIN, "admin@yale.edu", role="admin"))
    assert browser.get("/api/admin/api-keys").status_code == 200


def dev_login_never_in_production() -> None:
    local = {"host": "localhost"}
    with patch.dict(os.environ, {"DEV_LOGIN_EMAIL": "dev@yale.edu", "APP_ENV": "production"}):
        assert client().post("/api/auth/dev-login", headers=local).status_code == 404
    with patch.dict(os.environ, {"DEV_LOGIN_EMAIL": "dev@yale.edu", "APP_ENV": ""}):
        assert client().post("/api/auth/dev-login", headers={"host": "example.test"}).status_code == 404
        assert client().post("/api/auth/dev-login", headers=local).status_code == 200


def two_factor_attempts() -> None:
    def pending(jti: str | None) -> TestClient:
        browser = client()
        extra = {"2fa": "pending", **({"jti": jti} if jti else {})}
        browser.cookies.set(PENDING_2FA_COOKIE, create_token(ADMIN, "admin@yale.edu", role="admin", extra=extra, expiry_hours=0.25))
        return browser

    good = lambda: pyotp.TOTP(SECRET).now()  # noqa: E731
    wrong = lambda: f"{(int(good()) + 500000) % 1000000:06d}"  # noqa: E731
    # Five wrong codes, then even the right one is refused: Google sign-in starts again.
    guesser = pending("guess")
    for _ in range(5):
        assert guesser.post("/api/auth/2fa/login", json={"code": wrong()}).status_code == 400
    assert guesser.post("/api/auth/2fa/login", json={"code": good()}).status_code == 401
    # A fresh pending sign-in: a typo, then the right code, signs in.
    member = pending("fresh")
    assert member.post("/api/auth/2fa/login", json={"code": wrong()}).status_code == 400
    done = member.post("/api/auth/2fa/login", json={"code": good()})
    assert done.status_code == 200 and "yucg_session=" in done.headers["set-cookie"]
    # The pending token is single use, and one without an id (an old release's) is refused.
    assert pending("fresh").post("/api/auth/2fa/login", json={"code": good()}).status_code == 401
    assert pending(None).post("/api/auth/2fa/login", json={"code": good()}).status_code == 401


def upload_and_slack_limits() -> None:
    member = client()
    member.cookies.set("yucg_session", create_token(MEMBER, "ben@yale.edu"))
    with patch.object(attachments, "ATTACHMENTS_DIR", _tmp / "attachments"):
        too_big = b"%PDF" + b"0" * attachments.MAX_FILE_SIZE
        assert member.post("/api/attachments", files={"file": ("big.pdf", too_big, "application/pdf")}).status_code == 413
        assert member.post("/api/attachments", files={"file": ("small.pdf", b"%PDF-1.4 ok", "application/pdf")}).status_code == 200
    # Slack's routes refuse a huge unsigned body before reading it, and still check the signature of a normal one.
    huge = b"{" + b" " * (300 * 1024) + b"}"
    for path in ("/api/slack/events", "/api/slack/commands"):
        assert client().post(path, content=huge).status_code == 413, path
    assert client().post("/api/slack/events", json={"type": "event_callback"}).status_code == 401
    assert client().post("/api/slack/commands", data={"text": "help"}).status_code == 401


async def crawler_destinations() -> None:
    def resolving_to(ip: str):
        return patch("socket.getaddrinfo", lambda host, port, *a, **k: [(2, 1, 6, "", (ip, port))])

    async def hop(url: str) -> bool:
        try:
            await refuse_private_destination(httpx.Request("GET", url))
            return True
        except httpx.ConnectError:
            return False

    with resolving_to("93.184.216.34"):
        assert await hop("https://acme-example.com/team")
        for url in ("http://127.0.0.1/", "http://169.254.169.254/latest/meta-data/", "https://localhost/",
                    "https://acme-example.com:8080/", "file:///etc/passwd"):
            assert not await hop(url), url
    for ip in ("10.0.1.5", "127.0.0.1", "169.254.169.254", "::1"):
        with resolving_to(ip):
            assert not await hop("https://rebound-example.com/"), ip
    # A public page that redirects into the VPC: the second hop is refused.
    def site(request: httpx.Request) -> httpx.Response:
        if request.url.host == "acme-example.com":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
        return httpx.Response(200, text="secret")

    with resolving_to("93.184.216.34"):
        async with httpx.AsyncClient(transport=httpx.MockTransport(site), follow_redirects=True,
                                     event_hooks={"request": [refuse_private_destination]}) as crawler:
            try:
                await crawler.get("https://acme-example.com/team")
                raise AssertionError("redirect into the VPC was followed")
            except httpx.ConnectError:
                pass


def tests() -> None:
    asyncio.run(setup())
    api_key_scope()
    dev_login_never_in_production()
    two_factor_attempts()
    upload_and_slack_limits()
    asyncio.run(crawler_destinations())


if __name__ == "__main__":
    tests()
    print("security fixes: ok")
