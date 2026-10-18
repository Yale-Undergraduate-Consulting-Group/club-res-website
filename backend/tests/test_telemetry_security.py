"""Browser telemetry cannot forge server-owned quota or activity records."""
import os
from pathlib import Path
import sys
from unittest.mock import AsyncMock, patch

os.environ.setdefault("JWT_SECRET", "telemetry-test-secret-not-production")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth_deps import get_current_user
from app.routers import telemetry


app = FastAPI()
app.include_router(telemetry.router, prefix="/api/telemetry")


with TestClient(app) as anonymous:
    response = anonymous.post("/api/telemetry/event", json={
        "event_type": "page_view", "resource_type": "studio"})
    assert response.status_code == 401

app.dependency_overrides[get_current_user] = lambda: {
    "id": 17, "email": "member@yale.edu", "role": "standard"}

with patch.object(telemetry, "log_event", new=AsyncMock()) as log:
    with TestClient(app) as member:
        for reserved in ("draft_reserved", "bedrock_reserved", "campaign_sent", "cursor"):
            response = member.post("/api/telemetry/event", json={
                "event_type": reserved, "resource_type": "studio"})
            assert response.status_code == 422, (reserved, response.text)
        assert log.await_count == 0

        response = member.post("/api/telemetry/event", json={
            "event_type": "page_view", "resource_type": "email studio"})
        assert response.status_code == 422
        response = member.post("/api/telemetry/event", json={
            "event_type": "page_view", "resource_type": "studio",
            "details": {"content": "x" * 2100}})
        assert response.status_code == 413
        assert log.await_count == 0

        response = member.post("/api/telemetry/event", json={
            "event_type": "page_view", "resource_type": "studio"})
        assert response.status_code == 200
        log.assert_awaited_once_with(
            user_id=17, event_type="page_view", resource_type="studio", details=None)

        too_many = [{"event_type": "page_view", "resource_type": "studio"}] * 51
        response = member.post("/api/telemetry/batch", json={"events": too_many})
        assert response.status_code == 422
        assert log.await_count == 1

print("Authenticated telemetry allowlist and server-event isolation passed")
