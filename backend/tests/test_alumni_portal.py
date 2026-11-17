"""Alumni portal (app/routers/alumni_portal.py, docs/product/ALUMNI-PORTAL.md):
the authorization matrix for anonymous, member, alumni, board and club admin on
every portal route; the rich-text sanitiser; posts, cover images (local storage,
magic bytes), events, the opt-in directory, the board inbox, rate limits, board
marking and account deletion. Synthetic data only.
From backend/: python tests/test_alumni_portal.py
"""
import asyncio
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_tmp = Path(tempfile.mkdtemp())
os.environ["JWT_SECRET"] = "alumni-portal-test-secret-0123456789abc"
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 't.db'}"
os.environ["DOCUMENTS_LOCAL_DIR"] = str(_tmp / "files")
os.environ["DOCUMENTS_BUCKET"] = ""
os.environ["CATALOG_BUCKET"] = ""
os.environ["APP_ENV"] = "local"

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from app.database import get_db, init_db  # noqa: E402
from app.jwt_utils import create_alumni_token, create_token  # noqa: E402
from app.routers import alumni_portal  # noqa: E402
from app.services import document_storage  # noqa: E402
from app.services.safe_html import sanitize_rich_text  # noqa: E402

ADMIN, MEMBER = 1, 2
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 100
WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"\x00" * 50
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
HTML = b"<html><script>alert(1)</script></html>"


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


def client(**cookies) -> TestClient:
    c = TestClient(main.app, raise_server_exceptions=False)
    for name, value in cookies.items():
        c.cookies.set(name, value)
    return c


def member(user_id: int, role: str) -> TestClient:
    return client(yucg_session=create_token(user_id, f"{user_id}@yale.edu", None, None, role))


def alumnus(email: str, name: str) -> tuple[int, TestClient]:
    now = int(time.time())
    sql("INSERT INTO alumni_accounts (email, google_id, name, created_at, last_sign_in_at) VALUES (?, ?, ?, ?, ?)",
        email, f"g-{email}", name, now, now)
    account_id = sql("SELECT id FROM alumni_accounts WHERE email = ?", email)[0][0]
    return account_id, client(yucg_alumni=create_alumni_token(account_id))


async def setup() -> None:
    await init_db()
    db = await get_db()
    try:
        await db.executemany("INSERT INTO users (id, email, name, role, is_active) VALUES (?, ?, ?, ?, 1)",
                             [(ADMIN, "admin@yale.edu", "Club Admin", "admin"), (MEMBER, "ben@yale.edu", "Ben", "standard")])
        await db.commit()
    finally:
        await db.close()


def sanitiser() -> None:
    cases = {
        "<p>Hi<script>alert(1)</script></p>": "<p>Hi</p>",
        '<p onclick="steal()" style="color:red" class="x">Hi</p>': "<p>Hi</p>",
        "<img src=x onerror=alert(1)>after": "after",
        '<a href="javascript:alert(1)">x</a>': "<a>x</a>",
        '<a href="JaVaScRiPt:alert(1)">x</a>': "<a>x</a>",
        '<a href="java&#9;script:alert(1)">x</a>': "<a>x</a>",
        '<a href=" &#106;avascript:alert(1)">x</a>': "<a>x</a>",
        '<a href="\x01javascript:alert(1)">x</a>': "<a>x</a>",
        '<a href="data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==">x</a>': "<a>x</a>",
        '<a href="DATA:image/svg+xml,<svg onload=alert(1)>">x</a>': "<a>x</a>",
        '<a href="vbscript:msgbox(1)">x</a>': "<a>x</a>",
        '<a href="/admin">x</a>': "<a>x</a>",
        '<a href="//evil.example">x</a>': "<a>x</a>",
        '<a href="https://example.org/a?b=1&c=2" onmouseover="x()" target="_top">ok</a>':
            '<a href="https://example.org/a?b=1&amp;c=2" rel="noopener noreferrer">ok</a>',
        '<a href="mailto:board@example.org">mail</a>': '<a href="mailto:board@example.org" rel="noopener noreferrer">mail</a>',
        "<svg><script>alert(1)</script><a>x</a></svg>after": "after",
        '<iframe src="https://evil.example"></iframe><style>p{}</style><p>t</p>': "<p>t</p>",
        "<b>&lt;script&gt;</b>": "<b>&lt;script&gt;</b>",
        "<p><strong>bold": "<p><strong>bold</strong></p>",
        "<h2>T</h2><ul><li>a</li></ul><blockquote>q</blockquote>": "<h2>T</h2><ul><li>a</li></ul><blockquote>q</blockquote>",
        "<!-- <script>x</script> --><p>c</p>": "<p>c</p>",
        '<form action="https://evil"><input name=x><button>go</button></form>': "go",
    }
    for given, wanted in cases.items():
        got = sanitize_rich_text(given)
        assert got == wanted, (given, got, wanted)


