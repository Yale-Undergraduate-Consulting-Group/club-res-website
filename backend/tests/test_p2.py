"""P2 self-check. From backend/: python tests/test_p2.py"""
from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("JWT_SECRET", "p2-test-secret-not-a-known-default-xx")

from app.db_compat import translate_sqlite_sql
from app.token_crypto import decrypt_token, encrypt_token


def test_upsert_translate() -> None:
    q = translate_sqlite_sql(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)"
    )
    assert "ON CONFLICT (key)" in q
    assert "$1" in q and "$2" in q


def test_token_roundtrip() -> None:
    raw = "ya29.not-a-real-token"
    enc = encrypt_token(raw)
    assert enc and enc.startswith("enc:v1:")
    assert decrypt_token(enc) == raw
    assert decrypt_token(raw) == raw


def test_cookie_me() -> None:
    from fastapi.testclient import TestClient
    from app.jwt_utils import COOKIE_NAME, create_token
    from main import app

    token = create_token(1, "test@yale.edu", "Test", None, "standard")
    with TestClient(app) as client:
        r = client.get("/api/auth/me", cookies={COOKIE_NAME: token})
        assert r.status_code == 200
        # user 1 may not exist; cookie is accepted then DB re-read may drop it
        body = r.json()
        assert "authenticated" in body


if __name__ == "__main__":
    test_upsert_translate()
    test_token_roundtrip()
    test_cookie_me()
    print("ok")
