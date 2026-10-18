"""Small authenticated browser telemetry surface.

Server-owned events (delivery, quotas, AI calls) are written by their services and
can never be selected by a browser request.
"""
import json
from fastapi import APIRouter, Depends
from fastapi import HTTPException
from pydantic import BaseModel, Field
from typing import Any, Literal

from app.auth_deps import get_current_user
from app.services.usage_service import log_event

router = APIRouter()


class TelemetryEvent(BaseModel):
    event_type: Literal["page_view"]
    resource_type: str = Field(min_length=1, max_length=40, pattern=r"^[a-z][a-z0-9_-]*$")
    details: dict[str, Any] | None = None


class TelemetryBatch(BaseModel):
    events: list[TelemetryEvent] = Field(min_length=1, max_length=50)


def _bounded_details(details: dict[str, Any] | None) -> dict[str, Any] | None:
    if details is not None and len(json.dumps(details, separators=(",", ":"))) > 2048:
        raise HTTPException(413, "Telemetry details are too large")
    return details


@router.post("/event")
async def record_event(payload: TelemetryEvent, user: dict = Depends(get_current_user)):
    await log_event(
        user_id=user["id"],
        event_type=payload.event_type,
        resource_type=payload.resource_type,
        details=_bounded_details(payload.details),
    )
    return {"ok": True}


@router.post("/batch")
async def record_batch(payload: TelemetryBatch, user: dict = Depends(get_current_user)):
    for ev in payload.events:
        await log_event(
            user_id=user["id"],
            event_type=ev.event_type,
            resource_type=ev.resource_type,
            details=_bounded_details(ev.details),
        )
    return {"ok": True, "count": len(payload.events)}
