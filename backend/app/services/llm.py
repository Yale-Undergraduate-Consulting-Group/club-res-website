"""Text generation: Claude, chosen by use.

Every use names a tier, and each tier lists Bedrock models best-first. A model
the account cannot call yet (AWS gates new models per account) is skipped for
a while, so the site always runs the newest model the account may use and
moves up on its own once AWS enables a newer one.

While ANTHROPIC_API_KEY is set (and not past ANTHROPIC_API_KEY_EXPIRES) every
call goes to Anthropic's own API instead, in the same Converse request and
response shape, so no caller changes: Haiku for the fast tier, Sonnet for
writing and for the deep tier. Removing or expiring the key returns the site
to Bedrock; replacing the key and its expiry date switches to a new key.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timezone
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
    # Anthropic's own API, no geo premium. ponytail: priced as the Bedrock list
    # price of the same model; the key's own spending limit is the hard stop.
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
}
#: Tier -> model on Anthropic's own API. Sonnet stands in for the deep tier, so
#: a member choosing the most capable model gets Sonnet while this key is used.
ANTHROPIC_MODELS: dict[str, str] = {
    "fast": "claude-haiku-5-5",
    "write": "claude-sonnet-5-5",
    "deep": "claude-sonnet-5-5",
}
#: These models reason before answering unless told not to, which spends the
#: output allowance on hidden thinking (a 40-token Sonnet reply came back
#: empty). Each model names "off" its own way.
ANTHROPIC_THINKING_OFF: dict[str, dict[str, str]] = {
    "claude-haiku-5-5": {"type": "disabled"},
    "claude-sonnet-5-5": {"type": "between_tools"},
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


def anthropic_key() -> str | None:
    """The Anthropic API key while it is configured and not past its expiry
    date (ANTHROPIC_API_KEY_EXPIRES, YYYY-MM-DD, the last UTC day it is used).
    An unreadable date disables the key rather than running it unbounded."""
    key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    if not key:
        return None
    expires = (os.getenv("ANTHROPIC_API_KEY_EXPIRES") or "").strip()
    if expires:
        try:
            last_day = datetime.strptime(expires, "%Y-%m-%d").date()
        except ValueError:
            return None
        if datetime.now(timezone.utc).date() > last_day:
            return None
    return key


def llm_provider() -> str:
    return "anthropic" if anthropic_key() else "bedrock"


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
    if llm_provider() == "anthropic":
        # The deep tier runs on Sonnet here, so offering "Opus" would mislead.
        models = [m for m in BEDROCK_ANTHROPIC if m["tier"] != "opus"]
        label = "Claude (Anthropic API)"
    else:
        models = [m for m in BEDROCK_ANTHROPIC if m['id'] in allowed_bedrock_models()]
        label = "Claude on Bedrock"
    return {
        "provider": llm_provider(),
        "default": default_model_id(),
        "groups": [{"id": "anthropic", "label": label, "models": models}],
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
    if anthropic_key():
        return False
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
    """One Converse-shaped call, charged to the club budget, on the best usable model.

    Returns the raw Converse response; ``tools`` takes Converse toolSpec entries.
    With an Anthropic key configured the call goes to Anthropic's own API.
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
        key = anthropic_key()
        if key:
            return _anthropic_converse(
                key, requested, messages, system=system, tools=tools, max_tokens=max_tokens,
                user_id=user_id, purpose=purpose, characters=characters, on_model=on_model,
            )
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


ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"


def _tier_of(model_id: str) -> str:
    for tier, chain in TIERS.items():
        if model_id in chain:
            return tier
    return "write"


