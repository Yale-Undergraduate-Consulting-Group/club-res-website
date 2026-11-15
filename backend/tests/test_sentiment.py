"""Market sentiment (app/modules/sentiment.py): sign-in, per-app access, each member's
own runs, the not-configured states, uploads and the three analyses with the network
and the model faked. From backend/: python tests/test_sentiment.py
"""
import asyncio
import io
import json
import os
import re
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JWT_SECRET", "sentiment-test-secret-0123456789abcdefghij")
os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.mkdtemp()) / 't.db'}"
for name in ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "SERPAPI_KEY"):
    os.environ.pop(name, None)

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from app.database import get_db, init_db  # noqa: E402
from app.jwt_utils import create_token  # noqa: E402
from app.routers import sentiment as router  # noqa: E402
from app.services import sentiment_classify, sentiment_runs as runs, sentiment_sources as sources  # noqa: E402
from app.services import sentiment_text as text  # noqa: E402

ADMIN, ANA, BEN = 1, 2, 3
MODEL_CALLS: list[dict] = []
MODEL_MODE = {"broken": False}


def fake_complete_text(prompt, model_id=None, system=None, *, user_id=None, purpose="inference", max_tokens=2048, on_model=None):
    """Stands in for app.services.llm.complete_text: scores love/great up, bad down."""
    MODEL_CALLS.append({"user_id": user_id, "purpose": purpose, "n": len(prompt.splitlines())})
    if MODEL_MODE["broken"]:
        return "I cannot help with that."
    items = []
    for line in prompt.splitlines():
        number, _, body = line.partition(". ")
        lowered = body.lower()
        score = 0.8 if ("love" in lowered or "great" in lowered) else -0.6 if "bad" in lowered else 0.0
        items.append({"i": int(number), "score": score})
    return json.dumps({"items": items})


sentiment_classify.complete_text = fake_complete_text
STARTED: list[int] = []
runs.start = STARTED.append  # the test runs each queued run itself, in order


async def setup() -> list:
    await init_db()
    db = await get_db()
    try:
        await db.executemany(
            "INSERT INTO users (id, email, name, role, is_active) VALUES (?, ?, ?, ?, 1)",
            [(ADMIN, "admin@yale.edu", "Admin", "admin"), (ANA, "ana@yale.edu", "Ana", "standard"),
             (BEN, "ben@yale.edu", "Ben", "standard")],
        )
        await db.commit()
        return [tuple(r) for r in await (await db.execute(
            "SELECT app, version FROM schema_migrations WHERE app = 'sentiment'")).fetchall()]
    finally:
        await db.close()


def as_member(user_id: int, role: str = "standard") -> TestClient:
    client = TestClient(main.app, raise_server_exceptions=False)
    client.cookies.set("yucg_session", create_token(user_id, f"{user_id}@yale.edu", None, None, role))
    return client


def docx(paragraphs: list[str]) -> bytes:
    body = "".join(f"<w:p><w:r><w:t xml:space=\"preserve\">{p}</w:t></w:r></w:p>" for p in paragraphs)
    xml = f'<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>'
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("word/document.xml", xml)
    return out.getvalue()


def execute_started() -> None:
    while STARTED:
        asyncio.run(runs.execute_run(STARTED.pop(0)))


def upload(client, files, company="Acme", competitors="Figma, Canva"):
    return client.post("/api/sentiment/runs/transcripts", data={"company": company, "competitors": competitors},
                       files=[("files", f) for f in files])


def text_units() -> None:
    assert text.parse_line("Bob Smith: I love it.") == ("Bob Smith", "I love it.")
    assert text.parse_line("My name is David.") == ("", "My name is David.")
    assert text.parse_line("00:01:02.000 --> 00:01:05.000") == ("", "")
    assert text.sentences("Dr. Lee said hi. It was fine! Really? Yes.") == ["Dr. Lee said hi.", "It was fine!", "Really?", "Yes."]
    assert text.label_for(0.05) == "positive" and text.label_for(-0.05) == "negative" and text.label_for(0.049) == "neutral"
    assert text.docx_lines(docx(["Ana: a &amp; b", "", "second"])) == ["Ana: a & b", "second"]
    assert text.tokens("The templates are great, https://x.io", {"great"}) == ["templates"]


