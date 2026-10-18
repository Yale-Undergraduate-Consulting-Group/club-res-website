"""A search provider that fails must say so, not look like an empty company.

TinyFish and Firecrawl swallowed their errors and returned [], so a run whose
every search failed told members "large-company sites rarely publish person
emails". No network: the providers are replaced.
From backend/: python3 tests/test_search_failures.py"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JWT_SECRET", "p-search-failures-secret-not-a-known-default-xx")

from app.services import web_contact_discovery as W  # noqa: E402
from app.services import web_fetch as F  # noqa: E402


async def cases() -> None:
    # TinyFish fails and there is no fallback: empty, with the reason kept.
    with patch.object(F, "tinyfish_configured", return_value=True), \
         patch.object(F, "firecrawl_configured", return_value=False), \
         patch.object(F, "_tinyfish_web_search", AsyncMock(return_value=None)):
        assert await F.web_search("Barclays site:linkedin.com/in") == []
        assert F.last_search_failure() == "TinyFish gave no answer"

    # A real empty answer is not a failure.
    with patch.object(F, "tinyfish_configured", return_value=True), \
         patch.object(F, "firecrawl_configured", return_value=False), \
         patch.object(F, "_tinyfish_web_search", AsyncMock(return_value=[])):
        assert await F.web_search("nobody at all") == []
        assert F.last_search_failure() is None

    # A run counts the failure, so its message can say search was down.
    with patch.object(F, "web_search_configured", return_value=True), \
         patch.object(F, "tinyfish_configured", return_value=True), \
         patch.object(F, "firecrawl_configured", return_value=False), \
         patch.object(F, "_tinyfish_web_search", AsyncMock(return_value=None)):
        run, token = W.begin_search_run()
        try:
            assert await W._tavily_search("Barclays London site:linkedin.com/in") == []
        finally:
            W.end_search_run(token)
    summary = W.search_errors_summary(run)
    assert summary and summary["all_failed"] and summary["first_message"] == "TinyFish gave no answer", summary


if __name__ == "__main__":
    asyncio.run(cases())
    print("search failures: ok")
