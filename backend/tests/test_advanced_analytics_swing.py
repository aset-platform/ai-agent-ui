"""Tests for swing-setups feature."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import pytest

from advanced_analytics_models import AdvancedRow
from advanced_analytics_routes import (
    _death_cross_days_ago,
    _rolling_band_20d_prev,
    _rsi_lookback,
)


def test_advanced_row_swing_fields_default_none() -> None:
    """New swing fields default to None for back-compat with the
    seven existing AA reports that don't populate them.
    """
    row = AdvancedRow(ticker="TCS.NS")
    assert row.death_cross_days_ago is None
    assert row.rolling_low_20d_prev is None
    assert row.rolling_high_20d_prev is None
    assert row.rsi_3d_ago is None
    assert row.rsi_max_10d is None
    assert row.rec_category is None
    assert row.rec_severity is None
    assert row.rec_expected_return_pct is None


def _make_sma_df(s50: list[float], s200: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"SMA_50": s50, "SMA_200": s200})


def test_death_cross_none_when_50_above_200() -> None:
    """No death cross active when SMA-50 is above SMA-200 today."""
    df = _make_sma_df([100, 102, 104], [99, 100, 101])
    assert _death_cross_days_ago(df) is None


def test_death_cross_zero_when_today_is_cross() -> None:
    """Cross today → 0."""
    df = _make_sma_df([100, 101, 99], [98, 100, 100])
    # Yesterday: 50=101 > 200=100. Today: 50=99 < 200=100 → cross today.
    assert _death_cross_days_ago(df) == 0


def test_death_cross_n_days_back() -> None:
    """Cross 2 trading days back → 2."""
    df = _make_sma_df([101, 99, 98, 97], [100, 100, 99, 98])
    # Index 0→1 is cross (101>100 then 99<100). Today (i=3): 97<98.
    # n=4, cross at i=1 → (4-1)-1 = 2.
    assert _death_cross_days_ago(df) == 2


def test_death_cross_sentinel_when_below_entire_window() -> None:
    """SMA-50 below SMA-200 entire window → established sentinel."""
    from advanced_analytics_models import ESTABLISHED_CROSS_DAYS

    df = _make_sma_df([90, 91, 92], [100, 101, 102])
    assert _death_cross_days_ago(df) == ESTABLISHED_CROSS_DAYS


def test_death_cross_handles_nan_prefix() -> None:
    """NaN prefix (insufficient warmup) → established sentinel."""
    from advanced_analytics_models import ESTABLISHED_CROSS_DAYS

    df = _make_sma_df([np.nan, np.nan, 95], [np.nan, np.nan, 100])
    assert _death_cross_days_ago(df) == ESTABLISHED_CROSS_DAYS


def test_death_cross_missing_columns_returns_none() -> None:
    """Missing SMA columns return None safely."""
    df = pd.DataFrame({"close": [100, 101]})
    assert _death_cross_days_ago(df) is None


def test_rolling_band_20d_prev_basic() -> None:
    """20-day rolling band excludes today."""
    # 21 rows: index 0..19 used for the band, index 20 is "today".
    lows = list(range(10, 30)) + [5]  # today_low = 5 (below band)
    highs = list(range(20, 40)) + [50]  # today_high = 50 (above)
    df = pd.DataFrame({"low": lows, "high": highs})
    low, high = _rolling_band_20d_prev(df)
    assert low == 10  # min of 10..29 (indices 0..19)
    assert high == 39  # max of 20..39 (indices 0..19)


def test_rolling_band_short_history_returns_none() -> None:
    """Fewer than 21 rows → cannot exclude today, returns (None, None)."""
    df = pd.DataFrame({
        "low": [10, 11, 12],
        "high": [15, 16, 17],
    })
    assert _rolling_band_20d_prev(df) == (None, None)


def test_rolling_band_handles_nan() -> None:
    """NaN low/high values are ignored in min/max."""
    lows = [float("nan")] * 5 + list(range(10, 25)) + [5]
    highs = [float("nan")] * 5 + list(range(20, 35)) + [50]
    df = pd.DataFrame({"low": lows, "high": highs})
    low, high = _rolling_band_20d_prev(df)
    assert low == 10
    assert high == 34


def test_rsi_lookback_basic() -> None:
    """RSI lookback: today, 3-days-ago, max over last 10."""
    rsi_series = pd.Series(
        [40, 45, 50, 55, 60, 65, 70, 68, 60, 50, 45]
    )
    df = pd.DataFrame({"RSI_14": rsi_series})
    today, three_ago, max_10 = _rsi_lookback(df)
    assert today == 45
    assert three_ago == 68  # index -4 (3 trading days before today)
    assert max_10 == 70  # max over last 10 rows


def test_rsi_lookback_short_series_returns_partial_nones() -> None:
    """<4 rows → three_ago None; <10 rows → max_10 still computes
    over available rows."""
    df = pd.DataFrame({"RSI_14": [40, 50, 60]})
    today, three_ago, max_10 = _rsi_lookback(df)
    assert today == 60
    assert three_ago is None
    assert max_10 == 60  # max of the 3 available


def test_rsi_lookback_missing_column_returns_all_none() -> None:
    df = pd.DataFrame({"close": [100, 101]})
    assert _rsi_lookback(df) == (None, None, None)


def test_rsi_lookback_handles_nan() -> None:
    """NaN today returns None for today; lookback unaffected."""
    df = pd.DataFrame({
        "RSI_14": [40, 50, 60, 65, 55, float("nan")],
    })
    today, three_ago, max_10 = _rsi_lookback(df)
    assert today is None
    assert three_ago == 60
    assert max_10 == 65


def test_build_row_populates_swing_computed_cols() -> None:
    """Integration: _build_row stamps the 5 swing computed cols +
    today_low when ohlcv_g + indicators dict carry the right inputs.
    """
    import advanced_analytics_routes as aar

    n = 30
    dates = pd.date_range("2026-04-01", periods=n).date
    ohlcv_g = pd.DataFrame({
        "date": dates,
        "open": [100.0] * n,
        "high": [105.0 + i for i in range(n)],
        "low": [95.0 - i * 0.1 for i in range(n)],
        "close": [100.0 + i * 0.5 for i in range(n)],
        "volume": [1_000_000] * n,
    })

    # Indicators dict — mirror contract _load_indicators_latest emits.
    from advanced_analytics_models import ESTABLISHED_CROSS_DAYS

    indicators = {
        "rsi_14": 55.0,
        "sma_50": 110.0,
        "sma_200": 105.0,
        "golden_cross_days_ago": ESTABLISHED_CROSS_DAYS,
        # Pre-computed by _load_indicators_latest (Task 5 extension):
        "death_cross_days_ago": None,
        "rsi_3d_ago": 60.0,
        "rsi_max_10d": 65.0,
    }

    row = aar._build_row(
        ticker="TCS.NS",
        ohlcv_g=ohlcv_g,
        delivery_g=None,
        indicators=indicators,
        funds=None,
        prom=None,
        event=None,
        pscore=None,
        company=None,
    )

    # Today snapshot — last row of OHLCV.
    assert row.today_low == pytest.approx(95.0 - 29 * 0.1)
    # Indicator-dict-derived fields.
    assert row.death_cross_days_ago is None
    assert row.rsi_3d_ago == 60.0
    assert row.rsi_max_10d == 65.0
    # OHLCV-derived band (computed inside _build_row).
    # Window: indices 9..28 (last 20 before today).
    # low = min(95 - i*0.1) for i in 9..28 → at i=28 → 95 - 2.8 = 92.2.
    # high = max(105 + i) for i in 9..28 → at i=28 → 133.
    assert row.rolling_low_20d_prev == pytest.approx(92.2)
    assert row.rolling_high_20d_prev == pytest.approx(133.0)


from advanced_analytics_swing import (
    BULLISH_CATEGORIES,
    REGIMES,
    build_methodology,
)


def test_regimes_constant() -> None:
    """Regime literal set is exactly the three published values."""
    assert REGIMES == ("bull", "sideways", "bearish")


def test_bullish_categories_non_empty() -> None:
    """The bullish set is pinned at module level (Task 0)."""
    assert len(BULLISH_CATEGORIES) >= 2
    assert all(isinstance(c, str) for c in BULLISH_CATEGORIES)


def test_build_methodology_bull_shape() -> None:
    """Bull methodology has all required sub-fields and >= 8 gates."""
    m = build_methodology("bull")
    assert m["regime"] == "bull"
    assert isinstance(m["summary"], str) and len(m["summary"]) > 20
    assert isinstance(m["gates"], list)
    assert len(m["gates"]) >= 8
    for g in m["gates"]:
        assert "label" in g and "rule" in g and "why" in g
        assert g["rule"]  # non-empty
    assert "formula" in m["rank"]
    assert m["rank"]["direction"] == "DESC"
    assert m["rank"]["cap"] == 25


def test_build_methodology_sideways_shape() -> None:
    m = build_methodology("sideways")
    assert m["regime"] == "sideways"
    assert len(m["gates"]) >= 6
    assert m["rank"]["direction"] == "ASC"


def test_build_methodology_bearish_shape() -> None:
    m = build_methodology("bearish")
    assert m["regime"] == "bearish"
    assert len(m["gates"]) >= 5
    assert m["rank"]["direction"] == "DESC"


def test_build_methodology_unknown_regime_raises() -> None:
    import pytest

    with pytest.raises(ValueError):
        build_methodology("noisy")  # type: ignore[arg-type]


from advanced_analytics_models import (
    ESTABLISHED_CROSS_DAYS as _ESTABLISHED,
)
from advanced_analytics_swing import (
    passes_bull,
    rank_bull,
)


def _bull_row(**overrides: Any) -> AdvancedRow:
    base = dict(
        ticker="TCS.NS",
        today_ltp=120.0,
        sma_50=110.0,
        sma_200=100.0,
        golden_cross_days_ago=_ESTABLISHED,
        today_x_vol=3.0,
        current_dpc=55.0,
        avg_20d_dpc=45.0,
        x_dv_20d=1.5,
        rsi=60.0,
        pscore=7,
        pledged=2.0,
        week_52_high=150.0,
        rec_category="offensive",
        rec_severity="high",
        rec_expected_return_pct=12.0,
    )
    base.update(overrides)
    return AdvancedRow(**base)  # type: ignore[arg-type]


def test_passes_bull_happy_path() -> None:
    assert passes_bull(_bull_row(), rec_gate_applied=True) is True


def test_passes_bull_rejects_volume_above_band() -> None:
    assert passes_bull(_bull_row(today_x_vol=6.0), True) is False


def test_passes_bull_rejects_volume_below_band() -> None:
    assert passes_bull(_bull_row(today_x_vol=1.5), True) is False


def test_passes_bull_rejects_delivery_below_avg() -> None:
    assert (
        passes_bull(
            _bull_row(current_dpc=40.0, avg_20d_dpc=45.0), True
        ) is False
    )


def test_passes_bull_rejects_at_52w_high() -> None:
    assert (
        passes_bull(
            _bull_row(today_ltp=149.0, week_52_high=150.0), True
        ) is False
    )


def test_passes_bull_rejects_rsi_overbought() -> None:
    assert passes_bull(_bull_row(rsi=75.0), True) is False


def test_passes_bull_rejects_low_pscore() -> None:
    assert passes_bull(_bull_row(pscore=3), True) is False


def test_passes_bull_rejects_high_pledged() -> None:
    assert passes_bull(_bull_row(pledged=15.0), True) is False


def test_passes_bull_accepts_null_pledged() -> None:
    """promoter_holdings is sparse — NULL pledged tolerated.
    Only positive distress signal (pledged >= 10) rejects."""
    assert passes_bull(_bull_row(pledged=None), True) is True


def test_passes_bull_accepts_fresh_golden_cross_without_stack() -> None:
    """If SMA stack fails but golden_cross is fresh, gate passes."""
    row = _bull_row(
        today_ltp=105.0, sma_50=110.0, sma_200=108.0,
        golden_cross_days_ago=5,
    )
    assert passes_bull(row, True) is True


def test_passes_bull_accepts_established_with_pullback() -> None:
    """Long-confirmed uptrend (gxa = ESTABLISHED_CROSS_DAYS) with
    today's price pulled back to / below SMA-50 — buy-the-dip
    case. Symmetric to the bearish established-sentinel acceptance.
    Without this branch, names that have been bullish for 215+
    days but happen to be in a normal pullback are silently
    rejected as if they weren't in an uptrend at all.
    """
    row = _bull_row(
        # Price has pulled back BELOW SMA-50 (stack fails).
        today_ltp=105.0, sma_50=110.0, sma_200=100.0,
        # Long-confirmed uptrend — SMA-50 > SMA-200 entire window.
        golden_cross_days_ago=_ESTABLISHED,
    )
    assert passes_bull(row, True) is True


def test_passes_bull_rejects_stale_mid_window_cross() -> None:
    """Mid-window stale cross (31-214 days) still rejects when
    SMA stack also fails — neither fresh nor established."""
    row = _bull_row(
        today_ltp=105.0, sma_50=110.0, sma_200=108.0,
        golden_cross_days_ago=100,
    )
    assert passes_bull(row, True) is False


def test_passes_bull_rejects_non_bullish_category() -> None:
    """`risk_alert`, `rebalance`, `defensive` etc. are NOT in
    BULLISH_CATEGORIES."""
    assert (
        passes_bull(_bull_row(rec_category="risk_alert"), True)
        is False
    )
    assert (
        passes_bull(_bull_row(rec_category="rebalance"), True)
        is False
    )
    assert (
        passes_bull(_bull_row(rec_category="defensive"), True)
        is False
    )


def test_passes_bull_accepts_each_bullish_category() -> None:
    """All four pinned bullish categories pass the gate."""
    for cat in ("offensive", "value", "growth", "hold_accumulate"):
        assert passes_bull(_bull_row(rec_category=cat), True) is True


def test_passes_bull_severity_does_not_gate() -> None:
    """Phase A does NOT gate on severity; all severities pass when
    the category is bullish."""
    for sev in ("high", "medium", "low"):
        assert (
            passes_bull(_bull_row(rec_severity=sev), True) is True
        )


def test_passes_bull_skips_rec_gate_when_degraded() -> None:
    """When user has no rec run, rec-category gate is bypassed."""
    row = _bull_row(
        rec_category=None, rec_severity=None,
        rec_expected_return_pct=None,
    )
    assert passes_bull(row, rec_gate_applied=False) is True


def test_passes_bull_handles_nan_inputs() -> None:
    """NaN in any hard-gate column rejects the row (no crash)."""
    row = _bull_row(today_x_vol=float("nan"))
    assert passes_bull(row, True) is False


def test_rank_bull_uses_rec_score_when_present() -> None:
    row = _bull_row(
        rec_expected_return_pct=10.0, x_dv_20d=2.0, today_x_vol=3.0,
    )
    assert rank_bull(row, rec_gate_applied=True) == 10.0 * 2.0 * 3.0


def test_rank_bull_degrades_when_no_rec() -> None:
    row = _bull_row(
        rec_expected_return_pct=None, x_dv_20d=2.0, today_x_vol=3.0,
    )
    assert rank_bull(row, rec_gate_applied=False) == 1.0 * 2.0 * 3.0


def test_rank_bull_clamps_negative_rec_return() -> None:
    row = _bull_row(
        rec_expected_return_pct=-5.0, x_dv_20d=2.0, today_x_vol=3.0,
    )
    assert rank_bull(row, rec_gate_applied=True) == 0.0


from advanced_analytics_swing import (
    passes_sideways,
    rank_sideways,
)


def _sideways_row(
    market: str = "india", **overrides: Any
) -> AdvancedRow:
    base = dict(
        ticker="ITC.NS",
        today_ltp=100.0,
        sma_50=100.5,
        sma_200=100.0,  # |0.5|/100 = 0.005 (below 0.05)
        rsi=50.0,
        today_x_vol=1.0,
        today_not=100_000_000.0,  # ₹10cr > floor
        pscore=5,
        rolling_low_20d_prev=95.0,
        rolling_high_20d_prev=105.0,
    )
    base.update(overrides)
    return AdvancedRow(**base)  # type: ignore[arg-type]


def test_passes_sideways_happy_path() -> None:
    assert passes_sideways(_sideways_row(), market="india") is True


def test_passes_sideways_rejects_diverged_mas() -> None:
    row = _sideways_row(sma_50=110.0, sma_200=100.0)
    assert passes_sideways(row, "india") is False


def test_passes_sideways_rejects_price_far_from_sma50() -> None:
    row = _sideways_row(today_ltp=105.0, sma_50=100.0)
    assert passes_sideways(row, "india") is False


def test_passes_sideways_rejects_rsi_outside_band() -> None:
    assert passes_sideways(_sideways_row(rsi=35.0), "india") is False
    assert passes_sideways(_sideways_row(rsi=65.0), "india") is False


def test_passes_sideways_rejects_volume_surge() -> None:
    assert (
        passes_sideways(_sideways_row(today_x_vol=1.5), "india")
        is False
    )


def test_passes_sideways_rejects_volume_drought() -> None:
    assert (
        passes_sideways(_sideways_row(today_x_vol=0.5), "india")
        is False
    )


def test_passes_sideways_applies_inr_floor() -> None:
    row = _sideways_row(today_not=30_000_000.0)
    assert passes_sideways(row, "india") is False


def test_passes_sideways_applies_usd_floor_for_us_market() -> None:
    row = _sideways_row(today_not=500_000.0)
    assert passes_sideways(row, "us") is False


def test_passes_sideways_rejects_low_pscore() -> None:
    assert (
        passes_sideways(_sideways_row(pscore=3), "india") is False
    )


def test_rank_sideways_lower_at_band_edge() -> None:
    """A row near the band edge scores LOWER (closer to 0) → ranks
    higher when sorted ASC."""
    near_low = _sideways_row(
        today_ltp=96.0,
        rolling_low_20d_prev=95.0,
        rolling_high_20d_prev=105.0,
    )
    mid_band = _sideways_row(
        today_ltp=100.0,
        rolling_low_20d_prev=95.0,
        rolling_high_20d_prev=105.0,
    )
    assert rank_sideways(near_low) < rank_sideways(mid_band)


def test_rank_sideways_nan_returns_inf() -> None:
    row = _sideways_row(
        rolling_low_20d_prev=None, rolling_high_20d_prev=None,
    )
    assert rank_sideways(row) == float("inf")


# ---------------------------------------------------------------
# Task 9: passes_bearish + rank_bearish
# ---------------------------------------------------------------

from advanced_analytics_swing import (
    passes_bearish,
    rank_bearish,
)


def _bearish_row(
    market: str = "india", **overrides: Any
) -> AdvancedRow:
    base = dict(
        ticker="YESBANK.NS",
        today_ltp=20.0,
        today_low=19.5,
        sma_50=22.0,
        sma_200=25.0,
        death_cross_days_ago=10,
        rsi=45.0,
        rsi_3d_ago=55.0,
        rsi_max_10d=65.0,
        rolling_low_20d_prev=20.0,
        week_52_low=18.0,
        today_not=80_000_000.0,
    )
    base.update(overrides)
    return AdvancedRow(**base)  # type: ignore[arg-type]


def test_passes_bearish_happy_path() -> None:
    assert passes_bearish(_bearish_row(), "india") is True


def test_passes_bearish_rejects_stale_death_cross() -> None:
    """Mid-window stale cross (61-214d ago) is rejected — neither
    fresh nor the established-bearish 999 sentinel."""
    row = _bearish_row(death_cross_days_ago=100)
    assert passes_bearish(row, "india") is False


def test_passes_bearish_accepts_established_sentinel() -> None:
    """``_death_cross_days_ago`` returns
    ``ESTABLISHED_CROSS_DAYS`` when SMA-50 has been below SMA-200
    for the entire 215-row window. That's a deep, long-confirmed
    bearish state — a valid swing-short candidate. The gate must
    accept it alongside fresh crosses (≤ 60d)."""
    from advanced_analytics_models import ESTABLISHED_CROSS_DAYS

    row = _bearish_row(
        death_cross_days_ago=ESTABLISHED_CROSS_DAYS,
    )
    assert passes_bearish(row, "india") is True


def test_passes_bearish_rejects_no_death_cross() -> None:
    row = _bearish_row(
        sma_50=25.0, sma_200=22.0, death_cross_days_ago=None,
    )
    assert passes_bearish(row, "india") is False


def test_passes_bearish_rejects_weak_rsi_history() -> None:
    """RSI never reached 60 in last 10d → not a rollover."""
    row = _bearish_row(rsi_max_10d=55.0)
    assert passes_bearish(row, "india") is False


def test_passes_bearish_rejects_rsi_not_rolled_over() -> None:
    """Today's RSI still >= 50."""
    row = _bearish_row(rsi=55.0)
    assert passes_bearish(row, "india") is False


