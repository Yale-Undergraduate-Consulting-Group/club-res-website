"""Text generation: Claude on Bedrock US inference profiles, chosen by use.

Every use names a tier, and each tier lists models best-first. A model the
account cannot call yet (AWS gates new models per account) is skipped for a
while, so the site always runs the newest model the account may use and moves
up on its own once AWS enables a newer one.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Any, Callable

#: Tier -> models, best first. US geo inference profiles: Converse will not take
#: a bare anthropic.* foundation id.
TIERS: dict[str, tuple[str, ...]] = {
    # Ranking, routing, classification and JSON extraction: high volume.
    "fast": ("us.anthropic.claude-haiku-5-5", "us.anthropic.claude-haiku-4-5-20251001-v1:0"),
    # Member-facing writing: drafts, templates, Slack replies.
    "write": ("us.anthropic.claude-sonnet-5-5", "us.anthropic.claude-sonnet-4-6"),
    # Hardest analysis; used when a member picks it.
    "deep": ("us.anthropic.claude-opus-5-5", "us.anthropic.claude-opus-4-6-v1"),
}
#: USD per million tokens (input, output) at the standard global rate, from the
#: Bedrock offers and price list (2026-11-05). us.* profiles add GEO_PREMIUM.
PRICES_PER_MILLION: dict[str, tuple[float, float]] = {
    "us.anthropic.claude-haiku-5-5": (0.10, 0.50),
    "us.anthropic.claude-haiku-4-5-20251001-v1:0": (1.00, 5.00),
    "us.anthropic.claude-sonnet-5-5": (2.00, 10.00),
    "us.anthropic.claude-sonnet-4-6": (3.00, 15.00),
    "us.anthropic.claude-opus-5-5": (4.00, 20.00),
    "us.anthropic.claude-opus-4-6-v1": (5.00, 25.00),
}
GEO_PREMIUM = 1.10
#: An unpriced model is charged as the most expensive one, so adding a model
#: cannot slip past the club budget.
_UNPRICED = max(PRICES_PER_MILLION.values(), key=lambda price: price[1])

#: The models a member may choose; the ids are each tier's best model.
BEDROCK_ANTHROPIC: list[dict[str, str]] = [
    {"id": TIERS["fast"][0], "label": "Claude Haiku", "tier": "haiku", "blurb": "Fastest and cheapest"},
    {"id": TIERS["write"][0], "label": "Claude Sonnet", "tier": "sonnet", "blurb": "Default for writing and analysis"},
    {"id": TIERS["deep"][0], "label": "Claude Opus", "tier": "opus", "blurb": "Most capable; about twice Sonnet's price"},
]
_ALLOWED = {model for chain in TIERS.values() for model in chain}

INFERENCE_SLOTS = 2
_inference_slots = threading.BoundedSemaphore(INFERENCE_SLOTS)
#: How long a call waits for a free slot before answering 429. Failing at
#: once turned a discovery run's AI review into 58 calls of which 48 were
#: refused (6 review workers, 2 slots), and a draft that met a busy slot got a
#: 429 it could have waited out. Past the wait the 429 still comes, and the
#: draft page still retries on it.
SLOT_WAIT_SEC = float(os.getenv("LLM_SLOT_WAIT_SEC", "20") or 20)
_UNAVAILABLE = "The language model is unavailable right now."
#: How long a model AWS refused for this account is skipped before it is tried again.
GATED_RETRY_SEC = 15 * 60
_gated_until: dict[str, float] = {}
_gated_lock = threading.Lock()


def allowed_bedrock_models() -> set[str]:
    configured = os.getenv('BEDROCK_ALLOWED_MODEL_IDS', '')
    if configured.strip():
        return {value.strip() for value in configured.split(',') if value.strip()}
    return _ALLOWED | {default_model_id(), rank_model_id()}


def validate_model(model_id: str | None) -> str:
    from fastapi import HTTPException
    mid = (model_id or default_model_id()).strip()
    if mid not in allowed_bedrock_models():
        raise HTTPException(400, 'This model is not enabled by the administrator')
    return mid


def llm_provider() -> str:
    return "bedrock"


def default_model_id() -> str:
    """Member-facing writing and analysis."""
    explicit = (os.getenv("BEDROCK_MODEL_ID") or os.getenv("LLM_MODEL") or "").strip()
    return explicit or TIERS["write"][0]


def rank_model_id() -> str:
    """High-volume ranking and extraction."""
    explicit = (os.getenv("BEDROCK_RANK_MODEL_ID") or "").strip()
    return explicit or TIERS["fast"][0]


_BEDROCK_PROVIDERS = frozenset(
    {"anthropic", "amazon", "meta", "mistral", "cohere", "ai21", "deepseek"}
)
_BEDROCK_REGION_PREFIXES = frozenset({"us", "eu", "apac", "global"})


def is_bedrock_model(model_id: str | None) -> bool:
    """True for a Bedrock model id, with or without a cross-region prefix.

    Restricting this to Anthropic silently rejected ids such as
    `us.amazon.nova-micro-v1:0`, which blocked using a cheap model for the
    bulk ranking tier.
    """
    parts = (model_id or "").strip().lower().split(".")
    if parts and parts[0] in _BEDROCK_REGION_PREFIXES:
        parts = parts[1:]
    return len(parts) >= 2 and parts[0] in _BEDROCK_PROVIDERS


def cost_usd(model_id: str, input_tokens: int, output_tokens: int) -> float:
    """What a call costs at list price, including the US geo-profile premium."""
    price_in, price_out = PRICES_PER_MILLION.get(model_id, _UNPRICED)
    premium = GEO_PREMIUM if model_id.startswith("us.") or model_id not in PRICES_PER_MILLION else 1.0
    return (max(0, input_tokens) * price_in + max(0, output_tokens) * price_out) * premium / 1_000_000


def model_chain(model_id: str) -> tuple[str, ...]:
    """The model and the tier's older models after it, which stand in while it is gated."""
    for chain in TIERS.values():
        if model_id in chain:
            return chain[chain.index(model_id):]
    return (model_id,)


