"""YUCG Connect (app/routers/auth.py, docs/night/security.md): one Google
sign-in, routed by who the verified identity is. Members get the member outcome
(2FA included), plus the alumni session when also on the alumni lists; alumni
who are not members get only the alumni portal; everyone else gets the member
refusal, whatever the alumni lists hold. Google is faked; all data is synthetic.
From backend/: python tests/test_yucg_connect.py
"""
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import httpx
import pyotp

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_tmp = Path(tempfile.mkdtemp())
os.environ["JWT_SECRET"] = "connect-test-secret-0123456789abcdefgh"
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 't.db'}"
os.environ["DOCUMENTS_LOCAL_DIR"] = str(_tmp / "files")
os.environ["GOOGLE_CLIENT_ID"] = "test-client"

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from app.database import get_db, init_db  # noqa: E402

identity = {"email": "", "id": "", "verified_email": True, "name": "Person"}
TOTP_SECRET = "JBSWY3DPEHPK3PXP"
TOTP = pyotp.TOTP(TOTP_SECRET)


def wrong_code() -> str:
    """A code no step of the accepted window (now ± 30 s) produces."""
    near = {TOTP.at(time.time() + step) for step in (-30, 0, 30)}
    return next(c for c in (f"{n:06d}" for n in range(1000000)) if c not in near)


