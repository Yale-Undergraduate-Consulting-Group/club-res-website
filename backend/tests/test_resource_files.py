"""Managing a resource's files from its page: who may upload, replace, restore
and delete; quota; local-mode upload end to end; re-indexing; S3-mode signing.
Synthetic data only."""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JWT_SECRET", "resource-files-test-secret-value-123456")
os.environ.pop("DOCUMENTS_BUCKET", None)
os.environ.pop("CATALOG_BUCKET", None)
os.environ.pop("APP_ENV", None)

from botocore.exceptions import ClientError  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from app.database import get_db, init_db  # noqa: E402
from app.jwt_utils import create_token  # noqa: E402
from app.routers import workspace  # noqa: E402
from app.services import document_storage, resources  # noqa: E402

ADMIN, TEAM, MEMBER, TEAM_OTHER, ASSIGNED = 1, 2, 3, 4, 5


def client_for(user_id, role="standard"):
    client = TestClient(main.app, raise_server_exceptions=False)
    client.cookies.set("yucg_session", create_token(user_id, f"{user_id}@yale.edu", None, None, role))
    return client


async def scalar(db, sql, *args):
    return (await (await db.execute(sql, args)).fetchone())[0]


def upload(client, resource_id, name, data, file_id=None, content_type="text/plain"):
    """The browser's three steps in local mode: reserve, PUT, complete."""
    body = {"filename": name, "byte_size": len(data), "content_type": content_type}
    if file_id is not None:
        body["file_id"] = file_id
    start = client.post(f"/api/resources/{resource_id}/files/uploads", json=body)
    if start.status_code != 200:
        return start, None
    reserved = start.json()
    assert reserved["upload"]["method"] == "PUT"
    put = client.put(reserved["upload"]["url"], content=data)
    assert put.status_code == 200, put.text
    done = client.post(f"/api/resources/{resource_id}/files/{reserved['file_id']}/uploads/{reserved['version_id']}/complete")
    return done, reserved


def titles(client, query):
    return {i["title"] for i in client.get("/api/resources", params={"q": query}).json()["items"]}


def files_of(item):
    return {f["filename"]: f for f in item["files"]}


class FakeS3:
    """Just enough of the S3 calls the upload flow makes."""

    def __init__(self):
        self.objects = {}  # key -> (bytes, content_type, version, metadata)
        self.calls = []

    async def __call__(self, method, **kwargs):
        self.calls.append((method, kwargs))
        if method == "generate_presigned_url":
            return f"https://documents.example.test/{kwargs['Params']['Key']}?sig=1"
        key = kwargs.get("Key")
        if method == "head_object":
            if key not in self.objects:
                raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
            body, content_type, version, metadata = self.objects[key]
            return {"ContentLength": len(body), "ContentType": content_type, "VersionId": version, "Metadata": metadata}
        if method == "put_object":
            if key in self.objects:
                raise ClientError({"Error": {"Code": "412"}}, "PutObject")
            self.objects[key] = (kwargs["Body"], kwargs["ContentType"], f"seal-{len(self.objects)}", kwargs.get("Metadata", {}))
            return {"VersionId": self.objects[key][2]}
        if method == "download_file":
            assert kwargs["ExtraArgs"]["VersionId"] == self.objects[key][2]
            Path(kwargs["Filename"]).write_bytes(self.objects[key][0])
            return None
        raise AssertionError(method)


