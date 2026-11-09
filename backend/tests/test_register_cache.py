"""Register reads under load (app/services/register_cache.py).

Concurrent members share one summary scan and identical searches, at most
one uncached scan runs at a time, a search whose browser has gone is not
computed, and "claimed by" stays current on a shared page.
From backend/: python tests/test_register_cache.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JWT_SECRET", "register-cache-test-secret-0123456789ab")
os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.mkdtemp()) / 't.db'}"

from fastapi import HTTPException  # noqa: E402

from app.services import company_register, register_cache  # noqa: E402

PAGE = {"items": [{"display_name": "Acme", "company_domain": "acme.com", "claimed_by": None}], "total": 1, "limit": 50, "offset": 0}


async def scenario() -> None:
    running, peak, scans = 0, 0, []

    async def slow(**params):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        scans.append(params.get("q"))
        await asyncio.sleep(0.05)
        running -= 1
        return PAGE

    async def slow_summary():
        scans.append("summary")
        await asyncio.sleep(0.05)
        return {"companies": 1}

    claimed = {"member": None}

    async def claims(companies):
        return {"acme.com": {"member": claimed["member"]}} if claimed["member"] else {}

    params = lambda q: dict(q=q, tier=None, country=None, sector=None, min_amount=None, with_officers=False,  # noqa: E731
                            include_hidden=False, limit=50, offset=0, sort="value")

    async def present():
        return False

    async def gone():
        return True

    with patch.object(company_register, "browse_register", slow), \
         patch.object(company_register, "register_summary", slow_summary), \
         patch("app.services.company_claims.claims_for", claims), \
         patch("app.services.company_claims.company_key", lambda name, domain=None: domain):
        # Twenty members open the page at once: one summary scan, one per distinct search.
        await asyncio.gather(*[register_cache.summary(present) for _ in range(20)])
        pages = await asyncio.gather(*[register_cache.browse(params(q), present) for q in ["bio", "ai", "bio", "ai", "energy"] * 4])
        assert scans.count("summary") == 1, scans
        assert sorted(s for s in scans if s != "summary") == ["ai", "bio", "energy"], scans
        assert peak == 1, f"{peak} register scans ran at once"
        assert all(p["items"][0]["claimed_by"] is None for p in pages)

        # A colleague claims Acme: the cached page shows it at once.
        claimed["member"] = "Ana"
        assert (await register_cache.browse(params("bio"), present))["items"][0]["claimed_by"] == "Ana"
        assert PAGE["items"][0]["claimed_by"] is None, "the shared page itself must not be edited"

        # A cancelled search is not computed.
        before = len(scans)
        try:
            await register_cache.browse(params("closed tab"), gone)
            raise AssertionError("a gone client must not get a computed page")
        except HTTPException as exc:
            assert exc.status_code == 499
        assert len(scans) == before

        # After the TTL a search is computed again.
        with patch.object(register_cache, "BROWSE_TTL", 0):
            await register_cache.browse(params("bio"), present)
        assert scans.count("bio") == 2, scans


if __name__ == "__main__":
    asyncio.run(scenario())
    print("register cache: ok")
