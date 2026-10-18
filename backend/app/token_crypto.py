"""Encrypt Gmail/Graph tokens at rest. Key is derived from JWT_SECRET."""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.jwt_utils import JWT_SECRET

_PREFIX = "enc:v1:"


def _fernet() -> Fernet:
    digest = hashlib.sha256(JWT_SECRET.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_token(value: str | None) -> str | None:
    if not value:
        return value
    if value.startswith(_PREFIX):
        return value
    return _PREFIX + _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_token(value: str | None) -> str | None:
    if not value:
        return value
    if not value.startswith(_PREFIX):
        return value
    try:
        return _fernet().decrypt(value[len(_PREFIX) :].encode("ascii")).decode("utf-8")
    except InvalidToken:
        return None