def portal_routes() -> list[tuple[str, str]]:
    return [(path, method.upper()) for path, ops in main.app.openapi()["paths"].items() for method in ops
            if (path.startswith(("/api/alumni/", "/api/admin/alumni/")) or path == "/api/admin/alumni")
            and path != "/api/alumni/logout"]


#: Portal routes an ordinary alumnus may use (everything else on the alumni door is the board's).
READER = {
    ("/api/alumni/me", "GET"), ("/api/alumni/home", "GET"), ("/api/alumni/posts", "GET"),
    ("/api/alumni/posts/{post_id}", "GET"), ("/api/alumni/images/{image_id}", "GET"), ("/api/alumni/events", "GET"),
    ("/api/alumni/directory", "GET"), ("/api/alumni/profile", "GET"), ("/api/alumni/profile", "PUT"),
    ("/api/alumni/profile", "DELETE"), ("/api/alumni/profile/hide", "POST"), ("/api/alumni/messages", "POST"),
}
#: Club-admin routes that are about who may sign in: never the board's.
SIGN_IN_ADMIN = {"/api/admin/alumni", "/api/admin/alumni/domains", "/api/admin/alumni/domains/{domain}",
                 "/api/admin/alumni/emails", "/api/admin/alumni/emails/{email}", "/api/admin/alumni/accounts/{account_id}",
                 "/api/admin/alumni/accounts/{account_id}/board", "/api/admin/alumni/accounts/{account_id}/revoke",
                 "/api/admin/alumni/accounts/{account_id}/restore"}


def authorization_matrix(who: dict[str, TestClient]) -> None:
    """Every portal route x anonymous, member, club admin, alumnus, board member:
    refused (401/403) exactly where the rules say, allowed (any other answer) elsewhere."""
    routes = portal_routes()
    paths = {p for p, _ in routes}
    assert {p for p, _ in READER} <= paths and "/api/alumni/inbox" in paths and "/api/admin/alumni/posts" in paths
    assert not [p for p in paths if "resource" in p or "project" in p], paths
    wrong = []
    for path, method in routes:
        url = path
        for name in ("post_id", "image_id", "event_id", "message_id", "account_id"):
            url = url.replace("{" + name + "}", "999999")
        url = url.replace("{change}", "archive").replace("{domain}", "nowhere.example").replace("{email}", "x%40nowhere.example")
        if path.startswith("/api/alumni/"):
            expected = {"anonymous": 401, "member": 401, "admin": 401,
                        "alumnus": None if (path, method) in READER else 403, "board": None}
        else:
            expected = {"anonymous": 401, "member": 403, "admin": None, "alumnus": 401, "board": 401}
        for role, c in who.items():
            got = c.request(method, url, follow_redirects=False).status_code
            want = expected[role]
            if (want is None and got in (401, 403)) or (want is not None and got != want):
                wrong.append((role, method, path, got, want))
    assert not wrong, wrong
    # The sign-in lists and the board role stay with club admins: a board member gets
    # nothing from them, exactly like anyone without a member session.
    assert paths >= SIGN_IN_ADMIN
    print(f"portal matrix: {len(routes)} routes x {len(who)} roles, no gaps")


