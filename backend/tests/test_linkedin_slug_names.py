"""LinkedIn appends a short auto-generated de-duplication suffix to a
profile slug when the readable one is already taken - either as its own
hyphenated segment (don-gross-25b76b8) or fused onto the last word with no
separator (rachel-hutter60). Both showed up as garbled contact names
(e.g. "Rachel Hutter60", "Don Gross 25b76b8") once real search results
started flowing through the TinyFish/Monid backend - this was previously
masked by Firecrawl's self-hosted search always returning zero results.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('JWT_SECRET', 'linkedin-slug-secret-xxxxxxxxxxxxxxxx')

from app.services.web_contact_discovery import _slug_to_name


def tests() -> None:
    # Real slugs observed live against The Walt Disney Company Studios.
    assert _slug_to_name('don-gross-25b76b8') == 'Don Gross'
    assert _slug_to_name('rachel-hutter60') == 'Rachel Hutter'
    assert _slug_to_name('allison-erlikhman-5298b61a') == 'Allison Erlikhman'
    assert _slug_to_name('nicole-silveira-804a7017') == 'Nicole Silveira'
    assert _slug_to_name('richard-hill-75861a1b') == 'Richard Hill'
    assert _slug_to_name('vijay-subramaniam-11b73a7a') == 'Vijay Subramaniam'

    # A clean slug with no dedup suffix is untouched.
    assert _slug_to_name('john-smith') == 'John Smith'

    # A purely numeric or too-short slug still yields nothing usable.
    assert _slug_to_name('123456') is None
    assert _slug_to_name('a-1') is None
    assert _slug_to_name('') is None

    # A real surname that happens to be all hex letters is not stripped -
    # only a segment containing at least one digit is treated as a
    # machine-generated suffix.
    assert _slug_to_name('john-cafe') == 'John Cafe'


if __name__ == '__main__':
    tests()
    print('linkedin slug names: ok')
