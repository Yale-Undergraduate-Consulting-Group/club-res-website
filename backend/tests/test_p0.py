"""P0 self-check. From backend/: python tests/test_p0.py"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.services.email_verifier import verify_email_format


def test_email_format() -> None:
    assert verify_email_format("jane.doe@apple.com")["valid"] is True
    assert verify_email_format("not-an-email")["valid"] is False
    assert verify_email_format("")["valid"] is False
    assert verify_email_format("a@b")["valid"] is False


def test_unauthenticated_routes() -> None:
    os.environ.setdefault("JWT_SECRET", "p0-test-secret-not-a-known-default-xx")
    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/contacts").status_code == 401
        assert client.get("/api/analytics/dashboard").status_code == 401
        assert client.get("/api/campaigns").status_code == 401
        assert client.get("/api/emails/generated").status_code == 401
        assert client.get("/api/track/open/1").status_code == 200
        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["authenticated"] is False

        from app.jwt_utils import create_token

        token = create_token(1, "test@yale.edu", "Test", None, "standard")
        authed = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert authed.status_code == 200
        # Email/role come from the users table, not the JWT payload.
        assert "authenticated" in authed.json()
        assert client.get("/api/auth/me", params={"authorization": f"Bearer {token}"}).json()["authenticated"] is False


def test_jwt_refuses_empty_secret() -> None:
    env = {**os.environ, "JWT_SECRET": ""}
    r = subprocess.run(
        [sys.executable, "-c", "from app.jwt_utils import JWT_SECRET"],
        cwd=str(BACKEND),
        env=env,
        capture_output=True,
        text=True,
    )
    assert r.returncode != 0
    assert "JWT_SECRET" in (r.stderr + r.stdout)


if __name__ == "__main__":
    test_email_format()
    test_jwt_refuses_empty_secret()
    test_unauthenticated_routes()
    print("ok")
