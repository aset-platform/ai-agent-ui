"""Unit tests for Piotroski F-Score computation."""

from backend.pipeline.screener.piotroski import (
    PiotroskiResult,
    compute_piotroski,
)


def _make_financials(
    net_income=100,
    total_assets=1000,
    operating_cashflow=150,
    total_debt=200,
    current_assets=500,
    current_liabilities=300,
    shares_outstanding=1_000_000,
    gross_profit=400,
    revenue=800,
):
    """Build a financials dict with defaults."""
    return {
        "net_income": net_income,
        "total_assets": total_assets,
        "operating_cashflow": operating_cashflow,
        "total_debt": total_debt,
        "current_assets": current_assets,
        "current_liabilities": current_liabilities,
        "shares_outstanding": shares_outstanding,
        "gross_profit": gross_profit,
        "revenue": revenue,
    }


class TestComputePiotroski:
    """Tests for compute_piotroski()."""

    def test_perfect_score(self):
        """All 9 criteria pass -> score 9."""
        current = _make_financials(
            net_income=120,
            total_assets=1000,
            operating_cashflow=150,
            total_debt=180,
            current_assets=550,
            current_liabilities=300,
            shares_outstanding=900_000,
            gross_profit=500,
            revenue=900,
        )
        previous = _make_financials(
            net_income=100,
            total_assets=1000,
            operating_cashflow=130,
            total_debt=200,
            current_assets=500,
            current_liabilities=300,
            shares_outstanding=1_000_000,
            gross_profit=400,
            revenue=800,
        )
        result = compute_piotroski(current, previous)
        assert result.total_score == 9
        assert result.label == "Strong"
        assert result.roa_positive is True
        assert result.operating_cf_positive is True
        assert result.roa_increasing is True
        assert result.cf_gt_net_income is True
        assert result.leverage_decreasing is True
        assert result.current_ratio_increasing is True
        assert result.no_dilution is True
        assert result.gross_margin_increasing is True
        assert result.asset_turnover_increasing is True

    def test_zero_score(self):
        """All criteria fail -> score 0."""
        current = _make_financials(
            net_income=-50,
            total_assets=1000,
            operating_cashflow=-60,
            total_debt=300,
            current_assets=400,
            current_liabilities=500,
            shares_outstanding=1_200_000,
            gross_profit=300,
            revenue=700,
        )
        previous = _make_financials(
            net_income=100,
            total_assets=1000,
            operating_cashflow=150,
            total_debt=200,
            current_assets=500,
            current_liabilities=300,
            shares_outstanding=1_000_000,
            gross_profit=400,
            revenue=800,
        )
        result = compute_piotroski(current, previous)
        assert result.total_score == 0
        assert result.label == "Weak"

    def test_moderate_score(self):
        """Mixed criteria -> moderate score."""
        current = _make_financials(
            net_income=120,
            total_assets=1000,
            operating_cashflow=150,
            total_debt=250,
            current_assets=400,
            current_liabilities=500,
            shares_outstanding=1_000_000,
            gross_profit=400,
            revenue=800,
        )
        previous = _make_financials()
        result = compute_piotroski(current, previous)
        assert 1 <= result.total_score <= 8
        assert result.roa_positive is True
        assert result.operating_cf_positive is True
        assert result.cf_gt_net_income is True

    def test_zero_total_assets(self):
        """Division by zero guarded -> criterion fails."""
        current = _make_financials(total_assets=0)
        previous = _make_financials(total_assets=0)
        result = compute_piotroski(current, previous)
        assert result.roa_positive is False
        assert result.roa_increasing is False
        assert result.leverage_decreasing is False
        assert result.asset_turnover_increasing is False

    def test_none_values(self):
        """Missing data -> defaults to criterion fail."""
        current = {
            k: None
            for k in [
                "net_income",
                "total_assets",
                "operating_cashflow",
                "total_debt",
                "current_assets",
                "current_liabilities",
                "shares_outstanding",
                "gross_profit",
                "revenue",
            ]
        }
        previous = dict(current)
        result = compute_piotroski(current, previous)
        assert result.total_score == 0

    def test_equal_yoy_fails(self):
        """Equal YoY values -> 'increasing' criteria fail."""
        current = _make_financials()
        previous = _make_financials()
        result = compute_piotroski(current, previous)
        assert result.roa_increasing is False
        assert result.current_ratio_increasing is False
        assert result.gross_margin_increasing is False
        assert result.asset_turnover_increasing is False
        assert result.no_dilution is True

    def test_label_boundaries(self):
        """Label boundaries: 8=Strong, 5=Moderate, 4=Weak."""
        r8 = PiotroskiResult(
            total_score=8,
            roa_positive=True,
            operating_cf_positive=True,
            roa_increasing=True,
            cf_gt_net_income=True,
            leverage_decreasing=True,
            current_ratio_increasing=True,
            no_dilution=True,
            gross_margin_increasing=True,
            asset_turnover_increasing=False,
        )
        assert r8.label == "Strong"
        r5 = PiotroskiResult(
            total_score=5,
            roa_positive=True,
            operating_cf_positive=True,
            roa_increasing=True,
            cf_gt_net_income=True,
            leverage_decreasing=True,
            current_ratio_increasing=False,
            no_dilution=False,
            gross_margin_increasing=False,
            asset_turnover_increasing=False,
        )
        assert r5.label == "Moderate"
        r4 = PiotroskiResult(
            total_score=4,
            roa_positive=True,
            operating_cf_positive=True,
            roa_increasing=True,
            cf_gt_net_income=True,
            leverage_decreasing=False,
            current_ratio_increasing=False,
            no_dilution=False,
            gross_margin_increasing=False,
            asset_turnover_increasing=False,
        )
        assert r4.label == "Weak"
