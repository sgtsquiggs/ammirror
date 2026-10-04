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
    logging.getLogger("httpx").setLevel(logging.NOTSET)
