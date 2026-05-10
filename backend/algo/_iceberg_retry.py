"""Retry helper for Iceberg writes against concurrent committers.

The v3 regime / factors / universe repos all do
``tbl.delete(predicate)`` + ``tbl.append(arrow_tbl)`` directly. When
the backfill (or any other writer) commits between the read and the
write, PyIceberg raises:

    CommitFailedException: Requirement failed: branch main has
    changed: expected id X, found Y

The fix is a retry loop with exponential backoff that reloads the
table fresh each attempt — matches the canonical
``StockRepository._retry_commit`` pattern from ``stocks/repository.py``.

Usage::

    from backend.algo._iceberg_retry import retry_iceberg_op

    def _do_upsert():
        tbl = catalog.load_table(TABLE)
        tbl.delete(predicate)
        tbl.append(arrow_tbl)

    retry_iceberg_op(TABLE, _do_upsert)

The wrapped callable is invoked up to 4 times total (1 initial + 3
retries) with 0.5/1.0/2.0s backoff.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, TypeVar

from pyiceberg.exceptions import CommitFailedException

_logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_BACKOFF_SECONDS = (0.5, 1.0, 2.0)
# Serializes commits across threads to avoid SQLite catalog
# conflicts when multiple v3 writers race in the same process.
_commit_lock = threading.Lock()

T = TypeVar("T")


def retry_iceberg_op(
    identifier: str,
    operation: Callable[[], T],
) -> T:
    """Run ``operation`` under a process-wide commit lock with
    retries on ``CommitFailedException``.

    The caller is responsible for loading the table fresh inside
    ``operation`` (so the retry reads the latest snapshot)."""
    with _commit_lock:
        last_exc: CommitFailedException | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                return operation()
            except CommitFailedException as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES:
                    delay = _BACKOFF_SECONDS[attempt]
                    _logger.warning(
                        "Iceberg commit conflict on %s "
                        "(attempt %d/%d), retry in %.1fs",
                        identifier,
                        attempt + 1,
                        _MAX_RETRIES,
                        delay,
                    )
                    time.sleep(delay)
        assert last_exc is not None
        raise last_exc
