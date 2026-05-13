"""Tests for ``preload_intraday_bars`` (ASETPLTFRM-392).

Mirrors the shape of ``test_daily_bar_warmup.py``:

  1. Empty universe → empty dict, no I/O.
  2. Iceberg returns >= n_bars → no Kite call.
  3. Iceberg underfilled → Kite fallback used; densest merged in.
  4. Mixed full / underfilled → Kite called only for the gaps.
  5. Iceberg read failure → graceful fall-through to Kite.
  6. No Kite client + underfill → warning logged, partial returned.
  7. n_bars slicing: Iceberg over-returns → output truncated.
  8. Invalid interval_sec → ValueError at the boundary.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from backend.algo.backtest.types import BarData
from backend.algo.live.intraday_bar_warmup import (
    DEFAULT_INTRADAY_WARMUP_BARS,
    INTERVAL_SEC_BY_LABEL,
    preload_intraday_bars,
)


_TODAY = date(2026, 5, 13)


def _bar(
    ticker: str,
    d: date,
    open_ns: int,
    close: float = 100.0,
) -> BarData:
    c = Decimal(str(close))
    return BarData(
        ticker=ticker,
        date=d,
        open=c,
        high=c + Decimal("1"),
        low=c - Decimal("1"),
        close=c,
        volume=1_000,
        bar_open_ts_ns=open_ns,
    )


def _series(
    ticker: str,
    end_date: date,
    n: int,
    interval_sec: int = 300,
) -> list[BarData]:
    """Build ``n`` ascending intraday bars ending at ``end_date``
    (multiple bars share the same date — that's the whole point of
    intraday)."""
    # Anchor at 09:15:00 IST = 03:45:00 UTC.
    start_dt_utc_ns = int(
        (end_date.toordinal() - date(1970, 1, 1).toordinal())
        * 86_400 * 1_000_000_000
        + 3 * 3600 * 1_000_000_000
        + 45 * 60 * 1_000_000_000
    )
    return [
        _bar(
            ticker, end_date,
            open_ns=start_dt_utc_ns + i * interval_sec * 1_000_000_000,
            close=100.0 + i * 0.1,
        )
        for i in range(n)
    ]


def _fake_iceberg_reader(
    payload: dict[str, list[BarData]],
):
    """Helper: build an iceberg_reader closure returning ``payload``."""
    def _reader(tickers, interval_sec, window_start, today):
        return {t: payload.get(t, []) for t in tickers}
    return _reader


class TestPreloadHappyPaths:
    def test_empty_universe_returns_empty_dict(self):
        out = preload_intraday_bars(
            tickers=[],
            interval_sec=300,
        )
        assert out == {}

    def test_iceberg_full_no_kite_call(self):
        """Every ticker fresh in Iceberg → Kite never called."""
        n = DEFAULT_INTRADAY_WARMUP_BARS
        payload = {
            "ITC.NS": _series("ITC.NS", _TODAY, n, 300),
            "TCS.NS": _series("TCS.NS", _TODAY, n, 300),
        }
        kite = MagicMock()
        kite.fetch_intraday_historical.side_effect = AssertionError(
            "Kite should not be called when Iceberg is full",
        )

        out = preload_intraday_bars(
            tickers=["ITC.NS", "TCS.NS"],
            interval_sec=300,
            kite_client=kite,
            ticker_to_token={"ITC.NS": 1, "TCS.NS": 2},
            today=_TODAY,
            iceberg_reader=_fake_iceberg_reader(payload),
        )

        assert len(out["ITC.NS"]) == n
        assert len(out["TCS.NS"]) == n
        kite.fetch_intraday_historical.assert_not_called()

    def test_iceberg_under_fills_kite_fallback(self):
        """Iceberg returns < n_bars → Kite fallback fills the gap."""
        partial = _series("ITC.NS", _TODAY, 20, 300)
        kite_bars = _series("ITC.NS", _TODAY, 100, 300)
        kite = MagicMock()
        kite.fetch_intraday_historical.return_value = kite_bars

        out = preload_intraday_bars(
            tickers=["ITC.NS"],
            interval_sec=300,
            kite_client=kite,
            ticker_to_token={"ITC.NS": 1},
            today=_TODAY,
            iceberg_reader=_fake_iceberg_reader(
                {"ITC.NS": partial},
            ),
        )

        # Densest source wins.
        assert len(out["ITC.NS"]) == 100
        kite.fetch_intraday_historical.assert_called_once()
        call = kite.fetch_intraday_historical.call_args.kwargs
        assert call["interval_sec"] == 300
        assert call["n_bars"] == DEFAULT_INTRADAY_WARMUP_BARS

    def test_mixed_full_and_underfilled(self):
        n = DEFAULT_INTRADAY_WARMUP_BARS
        full = _series("ITC.NS", _TODAY, n, 300)
        partial = _series("TCS.NS", _TODAY, 5, 300)
        kite_bars = _series("TCS.NS", _TODAY, n, 300)

        kite = MagicMock()
        kite.fetch_intraday_historical.return_value = kite_bars

        out = preload_intraday_bars(
            tickers=["ITC.NS", "TCS.NS"],
            interval_sec=300,
            kite_client=kite,
            ticker_to_token={"ITC.NS": 1, "TCS.NS": 2},
            today=_TODAY,
            iceberg_reader=_fake_iceberg_reader(
                {"ITC.NS": full, "TCS.NS": partial},
            ),
        )

        # ITC: pure Iceberg path
        assert len(out["ITC.NS"]) == n
        # TCS: Kite fallback densified
        assert len(out["TCS.NS"]) == n
        # Kite called once, only for TCS
        kite.fetch_intraday_historical.assert_called_once()
        call = kite.fetch_intraday_historical.call_args.kwargs
        assert call["ticker"] == "TCS.NS"


class TestPreloadDegradedPaths:
    def test_iceberg_failure_falls_back_to_kite(self):
        """Iceberg raising → all tickers underfilled → Kite called
        for each one with a token."""
        kite = MagicMock()
        kite.fetch_intraday_historical.return_value = _series(
            "ITC.NS", _TODAY, 100, 300,
        )

        def _bad_reader(*_args, **_kwargs):
            raise RuntimeError("simulated DuckDB outage")

        out = preload_intraday_bars(
            tickers=["ITC.NS"],
            interval_sec=300,
            kite_client=kite,
            ticker_to_token={"ITC.NS": 1},
            today=_TODAY,
            iceberg_reader=_bad_reader,
        )

        assert len(out["ITC.NS"]) == 100
        kite.fetch_intraday_historical.assert_called_once()

    def test_no_kite_client_underfilled_warns_and_returns_partial(
        self, caplog,
    ):
        """No Kite client + sparse Iceberg → warning logged, partial
        bars returned (does NOT raise — strategies on sparse tickers
        silent-skip indicators)."""
        partial = _series("ITC.NS", _TODAY, 5, 300)

        with caplog.at_level("WARNING"):
            out = preload_intraday_bars(
                tickers=["ITC.NS"],
                interval_sec=300,
                kite_client=None,
                ticker_to_token=None,
                today=_TODAY,
                iceberg_reader=_fake_iceberg_reader(
                    {"ITC.NS": partial},
                ),
            )

        assert out["ITC.NS"] == partial
        assert any(
            "underfilled" in rec.message
            and "no Kite fallback" in rec.message
            for rec in caplog.records
        )

    def test_missing_ticker_to_token_skips_fallback(self):
        """Kite client present but token map is None → fallback not
        attempted (matches daily-warmup parity)."""
        partial = _series("ITC.NS", _TODAY, 5, 300)
        kite = MagicMock()

        out = preload_intraday_bars(
            tickers=["ITC.NS"],
            interval_sec=300,
            kite_client=kite,
            ticker_to_token=None,
            today=_TODAY,
            iceberg_reader=_fake_iceberg_reader(
                {"ITC.NS": partial},
            ),
        )

        assert out["ITC.NS"] == partial
        kite.fetch_intraday_historical.assert_not_called()


class TestPreloadSlicingAndValidation:
    def test_over_returned_bars_truncated_to_n(self):
        """Iceberg returns 200 bars, caller asks for 50 → only the
        most-recent 50 are kept (ascending by ts_ns)."""
        big = _series("ITC.NS", _TODAY, 200, 300)
        kite = MagicMock()  # never called

        out = preload_intraday_bars(
            tickers=["ITC.NS"],
            interval_sec=300,
            n_bars=50,
            kite_client=kite,
            ticker_to_token={"ITC.NS": 1},
            today=_TODAY,
            iceberg_reader=_fake_iceberg_reader({"ITC.NS": big}),
        )

        assert len(out["ITC.NS"]) == 50
        # Tail preserved — close prices climb linearly so the last
        # bar carries the highest close.
        assert out["ITC.NS"][-1].close == big[-1].close
        kite.fetch_intraday_historical.assert_not_called()

    @pytest.mark.parametrize(
        "bad", [30, 120, 600, 1800, 3600],
    )
    def test_invalid_interval_sec_rejected_at_boundary(
        self, bad,
    ):
        with pytest.raises(ValueError, match="not supported"):
            preload_intraday_bars(
                tickers=["ITC.NS"],
                interval_sec=bad,
            )

    def test_all_supported_intervals_pass_validation(self):
        """1m / 5m / 15m all accepted — matches the AST literal."""
        for sec in INTERVAL_SEC_BY_LABEL.values():
            out = preload_intraday_bars(
                tickers=["ITC.NS"],
                interval_sec=sec,
                today=_TODAY,
                iceberg_reader=_fake_iceberg_reader({"ITC.NS": []}),
            )
            assert out == {"ITC.NS": []}