def source_clients() -> None:
    """The Reddit and SerpAPI clients against a canned transport."""
    os.environ.update(REDDIT_CLIENT_ID="id", REDDIT_CLIENT_SECRET="secret", SERPAPI_KEY="sekrit")
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        url = str(request.url)
        if "access_token" in url:
            return httpx.Response(200, json={"access_token": "tok"})
        if "oauth.reddit.com/r/yale/search" in url:
            assert request.headers["authorization"] == "bearer tok" and "yucg" in request.headers["user-agent"]
            return httpx.Response(200, json={"data": {"after": None, "children": [{"data": {
                "id": "a", "subreddit": "yale", "title": "T", "selftext": "B", "created_utc": 1767225600,
                "score": 5, "num_comments": 2, "permalink": "/r/yale/a"}}]}})
        if "oauth.reddit.com/r/gone/search" in url:
            return httpx.Response(404)
        if request.url.params.get("engine") == "google_maps":
            return httpx.Response(200, json={"local_results": [{"data_id": "d1", "title": "Cafe", "reviews": 3}, {"title": "no id"}]})
        if request.url.params.get("engine") == "google_maps_reviews":
            return httpx.Response(500)
        raise AssertionError(url)

    real = httpx.AsyncClient
    sources.httpx.AsyncClient = lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    try:
        posts = asyncio.run(real_search_reddit(["yale"], "q", "year", 10))
        assert posts[0]["month"] == "2026-01" and posts[0]["url"] == "https://www.reddit.com/r/yale/a"
        try:
            asyncio.run(real_search_reddit(["gone"], "q", "year", 10))
            raise AssertionError("a missing subreddit must raise")
        except sources.SourceError as exc:
            assert "r/gone" in str(exc)
        assert [p["data_id"] for p in asyncio.run(sources.search_places("cafe"))] == ["d1"]
        try:
            asyncio.run(real_fetch_reviews("d1", 5))
            raise AssertionError("a SerpAPI failure must raise")
        except sources.SourceError as exc:
            assert "sekrit" not in str(exc) and "500" in str(exc)
    finally:
        sources.httpx.AsyncClient = real
        for name in ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "SERPAPI_KEY"):
            os.environ.pop(name, None)


real_search_reddit, real_fetch_reviews = sources.search_reddit, sources.fetch_reviews


