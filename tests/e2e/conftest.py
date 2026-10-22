from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _state_outside_the_repository(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """``analyze`` saves its report by default (STA-1).

    Every end-to-end test gets its own state directory, outside the working
    directory, so that tests never write into the repository or into the
    directory of logs they analyze. Tests about the default location remove
    the variable.
    """
    directory = tmp_path_factory.mktemp("state") / ".log-tracer"
    monkeypatch.setenv("LOG_TRACER_STATE_DIR", str(directory))
    return directory