def board_marking(admin: TestClient, standard: TestClient, board_id: int, board: TestClient, alum: TestClient, alum_id: int) -> None:
    assert board.get("/api/alumni/me").json()["board"] is False
    assert board.post("/api/alumni/posts", json={"title": "x"}).status_code == 403
    assert standard.put(f"/api/admin/alumni/accounts/{board_id}/board", json={"board": True}).status_code == 403
    for c in (alum, board):
        # Neither an alumnus nor a board member can make anyone board, or touch the sign-in lists.
        assert c.put(f"/api/admin/alumni/accounts/{alum_id}/board", json={"board": True}).status_code == 401
        assert c.post("/api/admin/alumni/domains", json={"domain": "example.org"}).status_code == 401
        assert c.post("/api/admin/alumni/emails", json={"email": "x@example.org"}).status_code == 401
        assert c.delete("/api/admin/alumni/emails/alum%40example.org").status_code == 401
    assert admin.put("/api/admin/alumni/accounts/999999/board", json={"board": True}).status_code == 404
    assert admin.put(f"/api/admin/alumni/accounts/{board_id}/board", json={"board": True}).json() == {"board": True}
    assert board.get("/api/alumni/me").json()["board"] is True
    accounts = {a["id"]: a for a in admin.get("/api/admin/alumni").json()["accounts"]}
    assert accounts[board_id]["board"] is True and accounts[alum_id]["board"] is False
    assert sql("SELECT board_set_by FROM alumni_accounts WHERE id = ?", board_id)[0][0] == ADMIN
    assert sql("SELECT COUNT(*) FROM audit_log WHERE action = 'alumni_board_add' AND user_id = ?", ADMIN)[0][0] == 1


def posts(admin: TestClient, standard: TestClient, board: TestClient, alum: TestClient, board_id: int) -> int:
    evil = '<p onclick="x()">Hello <script>alert(1)</script><a href="javascript:alert(1)">me</a><img src=x onerror=alert(1)></p>'
    draft = board.post("/api/alumni/posts", json={"title": " Reunion ", "body_html": evil}).json()
    assert draft["status"] == "draft" and draft["title"] == "Reunion" and draft["author"] == "Board Alum"
    assert draft["body_html"] == "<p>Hello <a>me</a></p>", draft["body_html"]
    stored = sql("SELECT body_html FROM alumni_posts WHERE id = ?", draft["id"])[0][0]
    assert "script" not in stored and "onclick" not in stored and "javascript" not in stored and "onerror" not in stored
    # Drafts are the board's.
    assert [p["id"] for p in alum.get("/api/alumni/posts").json()["items"]] == []
    assert alum.get(f"/api/alumni/posts/{draft['id']}").status_code == 404
    assert [p["id"] for p in board.get("/api/alumni/posts").json()["items"]] == [draft["id"]]
    assert board.post("/api/alumni/posts", json={"title": "Empty", "body_html": "<script>x</script>", "status": "published"}).status_code == 422
    # Ordinary alumni cannot write, edit or delete anyone's post.
    assert alum.post("/api/alumni/posts", json={"title": "Mine", "body_html": "<p>x</p>"}).status_code == 403
    assert alum.put(f"/api/alumni/posts/{draft['id']}", json={"title": "Hijack", "body_html": "<p>x</p>", "status": "published"}).status_code == 403
    assert alum.delete(f"/api/alumni/posts/{draft['id']}").status_code == 403
    published = board.put(f"/api/alumni/posts/{draft['id']}", json={
        "title": "Reunion", "body_html": "<p>See you <strong>there</strong></p>", "status": "published", "pinned": True}).json()
    assert published["status"] == "published" and published["pinned"] and published["published_at"]
    # A club admin writes through the admin door; the newest pinned post comes first.
    other = admin.post("/api/admin/alumni/posts", json={"title": "From the club", "body_html": "<p>Hi</p>", "status": "published"}).json()
    assert other["author"] == "Club Admin"
    feed = alum.get("/api/alumni/posts").json()
    assert [p["id"] for p in feed["items"]] == [draft["id"], other["id"]] and feed["total"] == 2
    assert alum.get(f"/api/alumni/posts/{draft['id']}").json()["body_html"] == "<p>See you <strong>there</strong></p>"
    # Members never read alumni posts; a club admin reads them through the admin door.
    assert standard.get("/api/alumni/posts").status_code == 401 and standard.get("/api/admin/alumni/posts").status_code == 403
    assert admin.get("/api/admin/alumni/posts").json()["total"] == 2
    assert admin.delete(f"/api/admin/alumni/posts/{other['id']}").json() == {"ok": True}
    assert alum.get(f"/api/alumni/posts/{other['id']}").status_code == 404
    actions = sql("SELECT action, user_id, details FROM audit_log WHERE resource_type = 'alumni_post' ORDER BY id")
    assert [a[0] for a in actions] == ["alumni_post_create", "alumni_post_update", "alumni_post_create", "alumni_post_delete"], actions
    assert f'"alumni_account_id": {board_id}' in actions[0][2] and actions[0][1] is None and actions[2][1] == ADMIN
    return draft["id"]


