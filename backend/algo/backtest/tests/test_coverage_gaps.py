from datetime import date
from unittest.mock import patch

from backend.algo.backtest.coverage import (
    CoverageGapSummary,
    GapReport,
    coverage_gap_summary,
    coverage_gaps,
    sufficiently_covered,
)

# Per-(ticker, bar_date) 15m bar counts. The reference trading-day
# set is derived from the union of every date ANY ticker traded, so
# no external holiday calendar is needed. D1/D2/D3 are the three
# trading days present across the batch.
_D1, _D2, _D3 = date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3)

_ROWS = [
    {"ticker": "A.NS", "bar_date": _D1, "bars": 25},
    {"ticker": "A.NS", "bar_date": _D2, "bars": 25},
    {"ticker": "B.NS", "bar_date": _D1, "bars": 25},
    {"ticker": "B.NS", "bar_date": _D2, "bars": 25},
    {"ticker": "B.NS", "bar_date": _D3, "bars": 25},
]


def test_missing_full_days_relative_to_reference_calendar():
    with patch(
        "backend.algo.backtest.coverage.query_iceberg_table",
        return_value=_ROWS,
    ) as q:
        gaps = coverage_gaps(
            tickers=["A.NS", "B.NS"],
            period_start=_D1,
            period_end=_D3,
        )
    # B traded all three reference days -> no gap.
    assert gaps["B.NS"] == GapReport("B.NS", 3, 3, ())
    # A is missing D3 (B traded it, so it's a real trading day).
    assert gaps["A.NS"].expected_days == 3
    assert gaps["A.NS"].present_days == 2
    assert gaps["A.NS"].missing_dates == (_D3,)
    # Batched single query (CLAUDE.md 4.1 #1).
    assert q.call_count == 1


# A full NSE 15m equity session is 25 bars (09:15-15:30). A day
# present but under-covered is a gap too, distinct from a fully
# absent day.
_PARTIAL_ROWS = [
    {"ticker": "A.NS", "bar_date": _D1, "bars": 25},
    {"ticker": "A.NS", "bar_date": _D2, "bars": 10},  # partial
    {"ticker": "B.NS", "bar_date": _D1, "bars": 25},
    {"ticker": "B.NS", "bar_date": _D2, "bars": 25},
    {"ticker": "B.NS", "bar_date": _D3, "bars": 25},   # A absent D3
]


def test_partial_days_flagged_separately_from_missing():
    with patch(
        "backend.algo.backtest.coverage.query_iceberg_table",
        return_value=_PARTIAL_ROWS,
    ):
        gaps = coverage_gaps(
            tickers=["A.NS", "B.NS"],
            period_start=_D1,
            period_end=_D3,
            min_bars=25,
        )
    a = gaps["A.NS"]
    # D2 is present-but-partial (10 < 25), not fully missing.
    assert a.partial_dates == (_D2,)
    # D3 is fully absent (A has no bars; B traded it).
    assert a.missing_dates == (_D3,)
    # A day is exactly one of full / partial / missing.
    assert _D2 not in a.missing_dates
    # B is fully covered on every reference day.
    assert gaps["B.NS"].partial_dates == ()
    assert gaps["B.NS"].missing_dates == ()


_ELIG_ROWS = [
    # GOOD traded all three reference days (100%).
    {"ticker": "GOOD.NS", "bar_date": _D1, "bars": 25},
    {"ticker": "GOOD.NS", "bar_date": _D2, "bars": 25},
    {"ticker": "GOOD.NS", "bar_date": _D3, "bars": 25},
    # BAD traded only 1 of 3 reference days (33% < 95%).
    {"ticker": "BAD.NS", "bar_date": _D1, "bars": 25},
]


def test_sufficiently_covered_excludes_below_threshold():
    with patch(
        "backend.algo.backtest.coverage.query_iceberg_table",
        return_value=_ELIG_ROWS,
    ):
        covered = sufficiently_covered(
            tickers=["GOOD.NS", "BAD.NS"],
            as_of=_D3,
            lookback_days=90,
            min_pct=95.0,
        )
    assert covered == {"GOOD.NS"}


def test_sufficiently_covered_fails_open_on_empty_read():
    # No coverage data at all -> can't judge -> keep every ticker
    # rather than nuking the universe (mirrors the ADTV-floor
    # empty-snapshot degrade).
    with patch(
        "backend.algo.backtest.coverage.query_iceberg_table",
        return_value=[],
    ):
        covered = sufficiently_covered(
            tickers=["GOOD.NS", "BAD.NS"],
            as_of=_D3,
        )
    assert covered == {"GOOD.NS", "BAD.NS"}


def test_coverage_gap_summary_reports_only_tickers_with_missing_days():
    with patch(
        "backend.algo.backtest.coverage.query_iceberg_table",
        return_value=_ELIG_ROWS,  # GOOD full, BAD missing 2 of 3
    ):
        summary = coverage_gap_summary(
            tickers=["GOOD.NS", "BAD.NS"],
            as_of=_D3,
            lookback_days=90,
        )
    assert isinstance(summary, CoverageGapSummary)
    assert summary.reference_days == 3
    assert summary.total_tickers == 2
    # Only BAD is reported; a fully-covered ticker is not noise.
    gap_tickers = [g.ticker for g in summary.gap_tickers]
    assert gap_tickers == ["BAD.NS"]
    assert summary.gap_tickers[0].present_days == 1
    assert len(summary.gap_tickers[0].missing_dates) == 2