def test_passes_bearish_rejects_rsi_recovering() -> None:
    """Today's RSI > 3-days-ago → recovering, not declining."""
    row = _bearish_row(rsi=48.0, rsi_3d_ago=40.0)
    assert passes_bearish(row, "india") is False


def test_passes_bearish_rejects_no_lower_low() -> None:
    """today_low >= 20-day prev low → no decisive break."""
    row = _bearish_row(today_low=21.0, rolling_low_20d_prev=20.0)
    assert passes_bearish(row, "india") is False


def test_passes_bearish_rejects_at_52w_floor() -> None:
    row = _bearish_row(today_ltp=18.5, week_52_low=18.0)
    assert passes_bearish(row, "india") is False


def test_passes_bearish_applies_liquidity_floor() -> None:
    row = _bearish_row(today_not=10_000_000.0)
    assert passes_bearish(row, "india") is False


def test_rank_bearish_higher_for_fresher_cross() -> None:
    fresh = _bearish_row(death_cross_days_ago=2)
    stale = _bearish_row(death_cross_days_ago=50)
    assert rank_bearish(fresh) > rank_bearish(stale)


def test_rank_bearish_nan_returns_zero() -> None:
    row = _bearish_row(death_cross_days_ago=None)
    assert rank_bearish(row) == 0.0


