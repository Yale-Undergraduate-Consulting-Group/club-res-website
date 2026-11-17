"""Admin → Members → Send setup reminders (app/services/setup_reminders.py).

The preview names exactly what each member is missing; sending recomputes that
on the server, DMs only the missing items through Slack (mocked here, never a
real message), maps every Slack outcome to a result, records each attempt, and
is admin only. From backend/: python tests/test_setup_reminders.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JWT_SECRET", "setup-reminders-test-secret-0123456789")
os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.mkdtemp()) / 't.db'}"
os.environ["FRONTEND_URL"] = "https://club.example.edu"

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from app.database import get_db, init_db  # noqa: E402
from app.jwt_utils import create_token  # noqa: E402
from app.services import slack_dm  # noqa: E402

ADMIN, ANA, BEN, CAL, DEE, EVE, FRA, SYS = range(1, 9)
SIGNATURE = ("sign_off_name", "sign_off_role", "sign_off_linkedin", "sign_off_phone")


async def run(sql: str, args: tuple = ()) -> list:
    db = await get_db()
    try:
        rows = [tuple(r) for r in await (await db.execute(sql, args)).fetchall()]
        await db.commit()
        return rows
    finally:
        await db.close()


async def seed() -> None:
    await init_db()
    people = [
        (ADMIN, "admin@yale.edu", "Ada Admin", "admin", 1, "t", True, SIGNATURE),
        (ANA, "ana@yale.edu", "Ana Lopez", "standard", 1, None, False, ()),
        (BEN, "ben@yale.edu", "Ben Ng", "standard", 1, "t", True, ("sign_off_name",)),
        (CAL, "cal@yale.edu", "Cal Roe", "standard", 1, "t", True, ("sign_off_name", "sign_off_role", "sign_off_phone")),
        (DEE, "dee@yale.edu", "", "standard", 1, "t", True, ("sign_off_name", "sign_off_role", "sign_off_linkedin")),
        (EVE, "eve@yale.edu", "Eve Fox", "standard", 1, "t", True, SIGNATURE),
        (FRA, "fra@yale.edu", "Fra Gone", "standard", 0, None, False, ()),
        (SYS, "sys@yale.edu", "Builder", "system", 1, None, False, ()),
    ]
    for uid, email, name, role, active, gmail, slack, filled in people:
        await run("INSERT INTO users (id, email, name, role, is_active, access_token) VALUES (?, ?, ?, ?, ?, ?)",
                  (uid, email, name, role, active, gmail))
        if slack:
            await run("INSERT INTO user_slack_tokens (user_id, access_token) VALUES (?, 'x')", (uid,))
        for field in filled:
            await run("INSERT INTO settings (key, value) VALUES (?, ?)", (f"member:{uid}:{field}", "set"))
    # A blank value is not filled in.
    await run("INSERT INTO settings (key, value) VALUES (?, '  ')", (f"member:{CAL}:sign_off_linkedin",))
    # Dee was reminded two days ago; Cal ten days ago (outside the week); Ben's last attempt failed.
    for uid, age, result in [(DEE, "-2 days", "sent"), (CAL, "-10 days", "sent"), (BEN, "-1 days", "failed")]:
        await run("""INSERT INTO setup_reminders (user_id, items, result, admin_id, created_at)
                     VALUES (?, 'phone', ?, ?, strftime('%Y-%m-%d %H:%M:%S', 'now', ?))""", (uid, result, ADMIN, age))


    # A year-old attempt is dropped when the next one is recorded.
    await run("""INSERT INTO setup_reminders (id, user_id, items, result, admin_id, created_at)
                 VALUES (100, ?, 'phone', 'sent', ?, strftime('%Y-%m-%d %H:%M:%S', 'now', '-400 days'))""", (ANA, ADMIN))


def client(uid: int, role: str) -> TestClient:
    c = TestClient(main.app, raise_server_exceptions=False)
    c.cookies.set("yucg_session", create_token(uid, f"{uid}@yale.edu", None, None, role))
    return c


class FakeSlack:
    """Answers Slack's Web API by email: who is in the workspace, who is rate limited, who errors."""

    def __init__(self):
        self.posts: dict[str, str] = {}
        self.calls: list[str] = []
        self.limited_once = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer xoxb-test"
        method = request.url.path.rsplit("/", 1)[1]
        self.calls.append(method)
        if method == "users.lookupByEmail":
            email = request.url.params["email"]
            if email == "ben@yale.edu":
                return httpx.Response(200, json={"ok": False, "error": "users_not_found"})
            if email == "cal@yale.edu" and not self.limited_once:
                self.limited_once = True
                return httpx.Response(429, headers={"Retry-After": "2"}, json={"ok": False, "error": "ratelimited"})
            return httpx.Response(200, json={"ok": True, "user": {"id": "U" + email.split("@")[0].upper()}})
        body = __import__("json").loads(request.content)
        if method == "conversations.open":
            if body["users"] == "UDEE":
                return httpx.Response(200, json={"ok": False, "error": "account_inactive"})
            return httpx.Response(200, json={"ok": True, "channel": {"id": "D" + body["users"]}})
        assert method == "chat.postMessage", method
        self.posts[body["channel"]] = body["text"]
        return httpx.Response(200, json={"ok": True})


