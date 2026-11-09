"""Onboarding at club scale: bulk invitations and member readiness.

A pasted list of addresses gets one result each, and one bad address never
stops the rest. Readiness says what each member still has to set up; only a
connected Gmail blocks sending. Members see only their own; the club view is
admin-only.
From backend/: python tests/test_onboarding.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JWT_SECRET", "onboarding-test-secret-0123456789abcdef")
os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.mkdtemp()) / 't.db'}"

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from app.database import get_db, init_db  # noqa: E402
from app.jwt_utils import create_token  # noqa: E402
from app.routers.invitations import digest  # noqa: E402

ADMIN, ANA, BEN = 1, 2, 3


async def sql(statement: str, params: tuple = (), many: bool = False) -> list:
    db = await get_db()
    try:
        if many:
            await db.executemany(statement, params)
            await db.commit()
            return []
        cursor = await db.execute(statement, params)
        rows = [tuple(r) for r in await cursor.fetchall()]
        await db.commit()
        return rows
    finally:
        await db.close()


async def setup() -> None:
    await init_db()
    await sql("INSERT INTO users (id, email, name, role, is_active, access_token) VALUES (?, ?, ?, ?, 1, ?)",
              [(ADMIN, "admin@yale.edu", "Admin", "admin", None), (ANA, "ana@yale.edu", "Ana", "standard", "gmail-token"),
               (BEN, "ben@yale.edu", "Ben", "standard", None)], many=True)
    await sql("INSERT INTO projects (id, name) VALUES (1, 'Client A')")
    await sql("INSERT INTO login_log (user_id, email) VALUES (?, ?)", (ANA, "ana@yale.edu"))
    await sql("INSERT INTO settings (key, value) VALUES (?, ?)", (f"member:{ANA}:sign_off_name", "Ana Lee"))
    await sql("INSERT INTO settings (key, value) VALUES (?, ?)", (f"member:{BEN}:sign_off_name", "   "))
    await sql("INSERT INTO user_slack_tokens (user_id, access_token) VALUES (?, ?)", (ANA, "slack-token"))
    await sql("INSERT INTO user_project_assignments (user_id, project_id) VALUES (?, 1)", (ANA,))


def as_member(user_id: int, role: str = "standard") -> TestClient:
    client = TestClient(main.app, raise_server_exceptions=False)
    client.cookies.set("yucg_session", create_token(user_id, f"{user_id}@yale.edu", None, None, role))
    return client


def tests() -> None:
    asyncio.run(setup())
    admin, ana = as_member(ADMIN, "admin"), as_member(ANA)

    # A pasted list: new, duplicate, existing member, not Yale, then new again.
    asyncio.run(sql("INSERT INTO membership_invitations (email, token_hash, created_by, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
                    ("pending@yale.edu", digest("x" * 40), ADMIN, 4102444800, 0)))
    response = admin.post("/api/admin/invitations/bulk", json={
        "emails": ["New1@Yale.edu", "new1@yale.edu", "ben@yale.edu", "pending@yale.edu", "someone@gmail.com", "new2@yale.edu"],
        "project_ids": [1]})
    assert response.status_code == 200, response.text
    body = response.json()
    by_email = {r["email"]: r for r in body["results"]}
    assert (body["created"], body["skipped"]) == (2, 3), body
    assert by_email["new1@yale.edu"]["status"] == "created" and by_email["new2@yale.edu"]["status"] == "created"
    assert by_email["ben@yale.edu"]["reason"] == "Already a member"
    assert by_email["pending@yale.edu"]["reason"] == "Invitation already pending"
    assert by_email["someone@gmail.com"]["reason"] == "Not a Yale email address"
    assert "token" not in by_email["ben@yale.edu"], "a skipped address must not get an invitation link"
    token = by_email["new2@yale.edu"]["token"]
    stored = asyncio.run(sql("SELECT token_hash, project_ids FROM membership_invitations WHERE email = 'new2@yale.edu'"))
    assert stored == [(digest(token), "[1]")], "only the token's hash is stored, with the chosen projects"
    assert admin.post("/api/admin/invitations/bulk", json={"emails": ["x@yale.edu"], "project_ids": [99]}).status_code == 400
    assert ana.post("/api/admin/invitations/bulk", json={"emails": ["x@yale.edu"]}).status_code == 403
    assert asyncio.run(sql("SELECT count(*) FROM membership_invitations WHERE email = 'x@yale.edu'")) == [(0,)]

    # Readiness: Ana has everything; Ben has only a blank sign-off; admin never signed in here.
    mine = ana.get("/api/auth/readiness").json()
    assert mine == {"signed_in": True, "gmail": True, "sign_off": True, "slack": True, "project": True,
                    "has_sent": False, "ready_to_send": True}, mine
    ben = as_member(BEN).get("/api/auth/readiness").json()
    assert ben["sign_off"] is False and ben["gmail"] is False and ben["ready_to_send"] is False, ben
    assert ana.get("/api/admin/users/readiness").status_code == 403
    club = {r["user_id"]: r for r in admin.get("/api/admin/users/readiness").json()}
    assert set(club) == {ADMIN, ANA, BEN}
    assert [uid for uid, r in club.items() if r["ready_to_send"]] == [ANA]

    # A deactivated member drops out of the club view.
    asyncio.run(sql("UPDATE users SET is_active = 0 WHERE id = ?", (BEN,)))
    assert BEN not in {r["user_id"] for r in admin.get("/api/admin/users/readiness").json()}


if __name__ == "__main__":
    tests()
    print("onboarding: ok")
