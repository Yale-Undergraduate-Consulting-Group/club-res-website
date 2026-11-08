
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

import asyncio
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
from app.modules import all_modules, mount, schedule, start
from app.services.scheduler_lease import keep_lease

# Every app on the platform, core first (app/modules, docs/PLATFORM.md).
MODULES = all_modules()

# CORS: use CORS_ORIGINS env (comma-separated) when going public; default localhost for dev
_default_origins = [
    "http://localhost:5173", "http://127.0.0.1:5173",
    "https://localhost:5173", "https://127.0.0.1:5173",
]
_cors_origins = os.getenv("CORS_ORIGINS", "").strip()
CORS_ORIGINS = [o.strip() for o in _cors_origins.split(",") if o.strip()] if _cors_origins else _default_origins


@asynccontextmanager
async def lifespan(app: FastAPI):
    # The frozen baseline, then every app's migrations once each (app/migrations.py).
    await init_db()
    await start(MODULES)
    # A deploy overlaps the old and new containers; only the lease holder runs jobs.
    lease_stop = asyncio.Event()
    lease_task = asyncio.create_task(keep_lease(lease_stop))
    scheduler = AsyncIOScheduler()
    # Beta neither sends mail nor synchronizes real Gmail accounts in background jobs.
    schedule(scheduler, MODULES, beta=os.getenv('APP_ENV', 'production').lower() == 'beta')
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)
    lease_stop.set()
    await lease_task


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


mount(app, MODULES)


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
