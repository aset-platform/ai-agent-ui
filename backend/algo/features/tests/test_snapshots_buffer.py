"""FE-5.1 — :class:`SnapshotsBuffer` unit tests
(ASETPLTFRM-417).

Covers the in-process buffer used by the backtest runner and
paper runtime. Live mode bypasses this buffer and goes to
Redis (covered by ``test_snapshots_dispatcher.py``).
"""

from __future__ import annotations

import threading
from decimal import Decimal
from unittest.mock import patch

import pytest

from backend.algo.features.snapshots_buffer import (
    FillSnapshotRow,
    SnapshotsBuffer,
    get_buffer,
    reset_buffer,
)


def _row(*, fill_id: str, ticker: str = "FAKE.NS") -> FillSnapshotRow:
    return FillSnapshotRow(
        fill_id=fill_id,
        run_id="run-1",
        strategy_id="strat-1",
        ticker=ticker,
        side="BUY",
        qty=1,
        fill_price=Decimal("100.00"),
        fill_ts_ns=None,
        bar_date="2026-05-15",
        mode="backtest",
        features={"rsi_14": Decimal("55.0")},
    )


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Each test starts with a fresh singleton so cross-test
    pollution can't mask a regression."""
    reset_buffer()
    yield
    reset_buffer()


def test_add_groups_by_key():
    buf = SnapshotsBuffer()
    k1 = ("strat-A", "run-A")
    k2 = ("strat-A", "run-B")
    buf.add(_row(fill_id="f1"), key=k1)
    buf.add(_row(fill_id="f2"), key=k1)
    buf.add(_row(fill_id="f3"), key=k2)
    assert buf.pending(k1) == 2
    assert buf.pending(k2) == 1
    assert len(buf) == 3


def test_flush_returns_rows_written_count():
    buf = SnapshotsBuffer()
    k = ("strat-A", "run-A")
    buf.add(_row(fill_id="f1"), key=k)
    buf.add(_row(fill_id="f2"), key=k)
    with patch(
        "backend.algo.features.snapshots."
        "write_trade_feature_snapshots_batch",
        return_value=2,
    ) as mock_writer:
        written = buf.flush(k)
    assert written == 2
    assert mock_writer.call_count == 1
    # Single call with both rows -> one Iceberg commit.
    call_rows = mock_writer.call_args[0][0]
    assert len(call_rows) == 2


def test_flush_clears_buffer_for_key():
    buf = SnapshotsBuffer()
    k = ("strat-A", "run-A")
    buf.add(_row(fill_id="f1"), key=k)
    with patch(
        "backend.algo.features.snapshots."
        "write_trade_feature_snapshots_batch",
        return_value=1,
    ):
        buf.flush(k)
    assert buf.pending(k) == 0
    assert len(buf) == 0


def test_flush_empty_key_is_noop():
    buf = SnapshotsBuffer()
    # Should not raise and should not call the writer.
    with patch(
        "backend.algo.features.snapshots."
        "write_trade_feature_snapshots_batch",
    ) as mock_writer:
        written = buf.flush(("nope", "nope"))
    assert written == 0
    assert mock_writer.call_count == 0


def test_flush_all_drains_every_key():
    buf = SnapshotsBuffer()
    k1 = ("strat-A", "run-A")
    k2 = ("strat-B", "run-B")
    buf.add(_row(fill_id="f1"), key=k1)
    buf.add(_row(fill_id="f2"), key=k2)
    buf.add(_row(fill_id="f3"), key=k2)
    with patch(
        "backend.algo.features.snapshots."
        "write_trade_feature_snapshots_batch",
        side_effect=lambda rows: len(rows),
    ) as mock_writer:
        results = buf.flush_all()
    assert results[k1] == 1
    assert results[k2] == 2
    # Two distinct keys -> two Iceberg commits (NOT three).
    assert mock_writer.call_count == 2
    assert len(buf) == 0


def test_flush_failure_does_not_re_buffer():
    buf = SnapshotsBuffer()
    k = ("strat-A", "run-A")
    buf.add(_row(fill_id="f1"), key=k)
    buf.add(_row(fill_id="f2"), key=k)
    with patch(
        "backend.algo.features.snapshots."
        "write_trade_feature_snapshots_batch",
        side_effect=RuntimeError("simulated iceberg outage"),
    ):
        # Must NOT raise (FE-5 contract: snapshot loss is OK,
        # fill ledger is durable elsewhere).
        written = buf.flush(k)
    assert written == 0
    # Buffer is cleared even on failure (bounded loss).
    assert buf.pending(k) == 0


def test_thread_safe_concurrent_add():
    buf = SnapshotsBuffer()
    k = ("strat-A", "run-A")

    def _worker(start: int) -> None:
        for i in range(100):
            buf.add(_row(fill_id=f"f-{start}-{i}"), key=k)

    threads = [threading.Thread(target=_worker, args=(t,)) for t in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert buf.pending(k) == 1000


def test_singleton_pattern():
    a = get_buffer()
    b = get_buffer()
    assert a is b
    # And reset_buffer breaks the identity (new instance after).
    reset_buffer()
    c = get_buffer()
    assert c is not a


def test_clear_drops_without_writing():
    buf = SnapshotsBuffer()
    k = ("strat-A", "run-A")
    buf.add(_row(fill_id="f1"), key=k)
    with patch(
        "backend.algo.features.snapshots."
        "write_trade_feature_snapshots_batch",
    ) as mock_writer:
        buf.clear(k)
    assert buf.pending(k) == 0
    # clear() never writes.
    assert mock_writer.call_count == 0
