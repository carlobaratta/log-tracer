import socket

import pytest


def test_network_access_is_blocked_during_tests() -> None:  # NFR-1
    with pytest.raises(RuntimeError, match="NFR-1"):
        socket.create_connection(("example.com", 80), timeout=1)
