"""Each app's settings (docs/PLATFORM.md step 4).

The production server refuses to start without a required setting, so the
deploy's health check fails and the previous release keeps serving. Dev
(APP_ENV=beta) runs without some on purpose - it has no Google sign-in - and
only logs them; local development (no APP_ENV) needs no cloud settings.
Admins see which settings each app has, by name only.
From backend/: python tests/test_settings.py
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JWT_SECRET", "settings-test-secret-0123456789abcdef")
os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.mkdtemp()) / 't.db'}"
for name in ("APP_ENV", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "BACKEND_URL", "FRONTEND_URL", "MONID_API_KEY"):
    os.environ.pop(name, None)

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from app.jwt_utils import create_token  # noqa: E402
from app.modules import all_modules  # noqa: E402

REQUIRED = {"GOOGLE_CLIENT_ID": "id.apps.googleusercontent.com", "GOOGLE_CLIENT_SECRET": "google-secret-value",
            "BACKEND_URL": "https://site.example", "FRONTEND_URL": "https://site.example"}


def starts() -> bool:
    try:
        with TestClient(main.app) as client:
            return client.get("/api/health").status_code == 200
    except RuntimeError as exc:
        assert "Required settings are not set" in str(exc), exc
        return False


def tests() -> None:
    names = [s.name for m in all_modules() for s in m.settings]
    assert len(names) == len(set(names)), names
    required = {s.name for m in all_modules() for s in m.settings if s.required}
    assert {"JWT_SECRET", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"} <= required, required

    # Local development: no APP_ENV, no cloud settings, and it starts.
    assert starts()

    # Production without Google sign-in refuses to start, naming what is
    # missing; with every required setting it starts, optional ones unset.
    without_secret = {k: v for k, v in REQUIRED.items() if k != "GOOGLE_CLIENT_SECRET"}
    with patch.dict(os.environ, {"APP_ENV": "production", **without_secret}):
        try:
            with TestClient(main.app):
                raise AssertionError("production must not start without a required setting")
        except RuntimeError as exc:
            assert "GOOGLE_CLIENT_SECRET" in str(exc) and "GOOGLE_CLIENT_ID" not in str(exc), exc
    # Dev as it is deployed: Google keys present but empty. It starts and says so.
    with patch.dict(os.environ, {"APP_ENV": "beta", **without_secret, "GOOGLE_CLIENT_SECRET": ""}):
        with patch("logging.Logger.warning") as warned:
            assert starts()
        assert any("GOOGLE_CLIENT_SECRET" in str(call) for call in warned.call_args_list), warned.call_args_list
    with patch.dict(os.environ, {"APP_ENV": "production", **REQUIRED}):
        assert starts()

        # Admins see each setting's state; values never leave the server.
        import asyncio

        from app.database import get_db, init_db

        async def admin_user() -> None:
            await init_db()
            db = await get_db()
            try:
                await db.execute("INSERT OR IGNORE INTO users (id, email, role, is_active) VALUES (1, 'a@yale.edu', 'admin', 1)")
                await db.execute("INSERT OR IGNORE INTO users (id, email, role, is_active) VALUES (2, 'm@yale.edu', 'standard', 1)")
                await db.commit()
            finally:
                await db.close()

        asyncio.run(admin_user())
        client = TestClient(main.app)
        assert client.get("/api/admin/apps/settings").status_code == 401
        client.cookies.set("yucg_session", create_token(2, "m@yale.edu", None, None, "standard"))
        assert client.get("/api/admin/apps/settings").status_code == 403
        client.cookies.set("yucg_session", create_token(1, "a@yale.edu", None, None, "admin"))
        response = client.get("/api/admin/apps/settings")
        rows = {r["name"]: r for r in response.json()}
        assert rows["GOOGLE_CLIENT_SECRET"]["configured"] and rows["GOOGLE_CLIENT_SECRET"]["required"]
        assert not rows["MONID_API_KEY"]["configured"] and not rows["MONID_API_KEY"]["required"]
        assert rows["MONID_API_KEY"]["app"] == "outreach" and rows["MONID_API_KEY"]["purpose"]
        for value in (*REQUIRED.values(), os.environ["JWT_SECRET"]):
            assert value not in response.text, "a setting's value reached the browser"


if __name__ == "__main__":
    tests()
    print("settings: ok")
