"""Tests for the per-fill trade-feature snapshot writer
(ASETPLTFRM-402 / FE-5, FE-5.1).

Coverage:
- happy path single-row append (via ``force_immediate=True``
  escape hatch — FE-5.1 dispatcher otherwise routes
  backtest/paper to the in-process buffer; the underlying
  row-shape contract is unchanged)
- Decimal + str feature serialization round-trip
- failure isolation: Iceberg raise → log + return None
- None / empty features → ``features_json == "{}"``
- ``written_at`` is tz-naive per Iceberg convention
- ``fill_ts_ns=None`` derives a deterministic ns from
  ``bar_date`` so the required=True column is never None.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pyarrow as pa

from backend.algo.features.snapshots import (
    _serialize_features,
    write_trade_feature_snapshot,
)


def _capture_append():
    """Returns (mock_catalog_loader, captured_tables_list)."""
    captured: list[pa.Table] = []

    fake_table = MagicMock()

    def _append(arrow_tbl: pa.Table) -> None:
        captured.append(arrow_tbl)

    fake_table.append.side_effect = _append

    fake_catalog = MagicMock()
    fake_catalog.load_table.return_value = fake_table
    return fake_catalog, captured


def test_writer_appends_single_row() -> None:
    """Happy path: one fill → one PyArrow row appended with
    the right mode + year_month + required fields."""
    fake_catalog, captured = _capture_append()
    with patch(
        "stocks.create_tables._get_catalog",
        return_value=fake_catalog,
    ):
        write_trade_feature_snapshot(
            fill_id="run-1:RELIANCE.NS:abc",
            run_id="run-1",
            strategy_id="strat-1",
            ticker="RELIANCE.NS",
            side="BUY",
            qty=10,
            fill_price=Decimal("2500.50"),
            fill_ts_ns=1_700_000_000_000_000_000,
            bar_date="2025-03-15",
            mode="backtest",
            features={"rsi_14": Decimal("55.2")},
            force_immediate=True,
        )

    assert len(captured) == 1, "exactly one row appended"
    tbl = captured[0]
    assert tbl.num_rows == 1
    row = tbl.to_pylist()[0]
    assert row["fill_id"] == "run-1:RELIANCE.NS:abc"
    assert row["mode"] == "backtest"
    assert row["year_month"] == "2025-03"
    assert row["ticker"] == "RELIANCE.NS"
    assert row["side"] == "BUY"
    assert row["qty"] == 10
    assert row["fill_price"] == 2500.50
    assert row["fill_ts_ns"] == 1_700_000_000_000_000_000
    assert row["bar_date"] == "2025-03-15"
    assert row["realised_pnl_inr"] is None
    assert row["outcome_label"] is None


def test_writer_serializes_features_to_json() -> None:
    """Decimal + str features round-trip through the JSON
    column with Decimal precision preserved (string form)."""
    fake_catalog, captured = _capture_append()
    with patch(
        "stocks.create_tables._get_catalog",
        return_value=fake_catalog,
    ):
        write_trade_feature_snapshot(
            fill_id="f1",
            run_id="r1",
            strategy_id="s1",
            ticker="TCS.NS",
            side="BUY",
            qty=5,
            fill_price=Decimal("3500.00"),
            fill_ts_ns=1_700_000_000_000_000_000,
            bar_date="2025-04-10",
            mode="paper",
            features={
                "rsi_14": Decimal("65.123456"),
                "time_of_day_bucket": "midday",
                "vwap": Decimal("3499.75"),
            },
            force_immediate=True,
        )

    row = captured[0].to_pylist()[0]
    blob = json.loads(row["features_json"])
    assert (
        blob["rsi_14"] == "65.123456"
    ), "Decimal precision preserved as string"
    assert blob["time_of_day_bucket"] == "midday"
    assert blob["vwap"] == "3499.75"


def test_writer_returns_none_on_iceberg_failure(caplog) -> None:
    """A PyIceberg / append raise MUST be swallowed; the
    function returns None without raising."""
    fake_table = MagicMock()
    fake_table.append.side_effect = RuntimeError("simulated iceberg outage")
    fake_catalog = MagicMock()
    fake_catalog.load_table.return_value = fake_table

    with patch(
        "stocks.create_tables._get_catalog",
        return_value=fake_catalog,
    ):
        with caplog.at_level("ERROR"):
            result = write_trade_feature_snapshot(
                fill_id="f1",
                run_id="r1",
                strategy_id="s1",
                ticker="X.NS",
                side="BUY",
                qty=1,
                fill_price=Decimal("100"),
                fill_ts_ns=1,
                bar_date="2025-01-01",
                mode="backtest",
                features={"x": Decimal("1")},
                force_immediate=True,
            )

    assert result is None
    assert any(
        "trade_feature_snapshot" in r.message for r in caplog.records
    ), "expected non-fatal log message"


def test_writer_handles_none_features() -> None:
    """``features=None`` → ``features_json == '{}'``."""
    fake_catalog, captured = _capture_append()
    with patch(
        "stocks.create_tables._get_catalog",
        return_value=fake_catalog,
    ):
        write_trade_feature_snapshot(
            fill_id="f1",
            run_id="r1",
            strategy_id="s1",
            ticker="X.NS",
            side="SELL",
            qty=2,
            fill_price=Decimal("50"),
            fill_ts_ns=100,
            bar_date="2025-02-01",
            mode="live",
            features=None,
            force_immediate=True,
        )

    row = captured[0].to_pylist()[0]
    assert row["features_json"] == "{}"


def test_writer_handles_empty_features() -> None:
    """``features={}`` → ``features_json == '{}'``."""
    fake_catalog, captured = _capture_append()
    with patch(
        "stocks.create_tables._get_catalog",
        return_value=fake_catalog,
    ):
        write_trade_feature_snapshot(
            fill_id="f1",
            run_id="r1",
            strategy_id="s1",
            ticker="X.NS",
            side="BUY",
            qty=1,
            fill_price=Decimal("10"),
            fill_ts_ns=1,
            bar_date="2025-02-01",
            mode="backtest",
            features={},
            force_immediate=True,
        )

    row = captured[0].to_pylist()[0]
    assert row["features_json"] == "{}"


def test_writer_strips_timezone_from_written_at() -> None:
    """``written_at`` is naive (no tzinfo) per the
    iceberg-tz-naive-timestamps convention."""
    fake_catalog, captured = _capture_append()
    with patch(
        "stocks.create_tables._get_catalog",
        return_value=fake_catalog,
    ):
        write_trade_feature_snapshot(
            fill_id="f1",
            run_id="r1",
            strategy_id="s1",
            ticker="X.NS",
            side="BUY",
            qty=1,
            fill_price=Decimal("10"),
            fill_ts_ns=1,
            bar_date="2025-02-01",
            mode="backtest",
            features={},
            force_immediate=True,
        )

    row = captured[0].to_pylist()[0]
    written_at = row["written_at"]
    assert isinstance(written_at, datetime)
    assert written_at.tzinfo is None


def test_writer_derives_fill_ts_ns_for_daily_fills() -> None:
    """Daily backtest fills pass ``fill_ts_ns=None``; the
    writer derives a deterministic ns from ``bar_date`` UTC
    midnight so the Iceberg required=True column is never
    None."""
    fake_catalog, captured = _capture_append()
    with patch(
        "stocks.create_tables._get_catalog",
        return_value=fake_catalog,
    ):
        write_trade_feature_snapshot(
            fill_id="f1",
            run_id="r1",
            strategy_id="s1",
            ticker="X.NS",
            side="BUY",
            qty=1,
            fill_price=Decimal("10"),
            fill_ts_ns=None,
            bar_date="2025-02-01",
            mode="backtest",
            features={},
            force_immediate=True,
        )

    row = captured[0].to_pylist()[0]
    expected_ns = int(
        datetime(2025, 2, 1, tzinfo=timezone.utc).timestamp() * 1_000_000_000
    )
    assert row["fill_ts_ns"] == expected_ns


def test_serialize_features_drops_nan_and_inf() -> None:
    """``NaN`` / ``inf`` are dropped from the JSON blob —
    reader interprets a missing key as "feature not
    computable at this bar"."""
    out = _serialize_features(
        {
            "good": Decimal("1.5"),
            "nan_dec": Decimal("NaN"),
            "inf_float": float("inf"),
            "nan_float": float("nan"),
            "txt": "ok",
        }
    )
    blob = json.loads(out)
    assert blob == {"good": "1.5", "txt": "ok"}
