"""AES-GCM encryption for Google refresh tokens. Format: "v1:" + base64url(nonce12 + ciphertext)."""

import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VERSION = "v1:"
NONCE_SIZE = 12


class TokenDecryptError(Exception):
    pass


def _resolve_key(key: bytes | None) -> bytes:
    if key is None:
        from app.config import get_settings

        raw = get_settings().google_token_key
        if not raw:
            raise TokenDecryptError("GOOGLE_TOKEN_KEY is not configured")
        key = base64.b64decode(raw)
    return key


def encrypt_token(plain: str, *, key: bytes | None = None) -> str:
    nonce = os.urandom(NONCE_SIZE)
    ct = AESGCM(_resolve_key(key)).encrypt(nonce, plain.encode("utf-8"), None)
    return VERSION + base64.urlsafe_b64encode(nonce + ct).decode("ascii")


def decrypt_token(blob: str, *, key: bytes | None = None) -> str:
    try:
        if not blob.startswith(VERSION):
            raise ValueError("unknown token format")
        data = base64.urlsafe_b64decode(blob[len(VERSION):].encode("ascii"))
        if len(data) <= NONCE_SIZE:
            raise ValueError("token too short")
        return AESGCM(_resolve_key(key)).decrypt(data[:NONCE_SIZE], data[NONCE_SIZE:], None).decode("utf-8")
    except TokenDecryptError:
        raise
    except (ValueError, InvalidTag, binascii.Error, UnicodeError) as e:
        raise TokenDecryptError("cannot decrypt token") from e
