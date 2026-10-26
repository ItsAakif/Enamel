"""TOTP multi-factor authentication helpers.

Secrets are encrypted at rest with Fernet. The key is derived from
``SECRET_KEY`` with SHA-256 so no additional secret is required, while the
database never stores plaintext TOTP seeds.
"""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

import pyotp
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _fernet_key() -> bytes:
    digest = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


@lru_cache(maxsize=4)
def _fernet_for(key_material: str) -> Fernet:
    return Fernet(_fernet_key())


def _fernet() -> Fernet:
    # Keyed by SECRET_KEY so tests that override it get a fresh cipher.
    return _fernet_for(settings.SECRET_KEY)


def encrypt_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode("utf-8")).decode("ascii")


def decrypt_secret(encrypted: str) -> str:
    if not encrypted:
        raise InvalidToken("missing MFA secret")
    return _fernet().decrypt(encrypted.encode("ascii")).decode("utf-8")


def generate_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, *, account_name: str) -> str:
    issuer = getattr(settings, "MFA_ISSUER", "Enamel")
    return pyotp.TOTP(secret).provisioning_uri(name=account_name, issuer_name=issuer)


def verify_totp(secret: str, otp: str) -> bool:
    if not secret or not otp:
        return False
    window = int(getattr(settings, "MFA_TOTP_VALID_WINDOW", 1))
    return bool(pyotp.TOTP(secret).verify(str(otp).strip(), valid_window=window))
