"""Password hashing with Argon2id (data-protection §3)."""

from __future__ import annotations

import secrets

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError

MIN_LENGTH = 12
MAX_LENGTH = 256  # bounds the hashing work one request can ask for

# argon2-cffi's defaults (RFC 9106 low-memory profile) with Argon2id. Tests swap in cheaper
# parameters; the type stays Argon2id.
HASHER = PasswordHasher(type=Type.ID)


def hash_password(password: str) -> str:
    return HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return HASHER.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return HASHER.check_needs_rehash(password_hash)


_dummy_hash: dict[str, str] = {}


def burn_verify(password: str) -> None:
    """Verify against a throwaway hash so an unknown email takes as long as a wrong password."""
    if "h" not in _dummy_hash:
        _dummy_hash["h"] = HASHER.hash(secrets.token_urlsafe(16))
    verify_password(_dummy_hash["h"], password)


def password_problem(password: str) -> str | None:
    if len(password) < MIN_LENGTH:
        return f"Use at least {MIN_LENGTH} characters."
    if len(password) > MAX_LENGTH:
        return f"Use at most {MAX_LENGTH} characters."
    return None


def generate_password() -> str:
    """A random password for a new account (96 bits)."""
    return secrets.token_urlsafe(12)
