"""Per-run / per-session buffer for
``stocks.trade_feature_snapshots`` rows (ASETPLTFRM-417 / FE-5.1).

Background
==========
FE-5 originally wrote one Iceberg commit per fill, which produces
~2 manifest files per commit. A single 7,000-fill backtest in
production blew the table to 14,000 manifest avros / ~9.4 GB on
disk for 5,096 logical rows, and orphan-sweep took 17+ minutes
walking the manifest tree.

This module buffers ``FillSnapshotRow`` rows in-process per
``(strategy_id, run_or_session_id)`` key and emits ONE Iceberg
commit at run / session end via
:func:`write_trade_feature_snapshots_batch`. Manifest count drops
from 14,000 to ~2 per backtest. Used by the backtest runner and
paper runtime; live runtime uses a Redis LIST instead (see
``backend.algo.features.snapshots._push_live_snapshot_to_redis``)
because live sessions can run for hours / days and an unbounded
in-process buffer is unsafe under uvicorn.

Failure semantics
=================
Flush failures are caught + logged with ``exc_info=True``; buffer
is NOT re-stocked on failure (the spec accepts bounded snapshot
loss per strategy-run because the fill itself + the
``algo.events`` ledger row are already durable).

Thread-safety
=============
Backtest runner may spawn worker threads in some cadences;
``threading.RLock`` guards every mutation. The buffer surface is
sync — paper runtime is async but the hot path stays out of the
event loop because :func:`SnapshotsBuffer.add` is a constant-time
in-memory append.

Singleton pattern
=================
Per CLAUDE.md §4.2 #12 (no mutable module-level globals), the
buffer is instantiated lazily inside :func:`get_buffer` and
cached on the function attribute. Cross-test isolation is
handled by :func:`reset_buffer` which the test fixtures call.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

_logger = logging.getLogger(__name__)


@dataclass
class FillSnapshotRow:
    """One buffered fill — flat shape matching the Iceberg
    schema columns ``write_trade_feature_snapshots_batch``
    expects. Values are stored as their natural Python types
    (Decimal, int, str, dict); type-coercion to Arrow happens
    inside the batch writer.
    """

    fill_id: str
    run_id: str
    strategy_id: str
    ticker: str
    side: str
    qty: int
    fill_price: Decimal
    fill_ts_ns: int | None
    bar_date: str
    mode: str
    features: dict[str, Any] | None = field(default=None)


_BufferKey = tuple[str, str]


class SnapshotsBuffer:
    """In-process append + flush buffer keyed by
    ``(strategy_id, run_or_session_id)``.

    Every public method is thread-safe via a single ``RLock``.
    Re-entrancy matters because :meth:`flush` calls the Iceberg
    writer which (under retry) may call back into logging code
    that also touches the singleton in test environments.
    """

    def __init__(self) -> None:
        self._lock: threading.RLock = threading.RLock()
        self._buf: dict[_BufferKey, list[FillSnapshotRow]] = {}

    def add(
        self,
        row: FillSnapshotRow,
        *,
        key: _BufferKey,
    ) -> None:
        """Append one row under ``key``. O(1) amortised."""
        with self._lock:
            self._buf.setdefault(key, []).append(row)

    def flush(self, key: _BufferKey) -> int:
        """Drain rows for ``key`` and write to Iceberg in ONE
        commit. Returns row count written.

        Failure isolation per FE-5 contract:

        * batch write raises -> log ``exc_info=True``, buffer
          IS still cleared (snapshot loss is bounded to this
          strategy-run; the alternative — leaving rows in the
          buffer indefinitely — leaks memory and risks emitting
          the same rows again on a re-flush attempt).
        * empty bucket -> no-op, returns 0.
        """
        with self._lock:
            rows = self._buf.pop(key, [])
        if not rows:
            return 0
        # Defer the import so the buffer module is safe to import
        # at process start (avoids circular import between the
        # snapshot dispatcher and the buffer).
        from backend.algo.features.snapshots import (
            write_trade_feature_snapshots_batch,
        )

        try:
            written = write_trade_feature_snapshots_batch(rows)
        except Exception:
            _logger.exception(
                "snapshots buffer flush failed for "
                "strategy_id=%s run_id=%s (rows=%d); "
                "rows dropped (non-fatal)",
                key[0],
                key[1],
                len(rows),
            )
            return 0
        return int(written)

    def flush_all(self) -> dict[_BufferKey, int]:
        """Drain every key in the buffer. Returns per-key
        rows-written counts. Used at process shutdown and by
        tests."""
        with self._lock:
            keys = list(self._buf.keys())
        out: dict[_BufferKey, int] = {}
        for k in keys:
            out[k] = self.flush(k)
        return out

    def clear(self, key: _BufferKey) -> None:
        """Drop buffered rows for ``key`` without writing.
        Intended for test cleanup."""
        with self._lock:
            self._buf.pop(key, None)

    def pending(self, key: _BufferKey) -> int:
        """Return the number of rows buffered for ``key``.
        Exposed for tests + observability."""
        with self._lock:
            return len(self._buf.get(key, []))

    def __len__(self) -> int:
        """Total rows across every key. Test affordance."""
        with self._lock:
            return sum(len(v) for v in self._buf.values())


def get_buffer() -> SnapshotsBuffer:
    """Return the process-wide :class:`SnapshotsBuffer`
    singleton. The first call lazily instantiates; subsequent
    calls return the same instance.

    Implemented as a function attribute (not a module-level
    global) per CLAUDE.md §4.2 #12. Tests can override via
    :func:`reset_buffer`.
    """
    inst = getattr(get_buffer, "_inst", None)
    if inst is None:
        inst = SnapshotsBuffer()
        get_buffer._inst = inst  # type: ignore[attr-defined]
    return inst


def reset_buffer() -> None:
    """Test-only: drop the singleton so the next
    :func:`get_buffer` returns a fresh instance. Not called in
    production code paths."""
    if hasattr(get_buffer, "_inst"):
        delattr(get_buffer, "_inst")
