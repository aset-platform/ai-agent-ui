"""Price movement analysis tool for the Stock Analysis Agent.

This module exposes the public :func:`analyse_stock_price` LangChain ``@tool``
function.  All heavy lifting is delegated to private sub-modules:

- :mod:`tools._analysis_shared` — constants, lazy Iceberg repo, cache helpers
- :mod:`tools._analysis_indicators` — technical indicator computation
- :mod:`tools._analysis_movement` — bull/bear phases, drawdown, Sharpe
- :mod:`tools._analysis_summary` — summary statistics for report
- :mod:`tools._analysis_chart` — Plotly chart builder

Technical indicators calculated: SMA 50/200, EMA 20, RSI 14, MACD,
Bollinger Bands, ATR 14.

Typical usage (via LangChain tool call)::

    from tools.price_analysis_tool import analyse_stock_price

    result = analyse_stock_price.invoke({"ticker": "AAPL"})
"""

import logging

from langchain_core.tools import tool

import tools._analysis_shared as _sh
from tools._analysis_indicators import _calculate_technical_indicators
from tools._analysis_movement import _analyse_price_movement
from tools._analysis_summary import _generate_summary_stats
from tools._analysis_chart import _create_analysis_chart

# Module-level logger — kept at module scope as a private constant
_logger = logging.getLogger(__name__)

# Re-export helpers so tests can still monkeypatch via price_analysis_tool._get_repo
_get_repo = _sh._get_repo
_calculate_technical_indicators = _calculate_technical_indicators  # noqa: F811 — re-export
_analyse_price_movement = _analyse_price_movement  # noqa: F811 — re-export


@tool
def analyse_stock_price(ticker: str) -> str:
    """Perform full technical price analysis on a stock and generate a chart.

    Loads locally stored OHLCV data (written by :func:`fetch_stock_data`),
    calculates technical indicators (SMA 50/200, EMA 20, RSI 14, MACD,
    Bollinger Bands, ATR 14), analyses bull/bear phases, max drawdown,
    support/resistance levels, annualised volatility, and Sharpe ratio.

    Saves an interactive 3-panel Plotly chart (candlestick + volume + RSI)
    in dark theme to ``charts/analysis/{TICKER}_analysis.html``.

    Args:
        ticker: Stock ticker symbol, e.g. ``"AAPL"``. Data must already be
            fetched via :func:`fetch_stock_data` before calling this tool.

    Returns:
        A formatted multi-section string report with all key metrics and
        the chart file path, or an error string if data is unavailable.

    Raises:
        Exception: Caught internally; returns an error string rather than
            propagating so the LangChain agent can handle it gracefully.

    Example:
        >>> result = analyse_stock_price.invoke({"ticker": "AAPL"})
        >>> "AAPL" in result
        True
    """
    ticker = ticker.upper().strip()
    _logger.info("analyse_stock_price | ticker=%s", ticker)
    sym = _sh._currency_symbol(_sh._load_currency(ticker))

    cached = _sh._load_cache(ticker, "analysis")
    if cached:
        _logger.info("Returning cached analysis for %s", ticker)
        return cached

    try:
        df = _sh._load_parquet(ticker)
        if df is None:
            return (
                f"No local data found for '{ticker}'. "
                "Please run fetch_stock_data first."
            )

        df = _calculate_technical_indicators(df)
        movement = _analyse_price_movement(df)
        stats = _generate_summary_stats(df, ticker)
        chart_path = _create_analysis_chart(df, ticker)

        try:
            repo = _sh._get_repo()
            if repo is not None:
                repo.upsert_technical_indicators(ticker, df)
                _iceberg_summary = {
                    **movement,
                    **stats,
                    "macd_signal_text": stats.get("macd_signal"),
                    "support_levels": str(movement.get("support_levels", [])),
                    "resistance_levels": str(movement.get("resistance_levels", [])),
                }
                repo.insert_analysis_summary(ticker, _iceberg_summary)
        except Exception as _e:
            _logger.error("Iceberg write failed for %s analysis: %s", ticker, _e)

        report = (
            f"=== PRICE ANALYSIS: {ticker} ===\n\n"
            f"PRICE SUMMARY\n"
            f"  Current Price   : {sym}{stats['current_price']}\n"
            f"  All Time High   : {sym}{stats['all_time_high']} ({stats['all_time_high_date']})\n"
            f"  All Time Low    : {sym}{stats['all_time_low']} ({stats['all_time_low_date']})\n"
            f"  10Y Total Return: {stats['total_return_pct']:+.1f}%\n"
            f"  Avg Annual Ret  : {stats['avg_annual_return_pct']:+.1f}%\n\n"
            f"TECHNICAL INDICATORS\n"
            f"  SMA 50          : {sym}{stats['sma_50']} ({stats['sma_50_signal']})\n"
            f"  SMA 200         : {sym}{stats['sma_200']} ({stats['sma_200_signal']})\n"
            f"  RSI (14)        : {stats['rsi_14']} — {stats['rsi_signal']}\n"
            f"  MACD            : {stats['macd_signal']}\n"
            f"  Volatility      : {movement['annualized_volatility_pct']}% annualised\n"
            f"  Sharpe Ratio    : {movement['sharpe_ratio']}\n\n"
            f"MARKET PHASES (vs SMA 200)\n"
            f"  Bull phase      : {movement['bull_phase_pct']}% of time\n"
            f"  Bear phase      : {movement['bear_phase_pct']}% of time\n\n"
            f"DRAWDOWN\n"
            f"  Max Drawdown    : {movement['max_drawdown_pct']:.1f}%\n"
            f"  Max DD Duration : {movement['max_drawdown_duration_days']} trading days\n\n"
            f"KEY LEVELS (last 252 days)\n"
            f"  Support         : {movement['support_levels']}\n"
            f"  Resistance      : {movement['resistance_levels']}\n\n"
            f"CALENDAR PERFORMANCE\n"
            f"  Best Month      : {stats['best_month']} ({stats['best_month_return_pct']:+.1f}%)\n"
            f"  Worst Month     : {stats['worst_month']} ({stats['worst_month_return_pct']:+.1f}%)\n"
            f"  Best Year       : {stats['best_year']} ({stats['best_year_return_pct']:+.1f}%)\n"
            f"  Worst Year      : {stats['worst_year']} ({stats['worst_year_return_pct']:+.1f}%)\n\n"
            f"CHART\n"
            f"  Saved to: {chart_path}\n"
        )

        _sh._save_cache(ticker, "analysis", report)
        _logger.info("analyse_stock_price complete for %s", ticker)
        return report

    except Exception as e:
        _logger.error("analyse_stock_price failed for %s: %s", ticker, e, exc_info=True)
        return f"Error analysing '{ticker}': {e}"