# ---------------------------------------------------------------
# Task 10 — _load_latest_recommendations batched PG lookup
# ---------------------------------------------------------------

import asyncio

from advanced_analytics_routes import _load_latest_recommendations


def test_load_latest_recommendations_empty_for_unknown_user() -> None:
    """No rec runs for an unknown user → run_id None, recs empty."""
    fake_uuid = "00000000-0000-0000-0000-000000000000"
    result = asyncio.run(
        _load_latest_recommendations(
            user_id=fake_uuid, tickers=["TCS.NS", "ITC.NS"],
        )
    )
    assert result["run_id"] is None
    assert result["run_date"] is None
    assert result["recs"] == {}


def test_load_latest_recommendations_empty_for_empty_ticker_list() -> None:
    """Empty ticker list → degenerate empty response, no PG hit."""
    fake_uuid = "00000000-0000-0000-0000-000000000000"
    result = asyncio.run(
        _load_latest_recommendations(
            user_id=fake_uuid, tickers=[],
        )
    )
    assert result["run_id"] is None
    assert result["run_date"] is None
    assert result["recs"] == {}


async def _discover_real_user_and_tickers() -> (
    tuple[str | None, list[str]]
):
    """Use the same async PG session helper to probe for fixtures.

    Returns ``(user_id, tickers)`` or ``(None, [])`` if the DB has
    no active recs — keeping the integration test CI-safe.
    """
    from sqlalchemy import text

    from stocks.repository import _pg_session

    async with _pg_session() as session:
        result = await session.execute(
            text(
                "SELECT DISTINCT run.user_id "
                "FROM stocks.recommendation_runs run "
                "JOIN stocks.recommendations r "
                "  ON r.run_id = run.run_id "
                "WHERE r.status = 'active' "
                "  AND run.run_type != 'admin_test' "
                "LIMIT 1"
            )
        )
        row = result.fetchone()
        if row is None:
            return None, []
        real_user_id = str(row[0])

        tres = await session.execute(
            text(
                "SELECT DISTINCT ticker "
                "FROM stocks.recommendations r "
                "JOIN stocks.recommendation_runs run "
                "  ON r.run_id = run.run_id "
                "WHERE run.user_id = CAST(:uid AS UUID) "
                "  AND r.status = 'active' "
                "  AND r.ticker IS NOT NULL "
                "  AND run.run_type != 'admin_test' "
                "LIMIT 5"
            ),
            {"uid": real_user_id},
        )
        real_tickers = [r[0] for r in tres.fetchall()]
    return real_user_id, real_tickers