async def run():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        os.environ["DATABASE_URL"] = f"sqlite:///{tmp}/test.db"
        os.environ["DOCUMENTS_LOCAL_DIR"] = str(tmp / "files")
        await init_db()
        db = await get_db()
        await db.executemany(
            "INSERT INTO users(id,email,name,role,is_active) VALUES (?,?,?,?,1)",
            [(ADMIN, "1@yale.edu", "Club Admin", "admin"), (TEAM, "2@yale.edu", "Team Person", "standard"),
             (MEMBER, "3@yale.edu", "Plain Member", "standard"), (TEAM_OTHER, "4@yale.edu", "Other Team", "standard"),
             (ASSIGNED, "5@yale.edu", "Assigned Person", "standard")])
        await db.execute("INSERT INTO projects(id, name) VALUES (1, 'Assigned project')")
        await db.execute("INSERT INTO user_project_assignments(user_id, project_id) VALUES (?, 1)", (ASSIGNED,))
        stamp = resources.now()
        # r1: public past project, TEAM is on it; r2: public past project, TEAM_OTHER (only) is on it;
        # r3: a template (admins manage); r4: private past project, TEAM on it; r5: public past project reached by project assignment.
        for rid, kind, title, disclosure, project in (
            (1, "past_project", "Alpha Project", "public", None), (2, "past_project", "Bravo Project", "public", None),
            (3, "template", "Slide Template", "public", None), (4, "past_project", "Hidden Project", "private", None),
            (5, "past_project", "Echo Project", "public", 1),
        ):
            await db.execute(
                "INSERT INTO resources(id, kind, title, disclosure, project_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                (rid, kind, title, disclosure, project, stamp, stamp))
        for rid, uid in ((1, TEAM), (2, TEAM_OTHER), (4, TEAM)):
            await db.execute("INSERT INTO resource_team(resource_id, role, name, name_key, user_id) VALUES (?, 'Associate', 'n', ?, ?)",
                             (rid, f"n{rid}{uid}", uid))
        await db.commit()
        for rid in range(1, 6):
            await db.execute("BEGIN IMMEDIATE")
            await resources.reindex(db, rid)
            await db.commit()

        admin, team, member, other, assigned = (client_for(ADMIN, "admin"), client_for(TEAM), client_for(MEMBER),
                                                client_for(TEAM_OTHER), client_for(ASSIGNED))

        # --- can_manage on the resource shapes ------------------------------------
        def manage_flags(client):
            return {i["title"]: i["can_manage"] for i in client.get("/api/resources").json()["items"]}

        assert manage_flags(admin) == {"Alpha Project": True, "Bravo Project": True, "Slide Template": True,
                                       "Hidden Project": True, "Echo Project": True}
        assert manage_flags(team) == {"Alpha Project": True, "Bravo Project": False, "Slide Template": False,
                                      "Hidden Project": True, "Echo Project": False}
        assert manage_flags(assigned)["Echo Project"] is True, "a project assignment is team membership too"
        assert not any(manage_flags(member).values())
        assert client_for(MEMBER).get("/api/resources/4").status_code == 404

        # --- Upload: local mode end to end ------------------------------------------
        assert document_storage.local_enabled()
        done, reserved = upload(team, 1, "alpha notes.txt", b"alpacas graze quietly")
        assert done.status_code == 200, done.text
        item = done.json()
        notes = files_of(item)["alpha notes.txt"]
        assert notes["id"] == reserved["file_id"] and notes["version_id"] == reserved["version_id"] and item["can_manage"]
        file1, v1 = notes["id"], notes["version_id"]
        assert titles(member, "alpacas") == {"Alpha Project"}, "a new file's text is searchable at once"
        # The signed link is single purpose: a download link cannot write, and a stranger's session cannot use an upload link.
        dl = member.get(f"/api/resources/1/files/{file1}/download").json()
        assert member.put(dl["url"].replace("local-file", "local-upload"), content=b"x").status_code == 404
        assert member.get(dl["url"]).content == b"alpacas graze quietly"
        again = team.post("/api/resources/1/files/uploads", json={"filename": "n2.txt", "byte_size": 3, "content_type": "text/plain"}).json()
        assert member.put(again["upload"]["url"], content=b"abc").status_code == 404, "not the uploader"
        assert admin.put(again["upload"]["url"], content=b"abc").status_code == 404
        # Exactly the reserved size; an existing object is never overwritten.
        assert team.put(again["upload"]["url"], content=b"abcd").status_code == 413
        assert team.put(again["upload"]["url"], content=b"ab").status_code == 422
        assert team.put(again["upload"]["url"], content=b"abc").status_code == 200
        assert team.put(again["upload"]["url"], content=b"abc").status_code == 409
        assert team.put("/api/resources/local-upload?token=garbage", content=b"abc").status_code == 404
        # Complete is idempotent and only for the uploader's own reservation.
        path = f"/api/resources/1/files/{again['file_id']}/uploads/{again['version_id']}/complete"
        assert member.post(path).status_code == 403 and other.post(path).status_code == 403
        assert admin.post(path).status_code == 404, "another member's reservation"
        assert team.post(path).status_code == 200 and team.post(path).status_code == 200
        assert await scalar(db, "SELECT COUNT(*) FROM workspace_document_versions WHERE document_id=? AND state='ready'", again["file_id"]) == 1

        # Completing without the bytes, or with the wrong size on disk, fails.
        empty = team.post("/api/resources/1/files/uploads", json={"filename": "empty.txt", "byte_size": 5, "content_type": "text/plain"}).json()
        assert team.post(f"/api/resources/1/files/{empty['file_id']}/uploads/{empty['version_id']}/complete").status_code == 409

        # --- Authorization matrix: start upload ------------------------------------
        body = {"filename": "x.txt", "byte_size": 4, "content_type": "text/plain"}
        starts = {  # (client, resource) -> status
            (admin, 3): 200, (admin, 4): 200, (team, 4): 200, (assigned, 5): 200,
            (team, 2): 403, (team, 3): 403, (member, 1): 403, (member, 3): 403, (other, 1): 403,
            (member, 4): 404, (other, 4): 404, (team, 999): 404,
        }
        for (client, rid), status in starts.items():
            assert client.post(f"/api/resources/{rid}/files/uploads", json=body).status_code == status, (rid, status)
        # file_id of another resource's file, or missing: 404 (never a way to touch it)
        assert team.post("/api/resources/1/files/uploads", json={**body, "file_id": 99999}).status_code == 404
        r2 = upload(other, 2, "bravo.txt", b"bravo text")[0].json()
        file2 = files_of(r2)["bravo.txt"]["id"]
        assert other.post("/api/resources/1/files/uploads", json={**body, "file_id": file2}).status_code == 403
        assert team.post("/api/resources/1/files/uploads", json={**body, "file_id": file2}).status_code == 404
        assert admin.post("/api/resources/1/files/uploads", json={**body, "file_id": file2}).status_code == 404

        # --- Limits -----------------------------------------------------------------
        for bad in ({"byte_size": 0}, {"byte_size": 100 * 1024 * 1024 + 1}, {"filename": ""}, {"content_type": ""}, {"filename": "a" * 256}):
            assert team.post("/api/resources/1/files/uploads", json={**body, **bad}).status_code == 422, bad

        # --- Quota: page uploads count, the import's versions do not ------------------
        before = (await workspace.storage_usage(db, TEAM))["reserved_bytes"]
        assert before > 0, "the team member's page uploads are charged to them"
        await db.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)", (f"document_quota_bytes:{TEAM}", str(before + 10)))
        await db.commit()
        over = team.post("/api/resources/1/files/uploads", json={**body, "byte_size": 11})
        assert over.status_code == 409 and over.json()["detail"]["available_bytes"] == 10, over.text
        assert team.post("/api/resources/1/files/uploads", json={**body, "byte_size": 10}).status_code == 200
        assert (await workspace.storage_usage(db, TEAM))["available_bytes"] == 0, "a pending reservation also holds space"
        assert team.post("/api/resources/1/files/uploads", json={**body, "byte_size": 1}).status_code == 409
        await db.execute("DELETE FROM settings WHERE key = ?", (f"document_quota_bytes:{TEAM}",))
        # A version the import stored server-side (its key prefix) is nobody's upload.
        admin_before = (await workspace.storage_usage(db, ADMIN))["reserved_bytes"]
        cursor = await db.execute("INSERT INTO workspace_documents(title, owner_user_id, created_at) VALUES ('i', ?, 1)", (ADMIN,))
        await db.execute(
            """INSERT INTO workspace_document_versions(document_id, uploaded_by, object_key, filename, byte_size, content_type, state, created_at)
               VALUES (?,?,?,?,?,?, 'ready', 1)""",
            (cursor.lastrowid, ADMIN, resources.IMPORT_KEY_PREFIX + "imported", "i.pptx", 5_000_000, "application/octet-stream"))
        await db.commit()
        assert (await workspace.storage_usage(db, ADMIN))["reserved_bytes"] == admin_before

        # --- Replace, versions, restore, re-indexing ------------------------------------
        done, _ = upload(team, 1, "alpha notes.txt", b"llamas hum loudly", file_id=file1)
        assert done.status_code == 200, done.text
        current = files_of(done.json())["alpha notes.txt"]
        v2 = current["version_id"]
        assert v2 != v1 and current["id"] == file1 and len(done.json()["files"]) == 2, "a replacement is a new version of the same file"
        assert titles(member, "llamas") == {"Alpha Project"} and titles(member, "alpacas") == set()
        listed = team.get(f"/api/resources/1/files/{file1}/versions")
        assert [(v["id"], v["current"]) for v in listed.json()] == [(v2, True), (v1, False)]
        assert listed.json()[0]["uploaded_by_name"] == "Team Person" and listed.json()[0]["byte_size"] == 17
        old = team.get(f"/api/resources/1/files/{file1}/download", params={"version_id": v1}).json()
        assert team.get(old["url"]).content == b"alpacas graze quietly"
        # Earlier versions are for managers: not via the API, and not through a link someone passes along.
        assert member.get(f"/api/resources/1/files/{file1}/download", params={"version_id": v1}).status_code == 403
        assert member.get(f"/api/resources/1/files/{file1}/download", params={"version_id": v2}).status_code == 403
        assert member.get(old["url"]).status_code == 404
        assert member.get(f"/api/resources/1/files/{file1}/download").status_code == 200
        assert other.get(f"/api/resources/1/files/{file1}/download", params={"version_id": v1}).status_code == 403
        assert admin.get(f"/api/resources/1/files/{file1}/download", params={"version_id": v1}).status_code == 200
        assert team.get(f"/api/resources/1/files/{file1}/download", params={"version_id": 99999}).status_code == 404

        restored = team.post(f"/api/resources/1/files/{file1}/versions/{v1}/restore")
        assert restored.status_code == 200 and files_of(restored.json())["alpha notes.txt"]["version_id"] == v1
        assert titles(member, "alpacas") == {"Alpha Project"} and titles(member, "llamas") == set(), "restore re-indexes the restored text"
        assert team.post(f"/api/resources/1/files/{file1}/versions/{v1}/restore").status_code == 200, "restoring the current version is a no-op"
        assert team.post(f"/api/resources/1/files/{file1}/versions/99999/restore").status_code == 404

        # --- Authorization matrix: versions / restore / delete / download?version_id ---
        for client, status in ((member, 403), (other, 403)):
            assert client.get(f"/api/resources/1/files/{file1}/versions").status_code == status
            assert client.post(f"/api/resources/1/files/{file1}/versions/{v2}/restore").status_code == status
            assert client.delete(f"/api/resources/1/files/{file1}").status_code == status
            assert client.post(f"/api/resources/1/files/{file1}/uploads/{v2}/cancel").status_code == status
        assert client_for(MEMBER).get("/api/resources/4/files/1/versions").status_code == 404
        # A file id used under another resource's id is not found, for every manager of that other resource.
        for client in (admin, other):
            assert client.get(f"/api/resources/2/files/{file1}/versions").status_code == 404
            assert client.post(f"/api/resources/2/files/{file1}/versions/{v1}/restore").status_code == 404
            assert client.delete(f"/api/resources/2/files/{file1}").status_code == 404
            assert client.get(f"/api/resources/2/files/{file1}/download", params={"version_id": v1}).status_code == 404
        assert team.get(f"/api/resources/1/files/{file2}/versions").status_code == 404
        assert admin.get(f"/api/resources/1/files/{file2}/versions").status_code == 404
        # A version of another file under this file id is not found either.
        assert team.post(f"/api/resources/1/files/{file1}/versions/{reserved['version_id'] + 100}/restore").status_code == 404

        # --- An admin-only file: managed and indexed by no one but admins' own tools ---
        sow = upload(admin, 1, "statement.txt", b"fee fourteen thousand")[0].json()
        sow_id = files_of(sow)["statement.txt"]["id"]
        await db.execute("UPDATE resource_files SET admin_only = 1 WHERE document_id = ?", (sow_id,))
        await db.commit()
        assert titles(admin, "fourteen") == {"Alpha Project"}, "text stored before it was flagged; the flag is applied below"
        await db.execute("BEGIN IMMEDIATE")
        await resources.reindex(db, 1)
        await db.commit()
        assert titles(admin, "fourteen") == set()
        sow_v = (await (await db.execute("SELECT current_version FROM workspace_documents WHERE id = ?", (sow_id,))).fetchone())[0]
        assert "statement.txt" not in files_of(team.get("/api/resources/1").json())
        for call in (lambda c: c.get(f"/api/resources/1/files/{sow_id}/versions"),
                     lambda c: c.post(f"/api/resources/1/files/{sow_id}/versions/{sow_v}/restore"),
                     lambda c: c.delete(f"/api/resources/1/files/{sow_id}"),
                     lambda c: c.get(f"/api/resources/1/files/{sow_id}/download", params={"version_id": sow_v}),
                     lambda c: c.post("/api/resources/1/files/uploads", json={**body, "file_id": sow_id})):
            assert call(team).status_code == 404 and call(member).status_code in (403, 404)
        replaced, _ = upload(admin, 1, "statement.txt", b"new fee ninety thousand", file_id=sow_id)
        assert replaced.status_code == 200, replaced.text
        assert titles(admin, "ninety") == set(), "an admin-only file's text never reaches search, replaced or not"
        assert await scalar(db, "SELECT text FROM resource_file_text WHERE document_id = ?", sow_id) == ""
        assert admin.get(f"/api/resources/1/files/{sow_id}/versions").status_code == 200

        # --- Cancel ---------------------------------------------------------------------
        fresh = team.post("/api/resources/1/files/uploads", json={"filename": "abandoned.txt", "byte_size": 3, "content_type": "text/plain"}).json()
        cancel = f"/api/resources/1/files/{fresh['file_id']}/uploads/{fresh['version_id']}/cancel"
        assert member.post(cancel).status_code == 403 and admin.post(cancel).status_code == 404
        assert team.post(cancel).json() == {"ok": True}
        assert await scalar(db, "SELECT COUNT(*) FROM resource_files WHERE document_id = ?", fresh["file_id"]) == 0, "a new file with no version is gone"
        assert await scalar(db, "SELECT COUNT(*) FROM workspace_documents WHERE id = ?", fresh["file_id"]) == 0
        assert team.put(fresh["upload"]["url"], content=b"abc").status_code == 404, "a cancelled reservation takes no bytes"
        assert team.post(cancel).status_code == 404
        retry = team.post("/api/resources/1/files/uploads", json={"filename": "n.txt", "byte_size": 3, "content_type": "text/plain", "file_id": file1}).json()
        assert team.post(f"/api/resources/1/files/{file1}/uploads/{retry['version_id']}/cancel").status_code == 200
        assert [v["id"] for v in team.get(f"/api/resources/1/files/{file1}/versions").json()] == [v2, v1], "cancelling a replacement keeps the file"
        assert team.post(f"/api/resources/1/files/{file1}/uploads/{v1}/cancel").status_code == 409, "a ready version cannot be cancelled"

        # --- Delete a file -----------------------------------------------------------------
        keys = [r["object_key"] for r in await (await db.execute(
            "SELECT object_key FROM workspace_document_versions WHERE document_id = ?", (file1,))).fetchall()]
        assert len(keys) == 2 and all(document_storage.local_path(k).exists() for k in keys)
        deleted = team.delete(f"/api/resources/1/files/{file1}")
        assert deleted.status_code == 200 and "alpha notes.txt" not in files_of(deleted.json())
        assert not any(document_storage.local_path(k).exists() for k in keys), "every stored version is deleted from storage"
        assert await scalar(db, "SELECT COUNT(*) FROM workspace_document_versions WHERE document_id = ?", file1) == 0
        assert await scalar(db, "SELECT COUNT(*) FROM resource_file_text WHERE document_id = ?", file1) == 0
        assert await scalar(db, "SELECT COUNT(*) FROM resource_file_pages WHERE document_id = ?", file1) == 0
        assert titles(member, "alpacas") == set() and titles(member, "llamas") == set()
        assert team.delete(f"/api/resources/1/files/{file1}").status_code == 404
        assert member.get(f"/api/resources/1/files/{file1}/download").status_code == 404
        actions = [r["action"] for r in await (await db.execute("SELECT action FROM audit_log ORDER BY id")).fetchall()]
        for action in ("resource_file_upload", "resource_file_restore", "resource_file_delete", "resource_file_upload_cancel"):
            assert action in actions, action
        # Deleting the resource clears its files' text chunks and stored objects as well.
        kept = admin.get("/api/resources/1").json()
        kept_ids = [f["id"] for f in kept["files"]]
        assert kept_ids
        kept_paths = [document_storage.local_path(r["object_key"]) for r in await (await db.execute(
            f"SELECT object_key FROM workspace_document_versions WHERE document_id IN ({','.join('?' * len(kept_ids))})", kept_ids)).fetchall()]
        assert admin.delete("/api/resources/1").json() == {"ok": True}
        assert not any(p.exists() for p in kept_paths)
        assert await scalar(db, "SELECT COUNT(*) FROM resource_file_pages WHERE resource_id = 1") == 0

        # --- S3 mode: presigned PUT, head check, bucket copy for the text, sealed cancel ---
        os.environ["DOCUMENTS_BUCKET"] = "documents.example"
        fake = FakeS3()
        workspace.s3_call, original = fake, workspace.s3_call
        try:
            start = team.post("/api/resources/4/files/uploads", json={"filename": "s3.txt", "byte_size": 11, "content_type": "text/plain"})
            assert start.status_code == 200, start.text
            s3 = start.json()
            presign = next(kwargs for method, kwargs in fake.calls if method == "generate_presigned_url")
            assert presign["Params"]["Bucket"] == "documents.example" and presign["Params"]["IfNoneMatch"] == "*"
            assert presign["Params"]["ContentLength"] == 11 and presign["ExpiresIn"] == 300
            assert s3["upload"]["headers"]["If-None-Match"] == "*" and s3["upload"]["url"].startswith("https://documents.example.test/")
            key = await scalar(db, "SELECT object_key FROM workspace_document_versions WHERE id = ?", s3["version_id"])
            assert key.startswith(resources.UPLOAD_KEY_PREFIX)
            complete = f"/api/resources/4/files/{s3['file_id']}/uploads/{s3['version_id']}/complete"
            assert team.post(complete).status_code == 409, "nothing in the bucket yet"
            fake.objects[key] = (b"hello bison", "application/octet-stream", "v-1", {})
            assert team.post(complete).status_code == 409, "content type differs from the reservation"
            fake.objects[key] = (b"hello bison", "text/plain", "v-1", {})
            assert team.post(complete).status_code == 200
            assert await scalar(db, "SELECT s3_version_id FROM workspace_document_versions WHERE id = ?", s3["version_id"]) == "v-1"
            assert titles(team, "bison") == {"Hidden Project"}, "text is extracted from the bucket copy of that version"
            assert titles(member, "bison") == set(), "a private project stays hidden"
            # Cancel seals the key with an empty marker object (a late PUT with If-None-Match then fails).
            cancel_start = team.post("/api/resources/4/files/uploads", json={"filename": "s3.txt", "byte_size": 4, "content_type": "text/plain", "file_id": s3["file_id"]}).json()
            ckey = await scalar(db, "SELECT object_key FROM workspace_document_versions WHERE id = ?", cancel_start["version_id"])
            assert team.post(f"/api/resources/4/files/{s3['file_id']}/uploads/{cancel_start['version_id']}/cancel").status_code == 200
            assert fake.objects[ckey][0] == b"" and fake.objects[ckey][3] == {"abandoned-reservation": str(cancel_start["version_id"])}
            assert await scalar(db, "SELECT state FROM workspace_document_versions WHERE id = ?", cancel_start["version_id"]) == "abandoned"
            # ...and a reservation whose real file arrived first cannot be cancelled: the browser completes it instead.
            late = team.post("/api/resources/4/files/uploads", json={"filename": "s3.txt", "byte_size": 4, "content_type": "text/plain", "file_id": s3["file_id"]}).json()
            lkey = await scalar(db, "SELECT object_key FROM workspace_document_versions WHERE id = ?", late["version_id"])
            fake.objects[lkey] = (b"late", "text/plain", "v-2", {})
            assert team.post(f"/api/resources/4/files/{s3['file_id']}/uploads/{late['version_id']}/cancel").status_code == 409
            assert team.post(f"/api/resources/4/files/{s3['file_id']}/uploads/{late['version_id']}/complete").status_code == 200
            # Downloads from the bucket are saved under the uploaded name, not the object key.
            assert team.get(f"/api/resources/4/files/{s3['file_id']}/download").status_code == 200
            get = [k for m, k in fake.calls if m == "generate_presigned_url" and k.get("ClientMethod") == "get_object"][-1]
            assert get["Params"]["ResponseContentDisposition"] == "attachment; filename=\"s3.txt\"; filename*=UTF-8''s3.txt"
            assert workspace.attachment_disposition('Café "Q3" SOW.pdf') == (
                "attachment; filename=\"Caf_ _Q3_ SOW.pdf\"; filename*=UTF-8''Caf%C3%A9%20_Q3_%20SOW.pdf")
            assert "\r" not in workspace.attachment_disposition("a\r\nSet-Cookie: x.pdf")
            # Local-upload links do not exist when a bucket is configured.
            assert team.put("/api/resources/local-upload?token=x", content=b"a").status_code == 404
        finally:
            workspace.s3_call = original
            os.environ.pop("DOCUMENTS_BUCKET")
        await db.close()


if __name__ == "__main__":
    asyncio.run(run())
    print("resource files: ok")
