"""Regression for the pytest-socket network guard (D1).

The suite runs with ``--disable-socket --allow-hosts=127.0.0.1,localhost`` (see
``pyproject.toml``). PostgreSQL and Redis remain reachable; every external host
must raise one of pytest-socket's blocked errors before a connection is made.
"""

from __future__ import annotations

import socket

import pytest
import pytest_socket


def test_external_connection_is_blocked():
    # ``--allow-hosts`` makes pytest-socket raise SocketConnectBlockedError on
    # connect(); a fully disabled socket raises SocketBlockedError instead.
    blocked = (
        pytest_socket.SocketBlockedError,
        pytest_socket.SocketConnectBlockedError,
    )

    with pytest.raises(blocked):
        socket.create_connection(("example.com", 443), timeout=1)
