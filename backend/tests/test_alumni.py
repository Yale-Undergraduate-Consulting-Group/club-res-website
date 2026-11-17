"""Alumni sign-in (app/routers/alumni.py, docs/night/security.md): who may sign
in, that alumni get no club data, revocation, and the denial matrix - an alumni
session gets nothing from any other route that an anonymous request would not
get, and a member session gets nothing from the alumni routes.
Google is faked; the resources are the synthetic library from test_resources,
there to prove alumni cannot reach data that exists.
From backend/: python tests/test_alumni.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_tmp = Path(tempfile.mkdtemp())
os.environ["JWT_SECRET"] = "alumni-test-secret-0123456789abcdefgh"
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 't.db'}"
os.environ["DOCUMENTS_LOCAL_DIR"] = str(_tmp / "files")
os.environ["GOOGLE_CLIENT_ID"] = "test-client"

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from app.database import get_db, init_db  # noqa: E402
from app.jwt_utils import create_alumni_token, create_token  # noqa: E402
from app.services import resource_import  # noqa: E402
from test_resources import make_zip  # noqa: E402

ADMIN, MEMBER = 1, 2
identity = {"email": "", "id": "", "verified_email": True, "name": "Alum"}


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


def member(user_id: int, role: str) -> TestClient:
    client = TestClient(main.app, raise_server_exceptions=False)
    client.cookies.set("yucg_session", create_token(user_id, f"{user_id}@yale.edu", None, None, role))
    return client


def alumni_sign_in(email: str, google_id: str, verified: bool = True) -> tuple[str, TestClient]:
    """Sign in on YUCG Connect, let Google answer with this identity; returns
    where the browser lands and the browser (holding whatever cookies it got).
    The addresses here are not members, so no member session may appear."""
    browser = TestClient(main.app, raise_server_exceptions=False)
    start = browser.get("/api/auth/google", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    identity.update(email=email, id=google_id, verified_email=verified)
    original = httpx.AsyncClient
    with patch("app.routers.auth.httpx.AsyncClient", lambda **kw: original(transport=httpx.MockTransport(google), **kw)):
        done = browser.get(f"/api/auth/google/callback?code=c&state={state}", follow_redirects=False)
    cookies = done.headers.get_list("set-cookie")
    assert not any(c.startswith("yucg_session=") for c in cookies), cookies
    if done.headers["location"].endswith("/alumni"):
        alumni_cookie = next(c for c in cookies if c.startswith("yucg_alumni="))
        assert "HttpOnly" in alumni_cookie and "Path=/api/alumni" in alumni_cookie and "SameSite=lax" in alumni_cookie, alumni_cookie
    return urlparse(done.headers["location"]).path + ("?" + urlparse(done.headers["location"]).query if urlparse(done.headers["location"]).query else ""), browser


async def setup() -> None:
    await init_db()
    db = await get_db()
    try:
        await db.executemany("INSERT INTO users (id, email, name, role, is_active) VALUES (?, ?, ?, ?, 1)",
                             [(ADMIN, "admin@yale.edu", "Admin", "admin"), (MEMBER, "ben@yale.edu", "Ben", "standard"),
                              (3, "team@yale.edu", "Team Person", "standard")])
        await db.commit()
    finally:
        await db.close()
    archive = _tmp / "export.zip"
    make_zip(archive)
    await resource_import.run_import(archive, log=lambda _line: None)


def sign_in_rules(admin: TestClient, standard: TestClient) -> None:
    users_before = sql("SELECT COUNT(*) FROM users")[0][0]
    # Both lists start empty: nobody gets in, and the refusal records only the domain.
    # A refusal is the member refusal: it never says whether an alumni list was checked.
    landed, _ = alumni_sign_in("alum@aya.yale.edu", "g-alum")
    assert landed == "/login?error=domain_not_allowed", landed
    assert sql("SELECT account_id, email_domain, outcome FROM alumni_sign_ins") == [(None, "aya.yale.edu", "not_eligible")]
    assert sql("SELECT COUNT(*) FROM alumni_accounts")[0][0] == 0

    # Only admins manage the lists; obvious mistakes are refused.
    assert standard.get("/api/admin/alumni").status_code == 403
    assert standard.post("/api/admin/alumni/domains", json={"domain": "aya.yale.edu"}).status_code == 403
    for bad in ("yale.edu", "gmail.com", "not a domain", "@"):
        assert admin.post("/api/admin/alumni/domains", json={"domain": bad}).status_code in (400, 422), bad
    assert admin.post("/api/admin/alumni/domains", json={"domain": "@AYA.yale.edu"}).json() == {"domain": "aya.yale.edu"}

    landed, _ = alumni_sign_in("alum@aya.yale.edu", "g-alum", verified=False)
    assert landed == "/login?error=email_not_verified", landed
    landed, alum = alumni_sign_in("alum@aya.yale.edu", "g-alum")
    assert landed == "/alumni", landed
    assert alum.get("/api/alumni/me").json() == {"email": "alum@aya.yale.edu", "name": "Alum", "board": False}
    # Subdomains and look-alikes are not the domain.
    for outsider in ("x@evil.aya.yale.edu", "x@aya.yale.edu.evil.test", "x@gmail.com"):
        assert alumni_sign_in(outsider, "g-x")[0] == "/login?error=domain_not_allowed", outsider
    # Another Google account with the same address does not take the account over.
    assert alumni_sign_in("alum@aya.yale.edu", "g-other")[0] == "/login?error=domain_not_allowed"

    # One allow-listed address from any provider.
    assert standard.post("/api/admin/alumni/emails", json={"email": "friend@gmail.com"}).status_code == 403
    assert admin.post("/api/admin/alumni/emails", json={"email": " Friend@Gmail.com ", "note": "Class of 2021"}).status_code == 201
    landed, friend = alumni_sign_in("friend@gmail.com", "g-friend")
    assert landed == "/alumni" and friend.get("/api/alumni/me").status_code == 200

    # Alumni are never member accounts.
    assert sql("SELECT COUNT(*) FROM users")[0][0] == users_before
    overview = admin.get("/api/admin/alumni").json()
    assert [d["domain"] for d in overview["domains"]] == ["aya.yale.edu"]
    assert [(e["email"], e["note"]) for e in overview["allowlist"]] == [("friend@gmail.com", "Class of 2021")]
    assert {a["email"]: a["allowed"] for a in overview["accounts"]} == {"alum@aya.yale.edu": True, "friend@gmail.com": True}
    outcomes = [(s["outcome"], s["email"], s["email_domain"]) for s in overview["sign_ins"]]
    assert ("identity_mismatch", "alum@aya.yale.edu", "aya.yale.edu") in outcomes
    assert ("not_eligible", None, "evil.aya.yale.edu") in outcomes and ("signed_in", "friend@gmail.com", "gmail.com") in outcomes
    actions = {r[0] for r in sql("SELECT action FROM audit_log WHERE user_id = ?", ADMIN)}
    assert {"alumni_domain_add", "alumni_email_add"} <= actions, actions


def no_club_data(admin: TestClient, alum: TestClient) -> None:
    """Alumni get no Resources or projects: no alumni route serves them and the
    member routes refuse the alumni session. Positive control: the data exists."""
    assert admin.get("/api/resources?limit=100").json()["total"] > 0
    public = next(i for i in admin.get("/api/resources?limit=100").json()["items"]
                  if i["kind"] == "past_project" and i["disclosure"] == "public" and i["files"])
    for path in ("/api/alumni/resources", f"/api/alumni/resources/{public['id']}/files/{public['files'][0]['id']}/download",
                 "/api/alumni/local-file?token=x"):
        assert alum.get(path).status_code == 404, path
    for path in ("/api/resources", f"/api/resources/{public['id']}", "/api/workspace/projects", "/api/projects"):
        assert alum.get(path).status_code in (401, 404), path
    assert not [p for p in main.app.openapi()["paths"] if p.startswith("/api/alumni") and ("resource" in p or "project" in p)]


def revocation(admin: TestClient, alum: TestClient, friend: TestClient) -> None:
    alum_id = sql("SELECT id FROM alumni_accounts WHERE email = 'alum@aya.yale.edu'")[0][0]
    assert admin.post(f"/api/admin/alumni/accounts/{alum_id}/revoke").status_code == 200
    assert alum.get("/api/alumni/me").status_code == 401
    assert alumni_sign_in("alum@aya.yale.edu", "g-alum")[0] == "/login?error=domain_not_allowed"
    assert admin.post(f"/api/admin/alumni/accounts/{alum_id}/restore").status_code == 200
    assert alum.get("/api/alumni/me").status_code == 200
    # Removing the domain ends the domain's sessions at once; the allow-listed address stays.
    assert admin.delete("/api/admin/alumni/domains/aya.yale.edu").status_code == 200
    assert alum.get("/api/alumni/me").status_code == 401 and friend.get("/api/alumni/me").status_code == 200
    assert admin.delete("/api/admin/alumni/emails/friend%40gmail.com").status_code == 200
    assert friend.get("/api/alumni/me").status_code == 401
    # Delete removes the account and its sign-in records.
    assert admin.delete(f"/api/admin/alumni/accounts/{alum_id}").status_code == 200
    assert sql("SELECT COUNT(*) FROM alumni_sign_ins WHERE account_id = ?", alum_id)[0][0] == 0
    assert admin.delete(f"/api/admin/alumni/accounts/{alum_id}").status_code == 404
    actions = {r[0] for r in sql("SELECT action FROM audit_log WHERE user_id = ?", ADMIN)}
    assert {"alumni_revoke", "alumni_restore", "alumni_domain_remove", "alumni_email_remove", "alumni_delete"} <= actions
    # Logout ends the alumni session and, since one sign-in made both, the member one.
    out = alum.post("/api/alumni/logout")
    assert out.status_code == 200 and {"yucg_alumni", "yucg_session"} <= {c.split("=", 1)[0] for c in out.headers.get_list("set-cookie")}


def denial_matrix(alumni_token: str, standard: TestClient) -> None:
    """Every method and path the server serves, from its own schema: an alumni
    token, however presented, gets exactly what no session gets, except on the
    alumni routes. Members get nothing from the alumni routes."""
    anonymous = TestClient(main.app, raise_server_exceptions=False)
    presented = []
    for how in ("alumni cookie", "member cookie", "bearer"):
        client = TestClient(main.app, raise_server_exceptions=False)
        if how == "alumni cookie":
            client.cookies.set("yucg_alumni", alumni_token)
        elif how == "member cookie":
            client.cookies.set("yucg_session", alumni_token)
        else:
            client.headers["Authorization"] = f"Bearer {alumni_token}"
        presented.append((how, client))
    routes = [(path, method.upper()) for path, ops in main.app.openapi()["paths"].items() for method in ops]
    assert len(routes) > 150, len(routes)
    alumni_routes, gaps, checked = [], [], 0
    for path, method in routes:
        url = path.replace("{", "").replace("}", "")
        if path.startswith("/api/alumni/") or path == "/api/alumni":
            alumni_routes.append((path, method))
            continue
        expected = anonymous.request(method, url, follow_redirects=False).status_code
        for how, client in presented:
            got = client.request(method, url, follow_redirects=False).status_code
            checked += 1
            if got != expected:
                gaps.append((method, path, how, got, expected))
    assert not gaps, gaps
    # Member sessions (and anonymous requests) get nothing from the alumni area,
    # even with a member token put in the alumni cookie.
    assert {p for p, _ in alumni_routes} >= {"/api/alumni/me", "/api/alumni/home", "/api/alumni/posts", "/api/alumni/inbox"}
    member_as_alumni = TestClient(main.app, raise_server_exceptions=False)
    member_as_alumni.cookies.set("yucg_alumni", standard.cookies.get("yucg_session"))
    for path, method in alumni_routes:
        if path == "/api/alumni/logout":
            continue
        url = path.replace("{", "").replace("}", "")
        # The local dev sign-in is a pre-sign-in route: with DEV_ALUMNI_EMAIL unset it does not exist.
        expected = 404 if path == "/api/alumni/dev-login" else 401
        for client in (anonymous, standard, member_as_alumni):
            assert client.request(method, url).status_code == expected, (method, path)
    print(f"denial matrix: {len(routes) - len(alumni_routes)} routes x 3 alumni presentations = {checked} checks, no gaps")


def dev_login_fences(admin: TestClient) -> None:
    """The local alumni dev sign-in exists only with DEV_ALUMNI_EMAIL set, outside
    production, on a loopback host, and only for an address the lists allow."""
    local = TestClient(main.app, base_url="http://localhost", raise_server_exceptions=False)
    os.environ.pop("DEV_ALUMNI_EMAIL", None)
    assert local.post("/api/alumni/dev-login").status_code == 404
    os.environ["DEV_ALUMNI_EMAIL"] = "dev-alum@example.org"
    remote = TestClient(main.app, base_url="http://club.example.org", raise_server_exceptions=False)
    assert remote.post("/api/alumni/dev-login").status_code == 404
    os.environ["APP_ENV"] = "production"
    assert local.post("/api/alumni/dev-login").status_code == 404
    os.environ.pop("APP_ENV")
    assert local.post("/api/alumni/dev-login").status_code == 403, "not on the lists yet"
    assert admin.post("/api/admin/alumni/emails", json={"email": "dev-alum@example.org"}).status_code == 201
    assert local.post("/api/alumni/dev-login").status_code == 200
    assert local.get("/api/alumni/me").json()["email"] == "dev-alum@example.org"
    os.environ.pop("DEV_ALUMNI_EMAIL")


def tests() -> None:
    asyncio.run(setup())
    admin, standard = member(ADMIN, "admin"), member(MEMBER, "standard")
    sign_in_rules(admin, standard)
    alum = TestClient(main.app, raise_server_exceptions=False)
    alum_id = sql("SELECT id FROM alumni_accounts WHERE email = 'alum@aya.yale.edu'")[0][0]
    alum.cookies.set("yucg_alumni", create_alumni_token(alum_id))
    friend = TestClient(main.app, raise_server_exceptions=False)
    friend.cookies.set("yucg_alumni", create_alumni_token(sql("SELECT id FROM alumni_accounts WHERE email = 'friend@gmail.com'")[0][0]))
    no_club_data(admin, alum)
    denial_matrix(create_alumni_token(alum_id), standard)
    dev_login_fences(admin)
    revocation(admin, alum, friend)


if __name__ == "__main__":
    tests()
    print("alumni: ok")
