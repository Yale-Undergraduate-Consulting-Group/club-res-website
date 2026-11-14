"""Past-survey library: the club's Qualtrics surveys, kept as compact outlines
in ``survey_library`` so drafts reuse proven wording, scales and structure.

``sync`` copies every survey the club's Qualtrics client can see; ``retrieve``
ranks their questions against the conversation (BM25, no extra service).
Past surveys can hold client details: the library is visible to everyone with
the survey app, so an admin restricts the app (Admin -> Apps) or lists
confidential surveys in SURVEY_LIBRARY_SKIP before the first sync.
"""
from __future__ import annotations

import html
import json
import math
import os
import re
from collections import Counter
from typing import Any

from app.services import qualtrics

#: Always skipped: test runs and throwaways.
DEFAULT_SKIP = ("test", "delete me")
_TAGS = re.compile(r"<[^>]*>")
_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    "a an and are as at be by do does for from has have how i if in is it its me my of on or our so than that the "
    "their them they this to was we what when which who why will with would you your yes no not other none please "
    "select all apply following".split()
)


def skip_pattern() -> re.Pattern[str]:
    """A survey is skipped when any word of its name starts with a listed word
    or phrase (SURVEY_LIBRARY_SKIP, comma-separated), ignoring case."""
    words = [*DEFAULT_SKIP, *(os.getenv("SURVEY_LIBRARY_SKIP") or "").split(",")]
    words = [w.strip() for w in words if w.strip()]
    return re.compile(r"(^|[^a-z0-9])(" + "|".join(re.escape(w) for w in words) + ")", re.IGNORECASE)


def clean(value: Any) -> str:
    text = html.unescape(_TAGS.sub(" ", str(value or "")))
    return " ".join(text.split())


def _question_type(q: dict[str, Any]) -> str:
    kind = q.get("QuestionType")
    if kind == "MC":
        return "multi_choice" if q.get("Selector") in ("MAVR", "MAHR") else "single_choice"
    return {"Matrix": "matrix", "Slider": "slider", "TE": "text", "RO": "rank_order",
            "CS": "constant_sum", "DB": "descriptive"}.get(kind, str(kind))


def compact(definition: dict[str, Any]) -> dict[str, Any]:
    """A Qualtrics survey definition as {id, name, sections: [{name, questions}]}, in flow order."""
    flow: list[str] = []

    def walk(items: list[dict[str, Any]] | None) -> None:
        for item in items or []:
            if item.get("ID"):
                flow.append(item["ID"])
            walk(item.get("Flow"))

    walk((definition.get("SurveyFlow") or {}).get("Flow"))
    order = {block_id: i for i, block_id in enumerate(flow)}
    blocks = sorted((definition.get("Blocks") or {}).values(), key=lambda b: order.get(b.get("ID"), 1_000_000_000))
    questions_by_id = definition.get("Questions") or {}
    sections = []
    for block in blocks:
        if block.get("Type") == "Trash":
            continue
        questions = []
        for element in block.get("BlockElements") or []:
            q = element.get("Type") == "Question" and questions_by_id.get(element.get("QuestionID"))
            if not q:
                continue
            kind = _question_type(q)
            if kind == "descriptive":
                continue
            out: dict[str, Any] = {"type": kind, "text": clean(q.get("QuestionText"))}
            choices = [c for c in (clean(c.get("Display")) for c in (q.get("Choices") or {}).values()) if c]
            if kind == "matrix":
                out["statements"] = choices
                out["scale"] = [a for a in (clean(a.get("Display")) for a in (q.get("Answers") or {}).values()) if a]
            elif choices and kind not in ("slider", "text"):
                out["choices"] = choices
            if kind == "slider":
                config = q.get("Configuration") or {}
                out["min"], out["max"] = config.get("CSSliderMin"), config.get("CSSliderMax")
            if q.get("DisplayLogic"):
                out["conditional"] = True
            if out["text"]:
                questions.append(out)
        if questions:
            sections.append({"name": clean(block.get("Description")) or "Block", "questions": questions})
    name = re.sub(r"(\s*-\s*Copy)+$", "", clean(definition.get("SurveyName")), flags=re.IGNORECASE)
    return {"id": definition.get("SurveyID"), "name": name, "sections": sections}


