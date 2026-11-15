"""Market sentiment: interview transcripts, Reddit and Google reviews.

Each analysis is a queued run owned by the member who started it (see
app/services/sentiment_runs.py); nobody else can read it, download it or
delete it. Sign-in and the app-access check come from the manifest
(app/modules/sentiment.py).
"""
from __future__ import annotations

import asyncio
import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field

from app.auth_deps import get_current_user
from app.services import sentiment_runs as runs
from app.services import sentiment_sources as sources
from app.services.sentiment_text import ALLOWED_EXTENSIONS, TranscriptError, read_lines

router = APIRouter()

MAX_FILES = 20
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_COMPETITORS = 10
MAX_SUBREDDITS = 5
MAX_PLACES = 5


def _require(configured: bool, message: str) -> None:
    if not configured:
        raise HTTPException(503, message)


async def _enqueue(user: dict, kind: str, title: str, payload: dict[str, Any]) -> dict[str, Any]:
    if await runs.active_count(user["id"]) >= runs.MAX_ACTIVE_RUNS_PER_MEMBER:
        raise HTTPException(429, f"You already have {runs.MAX_ACTIVE_RUNS_PER_MEMBER} analyses running. Wait for one to finish.")
    run_id = await runs.create_run(user["id"], kind, title, payload)
    runs.start(run_id)
    return {"id": run_id, "status": "queued"}


@router.get("/status")
async def status() -> dict[str, bool]:
    """Which outside sources this server has keys for."""
    return {"reddit": sources.reddit_configured(), "google": sources.serpapi_configured()}


@router.get("/runs")
async def list_runs(kind: Literal["transcripts", "reddit", "google"] | None = None,
                    user: dict = Depends(get_current_user)) -> list[dict]:
    return await runs.list_runs(user["id"], kind)


async def _own_run(run_id: int, user: dict) -> dict[str, Any]:
    run = await runs.get_run(run_id, user["id"])
    if not run:
        raise HTTPException(404, "Run not found")
    return run


@router.get("/runs/{run_id}")
async def get_run(run_id: int, user: dict = Depends(get_current_user)) -> dict:
    return await _own_run(run_id, user)