def test_load_latest_recommendations_returns_shape_for_real_user() -> None:
    """If any real user has an active rec run, the function returns
    the expected shape (run_id non-null, recs dict with values being
    (category, severity, expected_return_pct) tuples).

    Skipped if the DB has no active recs (CI-safe).
    """
    real_user_id, real_tickers = asyncio.run(
        _discover_real_user_and_tickers()
    )
    if real_user_id is None:
        pytest.skip("No active rec runs in DB")
    if not real_tickers:
        pytest.skip("No tickered recs for user")

    result = asyncio.run(
        _load_latest_recommendations(
            user_id=real_user_id, tickers=real_tickers,
        )
    )
    assert result["run_id"] is not None
    assert result["run_date"] is not None
    assert len(result["recs"]) >= 1
    # Spot-check one entry's shape.
    sample = next(iter(result["recs"].values()))
    assert isinstance(sample, tuple) and len(sample) == 3
    cat, sev, ret = sample
    assert cat is None or isinstance(cat, str)
    assert sev is None or isinstance(sev, str)
    assert ret is None or isinstance(ret, (int, float))


def test_apply_rec_data_populates_row_fields() -> None:
    """Stamp rec_* fields onto rows from the rec dict."""
    import advanced_analytics_routes as aar

    rows = [
        AdvancedRow(ticker="TCS.NS"),
        AdvancedRow(ticker="ITC.NS"),
    ]
    recs = {
        "TCS.NS": ("offensive", "high", 12.0),
        # ITC.NS missing — should remain None
    }
    aar._apply_rec_data(rows, recs)
    assert rows[0].rec_category == "offensive"
    assert rows[0].rec_severity == "high"
    assert rows[0].rec_expected_return_pct == 12.0
    assert rows[1].rec_category is None
    assert rows[1].rec_severity is None
    assert rows[1].rec_expected_return_pct is None


