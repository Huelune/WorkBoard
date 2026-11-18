"""Password hashing for local accounts.

The check runs on the user's own PC against a local file, so it stops a colleague at
the keyboard, not someone who edits the database file. Passwords are never stored or
logged; only a salted scrypt hash is kept.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from workboard.domain.errors import ValidationError

MIN_LENGTH = 8
_N, _R, _P, _LENGTH = 2**14, 8, 1, 32


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int, length: int) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=length)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = _scrypt(password, salt, _N, _R, _P, _LENGTH)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """True only for the right password; a damaged or unknown hash format never verifies."""
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = bytes.fromhex(digest)
        candidate = _scrypt(password, bytes.fromhex(salt), int(n), int(r), int(p), len(expected))
    except (AttributeError, TypeError, ValueError):
        return False
    return hmac.compare_digest(candidate, expected)


def validate_new_password(user_id: str, password: object) -> str:
    if not isinstance(password, str) or len(password.strip()) < MIN_LENGTH:
        raise ValidationError(f"password must be at least {MIN_LENGTH} characters")
    if password == user_id:
        raise ValidationError("password must differ from the ID")
    return password


# Checked when the ID is unknown, so a wrong ID takes as long as a wrong password.
DUMMY_HASH = hash_password(secrets.token_hex(16))
