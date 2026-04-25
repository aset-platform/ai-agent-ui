"""PEG ratio coverage on ScreenQL (ASETPLTFRM-332).

Covers both the trailing PEG (computed in the ``ci``
CTE from ``pe_ratio / earnings_growth``) and the raw
yfinance ``peg_ratio_yf`` column (populated via schema
evolution + pipeline capture). Also exercises the
``_compute_peg`` helper that the Screener endpoint
uses to materialise a PEG value per row.
"""

from __future__ import annotations

import pytest

from backend.insights.screen_parser import (
    FIELD_CATALOG,
    _CTE_TEMPLATES,
    generate_sql,
    parse_query,
)
from backend.insights_routes import (
    _compute_peg,
    _peg_ttm_from_quarters,
)


class TestFieldCatalog:
    """PEG fields are registered and discoverable."""

    def test_peg_ratio_registered(self):
        assert "peg_ratio" in FIELD_CATALOG
        fd = FIELD_CATALOG["peg_ratio"]
        assert fd.table == "ci"
        assert fd.column == "peg_ratio"
        assert fd.category == "Valuation"

    def test_peg_ratio_yf_registered(self):
        assert "peg_ratio_yf" in FIELD_CATALOG
        fd = FIELD_CATALOG["peg_ratio_yf"]
        assert fd.table == "ci"
        assert fd.column == "peg_ratio_yf"
        assert fd.category == "Valuation"


class TestCteTemplate:
    """``ci`` CTE materialises ``peg_ratio`` column."""

    def test_ci_cte_has_peg_case(self):
        ci = _CTE_TEMPLATES["ci"]
        # The computed PEG column is expressed as a
        # CASE WHEN with explicit null/zero guards.
        assert "AS peg_ratio" in ci
        # Formula may be split across lines; check each
        # side of the division independently.
        assert "pe_ratio" in ci
        assert "earnings_growth * 100" in ci
        assert "pe_ratio <= 0" in ci
        assert "earnings_growth <= 0" in ci


class TestGenerateSql:
    """ScreenQL filters on PEG compile correctly."""

    def test_peg_filter(self):
        # parse_query takes the condition without a
        # leading WHERE keyword; the SQL generator adds
        # the WHERE in `generate_sql`.
        ast = parse_query("peg_ratio < 1")
        gq = generate_sql(ast)
        # CTE gets pulled in and the condition reaches
        # the aliased ci.peg_ratio column.
        assert "ci_raw AS" in gq.sql
        assert "ci.peg_ratio" in gq.sql
        assert "peg_ratio" in gq.columns_used

    def test_peg_yf_filter_combined(self):
        ast = parse_query(
            "peg_ratio < 1.5 AND peg_ratio_yf < 2",
        )
        gq = generate_sql(ast)
        assert "ci.peg_ratio" in gq.sql
        assert "ci.peg_ratio_yf" in gq.sql


class TestComputePeg:
    """``_compute_peg`` matches the documented rule."""

    def test_valid_inputs(self):
        # NFLX: pe=30.08, growth=0.864 → PEG ≈ 0.348
        got = _compute_peg(30.077420, 0.864)
        assert got is not None
        assert 0.34 < got < 0.36

    def test_negative_growth_returns_none(self):
        # TSLA example — declining earnings
        assert _compute_peg(355.5, -0.606) is None

    def test_zero_growth_returns_none(self):
        assert _compute_peg(25.0, 0.0) is None

    def test_negative_pe_returns_none(self):
        # Loss-making company
        assert _compute_peg(-10.0, 0.5) is None

    def test_none_inputs(self):
        assert _compute_peg(None, 0.5) is None
        assert _compute_peg(25.0, None) is None
        assert _compute_peg(None, None) is None

    @pytest.mark.parametrize(
        "pe,growth,expected_range",
        [
            (27.11, 0.598, (0.44, 0.46)),  # MSFT
            (41.33, 0.956, (0.42, 0.44)),  # NVDA
            (34.62, 0.183, (1.88, 1.90)),  # AAPL
            (31.36, 0.311, (1.00, 1.02)),  # GOOGL
        ],
    )
    def test_spot_checks(
        self, pe, growth, expected_range,
    ):
        got = _compute_peg(pe, growth)
        assert got is not None
        lo, hi = expected_range
        assert lo <= got <= hi, (
            f"PEG {got} out of {expected_range} "
            f"for pe={pe} growth={growth}"
        )


class TestPegTtmFromQuarters:
    """``_peg_ttm_from_quarters`` — PEG from our own
    quarterly_results filings (TTM EPS + single-Q YoY).
    """

    def test_happy_path(self):
        # 5 quarters, growing: [Q0, Q1, Q2, Q3, Q4]
        # most recent first. TTM = 4.4, Q0/Q4 = 2.0 →
        # growth = 1.0 (100%). PE = 100 / 4.4 ≈ 22.7.
        # PEG ≈ 22.7 / 100 = 0.227.
        got = _peg_ttm_from_quarters(
            [1.2, 1.1, 1.1, 1.0, 0.6], 100.0,
        )
        assert got is not None
        assert 0.22 < got < 0.24

    def test_insufficient_quarters(self):
        # <5 quarters — can't do YoY comparison.
        assert _peg_ttm_from_quarters(
            [1.0, 0.9, 0.8, 0.7], 100.0,
        ) is None
        assert _peg_ttm_from_quarters([], 100.0) is None

    def test_negative_ttm(self):
        # Sum of last 4 quarters ≤ 0 — loss-maker.
        assert _peg_ttm_from_quarters(
            [0.5, -1.0, -1.0, -1.0, 0.8], 100.0,
        ) is None

    def test_negative_current_quarter(self):
        # Q0 ≤ 0 even with positive TTM — growth base
        # would be weird, skip to stay conservative.
        assert _peg_ttm_from_quarters(
            [-0.1, 1.0, 1.0, 1.0, 0.6], 100.0,
        ) is None

    def test_negative_year_ago_quarter(self):
        # Q4 ≤ 0 — growth ratio is garbage.
        assert _peg_ttm_from_quarters(
            [1.0, 0.9, 0.8, 0.7, -0.2], 100.0,
        ) is None

    def test_flat_growth(self):
        # Q0 == Q4 (no growth) — PEG undefined.
        assert _peg_ttm_from_quarters(
            [1.0, 1.0, 1.0, 1.0, 1.0], 100.0,
        ) is None

    def test_declining_growth(self):
        # Q0 < Q4 — negative growth, return None.
        assert _peg_ttm_from_quarters(
            [0.5, 0.6, 0.7, 0.8, 1.0], 100.0,
        ) is None

    def test_missing_close(self):
        eps = [1.2, 1.1, 1.1, 1.0, 0.6]
        assert _peg_ttm_from_quarters(eps, None) is None
        assert _peg_ttm_from_quarters(eps, 0.0) is None
        assert _peg_ttm_from_quarters(eps, -5.0) is None

    def test_real_world_example(self):
        # Fictitious mid-cap: current quarter EPS 2.5,
        # TTM EPS 9.0, year-ago quarter EPS 2.0,
        # price ₹450. PE=50, growth=25%, PEG=2.0.
        got = _peg_ttm_from_quarters(
            [2.5, 2.3, 2.2, 2.0, 2.0], 450.0,
        )
        assert got is not None
        assert 1.99 < got < 2.01
