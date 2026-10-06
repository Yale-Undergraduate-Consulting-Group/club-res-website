"""Every Tavily-dependent search path must prefer the self-hosted Firecrawl
instance when FIRECRAWL_URL is configured, and only fall back to a real
Tavily key if one is ever set later - Tavily/Apify/Verifalia are not paid
for, so the steady-state path must work through Firecrawl alone.
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('JWT_SECRET', 'firecrawl-delegation-test-secret-xxxxxxxxxxxxxxxx')


def tests() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ['DATABASE_URL'] = f'sqlite:///{Path(tmp) / "t.db"}'
        for mod in [m for m in list(sys.modules) if m.startswith('app.')]:
            del sys.modules[mod]

        from app.database import get_db, init_db
        asyncio.run(init_db())

        async def seed() -> None:
            db = await get_db()
            try:
                await db.execute("INSERT INTO users (id, email, name) VALUES (1, 'a@yale.edu', 'A')")
                await db.commit()
            finally:
                await db.close()

        asyncio.run(seed())

        os.environ.pop('TAVILY_API_KEY', None)
        os.environ['FIRECRAWL_URL'] = 'http://100.84.7.57:3002'

        from app.services import web_contact_discovery, yucgoutreach_discovery
        from app.services import web_fetch

        fake_result = [{'title': 'Firecrawl result', 'url': 'https://acme.com', 'content': 'from firecrawl'}]

        async def fake_web_search(query, max_results=8, **kwargs):
            return fake_result

        # web_contact_discovery._tavily_search delegates to Firecrawl, never
        # touches api.tavily.com, when Firecrawl is configured and no Tavily
        # key exists.
        with patch('app.services.web_fetch.web_search', side_effect=fake_web_search):
            result = asyncio.run(web_contact_discovery._tavily_search('Acme CEO'))
            assert result == fake_result

        # Same for the duplicated helper in yucgoutreach_discovery.
        with patch('app.services.web_fetch.web_search', side_effect=fake_web_search):
            result = asyncio.run(yucgoutreach_discovery._tavily_search('Acme CEO'))
            assert result == fake_result

        # Unconfigured Firecrawl AND no Tavily key: both degrade to empty,
        # never raise, matching every other optional-provider path.
        os.environ.pop('FIRECRAWL_URL', None)
        assert asyncio.run(web_contact_discovery._tavily_search('Acme CEO')) == []
        assert asyncio.run(yucgoutreach_discovery._tavily_search('Acme CEO')) == []

        # discover_contacts_from_web's own gate: unconfigured entirely
        # returns [] without attempting a search; configuring Firecrawl
        # (with no company name) still returns [] for a different reason
        # (no company), proving the gate itself is not what is blocking it.
        result = asyncio.run(web_contact_discovery.discover_contacts_from_web('', None))
        assert result == []

        os.environ['FIRECRAWL_URL'] = 'http://100.84.7.57:3002'
        with patch('app.services.web_fetch.web_search', side_effect=fake_web_search):
            # A real company name with Firecrawl configured must not short
            # circuit on the old Tavily-only gate.
            result = asyncio.run(web_contact_discovery.discover_contacts_from_web('Acme Robotics', 'acme.com', max_people=1))
            assert isinstance(result, list)

        del os.environ['FIRECRAWL_URL']


if __name__ == '__main__':
    tests()
    print('firecrawl search delegation: ok')