def _to_anthropic_block(block: dict[str, Any]) -> dict[str, Any]:
    if "anthropic" in block:
        # A reasoning block the API returned mid tool loop goes back unchanged.
        return block["anthropic"]
    if "text" in block:
        return {"type": "text", "text": block["text"]}
    if "toolUse" in block:
        use = block["toolUse"]
        return {"type": "tool_use", "id": use["toolUseId"], "name": use["name"], "input": use.get("input") or {}}
    if "toolResult" in block:
        result = block["toolResult"]
        parts = []
        for item in result.get("content") or []:
            if "text" in item:
                parts.append({"type": "text", "text": item["text"]})
            elif "json" in item:
                parts.append({"type": "text", "text": json.dumps(item["json"], default=str)})
        out: dict[str, Any] = {"type": "tool_result", "tool_use_id": result["toolUseId"], "content": parts}
        if result.get("status") == "error":
            out["is_error"] = True
        return out
    raise ValueError(f"Unsupported content block: {sorted(block)}")


def _from_anthropic(data: dict[str, Any]) -> dict[str, Any]:
    """Anthropic's Messages response in the Converse shape every caller reads."""
    content: list[dict[str, Any]] = []
    for block in data.get("content") or []:
        if block.get("type") == "text":
            content.append({"text": block.get("text") or ""})
        elif block.get("type") == "tool_use":
            content.append({"toolUse": {"toolUseId": block["id"], "name": block["name"], "input": block.get("input") or {}}})
        elif block.get("type") in {"thinking", "redacted_thinking"}:
            # Kept opaque so a tool loop can hand it back; response_text skips it.
            content.append({"anthropic": block})
    usage = data.get("usage") or {}
    return {
        "output": {"message": {"role": "assistant", "content": content}},
        "stopReason": data.get("stop_reason") or "end_turn",
        "usage": {"inputTokens": usage.get("input_tokens"), "outputTokens": usage.get("output_tokens")},
    }


def _anthropic_converse(
    key: str,
    requested: str,
    messages: list[dict[str, Any]],
    *,
    system: str | None,
    tools: list[dict[str, Any]] | None,
    max_tokens: int,
    user_id: int | None,
    purpose: str,
    characters: int,
    on_model: Callable[[str], None] | None,
) -> dict[str, Any]:
    import httpx
    from fastapi import HTTPException

    from app.services.generation_policy import (
        complete_bedrock_invocation, release_bedrock_invocation, reserve_bedrock_invocation,
    )
    model = ANTHROPIC_MODELS[_tier_of(requested)]
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "thinking": ANTHROPIC_THINKING_OFF[model],
        "messages": [
            {"role": m["role"], "content": [_to_anthropic_block(b) for b in m.get("content") or []]}
            for m in messages
        ],
    }
    if system:
        body["system"] = system
    if tools:
        body["tools"] = [
            {"name": t["toolSpec"]["name"], "description": t["toolSpec"].get("description", ""),
             "input_schema": (t["toolSpec"].get("inputSchema") or {}).get("json") or {"type": "object"}}
            for t in tools
        ]
    reservation_id = reserve_bedrock_invocation(
        model, user_id=user_id, purpose=purpose,
        estimated_input_tokens=max(1, characters // 4), max_output_tokens=max_tokens,
    )
    try:
        response = httpx.post(
            ANTHROPIC_URL, json=body, timeout=httpx.Timeout(60.0, connect=5.0),
            headers={"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION},
        )
    except httpx.HTTPError as exc:
        # Uncertain outcome: keep the reservation so spend is never undercounted.
        raise HTTPException(503, _UNAVAILABLE) from exc
    if response.status_code != 200:
        # A refused request (bad or spent key, rate limit, invalid input) is
        # answered before any inference, so nothing was spent.
        if response.status_code < 500:
            release_bedrock_invocation(reservation_id)
        if response.status_code == 429:
            raise HTTPException(429, 'Draft generation is busy; please retry shortly')
        raise HTTPException(503, _UNAVAILABLE)
    result = _from_anthropic(response.json())
    complete_bedrock_invocation(reservation_id, result["usage"]["inputTokens"], result["usage"]["outputTokens"])
    if on_model:
        on_model(model)
    return result