def upload(c: TestClient, base: str, data: bytes, content_type: str, size: int | None = None):
    reserved = c.post(f"{base}/images", json={"byte_size": size or len(data), "content_type": content_type})
    assert reserved.status_code == 201, reserved.text
    body = reserved.json()
    assert body["upload"]["url"].startswith(f"{base}/images/local-upload?token="), body
    key = sql("SELECT object_key FROM alumni_post_images WHERE id = ?", body["image_id"])[0][0]
    sent = c.put(body["upload"]["url"], content=data, headers={"Content-Type": content_type})
    assert sent.status_code != 200 or document_storage.local_path(key).read_bytes() == data
    return body["image_id"], sent, c.post(f"{base}/images/{body['image_id']}/complete"), key


def s3_covers() -> None:
    """With a bucket: completion reads only the first 16 bytes (a ranged GET of
    the exact version), and serving streams the object in chunks with the
    verified type and nosniff. S3 is faked."""
    from unittest.mock import patch

    from fastapi import HTTPException

    from app.services import alumni_covers

    class Body:
        def __init__(self, data: bytes):
            self.data, self.reads, self.closed = data, [], False

        def read(self, n: int = -1) -> bytes:
            self.reads.append(n)
            chunk, self.data = self.data[:n], self.data[n:]
            return chunk

        def close(self) -> None:
            self.closed = True

    calls = []
    head = {"ContentLength": len(PNG), "ContentType": "image/png", "VersionId": "v1"}

    async def fake(method, **kwargs):
        calls.append((method, kwargs))
        if method == "head_object":
            return head
        return {"Body": Body(PNG if "Range" not in kwargs else PNG[:16]), "ContentLength": len(PNG)}

    row = {"storage_bucket": "docs-bucket", "object_key": "alumni/covers/k", "byte_size": len(PNG),
           "declared_type": "image/png", "verified_type": "image/png", "s3_version_id": "v1"}
    with patch.object(alumni_covers.workspace, "s3_call", fake):
        assert asyncio.run(alumni_covers.verify(row)) == ("image/png", "v1")
        ranged = [kw for m, kw in calls if m == "get_object"]
        assert ranged == [{"Bucket": "docs-bucket", "Key": "alumni/covers/k", "VersionId": "v1", "Range": "bytes=0-15"}]
        head["ContentType"] = "image/svg+xml"
        try:
            asyncio.run(alumni_covers.verify(row))
            raise AssertionError("a type mismatch must be refused")
        except HTTPException as exc:
            assert exc.status_code == 409

        async def serve():
            response = await alumni_covers.response(row)
            assert response.media_type == "image/png" and response.headers["x-content-type-options"] == "nosniff"
            return b"".join([chunk async for chunk in response.body_iterator])
        assert asyncio.run(serve()) == PNG




