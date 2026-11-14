"""Qualtrics through the club's one OAuth client (client-credentials grant).

Settings: QUALTRICS_CLIENT_ID and QUALTRICS_CLIENT_SECRET turn it on;
QUALTRICS_BASE_URL is Yale's data centre unless set. Without the client the
survey builder still drafts, but cannot read past surveys or create one.
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://yalesurvey.yul1.qualtrics.com"
SCOPES = "read:surveys write:surveys manage:surveys"
NOT_CONFIGURED = "Qualtrics is not configured on this server."
LIKERT = ("Strongly disagree", "Disagree", "Neutral", "Agree", "Strongly agree")

_token: tuple[str, float] | None = None
_token_lock = asyncio.Lock()


class QualtricsError(Exception):
    pass


def base_url() -> str:
    return (os.getenv("QUALTRICS_BASE_URL") or DEFAULT_BASE_URL).strip().rstrip("/")


def _client() -> tuple[str, str]:
    return (os.getenv("QUALTRICS_CLIENT_ID") or "").strip(), (os.getenv("QUALTRICS_CLIENT_SECRET") or "").strip()


def configured() -> bool:
    return all(_client())


def edit_url(survey_id: str) -> str:
    return f"{base_url()}/survey-builder/{survey_id}/edit"


async def _access_token(http: httpx.AsyncClient) -> str:
    global _token
    async with _token_lock:
        if _token and time.monotonic() < _token[1]:
            return _token[0]
        client_id, secret = _client()
        if not (client_id and secret):
            raise QualtricsError(NOT_CONFIGURED)
        response = await http.post(
            f"{base_url()}/oauth2/token", auth=(client_id, secret),
            data={"grant_type": "client_credentials", "scope": SCOPES},
        )
        data = response.json() if response.content else {}
        if response.status_code != 200 or not data.get("access_token"):
            raise QualtricsError(f"Qualtrics sign-in failed: {data.get('error_description') or data.get('error') or response.status_code}")
        # Tokens last an hour; renew five minutes early.
        _token = (data["access_token"], time.monotonic() + int(data.get("expires_in") or 3600) - 300)
        return _token[0]


async def call(http: httpx.AsyncClient, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    """One API v3 request; returns its ``result``. ``path`` starts after /API/v3."""
    token = await _access_token(http)
    response = await http.request(method, f"{base_url()}/API/v3{path}", json=body,
                                  headers={"Authorization": f"Bearer {token}"})
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code >= 400:
        error = (data.get("meta") or {}).get("error") or {}
        detail = "; ".join(f"{v.get('Field')}: {v.get('Description')}" for v in error.get("validationErrors") or [])
        raise QualtricsError(f"Qualtrics {method} {path} failed: {detail or error.get('errorMessage') or response.status_code}")
    return data.get("result")


def session() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=30.0)


def question_definition(question: dict[str, Any], index: int) -> dict[str, Any]:
    """One drafted question as a Qualtrics question definition."""
    text = question["text"]
    base: dict[str, Any] = {
        "QuestionText": text,
        "QuestionDescription": text[:100],
        "DataExportTag": f"Q{index + 1}",
        "Configuration": {"QuestionDescriptionOption": "UseText"},
        "Validation": {"Settings": {"ForceResponse": "OFF", "Type": "None"}},
        "Language": [],
    }

    def choices(labels: list[str] | tuple[str, ...]) -> dict[str, Any]:
        return {"Choices": {str(j + 1): {"Display": c} for j, c in enumerate(labels)},
                "ChoiceOrder": [str(j + 1) for j in range(len(labels))]}

    kind = question["type"]
    if kind == "multi_choice":
        return {**base, "QuestionType": "MC", "Selector": "MAVR", "SubSelector": "TX", **choices(question.get("choices") or [])}
    if kind == "likert":
        return {**base, "QuestionType": "MC", "Selector": "SAVR", "SubSelector": "TX", **choices(LIKERT)}
    if kind == "slider":
        low, high = int(question.get("min") or 0), int(question.get("max") if question.get("max") is not None else 10)
        return {
            **base, "QuestionType": "Slider", "Selector": "HSLIDER", "SubSelector": None,
            "Configuration": {
                "QuestionDescriptionOption": "UseText", "CSSliderMin": low, "CSSliderMax": high,
                "GridLines": min(high - low, 10), "SnapToGrid": True, "NumDecimals": "0",
                "ShowValue": True, "CustomStart": False, "NotApplicable": False, "MobileFirst": True,
            },
            "Choices": {"1": {"Display": ""}}, "ChoiceOrder": ["1"], "Labels": {}, "NextChoiceId": 2, "NextAnswerId": 1,
        }
    if kind == "text":
        return {**base, "QuestionType": "TE", "Selector": "ML", "DefaultChoices": False}
    return {**base, "QuestionType": "MC", "Selector": "SAVR", "SubSelector": "TX", **choices(question.get("choices") or [])}


async def create_survey(name: str, questions: list[dict[str, Any]]) -> dict[str, Any]:
    """A draft survey in the club's Qualtrics account. Questions that Qualtrics
    refuses are reported, not hidden; the survey keeps the rest."""
    async with session() as http:
        created = await call(http, "POST", "/survey-definitions",
                             {"SurveyName": name, "Language": "EN", "ProjectCategory": "CORE"})
        survey_id, block_id = created["SurveyID"], created["DefaultBlockID"]
        # A new survey starts with one blank question; remove it.
        try:
            existing = await call(http, "GET", f"/survey-definitions/{survey_id}/questions")
        except QualtricsError:
            existing = None
        for q in (existing or {}).get("elements") or []:
            try:
                await call(http, "DELETE", f"/survey-definitions/{survey_id}/questions/{q['QuestionID']}")
            except QualtricsError:
                pass
        added, failed = 0, []
        for index, question in enumerate(questions):
            try:
                await call(http, "POST", f"/survey-definitions/{survey_id}/questions?blockId={block_id}",
                           question_definition(question, index))
                added += 1
            except QualtricsError as exc:
                failed.append({"question": question["text"], "error": str(exc)})
    return {"survey_id": survey_id, "edit_url": edit_url(survey_id), "added": added, "failed": failed}
