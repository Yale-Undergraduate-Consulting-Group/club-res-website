"""Survey builder (app/modules/surveys.py): sign-in, per-app access, drafting
through the club model, the past-survey library and Qualtrics creation. The
model and Qualtrics are faked; nothing leaves the process.
From backend/: python tests/test_surveys.py
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JWT_SECRET", "surveys-test-secret-0123456789abcdefghij")
os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.mkdtemp()) / 't.db'}"
for name in ("QUALTRICS_CLIENT_ID", "QUALTRICS_CLIENT_SECRET", "SURVEY_LIBRARY_SKIP"):
    os.environ.pop(name, None)

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from app.database import get_db, init_db  # noqa: E402
from app.jwt_utils import create_token  # noqa: E402
from app.routers import surveys as surveys_router  # noqa: E402
from app.services import qualtrics, survey_library  # noqa: E402

ADMIN, ANA, BEN = 1, 2, 3


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
            "SELECT app, version FROM schema_migrations WHERE app = 'surveys'")).fetchall()]
    finally:
        await db.close()


def as_member(user_id: int, role: str = "standard") -> TestClient:
    client = TestClient(main.app, raise_server_exceptions=False)
    client.cookies.set("yucg_session", create_token(user_id, f"{user_id}@yale.edu", None, None, role))
    return client


def definition(survey_id: str, name: str, questions: list[tuple[str, str]]) -> dict:
    """A Qualtrics survey definition with one block, in flow."""
    return {
        "SurveyID": survey_id, "SurveyName": name,
        "SurveyFlow": {"Flow": [{"ID": "BL_1", "Type": "Block"}]},
        "Blocks": {"BL_1": {"ID": "BL_1", "Description": "Main", "Type": "Standard",
                            "BlockElements": [{"Type": "Question", "QuestionID": f"QID{i}"} for i in range(len(questions))]}},
        "Questions": {f"QID{i}": {"QuestionType": "MC", "Selector": "SAVR", "QuestionText": f"<b>{text}</b>",
                                  "Choices": {"1": {"Display": choice}}}
                      for i, (text, choice) in enumerate(questions)},
    }


class FakeQualtrics:
    """The Qualtrics API as httpx sees it, recording every request."""

    def __init__(self, surveys: dict[str, dict], refuse_question: str = "") -> None:
        self.surveys, self.refuse_question, self.requests = surveys, refuse_question, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        self.requests.append((method, path))
        if path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        assert request.headers["Authorization"] == "Bearer tok"
        if method == "GET" and path == "/API/v3/surveys":
            return httpx.Response(200, json={"result": {"elements": [
                {"id": sid, "name": d["SurveyName"]} for sid, d in self.surveys.items()], "nextPage": None}})
        if method == "GET" and path.startswith("/API/v3/survey-definitions/") and path.count("/") == 4:
            return httpx.Response(200, json={"result": self.surveys[path.rsplit("/", 1)[1]]})
        if method == "POST" and path == "/API/v3/survey-definitions":
            return httpx.Response(200, json={"result": {"SurveyID": "SV_new", "DefaultBlockID": "BL_new"}})
        if method == "GET" and path.endswith("/questions"):
            return httpx.Response(200, json={"result": {"elements": [{"QuestionID": "QID_blank"}]}})
        if method == "DELETE":
            return httpx.Response(200, json={"result": {}})
        if method == "POST" and path.endswith("/questions"):
            if json.loads(request.content)["QuestionText"] == self.refuse_question:
                return httpx.Response(400, json={"meta": {"error": {"errorMessage": "Invalid question"}}})
            return httpx.Response(200, json={"result": {"QuestionID": "QID9"}})
        return httpx.Response(404, json={})


def use_qualtrics(fake: FakeQualtrics) -> None:
    os.environ["QUALTRICS_CLIENT_ID"], os.environ["QUALTRICS_CLIENT_SECRET"] = "id", "secret"
    qualtrics._token = None
    qualtrics.session = lambda: httpx.AsyncClient(transport=httpx.MockTransport(fake))


def fake_model(reply: str, calls: list) -> None:
    def converse(messages, **kwargs):
        calls.append((messages, kwargs))
        return {"output": {"message": {"content": [{"text": reply}]}}}
    surveys_router.converse = converse


DRAFT = {"message": "Here is a draft.", "survey": {"name": "Coffee", "questions": [
    {"type": "single_choice", "text": "How often do you buy coffee?", "choices": ["Daily", "Weekly"]},
    {"type": "likert", "text": "Price matters more than location."},
    {"type": "slider", "text": "How likely are you to recommend us?", "min": 0, "max": 10},
]}}


def tests() -> None:
    assert asyncio.run(setup()) == [("surveys", 1)]
    admin, ana, ben = as_member(ADMIN, "admin"), as_member(ANA), as_member(BEN)
    anonymous = TestClient(main.app)

    # Sign-in on every route.
    for method, path in (("get", "/api/surveys/library"), ("post", "/api/surveys/library/sync"),
                         ("post", "/api/surveys/chat"), ("post", "/api/surveys/create")):
        assert getattr(anonymous, method)(path).status_code == 401, path

    # Without the club's Qualtrics client: drafting works, Qualtrics is refused plainly.
    status = ana.get("/api/surveys/library").json()
    assert status["qualtrics"] is False and status["surveys"] == 0
    assert ana.post("/api/surveys/library/sync").status_code == 503
    assert ana.post("/api/surveys/create", json={"survey": DRAFT["survey"]}).status_code == 503

    calls: list = []
    fake_model(json.dumps(DRAFT), calls)
    reply = ana.post("/api/surveys/chat", json={"messages": [{"role": "user", "content": "Coffee near campus; H1 price"}]})
    assert reply.status_code == 200, reply.text
    assert reply.json()["survey"]["name"] == "Coffee" and reply.json()["sources"] == []
    assert calls[-1][1]["user_id"] == ANA, "the call was not charged to the member"

    # The conversation must alternate and end with the analyst.
    bad = ana.post("/api/surveys/chat", json={"messages": [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}]})
    assert bad.status_code == 422
    assert ana.post("/api/surveys/chat", json={"messages": [{"role": "assistant", "content": "a"}]}).status_code == 422

    # A malformed draft (choice question without choices) is never passed on as usable.
    broken = {"message": "ok", "survey": {"name": "X", "questions": [{"type": "single_choice", "text": "Pick one"}]}}
    fake_model(json.dumps(broken), calls)
    assert ana.post("/api/surveys/chat", json={"messages": [{"role": "user", "content": "x"}]}).json()["survey"] is None
    # Prose around the JSON still parses; plain prose is the message with no draft.
    fake_model("Sure.\n" + json.dumps(DRAFT) + "\nDone", calls)
    assert ana.post("/api/surveys/chat", json={"messages": [{"role": "user", "content": "x"}]}).json()["survey"]["name"] == "Coffee"
    fake_model("Who are the respondents?", calls)
    plain = ana.post("/api/surveys/chat", json={"messages": [{"role": "user", "content": "x"}]}).json()
    assert plain == {"message": "Who are the respondents?", "survey": None, "sources": []}

    # Library sync with the club client: skip list honoured, past surveys feed the prompt.
    os.environ["SURVEY_LIBRARY_SKIP"] = "Acme"
    fake = FakeQualtrics({
        "SV_1": definition("SV_1", "Coffee habits - Copy", [("How often do you drink coffee?", "Daily")]),
        "SV_2": definition("SV_2", "Acme brand study", [("Rate Acme coffee", "Good")]),
        "SV_3": definition("SV_3", "Pilot test run", [("Coffee?", "Yes")]),
    })
    use_qualtrics(fake)
    assert ana.post("/api/surveys/library/sync").status_code == 202
    status = ana.get("/api/surveys/library").json()
    assert status["names"] == ["Coffee habits"], status
    assert status["last_sync"]["ok"] and status["last_sync"]["skipped"] == 2
    assert ("GET", "/API/v3/survey-definitions/SV_2") not in fake.requests, "a skipped survey was downloaded"

    fake_model(json.dumps(DRAFT), calls)
    reply = ben.post("/api/surveys/chat", json={"messages": [{"role": "user", "content": "how often students drink coffee"}]}).json()
    assert reply["sources"] == ["Coffee habits"]
    assert "How often do you drink coffee?" in calls[-1][1]["system"]

    # Adding a name to the skip list hides an already copied survey at once.
    os.environ["SURVEY_LIBRARY_SKIP"] = "Acme, coffee habits"
    assert ana.get("/api/surveys/library").json()["surveys"] == 0
    os.environ["SURVEY_LIBRARY_SKIP"] = "Acme"
    # A survey no longer visible leaves the library on the next sync.
    del fake.surveys["SV_1"]
    assert ana.post("/api/surveys/library/sync").status_code == 202
    assert ana.get("/api/surveys/library").json()["surveys"] == 0

    # Creation: blank question removed, every question posted, refusals reported.
    fake.refuse_question = "Price matters more than location."
    created = ana.post("/api/surveys/create", json={"survey": DRAFT["survey"]})
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["survey_id"] == "SV_new" and body["added"] == 2
    assert [f["question"] for f in body["failed"]] == ["Price matters more than location."]
    assert ("DELETE", "/API/v3/survey-definitions/SV_new/questions/QID_blank") in fake.requests
    assert ana.post("/api/surveys/create", json={"survey": {"name": "x", "questions": []}}).status_code == 422
    slider = {"name": "x", "questions": [{"type": "slider", "text": "Rate", "min": 5, "max": 5}]}
    assert ana.post("/api/surveys/create", json={"survey": slider}).status_code == 422

    # Restricted to Ben: Ana is refused and loses the menu entry; admins keep it.
    assert "surveys" in ana.get("/api/auth/me").json()["user"]["apps"]
    assert admin.put("/api/admin/apps/surveys", json={"restricted": True, "member_ids": [BEN]}).status_code == 200
    assert ana.get("/api/surveys/library").status_code == 403
    assert "surveys" not in ana.get("/api/auth/me").json()["user"]["apps"]
    assert ben.get("/api/surveys/library").status_code == 200
    assert admin.get("/api/surveys/library").status_code == 200


def unit() -> None:
    """Pure helpers: outline order and question definitions."""
    outline = survey_library.compact({
        "SurveyID": "SV", "SurveyName": "S",
        "SurveyFlow": {"Flow": [{"ID": "BL_b"}, {"ID": "BL_a"}]},
        "Blocks": {"a": {"ID": "BL_a", "Description": "Second", "BlockElements": [{"Type": "Question", "QuestionID": "Q1"}]},
                   "b": {"ID": "BL_b", "Description": "First", "BlockElements": [{"Type": "Question", "QuestionID": "Q2"}]},
                   "t": {"ID": "BL_t", "Type": "Trash", "BlockElements": [{"Type": "Question", "QuestionID": "Q3"}]}},
        "Questions": {"Q1": {"QuestionType": "TE", "QuestionText": "Why?"},
                      "Q2": {"QuestionType": "DB", "QuestionText": "Intro"},
                      "Q3": {"QuestionType": "TE", "QuestionText": "Deleted"}},
    })
    assert [s["name"] for s in outline["sections"]] == ["Second"], "descriptive-only and trash blocks must not appear"
    likert = qualtrics.question_definition({"type": "likert", "text": "Agree?"}, 2)
    assert likert["DataExportTag"] == "Q3" and list(likert["Choices"]) == ["1", "2", "3", "4", "5"]
    multi = qualtrics.question_definition({"type": "multi_choice", "text": "Which?", "choices": ["A", "B"]}, 0)
    assert multi["Selector"] == "MAVR" and multi["ChoiceOrder"] == ["1", "2"]


if __name__ == "__main__":
    unit()
    tests()
    print("surveys: ok")
