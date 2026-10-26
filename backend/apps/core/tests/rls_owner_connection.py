"""The boundary proofs need a connection that may switch roles (T138).

The boundary proofs in ``apps/core/tests/test_rls*.py`` switch the
connection to the **real runtime roles** (``enamel_app``, ``enamel_portal``,
``enamel_maint``, ...) so that the real policies are exercised in force.
``SET ROLE`` to another role requires membership in it (or the superuser
attribute), so the proofs need a connection that may switch.

In the ordinary run the test connection **is** the compose owner, and the
proofs switch away from it and back. When the suite runs under the test
fixture role (``enamel_test``, see ``rls_test_role.py``) the connection may
not switch, so this fixture reconnects it with the **owner's** credentials
for the duration of the module and restores it afterwards. The owner URL
comes from ``ENAMEL_OWNER_DATABASE_URL``; the database itself is unchanged
(the test database), only the credentials are.

The fixture never skips: without an owner URL the module fails loudly with
the reason.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

import pytest
from django.db import connection

OWNER_URL_ENV = "ENAMEL_OWNER_DATABASE_URL"

CREDENTIAL_KEYS = ("HOST", "PORT", "USER", "PASSWORD")


def owner_credentials() -> dict[str, object]:
    """The owner's credentials, or a loud failure."""

    raw = os.environ.get(OWNER_URL_ENV, "").strip()
    if not raw:
        pytest.fail(
            f"{OWNER_URL_ENV} is not set: the boundary proofs need a connection "
            "that may switch to the runtime roles, and the test connection "
            "(the test fixture role) may not switch."
        )
    parsed = urlparse(raw)
    if not parsed.username:
        pytest.fail(f"{OWNER_URL_ENV} does not carry a user name.")
    return {
        "HOST": parsed.hostname or "127.0.0.1",
        "PORT": parsed.port or 5432,
        "USER": parsed.username,
        "PASSWORD": parsed.password or "",
    }


def connection_is_superuser() -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
        row = cursor.fetchone()
    return bool(row and row[0])


@pytest.fixture(scope="module", autouse=True)
def owner_connection(django_db_setup, django_db_blocker):
    """Make sure the proofs may switch to the runtime roles."""

    with django_db_blocker.unblock():
        if connection_is_superuser():
            # The compose owner: the proofs switch away from it and back.
            yield
            return

        original = {key: connection.settings_dict.get(key) for key in CREDENTIAL_KEYS}
        credentials = owner_credentials()
        connection.close()
        connection.settings_dict.update(credentials)
        connection.connect()
        try:
            yield
        finally:
            connection.close()
            connection.settings_dict.update(original)
            connection.connect()


__all__ = ["CREDENTIAL_KEYS", "OWNER_URL_ENV", "connection_is_superuser", "owner_connection"]
