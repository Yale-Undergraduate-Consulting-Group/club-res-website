"""The rules every app on the platform keeps (app/modules, docs/PLATFORM.md).

A new app is a manifest. These are the ways one can break the others: a
duplicate path prefix or job id, moving a URL that outside services call,
serving a members-only router without sign-in, or sending mail from beta.
From backend/: python tests/test_modules.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JWT_SECRET", "modules-test-secret-0123456789abcdef")
os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.mkdtemp()) / 't.db'}"

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from app.modules import all_modules, schedule  # noqa: E402

#: Called from outside the app: the open-tracking pixel in sent mail, Slack's
#: signed events and commands, Google's OAuth callback. They must never move.
FIXED_PREFIXES = {"/api/track", "/api/slack", "/api/auth"}
#: Routers whose routes rely on the manifest's session check alone. Dropping
#: signed_in from one of these serves it to anyone: /api/analytics/leaderboard
#: ran its query with no session when the flag was removed.
MEMBERS_ONLY = {"/api/contacts", "/api/emails", "/api/campaigns", "/api/analytics", "/api/outreach",
                "/api/yucg/releases"}


def tests() -> None:
    modules = all_modules()
    ids = [m.id for m in modules]
    assert len(ids) == len(set(ids)), ids
    prefixes = [r.prefix for m in modules for r in m.routes]
    assert len(prefixes) == len(set(prefixes)), prefixes
    assert all(p.startswith("/api/") for p in prefixes), prefixes
    assert FIXED_PREFIXES <= set(prefixes), FIXED_PREFIXES - set(prefixes)
    job_ids = [j.id for m in modules for j in m.jobs]
    assert len(job_ids) == len(set(job_ids)), job_ids
    # Each app numbers its migrations 1, 2, 3... with no gaps or repeats.
    for module in modules:
        versions = sorted(m.version for m in module.migrations)
        assert versions == list(range(1, len(versions) + 1)), (module.id, versions)

    # Every members-only router answers 401 to a request with no session, on
    # every route in it.
    paths = main.app.openapi()["paths"]
    client = TestClient(main.app, raise_server_exceptions=False)
    flagged = {r.prefix for m in modules for r in m.routes if r.signed_in}
    assert MEMBERS_ONLY <= flagged, MEMBERS_ONLY - flagged
    for prefix in sorted(MEMBERS_ONLY | flagged):
        # A longer prefix belongs to another router ("/api/yucg/releases" is not "/api/yucg").
        mine = [(p, m) for p, ops in paths.items() if p == prefix or p.startswith(prefix + "/")
                for m in ops if not any(o != prefix and o.startswith(prefix + "/") and p.startswith(o)
                                        for o in prefixes)]
        assert mine, prefix
        for path, method in mine:
            status = client.request(method.upper(), path.replace("{", "").replace("}", "")).status_code
            assert status == 401, (method, path, status)

    # Beta never schedules a job that sends or reads members' mail.
    class Recorder:
        def __init__(self):
            self.ids = []

        def add_job(self, fn, trigger, *, id, **kw):
            self.ids.append(id)

    production, beta = Recorder(), Recorder()
    schedule(production, modules, beta=False)
    schedule(beta, modules, beta=True)
    mail = {j.id for m in modules for j in m.jobs if j.sends_mail}
    assert mail and set(production.ids) == set(job_ids), production.ids
    assert not mail & set(beta.ids) and set(beta.ids) == set(job_ids) - mail, beta.ids
    # The jobs that send or read members' mail today; a manifest that drops
    # the flag would let beta send.
    assert {"follow_up_sequences", "notification_digests", "gmail_reply_sync", "campaign_drain"} <= mail, mail


if __name__ == "__main__":
    tests()
    print("modules: ok")
