"""Slack's inbound door: a signed slash command and a signed event stream.

Slack cannot present a club session, so this router is mounted without the
usual authentication dependency and proves two things itself: the request
carries Slack's signature, and the Slack user has connected their own club
account. Everything it can do is read-only.

Events are acknowledged immediately and answered in the background, because
Slack retries anything it does not hear back from within three seconds — and
a retried mention that gets answered twice reads as a broken bot.
"""
import logging

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app.services.slack_agent import (
    HELP, already_handled, answer_event, handle_message, member_for_slack_user,
    verify_slack_signature,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Events the helper answers. Anything else (channel joins, edits, bot chatter)
# is acknowledged and ignored.
ANSWERED_EVENTS = {"app_mention", "message"}


def _signed(request: Request, body: bytes) -> bool:
    return verify_slack_signature(
        body,
        request.headers.get("x-slack-request-timestamp", ""),
        request.headers.get("x-slack-signature", ""),
    )


@router.post("/commands")
async def slack_command(request: Request):
    """The `/yucg` slash command. Plain questions work here too."""
    body = await request.body()
    if not _signed(request, body):
        return JSONResponse({"error": "invalid_signature"}, status_code=401)

    form = await request.form()
    member = await member_for_slack_user(str(form.get("user_id") or ""))
    if not member:
        return JSONResponse({
            "response_type": "ephemeral",
            "text": (
                "I do not know which club account this is. Open YUCG Outreach → "
                "Profile → Integrations and press *Connect Slack*, then try again."
            ),
        })

    text = str(form.get("text") or "")
    if text.strip() in {"", "help", "-h", "--help"}:
        return JSONResponse({"response_type": "ephemeral", "text": HELP})
    return JSONResponse(await handle_message(text, member))


@router.post("/events")
async def slack_events(request: Request, background: BackgroundTasks):
    """Mentions and direct messages — the helper members actually talk to."""
    body = await request.body()
    payload = await request.json()

    # Slack verifies a new event subscription by asking for the challenge
    # back. This one request is unsigned by design.
    if payload.get("type") == "url_verification":
        return PlainTextResponse(str(payload.get("challenge") or ""))

    if not _signed(request, body):
        return JSONResponse({"error": "invalid_signature"}, status_code=401)

    event = payload.get("event") or {}
    event_type = str(event.get("type") or "")
    # The helper's own messages arrive as events; answering them is a loop.
    is_bot = bool(event.get("bot_id")) or event.get("subtype") == "bot_message"
    # A plain channel message is not a question for the helper: only mentions
    # and direct messages (channel_type "im") are.
    directed = event_type == "app_mention" or (
        event_type == "message" and event.get("channel_type") == "im"
    )
    if event_type not in ANSWERED_EVENTS or is_bot or not directed:
        return JSONResponse({"ok": True})

    if await already_handled(str(payload.get("event_id") or "")):
        return JSONResponse({"ok": True, "duplicate": True})

    async def answer() -> None:
        try:
            await answer_event(event)
        except Exception:
            logger.exception("Slack helper failed to answer %s", event_type)

    # Acknowledge now, answer immediately after: Slack retries at three
    # seconds and a database read plus a post can exceed that.
    background.add_task(answer)
    return JSONResponse({"ok": True})
