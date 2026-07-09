"""Tests for bcrypt password hashing with versioned format + plaintext migration."""

from app.core.password import (
    hash_password,
    verify_password,
    needs_rehash,
    verify_admin_password,
)


def test_hash_roundtrip():
    h = hash_password("s3cret-pw")
    assert h.startswith("$mkv2$")
    assert verify_password("s3cret-pw", h) is True
    assert verify_password("wrong", h) is False


def test_hash_over_72_bytes_uses_full_password():
    # bcrypt silently truncates at 72 bytes; SHA-256 pre-hash must avoid that.
    base = "A" * 72
    h = hash_password(base + "DIFFERENT_TAIL")
    assert verify_password(base + "DIFFERENT_TAIL", h) is True
    assert verify_password(base + "OTHER_TAIL_XXXX", h) is False


def test_needs_rehash():
    assert needs_rehash("plaintext") is True
    assert needs_rehash(hash_password("x")) is False


def test_malformed_hash_fails_closed():
    assert verify_password("anything", "$mkv2$not-a-real-bcrypt-hash") is False


def test_verify_admin_password_constant_time_hashed():
    h = hash_password("admin-pw")
    assert verify_admin_password("admin-pw", hashed=h, plaintext="") is True
    assert verify_admin_password("nope", hashed=h, plaintext="") is False


def test_verify_admin_password_plaintext_fallback():
    # Legacy deployments set ADMIN_PASSWORD plaintext; must still work, constant-time.
    assert verify_admin_password("legacy", hashed="", plaintext="legacy") is True
    assert verify_admin_password("nope", hashed="", plaintext="legacy") is False


def test_verify_admin_password_prefers_hash_when_both_set():
    h = hash_password("hashed-pw")
    assert verify_admin_password("hashed-pw", hashed=h, plaintext="plain-pw") is True
    assert verify_admin_password("plain-pw", hashed=h, plaintext="plain-pw") is False