def test_apply_rec_data_empty_dict_is_noop() -> None:
    rows = [AdvancedRow(ticker="TCS.NS")]
    import advanced_analytics_routes as aar
    aar._apply_rec_data(rows, {})
    assert rows[0].rec_category is None


from advanced_analytics_models import (
    SwingMethodology,
    SwingMethodologyGate,
    SwingMethodologyRank,
    SwingSetupsResponse,
)


def test_swing_methodology_constructs() -> None:
    m = SwingMethodology(
        regime="bull",
        summary="Trend-up + demand + quality.",
        gates=[
            SwingMethodologyGate(
                label="Trend stack", rule="x>y", why="trend",
            ),
        ],
        rank=SwingMethodologyRank(
            formula="a*b", direction="DESC", cap=25, degraded=None,
        ),
    )
    assert m.regime == "bull"
    assert m.gates[0].label == "Trend stack"
    assert m.rank.direction == "DESC"


def test_swing_methodology_rejects_invalid_direction() -> None:
    """Pydantic validation rejects directions outside ASC/DESC."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SwingMethodologyRank(
            formula="a", direction="BOTH",  # type: ignore[arg-type]
            cap=25, degraded=None,
        )


def test_swing_setups_response_constructs() -> None:
    resp = SwingSetupsResponse(
        rows=[],
        total=0,
        regime="bull",
        as_of="2026-05-12",
        rec_gate_applied=False,
        rec_run_id=None,
        rec_run_date=None,
        notes=[
            "Recommendation gate not applied — no rec run this "
            "month",
        ],
        methodology=SwingMethodology(
            regime="bull", summary="x", gates=[],
            rank=SwingMethodologyRank(
                formula="a", direction="DESC", cap=25,
                degraded=None,
            ),
        ),
    )
    assert resp.regime == "bull"
    assert resp.rec_gate_applied is False
    assert resp.methodology.regime == "bull"


def test_swing_setups_response_default_notes_empty() -> None:
    """`notes` defaults to empty list."""
    resp = SwingSetupsResponse(
        rows=[],
        total=0,
        regime="sideways",
        as_of="2026-05-12",
        rec_gate_applied=True,
        rec_run_id="uuid",
        rec_run_date="2026-05-01",
        methodology=SwingMethodology(
            regime="sideways", summary="x", gates=[],
            rank=SwingMethodologyRank(
                formula="a", direction="ASC", cap=25,
                degraded=None,
            ),
        ),
    )
    assert resp.notes == []


# ---------------------------------------------------------------
# Task 13: _compute_swing_setup orchestrator
# ---------------------------------------------------------------

import asyncio
from datetime import date


def test_compute_swing_setup_bull_filters_and_ranks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: rows that pass bull filter are ranked DESC,
    capped at SWING_CAP, methodology + degraded-flag set
    correctly."""
    import advanced_analytics_routes as aar
    from advanced_analytics_swing import SWING_CAP

    # Build SWING_CAP + 3 rows that all pass the bull filter, with
    # rank scores increasing so ordering can be verified.
    rows: list[AdvancedRow] = []
    for i in range(SWING_CAP + 3):
        rows.append(_bull_row(
            ticker=f"T{i}.NS",
            today_x_vol=2.0 + i * 0.01,
            x_dv_20d=1.5,
            rec_expected_return_pct=10.0 + i,
            rec_category="offensive",
        ))

    async def fake_cached_full_rows(_user, _as_of):
        return rows

    async def fake_load_recs(user_id, tickers):
        return {
            "run_id": "uuid-x",
            "run_date": "2026-05-01",
            "recs": {
                r.ticker: ("offensive", "high", float(10.0 + i))
                for i, r in enumerate(rows)
            },
        }

    monkeypatch.setattr(aar, "_cached_full_rows", fake_cached_full_rows)
    monkeypatch.setattr(
        aar, "_load_latest_recommendations", fake_load_recs,
    )

    class _U:
        user_id = "user-1"

    resp = asyncio.run(aar._compute_swing_setup(
        user=_U(),  # type: ignore[arg-type]
        regime="bull",
        market="all",
        as_of=date(2026, 5, 12),
        page=1,
        page_size=25,
        sort_key=None,
        sort_dir=None,
    ))
    assert resp.total == SWING_CAP
    assert len(resp.rows) == 25  # page_size = cap
    assert resp.rec_gate_applied is True
    # Highest rank should be the largest i (T_{SWING_CAP+2}).
    assert resp.rows[0].ticker == f"T{SWING_CAP + 2}.NS"
    assert resp.methodology.regime == "bull"