def covers(admin: TestClient, board: TestClient, alum: TestClient, post_id: int) -> None:
    base = "/api/alumni"
    assert board.post(f"{base}/images", json={"byte_size": 5 * 1024 * 1024 + 1, "content_type": "image/png"}).status_code == 413
    for refused in ("image/svg+xml", "text/html", "image/gif", "application/octet-stream"):
        assert board.post(f"{base}/images", json={"byte_size": 10, "content_type": refused}).status_code == 415, refused
    # Bytes that are not the image they claim are deleted and refused, whatever the declared type.
    for data, declared in ((SVG, "image/png"), (HTML, "image/jpeg"), (PNG, "image/jpeg"), (b"GIF89a" + b"\x00" * 20, "image/webp")):
        image_id, sent, done, key = upload(board, base, data, declared)
        assert sent.status_code == 200 and done.status_code == 415, (declared, done.status_code)
        assert not sql("SELECT 1 FROM alumni_post_images WHERE id = ?", image_id)
        assert not document_storage.local_path(key).exists()
    # More than reserved is refused while streaming.
    reserved = board.post(f"{base}/images", json={"byte_size": 10, "content_type": "image/png"}).json()
    assert board.put(reserved["upload"]["url"], content=PNG, headers={"Content-Type": "image/png"}).status_code == 413
    # The other two types pass; the cover serves with its verified type and nosniff.
    for data, kind in ((JPEG, "image/jpeg"), (WEBP, "image/webp")):
        assert upload(board, base, data, kind)[2].status_code == 200, kind
    image_id, sent, done, _ = upload(board, base, PNG, "image/png")
    assert done.status_code == 200
    cover = done.json()
    # Another board actor cannot complete or feed someone else's upload.
    assert admin.post(f"/api/admin/alumni/images/{image_id}/complete").status_code == 404
    # Unattached: the board sees it, an alumnus does not, nobody without the alumni session does.
    seen = board.get(cover["url"])
    assert seen.status_code == 200 and seen.content == PNG
    assert seen.headers["content-type"] == "image/png" and seen.headers["x-content-type-options"] == "nosniff"
    assert seen.headers["content-disposition"] == "inline"
    assert alum.get(cover["url"]).status_code == 404
    assert client().get(cover["url"]).status_code == 401
    # A link for another image, or an upload link, does not open it.
    other_token = document_storage.signed_token("alumni-cover", image_id + 1, 60)
    assert board.get(f"{base}/images/{image_id}?token={other_token}").status_code == 404
    upload_token = document_storage.signed_token("alumni-cover-upload", image_id, 60)
    assert board.get(f"{base}/images/{image_id}?token={upload_token}").status_code == 404
    # Attached to the published post, every signed-in alumnus may read it.
    post = alum.get(f"{base}/posts/{post_id}").json()
    updated = board.put(f"{base}/posts/{post_id}", json={
        "title": post["title"], "body_html": post["body_html"], "status": "published", "pinned": True, "cover_image_id": image_id}).json()
    assert updated["cover"]["id"] == image_id
    reader_url = alum.get(f"{base}/posts/{post_id}").json()["cover"]["url"]
    assert alum.get(reader_url).status_code == 200
    assert admin.get(admin.get(f"/api/admin/alumni/posts/{post_id}").json()["cover"]["url"]).status_code == 200
    # One cover per post.
    second = board.post(f"{base}/posts", json={"title": "Copy", "body_html": "<p>x</p>", "cover_image_id": image_id})
    assert second.status_code == 409
    # Replacing the cover deletes the old image and its bytes.
    old_key = sql("SELECT object_key FROM alumni_post_images WHERE id = ?", image_id)[0][0]
    new_id = upload(board, base, PNG, "image/png")[0]
    board.put(f"{base}/posts/{post_id}", json={"title": post["title"], "body_html": post["body_html"], "status": "published",
                                              "pinned": True, "cover_image_id": new_id})
    assert not sql("SELECT 1 FROM alumni_post_images WHERE id = ?", image_id)
    assert not document_storage.local_path(old_key).exists()
    # Unused uploads older than a day go at the next reservation.
    stale = sql("SELECT object_key FROM alumni_post_images WHERE id != ? AND state = 'ready'", new_id)
    assert stale
    sql("UPDATE alumni_post_images SET created_at = created_at - 90000 WHERE id != ?", new_id)
    upload(board, base, PNG, "image/png")
    assert sql("SELECT COUNT(*) FROM alumni_post_images WHERE created_at < ?", int(time.time()) - 86400)[0][0] == 0
    assert not any(document_storage.local_path(k).exists() for (k,) in stale)
    assert sql("SELECT 1 FROM alumni_post_images WHERE id = ?", new_id)
    actions = {r[0] for r in sql("SELECT action FROM audit_log WHERE resource_type = 'alumni_cover'")}
    assert {"alumni_cover_reserve", "alumni_cover_complete", "alumni_cover_refuse"} <= actions