def google(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/token":
        return httpx.Response(200, json={"access_token": "fake", "expires_in": 3600})
    return httpx.Response(200, json=identity)


def sql(query: str, *args):
    async def go():
        db = await get_db()
        try:
            rows = await (await db.execute(query, args)).fetchall()
            await db.commit()
            return [tuple(r) for r in rows]
        finally:
            await db.close()
    return asyncio.run(go())


def sign_in(email: str, google_id: str) -> tuple[str, set[str], TestClient]:
    """The one Google sign-in on /login: where the browser lands, which cookies
    the callback set, and the browser holding them."""
    browser = TestClient(main.app, raise_server_exceptions=False)
    start = browser.get("/api/auth/google", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    identity.update(email=email, id=google_id)
    original = httpx.AsyncClient
    with patch("app.routers.auth.httpx.AsyncClient", lambda **kw: original(transport=httpx.MockTransport(google), **kw)):
        done = browser.get(f"/api/auth/google/callback?code=c&state={state}", follow_redirects=False)
    location = urlparse(done.headers["location"])
    cookies = {c.split("=", 1)[0] for c in done.headers.get_list("set-cookie") if 'Max-Age=0' not in c and '=""' not in c}
    return location.path + (f"?{location.query}" if location.query else ""), cookies, browser


def member_session(browser: TestClient) -> bool:
    return browser.get("/api/auth/me").json()["authenticated"]


def alumni_session(browser: TestClient) -> bool:
    return browser.get("/api/alumni/me").status_code == 200


async def setup() -> None:
    await init_db()
    db = await get_db()
    try:
        await db.executemany(
            "INSERT INTO users (email, name, role, is_active, google_id, totp_secret) VALUES (?, ?, 'standard', ?, ?, ?)",
            [("member@yale.edu", "Member", 1, "g-member", None), ("both@yale.edu", "Both", 1, "g-both", None),
             ("twofa@yale.edu", "Two Factor", 1, "g-twofa", TOTP_SECRET),
             ("former@yale.edu", "Former", 0, "g-former", None)],
        )
        now = int(time.time())
        await db.execute("INSERT INTO alumni_domains (domain, added_at) VALUES ('aya.yale.edu', ?)", (now,))
        await db.executemany("INSERT INTO alumni_allowlist (email, added_at) VALUES (?, ?)",
                             [(e, now) for e in ("both@yale.edu", "twofa@yale.edu", "grad@yale.edu", "former@yale.edu")])
        await db.execute("INSERT INTO alumni_accounts (email, google_id, created_at, revoked_at) VALUES ('gone@aya.yale.edu', 'g-gone', ?, ?)",
                         (now, now))
        await db.commit()
    finally:
        await db.close()


def routing_matrix() -> None:
    users = sql("SELECT COUNT(*) FROM users")[0][0]

    # Member only: the member site, no alumni session, nothing in the alumni log.
    landed, cookies, browser = sign_in("member@yale.edu", "g-member")
    assert (landed, cookies) == ("/", {"yucg_session"}), (landed, cookies)
    assert member_session(browser) and not alumni_session(browser)
    assert sql("SELECT COUNT(*) FROM alumni_sign_ins")[0][0] == 0

    # On the alumni lists only: the alumni portal, never a member session.
    landed, cookies, browser = sign_in("alum@aya.yale.edu", "g-alum")
    assert (landed, cookies) == ("/alumni", {"yucg_alumni"}), (landed, cookies)
    assert alumni_session(browser) and not member_session(browser)
    # A @yale.edu address on the allow-list is still not a member.
    landed, cookies, browser = sign_in("grad@yale.edu", "g-grad")
    assert (landed, cookies) == ("/alumni", {"yucg_alumni"}), (landed, cookies)
    assert not member_session(browser)
    # A deactivated member is not a member; the alumni lists still apply.
    landed, cookies, browser = sign_in("former@yale.edu", "g-former")
    assert (landed, cookies) == ("/alumni", {"yucg_alumni"}), (landed, cookies)
    assert not member_session(browser)

    # Both: the member site, and the alumni session for "Switch to alumni".
    landed, cookies, browser = sign_in("both@yale.edu", "g-both")
    assert (landed, cookies) == ("/", {"yucg_session", "yucg_alumni"}), (landed, cookies)
    assert member_session(browser) and alumni_session(browser)
    # Signing out of either area ends both.
    out = browser.post("/api/alumni/logout").headers.get_list("set-cookie")
    assert {c.split("=", 1)[0] for c in out} >= {"yucg_session", "yucg_alumni"}, out
    assert not member_session(browser) and not alumni_session(browser)

    # A member with two-factor still goes through it, and is also on the alumni
    # lists: neither session before the code.
    landed, cookies, browser = sign_in("twofa@yale.edu", "g-twofa")
    assert landed == "/login?need_2fa=1" and cookies == {"yucg_2fa_pending"}, (landed, cookies)
    assert not member_session(browser) and not alumni_session(browser)
    # A wrong code gives neither; the right one gives both, in the same answer.
    assert browser.post("/api/auth/2fa/login", json={"code": wrong_code()}).status_code == 400
    assert not member_session(browser) and not alumni_session(browser)
    done = browser.post("/api/auth/2fa/login", json={"code": TOTP.now()})
    assert done.status_code == 200, done.text
    assert {"yucg_session", "yucg_alumni"} <= {c.split("=", 1)[0] for c in done.headers.get_list("set-cookie")}
    assert member_session(browser) and alumni_session(browser)
    # Abandoned at the attempt limit: neither session, even with the right code.
    _, _, browser = sign_in("twofa@yale.edu", "g-twofa")
    for _ in range(5):
        assert browser.post("/api/auth/2fa/login", json={"code": wrong_code()}).status_code == 400
    assert browser.post("/api/auth/2fa/login", json={"code": TOTP.now()}).status_code == 401
    assert not member_session(browser) and not alumni_session(browser)

    # Neither, and a revoked alumnus: the member refusal, nothing more.
    assert sign_in("nobody@yale.edu", "g-nobody")[:2] == ("/login?error=invitation_required", set())
    assert sign_in("stranger@gmail.com", "g-stranger")[:2] == ("/login?error=domain_not_allowed", set())
    assert sign_in("gone@aya.yale.edu", "g-gone")[:2] == ("/login?error=domain_not_allowed", set())
    # Another Google account for an alumnus's address: the same refusal.
    assert sign_in("alum@aya.yale.edu", "g-other")[:2] == ("/login?error=domain_not_allowed", set())
    # A member's address from another Google account: the member refusal, even
    # though the address is also on the alumni lists.
    assert sign_in("both@yale.edu", "g-intruder")[:2] == ("/login?error=identity_mismatch", set())

    # Alumni are never member accounts, and every alumni outcome is audited.
    assert sql("SELECT COUNT(*) FROM users")[0][0] == users
    assert sql("SELECT COUNT(*) FROM alumni_accounts WHERE email IN ('alum@aya.yale.edu', 'grad@yale.edu')")[0][0] == 2
    outcomes = sql("SELECT email_domain, outcome FROM alumni_sign_ins ORDER BY id")
    assert outcomes == [
        ("aya.yale.edu", "signed_in"), ("yale.edu", "signed_in"), ("yale.edu", "signed_in"),
        ("yale.edu", "signed_in"), ("yale.edu", "signed_in"),
        ("yale.edu", "not_eligible"), ("gmail.com", "not_eligible"), ("aya.yale.edu", "revoked"),
        ("aya.yale.edu", "identity_mismatch"), ("yale.edu", "identity_mismatch"),
    ], outcomes


if __name__ == "__main__":
    asyncio.run(setup())
    routing_matrix()
    print("yucg connect: ok")
