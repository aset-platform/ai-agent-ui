"""Unit tests for the FE-10 live / paper feature emission hook.

The hook is a SIDE EFFECT — it must:

  * Skip daily cadences (FE-3 daily compute owns those).
  * Skip unsupported ``interval_sec`` values.
  * Compute features via :func:`compute_intraday_features` and
    persist via :func:`_write_features_batch` (FE-3's writer is
    reused so the NaN-replaceable upsert / cache invalidation
    behaviour stays in one place).
  * Never raise — every exception logged with ``exc_info=True``.

We mock the FE-3 writer + the compute engine so no real Iceberg
I/O runs in CI. Engine output is fed deterministically.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest

from backend.algo.backtest.types import BarData
from backend.algo.features.live_emitter import emit_features_for_bar


def _bar(
    *,
    ticker: str = "RELIANCE.NS",
    ts_ns: int = 1_700_000_000_000_000_000,
    close: str = "2500.50",
) -> BarData:
    return BarData(
        ticker=ticker,
        date=datetime.fromtimestamp(
            ts_ns / 1_000_000_000,
            tz=timezone.utc,
        ).date(),
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=1000,
        bar_open_ts_ns=ts_ns,
    )


def _fake_features(ts_ns: int) -> dict:
    return {
        ts_ns: {
            "vwap": Decimal("2500.10"),
            "rsi_14": Decimal("55.5"),
            "today_ltp": Decimal("2500.50"),
            "today_vol": Decimal("1000"),
        }
    }


def test_emit_features_for_bar_writes_via_batch_writer() -> None:
    """Happy path: writer called with the correct arrow rows.

    Asserts the rows carry ``ticker``, ``bar_open_ts_ns``,
    ``interval_sec``, ``feature_set_version`` exactly as the
    caller supplied, and that each feature name from the engine
    is present in the long-format batch.
    """
    bar = _bar()
    history = [bar]
    with (
        patch(
            "backend.algo.features.live_emitter." "compute_intraday_features",
            return_value=_fake_features(bar.bar_open_ts_ns),
        ) as mock_engine,
        patch(
            "backend.algo.features.live_emitter." "_write_features_batch",
        ) as mock_writer,
    ):
        emit_features_for_bar(
            ticker="RELIANCE.NS",
            interval_sec=900,
            history=history,
            cadence_interval="15m",
            mode="paper",
            feature_set_version="v1",
        )

    mock_engine.assert_called_once()
    assert mock_writer.call_count == 1
    arrow_rows = mock_writer.call_args.kwargs["arrow_rows"]
    assert isinstance(arrow_rows, list)
    assert len(arrow_rows) == 4  # 4 features in fake panel
    names = {r["feature_name"] for r in arrow_rows}
    assert names == {"vwap", "rsi_14", "today_ltp", "today_vol"}
    for r in arrow_rows:
        assert r["ticker"] == "RELIANCE.NS"
        assert r["bar_open_ts_ns"] == bar.bar_open_ts_ns
        assert r["interval_sec"] == 900
        assert r["feature_set_version"] == "v1"


def test_emit_features_for_bar_skips_daily_cadence() -> None:
    """``cadence_interval='1d'`` is a no-op — FE-3 owns daily."""
    bar = _bar()
    with (
        patch(
            "backend.algo.features.live_emitter." "compute_intraday_features",
        ) as mock_engine,
        patch(
            "backend.algo.features.live_emitter." "_write_features_batch",
        ) as mock_writer,
    ):
        emit_features_for_bar(
            ticker="RELIANCE.NS",
            interval_sec=86400,
            history=[bar],
            cadence_interval="1d",
            mode="paper",
        )
    mock_engine.assert_not_called()
    mock_writer.assert_not_called()


def test_emit_features_for_bar_rejects_invalid_interval_sec(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``interval_sec`` outside ``{60, 300, 900}`` is rejected
    with a WARNING log and no writer call."""
    bar = _bar()
    with (
        patch(
            "backend.algo.features.live_emitter." "compute_intraday_features",
        ) as mock_engine,
        patch(
            "backend.algo.features.live_emitter." "_write_features_batch",
        ) as mock_writer,
        caplog.at_level("WARNING"),
    ):
        emit_features_for_bar(
            ticker="RELIANCE.NS",
            interval_sec=30,
            history=[bar],
            cadence_interval="15m",
            mode="live",
        )
    mock_engine.assert_not_called()
    mock_writer.assert_not_called()
    assert any("interval_sec=30" in rec.message for rec in caplog.records)