def list_models() -> dict[str, Any]:
    return {
        "provider": llm_provider(),
        "default": default_model_id(),
        "groups": [
            {"id": "anthropic", "label": "Claude on Bedrock", "models": [m for m in BEDROCK_ANTHROPIC if m['id'] in allowed_bedrock_models()]},
        ],
    }


def complete_text(
    prompt: str,
    model_id: str | None = None,
    system: str | None = None,
    *,
    user_id: int | None = None,
    purpose: str = 'inference',
    max_tokens: int = 2048,
    on_model: Callable[[str], None] | None = None,
) -> str:
    from fastapi import HTTPException
    if len(prompt) + len(system or '') > 24000:
        raise HTTPException(413, 'Draft input exceeds the 24,000 character limit')
    response = converse(
        [{"role": "user", "content": [{"text": prompt}]}],
        model_id=model_id, system=system, user_id=user_id, purpose=purpose, max_tokens=max_tokens,
        on_model=on_model,
    )
    return response_text(response)


def complete_json(
    prompt: str,
    model_id: str | None = None,
    system: str | None = None,
    max_tokens: int = 2048,
) -> dict[str, Any] | None:
    raw = complete_text(prompt, model_id=model_id, system=system, max_tokens=max_tokens)
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        return None
    try:
        data = json.loads(match.group())
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def response_text(response: dict[str, Any]) -> str:
    parts = ((response.get("output") or {}).get("message") or {}).get("content") or []
    return "".join(p.get("text") or "" for p in parts if isinstance(p, dict)).strip()


