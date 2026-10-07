
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from backend directory so it works whether we're run from repo root or backend/
_backend_dir = Path(__file__).resolve().parent
_env_path = _backend_dir / ".env"
load_dotenv(_env_path, override=True)
if _env_path.exists():
    _cid = (os.getenv("SLACK_CLIENT_ID") or "").strip()
    _secret = (os.getenv("SLACK_CLIENT_SECRET") or "").strip()
    print(f"[env] Loaded backend/.env (Slack: client_id={'set' if _cid else 'NOT SET'}, client_secret={'set' if _secret else 'NOT SET'})")
else:
    print(f"[env] No backend/.env found at {_env_path}")

from contextlib import asynccontextmanager
import logging
from app.log_redaction import RedactAccessPath
logging.getLogger("uvicorn.access").addFilter(RedactAccessPath())

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth_deps import get_current_user
from app.services.llm import list_models
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.database import init_db
from app.routers import contacts, emails, campaigns, analytics, settings, auth, outreach, track, admin, attachments, telemetry, operations, yucgoutreach, yucg_prospects, releases, projects
from app.routers.campaigns import drain_releasing_campaigns
from app.services.follow_up_job import run_follow_up_sequences
from app.services.notification_digest_job import run_notification_digests
from app.services.gmail_reply_sync import sync_replies_all_senders
from app.services.yucgoutreach_discovery import (
    drain_queued_yucgoutreach_runs,
    recover_interrupted_yucgoutreach_runs,
)
from app.services.company_register import drain_company_register
from app.services.roster_watch import drain_roster_queue, enroll_prospect_companies
from app.services.roster_email import drain_roster_emails, drain_roster_verification
from app.services.roster_adjudicate import drain_roster_adjudication

# CORS: use CORS_ORIGINS env (comma-separated) when going public; default localhost for dev
_default_origins = [
    "http://localhost:5173", "http://127.0.0.1:5173",
    "https://localhost:5173", "https://127.0.0.1:5173",
]
_cors_origins = os.getenv("CORS_ORIGINS", "").strip()
CORS_ORIGINS = [o.strip() for o in _cors_origins.split(",") if o.strip()] if _cors_origins else _default_origins


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await recover_interrupted_yucgoutreach_runs()
    await enroll_prospect_companies()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        drain_queued_yucgoutreach_runs,
        "interval",
        seconds=10,
        id="company_discovery_queue",
        max_instances=1,
        coalesce=True,
    )
    # Bulk register ingest: one step per pass (ticker list, then a Form D
    # quarter, then sector backfill), so no pass is long or bursty at SEC.
    scheduler.add_job(
        drain_company_register,
        "interval",
        minutes=int(os.getenv("REGISTER_INGEST_MINUTES", "20") or 20),
        id="company_register_ingest",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        drain_roster_queue,
        "interval",
        seconds=60,
        id="company_roster_watch",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        drain_roster_emails,
        "interval",
        seconds=300,
        id="company_roster_emails",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        drain_roster_adjudication,
        "interval",
        minutes=20,
        id="company_roster_adjudicate",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        drain_roster_verification,
        "cron",
        hour=7,
        minute=30,
        id="company_roster_verification",
        max_instances=1,
        coalesce=True,
    )
    if os.getenv('APP_ENV', 'production').lower() == 'beta':
        # Beta neither sends mail nor synchronizes real Gmail accounts in background jobs.
        scheduler.start()
        yield
        scheduler.shutdown(wait=False)
        return
    scheduler.add_job(
        run_follow_up_sequences,
        "cron",
        hour=8,
        minute=0,
        id="follow_up_sequences",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        run_notification_digests,
        "cron",
        hour=8,
        minute=5,
        id="notification_digests",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        sync_replies_all_senders,
        "cron",
        minute="*/2",
        id="gmail_reply_sync",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        drain_releasing_campaigns,
        "cron",
        minute="*/5",
        id="campaign_drain",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(
    lifespan=lifespan,
    title="YUCG Outreach",
    description="Member-owned client outreach and project coordination for YUCG",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def private_api_headers(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
    return response


_require_user = [Depends(get_current_user)]
app.include_router(contacts.router, prefix="/api/contacts", tags=["contacts"], dependencies=_require_user)
app.include_router(emails.router, prefix="/api/emails", tags=["emails"], dependencies=_require_user)
app.include_router(campaigns.router, prefix="/api/campaigns", tags=["campaigns"], dependencies=_require_user)
app.include_router(analytics.router, prefix="/api/analytics", tags=["analytics"], dependencies=_require_user)
from app.routers import invitations, workspace, activity
app.include_router(activity.router, prefix="/api/activity", tags=["activity"])
app.include_router(workspace.router, prefix="/api/workspace", tags=["workspace"])
app.include_router(invitations.router, prefix="/api/admin/invitations", tags=["invitations"])
app.include_router(settings.router, prefix="/api/settings", tags=["settings"])
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(outreach.router, prefix="/api/outreach", tags=["outreach"], dependencies=_require_user)
app.include_router(track.router, prefix="/api/track", tags=["track"])
# Slack signs its own requests and carries no club session, so this door
# authenticates itself rather than using the session dependency.
from app.routers import slack as slack_router
app.include_router(slack_router.router, prefix="/api/slack", tags=["slack"])
app.include_router(admin.router, prefix="/api/admin", tags=["admin"])
app.include_router(attachments.router, prefix="/api/attachments", tags=["attachments"])
app.include_router(telemetry.router, prefix="/api/telemetry", tags=["telemetry"])
app.include_router(operations.router, prefix="/api/admin/operations", tags=["operations"])
app.include_router(yucgoutreach.router, prefix="/api/yucgoutreach", tags=["yucgoutreach"])
app.include_router(yucg_prospects.router, prefix="/api/yucg", tags=["yucg-coordinator"])
app.include_router(releases.router, prefix="/api/yucg/releases", tags=["releases"], dependencies=_require_user)
app.include_router(projects.router, prefix="/api/projects", tags=["projects"])


@app.get("/api/health")
async def health_check():
    return {"status": "ok", "service": "YUCG Outreach"}


@app.get("/api/ai/models")
async def ai_models(_user: dict = Depends(get_current_user)):
    return list_models()


def spa_file(root: Path, full_path: str) -> Path:
    """Serve a real file if it exists under dist; otherwise index.html (React routes)."""
    root = root.resolve()
    index = root / "index.html"
    if not full_path or full_path.endswith("/"):
        return index
    candidate = (root / full_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return index
    return candidate if candidate.is_file() else index


def _mount_spa() -> None:
    """Hosted box: same process as the API. Localhost still uses Vite on :5173."""
    raw = (os.getenv("FRONTEND_DIST") or "").strip()
    root = Path(raw) if raw else _backend_dir / "frontend_dist"
    if not root.is_dir():
        return
    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    assets = root / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="spa-assets")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        return FileResponse(spa_file(root, full_path))


_mount_spa()
