"""Text generation: current Claude on Bedrock US inference profiles."""
from __future__ import annotations

import json
import os
import re
import threading
from typing import Any

# US geo profiles. Converse will not take a bare anthropic.* foundation id.
# Keep the default catalog deliberately small. Administrators can add a reviewed
# model ID through BEDROCK_ALLOWED_MODEL_IDS without exposing costly models by default.
BEDROCK_ANTHROPIC: list[dict[str, str]] = [
    {
        "id": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "label": "Claude",
        "tier": "haiku",
        "blurb": "Fast, grounded club work",
    },
]

_HAIKU = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
_ALLOWED = {m["id"] for m in BEDROCK_ANTHROPIC}
INFERENCE_SLOTS = 2
_inference_slots = threading.BoundedSemaphore(INFERENCE_SLOTS)
#: How long a call waits for a free slot before answering 429. Failing at
#: once turned a discovery run's AI review into 58 calls of which 48 were
#: refused (6 review workers, 2 slots), and a draft that met a busy slot got a
#: 429 it could have waited out. Past the wait the 429 still comes, and the
#: draft page still retries on it.
SLOT_WAIT_SEC = float(os.getenv("LLM_SLOT_WAIT_SEC", "20") or 20)
_UNAVAILABLE = "The language model is unavailable right now."


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
    explicit = (os.getenv("BEDROCK_MODEL_ID") or os.getenv("LLM_MODEL") or "").strip()
    if explicit:
        return explicit
    return _HAIKU


def rank_model_id() -> str:
    """Cheap labels on the host. Studio still uses the UI / default_model_id()."""
    explicit = (os.getenv("BEDROCK_RANK_MODEL_ID") or "").strip()
    if explicit:
        return explicit
    return _HAIKU


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


# Quota units one call consumes, relative to the Haiku-class member-facing tier
# at 1.0, derived from blended $/1M token pricing. A flat call count charges a
# Nova Micro triage call (~$0.035/1M in) the same as a Studio draft (~$1/1M in),
# so a cheap bulk fanout could exhaust the hour that Studio needs. Floors are
# deliberately above true cost ratio so throughput stays bounded too.
_MODEL_QUOTA_WEIGHTS: tuple[tuple[str, float], ...] = (
    ("nova-micro", 0.05),
    ("nova-lite", 0.10),
    ("nova-pro", 0.75),
    ("claude-haiku", 1.0),
    ("claude-sonnet", 3.0),
    ("claude-opus", 15.0),
)


def model_quota_weight(model_id: str | None) -> float:
    """Quota cost of one call to this model.

    An unrecognised model is charged the member-facing rate rather than being
    treated as free, so adding a model cannot silently bypass the club budget.
    """
    mid = (model_id or "").strip().lower()
    for token, weight in _MODEL_QUOTA_WEIGHTS:
        if token in mid:
            return weight
    return 1.0


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
) -> str:
    mid = validate_model(model_id)
    return _bedrock_text(prompt, mid, system, user_id=user_id, purpose=purpose, max_tokens=max_tokens)


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


def _bedrock_text(
    prompt: str,
    model_id: str,
    system: str | None,
    *,
    user_id: int | None = None,
    purpose: str = 'inference',
    max_tokens: int = 2048,
) -> str:
    import boto3

    from fastapi import HTTPException
    from botocore.config import Config
    mid = validate_model(model_id)
    if len(prompt) + len(system or '') > 24000:
        raise HTTPException(413, 'Draft input exceeds the 24,000 character limit')
    region = (os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1").strip()
    if not 1 <= max_tokens <= 4096:
        raise HTTPException(503, 'Model output limit is invalid')
    kwargs: dict[str, Any] = {
        "modelId": mid,
        "messages": [{"role": "user", "content": [{"text": prompt}]}],
        "inferenceConfig": {"maxTokens": max_tokens},
    }
    if system:
        kwargs["system"] = [{"text": system}]
    if not _inference_slots.acquire(timeout=SLOT_WAIT_SEC):
        raise HTTPException(429, 'Draft generation is busy; please retry shortly')
    try:
        from app.services.generation_policy import reserve_bedrock_invocation, complete_bedrock_invocation
        reservation_id = reserve_bedrock_invocation(
            mid,
            user_id=user_id,
            purpose=purpose,
            estimated_input_tokens=max(1,(len(prompt)+len(system or ''))//4),
            max_output_tokens=max_tokens,
        )
        runtime_config = Config(connect_timeout=5, read_timeout=60, retries={'total_max_attempts': 1})
        try:
            client = boto3.client("bedrock-runtime", region_name=region, config=runtime_config)
            resp = client.converse(**kwargs)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(503, _UNAVAILABLE) from exc
        usage = resp.get('usage') or {}
        complete_bedrock_invocation(reservation_id,usage.get('inputTokens'),usage.get('outputTokens'))
    finally:
        _inference_slots.release()
    parts = ((resp.get("output") or {}).get("message") or {}).get("content") or []
    texts = [p.get("text") or "" for p in parts if isinstance(p, dict)]
    return "".join(texts).strip()
