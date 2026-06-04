from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

INSECURE_DEFAULT_SECRET_SEED = "change-me-local-dev-secret"


def secret_seed_is_insecure(seed: str) -> bool:
    return not seed.strip() or seed.strip() == INSECURE_DEFAULT_SECRET_SEED


def _fernet(seed: str) -> Fernet:
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str, seed: str) -> str:
    if not value:
        return ""
    return _fernet(seed).encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str, seed: str) -> str:
    if not value:
        return ""
    try:
        return _fernet(seed).decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return ""
