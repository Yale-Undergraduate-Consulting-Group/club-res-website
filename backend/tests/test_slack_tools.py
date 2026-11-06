"""Slack tools preserve the website's member-level visibility rules."""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JWT_SECRET", "slack-tools-test-secret-value-1234")

from app.database import get_db, init_db  # noqa: E402
from app.services.slack_tools import run_tool  # noqa: E402


async def seed(db) -> None:
    await db.executemany(
        "INSERT INTO users(id,email,name,role,is_active) VALUES (?,?,?,?,1)",
        [
            (1, "member@yale.edu", "Member", "standard"),
            (2, "other@yale.edu", "Other", "standard"),
        ],
    )
    await db.executemany(
        "INSERT INTO contacts(id,name,email,company,owner_id) VALUES (?,?,?,?,?)",
        [
            (1, "Shared Person", "shared@example.com", "Shared Co", None),
            (2, "Own Person", "own@example.com", "Own Co", 1),
            (3, "Private Person", "private@example.com", "Private Co", 2),
        ],
    )
    await db.executemany(
        "INSERT INTO generated_emails(id,user_id,contact_id,subject,body) VALUES (?,?,?,?,?)",
        [
            (1, 1, 2, "Own draft", "Visible draft body"),
            (2, 2, 3, "Private draft", "Other member's private draft body"),
        ],
    )
    await db.executemany(
        "INSERT INTO projects(id,name) VALUES (?,?)",
        [(1, "Member Project"), (2, "Other Project")],
    )
    await db.executemany(
        "INSERT INTO user_project_assignments(user_id,project_id,role_in_project) VALUES (?,?,?)",
        [(1, 1, "member"), (2, 2, "member")],
    )
    await db.executemany(
        """INSERT INTO workspace_documents
           (id,title,owner_user_id,project_id,visibility,created_at)
           VALUES (?,?,?,?,?,1)""",
        [
            (1, "Other Private Plan", 2, None, "private"),
            (2, "Shared Club Guide", 2, None, "club"),
            (3, "Other Project Secrets", 2, 2, "project"),
            (4, "Member Project Notes", 2, 1, "project"),
        ],
    )
    await db.commit()


async def tools_never_cross_member_boundaries() -> None:
    member = {"id": 1, "email": "member@yale.edu", "role": "standard"}

    contacts = await run_tool("search_contacts", {"query": "Person", "limit": 10}, member)
    assert {row["id"] for row in contacts["contacts"]} == {1, 2}
    assert "private@example.com" not in str(contacts)

    shared = await run_tool("get_contact", {"contact_id": 1}, member)
    assert shared["contact"]["name"] == "Shared Person", "shared catalog contact was hidden"
    private = await run_tool("get_contact", {"contact_id": 3}, member)
    assert private == {"error": "Not found or unavailable"}
    assert "Private Person" not in str(private)

    drafts = await run_tool("list_my_drafts", {"limit": 10}, member)
    assert [row["id"] for row in drafts["drafts"]] == [1]
    assert "private draft" not in str(drafts).lower()

    documents = await run_tool("search_documents", {"query": "", "limit": 10}, member)
    assert {row["id"] for row in documents["documents"]} == {2, 4}
    assert "Other Private Plan" not in str(documents)
    assert "Other Project Secrets" not in str(documents)


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp) / 'slack-tools.db'}"
        await init_db()
        db = await get_db()
        try:
            await seed(db)
        finally:
            await db.close()
        await tools_never_cross_member_boundaries()
        print("slack tools: ok")


asyncio.run(main())
