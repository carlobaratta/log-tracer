import socket
from typing import NoReturn

import pytest


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """NFR-1: any attempt to open a network connection fails the test."""

    def refuse(*_args: object, **_kwargs: object) -> NoReturn:
        raise RuntimeError("network access is not allowed (NFR-1)")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