def test_compute_swing_setup_degrades_when_no_rec_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No rec run for user → rec_gate_applied False, bypass rec
    gate but still rank by vol*delivery."""
    import advanced_analytics_routes as aar

    rows = [
        _bull_row(
            ticker="X.NS",
            rec_category=None,
            rec_severity=None,
            rec_expected_return_pct=None,
        ),
    ]

    async def fake_cached_full_rows(_user, _as_of):
        return rows

    async def fake_load_recs(user_id, tickers):
        return {"run_id": None, "run_date": None, "recs": {}}

    monkeypatch.setattr(
        aar, "_cached_full_rows", fake_cached_full_rows,
    )
    monkeypatch.setattr(
        aar, "_load_latest_recommendations", fake_load_recs,
    )

    class _U:
        user_id = "user-2"

    resp = asyncio.run(aar._compute_swing_setup(
        user=_U(),  # type: ignore[arg-type]
        regime="bull",
        market="all",
        as_of=date(2026, 5, 12),
        page=1,
        page_size=25,
        sort_key=None,
        sort_dir=None,
    ))
    assert resp.rec_gate_applied is False
    assert resp.total == 1
    assert any("not applied" in n.lower() for n in resp.notes)


def test_compute_swing_setup_unknown_regime_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid regime → HTTPException 400."""
    import advanced_analytics_routes as aar
    from fastapi import HTTPException

    async def fake_cached_full_rows(_u, _d):
        return []

    monkeypatch.setattr(
        aar, "_cached_full_rows", fake_cached_full_rows,
    )

    class _U:
        user_id = "u"

    with pytest.raises(HTTPException):
        asyncio.run(aar._compute_swing_setup(
            user=_U(),  # type: ignore[arg-type]
            regime="noisy",  # type: ignore[arg-type]
            market="all",
            as_of=date(2026, 5, 12),
            page=1,
            page_size=25,
            sort_key=None,
            sort_dir=None,
        ))


