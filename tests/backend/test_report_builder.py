"""Unit tests for agents/report_builder.py.

Tests parsing of tool output text and report rendering.
ASETPLTFRM-72.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure backend is importable.
_backend = str(
    Path(__file__).resolve().parent.parent.parent
    / "backend"
)
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from agents.report_builder import (  # noqa: E402
    _extract,
    _parse_analysis,
    _parse_forecast,
    _parse_stock_info,
    build_report,
)


# ---------------------------------------------------------------
# _extract
# ---------------------------------------------------------------


class TestExtract:
    def test_basic(self):
        text = "Current Price : $252.82"
        assert _extract(text, "Current Price") == "$252.82"

    def test_with_extra_spaces(self):
        text = "  RSI (14)  :   67.5  "
        assert _extract(text, "RSI (14)") == "67.5"

    def test_missing_label(self):
        assert _extract("foo bar", "Missing") is None

    def test_empty_string(self):
        assert _extract("", "Price") is None

    def test_multiline(self):
        text = "Line 1\nSMA 50 : 264.12\nLine 3"
        assert _extract(text, "SMA 50") == "264.12"


# ---------------------------------------------------------------
# _parse_analysis
# ---------------------------------------------------------------


class TestParseAnalysis:
    SAMPLE = (
        "Current Price : ₹1,365.00\n"
        "All Time High : ₹1,800.00\n"
        "All Time Low : ₹150.00\n"
        "10Y Total Return : 450%\n"
        "Avg Annual Ret : 18.5%\n"
        "SMA 50 : ₹1,380.00\n"
        "SMA 200 : ₹1,250.00\n"
        "RSI (14) : 55.3\n"
        "MACD : Bullish crossover\n"
        "Volatility : 28.5%\n"
        "Sharpe Ratio : 0.85\n"
        "Bull phase : 65%\n"
        "Bear phase : 35%\n"
        "Max Drawdown : -32.5%\n"
        "Max DD Duration : 180 days\n"
        "Support : ₹1,300\n"
        "Resistance : ₹1,420\n"
        "Best Month : Jan (+8.2%)\n"
        "Worst Month : Sep (-5.1%)\n"
        "Best Year : 2021 (+42%)\n"
        "Worst Year : 2022 (-15%)\n"
        "Saved to : /charts/analysis/INFY_analysis.html\n"
    )

    def test_full_parse(self):
        d = _parse_analysis(self.SAMPLE)
        assert d["current_price"] == "₹1,365.00"
        assert d["rsi"] == "55.3"
        assert d["sharpe"] == "0.85"
        assert d["chart"] is not None

    def test_partial_output(self):
        d = _parse_analysis("Current Price : $100\n")
        assert d["current_price"] == "$100"
        assert d["rsi"] is None
        assert d["chart"] is None

    def test_empty_output(self):
        d = _parse_analysis("")
        assert all(v is None for v in d.values())


# ---------------------------------------------------------------
# _parse_forecast
# ---------------------------------------------------------------


class TestParseForecast:
    SAMPLE = (
        "CURRENT PRICE : ₹1,365.00\n"
        "SENTIMENT : Bullish\n"
        "Chart : /charts/forecasts/INFY_forecast.html\n"
        "3M Target : ₹1,450 (+6.2%) [₹1,380 – ₹1,520]\n"
        "6M Target : ₹1,550 (+13.6%) [₹1,400 – ₹1,700]\n"
        "9M Target : ₹1,620 (+18.7%) [₹1,450 – ₹1,800]\n"
        "MAE : 25.30\n"
        "RMSE : 32.10\n"
        "MAPE : 4.5%\n"
    )

    def test_full_parse(self):
        d = _parse_forecast(self.SAMPLE)
        assert d["current_price"] == "₹1,365.00"
        assert d["sentiment"] == "Bullish"
        assert "3M" in d["targets"]
        assert "6M" in d["targets"]
        assert "9M" in d["targets"]
        assert d["mae"] == "25.30"
        assert d["rmse"] == "32.10"

    def test_missing_horizons(self):
        d = _parse_forecast(
            "CURRENT PRICE : $100\n"
            "3M Target : $110 (+10%)\n"
        )
        assert "3M" in d["targets"]
        assert "6M" not in d["targets"]
        assert "9M" not in d["targets"]

    def test_empty_output(self):
        d = _parse_forecast("")
        assert d["targets"] == {}
        assert d["current_price"] is None

    def test_accuracy_error(self):
        d = _parse_forecast(
            "Accuracy : Could not compute backtest\n"
        )
        assert d["accuracy_error"] is not None


# ---------------------------------------------------------------
# _parse_stock_info
# ---------------------------------------------------------------


class TestParseStockInfo:
    def test_valid_json(self):
        d = _parse_stock_info(
            '{"company": "Infosys", "sector": "IT"}'
        )
        assert d["company"] == "Infosys"
        assert d["sector"] == "IT"

    def test_invalid_json(self):
        d = _parse_stock_info("not json {{{")
        assert d == {}

    def test_empty_string(self):
        d = _parse_stock_info("")
        assert d == {}

    def test_none_input(self):
        d = _parse_stock_info(None)
        assert d == {}


# ---------------------------------------------------------------
# build_report
# ---------------------------------------------------------------


class TestBuildReport:
    def test_all_tools(self):
        results = {
            "get_stock_info": '{"company": "AAPL"}',
            "analyse_stock_price": (
                "Current Price : $252\n"
                "RSI (14) : 55\n"
            ),
            "forecast_stock": (
                "CURRENT PRICE : $252\n"
                "SENTIMENT : Neutral\n"
                "3M Target : $260 (+3.2%)\n"
            ),
        }
        report = build_report(results, "AAPL")
        assert "AAPL" in report or "aapl" in report.lower()
        assert isinstance(report, str)
        assert len(report) > 0

    def test_single_tool(self):
        results = {
            "analyse_stock_price": (
                "Current Price : $100\n"
            ),
        }
        report = build_report(results, "TEST")
        assert isinstance(report, str)

    def test_empty_results(self):
        report = build_report({}, "")
        assert isinstance(report, str)

    def test_no_crash_on_malformed(self):
        results = {
            "get_stock_info": "{{broken",
            "analyse_stock_price": "",
            "forecast_stock": None,
        }
        # Should not raise
        report = build_report(results, "BAD")
        assert isinstance(report, str)
