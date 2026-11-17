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
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app.services.slack_agent import (
    MODEL_UNAVAILABLE_REPLY, WORKING_REPLY, already_handled, answer_event, handle_message,
    is_deterministic_message, member_for_slack_user, verify_slack_signature,
)
from app.services import slack_commands
from app.services.slack_tools import SlackContext

logger = logging.getLogger(__name__)
router = APIRouter()

# Events the helper answers. Anything else (channel joins, edits, bot chatter)
# is acknowledged and ignored.
ANSWERED_EVENTS = {"app_mention", "message"}
#: Slack's commands and events are a few kilobytes. These routes read the body
#: before any signature check, so anyone could otherwise make the server hold
#: up to nginx's 100 MB per request in memory.
MAX_BODY_BYTES = 256 * 1024


async def _body(request: Request) -> bytes | None:
    """The request body, or None when it is larger than MAX_BODY_BYTES. The
    body is kept on the request, so ``form()`` and ``json()`` still work."""
    declared = request.headers.get("content-length") or ""
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        return None
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            return None
        chunks.append(chunk)
    request._body = b"".join(chunks)  # Starlette's own cache: form() and json() read it
    return request._body


def _signed(request: Request, body: bytes) -> bool:
    return verify_slack_signature(
        body,
        request.headers.get("x-slack-request-timestamp", ""),
        request.headers.get("x-slack-signature", ""),
    )


@router.post("/commands")
async def slack_command(request: Request, background: BackgroundTasks):
    """The `/yucg` slash command. Model questions finish after the immediate ack."""
    body = await _body(request)
    if body is None:
        return JSONResponse({"error": "too_large"}, status_code=413)
    if not _signed(request, body):
        return JSONResponse({"error": "invalid_signature"}, status_code=401)

    form = await request.form()
    slack_user = str(form.get("user_id") or "")
    member = await member_for_slack_user(slack_user)
    text = str(form.get("text") or "")

    # help, onboard, link, unlink, usage, requests: answered from the database,
    # and the first three also for someone who has not linked an account yet.
    subcommand = slack_commands.parse_subcommand(text)
    if subcommand:
        reply, request_id = await slack_commands.answer(*subcommand, member, slack_user)
        if request_id:
            # DMs to the admins finish after the acknowledgement Slack waits for.
            background.add_task(slack_commands.deliver_request, request_id)
        return JSONResponse({"response_type": "ephemeral", "text": reply})

    if not member:
        return JSONResponse({"response_type": "ephemeral", "text": slack_commands.NOT_LINKED})

    # Slash commands stay single-turn: no channel/thread, so no memory and no
    # current-thread reading, but named channels the asker belongs to can be read.
    context = SlackContext(slack_user=str(form.get("user_id") or ""))
    if not text.strip() or is_deterministic_message(text):
        return JSONResponse(await handle_message(text, member, context))

    response_url = str(form.get("response_url") or "")
    parsed = urlparse(response_url)
    if parsed.scheme != "https" or parsed.hostname not in {"hooks.slack.com", "hooks.slack-gov.com"}:
        # Slack supplies response_url in production. Keeping this fallback makes
        # local/manual calls useful without creating an arbitrary outbound URL.
        return JSONResponse(await handle_message(text, member, context))

    async def answer_command() -> None:
        import httpx

        try:
            answer = await handle_message(text, member, context)
            payload = {
                "response_type": "ephemeral",
                "replace_original": True,
                "text": f"<@{slack_user}> " + str(answer.get("text") or MODEL_UNAVAILABLE_REPLY),
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(response_url, json=payload)
        except Exception:
            logger.exception("Slack helper failed to answer slash command")

    background.add_task(answer_command)
    return JSONResponse({
        "response_type": "ephemeral",
        "text": WORKING_REPLY,
    })


@router.post("/events")
async def slack_events(request: Request, background: BackgroundTasks):
    """Mentions and direct messages — the helper members actually talk to."""
    body = await _body(request)
    if body is None:
        return JSONResponse({"error": "too_large"}, status_code=413)
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
