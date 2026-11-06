"""Read-only tools for the Slack assistant, scoped by the web app's access rules."""
from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException

TOOL_RESULT_CHARS = 6_000


def _tool(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return {
        "toolSpec": {
            "name": name,
            "description": description,
            "inputSchema": {"json": schema},
        }
    }


TOOLS = [
    _tool(
        "search_contacts",
        "Search contacts the asking member can view. Shared catalog contacts are included; another member's private contacts are not.",
        {
            "query": {"type": "string", "description": "Name, email, title, or company text"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 10},
        },
        ["query"],
    ),
    _tool(
        "get_contact",
        "Read one contact by id if the asking member can view it.",
        {"contact_id": {"type": "integer", "minimum": 1}},
        ["contact_id"],
    ),
    _tool(
        "search_companies",
        "Search visible companies and summarize contact, campaign, mail, reply, bounce, and queue counts.",
        {
            "query": {"type": "string", "description": "Whole or partial company name"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 10},
        },
        ["query"],
    ),
    _tool(
        "list_campaigns",
        "List campaigns the asking member can view, including status and delivery counts.",
        {
            "query": {"type": "string", "description": "Optional text in the campaign name"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 10},
        },
    ),
    _tool(
        "list_my_drafts",
        "List only the asking member's saved Studio email drafts.",
        {
            "query": {"type": "string", "description": "Optional contact, company, subject, or body text"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 10},
        },
    ),
    _tool(
        "search_documents",
        "Search document titles visible to the asking member. Private and project documents remain access-controlled.",
        {
            "query": {"type": "string", "description": "Text in the document title"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 10},
        },
        ["query"],
    ),
]


def _limit(value: Any, default: int = 5) -> int:
    try:
        return max(1, min(10, int(value)))
    except (TypeError, ValueError):
        return default


def _text(value: Any, maximum: int = 500) -> str:
    return str(value or "")[:maximum]


def _bounded(value: dict[str, Any]) -> dict[str, Any]:
    """Keep a tool response from consuming the next model call's context."""
    encoded = json.dumps(value, default=str)
    if len(encoded) <= TOOL_RESULT_CHARS:
        return value
    return {"truncated": True, "summary": encoded[:TOOL_RESULT_CHARS]}


async def _search_contacts(member: dict, data: dict) -> dict:
    from app.routers.contacts import list_contacts

    result = await list_contacts(
        q=_text(data.get("query"), 200), limit=_limit(data.get("limit")), offset=0, user=member
    )
    fields = ("id", "name", "email", "title", "company", "company_domain", "pipeline_status")
    return {
        "contacts": [{key: row.get(key) for key in fields} for row in result["items"]],
        "total": result["total"],
    }


async def _get_contact(member: dict, data: dict) -> dict:
    from app.database import get_db
    from app.services.contact_access import require_contact_access

    contact_id = int(data.get("contact_id") or 0)
    db = await get_db()
    try:
        row = dict(await require_contact_access(db, contact_id, member))
    finally:
        await db.close()
    fields = (
        "id", "name", "email", "title", "company", "company_domain", "linkedin_url",
        "department", "pipeline_status", "confidence",
    )
    return {"contact": {key: row.get(key) for key in fields}}


async def _search_companies(member: dict, data: dict) -> dict:
    from app.routers.contacts import companies_summary

    rows = await companies_summary(
        q=_text(data.get("query"), 200), limit=_limit(data.get("limit")), offset=0, user=member
    )
    return {"companies": rows}


async def _list_campaigns(member: dict, data: dict) -> dict:
    from app.routers.campaigns import list_campaigns

    query = _text(data.get("query"), 200).casefold()
    rows = await list_campaigns(user=member)
    if query:
        rows = [row for row in rows if query in _text(row.get("name"), 500).casefold()]
    fields = (
        "id", "name", "status", "contact_count", "sent_count", "pending_count",
        "queued_count", "sending_count", "failed_count", "created_at", "updated_at",
    )
    return {
        "campaigns": [
            {key: row.get(key) for key in fields}
            for row in rows[: _limit(data.get("limit"))]
        ]
    }


async def _list_drafts(member: dict, data: dict) -> dict:
    from app.routers.emails import list_generated_emails

    query = _text(data.get("query"), 200).casefold()
    rows = await list_generated_emails(contact_id=None, sort="created_desc", user=member)
    if query:
        rows = [
            row for row in rows
            if query in " ".join(
                _text(row.get(key), 2_000) for key in ("name", "company", "subject", "body")
            ).casefold()
        ]
    fields = ("id", "contact_id", "name", "email", "company", "subject", "created_at")
    limit = _limit(data.get("limit"))
    drafts = []
    for row in rows[:limit]:
        draft = {key: row.get(key) for key in fields}
        draft["body"] = _text(row.get("body"), 1_500)
        drafts.append(draft)
    return {"drafts": drafts}


async def _search_documents(member: dict, data: dict) -> dict:
    from app.routers.workspace import documents

    rows = await documents(
        q=_text(data.get("query"), 200), limit=_limit(data.get("limit")), offset=0, user=member
    )
    fields = (
        "id", "title", "project_id", "project_name", "visibility",
        "owner_name", "created_at", "revision",
    )
    return {"documents": [{key: row.get(key) for key in fields} for row in rows]}



_HANDLERS = {
    "search_contacts": _search_contacts,
    "get_contact": _get_contact,
    "search_companies": _search_companies,
    "list_campaigns": _list_campaigns,
    "list_my_drafts": _list_drafts,
    "search_documents": _search_documents,
}


async def run_tool(name: str, data: Any, member: dict) -> dict[str, Any]:
    """Run one tool without revealing whether an inaccessible record exists."""
    handler = _HANDLERS.get(name)
    if handler is None:
        return {"error": "Unknown tool"}
    if not isinstance(data, dict):
        return {"error": "Tool input must be an object"}
    try:
        return _bounded(await handler(member, data))
    except (HTTPException, TypeError, ValueError, KeyError):
        return {"error": "Not found or unavailable"}
    except Exception:
        return {"error": "The requested club data is unavailable right now"}
