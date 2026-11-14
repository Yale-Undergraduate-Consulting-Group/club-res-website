"""Ranking runs on the fast Haiku tier and writing on Sonnet, on Bedrock or, while
a key is configured, on Anthropic's own API. From backend/: python3 tests/test_llm_provider.py"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from fastapi import HTTPException

# The Bedrock cases below must not pick up a key from the developer's shell.
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("ANTHROPIC_API_KEY_EXPIRES", None)
from app.services.llm import complete_text, default_model_id, llm_provider, rank_model_id  # noqa: E402


def test_bedrock_rank_is_haiku() -> None:
    os.environ["LLM_PROVIDER"] = "bedrock"
    os.environ.pop("BEDROCK_RANK_MODEL_ID", None)
    os.environ.pop("BEDROCK_MODEL_ID", None)
    os.environ.pop("LLM_MODEL", None)
    assert llm_provider() == "bedrock"
    assert "haiku" in rank_model_id().lower()
    assert "sonnet" in default_model_id().lower(), "member-facing writing runs on the write tier"


def test_explicit_rank_wins() -> None:
    os.environ["LLM_PROVIDER"] = "bedrock"
    os.environ["BEDROCK_RANK_MODEL_ID"] = "us.anthropic.claude-3-haiku-20240307-v1:0"
    assert rank_model_id() == "us.anthropic.claude-3-haiku-20240307-v1:0"


def test_assistant_stays_on_bedrock_when_provider_is_unset() -> None:
    os.environ.pop("LLM_PROVIDER", None)
    os.environ.pop("BEDROCK_RANK_MODEL_ID", None)
    os.environ.pop("BEDROCK_MODEL_ID", None)
    os.environ.pop("LLM_MODEL", None)
    os.environ["OLLAMA_MODEL"] = "llama3.2"
    assert llm_provider() == "bedrock"
    assert "haiku" in rank_model_id().lower()
    assert "sonnet" in default_model_id().lower()


def test_ollama_provider_still_uses_bedrock_only() -> None:
    os.environ["LLM_PROVIDER"] = "ollama"
    os.environ.pop("BEDROCK_RANK_MODEL_ID", None)
    os.environ.pop("BEDROCK_MODEL_ID", None)
    os.environ.pop("LLM_MODEL", None)
    with patch("boto3.client") as factory:
        factory.return_value.converse.return_value = {
            "output": {"message": {"content": [{"text": "hosted"}]}},
            "usage": {},
        }
        assert complete_text("ping") == "hosted"
        factory.assert_called()
    try:
        complete_text("ping", "ollama:local")
        raise AssertionError("ollama:local must not be an inference outlet")
    except HTTPException as exc:
        assert exc.status_code == 400


def test_missing_bedrock_credentials_are_unavailable() -> None:
    from botocore.exceptions import NoCredentialsError

    os.environ.pop("BEDROCK_RANK_MODEL_ID", None)
    os.environ.pop("BEDROCK_MODEL_ID", None)
    os.environ.pop("LLM_MODEL", None)
    os.environ.pop("AWS_PROFILE", None)
    with patch("boto3.client") as factory, patch("subprocess.run") as run:
        factory.return_value.converse.side_effect = NoCredentialsError()
        try:
            complete_text("ping", rank_model_id())
            raise AssertionError("Missing instance credentials became a 500")
        except HTTPException as exc:
            assert exc.status_code == 503
            assert "unavailable" in str(exc.detail).lower()
        run.assert_not_called()


class _Reply:
    def __init__(self, status: int, data: dict | None = None) -> None:
        self.status_code = status
        self._data = data or {}

    def json(self) -> dict:
        return self._data


def _with_key(expires: str | None = None) -> None:
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test"
    if expires is None:
        os.environ.pop("ANTHROPIC_API_KEY_EXPIRES", None)
    else:
        os.environ["ANTHROPIC_API_KEY_EXPIRES"] = expires


def _clear_key() -> None:
    os.environ.pop("ANTHROPIC_API_KEY", None)
    os.environ.pop("ANTHROPIC_API_KEY_EXPIRES", None)


def test_key_is_used_through_its_last_day_and_not_after() -> None:
    from datetime import datetime, timedelta, timezone

    today = datetime.now(timezone.utc).date()
    _with_key(today.isoformat())
    assert llm_provider() == "anthropic", "the expiry date is the last day the key is used"
    _with_key((today - timedelta(days=1)).isoformat())
    assert llm_provider() == "bedrock", "an expired key hands the site back to Bedrock"
    _with_key("next week")
    assert llm_provider() == "bedrock", "an unreadable expiry must not run the key unbounded"
    _clear_key()


def test_tiers_map_to_haiku_and_sonnet_and_tool_calls_round_trip() -> None:
    from app.services import generation_policy, llm

    _with_key()
    sent: list[dict] = []
    charged: list[tuple] = []

    def fake_post(url, json, timeout, headers):
        sent.append(json)
        return _Reply(200, {
            "content": [{"type": "text", "text": "Looking."},
                        {"type": "tool_use", "id": "tu_1", "name": "find", "input": {"q": "acme"}}],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 120, "output_tokens": 30},
        })

    with patch("httpx.post", fake_post), \
         patch.object(generation_policy, "reserve_bedrock_invocation", lambda model, **kw: charged.append(("reserve", model)) or 7), \
         patch.object(generation_policy, "complete_bedrock_invocation", lambda rid, i, o: charged.append(("complete", rid, i, o))), \
         patch.object(generation_policy, "release_bedrock_invocation", lambda rid: charged.append(("release", rid))):
        tools = [{"toolSpec": {"name": "find", "description": "Search", "inputSchema": {"json": {"type": "object"}}}}]
        history = [
            {"role": "user", "content": [{"text": "who at acme?"}]},
            {"role": "assistant", "content": [{"toolUse": {"toolUseId": "tu_0", "name": "find", "input": {}}}]},
            {"role": "user", "content": [{"toolResult": {"toolUseId": "tu_0", "content": [{"json": {"n": 2}}]}}]},
        ]
        response = llm.converse(history, model_id=rank_model_id(), system="be brief", tools=tools)
        llm.converse([{"role": "user", "content": [{"text": "x"}]}], model_id=llm.TIERS["deep"][0])

    assert sent[0]["model"] == "claude-haiku-5-5", "the fast tier runs on Haiku"
    assert sent[1]["model"] == "claude-sonnet-5-5", "the deep tier runs on Sonnet while the key is used"
    assert sent[0]["tools"][0]["input_schema"] == {"type": "object"}
    assert sent[0]["messages"][2]["content"][0] == {"type": "tool_result", "tool_use_id": "tu_0",
                                                    "content": [{"type": "text", "text": '{"n": 2}'}]}
    assert response["stopReason"] == "tool_use"
    blocks = response["output"]["message"]["content"]
    assert blocks[1]["toolUse"] == {"toolUseId": "tu_1", "name": "find", "input": {"q": "acme"}}
    assert ("reserve", "claude-haiku-5-5") in charged and ("complete", 7, 120, 30) in charged, "every call is charged to the club budget"
    _clear_key()


def test_a_refused_call_is_not_charged_and_reads_unavailable() -> None:
    from app.services import generation_policy, llm

    _with_key()
    released: list[int] = []
    with patch("httpx.post", lambda *a, **k: _Reply(401, {"error": {"type": "authentication_error"}})), \
         patch.object(generation_policy, "reserve_bedrock_invocation", lambda model, **kw: 9), \
         patch.object(generation_policy, "complete_bedrock_invocation", lambda *a: None), \
         patch.object(generation_policy, "release_bedrock_invocation", released.append):
        try:
            llm.complete_text("ping", rank_model_id())
            raise AssertionError("a refused key must not look like an answer")
        except HTTPException as exc:
            assert exc.status_code == 503
    assert released == [9], "a refused request spends nothing, so its reservation is released"
    _clear_key()

if __name__ == "__main__":
    test_bedrock_rank_is_haiku()
    test_explicit_rank_wins()
    test_assistant_stays_on_bedrock_when_provider_is_unset()
    test_ollama_provider_still_uses_bedrock_only()
    test_missing_bedrock_credentials_are_unavailable()
    test_key_is_used_through_its_last_day_and_not_after()
    test_tiers_map_to_haiku_and_sonnet_and_tool_calls_round_trip()
    test_a_refused_call_is_not_charged_and_reads_unavailable()
    print("ok")
