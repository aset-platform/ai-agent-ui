"""FE-5.1 — :func:`write_trade_feature_snapshot` dispatcher
unit tests (ASETPLTFRM-417).

Validates that the dispatcher routes correctly by ``mode``:

* ``backtest`` / ``paper`` -> in-process buffer (no Iceberg, no
  Redis push at call time).
* ``live`` -> Redis LIST push (no buffer, no Iceberg).
* Unknown mode -> log + drop.
* Redis outage on live -> swallowed (no exception, no fill
  block).
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from backend.algo.features import snapshots as snap_mod
from backend.algo.features.snapshots import (
    write_trade_feature_snapshot,
)
from backend.algo.features.snapshots_buffer import (
    get_buffer,
    reset_buffer,
)


_BASE_KW = dict(
    fill_id="f1",
    run_id="run-1",
    strategy_id="strat-1",
    ticker="FAKE.NS",
    side="BUY",
    qty=1,
    fill_price=Decimal("100.00"),
    fill_ts_ns=None,
    bar_date="2026-05-15",
    features={"rsi_14": Decimal("55.0")},
)


@pytest.fixture(autouse=True)
def _reset():
    reset_buffer()
    yield
    reset_buffer()


def test_dispatcher_adds_backtest_to_buffer():
    with patch.object(snap_mod, "_push_live_snapshot_to_redis") as red:
        write_trade_feature_snapshot(mode="backtest", **_BASE_KW)
    buf = get_buffer()
    assert buf.pending(("strat-1", "run-1")) == 1
    # Redis path NOT touched for backtest mode.
    assert red.call_count == 0


def test_dispatcher_adds_paper_to_buffer():
    with patch.object(snap_mod, "_push_live_snapshot_to_redis") as red:
        write_trade_feature_snapshot(mode="paper", **_BASE_KW)
    buf = get_buffer()
    assert buf.pending(("strat-1", "run-1")) == 1
    assert red.call_count == 0


def test_dispatcher_pushes_live_to_redis():
    with patch.object(snap_mod, "_push_live_snapshot_to_redis") as red:
        write_trade_feature_snapshot(
            mode="live",
            user_id="user-123",
            **_BASE_KW,
        )
    buf = get_buffer()
    # Buffer NOT touched for live mode.
    assert len(buf) == 0
    assert red.call_count == 1
    # user_id propagated through.
    kw = red.call_args.kwargs
    assert kw["user_id"] == "user-123"
    assert kw["row"].fill_id == "f1"


def test_dispatcher_drops_unknown_mode_with_log(caplog):
    with patch.object(snap_mod, "_push_live_snapshot_to_redis") as red:
        with caplog.at_level("WARNING"):
            write_trade_feature_snapshot(mode="garbage", **_BASE_KW)
    assert red.call_count == 0
    assert len(get_buffer()) == 0
    # A WARNING log line must mention the unknown mode.
    assert any("unknown mode" in rec.message for rec in caplog.records)


def test_live_redis_push_failure_swallowed():
    """When the cache layer raises, the dispatcher must NOT
    propagate — the fill ledger is the source of truth."""
    fake_cache = MagicMock()
    fake_cache.rpush.side_effect = RuntimeError("redis down")
    with patch.object(
        snap_mod,
        "get_cache",
        return_value=fake_cache,
    ):
        # MUST NOT raise.
        write_trade_feature_snapshot(
            mode="live",
            user_id="user-x",
            **_BASE_KW,
        )


def test_redis_key_format_matches_spec():
    """Key shape: ``algo:live:snapshots:{user_id}:{iso_date}``."""
    fake_cache = MagicMock()
    fake_cache.rpush.return_value = 1
    fake_cache.expire.return_value = True
    from datetime import date

    with patch.object(
        snap_mod,
        "get_cache",
        return_value=fake_cache,
    ):
        from backend.algo.features.snapshots import (
            _push_live_snapshot_to_redis,
        )
        from backend.algo.features.snapshots_buffer import (
            FillSnapshotRow,
        )

        row = FillSnapshotRow(
            fill_id="f1",
            run_id="run-1",
            strategy_id="strat-1",
            ticker="FAKE.NS",
            side="BUY",
            qty=1,
            fill_price=Decimal("100"),
            fill_ts_ns=None,
            bar_date="2026-05-15",
            mode="live",
            features=None,
        )
        _push_live_snapshot_to_redis(
            row=row,
            user_id="user-123",
            trading_date_ist=date(2026, 5, 15),
        )
    args, _ = fake_cache.rpush.call_args
    assert args[0] == "algo:live:snapshots:user-123:2026-05-15"
    # Expire is called with 48h TTL.
    exp_args, _ = fake_cache.expire.call_args
    assert exp_args[0] == "algo:live:snapshots:user-123:2026-05-15"
    assert exp_args[1] == 48 * 60 * 60


def test_dispatcher_force_immediate_writes_one_row():
    """``force_immediate=True`` is the escape hatch for
    fixtures / admin tools — bypasses buffer + Redis."""
    with patch(
        "backend.algo.features.snapshots."
        "write_trade_feature_snapshots_batch",
        return_value=1,
    ) as wr:
        write_trade_feature_snapshot(
            mode="backtest",
            force_immediate=True,
            **_BASE_KW,
        )
    assert wr.call_count == 1
    # Buffer NOT touched.
    assert len(get_buffer()) == 0


def test_dispatcher_swallows_internal_exceptions(caplog):
    """Dispatcher contract: NEVER raise even if the buffer
    layer itself misbehaves (defense-in-depth — the fill must
    never be blocked)."""
    with patch.object(
        snap_mod,
        "get_buffer",
        side_effect=RuntimeError("simulated singleton crash"),
    ):
        with caplog.at_level("ERROR"):
            write_trade_feature_snapshot(mode="backtest", **_BASE_KW)
    # Logged at error level, no raise.
    assert any("dispatcher failed" in rec.message for rec in caplog.records)


def test_live_without_user_id_falls_back_to_immediate(caplog):
    """If the live path can't resolve a user, the dispatcher
    falls back to immediate write rather than silently
    dropping the row."""
    with (
        patch(
            "backend.algo.features.snapshots."
            "write_trade_feature_snapshots_batch",
            return_value=1,
        ) as wr,
        patch.object(
            snap_mod,
            "_resolve_live_user_id",
            return_value=None,
        ),
    ):
        with caplog.at_level("WARNING"):
            write_trade_feature_snapshot(
                mode="live",
                **_BASE_KW,
            )
    assert wr.call_count == 1
    assert any("user_id unresolved" in rec.message for rec in caplog.records)
