"""Credential encryption for connectors (T130).

Connector credentials are Fernet-encrypted with a key derived from
``settings.CONNECTOR_KEY``. The ciphertext lives in ``ConnectorConfig.credentials_enc``
and is *never* returned by a serializer: the API exposes only a boolean
``has_credentials`` flag.

Key policy
----------
``CONNECTOR_KEY`` must be a urlsafe-base64 32-byte Fernet key. In development and
tests it is empty, so a deterministic key is derived from ``SECRET_KEY`` (this is
explicitly not a production key: ``config.settings.prod`` should require
``CONNECTOR_KEY``). A malformed key fails closed with ``CredentialKeyError``
rather than silently encrypting under a weak key.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


class CredentialError(RuntimeError):
    """Base class for credential handling failures."""


class CredentialKeyError(CredentialError):
    """The configured ``CONNECTOR_KEY`` is not a valid Fernet key."""


class CredentialDecryptError(CredentialError):
    """The stored ciphertext could not be decrypted with the active key."""


def _derive_key(seed: str) -> bytes:
    return base64.urlsafe_b64encode(hashlib.sha256(seed.encode("utf-8")).digest())


def connector_key() -> bytes:
    """Return the active Fernet key (validated)."""

    configured = str(getattr(settings, "CONNECTOR_KEY", "") or "").strip()
    if configured:
        key = configured.encode("utf-8")
        try:
            Fernet(key)
        except (ValueError, TypeError) as exc:  # pragma: no cover - config error
            raise CredentialKeyError(
                "CONNECTOR_KEY must be a urlsafe-base64 32-byte Fernet key."
            ) from exc
        return key
    seed = str(getattr(settings, "SECRET_KEY", "") or "enamel-dev-connector-key")
    return _derive_key(f"enamel-connector-key:{seed}")


def _fernet() -> Fernet:
    return Fernet(connector_key())


def encrypt_credentials(data: dict[str, Any] | None) -> str:
    """Return the Fernet ciphertext of ``data`` (empty input -> empty string)."""

    if not data:
        return ""
    payload = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _fernet().encrypt(payload).decode("ascii")


def decrypt_credentials(token: str | None) -> dict[str, Any]:
    """Decrypt ``token`` into a dict; raise ``CredentialDecryptError`` on failure."""

    if not token:
        return {}
    try:
        raw = _fernet().decrypt(str(token).encode("ascii"))
    except (InvalidToken, ValueError, TypeError) as exc:
        raise CredentialDecryptError("Stored connector credentials are unreadable.") from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise CredentialDecryptError("Stored connector credentials are malformed.") from exc
    if not isinstance(data, dict):
        raise CredentialDecryptError("Stored connector credentials are malformed.")
    return data


def has_credentials(config: Any) -> bool:
    """True when the connector has a stored credential blob."""

    return bool(str(getattr(config, "credentials_enc", "") or "").strip())


__all__ = [
    "CredentialDecryptError",
    "CredentialError",
    "CredentialKeyError",
    "connector_key",
    "decrypt_credentials",
    "encrypt_credentials",
    "has_credentials",
]
