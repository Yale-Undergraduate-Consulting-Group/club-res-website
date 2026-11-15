"""Survey builder: chat that turns project background and hypotheses into a
survey draft, then builds it as a draft survey in the club's Qualtrics account.

Ported from the yucg-survey-agent prototype. Drafting uses the club's model
(app/services/llm.py); the past-survey library and survey creation use the
club's Qualtrics client (app/services/qualtrics.py), which is optional: without
it the chat still drafts. Sign-in and app access come from the manifest.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from app.auth_deps import get_current_user
from app.database import get_db
from app.services import qualtrics, survey_library
from app.services.llm import converse, default_model_id, response_text

logger = logging.getLogger(__name__)
router = APIRouter()

#: Characters of conversation sent per reply. The full draft travels in each
#: assistant turn, so long revisions grow; past this the member starts afresh.
MAX_CONVERSATION_CHARS = 120_000
MAX_OUTPUT_TOKENS = 4096

SYSTEM_PROMPT = """You are the YUCG Survey Agent, helping a Yale Undergraduate Consulting Group analyst design a client survey.

How to work:
1. The analyst will usually paste project background and the hypotheses the survey should test. Read it carefully.
2. Briefly restate the goals and hypotheses. Ask at most 2-3 questions, and only about essentials that are missing (target respondents, length limit, must-ask topics). If nothing is missing, skip straight to drafting.
3. Draft the survey. Every question must trace back to a hypothesis or a needed screener/demographic. Screeners first, demographics last. Use standard formats (5-point agree scale, 0-10 scales, multiple choice). Avoid leading, double-barreled or jargon-heavy wording; include "Other" or "Prefer not to say" where appropriate. Aim for under 10 minutes.
4. Revise whenever the analyst asks. Always return the full current draft, not just the changes.
5. Never claim the survey has been created in Qualtrics. The analyst clicks "Create in Qualtrics" when they are happy.

Reply ONLY with a JSON object of this shape:
{
  "message": "what you say to the analyst (plain text, concise)",
  "survey": null or {
    "name": "short survey title",
    "questions": [
      {
        "type": "single_choice" | "multi_choice" | "likert" | "slider" | "text",
        "text": "question wording",
        "choices": ["..."],          // single_choice / multi_choice only
        "min": 0, "max": 10,          // slider only
        "hypothesis": "which hypothesis or purpose this serves"
      }
    ]
  }
}
"likert" always uses: Strongly disagree, Disagree, Neutral, Agree, Strongly agree.
Set "survey" to null until you have enough to draft; after that, always include the full draft.
Respond with the JSON object only, no other text."""

LIBRARY_PROMPT = """

## Past YUCG surveys (retrieved for this project)
Below are the most relevant questions from surveys YUCG has run before. Use them as a house-style guide:
reuse proven wording, answer scales and screener/demographic patterns where they fit this project, and
follow their section structure when it suits. Do not copy questions that don't serve this project's hypotheses,
and never carry over another client's name or confidential details. When you reuse a pattern, mention it briefly
in your message (e.g. "screeners follow the brand-perception survey").

"""

_sync_lock = asyncio.Lock()
_last_sync: dict[str, Any] = {}


async def create_tables(db: Any) -> None:
    """Migration surveys:1 - additive and quick (app/migrations.py)."""
    await db.execute(
        """CREATE TABLE IF NOT EXISTS survey_library (
               survey_id TEXT PRIMARY KEY,
               name TEXT NOT NULL,
               outline_json TEXT NOT NULL,
               synced_at TEXT NOT NULL)"""
    )


class Question(BaseModel):
    type: Literal["single_choice", "multi_choice", "likert", "slider", "text"]
    text: str = Field(min_length=1, max_length=2000)
    choices: list[str] | None = Field(default=None, max_length=50)
    min: int | None = Field(default=None, ge=-1000, le=1000)
    max: int | None = Field(default=None, ge=-1000, le=1000)
    hypothesis: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _shape(self) -> "Question":
        self.text = self.text.strip()
        if not self.text:
            raise ValueError("A question has no wording")
        if self.type in ("single_choice", "multi_choice"):
            self.choices = [c.strip() for c in self.choices or [] if c and c.strip()]
            if not self.choices:
                raise ValueError(f'"{self.text[:60]}" needs answer choices')
        if self.type == "slider" and (self.min if self.min is not None else 0) >= (self.max if self.max is not None else 10):
            raise ValueError(f'"{self.text[:60]}" needs a slider range whose minimum is below its maximum')
        return self


class Survey(BaseModel):
    name: str = Field(default="YUCG survey", max_length=200)
    questions: list[Question] = Field(min_length=1, max_length=150)


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class ChatIn(BaseModel):
    messages: list[Message] = Field(min_length=1, max_length=200)


class CreateIn(BaseModel):
    survey: Survey


def _parse_reply(text: str) -> dict[str, Any]:
    """The model's JSON reply; plain text becomes the message with no draft."""
    data: Any = None
    try:
        data = json.loads(text)
    except ValueError:
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                data = json.loads(match.group())
            except ValueError:
                data = None
    if not isinstance(data, dict):
        return {"message": text, "survey": None}
    survey = data.get("survey")
    if survey is not None:
        try:
            survey = Survey.model_validate(survey).model_dump(exclude_none=True)
        except ValueError:
            # A malformed draft is not shown as if it were usable.
            return {"message": (str(data.get("message") or "") + "\n\nThe draft came back malformed; ask me to send it again.").strip(),
                    "survey": None}
    return {"message": str(data.get("message") or ""), "survey": survey}