def _gated(exc: Exception) -> bool:
    """True when AWS refused the model for this account, not the request itself."""
    error = getattr(exc, "response", None)
    if not isinstance(error, dict):
        return False
    code = (error.get("Error") or {}).get("Code", "")
    message = (error.get("Error") or {}).get("Message", "").lower()
    if code == "ResourceNotFoundException":
        return True  # retired version, or a use-case form not yet on file
    # The whole account barred from Bedrock (seen since 2026-11-06 as
    # "ValidationException: Error 002: Access to Bedrock models is not allowed
    # for this account"): refused before any inference, so nothing is spent.
    if "not allowed for this account" in message:
        return True
    if code == "AccessDeniedException":
        return "not available for this account" in message
    return code == "ValidationException" and "model identifier is invalid" in message


def refused_everywhere(model_id: str | None) -> bool:
    """Every model that could stand in for ``model_id`` was refused for this
    account within GATED_RETRY_SEC. Callers with their own retries (contact
    review) stop at once instead of repeating calls that cannot succeed."""
    chain = model_chain(validate_model(model_id))
    now = time.monotonic()
    with _gated_lock:
        return all(_gated_until.get(m, 0) > now for m in chain)


def _usable(chain: tuple[str, ...]) -> list[str]:
    now = time.monotonic()
    with _gated_lock:
        open_models = [m for m in chain if _gated_until.get(m, 0) <= now]
    # When every model is marked, try them all again rather than refuse outright.
    return open_models or list(chain)


def converse(
    messages: list[dict[str, Any]],
    *,
    model_id: str | None = None,
    system: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    user_id: int | None = None,
    purpose: str = 'inference',
    max_tokens: int = 2048,
    on_model: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """One Bedrock Converse call, charged to the club budget, on the best usable model.

    Returns the raw Converse response; ``tools`` takes Converse toolSpec entries.
    """
    import boto3

    from botocore.config import Config
    from fastapi import HTTPException

    from app.services.generation_policy import (
        complete_bedrock_invocation, release_bedrock_invocation, reserve_bedrock_invocation,
    )
    requested = validate_model(model_id)
    if not 1 <= max_tokens <= 4096:
        raise HTTPException(503, 'Model output limit is invalid')
    region = (os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1").strip()
    characters = len(system or '') + len(json.dumps(messages, default=str))
    kwargs: dict[str, Any] = {"messages": messages, "inferenceConfig": {"maxTokens": max_tokens}}
    if system:
        kwargs["system"] = [{"text": system}]
    if tools:
        kwargs["toolConfig"] = {"tools": tools}
    runtime_config = Config(connect_timeout=5, read_timeout=60, retries={'total_max_attempts': 1})
    if not _inference_slots.acquire(timeout=SLOT_WAIT_SEC):
        raise HTTPException(429, 'Draft generation is busy; please retry shortly')
    try:
        client = boto3.client("bedrock-runtime", region_name=region, config=runtime_config)
        for mid in _usable(model_chain(requested)):
            reservation_id = reserve_bedrock_invocation(
                mid, user_id=user_id, purpose=purpose,
                estimated_input_tokens=max(1, characters // 4), max_output_tokens=max_tokens,
            )
            try:
                response = client.converse(modelId=mid, **kwargs)
            except Exception as exc:
                if _gated(exc):
                    # Refused before any inference: nothing was spent.
                    release_bedrock_invocation(reservation_id)
                    with _gated_lock:
                        _gated_until[mid] = time.monotonic() + GATED_RETRY_SEC
                    continue
                # Uncertain outcome: keep the reservation so spend is never undercounted.
                raise HTTPException(503, _UNAVAILABLE) from exc
            usage = response.get('usage') or {}
            complete_bedrock_invocation(reservation_id, usage.get('inputTokens'), usage.get('outputTokens'))
            if on_model:
                on_model(mid)
            return response
        raise HTTPException(503, _UNAVAILABLE)
    finally:
        _inference_slots.release()