def events(admin: TestClient, board: TestClient, alum: TestClient) -> None:
    base = {"title": "Dinner", "starts_local": "2031-03-10T18:30", "timezone": "America/New_York",
            "place": "New Haven", "online_url": None, "description_html": '<p>Come<script>x</script></p>'}
    created = board.post("/api/alumni/events", json=base)
    assert created.status_code == 201, created.text
    event = created.json()
    expected = int(datetime(2031, 3, 10, 18, 30, tzinfo=ZoneInfo("America/New_York")).timestamp())
    assert event["starts_at"] == expected and event["starts_local"] == "2031-03-10T18:30" and event["timezone"] == "America/New_York"
    assert event["description_html"] == "<p>Come</p>"
    for bad in ({"timezone": "Mars/Base"}, {"timezone": "../../etc/passwd"}, {"starts_local": "tomorrow evening"},
                {"place": None, "online_url": None}, {"online_url": "javascript:alert(1)"},
                {"online_url": "http://zoom.example/j/1"}, {"starts_local": "2031-03-10T18:30+05:00"}):
        assert board.post("/api/alumni/events", json={**base, **bad}).status_code == 422, bad
    online = admin.post("/api/admin/alumni/events", json={**base, "title": "Webinar", "place": None,
                                                           "online_url": "https://zoom.example/j/1", "starts_local": "2031-01-05T09:00",
                                                           "timezone": "Europe/London"}).json()
    past = board.post("/api/alumni/events", json={**base, "title": "Old gala", "starts_local": "2001-05-01T19:00"}).json()
    listed = alum.get("/api/alumni/events").json()
    assert [e["id"] for e in listed["upcoming"]] == [online["id"], event["id"]] and [e["id"] for e in listed["past"]] == [past["id"]]
    assert alum.post("/api/alumni/events", json=base).status_code == 403
    assert alum.put(f"/api/alumni/events/{event['id']}", json=base).status_code == 403
    assert alum.delete(f"/api/alumni/events/{event['id']}").status_code == 403
    moved = board.put(f"/api/alumni/events/{event['id']}", json={**base, "starts_local": "2031-03-11T18:30"}).json()
    assert moved["starts_at"] == expected + 86400
    assert board.put("/api/alumni/events/999999", json=base).status_code == 404
    assert admin.delete(f"/api/admin/alumni/events/{past['id']}").json() == {"ok": True}
    assert [e["id"] for e in alum.get("/api/alumni/events").json()["past"]] == []
    actions = [r[0] for r in sql("SELECT action FROM audit_log WHERE resource_type = 'alumni_event' ORDER BY id")]
    assert actions == ["alumni_event_create", "alumni_event_create", "alumni_event_create", "alumni_event_update", "alumni_event_delete"]