async def sync(db: Any) -> dict[str, list[str]]:
    """Copy every visible survey into the library. Surveys no longer visible,
    or now on the skip list, leave it; one that fails to download keeps its
    previous copy."""
    skip = skip_pattern()
    async with qualtrics.session() as http:
        listing: list[dict[str, Any]] = []
        page = await qualtrics.call(http, "GET", "/surveys")
        while True:
            listing.extend(page.get("elements") or [])
            next_page = page.get("nextPage")
            if not next_page:
                break
            page = await qualtrics.call(http, "GET", re.sub(r"^.*/API/v3", "", next_page))
        saved, skipped, failed, keep = [], [], [], []
        for item in listing:
            if skip.search(item.get("name") or ""):
                skipped.append(item.get("name") or "")
                continue
            try:
                survey = compact(await qualtrics.call(http, "GET", f"/survey-definitions/{item['id']}"))
            except qualtrics.QualtricsError as exc:
                failed.append(f"{item.get('name')}: {exc}")
                keep.append(item["id"])
                continue
            await db.execute(
                """INSERT INTO survey_library (survey_id, name, outline_json, synced_at)
                   VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(survey_id) DO UPDATE SET name = excluded.name,
                       outline_json = excluded.outline_json, synced_at = excluded.synced_at""",
                (item["id"], survey["name"], json.dumps(survey["sections"])),
            )
            saved.append(survey["name"])
            keep.append(item["id"])
    marks = ",".join("?" * len(keep))
    await db.execute(f"DELETE FROM survey_library WHERE survey_id NOT IN ({marks})" if keep
                     else "DELETE FROM survey_library", keep)
    await db.commit()
    return {"saved": saved, "skipped": skipped, "failed": failed}


async def load(db: Any) -> list[dict[str, Any]]:
    """The library, minus surveys added to the skip list since they were copied."""
    skip = skip_pattern()
    rows = await (await db.execute(
        "SELECT survey_id, name, outline_json FROM survey_library ORDER BY name")).fetchall()
    return [{"id": r["survey_id"], "name": r["name"], "sections": json.loads(r["outline_json"])}
            for r in rows if not skip.search(r["name"])]


def tokens(text: str) -> list[str]:
    return [t for t in _WORD.findall(clean(text).lower()) if len(t) > 2 and t not in _STOP]


def retrieve(surveys: list[dict[str, Any]], query: str, *, max_surveys: int = 3,
             max_questions: int = 25) -> tuple[list[str], str]:
    """The past surveys and questions most like ``query``: their names, and a
    prompt-ready outline."""
    docs = []
    for survey in surveys:
        for section in survey["sections"]:
            for q in section["questions"]:
                words = " ".join([q["text"], *(q.get("choices") or []), *(q.get("statements") or [])])
                docs.append((survey, q, Counter(tokens(f"{words} {section['name']} {survey['name']}"))))
    if not docs:
        return [], ""
    df: Counter[str] = Counter()
    for *_, tf in docs:
        df.update(tf.keys())
    n, avg = len(docs), sum(sum(tf.values()) for *_, tf in docs) / len(docs) or 1
    k1, b = 1.4, 0.75
    query_terms = set(tokens(query))

    def score(tf: Counter[str]) -> float:
        length = sum(tf.values())
        total = 0.0
        for term in query_terms:
            f = tf.get(term)
            if f:
                idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
                total += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * length / avg))
        return total

    scored = sorted(((score(tf), survey, q) for survey, q, tf in docs), key=lambda x: -x[0])
    scored = [x for x in scored if x[0] > 0]
    by_survey: Counter[str] = Counter()
    for s, survey, _ in scored:
        by_survey[survey["id"]] += s
    top_ids = [sid for sid, _ in by_survey.most_common(max_surveys)]
    picked = [(survey, q) for _, survey, q in scored if survey["id"] in top_ids][:max_questions]
    if not picked:
        return [], ""

    def line(q: dict[str, Any]) -> str:
        out = f"- [{q['type']}] {q['text']}"
        if q.get("choices"):
            out += "  | choices: " + " / ".join(q["choices"])
        if q.get("statements"):
            out += "  | statements: " + " / ".join(q["statements"][:6]) + "  | scale: " + " / ".join(q.get("scale") or [])
        if q["type"] == "slider":
            out += f"  | range {q.get('min') if q.get('min') is not None else 0}-{q.get('max') if q.get('max') is not None else 10}"
        return out

    by_id = {s["id"]: s for s in surveys}
    parts = []
    for sid in top_ids:
        survey = by_id[sid]
        outline = " → ".join(f"{s['name']} ({len(s['questions'])})" for s in survey["sections"])
        lines = "\n".join(line(q) for s, q in picked if s["id"] == sid)
        parts.append(f"### {survey['name']}\nStructure: {outline}\nMost relevant questions:\n{lines}")
    return [by_id[sid]["name"] for sid in top_ids], "\n\n".join(parts)
