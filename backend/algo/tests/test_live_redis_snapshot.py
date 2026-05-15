"""FE-5.1 — Live runtime now pushes snapshots to Redis instead
of Iceberg (ASETPLTFRM-417).

Validates:

* The live fill path emits a Redis ``rpush`` with the expected
  key shape and a JSON payload.
* The in-process snapshot buffer is NOT touched on the live
  path (live sessions can run for hours/days and an
  in-process buffer would grow unbounded).
* A Redis outage does NOT block the fill ledger / event
  emission.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

from backend.algo.features import snapshots as snap_mod
from backend.algo.features.snapshots import (
    _push_live_snapshot_to_redis,
    write_trade_feature_snapshot,
)
from backend.algo.features.snapshots_buffer import (
    get_buffer,
    reset_buffer,
)


def _kw():
    return dict(
        fill_id="kite-123",
        run_id="run-1",
        strategy_id="strat-1",
        ticker="RELIANCE.NS",
        side="BUY",
        qty=5,
        fill_price=Decimal("2500.00"),
        fill_ts_ns=None,
        bar_date="2026-05-15",
        features=None,
    )


def test_live_fill_pushes_to_redis_list_not_iceberg():
    """``write_trade_feature_snapshot(mode='live', ...)``
    routes to Redis ``rpush``, NOT to the buffer and NOT to
    ``write_trade_feature_snapshots_batch``."""
    reset_buffer()
    fake_cache = MagicMock()
    fake_cache.rpush.return_value = 1
    fake_cache.expire.return_value = True
    with (
        patch(
            "backend.algo.features.snapshots."
            "write_trade_feature_snapshots_batch",
        ) as batch_writer,
        patch.object(
            snap_mod,
            "get_cache",
            return_value=fake_cache,
        ),
    ):
        write_trade_feature_snapshot(
            mode="live",
            user_id="user-1",
            **_kw(),
        )
    # Iceberg writer not called.
    assert batch_writer.call_count == 0
    # Redis rpush called exactly once.
    assert fake_cache.rpush.call_count == 1
    # Buffer untouched.
    assert len(get_buffer()) == 0


def test_live_redis_outage_swallowed():
    """A Redis error must NOT propagate — the fill ledger
    (algo.events) is the source of truth and snapshot loss
    is acceptable."""
    fake_cache = MagicMock()
    fake_cache.rpush.side_effect = RuntimeError("redis down")
    with patch.object(
        snap_mod,
        "get_cache",
        return_value=fake_cache,
    ):
        # Must NOT raise.
        write_trade_feature_snapshot(
            mode="live",
            user_id="user-x",
            **_kw(),
        )


def test_live_redis_payload_is_well_formed_json():
    """Verify the JSON payload pushed to Redis has the keys
    the EOD flush job expects to deserialize."""
    import json

    fake_cache = MagicMock()
    fake_cache.rpush.return_value = 1
    fake_cache.expire.return_value = True
    with patch.object(
        snap_mod,
        "get_cache",
        return_value=fake_cache,
    ):
        write_trade_feature_snapshot(
            mode="live",
            user_id="user-1",
            **_kw(),
        )
    args, _ = fake_cache.rpush.call_args
    key, value = args
    assert key.startswith("algo:live:snapshots:user-1:")
    payload = json.loads(value)
    for required in (
        "fill_id",
        "run_id",
        "strategy_id",
        "ticker",
        "side",
        "qty",
        "fill_price",
        "bar_date",
        "mode",
    ):
        assert required in payload
    assert payload["mode"] == "live"
    assert payload["ticker"] == "RELIANCE.NS"


def test_live_redis_push_helper_directly():
    """Direct call to :func:`_push_live_snapshot_to_redis`
    sets the 48h TTL."""
    from datetime import date

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
    fake_cache = MagicMock()
    fake_cache.rpush.return_value = 1
    fake_cache.expire.return_value = True
    with patch.object(
        snap_mod,
        "get_cache",
        return_value=fake_cache,
    ):
        _push_live_snapshot_to_redis(
            row=row,
            user_id="user-1",
            trading_date_ist=date(2026, 5, 15),
        )
    # 48h = 172800 seconds.
    exp_args, _ = fake_cache.expire.call_args
    assert exp_args[1] == 48 * 60 * 60