def directory(admin: TestClient, standard: TestClient, alum: TestClient, alum_id: int, other: TestClient, other_id: int) -> None:
    assert alum.get("/api/alumni/profile").json() == {"profile": None, "email": "alum@example.org"}
    assert alum.get("/api/alumni/directory").json() == {"items": []}
    profile = {"display_name": "Alex Alum", "class_year": 2019, "company": "Acme", "role": "Analyst", "city": "Boston",
               "linkedin_url": "https://www.linkedin.com/in/alex-alum", "note": "Happy to talk", "coffee_chats": True}
    # Default is unlisted, and the email is never shown unless chosen.
    saved = alum.put("/api/alumni/profile", json=profile).json()["profile"]
    assert saved["listed"] is False and saved["show_email"] is False
    assert alum.get("/api/alumni/directory").json() == {"items": []}
    for bad in ("http://www.linkedin.com/in/x", "https://linkedin.com.evil.example/in/x", "javascript:alert(1)",
                "https://example.org/in/x"):
        assert alum.put("/api/alumni/profile", json={**profile, "linkedin_url": bad}).status_code == 422, bad
    assert alum.put("/api/alumni/profile", json={**profile, "class_year": 1800}).status_code == 422
    alum.put("/api/alumni/profile", json={**profile, "listed": True})
    entry = other.get("/api/alumni/directory").json()["items"]
    assert len(entry) == 1 and entry[0]["display_name"] == "Alex Alum" and entry[0]["email"] is None and entry[0]["coffee_chats"]
    alum.put("/api/alumni/profile", json={**profile, "listed": True, "show_email": True})
    assert other.get("/api/alumni/directory?q=acme").json()["items"][0]["email"] == "alum@example.org"
    assert other.get("/api/alumni/directory?q=nobody").json() == {"items": []}
    # Members never; a club admin reads it through the admin door.
    assert standard.get("/api/alumni/directory").status_code == 401 and standard.get("/api/admin/alumni/directory").status_code == 403
    assert [e["display_name"] for e in admin.get("/api/admin/alumni/directory").json()["items"]] == ["Alex Alum"]
    # Revocation hides a listed profile at once; restoring brings it back.
    other.put("/api/alumni/profile", json={**profile, "display_name": "Olive Other", "listed": True})
    assert len(alum.get("/api/alumni/directory").json()["items"]) == 2
    admin.post(f"/api/admin/alumni/accounts/{other_id}/revoke")
    assert [e["display_name"] for e in admin.get("/api/admin/alumni/directory").json()["items"]] == ["Alex Alum"]
    admin.post(f"/api/admin/alumni/accounts/{other_id}/restore")
    assert len(alum.get("/api/alumni/directory").json()["items"]) == 2
    # Self-service hide and delete.
    assert alum.post("/api/alumni/profile/hide").json() == {"ok": True}
    assert [e["display_name"] for e in other.get("/api/alumni/directory").json()["items"]] == ["Olive Other"]
    assert alum.get("/api/alumni/profile").json()["profile"]["listed"] is False
    assert alum.delete("/api/alumni/profile").json() == {"ok": True}
    assert sql("SELECT COUNT(*) FROM alumni_profiles WHERE account_id = ?", alum_id)[0][0] == 0
    assert alum.delete("/api/alumni/profile").status_code == 404 and alum.post("/api/alumni/profile/hide").status_code == 404
    actions = [r[0] for r in sql("SELECT action FROM audit_log WHERE resource_type = 'alumni_profile' AND resource_id = ? ORDER BY id", str(alum_id))]
    assert actions[-2:] == ["alumni_profile_hide", "alumni_profile_delete"] and actions.count("alumni_profile_save") == 3


def inbox(admin: TestClient, board: TestClient, alum: TestClient) -> None:
    assert alum.post("/api/alumni/messages", json={"subject": "Hello", "body": "Can I help with recruiting?\nThanks"}).json() == {"ok": True}
    assert alum.post("/api/alumni/messages", json={"subject": " ", "body": "x"}).status_code == 422
    assert alum.get("/api/alumni/inbox").status_code == 403
    listed = board.get("/api/alumni/inbox").json()
    assert listed["unread"] == 1 and len(listed["items"]) == 1 and "body" not in listed["items"][0]
    message = listed["items"][0]
    assert message["sender"] == {"name": "Alex", "email": "alum@example.org"}, message
    opened = board.get(f"/api/alumni/inbox/{message['id']}").json()
    assert opened["body"] == "Can I help with recruiting?\nThanks" and opened["read"] is True
    assert board.get("/api/alumni/inbox").json()["unread"] == 0
    assert board.post(f"/api/alumni/inbox/{message['id']}/unread").json() == {"ok": True}
    assert board.get("/api/alumni/home").json()["unread"] == 1
    assert board.delete(f"/api/alumni/inbox/{message['id']}").status_code == 409
    assert admin.post(f"/api/admin/alumni/inbox/{message['id']}/archive").json() == {"ok": True}
    assert board.get("/api/alumni/inbox").json()["items"] == []
    assert [m["id"] for m in board.get("/api/alumni/inbox?archived=true").json()["items"]] == [message["id"]]
    assert board.post(f"/api/alumni/inbox/{message['id']}/restore").json() == {"ok": True}
    board.post(f"/api/alumni/inbox/{message['id']}/archive")
    assert board.delete(f"/api/alumni/inbox/{message['id']}").json() == {"ok": True}
    assert board.get(f"/api/alumni/inbox/{message['id']}").status_code == 404
    actions = [r[0] for r in sql("SELECT action FROM audit_log WHERE resource_type = 'alumni_message' ORDER BY id")]
    assert actions == ["alumni_message_create", "alumni_message_read", "alumni_message_archive", "alumni_message_restore",
                       "alumni_message_archive", "alumni_message_delete"], actions


