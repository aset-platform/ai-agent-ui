"""FE-9 tests — sector_rotation_score primitive + engine wiring.

The primitive ranks an arbitrary mapping of sector → 15m return
into a per-sector ``[0.0, 1.0]`` score (best=1.0, worst=0.0). The
engine layers it onto every equity ticker via
``ticker_to_sector_index``. Tickers without a mapped sector see
the feature absent (skip-emission contract).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from backend.algo.backtest.types import BarData
from backend.algo.features import compute_intraday_features_for_universe
from backend.algo.features.primitives import compute_sector_rotation_at_bar

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
# Primitive: compute_sector_rotation_at_bar
# ────────────────────────────────────────────────────────────────


def test_compute_sector_rotation_normalises_to_zero_one_range():
    """4 sectors with distinct returns → best=1.0, worst=0.0,
    intermediates evenly spaced at 2/3 and 1/3.
    """
    returns = {
        "S_A": Decimal("0.04"),  # best
        "S_B": Decimal("0.02"),
        "S_C": Decimal("0.01"),
        "S_D": Decimal("-0.01"),  # worst
    }
    scores = compute_sector_rotation_at_bar(returns)
    # 4 sectors → denom = 3.
    assert scores["S_A"] == Decimal("3") / Decimal("3")  # 1.0
    assert scores["S_B"] == Decimal("2") / Decimal("3")
    assert scores["S_C"] == Decimal("1") / Decimal("3")
    assert scores["S_D"] == Decimal("0") / Decimal("3")  # 0.0


def test_single_sector_returns_empty():
    """Feature is undefined for < 2 sectors — would divide by zero
    in the rank normalisation."""
    assert compute_sector_rotation_at_bar({"ONLY": Decimal("0.01")}) == {}


def test_empty_input_returns_empty():
    assert compute_sector_rotation_at_bar({}) == {}


def test_tied_returns_break_by_symbol_order():
    """Two sectors with identical returns: the alphabetically
    EARLIER symbol gets the better rank (deterministic order so
    re-runs are reproducible)."""
    returns = {
        "ZZ": Decimal("0.01"),
        "AA": Decimal("0.01"),
    }
    scores = compute_sector_rotation_at_bar(returns)
    # Tie-breaker by symbol asc → AA ranked first (best).
    assert scores["AA"] == Decimal("1")
    assert scores["ZZ"] == Decimal("0")


def test_two_sectors_normalises_to_endpoints():
    """N=2 → denominator 1; the better return → 1.0, the worse → 0.0."""
    returns = {"S_A": Decimal("0.05"), "S_B": Decimal("0.01")}
    scores = compute_sector_rotation_at_bar(returns)
    assert scores["S_A"] == Decimal("1")
    assert scores["S_B"] == Decimal("0")


# ────────────────────────────────────────────────────────────────
# Engine wiring
# ────────────────────────────────────────────────────────────────


def test_engine_emits_sector_rotation_for_mapped_tickers():
    """2 tickers mapped to different sectors; 2 sectoral indices
    have distinct returns at bar 1 → mapped tickers each get the
    score for their own sector."""
    stock_it = _series("INFY.NS", [Decimal(100), Decimal(101)])
    stock_pharma = _series("CIPLA.NS", [Decimal(500), Decimal(502)])
    # NIFTY IT ramps +1% (best); NIFTY PHARMA ramps +0.5% (worst).
    nifty_it = _series("NIFTY IT", [Decimal(1000), Decimal(1010)])
    nifty_pharma = _series(
        "NIFTY PHARMA",
        [Decimal(2000), Decimal(2010)],
    )

    panel = compute_intraday_features_for_universe(
        {"INFY.NS": stock_it, "CIPLA.NS": stock_pharma},
        index_bars_by_symbol={
            "NIFTY IT": nifty_it,
            "NIFTY PHARMA": nifty_pharma,
        },
        ticker_to_sector_index={
            "INFY.NS": "NIFTY IT",
            "CIPLA.NS": "NIFTY PHARMA",
        },
    )
    ts1 = stock_it[1].bar_open_ts_ns
    # 2 sectors → IT best (1.0), PHARMA worst (0.0).
    assert panel["INFY.NS"][ts1]["sector_rotation_score"] == Decimal("1")
    assert panel["CIPLA.NS"][ts1]["sector_rotation_score"] == Decimal("0")
    # Bar 0 has no prev-bar return → feature absent.
    ts0 = stock_it[0].bar_open_ts_ns
    assert "sector_rotation_score" not in panel["INFY.NS"][ts0]


def test_engine_absent_for_unmapped_ticker():
    """Ticker not in ``ticker_to_sector_index`` → sector_rotation
    feature absent; other features still emit."""
    stock = _series("UNKNOWN.NS", [Decimal(100), Decimal(101)])
    nifty_it = _series("NIFTY IT", [Decimal(1000), Decimal(1010)])
    nifty_pharma = _series(
        "NIFTY PHARMA",
        [Decimal(2000), Decimal(2010)],
    )

    panel = compute_intraday_features_for_universe(
        {"UNKNOWN.NS": stock},
        index_bars_by_symbol={
            "NIFTY IT": nifty_it,
            "NIFTY PHARMA": nifty_pharma,
        },
        ticker_to_sector_index={},  # empty — no mapping
    )
    ts1 = stock[1].bar_open_ts_ns
    feats = panel["UNKNOWN.NS"][ts1]
    assert "sector_rotation_score" not in feats
    # Non-FE-9 features still present.
    assert "today_ltp" in feats


def test_engine_absent_when_only_one_sectoral_index():
    """Only one sectoral index has returns → primitive returns {}
    → feature absent for every ticker."""
    stock = _series("INFY.NS", [Decimal(100), Decimal(101)])
    nifty_it = _series("NIFTY IT", [Decimal(1000), Decimal(1010)])

    panel = compute_intraday_features_for_universe(
        {"INFY.NS": stock},
        index_bars_by_symbol={"NIFTY IT": nifty_it},
        ticker_to_sector_index={"INFY.NS": "NIFTY IT"},
    )
    ts1 = stock[1].bar_open_ts_ns
    assert "sector_rotation_score" not in panel["INFY.NS"][ts1]


def test_engine_excludes_broad_indices_from_sectoral_rank():
    """NIFTY 50 and NIFTY BANK are BROAD per
    ``BROAD_INDEX_SYMBOLS`` — they MUST NOT participate in the
    sectoral rank. If only NIFTY 50 + NIFTY BANK are present
    (zero sectorals), sector_rotation_score is absent."""
    stock = _series("INFY.NS", [Decimal(100), Decimal(101)])
    nifty50 = _series("NIFTY 50", [Decimal(1000), Decimal(1010)])
    nifty_bank = _series("NIFTY BANK", [Decimal(2000), Decimal(2050)])

    panel = compute_intraday_features_for_universe(
        {"INFY.NS": stock},
        index_bars_by_symbol={
            "NIFTY 50": nifty50,
            "NIFTY BANK": nifty_bank,
        },
        ticker_to_sector_index={"INFY.NS": "NIFTY IT"},
    )
    ts1 = stock[1].bar_open_ts_ns
    assert "sector_rotation_score" not in panel["INFY.NS"][ts1]


@pytest.mark.parametrize("ts_with_only_one_sector", [True, False])
def test_engine_consistent_score_across_tickers_in_same_sector(
    ts_with_only_one_sector,
):
    """Two tickers in the SAME sector see the SAME rotation score
    at the same ts_ns."""
    stock_a = _series("INFY.NS", [Decimal(100), Decimal(101)])
    stock_b = _series("TCS.NS", [Decimal(200), Decimal(202)])
    nifty_it = _series("NIFTY IT", [Decimal(1000), Decimal(1010)])
    extra_indices = (
        {}
        if ts_with_only_one_sector
        else {
            "NIFTY PHARMA": _series(
                "NIFTY PHARMA",
                [Decimal(2000), Decimal(2005)],
            )
        }
    )

    panel = compute_intraday_features_for_universe(
        {"INFY.NS": stock_a, "TCS.NS": stock_b},
        index_bars_by_symbol={"NIFTY IT": nifty_it, **extra_indices},
        ticker_to_sector_index={
            "INFY.NS": "NIFTY IT",
            "TCS.NS": "NIFTY IT",
        },
    )
    ts1 = stock_a[1].bar_open_ts_ns
    if ts_with_only_one_sector:
        # Only 1 sectoral → score undefined → absent on both.
        assert "sector_rotation_score" not in panel["INFY.NS"][ts1]
        assert "sector_rotation_score" not in panel["TCS.NS"][ts1]
    else:
        # 2 sectorals — IT (+1%) beats PHARMA (+0.25%) → IT = 1.0.
        s_a = panel["INFY.NS"][ts1]["sector_rotation_score"]
        s_b = panel["TCS.NS"][ts1]["sector_rotation_score"]
        assert s_a == s_b == Decimal("1")
