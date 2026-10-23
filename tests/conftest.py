import os
import socket
from typing import NoReturn

import pytest
from hypothesis import settings

# Property-based tests explore random inputs locally ("dev"). In CI ("ci") the
# examples are derived from each test deterministically, so a failure can be
# reproduced by re-running the same commit. ``deadline=None`` avoids flaky
# timing failures on slow runners.
settings.register_profile("dev", max_examples=100, deadline=None)
settings.register_profile("ci", max_examples=200, deadline=None, derandomize=True)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "dev"))


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """NFR-1: any attempt to open a network connection fails the test."""

    def refuse(*_args: object, **_kwargs: object) -> NoReturn:
        raise RuntimeError("network access is not allowed (NFR-1)")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture(autouse=True)
def _no_forced_colors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Output is checked as it is when stdout is not a terminal (CLI-3).

    FORCE_COLOR, set in some developer shells, would make Rich add colors
    anyway. Typer reads it, and GITHUB_ACTIONS, once at import time for its
    own usage errors: the tests that read those messages strip the styles.
    """
    monkeypatch.delenv("FORCE_COLOR", raising=False)
