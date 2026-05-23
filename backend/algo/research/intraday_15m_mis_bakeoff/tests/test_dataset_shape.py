"""Dataset shape tests using an in-memory pyarrow fixture.

No Iceberg dependency — we monkeypatch ``load_features_eav``
and ``load_bars`` to return synthetic frames, exercising the
pivot + join + filter logic only.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from backend.algo.research.intraday_15m_mis_bakeoff import dataset


def _eav_fixture() -> pd.DataFrame:
    """Two tickers × 8 bars × 3 features in EAV form."""
    rows = []
    base_ts = 1_700_000_000_000_000_000
    for ticker in ("A", "B"):
        for i in range(8):
            ts = base_ts + i * 900 * 1_000_000_000
            for fname, fval in [
                ("rsi_14", 50.0 + i),
                ("relative_volume", 1.0 + i * 0.1),
                ("regime_label", "SIDEWAYS"),
            ]:
                rows.append({
                    "ticker": ticker,
                    "bar_open_ts_ns": ts,
                    "bar_date": date(2026, 1, 5),
                    "interval_sec": 900,
                    "feature_name": fname,
                    "feature_value": fval,
                    "feature_set_version": "v1",
                })
    return pd.DataFrame(rows)


def _bars_fixture() -> pd.DataFrame:
    rows = []
    base_ts = 1_700_000_000_000_000_000
    for ticker in ("A", "B"):
        for i in range(8):
            ts = base_ts + i * 900 * 1_000_000_000
            rows.append({
                "ticker": ticker,
                "bar_open_ts_ns": ts,
                "bar_date": date(2026, 1, 5),
                "interval_sec": 900,
                "open": 100 + i,
                "high": 101 + i,
                "low":  99 + i,
                "close": 100 + i + 0.5,
                "volume": 1000,
                "atr_14": 1.0,
            })
    return pd.DataFrame(rows)


def test_pivot_produces_wide_frame_with_expected_columns(monkeypatch):
    monkeypatch.setattr(dataset, "_load_features_eav",
                        lambda **kwargs: _eav_fixture())
    monkeypatch.setattr(dataset, "_load_bars",
                        lambda **kwargs: _bars_fixture())

    df = dataset.load_research_frame(
        tickers=["A", "B"],
        date_min=date(2026, 1, 1),
        date_max=date(2026, 1, 31),
        enforce_session_hours=False,
        drop_warmup_bars=0,
    )
    assert {"rsi_14", "relative_volume", "regime_label",
            "open", "close",
            "ticker", "bar_open_ts_ns", "bar_date"} <= set(df.columns)


def test_pivot_has_no_duplicate_keys(monkeypatch):
    monkeypatch.setattr(dataset, "_load_features_eav",
                        lambda **kwargs: _eav_fixture())
    monkeypatch.setattr(dataset, "_load_bars",
                        lambda **kwargs: _bars_fixture())
    df = dataset.load_research_frame(
        tickers=["A", "B"],
        date_min=date(2026, 1, 1),
        date_max=date(2026, 1, 31),
        enforce_session_hours=False,
        drop_warmup_bars=0,
    )
    assert df.duplicated(["ticker", "bar_open_ts_ns"]).sum() == 0


def test_join_aligns_on_ticker_and_ts(monkeypatch):
    monkeypatch.setattr(dataset, "_load_features_eav",
                        lambda **kwargs: _eav_fixture())
    monkeypatch.setattr(dataset, "_load_bars",
                        lambda **kwargs: _bars_fixture())
    df = dataset.load_research_frame(
        tickers=["A", "B"],
        date_min=date(2026, 1, 1),
        date_max=date(2026, 1, 31),
        enforce_session_hours=False,
        drop_warmup_bars=0,
    )
    assert df["close"].notna().all()
    assert df["rsi_14"].notna().all()


def test_session_hours_filter_drops_pre_open(monkeypatch):
    eav = _eav_fixture()
    bars = _bars_fixture()
    pre_open_ts = 1_699_999_000_000_000_000
    extra_eav = pd.DataFrame([{
        "ticker": "A", "bar_open_ts_ns": pre_open_ts,
        "bar_date": date(2026, 1, 5), "interval_sec": 900,
        "feature_name": "rsi_14", "feature_value": 30.0,
        "feature_set_version": "v1",
    }])
    extra_bars = pd.DataFrame([{
        "ticker": "A", "bar_open_ts_ns": pre_open_ts,
        "bar_date": date(2026, 1, 5), "interval_sec": 900,
        "open": 100, "high": 101, "low": 99, "close": 100,
        "volume": 1000, "atr_14": 1.0,
    }])
    monkeypatch.setattr(dataset, "_load_features_eav",
                        lambda **kwargs: pd.concat([eav, extra_eav]))
    monkeypatch.setattr(dataset, "_load_bars",
                        lambda **kwargs: pd.concat([bars, extra_bars]))
    df = dataset.load_research_frame(
        tickers=["A", "B"],
        date_min=date(2026, 1, 1),
        date_max=date(2026, 1, 31),
        enforce_session_hours=True,
        drop_warmup_bars=0,
    )
    assert (df["bar_open_ts_ns"] == pre_open_ts).sum() == 0
