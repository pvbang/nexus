"""Password hashing utilities with versioned hash format + constant-time admin check.

Hash format: ``$mkv<N>$<bcrypt_hash>``.
- **v2** (current): ``bcrypt(b64(sha256(password)))`` — SHA-256 pre-hash avoids
  bcrypt's 72-byte silent-truncation limit so the full password contributes.

Verification auto-detects the version and falls back to bare bcrypt for hashes
without a prefix, so existing deployments upgrade transparently.

Ported/adapted from deer-flow ``backend/app/gateway/auth/password.py``.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets

import bcrypt

_PREFIX_V2 = "$mkv2$"


def _pre_hash_v2(password: str) -> bytes:
    """SHA-256 pre-hash to bypass bcrypt's 72-byte limit."""
    return base64.b64encode(hashlib.sha256(password.encode("utf-8")).digest())


def hash_password(password: str) -> str:
    """Hash a password (current version: v2 — SHA-256 + bcrypt)."""
    raw = bcrypt.hashpw(_pre_hash_v2(password), bcrypt.gensalt()).decode("utf-8")
    return f"{_PREFIX_V2}{raw}"


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash, auto-detecting the version.

    Accepts v2 (``$mkv2$…``) and bare bcrypt hashes (legacy). Fails closed on
    malformed input rather than raising.
    """
    if not hashed_password:
        return False
    try:
        if hashed_password.startswith(_PREFIX_V2):
            bcrypt_hash = hashed_password[len(_PREFIX_V2):]
            return bcrypt.checkpw(_pre_hash_v2(plain_password), bcrypt_hash.encode("utf-8"))
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except ValueError:
        return False


def needs_rehash(hashed_password: str) -> bool:
    """Return True if the stored value is not a current-version hash."""
    return not hashed_password.startswith(_PREFIX_V2)


def verify_admin_password(candidate: str, *, hashed: str, plaintext: str) -> bool:
    """Verify an admin login attempt, constant-time.

    Prefers a bcrypt hash (``ADMIN_PASSWORD_HASH``) when set; otherwise falls
    back to a constant-time comparison against legacy plaintext
    (``ADMIN_PASSWORD``). Returns False if neither is configured.
    """
    if hashed:
        return verify_password(candidate, hashed)
    if plaintext:
        return secrets.compare_digest(candidate.encode("utf-8"), plaintext.encode("utf-8"))
    return False


async def hash_password_async(password: str) -> str:
    """Hash a password off the event loop (bcrypt is CPU-blocking)."""
    return await asyncio.to_thread(hash_password, password)


async def verify_password_async(plain_password: str, hashed_password: str) -> bool:
    """Verify a password off the event loop."""
    return await asyncio.to_thread(verify_password, plain_password, hashed_password)
