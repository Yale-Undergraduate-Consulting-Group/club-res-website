"""A tracked contact denotes exactly one bare mailbox, never a recipient list."""
from email.headerregistry import Address


def validate_header(value: str) -> None:
    if any(ord(char)<32 or ord(char)==127 for char in value):
        raise ValueError('Email headers cannot contain control characters')


def validate_recipient(value: str) -> None:
    if not value or value!=value.strip() or any(ord(char)<32 or ord(char)==127 for char in value):
        raise ValueError('Recipient must be one email address without control characters')
    try:
        parsed=Address(addr_spec=value)
    except (ValueError,IndexError) as exc:
        raise ValueError('Recipient must be one bare email address') from exc
    if not parsed.username or not parsed.domain:
        raise ValueError('Recipient must include a mailbox and domain')
