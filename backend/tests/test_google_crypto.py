import base64
import os

import pytest

from app.config import Settings
from app.google.crypto import TokenDecryptError, decrypt_token, encrypt_token


def _key() -> bytes:
    return os.urandom(32)


def test_roundtrip():
    key = _key()
    blob = encrypt_token("1//refresh-abc", key=key)
    assert blob.startswith("v1:")
    assert "refresh-abc" not in blob
    assert decrypt_token(blob, key=key) == "1//refresh-abc"


def test_ciphertext_differs_each_time():
    key = _key()
    assert encrypt_token("same", key=key) != encrypt_token("same", key=key)


def test_tampered_blob_raises():
    key = _key()
    blob = encrypt_token("1//refresh-abc", key=key)
    tampered = blob[:-1] + ("A" if blob[-1] != "A" else "B")
    with pytest.raises(TokenDecryptError):
        decrypt_token(tampered, key=key)


def test_wrong_key_raises():
    blob = encrypt_token("1//refresh-abc", key=_key())
    with pytest.raises(TokenDecryptError):
        decrypt_token(blob, key=_key())


def test_google_disabled_without_key():
    s = Settings(_env_file=None, google_client_id="id", google_client_secret="secret", google_token_key="")
    assert s.google_enabled is False
    k = base64.b64encode(_key()).decode()
    s2 = Settings(_env_file=None, google_client_id="id", google_client_secret="secret", google_token_key=k)
    assert s2.google_enabled is True
    assert s2.google_redirect_uri == s2.public_url.rstrip("/") + "/api/google/callback"


def test_invalid_key_length_rejected():
    short = base64.b64encode(os.urandom(16)).decode()
    with pytest.raises(ValueError):
        Settings(_env_file=None, google_token_key=short)
