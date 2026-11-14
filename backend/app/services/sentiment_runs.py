"""Market sentiment runs: one table of each member's own queued analyses.

A request creates a row (``queued``) and starts an asyncio task in the serving
process; the task moves it to ``running``, reports progress, and ends it
``completed`` or ``failed``. A run owns no files: uploads are reduced to their
sentences when the request is made and that text is dropped from the row when
the run ends. Every progress update touches ``updated_at``; a leader-only job
fails runs that stopped touching it (the process died), which is safe across a
blue/green overlap because a live run keeps its row fresh.
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
from collections import defaultdict
from typing import Any, Awaitable, Callable

from app.database import get_db, row_to_dict
from app.services import sentiment_text as text
from app.services.sentiment_classify import ClassificationError, classify
from app.services.sentiment_sources import (
    NotConfigured, SourceError, fetch_reviews, search_reddit,
)

log = logging.getLogger(__name__)

MAX_ACTIVE_RUNS_PER_MEMBER = 2
MAX_SCORED_ITEMS = 1500
STALE_AFTER_MINUTES = 10
MIN_WORDS_TO_SCORE = 3

_tasks: set[asyncio.Task] = set()


async def create_tables(db: Any) -> None:
    """Migration sentiment:1 - additive and quick (app/migrations.py)."""
    await db.execute(
        """CREATE TABLE IF NOT EXISTS sentiment_runs (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
               kind TEXT NOT NULL,
               title TEXT NOT NULL,
               status TEXT NOT NULL DEFAULT 'queued',
               progress_pct INTEGER NOT NULL DEFAULT 0,
               progress_message TEXT,
               error_message TEXT,
               input_json TEXT,
               result_json TEXT,
               created_at TEXT DEFAULT CURRENT_TIMESTAMP,
               updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
               completed_at TEXT)"""
    )
    await db.execute("CREATE INDEX IF NOT EXISTS idx_sentiment_runs_user ON sentiment_runs(user_id, id)")


# --- Rows --------------------------------------------------------------------

LIST_COLUMNS = "id, kind, title, status, progress_pct, progress_message, error_message, created_at, updated_at, completed_at"


async def active_count(user_id: int) -> int:
    db = await get_db()
    try:
        row = await (await db.execute(
            "SELECT COUNT(*) FROM sentiment_runs WHERE user_id = ? AND status IN ('queued','running')",
            (user_id,))).fetchone()
        return int(row[0])
    finally:
        await db.close()


async def create_run(user_id: int, kind: str, title: str, payload: dict[str, Any]) -> int:
    db = await get_db()
    try:
        cursor = await db.execute(
            "INSERT INTO sentiment_runs (user_id, kind, title, input_json, progress_message) VALUES (?, ?, ?, ?, 'Queued')",
            (user_id, kind, title, json.dumps(payload)))
        await db.commit()
        return int(cursor.lastrowid)
    finally:
        await db.close()


async def list_runs(user_id: int, kind: str | None) -> list[dict[str, Any]]:
    db = await get_db()
    try:
        rows = await (await db.execute(
            f"SELECT {LIST_COLUMNS} FROM sentiment_runs WHERE user_id = ?" + (" AND kind = ?" if kind else "")
            + " ORDER BY id DESC LIMIT 50", (user_id, kind) if kind else (user_id,))).fetchall()
        return [row_to_dict(r) for r in rows]
    finally:
        await db.close()


async def get_run(run_id: int, user_id: int) -> dict[str, Any] | None:
    """The member's own run with its result; anyone else's is None."""
    db = await get_db()
    try:
        row = await (await db.execute(
            f"SELECT {LIST_COLUMNS}, result_json FROM sentiment_runs WHERE id = ? AND user_id = ?",
            (run_id, user_id))).fetchone()
    finally:
        await db.close()
    if not row:
        return None
    run = row_to_dict(row)
    raw = run.pop("result_json", None)
    run["result"] = json.loads(raw) if raw else None
    return run


async def delete_run(run_id: int, user_id: int) -> bool:
    db = await get_db()
    try:
        cursor = await db.execute("DELETE FROM sentiment_runs WHERE id = ? AND user_id = ?", (run_id, user_id))
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()


async def _update(run_id: int, **fields: Any) -> None:
    columns = ", ".join(f"{name} = ?" for name in fields)
    db = await get_db()
    try:
        await db.execute(f"UPDATE sentiment_runs SET {columns}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (*fields.values(), run_id))
        await db.commit()
    finally:
        await db.close()


async def fail_stale_runs() -> int:
    """Leader-only job: fail runs that stopped reporting (their process died)."""
    db = await get_db()
    try:
        cursor = await db.execute(
            """UPDATE sentiment_runs
               SET status = 'failed', progress_pct = 100, input_json = NULL,
                   error_message = 'The run was interrupted by a server restart. Start it again.',
                   completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
               WHERE status IN ('queued', 'running')
                 AND datetime(updated_at) <= datetime('now', ?)""",
            (f"-{STALE_AFTER_MINUTES} minutes",))
        await db.commit()
        return cursor.rowcount or 0
    finally:
        await db.close()


# --- Running -----------------------------------------------------------------

def start(run_id: int) -> None:
    """Run in the background of this process."""
    task = asyncio.create_task(execute_run(run_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


Progress = Callable[[int, str], Awaitable[None]]


async def execute_run(run_id: int) -> None:
    db = await get_db()
    try:
        row = await (await db.execute(
            "SELECT user_id, kind, input_json FROM sentiment_runs WHERE id = ? AND status = 'queued'", (run_id,))).fetchone()
    finally:
        await db.close()
    if not row:
        return
    user_id, kind, raw = row["user_id"], row["kind"], row["input_json"]
    await _update(run_id, status="running", progress_pct=2, progress_message="Starting")

    async def progress(pct: int, message: str) -> None:
        await _update(run_id, progress_pct=max(0, min(99, pct)), progress_message=message)

    try:
        result = await EXECUTORS[kind](json.loads(raw or "{}"), user_id, progress)
    except (ClassificationError, SourceError, NotConfigured) as exc:
        await _finish_failed(run_id, str(exc))
    except Exception:  # noqa: BLE001 - the member gets a message, the log gets the trace
        log.exception("sentiment run %s failed", run_id)
        await _finish_failed(run_id, "The run failed unexpectedly. Try again; if it repeats, tell an admin.")
    else:
        await _update(run_id, status="completed", progress_pct=100, progress_message="Done", input_json=None,
                      result_json=json.dumps(result), completed_at=_now())


async def _finish_failed(run_id: int, message: str) -> None:
    await _update(run_id, status="failed", progress_pct=100, progress_message="Failed", input_json=None,
                  error_message=message, completed_at=_now())


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _scores_progress(progress: Progress, low: int, high: int, label: str):
    async def report(done: int, total: int) -> None:
        await progress(low + (high - low) * done // max(total, 1), f"{label}: scored {done} of {total}")
    return report


# --- Interview transcripts ---------------------------------------------------

def build_transcript_input(company: str, competitors: list[str], files: list[tuple[str, list[str]]]) -> dict[str, Any]:
    """Sentences of every file, tagged with speaker and role. The run keeps
    only this text."""
    transcripts = []
    scored = 0
    for filename, lines in files:
        name = filename.rsplit(".", 1)[0].replace("_", " ").strip() or filename
        parsed = [text.parse_line(line) for line in lines]
        roles = text.tag_roles(name, parsed)
        rows = []
        for speaker, body in parsed:
            if not body:
                continue
            role = roles.get(speaker, "unknown") if speaker else "unknown"
            for sentence in text.sentences(body):
                scorable = role != "interviewer" and len(sentence.split()) >= MIN_WORDS_TO_SCORE
                scored += scorable
                rows.append({"speaker": speaker, "role": role, "text": sentence, "scorable": scorable})
        transcripts.append({"name": name, "rows": rows})
    return {"company": company, "competitors": competitors, "transcripts": transcripts, "scored": scored}


async def run_transcripts(payload: dict[str, Any], user_id: int, progress: Progress) -> dict[str, Any]:
    company: str = payload["company"]
    competitors: list[str] = payload["competitors"]
    flat = [(ti, row) for ti, t in enumerate(payload["transcripts"]) for row in t["rows"]]
    targets = [row["text"] for _, row in flat if row["scorable"]]
    await progress(5, f"Scoring {len(targets)} sentences")
    scores = iter(await classify(targets, user_id, _scores_progress(progress, 5, 95, "Interviews")))

    sentences = []
    for ti, row in flat:
        score = next(scores) if row["scorable"] else None
        sentences.append({
            "transcript": ti, "speaker": row["speaker"], "role": row["role"], "text": row["text"],
            "score": None if score is None else round(score, 3),
            "label": None if score is None else text.label_for(score),
            "mentions_company": text.mentions(row["text"], company),
            "mentions": [c for c in competitors if text.mentions(row["text"], c)],
        })

    def group(rows: list[dict[str, Any]]) -> dict[str, Any]:
        scored = [r for r in rows if r["score"] is not None]
        return {**text.summarise([r["score"] for r in scored])}

    def comparison(rows: list[dict[str, Any]]) -> dict[str, Any]:
        scored = [r for r in rows if r["score"] is not None]
        return {
            "all": group(scored),
            "company": group([r for r in scored if r["mentions_company"] and not r["mentions"]]),
            "competitors": [{"name": c, **group([r for r in scored if c in r["mentions"]])} for c in competitors],
        }

    transcripts = []
    for ti, t in enumerate(payload["transcripts"]):
        mine = [s for s in sentences if s["transcript"] == ti]
        speakers = {r["speaker"] for r in mine if r["role"] == "interviewee" and r["speaker"]}
        transcripts.append({
            "name": t["name"], "sentences": len(mine),
            "interviewee": sorted(speakers)[0] if speakers else None,
            **comparison(mine),
        })

    stop = text.name_words(company, *competitors)
    target_rows = [(s["text"], s["score"]) for s in sentences
                   if s["score"] is not None and s["mentions_company"] and not s["mentions"]]
    return {
        "company": company, "competitors": competitors,
        "overall": comparison(sentences), "transcripts": transcripts, "sentences": sentences,
        "word_stats": text.word_stats(target_rows, stop),
    }


# --- Reddit ------------------------------------------------------------------

async def run_reddit(payload: dict[str, Any], user_id: int, progress: Progress) -> dict[str, Any]:
    await progress(3, "Searching Reddit")
    found = await search_reddit(payload["subreddits"], payload["query"], payload["time_filter"], payload["limit"])
    posts = list({p["id"]: p for p in reversed(found) if p["id"]}.values())[::-1]  # first of a duplicate wins
    if not posts:
        return {"query": payload["query"], "summary": text.summarise([]), "monthly_trend": [], "top_keywords": [],
                "posts": [], "subreddit_breakdown": []}
    await progress(15, f"Scoring {len(posts)} posts")
    scores = await classify([f"{p['title']}\n{p['text'][:1500]}".strip() for p in posts], user_id,
                            _scores_progress(progress, 15, 95, "Reddit"))
    for post, score in zip(posts, scores):
        post["text"] = post["text"][:600]
        post["score_sentiment"] = round(score, 3)
        post["label"] = text.label_for(score)

    by_month: dict[str, list[float]] = defaultdict(list)
    by_sub: dict[str, list[float]] = defaultdict(list)
    for p in posts:
        by_month[p["month"]].append(p["score_sentiment"])
        by_sub[p["subreddit"]].append(p["score_sentiment"])
    stop = text.name_words(payload["query"])
    return {
        "query": payload["query"],
        "summary": text.summarise([p["score_sentiment"] for p in posts]),
        "monthly_trend": [{"month": m, "avg_sentiment": round(sum(v) / len(v), 4), "post_count": len(v)}
                          for m, v in sorted(by_month.items())],
        "top_keywords": text.word_stats([(f"{p['title']} {p['text']}", p["score_sentiment"]) for p in posts],
                                        stop, min_count=3, top=30),
        "posts": sorted(posts, key=lambda p: -p["score"]),
        "subreddit_breakdown": [{"subreddit": s, **text.summarise(v)} for s, v in sorted(by_sub.items())],
    }


# --- Google reviews ----------------------------------------------------------

async def run_google(payload: dict[str, Any], user_id: int, progress: Progress) -> dict[str, Any]:
    places = payload["places"]
    gathered: list[dict[str, Any]] = []
    for i, place in enumerate(places):
        await progress(3 + 25 * i // len(places), f"Fetching reviews for {place['name'] or 'place'}")
        try:
            reviews = await fetch_reviews(place["data_id"], payload["reviews_per_place"])
            error = None
        except SourceError as exc:
            reviews, error = [], str(exc)
        gathered.append({**place, "reviews": reviews, "error": error})
    flat = [r for g in gathered for r in g["reviews"]]
    if flat:
        scores = iter(await classify([r["text"] for r in flat], user_id, _scores_progress(progress, 30, 95, "Google reviews")))
        for review in flat:
            review["score"] = round(next(scores), 3)
            review["label"] = text.label_for(review["score"])
    out = []
    for g in gathered:
        revs = g["reviews"]
        out.append({
            **{k: g[k] for k in ("data_id", "name", "address", "rating", "review_count", "error")},
            **text.summarise([r["score"] for r in revs]),
            "top_positive": sorted(revs, key=lambda r: -r["score"])[:5],
            "top_negative": sorted(revs, key=lambda r: r["score"])[:5],
            "reviews": revs,
        })
    return {"places": out, "summary": text.summarise([r["score"] for r in flat])}


EXECUTORS: dict[str, Callable[[dict[str, Any], int, Progress], Awaitable[dict[str, Any]]]] = {
    "transcripts": run_transcripts,
    "reddit": run_reddit,
    "google": run_google,
}


# --- CSV ---------------------------------------------------------------------

def _cell(value: Any) -> Any:
    """Spreadsheets run text that starts with = + - @ as a formula."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def to_csv(kind: str, result: dict[str, Any]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    if kind == "transcripts":
        names = [t["name"] for t in result["transcripts"]]
        writer.writerow(["transcript", "speaker", "role", "sentence", "sentiment", "score",
                         "mentions_company", "mentions_competitors"])
        for s in result["sentences"]:
            writer.writerow([_cell(v) for v in (names[s["transcript"]], s["speaker"], s["role"], s["text"],
                                                s["label"] or "", "" if s["score"] is None else s["score"],
                                                s["mentions_company"], "; ".join(s["mentions"]))])
    elif kind == "reddit":
        writer.writerow(["subreddit", "created", "title", "text", "reddit_score", "comments", "sentiment", "score", "url"])
        for p in result["posts"]:
            writer.writerow([_cell(v) for v in (p["subreddit"], p["created"], p["title"], p["text"], p["score"],
                                                p["num_comments"], p["label"], p["score_sentiment"], p["url"])])
    else:
        writer.writerow(["place", "address", "author", "rating", "date", "review", "sentiment", "score"])
        for place in result["places"]:
            for r in place["reviews"]:
                writer.writerow([_cell(v) for v in (place["name"], place["address"], r["author"], r["rating"],
                                                    r["date"], r["text"], r["label"], r["score"])])
    return out.getvalue()
