"""Rank model is Haiku on Bedrock. From backend/: python3 tests/test_llm_provider.py"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from fastapi import HTTPException
from app.services.llm import complete_text, default_model_id, llm_provider, rank_model_id  # noqa: E402


def test_bedrock_rank_is_haiku() -> None:
    os.environ["LLM_PROVIDER"] = "bedrock"
    os.environ.pop("BEDROCK_RANK_MODEL_ID", None)
    os.environ.pop("BEDROCK_MODEL_ID", None)
    os.environ.pop("LLM_MODEL", None)
    assert llm_provider() == "bedrock"
    assert "haiku" in rank_model_id().lower()
    assert rank_model_id() == default_model_id(), "Interactive and ranking work share the reviewed low-cost model"


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
    assert "haiku" in default_model_id().lower()


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


if __name__ == "__main__":
    test_bedrock_rank_is_haiku()
    test_explicit_rank_wins()
    test_assistant_stays_on_bedrock_when_provider_is_unset()
    test_ollama_provider_still_uses_bedrock_only()
    test_missing_bedrock_credentials_are_unavailable()
    print("ok")