def tests() -> None:
    asyncio.run(seed())
    admin = client(ADMIN, "admin")
    token = AsyncMock(return_value="xoxb-test")

    # Admin only.
    assert client(ANA, "standard").get("/api/admin/setup-reminders").status_code == 403
    assert client(ANA, "standard").post("/api/admin/setup-reminders", json={"user_ids": [ANA]}).status_code == 403
    anonymous = TestClient(main.app, raise_server_exceptions=False)
    assert anonymous.get("/api/admin/setup-reminders").status_code == 401
    assert anonymous.post("/api/admin/setup-reminders", json={"user_ids": [ANA]}).status_code == 401

    # Preview: only incomplete active members, exactly what each is missing, Gmail first.
    with patch("app.services.setup_reminders._bot_token", token):
        body = admin.get("/api/admin/setup-reminders").json()
    assert body["slack_connected"] is True
    rows = {m["user_id"]: m for m in body["members"]}
    assert set(rows) == {ANA, BEN, CAL, DEE}, rows.keys()  # complete, inactive and system members are not listed
    assert [i["key"] for i in rows[ANA]["missing"]] == ["gmail", "role", "linkedin", "phone", "sign_off", "slack"]
    assert [i["label"] for i in rows[ANA]["missing"]][:2] == ["Gmail", "Signature role"]
    assert [i["key"] for i in rows[BEN]["missing"]] == ["role", "linkedin", "phone"]
    assert [i["key"] for i in rows[CAL]["missing"]] == ["linkedin"], "a blank value is missing"
    assert [i["key"] for i in rows[DEE]["missing"]] == ["phone"]
    # Reminded within a week is marked; ten days ago, and a failed attempt, are not.
    assert rows[DEE]["reminded_at"] and not rows[CAL]["reminded_at"] and not rows[BEN]["reminded_at"], rows

    # No Slack token: the preview says so and sending refuses clearly.
    with patch("app.services.setup_reminders._bot_token", AsyncMock(return_value=None)):
        assert admin.get("/api/admin/setup-reminders").json()["slack_connected"] is False
        refused = admin.post("/api/admin/setup-reminders", json={"user_ids": [ANA]})
    assert refused.status_code == 409 and "not connected" in refused.json()["detail"], refused.text
    assert asyncio.run(run("SELECT COUNT(*) FROM setup_reminders WHERE admin_id = ?", (ADMIN,)))[0][0] == 4

    # Sending: the client names ids only. Ana, Ben, Cal and Dee are incomplete; Eve is complete,
    # Fra inactive, the system account is not a member, 999 does not exist; none of those is messaged.
    slack = FakeSlack()
    sleeps: list[float] = []

    async def no_wait(seconds: float) -> None:
        sleeps.append(seconds)

    with patch("app.services.setup_reminders._bot_token", token), \
            patch("app.services.slack_dm.new_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(slack.handler))), \
            patch("app.services.slack_dm.pause", no_wait):
        resp = admin.post("/api/admin/setup-reminders", json={
            "user_ids": [ANA, BEN, CAL, DEE, EVE, FRA, SYS, 999, ANA], "items": ["gmail"], "missing": {"2": []}})
    assert resp.status_code == 200, resp.text
    got = {r["user_id"]: r for r in resp.json()["results"]}
    assert [r["user_id"] for r in resp.json()["results"]] == [ANA, BEN, CAL, DEE, EVE, FRA, SYS, 999], "one per id, in order"
    assert got[ANA]["status"] == "sent" and got[ANA]["error"] is None
    assert got[BEN]["status"] == "not_found"
    assert got[CAL]["status"] == "sent", "Slack's 429 is waited out, then the DM goes"
    assert 2.0 in sleeps
    assert got[DEE]["status"] == "failed" and got[DEE]["error"] == "account_inactive"
    assert all(got[u]["status"] == "skipped" for u in (EVE, FRA, SYS, 999))
    assert slack.calls.count("chat.postMessage") == 2 and set(slack.posts) == {"DUANA", "DUCAL"}

    # The message: first name, only what is missing, where to fix it; nothing for what is done.
    ana_text, cal_text = slack.posts["DUANA"], slack.posts["DUCAL"]
    assert ana_text.startswith("Hi Ana!")
    assert ana_text.count("•") == 6
    assert "https://club.example.edu/profile?tab=integrations" in ana_text and "tab=settings" in ana_text
    assert cal_text.count("•") == 1 and "LinkedIn" in cal_text
    assert "Gmail" not in cal_text and "Slack" not in cal_text and "phone" not in cal_text

    # Every attempt is recorded and audited; skipped ones are not.
    log = asyncio.run(run("SELECT user_id, items, result, error, admin_id FROM setup_reminders WHERE id > 100 ORDER BY id"))
    assert log == [
        (ANA, "gmail,role,linkedin,phone,sign_off,slack", "sent", None, ADMIN),
        (BEN, "role,linkedin,phone", "not_found", "users_not_found", ADMIN),
        (CAL, "linkedin", "sent", None, ADMIN),
        (DEE, "phone", "failed", "account_inactive", ADMIN),
    ], log
    assert asyncio.run(run("SELECT COUNT(*) FROM setup_reminders WHERE id = 100"))[0][0] == 0, "older than a year is deleted"
    audit = asyncio.run(run("SELECT resource_id, details FROM audit_log WHERE action = 'setup_reminder' ORDER BY id"))
    assert [a[0] for a in audit] == [str(ANA), str(BEN), str(CAL), str(DEE)] and "account_inactive" in audit[3][1]

    # Now Ana is marked reminded, Dee's failed attempt does not mark her again.
    with patch("app.services.setup_reminders._bot_token", token):
        rows = {m["user_id"]: m for m in admin.get("/api/admin/setup-reminders").json()["members"]}
    assert rows[ANA]["reminded_at"] and rows[CAL]["reminded_at"] and not rows[BEN]["reminded_at"]

    # A member who has since finished setting up is not messaged even if the client still lists them.
    asyncio.run(run("UPDATE users SET access_token = 't' WHERE id = ?", (ANA,)))
    for key in ("sign_off_name", "sign_off_role", "sign_off_linkedin", "sign_off_phone"):
        asyncio.run(run("INSERT INTO settings (key, value) VALUES (?, 'x')", (f"member:{ANA}:{key}",)))
    asyncio.run(run("INSERT INTO user_slack_tokens (user_id, access_token) VALUES (?, 'x')", (ANA,)))
    slack = FakeSlack()
    with patch("app.services.setup_reminders._bot_token", token), \
            patch("app.services.slack_dm.new_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(slack.handler))), \
            patch("app.services.slack_dm.pause", no_wait):
        again = admin.post("/api/admin/setup-reminders", json={"user_ids": [ANA]}).json()["results"]
    assert again[0]["status"] == "skipped" and slack.calls == []

    # Persistent rate limiting is reported as a failure with Slack's code, never as sent.
    with patch("app.services.setup_reminders._bot_token", token), \
            patch("app.services.slack_dm.new_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(429, headers={"Retry-After": "1"})))), \
            patch("app.services.slack_dm.pause", no_wait):
        limited = admin.post("/api/admin/setup-reminders", json={"user_ids": [DEE]}).json()["results"]
    assert limited[0]["status"] == "failed" and limited[0]["error"] == "ratelimited", limited

    assert admin.post("/api/admin/setup-reminders", json={"user_ids": []}).status_code == 422


if __name__ == "__main__":
    tests()
    print("setup reminders: ok")
