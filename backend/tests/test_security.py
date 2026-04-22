from __future__ import annotations

import re

from backend.app.security import (
    hash_password,
    hash_session_token,
    looks_like_password_hash,
    verify_password,
    generate_session_token,
)


def test_password_hash_round_trip_and_format() -> None:
    password = "correct horse battery staple"

    password_hash = hash_password(password)

    assert looks_like_password_hash(password_hash)
    assert password_hash.startswith("pbkdf2_sha256$")
    assert verify_password(password, password_hash)
    assert not verify_password("wrong password", password_hash)


def test_verify_password_rejects_malformed_hash() -> None:
    assert not verify_password("password", "not-a-password-hash")


def test_session_token_generation_and_hashing_are_secret_bound() -> None:
    token_a = generate_session_token()
    token_b = generate_session_token()

    assert token_a != token_b
    assert re.fullmatch(r"[A-Za-z0-9_-]+", token_a)

    digest = hash_session_token(token_a, "secret-one")
    assert len(digest) == 64
    assert digest == hash_session_token(token_a, "secret-one")
    assert digest != hash_session_token(token_a, "secret-two")