def rate_limits(board: TestClient, other: TestClient) -> None:
    for n in range(alumni_portal.CONTACT_PER_HOUR):
        assert other.post("/api/alumni/messages", json={"subject": f"S{n}", "body": "b"}).status_code == 201
    assert other.post("/api/alumni/messages", json={"subject": "one more", "body": "b"}).status_code == 429
    original = alumni_portal.WRITES_PER_HOUR
    alumni_portal.WRITES_PER_HOUR = sql("SELECT COUNT(*) FROM alumni_rate_events WHERE actor LIKE 'a:%' AND kind = 'write'")[0][0] + 1
    try:
        assert board.post("/api/alumni/posts", json={"title": "ok", "body_html": "<p>x</p>"}).status_code == 201
        assert board.post("/api/alumni/posts", json={"title": "too many", "body_html": "<p>x</p>"}).status_code == 429
        assert board.post("/api/alumni/images", json={"byte_size": 10, "content_type": "image/png"}).status_code == 429
    finally:
        alumni_portal.WRITES_PER_HOUR = original


def home(board: TestClient, alum: TestClient) -> None:
    page = alum.get("/api/alumni/home").json()
    assert page["me"]["email"] == "alum@example.org" and page["me"]["board"] is False and page["unread"] is None
    assert page["profile"] == {"exists": False, "listed": False}
    assert all(p["status"] == "published" for p in page["posts"]) and len(page["posts"]) <= 3
    assert page["events"] and all(e["starts_at"] >= time.time() for e in page["events"])
    assert isinstance(board.get("/api/alumni/home").json()["unread"], int)


def account_deletion(admin: TestClient, board: TestClient, board_id: int, post_id: int) -> None:
    board.put("/api/alumni/profile", json={"display_name": "Board Alum", "listed": True})
    board.post("/api/alumni/messages", json={"subject": "From the board member", "body": "b"})
    assert admin.delete(f"/api/admin/alumni/accounts/{board_id}").json() == {"ok": True}
    assert board.get("/api/alumni/me").status_code == 401
    assert sql("SELECT COUNT(*) FROM alumni_profiles WHERE account_id = ?", board_id)[0][0] == 0
    post = admin.get(f"/api/admin/alumni/posts/{post_id}").json()
    assert post["author"] == "YUCG Alumni Board"
    kept = [m for m in admin.get("/api/admin/alumni/inbox").json()["items"] if m["subject"] == "From the board member"]
    assert len(kept) == 1 and kept[0]["sender"] is None
    assert sql("SELECT COUNT(*) FROM alumni_rate_events WHERE actor = ?", f"a:{board_id}")[0][0] == 0


def tests() -> None:
    asyncio.run(setup())
    sanitiser()
    s3_covers()
    admin, standard = member(ADMIN, "admin"), member(MEMBER, "standard")
    for email in ("alum@example.org", "other@example.org", "board@example.org"):
        assert admin.post("/api/admin/alumni/emails", json={"email": email}).status_code == 201
    alum_id, alum = alumnus("alum@example.org", "Alex")
    other_id, other = alumnus("other@example.org", "Olive")
    board_id, board = alumnus("board@example.org", "Board Alum")
    board_marking(admin, standard, board_id, board, alum, alum_id)
    authorization_matrix({"anonymous": client(), "member": standard, "admin": admin, "alumnus": alum, "board": board})
    post_id = posts(admin, standard, board, alum, board_id)
    covers(admin, board, alum, post_id)
    events(admin, board, alum)
    home(board, alum)
    directory(admin, standard, alum, alum_id, other, other_id)
    inbox(admin, board, alum)
    rate_limits(board, other)
    # Taking the board role away ends the board powers at the next request.
    assert admin.put(f"/api/admin/alumni/accounts/{other_id}/board", json={"board": True}).status_code == 200
    assert other.get("/api/alumni/inbox").status_code == 200
    assert admin.put(f"/api/admin/alumni/accounts/{other_id}/board", json={"board": False}).json() == {"board": False}
    assert other.get("/api/alumni/inbox").status_code == 403
    account_deletion(admin, board, board_id, post_id)


if __name__ == "__main__":
    tests()
    print("alumni portal: ok")
