import logging
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _reset_ammirror_logger() -> Iterator[None]:
    """The CLI configures the `ammirror` logger once per invocation; undo it per test."""
    yield
    logger = logging.getLogger("ammirror")
    for h in list(logger.handlers):
        logger.removeHandler(h)
    logger.setLevel(logging.NOTSET)
    logger.propagate = True
    httpx_logger = logging.getLogger("httpx")
    for h in list(httpx_logger.handlers):
        httpx_logger.removeHandler(h)
    httpx_logger.setLevel(logging.NOTSET)
    httpx_logger.propagate = True


@pytest.fixture(autouse=True)
def _no_like_verify_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """run_sync waits before re-reading likes; tests that don't inject a sleep must not."""
    monkeypatch.setattr("ammirror.sync.time.sleep", lambda _s: None)
