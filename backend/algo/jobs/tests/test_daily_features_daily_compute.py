"""Tests for ``daily_features_daily_compute`` — FE-15a.

Covers the orchestration shape, the NaN-replaceable upsert
predicate (scoped to interval_sec=86400 — critical to avoid
wiping intraday rows), and the structured stats roll-up.
Iceberg / DuckDB / PG boundaries are mocked.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from backend.algo.backtest.types import BarData
from backend.algo.features import FEATURE_SET_VERSION
from backend.algo.jobs.daily_features_daily_compute import (
    INTERVAL_SEC,
    _utc_midnight_ns,
    run_daily_features_daily_compute_job,
)


def _bar(
    ticker="A.NS",
    day=date(2026, 1, 5),
):
    return BarData(
        ticker=ticker,
        date=day,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100.5"),
        volume=1000,
        bar_open_ts_ns=_utc_midnight_ns(day),
    )


def _fake_session():
    sess = AsyncMock()
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=sess)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=session_cm)
    return factory


def _patch_session_and_universe(factory, universe):
    return (
        patch(
            "backend.algo.jobs.daily_features_daily_compute."
            "disposable_pg_session",
            return_value=factory,
        ),
        patch(
            "backend.algo.jobs.daily_features_daily_compute."
            "_resolve_nifty500_universe",
            new=AsyncMock(return_value=list(universe)),
        ),
    )


def test_interval_sec_is_86400():
    """Daily job MUST hardcode interval_sec=86400. A regression
    here would either overwrite intraday rows (if 900) or
    silently miss the write (if anything else)."""
    assert INTERVAL_SEC == 86400


def test_utc_midnight_ns_is_deterministic():
    """UTC midnight of a given date must always produce the
    same ts_ns. Required for idempotent upsert keys.
    """
    d = date(2026, 5, 15)
    assert _utc_midnight_ns(d) == _utc_midnight_ns(d)
    expected = int(
        datetime.combine(d, time.min, tzinfo=timezone.utc).timestamp()
        * 1_000_000_000
    )
    assert _utc_midnight_ns(d) == expected


async def test_happy_path_writes_at_86400_with_version_stamp():
    """End-to-end happy path: 2 tickers, panel returns 3 features
    per bar, write goes through with interval_sec=86400 + tz-naive
    written_at + version stamp."""
    factory = _fake_session()
    day_a = date(2026, 1, 5)
    bars_a = [_bar("A.NS", day=day_a)]
    bars_b = [_bar("B.NS", day=day_a)]
    panel = {
        "A.NS": {
            _utc_midnight_ns(day_a): {
                "ema_50": Decimal("100.5"),
                "rsi_14": Decimal("55"),
                "atr_14": Decimal("2.5"),
            },
        },
        "B.NS": {
            _utc_midnight_ns(day_a): {
                "ema_50": Decimal("100.5"),
                "rsi_14": Decimal("60"),
                "atr_14": Decimal("2.7"),
            },
        },
    }
    mock_tbl = MagicMock()
    mock_cat = MagicMock()
    mock_cat.load_table.return_value = mock_tbl

    sess_patch, univ_patch = _patch_session_and_universe(
        factory,
        ["A.NS", "B.NS"],
    )
    with (
        sess_patch,
        univ_patch,
        patch(
            "backend.algo.jobs.daily_features_daily_compute."
            "_load_daily_bars_for_tickers",
            return_value={"A.NS": bars_a, "B.NS": bars_b},
        ),
        patch(
            "backend.algo.jobs.daily_features_daily_compute."
            "compute_daily_features_for_universe",
            return_value=panel,
        ),
        patch(
            "stocks.create_tables._get_catalog",
            return_value=mock_cat,
        ),
        patch(
            "backend.algo.jobs.daily_features_daily_compute."
            "invalidate_metadata",
        ),
    ):
        result = await run_daily_features_daily_compute_job(
            {"period_start": "2026-01-05", "period_end": "2026-01-05"},
        )

    assert result["status"] == "ok"
    assert result["universe_size"] == 2
    assert result["tickers_processed"] == 2
    assert result["tickers_failed"] == 0
    assert result["rows_written"] == 6
    assert result["interval_sec"] == 86400
    assert result["feature_set_version"] == FEATURE_SET_VERSION
    assert result["window"] == ["2026-01-05", "2026-01-05"]

    assert mock_tbl.append.call_count == 1
    arrow_tbl = mock_tbl.append.call_args.args[0]
    intervals = arrow_tbl.column("interval_sec").to_pylist()
    assert all(iv == 86400 for iv in intervals)
    written = arrow_tbl.column("written_at").to_pylist()
    assert all(isinstance(w, datetime) and w.tzinfo is None for w in written)


async def test_scoped_pre_delete_includes_interval_sec_86400():
    """The NaN-replaceable upsert pre-delete predicate MUST
    scope on ``interval_sec=86400`` in addition to ticker +
    bar_date. Otherwise the daily-compute run would wipe the
    intraday rows for the same (ticker, bar_date).
    """
    from pyiceberg.expressions import And

    factory = _fake_session()
    day_a = date(2026, 1, 5)
    bars = [_bar("A.NS", day=day_a)]
    panel = {
        "A.NS": {
            _utc_midnight_ns(day_a): {"ema_50": Decimal("100.5")},
        },
    }
    mock_tbl = MagicMock()
    mock_cat = MagicMock()
    mock_cat.load_table.return_value = mock_tbl

    sess_patch, univ_patch = _patch_session_and_universe(
        factory,
        ["A.NS"],
    )
    with (
        sess_patch,
        univ_patch,
        patch(
            "backend.algo.jobs.daily_features_daily_compute."
            "_load_daily_bars_for_tickers",
            return_value={"A.NS": bars},
        ),
        patch(
            "backend.algo.jobs.daily_features_daily_compute."
            "compute_daily_features_for_universe",
            return_value=panel,
        ),
        patch(
            "stocks.create_tables._get_catalog",
            return_value=mock_cat,
        ),
        patch(
            "backend.algo.jobs.daily_features_daily_compute."
            "invalidate_metadata",
        ),
    ):
        await run_daily_features_daily_compute_job(
            {"period_start": "2026-01-05", "period_end": "2026-01-05"},
        )

    mock_tbl.delete.assert_called_once()
    pred = mock_tbl.delete.call_args.args[0]
    assert isinstance(pred, And)

    seen_refs: set[str] = set()

    def _walk(p):
        if isinstance(p, And):
            _walk(p.left)
            _walk(p.right)
            return
        term = getattr(p, "term", None)
        if term is not None:
            name = getattr(term, "name", None)
            if name:
                seen_refs.add(name)

    _walk(pred)
    assert "ticker" in seen_refs, f"got {seen_refs}"
    assert "bar_date" in seen_refs, f"got {seen_refs}"
    assert "interval_sec" in seen_refs, f"got {seen_refs}"


async def test_warmup_tail_read_but_only_window_written():
    """The job reads ``[write_start - warmup_days, write_end]``
    bars (for SMA200 warmup) but ONLY writes feature rows whose
    bar_date is in the WRITE window. Bars in the warmup tail
    must not produce feature rows.
    """
    factory = _fake_session()
    write_day = date(2026, 1, 5)
    warmup_day = date(2025, 9, 1)
    bars = [_bar("A.NS", day=warmup_day), _bar("A.NS", day=write_day)]
    panel = {
        "A.NS": {
            _utc_midnight_ns(warmup_day): {"ema_50": Decimal("99")},
            _utc_midnight_ns(write_day): {"ema_50": Decimal("100.5")},
        },
    }
    mock_tbl = MagicMock()
    mock_cat = MagicMock()
    mock_cat.load_table.return_value = mock_tbl

    sess_patch, univ_patch = _patch_session_and_universe(
        factory,
        ["A.NS"],
    )
    with (
        sess_patch,
        univ_patch,
        patch(
            "backend.algo.jobs.daily_features_daily_compute."
            "_load_daily_bars_for_tickers",
            return_value={"A.NS": bars},
        ),
        patch(
            "backend.algo.jobs.daily_features_daily_compute."
            "compute_daily_features_for_universe",
            return_value=panel,
        ),
        patch(
            "stocks.create_tables._get_catalog",
            return_value=mock_cat,
        ),
        patch(
            "backend.algo.jobs.daily_features_daily_compute."
            "invalidate_metadata",
        ),
    ):
        result = await run_daily_features_daily_compute_job(
            {
                "period_start": "2026-01-05",
                "period_end": "2026-01-05",
                "warmup_days": 320,
            },
        )

    assert result["rows_written"] == 1
    arrow_tbl = mock_tbl.append.call_args.args[0]
    bar_dates = arrow_tbl.column("bar_date").to_pylist()
    assert bar_dates == ["2026-01-05"]


async def test_nan_feature_values_filtered_before_write():
    """NaN / inf feature values MUST be filtered before the Arrow
    table is constructed.
    """
    import math

    factory = _fake_session()
    day_a = date(2026, 1, 5)
    bars = [_bar("A.NS", day=day_a)]
    panel = {
        "A.NS": {
            _utc_midnight_ns(day_a): {
                "ema_50": Decimal("100"),
                "rsi_14": float("nan"),
                "atr_14": math.inf,
                "bb_width": Decimal("99.5"),
            },
        },
    }
    mock_tbl = MagicMock()
    mock_cat = MagicMock()
    mock_cat.load_table.return_value = mock_tbl

    sess_patch, univ_patch = _patch_session_and_universe(
        factory,
        ["A.NS"],
    )
    with (
        sess_patch,
        univ_patch,
        patch(
            "backend.algo.jobs.daily_features_daily_compute."
            "_load_daily_bars_for_tickers",
            return_value={"A.NS": bars},
        ),
        patch(
            "backend.algo.jobs.daily_features_daily_compute."
            "compute_daily_features_for_universe",
            return_value=panel,
        ),
        patch(
            "stocks.create_tables._get_catalog",
            return_value=mock_cat,
        ),
        patch(
            "backend.algo.jobs.daily_features_daily_compute."
            "invalidate_metadata",
        ),
    ):
        result = await run_daily_features_daily_compute_job(
            {"period_start": "2026-01-05", "period_end": "2026-01-05"},
        )

    assert result["status"] == "ok"
    assert result["rows_written"] == 2
    arrow_tbl = mock_tbl.append.call_args.args[0]
    feat_names = arrow_tbl.column("feature_name").to_pylist()
    assert set(feat_names) == {"ema_50", "bb_width"}


async def test_batched_read_crash_flags_all_tickers_failed():
    """If the batched ohlcv read raises, every ticker in the
    batch is recorded as a fetch failure and compute never fires.
    """
    factory = _fake_session()
    sess_patch, univ_patch = _patch_session_and_universe(
        factory,
        ["A.NS", "B.NS"],
    )
    with (
        sess_patch,
        univ_patch,
        patch(
            "backend.algo.jobs.daily_features_daily_compute."
            "_load_daily_bars_for_tickers",
            side_effect=RuntimeError("simulated batched read failure"),
        ),
        patch(
            "backend.algo.jobs.daily_features_daily_compute."
            "compute_daily_features_for_universe",
        ) as mock_compute,
    ):
        result = await run_daily_features_daily_compute_job(
            {"period_start": "2026-01-05", "period_end": "2026-01-05"},
        )

    assert result["status"] == "ok"
    assert result["tickers_processed"] == 0
    assert result["tickers_failed"] == 2
    assert all(
        "fetch:" in reason for _, reason in result["failures"]
    )
    mock_compute.assert_not_called()


async def test_empty_universe_returns_skipped():
    """Empty universe → graceful early exit, no compute / write."""
    factory = _fake_session()
    sess_patch, univ_patch = _patch_session_and_universe(factory, [])
    with (
        sess_patch,
        univ_patch,
        patch(
            "backend.algo.jobs.daily_features_daily_compute."
            "_load_daily_bars_for_tickers",
        ) as mock_loader,
    ):
        result = await run_daily_features_daily_compute_job(None)

    assert result["status"] == "skipped_empty_universe"
    assert result["universe_size"] == 0
    mock_loader.assert_not_called()


def test_register_job_wired_in_executor():
    """``daily_features_daily_compute`` must be registered in
    ``JOB_EXECUTORS`` so the scheduler can dispatch it.
    """
    from backend.jobs.executor import JOB_EXECUTORS

    assert "daily_features_daily_compute" in JOB_EXECUTORS


def test_register_job_wrapper_is_pipeline_compatible():
    """Sync wrapper with the standard pipeline-step signature."""
    import inspect

    from backend.jobs.executor import JOB_EXECUTORS

    fn = JOB_EXECUTORS["daily_features_daily_compute"]
    assert not inspect.iscoroutinefunction(fn)
    params = inspect.signature(fn).parameters
    for required in (
        "scope",
        "run_id",
        "repo",
        "cancel_event",
        "force",
    ):
        assert required in params
