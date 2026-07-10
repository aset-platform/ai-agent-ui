"""Tests for cooldown_hydration (ASETPLTFRM-436)."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch
from uuid import UUID

from backend.algo.backtest.cooldown_hydration import (
    load_recent_failed_exits,
)
from backend.algo.backtest.cooldown_monitor import in_cooldown


_UID = UUID("11111111-1111-1111-1111-111111111111")
_SID = UUID("22222222-2222-2222-2222-222222222222")


def test_returns_hydrated_closes_compatible_with_in_cooldown():
    """Output rows must be usable as input to in_cooldown."""
    fake_rows = [
        {"ticker": "AAA.NS", "last_failed_exit": date(2025, 1, 1)},
        {"ticker": "BBB.NS", "last_failed_exit": date(2025, 1, 10)},
    ]
    with patch(
        "backend.db.duckdb_engine.query_iceberg_table",
        return_value=fake_rows,
    ):
        closes = load_recent_failed_exits(
            user_id=_UID, strategy_id=_SID,
            cooldown_days=30, as_of=date(2025, 1, 31),
            runtime_mode="paper",
        )
    # Compatible-shape: in_cooldown reads .ticker, .exit_reason,
    # .closed_at — all present.
    assert {c.ticker for c in closes} == {"AAA.NS", "BBB.NS"}
    for c in closes:
        assert c.exit_reason in ("time_stop", "stop_loss")
        assert c.closed_at is not None
    # End-to-end: AAA still in cooldown 30 days later.
    assert in_cooldown(
        ticker="AAA.NS",
        bar_date=date(2025, 1, 20),
        closed_positions=closes,
        cooldown_days=30,
    )


def test_zero_cooldown_days_short_circuits():
    with patch(
        "backend.db.duckdb_engine.query_iceberg_table",
    ) as q:
        closes = load_recent_failed_exits(
            user_id=_UID, strategy_id=_SID,
            cooldown_days=0, as_of=date(2025, 1, 31),
            runtime_mode="paper",
        )
    assert closes == []
    # No DB query when disabled.
    assert q.call_count == 0


def test_unknown_runtime_mode_returns_empty():
    with patch(
        "backend.db.duckdb_engine.query_iceberg_table",
    ) as q:
        closes = load_recent_failed_exits(
            user_id=_UID, strategy_id=_SID,
            cooldown_days=30, as_of=date(2025, 1, 31),
            runtime_mode="backtest",  # not paper/live
        )
    assert closes == []
    assert q.call_count == 0


def test_empty_iceberg_returns_empty_no_raise():
    # Fresh dev box / first deploy.
    with patch(
        "backend.db.duckdb_engine.query_iceberg_table",
        side_effect=FileNotFoundError("no metadata"),
    ):
        closes = load_recent_failed_exits(
            user_id=_UID, strategy_id=_SID,
            cooldown_days=30, as_of=date(2025, 1, 31),
            runtime_mode="paper",
        )
    assert closes == []


def test_skips_rows_with_missing_ticker_or_date():
    fake_rows = [
        {"ticker": None, "last_failed_exit": date(2025, 1, 1)},
        {"ticker": "AAA.NS", "last_failed_exit": None},
        {"ticker": "GOOD.NS", "last_failed_exit": date(2025, 1, 5)},
    ]
    with patch(
        "backend.db.duckdb_engine.query_iceberg_table",
        return_value=fake_rows,
    ):
        closes = load_recent_failed_exits(
            user_id=_UID, strategy_id=_SID,
            cooldown_days=30, as_of=date(2025, 1, 31),
            runtime_mode="paper",
        )
    assert {c.ticker for c in closes} == {"GOOD.NS"}


def test_query_failure_degrades_open():
    # Any unexpected exception should degrade open (trade
    # normally), not block the runtime startup.
    with patch(
        "backend.db.duckdb_engine.query_iceberg_table",
        side_effect=RuntimeError("transient DuckDB hiccup"),
    ):
        closes = load_recent_failed_exits(
            user_id=_UID, strategy_id=_SID,
            cooldown_days=30, as_of=date(2025, 1, 31),
            runtime_mode="live",
        )
    assert closes == []


def test_gtt_triggered_phase1_counts_as_failed_exit():
    """ASETPLTFRM — a GTT-triggered hard-stop (phase 1) must be
    treated as a failed exit on restart-hydration, mirroring the
    in-process cooldown recording added to Piece A/B. Without this,
    a runtime restart between the GTT trigger and the next signal
    would silently lose the cooldown entry, re-opening the exact
    same-day re-entry gap it exists to close."""
    fake_rows = [
        {"ticker": "SUPRIYA.NS", "last_failed_exit": date(2026, 7, 7)},
    ]
    with patch(
        "backend.db.duckdb_engine.query_iceberg_table",
        return_value=fake_rows,
    ) as q:
        closes = load_recent_failed_exits(
            user_id=_UID, strategy_id=_SID,
            cooldown_days=7, as_of=date(2026, 7, 7),
            runtime_mode="live",
        )
    sql = q.call_args.args[1]
    assert "gtt_triggered" in sql
    assert "phase" in sql
    assert {c.ticker for c in closes} == {"SUPRIYA.NS"}
    assert in_cooldown(
        ticker="SUPRIYA.NS",
        bar_date=date(2026, 7, 7),
        closed_positions=closes,
        cooldown_days=7,
    )


def test_gtt_triggered_atr_trail_excluded_from_query():
    """A phase-2 (ATR trail) GTT trigger is a locked-in win, not a
    thesis failure — the SQL's phase allow-list must be exactly
    (1, 15), never including phase 2."""
    with patch(
        "backend.db.duckdb_engine.query_iceberg_table",
        return_value=[],
    ) as q:
        load_recent_failed_exits(
            user_id=_UID, strategy_id=_SID,
            cooldown_days=7, as_of=date(2026, 7, 7),
            runtime_mode="live",
        )
    sql = q.call_args.args[1]
    assert "('1', '15')" in sql


def test_paper_and_live_scoped_to_own_mode():
    # Verify the query filter uses the right mode list per
    # runtime. We check by inspecting the SQL params, not the
    # result, since the mock fakes the row return regardless.
    captured: dict = {}

    def _capture(table, sql, params, *a, **kw):
        captured["sql"] = sql
        captured["params"] = params
        return []

    with patch(
        "backend.db.duckdb_engine.query_iceberg_table",
        side_effect=_capture,
    ):
        load_recent_failed_exits(
            user_id=_UID, strategy_id=_SID,
            cooldown_days=30, as_of=date(2025, 1, 31),
            runtime_mode="paper",
        )
    assert "'paper'" in captured["sql"]
    assert "'live'" not in captured["sql"]

    captured.clear()
    with patch(
        "backend.db.duckdb_engine.query_iceberg_table",
        side_effect=_capture,
    ):
        load_recent_failed_exits(
            user_id=_UID, strategy_id=_SID,
            cooldown_days=30, as_of=date(2025, 1, 31),
            runtime_mode="live",
        )
    assert "'live'" in captured["sql"]
    assert "'paper'" not in captured["sql"]