@router.get("/runs/{run_id}/csv")
async def download_csv(run_id: int, user: dict = Depends(get_current_user)) -> Response:
    run = await _own_run(run_id, user)
    if run["status"] != "completed" or not run["result"]:
        raise HTTPException(409, "The run has not finished")
    body = runs.to_csv(run["kind"], run["result"])
    return Response(body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="sentiment-{run["kind"]}-{run_id}.csv"'})


@router.delete("/runs/{run_id}")
async def delete_run(run_id: int, user: dict = Depends(get_current_user)) -> dict[str, bool]:
    if not await runs.delete_run(run_id, user["id"]):
        raise HTTPException(404, "Run not found")
    return {"ok": True}


# --- Interview transcripts ---------------------------------------------------

def _names(raw: str, limit: int, what: str) -> list[str]:
    names = list(dict.fromkeys(n.strip() for n in raw.split(",") if n.strip()))
    if len(names) > limit or any(len(n) > 60 for n in names):
        raise HTTPException(422, f"At most {limit} {what}, each under 60 characters")
    return names


@router.post("/runs/transcripts", status_code=202)
async def start_transcripts(
    company: str = Form(..., max_length=100),
    competitors: str = Form("", max_length=1000),
    files: list[UploadFile] = File(...),
    user: dict = Depends(get_current_user),
) -> dict:
    company = company.strip()
    if not company:
        raise HTTPException(422, "Company name is required")
    competitor_names = [c for c in _names(competitors, MAX_COMPETITORS, "competitors") if c.lower() != company.lower()]
    if not 1 <= len(files) <= MAX_FILES:
        raise HTTPException(422, f"Upload between 1 and {MAX_FILES} files")
    parsed: list[tuple[str, list[str]]] = []
    for upload in files:
        filename = (upload.filename or "").strip()
        if not filename.lower().endswith(ALLOWED_EXTENSIONS):
            raise HTTPException(422, f"{filename or 'A file'}: only .docx, .txt and .pdf files are accepted")
        data = await upload.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise HTTPException(413, f"{filename} is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")
        try:
            lines = await asyncio.to_thread(read_lines, filename, data)
        except TranscriptError as exc:
            raise HTTPException(422, f"{filename}: {exc}") from None
        if not lines:
            raise HTTPException(422, f"{filename} has no text")
        parsed.append((filename, lines))
    payload = runs.build_transcript_input(company, competitor_names, parsed)
    if payload["scored"] == 0:
        raise HTTPException(422, "The files contain no interviewee sentences to score")
    if payload["scored"] > runs.MAX_SCORED_ITEMS:
        raise HTTPException(422, f"The files hold {payload['scored']} sentences to score; the limit is "
                                 f"{runs.MAX_SCORED_ITEMS} per analysis. Split them into smaller sets.")
    noun = "transcript" if len(parsed) == 1 else "transcripts"
    return await _enqueue(user, "transcripts", f"{company}: {len(parsed)} {noun}", payload)


# --- Reddit ------------------------------------------------------------------

class RedditRun(BaseModel):
    subreddits: list[str] = Field(min_length=1, max_length=MAX_SUBREDDITS)
    query: str = Field(min_length=1, max_length=200)
    time_filter: Literal["hour", "day", "week", "month", "year", "all"] = "year"
    limit: int = Field(default=50, ge=1, le=100, description="Posts per subreddit")


@router.post("/runs/reddit", status_code=202)
async def start_reddit(body: RedditRun, user: dict = Depends(get_current_user)) -> dict:
    _require(sources.reddit_configured(), sources.REDDIT_MESSAGE)
    names = list(dict.fromkeys(re.sub(r"^/?r/", "", s.strip(), flags=re.I) for s in body.subreddits if s.strip()))
    if not names or any(not re.fullmatch(r"[A-Za-z0-9_]{2,21}", n) for n in names):
        raise HTTPException(422, "Subreddit names are 2 to 21 letters, numbers or underscores")
    query = body.query.strip()
    if not query:
        raise HTTPException(422, "A search is required")
    payload = {"subreddits": names, "query": query, "time_filter": body.time_filter, "limit": body.limit}
    return await _enqueue(user, "reddit", f"“{query}” in {', '.join('r/' + n for n in names)}", payload)


# --- Google reviews ----------------------------------------------------------

class PlaceSearch(BaseModel):
    query: str = Field(min_length=2, max_length=200)


class Place(BaseModel):
    data_id: str = Field(min_length=1, max_length=200)
    name: str = Field(default="", max_length=200)
    address: str = Field(default="", max_length=300)
    rating: float | None = None
    review_count: int | None = None


class GoogleRun(BaseModel):
    places: list[Place] = Field(min_length=1, max_length=MAX_PLACES)
    reviews_per_place: int = Field(default=20, ge=5, le=40)


@router.post("/google/places")
async def search_places(body: PlaceSearch, user: dict = Depends(get_current_user)) -> dict:
    _require(sources.serpapi_configured(), sources.GOOGLE_MESSAGE)
    try:
        return {"places": await sources.search_places(body.query.strip())}
    except sources.SourceError as exc:
        raise HTTPException(502, str(exc)) from None


@router.post("/runs/google", status_code=202)
async def start_google(body: GoogleRun, user: dict = Depends(get_current_user)) -> dict:
    _require(sources.serpapi_configured(), sources.GOOGLE_MESSAGE)
    places = [p.model_dump() for p in body.places]
    names = ", ".join(p["name"] or "place" for p in places[:2]) + (f" +{len(places) - 2}" if len(places) > 2 else "")
    payload = {"places": places, "reviews_per_place": body.reviews_per_place}
    return await _enqueue(user, "google", f"Google reviews: {names}", payload)

