"""Best-effort company domain guessing for the Find-people flow: a name like
'Meta Platforms, Inc.' guesses metaplatforms.com, but the guess is only ever
handed back once it's confirmed to actually answer - never fabricated.
"""
import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('JWT_SECRET', 'domain-guess-secret-xxxxxxxxxxxxxxxxxx')

from app.services import company_email_cache as cec


def legal_suffixes_strip_to_the_exact_domain() -> None:
    # Comma, period, and a legal suffix all fall away the same way.
    assert cec.guess_domain_from_name('Meta Platforms, Inc.') == 'metaplatforms.com'
    assert cec.guess_domain_from_name('Acme Corporation') == 'acme.com'
    assert cec.guess_domain_from_name('Blue Sky Group LLC') == 'bluesky.com'
    assert cec.guess_domain_from_name('   ') is None


def resolving_guess_comes_back_verified() -> None:
    async def fake_head_ok(url: str) -> bool:
        return url == 'https://metaplatforms.com'

    with patch.object(cec, '_head_ok', fake_head_ok):
        result = asyncio.run(cec.verify_domain_guess('Meta Platforms, Inc.'))
    assert result == {'domain': 'metaplatforms.com', 'verified': True}, result


def nonresolving_guess_never_gets_fabricated() -> None:
    async def fake_head_ok(url: str) -> bool:
        return False

    with patch.object(cec, '_head_ok', fake_head_ok):
        result = asyncio.run(cec.verify_domain_guess('Totally Nonexistent Widgets Inc'))
    assert result == {'domain': None, 'verified': False}, result

    # A timeout/connection error must also decline rather than raise.
    async def fake_head_timeout(url: str) -> bool:
        raise TimeoutError('no response')

    with patch.object(cec, '_head_ok', fake_head_timeout):
        result = asyncio.run(cec.verify_domain_guess('Meta Platforms, Inc.'))
    assert result == {'domain': None, 'verified': False}, result


if __name__ == '__main__':
    legal_suffixes_strip_to_the_exact_domain()
    resolving_guess_comes_back_verified()
    nonresolving_guess_never_gets_fabricated()
    print('domain guess: ok')