def test_emit_features_for_bar_swallows_writer_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Writer raise MUST be caught + logged with ``exc_info``.

    The hook is wrapped around live trading; any uncaught
    exception here would crash the bar-close handler.
    """
    bar = _bar()
    with (
        patch(
            "backend.algo.features.live_emitter." "compute_intraday_features",
            return_value=_fake_features(bar.bar_open_ts_ns),
        ),
        patch(
            "backend.algo.features.live_emitter." "_write_features_batch",
            side_effect=RuntimeError("simulated iceberg outage"),
        ),
        caplog.at_level("ERROR"),
    ):
        ret = emit_features_for_bar(
            ticker="RELIANCE.NS",
            interval_sec=900,
            history=[bar],
            cadence_interval="15m",
            mode="live",
        )
    assert ret is None
    # _logger.exception emits at ERROR and ALWAYS attaches
    # exc_info — verify both.
    exc_records = [
        r
        for r in caplog.records
        if r.levelname == "ERROR" and r.exc_info is not None
    ]
    assert exc_records, "expected an ERROR record with exc_info"
    assert any("write failed" in r.message for r in exc_records)


def test_emit_features_for_bar_skips_when_compute_returns_empty() -> None:
    """Engine returns ``{}`` (e.g. under warmup) — writer skipped
    silently, no exception, no log noise."""
    bar = _bar()
    with (
        patch(
            "backend.algo.features.live_emitter." "compute_intraday_features",
            return_value={},
        ),
        patch(
            "backend.algo.features.live_emitter." "_write_features_batch",
        ) as mock_writer,
    ):
        emit_features_for_bar(
            ticker="RELIANCE.NS",
            interval_sec=900,
            history=[bar],
            cadence_interval="15m",
            mode="paper",
        )
    mock_writer.assert_not_called()


def test_emit_features_for_bar_skips_empty_history() -> None:
    """Empty history → no engine call, no write."""
    with (
        patch(
            "backend.algo.features.live_emitter." "compute_intraday_features",
        ) as mock_engine,
        patch(
            "backend.algo.features.live_emitter." "_write_features_batch",
        ) as mock_writer,
    ):
        emit_features_for_bar(
            ticker="RELIANCE.NS",
            interval_sec=900,
            history=[],
            cadence_interval="15m",
            mode="paper",
        )
    mock_engine.assert_not_called()
    mock_writer.assert_not_called()


def test_emit_features_for_bar_panel_has_correct_ticker_and_ts_ns() -> None:
    """Golden test — the panel handed to ``_panel_to_arrow_rows``
    (through the writer) carries the right ``(ticker, ts_ns)``
    pair for the LAST bar in history.

    We pin a 3-bar history; engine returns features ONLY for the
    last bar's ts; assert the writer sees rows tagged with that
    last bar's ts (not the first / middle bar).
    """
    bars = [
        _bar(ts_ns=1_700_000_000_000_000_000, close="100"),
        _bar(ts_ns=1_700_000_900_000_000_000, close="101"),
        _bar(ts_ns=1_700_000_001_800_000_000, close="102"),
    ]
    last_ts = bars[-1].bar_open_ts_ns
    with (
        patch(
            "backend.algo.features.live_emitter." "compute_intraday_features",
            return_value=_fake_features(last_ts),
        ),
        patch(
            "backend.algo.features.live_emitter." "_write_features_batch",
        ) as mock_writer,
    ):
        emit_features_for_bar(
            ticker="TCS.NS",
            interval_sec=900,
            history=bars,
            cadence_interval="15m",
            mode="live",
        )
    arrow_rows = mock_writer.call_args.kwargs["arrow_rows"]
    assert {r["ticker"] for r in arrow_rows} == {"TCS.NS"}
    assert {r["bar_open_ts_ns"] for r in arrow_rows} == {last_ts}
    # Sanity: bar_date stamped from the last bar's UTC date.
    expected_date = datetime.fromtimestamp(
        last_ts / 1_000_000_000,
        tz=timezone.utc,
    ).date()
    assert {r["bar_date"] for r in arrow_rows} == {
        expected_date.strftime("%Y-%m-%d"),
    }


def test_emit_features_for_bar_rejects_unknown_cadence_interval(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Cadence label outside the supported set → warn + no-op."""
    bar = _bar()
    with (
        patch(
            "backend.algo.features.live_emitter." "compute_intraday_features",
        ) as mock_engine,
        patch(
            "backend.algo.features.live_emitter." "_write_features_batch",
        ) as mock_writer,
        caplog.at_level("WARNING"),
    ):
        emit_features_for_bar(
            ticker="RELIANCE.NS",
            interval_sec=900,
            history=[bar],
            cadence_interval="30m",  # not in registry
            mode="paper",
        )
    mock_engine.assert_not_called()
    mock_writer.assert_not_called()
    assert any("cadence_interval" in rec.message for rec in caplog.records)


def test_emit_features_for_bar_swallows_compute_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Engine raise MUST be caught + logged; writer not called."""
    bar = _bar()
    with (
        patch(
            "backend.algo.features.live_emitter." "compute_intraday_features",
            side_effect=ValueError("bad bars"),
        ),
        patch(
            "backend.algo.features.live_emitter." "_write_features_batch",
        ) as mock_writer,
        caplog.at_level("ERROR"),
    ):
        ret = emit_features_for_bar(
            ticker="RELIANCE.NS",
            interval_sec=900,
            history=[bar],
            cadence_interval="15m",
            mode="paper",
        )
    assert ret is None
    mock_writer.assert_not_called()
    assert any(
        "compute_intraday_features failed" in rec.message
        for rec in caplog.records
    )


def test_emit_features_for_bar_no_bar_open_ts_ns_skipped() -> None:
    """Daily-shaped bar (``bar_open_ts_ns=None``) defensively
    skipped — runtime should never hand us one of these but the
    guard exists in case the adapter shim regresses."""
    bar = BarData(
        ticker="RELIANCE.NS",
        date=date(2026, 5, 12),
        open=Decimal("100"),
        high=Decimal("100"),
        low=Decimal("100"),
        close=Decimal("100"),
        volume=1000,
        bar_open_ts_ns=None,
    )
    with (
        patch(
            "backend.algo.features.live_emitter." "compute_intraday_features",
        ) as mock_engine,
        patch(
            "backend.algo.features.live_emitter." "_write_features_batch",
        ) as mock_writer,
    ):
        emit_features_for_bar(
            ticker="RELIANCE.NS",
            interval_sec=900,
            history=[bar],
            cadence_interval="15m",
            mode="paper",
        )
    mock_engine.assert_not_called()
    mock_writer.assert_not_called()