# ---------------------------------------------------------------
# Task 14 + 15 — swing-setups endpoints (router registration)
# ---------------------------------------------------------------


def _build_swing_test_app(monkeypatch: pytest.MonkeyPatch):
    """Mount the AA router on a bare FastAPI app with the
    ``pro_or_superuser`` guard stubbed to a superuser.

    Mirrors the fixture pattern in
    ``tests/backend/test_advanced_analytics_routes.py``.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import advanced_analytics_routes as aar
    from auth.dependencies import pro_or_superuser
    from auth.models import UserContext

    app = FastAPI()
    router = aar.create_advanced_analytics_router()
    app.include_router(router, prefix="/v1")

    # Stub out the heavy collaborators so route-existence tests
    # don't require Iceberg / Redis / Postgres.
    async def _fake_rows(_user, _as_of):
        return []

    async def _fake_recs(user_id, tickers):
        return {"run_id": None, "run_date": None, "recs": {}}

    monkeypatch.setattr(aar, "_cached_full_rows", _fake_rows)
    monkeypatch.setattr(
        aar, "_load_latest_recommendations", _fake_recs,
    )

    class _NoOpCache:
        def get(self, _k):
            return None

        def set(self, _k, _v, ttl=None):
            return None

    monkeypatch.setattr(aar, "get_cache", lambda: _NoOpCache())

    def _ctx() -> UserContext:
        return UserContext(
            user_id="swing-test-user",
            email="swing@test",
            role="superuser",
        )

    app.dependency_overrides[pro_or_superuser] = _ctx
    return TestClient(app)


def test_swing_setups_methodology_endpoint_returns_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``GET /swing-setups/methodology?regime=sideways`` returns
    a structured methodology block."""
    client = _build_swing_test_app(monkeypatch)
    resp = client.get(
        "/v1/advanced-analytics/swing-setups/methodology"
        "?regime=sideways"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # SwingMethodology shape — must carry at least these keys.
    assert "regime" in body
    assert body["regime"] == "sideways"


def test_swing_setups_endpoint_route_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``GET /swing-setups?regime=bull`` is registered and
    returns a 200 with the SwingSetupsResponse envelope."""
    client = _build_swing_test_app(monkeypatch)
    resp = client.get(
        "/v1/advanced-analytics/swing-setups?regime=bull"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "rows" in body
    assert "total" in body
    assert "regime" in body
    assert body["regime"] == "bull"


def test_swing_setups_endpoint_rejects_unknown_regime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown regime is rejected by FastAPI's ``Literal``
    validator (422) before our orchestrator runs."""
    client = _build_swing_test_app(monkeypatch)
    resp = client.get(
        "/v1/advanced-analytics/swing-setups?regime=mango"
    )
    assert resp.status_code in (400, 422)


def test_swing_setups_methodology_rejects_unknown_regime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Methodology endpoint also validates regime."""
    client = _build_swing_test_app(monkeypatch)
    resp = client.get(
        "/v1/advanced-analytics/swing-setups/methodology"
        "?regime=cheesy"
    )
    assert resp.status_code in (400, 422)


def test_methodology_thresholds_match_filter_constants() -> None:
    """Methodology rule strings must reference the same threshold
    constants the filters actually use. Catches drift.

    If someone tunes BULL_VOL_MIN to 1.8 in
    ``advanced_analytics_swing.py`` but forgets to update the
    methodology block, this test fails loudly.
    """
    from advanced_analytics_swing import (
        BEARISH_DEATH_CROSS_FRESH_DAYS,
        BEARISH_FLOOR_RATIO,
        BEARISH_NOT_FLOOR_INR,
        BEARISH_NOT_FLOOR_USD,
        BEARISH_RSI_MAX_RECENT,
        BEARISH_RSI_TODAY_MAX,
        BULL_GOLDEN_CROSS_FRESH_DAYS,
        BULL_PLEDGED_MAX,
        BULL_PSCORE_MIN,
        BULL_RANGE_MAX,
        BULL_RSI_MAX,
        BULL_VOL_MAX,
        BULL_VOL_MIN,
        SIDEWAYS_MA_CONV_MAX,
        SIDEWAYS_NOT_FLOOR_INR,
        SIDEWAYS_NOT_FLOOR_USD,
        SIDEWAYS_PRICE_NEAR_SMA50,
        SIDEWAYS_PSCORE_MIN,
        SIDEWAYS_RSI_MAX,
        SIDEWAYS_RSI_MIN,
        SIDEWAYS_VOL_MAX,
        SIDEWAYS_VOL_MIN,
        build_methodology,
    )

    bull = build_methodology("bull")
    rules_bull = " ".join(g["rule"] for g in bull["gates"])
    assert str(BULL_VOL_MIN) in rules_bull
    assert str(BULL_VOL_MAX) in rules_bull
    assert str(BULL_RSI_MAX) in rules_bull
    assert str(BULL_PSCORE_MIN) in rules_bull
    assert str(BULL_PLEDGED_MAX) in rules_bull
    assert str(BULL_RANGE_MAX) in rules_bull
    assert str(BULL_GOLDEN_CROSS_FRESH_DAYS) in rules_bull
    # Established-uptrend sentinel branch on the bull trend gate
    # (parallel to bearish's established-bearish sentinel acceptance).
    from advanced_analytics_models import ESTABLISHED_CROSS_DAYS
    assert str(ESTABLISHED_CROSS_DAYS) in rules_bull

    sw = build_methodology("sideways")
    rules_sw = " ".join(g["rule"] for g in sw["gates"])
    assert str(SIDEWAYS_MA_CONV_MAX) in rules_sw
    assert str(SIDEWAYS_PRICE_NEAR_SMA50) in rules_sw
    assert str(SIDEWAYS_RSI_MIN) in rules_sw
    assert str(SIDEWAYS_RSI_MAX) in rules_sw
    assert str(SIDEWAYS_VOL_MIN) in rules_sw
    assert str(SIDEWAYS_VOL_MAX) in rules_sw
    assert str(SIDEWAYS_PSCORE_MIN) in rules_sw
    # Liquidity-floor rule uses formatted numbers (with thousand
    # separators), so assert against the formatted string rather
    # than the bare float.
    assert f"{SIDEWAYS_NOT_FLOOR_INR:,.0f}" in rules_sw
    assert f"{SIDEWAYS_NOT_FLOOR_USD:,.0f}" in rules_sw

    bear = build_methodology("bearish")
    rules_bear = " ".join(g["rule"] for g in bear["gates"])
    assert str(BEARISH_DEATH_CROSS_FRESH_DAYS) in rules_bear
    assert str(BEARISH_RSI_MAX_RECENT) in rules_bear
    assert str(BEARISH_RSI_TODAY_MAX) in rules_bear
    assert str(BEARISH_FLOOR_RATIO) in rules_bear
    assert f"{BEARISH_NOT_FLOOR_INR:,.0f}" in rules_bear
    assert f"{BEARISH_NOT_FLOOR_USD:,.0f}" in rules_bear


def test_methodology_bullish_categories_match_constant() -> None:
    """The methodology mentions the exact pinned bullish set."""
    from advanced_analytics_swing import (
        BULLISH_CATEGORIES,
        build_methodology,
    )

    bull = build_methodology("bull")
    rules_bull = " ".join(g["rule"] for g in bull["gates"])
    for cat in BULLISH_CATEGORIES:
        assert cat in rules_bull, (
            f"Bullish category {cat!r} not surfaced in "
            "methodology gates"
        )