@router.get("/library")
async def library_status(user: dict = Depends(get_current_user)) -> dict:
    db = await get_db()
    try:
        surveys = await survey_library.load(db)
        last = (await (await db.execute("SELECT MAX(synced_at) AS at FROM survey_library")).fetchone())["at"]
    finally:
        await db.close()
    return {
        "qualtrics": qualtrics.configured(),
        "surveys": len(surveys),
        "questions": sum(len(s["questions"]) for survey in surveys for s in survey["sections"]),
        "names": [s["name"] for s in surveys],
        "synced_at": last,
        "syncing": _sync_lock.locked(),
        "last_sync": _last_sync or None,
    }


async def _run_sync() -> None:
    async with _sync_lock:
        db = await get_db()
        try:
            result = await survey_library.sync(db)
            _last_sync.clear()
            _last_sync.update(ok=True, failed=result["failed"], saved=len(result["saved"]), skipped=len(result["skipped"]))
        except Exception as exc:  # reported on the page; the old library stays
            logger.warning("survey library sync failed: %s", exc)
            _last_sync.clear()
            _last_sync.update(ok=False, error=str(exc) if isinstance(exc, qualtrics.QualtricsError) else "Library refresh failed.")
        finally:
            await db.close()


@router.post("/library/sync", status_code=202)
async def library_sync(background: BackgroundTasks, user: dict = Depends(get_current_user)) -> dict:
    if not qualtrics.configured():
        raise HTTPException(503, qualtrics.NOT_CONFIGURED)
    if _sync_lock.locked():
        raise HTTPException(409, "The library is already refreshing.")
    background.add_task(_run_sync)
    return {"syncing": True}


@router.post("/chat")
async def chat(body: ChatIn, user: dict = Depends(get_current_user)) -> dict:
    messages = body.messages
    if messages[0].role != "user" or messages[-1].role != "user" or any(
            a.role == b.role for a, b in zip(messages, messages[1:])):
        raise HTTPException(422, "Messages must alternate, starting and ending with the analyst.")
    if sum(len(m.content) for m in messages) > MAX_CONVERSATION_CHARS:
        raise HTTPException(413, "This conversation is too long. Start a new one; your draft can be imported.")
    db = await get_db()
    try:
        library = await survey_library.load(db)
    finally:
        await db.close()
    query = "\n".join(m.content for m in messages if m.role == "user")
    sources, context = survey_library.retrieve(library, query)
    system = SYSTEM_PROMPT + LIBRARY_PROMPT + context if context else SYSTEM_PROMPT
    response = await asyncio.to_thread(
        converse,
        [{"role": m.role, "content": [{"text": m.content}]} for m in messages],
        model_id=default_model_id(), system=system, user_id=user["id"], purpose="survey_draft",
        max_tokens=MAX_OUTPUT_TOKENS,
    )
    return {**_parse_reply(response_text(response)), "sources": sources}


@router.post("/create")
async def create(body: CreateIn, user: dict = Depends(get_current_user)) -> dict:
    if not qualtrics.configured():
        raise HTTPException(503, qualtrics.NOT_CONFIGURED)
    survey = body.survey
    try:
        result = await qualtrics.create_survey(survey.name.strip() or "YUCG survey",
                                               [q.model_dump() for q in survey.questions])
    except qualtrics.QualtricsError as exc:
        raise HTTPException(502, str(exc)) from exc
    logger.info("survey %s created in Qualtrics by member %s", result["survey_id"], user["id"])
    return result
