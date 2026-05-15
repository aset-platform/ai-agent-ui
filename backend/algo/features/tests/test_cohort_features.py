"""FE-8 cohort-pass tests for
:func:`backend.algo.features.compute_intraday_features_for_universe`.

Four cross-sectional features are layered onto the per-ticker
panel:

* ``rs_vs_nifty_15m``
* ``rs_vs_sector_15m``
* ``market_breadth_pct_above_sma200``
* ``advance_decline_ratio``

Tests verify skip-emission contract (features absent — never
``None`` / ``NaN``) and that non-FE-8 features stay byte-identical
relative to the pre-FE-8 invocation.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from backend.algo.backtest.types import BarData
from backend.algo.features import compute_intraday_features_for_universe

IST = timezone(timedelta(minutes=330))


def _ts_ns(day: date, hour: int, minute: int) -> int:
    dt = datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)
    return int(dt.astimezone(timezone.utc).timestamp() * 1_000_000_000)


def _bar(
    day: date,
    hour: int,
    minute: int,
    close: Decimal,
    *,
    ticker: str = "T.NS",
    volume: int = 1000,
) -> BarData:
    return BarData(
        ticker=ticker,
        date=day,
        open=close - Decimal("0.5"),
        high=close + Decimal("0.5"),
        low=close - Decimal("1.0"),
        close=close,
        volume=volume,
        bar_open_ts_ns=_ts_ns(day, hour, minute),
    )


def _series(
    ticker: str,
    closes: list[Decimal],
    day: date = date(2026, 4, 1),
) -> list[BarData]:
    """Bars at 15-minute spacing from 09:15 IST on ``day``."""
    out: list[BarData] = []
    for i, c in enumerate(closes):
        total_min = 9 * 60 + 15 + i * 15
        hour, minute = divmod(total_min, 60)
        out.append(_bar(day, hour, minute, c, ticker=ticker))
    return out


# ────────────────────────────────────────────────────────────────
# rs_vs_nifty_15m
# ────────────────────────────────────────────────────────────────


def test_rs_vs_nifty_15m_happy_path():
    """RS = stock_ret - nifty_ret per bar. Use 2 tickers, 5 bars,
    NIFTY 50 ramping linearly so each stock_ret − nifty_ret is
    a tight closed-form Decimal."""
    stock_a = _series("A.NS", [Decimal(100 + i) for i in range(5)])
    stock_b = _series("B.NS", [Decimal(200 + 2 * i) for i in range(5)])
    nifty = _series("NIFTY 50", [Decimal(1000 + 5 * i) for i in range(5)])

    panel = compute_intraday_features_for_universe(
        {"A.NS": stock_a, "B.NS": stock_b},
        index_bars_by_symbol={"NIFTY 50": nifty},
    )
    # Bar 0 — no prev bar — feature absent.
    ts0 = stock_a[0].bar_open_ts_ns
    assert "rs_vs_nifty_15m" not in panel["A.NS"][ts0]
    assert "rs_vs_nifty_15m" not in panel["B.NS"][ts0]

    # Bar 1: A: (101/100 - 1) - (1005/1000 - 1) = 0.01 - 0.005 = 0.005
    ts1 = stock_a[1].bar_open_ts_ns
    expected_a = (Decimal("101") / Decimal("100") - Decimal("1")) - (
        Decimal("1005") / Decimal("1000") - Decimal("1")
    )
    assert panel["A.NS"][ts1]["rs_vs_nifty_15m"] == expected_a

    # Bar 1: B: (202/200 - 1) - (1005/1000 - 1) = 0.01 - 0.005 = 0.005
    expected_b = (Decimal("202") / Decimal("200") - Decimal("1")) - (
        Decimal("1005") / Decimal("1000") - Decimal("1")
    )
    assert panel["B.NS"][ts1]["rs_vs_nifty_15m"] == expected_b


# ────────────────────────────────────────────────────────────────
# rs_vs_sector_15m
# ────────────────────────────────────────────────────────────────


def test_rs_vs_sector_15m_uses_mapped_sector():
    """Ticker mapped to ``"NIFTY IT"``; feature emits with the IT
    index return as the baseline."""
    stock = _series("INFY.NS", [Decimal(100 + i) for i in range(4)])
    nifty = _series("NIFTY 50", [Decimal(1000) for _ in range(4)])
    nifty_it = _series(
        "NIFTY IT",
        [Decimal(500), Decimal(505), Decimal(510), Decimal(515)],
    )

    panel = compute_intraday_features_for_universe(
        {"INFY.NS": stock},
        index_bars_by_symbol={
            "NIFTY 50": nifty,
            "NIFTY IT": nifty_it,
        },
        ticker_to_sector_index={"INFY.NS": "NIFTY IT"},
    )
    ts1 = stock[1].bar_open_ts_ns
    expected = (Decimal("101") / Decimal("100") - Decimal("1")) - (
        Decimal("505") / Decimal("500") - Decimal("1")
    )
    assert panel["INFY.NS"][ts1]["rs_vs_sector_15m"] == expected


def test_rs_vs_sector_15m_absent_for_unmapped_ticker():
    """Ticker not in ``ticker_to_sector_index`` → feature absent;
    other features still emit (notably ``rs_vs_nifty_15m`` since
    NIFTY 50 is present)."""
    stock = _series("UNKNOWN.NS", [Decimal(100 + i) for i in range(3)])
    nifty = _series("NIFTY 50", [Decimal(1000 + i) for i in range(3)])

    panel = compute_intraday_features_for_universe(
        {"UNKNOWN.NS": stock},
        index_bars_by_symbol={"NIFTY 50": nifty},
        ticker_to_sector_index={},  # empty — no mapping
    )
    ts1 = stock[1].bar_open_ts_ns
    feats = panel["UNKNOWN.NS"][ts1]
    assert "rs_vs_sector_15m" not in feats
    # but rs_vs_nifty_15m still computes
    assert "rs_vs_nifty_15m" in feats
    # and per-ticker features still present
    assert "today_ltp" in feats


# ────────────────────────────────────────────────────────────────
# market_breadth_pct_above_sma200
# ────────────────────────────────────────────────────────────────


def _build_long_series_for_sma200(
    ticker: str,
    final_close: Decimal,
    n_bars: int = 250,
) -> list[BarData]:
    """Build a 250-bar series where the LAST close equals
    ``final_close`` and earlier closes ramp toward it from a stable
    baseline of 100, so SMA(200) settles around 100 by the end.
    """
    bars: list[BarData] = []
    # First 200 bars at constant 100, then ramp to final_close.
    closes: list[Decimal] = [Decimal("100")] * 200
    # Last 50 bars linear ramp from 100 to final_close.
    for i in range(n_bars - 200):
        frac = Decimal(i + 1) / Decimal(n_bars - 200)
        closes.append(
            Decimal("100") + (final_close - Decimal("100")) * frac,
        )
    # 250 bars / 25 bars-per-day = 10 days @ 15m cadence.
    for i, c in enumerate(closes):
        day_idx = i // 25
        bar_in_day = i % 25
        day = date(2026, 4, 1) + timedelta(days=day_idx)
        total_min = 9 * 60 + 15 + bar_in_day * 15
        hour, minute = divmod(total_min, 60)
        bars.append(_bar(day, hour, minute, c, ticker=ticker))
    return bars


def test_market_breadth_pct_above_sma200():
    """4-ticker cohort, 250 bars each so SMA(200) settles. 2
    tickers end above their SMA(200) and 2 end below → 50.0%."""
    above_a = _build_long_series_for_sma200("ABOVE_A.NS", Decimal("120"))
    above_b = _build_long_series_for_sma200("ABOVE_B.NS", Decimal("110"))
    below_a = _build_long_series_for_sma200("BELOW_A.NS", Decimal("80"))
    below_b = _build_long_series_for_sma200("BELOW_B.NS", Decimal("90"))

    panel = compute_intraday_features_for_universe(
        {
            "ABOVE_A.NS": above_a,
            "ABOVE_B.NS": above_b,
            "BELOW_A.NS": below_a,
            "BELOW_B.NS": below_b,
        },
        index_bars_by_symbol={},  # no nifty → no RS, fine
    )
    # Pick the LAST bar — by then all 4 tickers have sma_200.
    final_ts = above_a[-1].bar_open_ts_ns
    feats_a = panel["ABOVE_A.NS"][final_ts]
    assert "market_breadth_pct_above_sma200" in feats_a
    assert feats_a["market_breadth_pct_above_sma200"] == Decimal("50")
    # Same value on every ticker at the same ts_ns (cohort feature).
    for tk in ("ABOVE_B.NS", "BELOW_A.NS", "BELOW_B.NS"):
        assert (
            panel[tk][final_ts]["market_breadth_pct_above_sma200"]
            == Decimal("50")
        )


def test_market_breadth_absent_during_sma200_warmup():
    """At bar #100 (sma_200 not settled for any ticker), the
    breadth feature is absent."""
    series_a = _series(
        "A.NS",
        [Decimal(100 + i) for i in range(50)],
    )
    series_b = _series(
        "B.NS",
        [Decimal(200 + i) for i in range(50)],
    )
    panel = compute_intraday_features_for_universe(
        {"A.NS": series_a, "B.NS": series_b},
        index_bars_by_symbol={},
    )
    for ts_ns in (b.bar_open_ts_ns for b in series_a):
        assert (
            "market_breadth_pct_above_sma200"
            not in panel["A.NS"][ts_ns]
        )


# ────────────────────────────────────────────────────────────────
# advance_decline_ratio
# ────────────────────────────────────────────────────────────────


def test_advance_decline_ratio_happy_path():
    """4 tickers: 3 up + 1 down on the SAME bar transition →
    ratio = 3 / 1 = 3.0. Build minimal 2-bar series."""
    up_a = _series("UP_A.NS", [Decimal("100"), Decimal("101")])
    up_b = _series("UP_B.NS", [Decimal("200"), Decimal("202")])
    up_c = _series("UP_C.NS", [Decimal("50"), Decimal("51")])
    down_a = _series("DOWN_A.NS", [Decimal("100"), Decimal("99")])

    panel = compute_intraday_features_for_universe(
        {
            "UP_A.NS": up_a,
            "UP_B.NS": up_b,
            "UP_C.NS": up_c,
            "DOWN_A.NS": down_a,
        },
        index_bars_by_symbol={},
    )
    ts1 = up_a[1].bar_open_ts_ns
    # 3 advancers / 1 decliner = 3.
    assert panel["UP_A.NS"][ts1]["advance_decline_ratio"] == Decimal("3")
    # Same value on every ticker at the same ts_ns.
    assert panel["DOWN_A.NS"][ts1]["advance_decline_ratio"] == Decimal("3")


def test_advance_decline_ratio_absent_when_no_decliners():
    """All tickers advance → decliners = 0 → feature absent
    (div-by-zero guard, skip-emission not NaN)."""
    a = _series("A.NS", [Decimal("100"), Decimal("101")])
    b = _series("B.NS", [Decimal("200"), Decimal("202")])
    panel = compute_intraday_features_for_universe(
        {"A.NS": a, "B.NS": b},
        index_bars_by_symbol={},
    )
    ts1 = a[1].bar_open_ts_ns
    assert "advance_decline_ratio" not in panel["A.NS"][ts1]
    assert "advance_decline_ratio" not in panel["B.NS"][ts1]


# ────────────────────────────────────────────────────────────────
# Engine integration — kwargs + byte-identity
# ────────────────────────────────────────────────────────────────


def test_no_index_bars_kwarg_drops_all_4_features():
    """Call with ``index_bars_by_symbol=None`` (default): every
    FE-8 feature absent; non-FE-8 features still emit."""
    stock = _series("A.NS", [Decimal(100 + i) for i in range(5)])
    panel = compute_intraday_features_for_universe(
        {"A.NS": stock},
        # index_bars_by_symbol defaults to None
    )
    for b in stock:
        feats = panel["A.NS"][b.bar_open_ts_ns]
        assert "rs_vs_nifty_15m" not in feats
        assert "rs_vs_sector_15m" not in feats
        assert "market_breadth_pct_above_sma200" not in feats
        assert "advance_decline_ratio" not in feats
        # per-bar features still emit
        assert "today_ltp" in feats


def test_phase_a_byte_identical_for_non_cohort_keys():
    """The Phase-A per-ticker output for every NON-FE-8 feature
    key must be byte-identical between
    ``index_bars_by_symbol=None`` and an empty cohort pass — i.e.
    the FE-8 wiring is purely additive."""
    stock = _series("A.NS", [Decimal(100 + i) for i in range(10)])
    nocohort = compute_intraday_features_for_universe(
        {"A.NS": stock},
    )
    withcohort = compute_intraday_features_for_universe(
        {"A.NS": stock},
        index_bars_by_symbol={},  # empty — A/D + breadth still
        # compute (cohort is just A.NS), but no NIFTY → no RS.
    )
    fe8_keys = {
        "rs_vs_nifty_15m",
        "rs_vs_sector_15m",
        "market_breadth_pct_above_sma200",
        "advance_decline_ratio",
    }
    for ts_ns in nocohort["A.NS"]:
        feats_no = nocohort["A.NS"][ts_ns]
        feats_with = withcohort["A.NS"][ts_ns]
        non_fe8_no = {k: v for k, v in feats_no.items() if k not in fe8_keys}
        non_fe8_with = {
            k: v for k, v in feats_with.items() if k not in fe8_keys
        }
        assert non_fe8_no == non_fe8_with


def test_decimal_precision_preserved():
    """RS computation must flow through Decimal arithmetic — no
    float coercion in the engine."""
    stock = _series("A.NS", [Decimal("100"), Decimal("100.01")])
    nifty = _series(
        "NIFTY 50",
        [Decimal("1000"), Decimal("1000.10")],
    )
    panel = compute_intraday_features_for_universe(
        {"A.NS": stock},
        index_bars_by_symbol={"NIFTY 50": nifty},
    )
    ts1 = stock[1].bar_open_ts_ns
    rs = panel["A.NS"][ts1]["rs_vs_nifty_15m"]
    assert isinstance(rs, Decimal)
    expected = (
        Decimal("100.01") / Decimal("100") - Decimal("1")
    ) - (Decimal("1000.10") / Decimal("1000") - Decimal("1"))
    assert rs == expected
