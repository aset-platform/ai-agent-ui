"""Unit tests for the FE-4 partition-chunk Redis loader.

Covers:

- Full cache hit short-circuits Iceberg.
- Full cache miss triggers a single Iceberg scan and writes
  every chunk back to the cache.
- Partial miss scans ONLY the missing ``(ticker, year_month)``
  combos.
- On-demand backfill fires when Iceberg returns nothing for a
  chunk, then a re-scan picks up the freshly-written rows.
- Fail-fast: ``FeaturePanelMissingError`` lists the still-
  missing tuples after backfill.
- Window-slicing: bars outside ``[period_start, period_end]``
  IST drop from the assembled panel.
- Partition-chunk key schema is the exact contract spec'd.
- Runner integration smoke — the backtest runner calls the
  loader with the right kwargs and propagates the result.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

import backend.algo.features.loader as loader_mod
from backend.algo.features.loader import (
    FeaturePanelMissingError,
    _chunk_key,
    _serialize_chunk,
    load_intraday_features_window,
)

IST = timezone(timedelta(hours=5, minutes=30))


def _ts_ns(day: date, hour: int, minute: int) -> int:
    dt = datetime(
        day.year,
        day.month,
        day.day,
        hour,
        minute,
        tzinfo=IST,
    )
    return int(
        dt.astimezone(timezone.utc).timestamp() * 1_000_000_000,
    )


def _make_row(
    *,
    ticker: str,
    ts_ns: int,
    bar_date: str,
    year_month: str,
    interval_sec: int,
    feature_name: str,
    feature_value: float,
    feature_set_version: str = "v1.0",
) -> dict:
    return {
        "ticker": ticker,
        "bar_open_ts_ns": ts_ns,
        "bar_date": bar_date,
        "year_month": year_month,
        "interval_sec": interval_sec,
        "feature_name": feature_name,
        "feature_value": feature_value,
        "feature_set_version": feature_set_version,
    }


@pytest.fixture
def stub_cache():
    """In-memory dict-backed cache stub matching the
    ``CacheService`` interface used by the loader.
    """
    storage: dict[str, str] = {}

    cache = MagicMock()

    def _get(key: str):
        return storage.get(key)

    def _set(key: str, value: str, ttl: int = 0):
        storage[key] = value

    def _invalidate(pattern: str):
        import fnmatch

        for k in list(storage.keys()):
            if fnmatch.fnmatch(k, pattern):
                del storage[k]

    cache.get.side_effect = _get
    cache.set.side_effect = _set
    cache.invalidate.side_effect = _invalidate
    cache._storage = storage  # expose for assertions
    return cache


# ────────────────────────────────────────────────────────────────
# Partition-chunk key schema
# ────────────────────────────────────────────────────────────────


def test_partition_chunk_key_schema():
    assert _chunk_key("RELIANCE.NS", "2026-04", 900) == (
        "cache:feature:chunk:RELIANCE.NS:2026-04:900"
    )


# ────────────────────────────────────────────────────────────────
# Phase 1: Redis hit / miss
# ────────────────────────────────────────────────────────────────


def test_full_cache_hit_skips_iceberg(stub_cache):
    """Pre-populate Redis with every chunk; the Iceberg scan must
    never fire."""
    day = date(2026, 4, 15)
    ts = _ts_ns(day, 9, 15)
    row = _make_row(
        ticker="RELIANCE.NS",
        ts_ns=ts,
        bar_date="2026-04-15",
        year_month="2026-04",
        interval_sec=900,
        feature_name="rsi_14",
        feature_value=55.5,
    )
    stub_cache._storage["cache:feature:chunk:RELIANCE.NS:2026-04:900"] = (
        _serialize_chunk([row])
    )

    with (
        patch.object(loader_mod, "get_cache", return_value=stub_cache),
        patch.object(
            loader_mod,
            "_scan_iceberg_for_chunks",
        ) as scan_mock,
    ):
        panel = load_intraday_features_window(
            tickers=["RELIANCE.NS"],
            interval_sec=900,
            period_start=date(2026, 4, 1),
            period_end=date(2026, 4, 30),
        )
    scan_mock.assert_not_called()
    assert "RELIANCE.NS" in panel
    assert panel["RELIANCE.NS"][ts]["rsi_14"] == Decimal("55.5")


def test_full_cache_miss_calls_iceberg_then_caches(stub_cache):
    """Empty Redis; Iceberg returns rows. Result is cached for
    every touched chunk."""
    day = date(2026, 4, 15)
    ts = _ts_ns(day, 9, 30)
    row = _make_row(
        ticker="TCS.NS",
        ts_ns=ts,
        bar_date="2026-04-15",
        year_month="2026-04",
        interval_sec=900,
        feature_name="vwap",
        feature_value=3500.25,
    )

    with (
        patch.object(loader_mod, "get_cache", return_value=stub_cache),
        patch.object(
            loader_mod,
            "_scan_iceberg_for_chunks",
            return_value={("TCS.NS", "2026-04"): [row]},
        ) as scan_mock,
    ):
        panel = load_intraday_features_window(
            tickers=["TCS.NS"],
            interval_sec=900,
            period_start=date(2026, 4, 1),
            period_end=date(2026, 4, 30),
        )
    scan_mock.assert_called_once()
    assert panel["TCS.NS"][ts]["vwap"] == Decimal("3500.25")
    # Cache write-through fired.
    assert "cache:feature:chunk:TCS.NS:2026-04:900" in stub_cache._storage


def test_partial_miss_only_scans_missing_chunks(stub_cache):
    """One ticker cached, another missing. The Iceberg scan is
    called with ONLY the missing ticker."""
    day = date(2026, 4, 15)
    ts = _ts_ns(day, 9, 15)
    cached_row = _make_row(
        ticker="RELIANCE.NS",
        ts_ns=ts,
        bar_date="2026-04-15",
        year_month="2026-04",
        interval_sec=900,
        feature_name="rsi_14",
        feature_value=42.0,
    )
    fetched_row = _make_row(
        ticker="HDFC.NS",
        ts_ns=ts,
        bar_date="2026-04-15",
        year_month="2026-04",
        interval_sec=900,
        feature_name="rsi_14",
        feature_value=60.0,
    )
    stub_cache._storage["cache:feature:chunk:RELIANCE.NS:2026-04:900"] = (
        _serialize_chunk([cached_row])
    )

    with (
        patch.object(loader_mod, "get_cache", return_value=stub_cache),
        patch.object(
            loader_mod,
            "_scan_iceberg_for_chunks",
            return_value={("HDFC.NS", "2026-04"): [fetched_row]},
        ) as scan_mock,
    ):
        panel = load_intraday_features_window(
            tickers=["RELIANCE.NS", "HDFC.NS"],
            interval_sec=900,
            period_start=date(2026, 4, 1),
            period_end=date(2026, 4, 30),
        )
    scan_mock.assert_called_once()
    kwargs = scan_mock.call_args.kwargs
    assert kwargs["tickers"] == ["HDFC.NS"]
    assert kwargs["year_months"] == ["2026-04"]
    assert kwargs["interval_sec"] == 900
    assert panel["RELIANCE.NS"][ts]["rsi_14"] == Decimal("42.0")
    assert panel["HDFC.NS"][ts]["rsi_14"] == Decimal("60.0")


# ────────────────────────────────────────────────────────────────
# Phase 2: on-demand backfill
# ────────────────────────────────────────────────────────────────


def test_on_demand_backfill_fires_on_iceberg_miss(stub_cache):
    """Empty Redis + Iceberg returns nothing on first scan;
    backfill is invoked, then the re-scan finds the rows."""
    day = date(2026, 4, 15)
    ts = _ts_ns(day, 9, 15)
    written_row = _make_row(
        ticker="INFY.NS",
        ts_ns=ts,
        bar_date="2026-04-15",
        year_month="2026-04",
        interval_sec=900,
        feature_name="atr_14",
        feature_value=12.34,
    )

    scan_calls: list[dict] = []

    def _scan_side_effect(**kwargs):
        scan_calls.append(kwargs)
        # First call (pre-backfill) → empty; second (post-backfill)
        # → real row.
        if len(scan_calls) == 1:
            return {}
        return {("INFY.NS", "2026-04"): [written_row]}

    backfill_mock = MagicMock()

    with (
        patch.object(loader_mod, "get_cache", return_value=stub_cache),
        patch.object(
            loader_mod,
            "_scan_iceberg_for_chunks",
            side_effect=_scan_side_effect,
        ),
        patch.object(
            loader_mod,
            "_run_backfill_sync",
            side_effect=backfill_mock,
        ),
    ):
        panel = load_intraday_features_window(
            tickers=["INFY.NS"],
            interval_sec=900,
            period_start=date(2026, 4, 10),
            period_end=date(2026, 4, 20),
        )
    backfill_mock.assert_called_once()
    bf_kwargs = backfill_mock.call_args.kwargs
    assert bf_kwargs["tickers"] == ["INFY.NS"]
    assert bf_kwargs["interval_sec"] == 900
    # Window aligned to whole missing month(s).
    assert bf_kwargs["period_start"] == date(2026, 4, 1)
    assert bf_kwargs["period_end"] == date(2026, 4, 30)
    assert panel["INFY.NS"][ts]["atr_14"] == Decimal("12.34")
    # Two scans: pre + post backfill.
    assert len(scan_calls) == 2


def test_fail_fast_when_still_missing_after_backfill(stub_cache):
    """Iceberg empty before AND after backfill ⇒
    FeaturePanelMissingError with chunk tuples in message."""
    with (
        patch.object(loader_mod, "get_cache", return_value=stub_cache),
        patch.object(
            loader_mod,
            "_scan_iceberg_for_chunks",
            return_value={},
        ),
        patch.object(loader_mod, "_run_backfill_sync"),
        pytest.raises(FeaturePanelMissingError) as exc_info,
    ):
        load_intraday_features_window(
            tickers=["BHEL.NS"],
            interval_sec=900,
            period_start=date(2026, 4, 1),
            period_end=date(2026, 4, 30),
        )
    msg = str(exc_info.value)
    assert "BHEL.NS" in msg
    assert "2026-04" in msg
    assert "900" in msg


# ────────────────────────────────────────────────────────────────
# Phase 5: window slicing
# ────────────────────────────────────────────────────────────────


def test_window_slicing_filters_out_of_range_bars(stub_cache):
    """Cache returns rows spanning two months; request only mid-
    April → rows from January / out-of-range April are dropped."""
    jan_ts = _ts_ns(date(2026, 1, 15), 10, 0)
    apr5_ts = _ts_ns(date(2026, 4, 5), 10, 0)
    apr10_ts = _ts_ns(date(2026, 4, 10), 10, 0)
    apr20_ts = _ts_ns(date(2026, 4, 20), 10, 0)
    jan_row = _make_row(
        ticker="X.NS",
        ts_ns=jan_ts,
        bar_date="2026-01-15",
        year_month="2026-01",
        interval_sec=900,
        feature_name="vwap",
        feature_value=1.0,
    )
    apr_rows = [
        _make_row(
            ticker="X.NS",
            ts_ns=apr5_ts,
            bar_date="2026-04-05",
            year_month="2026-04",
            interval_sec=900,
            feature_name="vwap",
            feature_value=2.0,
        ),
        _make_row(
            ticker="X.NS",
            ts_ns=apr10_ts,
            bar_date="2026-04-10",
            year_month="2026-04",
            interval_sec=900,
            feature_name="vwap",
            feature_value=3.0,
        ),
        _make_row(
            ticker="X.NS",
            ts_ns=apr20_ts,
            bar_date="2026-04-20",
            year_month="2026-04",
            interval_sec=900,
            feature_name="vwap",
            feature_value=4.0,
        ),
    ]
    stub_cache._storage["cache:feature:chunk:X.NS:2026-01:900"] = (
        _serialize_chunk([jan_row])
    )
    stub_cache._storage["cache:feature:chunk:X.NS:2026-02:900"] = (
        _serialize_chunk([])
    )
    stub_cache._storage["cache:feature:chunk:X.NS:2026-03:900"] = (
        _serialize_chunk([])
    )
    stub_cache._storage["cache:feature:chunk:X.NS:2026-04:900"] = (
        _serialize_chunk(apr_rows)
    )

    with patch.object(loader_mod, "get_cache", return_value=stub_cache):
        panel = load_intraday_features_window(
            tickers=["X.NS"],
            interval_sec=900,
            period_start=date(2026, 4, 8),
            period_end=date(2026, 4, 15),
        )
    ts_keys = sorted(panel["X.NS"].keys())
    # Only the Apr 10 bar falls in [Apr 8, Apr 15].
    assert ts_keys == [apr10_ts]


# ────────────────────────────────────────────────────────────────
# Runner integration smoke
# ────────────────────────────────────────────────────────────────


def test_runner_uses_loader():
    """The intraday runner dispatches to
    ``load_intraday_features_window`` with the right kwargs and
    propagates the returned panel into the per-bar feature
    lookup."""
    from backend.algo.backtest.runner import run_backtest
    from backend.algo.backtest.types import BacktestRequest, BarData
    from backend.algo.strategy.ast import parse_strategy

    day = date(2026, 4, 1)
    ts0 = _ts_ns(day, 9, 15)
    ts1 = _ts_ns(day, 9, 30)
    bars = {
        "FAKE.NS": [
            BarData(
                ticker="FAKE.NS",
                date=day,
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100"),
                volume=1000,
                bar_open_ts_ns=ts0,
            ),
            BarData(
                ticker="FAKE.NS",
                date=day,
                open=Decimal("101"),
                high=Decimal("102"),
                low=Decimal("100"),
                close=Decimal("101"),
                volume=1000,
                bar_open_ts_ns=ts1,
            ),
        ]
    }
    sentinel_panel = {"FAKE.NS": {ts0: {"rsi_14": Decimal("50")}}}
    strategy = parse_strategy(
        {
            "id": str(uuid4()),
            "name": "smoke",
            "universe": {
                "type": "scope",
                "scope": "watchlist",
                "filter": {
                    "ticker_type": ["stock"],
                    "market": "india",
                },
            },
            "schedule": {
                "type": "bar_close",
                "interval": "15m",
                "time": "every-bar IST",
            },
            "rebalance": {"type": "daily", "max_positions": 1},
            "root": {"type": "buy", "qty": {"shares": 1}},
            "risk": {
                "per_trade": {
                    "stop_loss_pct": 5,
                    "max_qty": 100,
                },
                "portfolio": {
                    "max_exposure_pct": 80,
                    "max_concentration_pct": 25,
                },
                "daily": {
                    "max_loss_pct": 50,
                    "max_open_positions": 50,
                },
            },
        }
    )
    request = BacktestRequest(
        strategy_id=strategy.id,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 1),
        interval_sec=900,
    )
    with (
        patch(
            "backend.algo.backtest.runner.load_intraday_bars_window",
            return_value=bars,
        ),
        patch(
            "backend.algo.backtest.runner.load_intraday_features_window",
            return_value=sentinel_panel,
        ) as feat_loader,
        patch("backend.algo.backtest.runner.flush_events"),
    ):
        run_backtest(
            strategy=strategy,
            request=request,
            user_id=uuid4(),
            universe=["FAKE.NS"],
        )
    # FE-15b — the runner now calls load_intraday_features_window
    # TWICE for intraday strategies: once for primary cadence
    # (interval_sec=900) and once for the daily cross-cadence
    # overlay (interval_sec=86400). The primary call carries the
    # canonical loader contract; the overlay call sets
    # enable_on_demand_backfill=False.
    assert feat_loader.call_count == 2
    primary_call = next(
        c for c in feat_loader.call_args_list
        if c.kwargs.get("interval_sec") == 900
    )
    overlay_call = next(
        c for c in feat_loader.call_args_list
        if c.kwargs.get("interval_sec") == 86400
    )
    assert primary_call.kwargs["period_start"] == date(2026, 4, 1)
    assert primary_call.kwargs["period_end"] == date(2026, 4, 1)
    assert primary_call.kwargs["tickers"] == ["FAKE.NS"]
    assert overlay_call.kwargs["period_start"] == date(2026, 4, 1)
    assert overlay_call.kwargs["period_end"] == date(2026, 4, 1)
    assert overlay_call.kwargs["enable_on_demand_backfill"] is False