def tests() -> None:
    assert asyncio.run(setup()) == [("sentiment", 1)]
    text_units()
    source_clients()
    admin, ana, ben = as_member(ADMIN, "admin"), as_member(ANA), as_member(BEN)

    # Every route answers 401 without a session, and reports what the server can reach.
    anon = TestClient(main.app)
    assert anon.get("/api/sentiment/status").status_code == 401
    assert anon.get("/api/sentiment/runs").status_code == 401
    assert anon.post("/api/sentiment/runs/reddit", json={}).status_code == 401
    assert ana.get("/api/sentiment/status").json() == {"reddit": False, "google": False}
    assert "sentiment" in ana.get("/api/auth/me").json()["user"]["apps"]

    # Reddit and Google answer 503 with a clear message until a key is added.
    reddit_body = {"subreddits": ["r/yale"], "query": "acme", "time_filter": "year", "limit": 10}
    r = ana.post("/api/sentiment/runs/reddit", json=reddit_body)
    assert r.status_code == 503 and r.json()["detail"] == "Reddit is not configured on this server"
    r = ana.post("/api/sentiment/google/places", json={"query": "acme new haven"})
    assert r.status_code == 503 and "not configured" in r.json()["detail"]
    assert ana.post("/api/sentiment/runs/google", json={"places": [{"data_id": "x"}]}).status_code == 503

    # Uploads: limits and types.
    txt = ("Interviewer: What do you think of Acme?\n"
           "Bob Smith: I love Acme because the templates are great. Figma is bad for beginners. "
           "Acme beats Figma on price though. The onboarding was fine overall.\n").encode()
    assert upload(ana, [("a.exe", b"x", "application/octet-stream")]).status_code == 422
    assert upload(ana, [("a.docx", b"not a zip", "application/octet-stream")]).status_code == 422
    assert upload(ana, [("a.txt", txt, "text/plain")], company="  ").status_code == 422
    assert upload(ana, [("a.txt", b"  \n", "text/plain")]).status_code == 422
    assert upload(ana, [(f"f{i}.txt", txt, "text/plain") for i in range(router.MAX_FILES + 1)]).status_code == 422
    router.MAX_FILE_BYTES = 100
    assert upload(ana, [("big.txt", txt, "text/plain")]).status_code == 413
    router.MAX_FILE_BYTES = 10 * 1024 * 1024
    runs.MAX_SCORED_ITEMS = 2
    assert upload(ana, [("Bob_Smith.txt", txt, "text/plain")]).status_code == 422
    runs.MAX_SCORED_ITEMS = 1500
    assert not STARTED and ana.get("/api/sentiment/runs").json() == []

    # A transcript run: .txt and .docx, model faked.
    memo = docx(["Cara Lee: Acme is bad at exports. I love the editor in Acme though.", "Interviewer: thanks!"])
    created = upload(ana, [("Bob_Smith.txt", txt, "text/plain"), ("Cara_Lee.docx", memo, "application/octet-stream")])
    assert created.status_code == 202, created.text
    run_id = created.json()["id"]
    assert ana.get(f"/api/sentiment/runs/{run_id}").json()["status"] == "queued"
    execute_started()
    run = ana.get(f"/api/sentiment/runs/{run_id}").json()
    assert run["status"] == "completed" and run["progress_pct"] == 100, run
    result = run["result"]
    assert [t["name"] for t in result["transcripts"]] == ["Bob Smith", "Cara Lee"]
    assert result["transcripts"][0]["interviewee"] == "Bob Smith"
    interviewer = [s for s in result["sentences"] if s["role"] == "interviewer"]
    assert interviewer and all(s["score"] is None for s in interviewer), "interviewer lines must not be scored"
    assert result["overall"]["company"]["count"] == 3  # Acme-only sentences: Bob's love/great and onboarding, Cara's exports
    assert result["overall"]["company"]["avg_sentiment"] is not None
    figma = next(c for c in result["overall"]["competitors"] if c["name"] == "Figma")
    assert figma["count"] == 2 and figma["avg_sentiment"] == -0.3
    assert all(c["user_id"] == ANA and c["purpose"] == "sentiment" for c in MODEL_CALLS)
    assert "input_json" not in run
    db = asyncio.run(_row(run_id))
    assert db["input_json"] is None, "the uploaded text must not outlive the run"

    # Only the owner sees a run, its CSV, or can delete it.
    for url in (f"/api/sentiment/runs/{run_id}", f"/api/sentiment/runs/{run_id}/csv"):
        assert ben.get(url).status_code == 404 and admin.get(url).status_code == 404
        assert ana.get(url).status_code == 200
    assert ben.delete(f"/api/sentiment/runs/{run_id}").status_code == 404
    assert ben.get("/api/sentiment/runs").json() == []
    assert [r["id"] for r in ana.get("/api/sentiment/runs?kind=transcripts").json()] == [run_id]
    assert ana.get("/api/sentiment/runs?kind=reddit").json() == []
    csv_text = ana.get(f"/api/sentiment/runs/{run_id}/csv").text
    assert csv_text.startswith("transcript,speaker,role,sentence") and "Figma is bad" in csv_text
    assert ana.get("/api/sentiment/runs/999").status_code == 404

    # A model that cannot score fails the run with a message, not made-up scores.
    MODEL_MODE["broken"] = True
    failing = upload(ana, [("Bob_Smith.txt", txt, "text/plain")]).json()["id"]
    execute_started()
    failed = ana.get(f"/api/sentiment/runs/{failing}").json()
    assert failed["status"] == "failed" and "no score" in failed["error_message"] and failed["result"] is None
    assert ana.get(f"/api/sentiment/runs/{failing}/csv").status_code == 409
    MODEL_MODE["broken"] = False

    # Reddit, once configured.
    os.environ["REDDIT_CLIENT_ID"], os.environ["REDDIT_CLIENT_SECRET"] = "id", "secret"
    assert ana.get("/api/sentiment/status").json()["reddit"] is True
    seen = {}

    async def fake_reddit(subreddits, query, time_filter, limit):
        seen.update(subreddits=subreddits, query=query, time_filter=time_filter, limit=limit)
        def post(i, title, month):
            return {"id": f"p{i}", "subreddit": "yale", "title": title, "text": "", "created": f"{month}-03T00:00:00+00:00",
                    "month": month, "score": 10 - i, "num_comments": i, "url": f"https://www.reddit.com/p{i}"}
        return [post(1, "I love acme templates", "2026-01"), post(2, "acme is bad, templates broken", "2026-02"),
                post(3, "acme templates again", "2026-02"), post(1, "duplicate", "2026-01")]

    runs.search_reddit = fake_reddit
    assert ana.post("/api/sentiment/runs/reddit", json={**reddit_body, "subreddits": ["bad name!"]}).status_code == 422
    assert ana.post("/api/sentiment/runs/reddit", json={**reddit_body, "limit": 500}).status_code == 422
    rid = ana.post("/api/sentiment/runs/reddit", json=reddit_body).json()["id"]
    execute_started()
    assert seen["subreddits"] == ["yale"]
    rr = ana.get(f"/api/sentiment/runs/{rid}").json()["result"]
    assert rr["summary"]["count"] == 3 and rr["summary"]["distribution"] == {"positive": 1, "neutral": 1, "negative": 1}
    assert [m["month"] for m in rr["monthly_trend"]] == ["2026-01", "2026-02"]
    assert rr["subreddit_breakdown"][0]["subreddit"] == "yale"
    assert any(k["word"] == "templates" for k in rr["top_keywords"])
    assert ana.get(f"/api/sentiment/runs/{rid}/csv").text.startswith("subreddit,created,title")

    # Google reviews, once configured.
    os.environ["SERPAPI_KEY"] = "key"

    async def fake_places(query):
        return [{"data_id": "d1", "place_id": "p1", "name": "Acme Cafe", "address": "1 Main St", "rating": 4.5, "review_count": 10}]

    async def fake_reviews(data_id, count):
        assert data_id == "d1" and count == 20
        return [{"author": "A", "rating": 5, "text": "Great coffee, love it", "date": "a week ago"},
                {"author": "B", "rating": 1, "text": "Bad service", "date": "a month ago"}]

    sources.search_places, runs.fetch_reviews = fake_places, fake_reviews
    places = ana.post("/api/sentiment/google/places", json={"query": "acme new haven"}).json()["places"]
    assert places[0]["name"] == "Acme Cafe"
    assert ana.post("/api/sentiment/runs/google", json={"places": []}).status_code == 422
    gid = ana.post("/api/sentiment/runs/google", json={"places": places}).json()["id"]
    execute_started()
    gr = ana.get(f"/api/sentiment/runs/{gid}").json()["result"]
    assert gr["places"][0]["top_positive"][0]["author"] == "A" and gr["places"][0]["top_negative"][0]["author"] == "B"
    assert gr["summary"]["count"] == 2
    assert "Great coffee" in ana.get(f"/api/sentiment/runs/{gid}/csv").text

    # At most two analyses at once per member; a stale run is failed and its input dropped.
    first = ana.post("/api/sentiment/runs/reddit", json=reddit_body).json()["id"]
    assert ana.post("/api/sentiment/runs/reddit", json=reddit_body).status_code == 202
    assert ana.post("/api/sentiment/runs/reddit", json=reddit_body).status_code == 429
    assert ben.post("/api/sentiment/runs/reddit", json=reddit_body).status_code == 202
    assert asyncio.run(runs.fail_stale_runs()) == 0
    asyncio.run(_age(first))
    assert asyncio.run(runs.fail_stale_runs()) == 1
    stale = ana.get(f"/api/sentiment/runs/{first}").json()
    assert stale["status"] == "failed" and "interrupted" in stale["error_message"]
    assert asyncio.run(_row(first))["input_json"] is None
    STARTED.clear()

    # Deleting a run removes it.
    assert ana.delete(f"/api/sentiment/runs/{rid}").json() == {"ok": True}
    assert ana.get(f"/api/sentiment/runs/{rid}").status_code == 404

    # Restricted to Ben: Ana is refused and loses the menu entry; admins keep it.
    assert admin.put("/api/admin/apps/sentiment", json={"restricted": True, "member_ids": [BEN]}).status_code == 200
    assert ana.get("/api/sentiment/runs").status_code == 403
    assert "sentiment" not in ana.get("/api/auth/me").json()["user"]["apps"]
    assert ben.get("/api/sentiment/runs").status_code == 200
    assert admin.get("/api/sentiment/runs").status_code == 200


async def _row(run_id: int) -> dict:
    db = await get_db()
    try:
        return dict(await (await db.execute("SELECT * FROM sentiment_runs WHERE id = ?", (run_id,))).fetchone())
    finally:
        await db.close()


async def _age(run_id: int) -> None:
    db = await get_db()
    try:
        await db.execute("UPDATE sentiment_runs SET updated_at = datetime('now', '-1 hour') WHERE id = ?", (run_id,))
        await db.commit()
    finally:
        await db.close()


if __name__ == "__main__":
    tests()
    print("sentiment: ok")
