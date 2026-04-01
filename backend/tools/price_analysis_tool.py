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
from datetime import date

import tools._analysis_shared as _sh
from langchain_core.tools import tool
from tools._analysis_indicators import _calculate_technical_indicators
from tools._analysis_movement import _analyse_price_movement
from tools._analysis_summary import _generate_summary_stats
from validation import validate_ticker

# Module-level logger — kept at module scope as a private constant
_logger = logging.getLogger(__name__)

# Re-export helpers so tests can still monkeypatch via price_analysis_tool.*
_get_repo = _sh._get_repo
_require_repo = _sh._require_repo
_calculate_technical_indicators = (
    _calculate_technical_indicators  # noqa: F811 — re-export
)
_analyse_price_movement = _analyse_price_movement  # noqa: F811 — re-export


@tool
def analyse_stock_price(ticker: str) -> str:
    """Perform full technical price analysis on a stock and generate a chart.

    **IMPORTANT**: OHLCV data must already exist in Iceberg before calling
    this tool.  Call ``fetch_stock_data`` for the ticker in a **prior step**
    and wait for it to complete.  Do NOT call both tools in the same step.

    Calculates technical indicators (SMA 50/200, EMA 20, RSI 14, MACD,
    Bollinger Bands, ATR 14), analyses bull/bear phases, max drawdown,
    support/resistance levels, annualised volatility, and Sharpe ratio.

    Saves an interactive 3-panel Plotly chart (candlestick + volume + RSI)
    in dark theme to ``charts/analysis/{TICKER}_analysis.html``.

    Args:
        ticker: Stock ticker symbol, e.g. ``"AAPL"``.

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
    err = validate_ticker(ticker)
    if err:
        return f"Error: {err}"
    ticker = ticker.upper().strip()
    from tools._ticker_linker import auto_link_ticker

    auto_link_ticker(ticker)
    _logger.info(
        "analyse_stock_price | ticker=%s",
        ticker,
    )
    sym = _sh._currency_symbol(_sh._load_currency(ticker))

    # Iceberg freshness gate: return cached analysis only if
    # it was run today AND OHLCV data hasn't been updated since.
    try:
        repo_check = _sh._get_repo()
        if repo_check is not None:
            latest = repo_check.get_latest_analysis_summary(
                ticker
            )
            if latest is not None:
                ad = latest.get("analysis_date")
                if ad is not None:
                    if hasattr(ad, "date"):
                        ad = ad.date()
                    if ad == date.today():
                        # Verify OHLCV hasn't been refreshed
                        # since the analysis was generated.
                        ohlcv_date = (
                            repo_check.get_latest_ohlcv_date(
                                ticker
                            )
                        )
                        if ohlcv_date is not None:
                            if hasattr(ohlcv_date, "date"):
                                ohlcv_date = (
                                    ohlcv_date.date()
                                )
                            if ohlcv_date <= ad:
                                _logger.info(
                                    "Analysis up-to-date"
                                    " for %s (Iceberg)",
                                    ticker,
                                )
                                return (
                                    f"Analysis for "
                                    f"{ticker} is already"
                                    f" up-to-date (run "
                                    f"today). Use "
                                    f"load_stock_data or"
                                    f" the dashboard to "
                                    f"view results."
                                )
    except Exception as exc:
        _logger.debug(
            "Freshness check skipped for %s: %s",
            ticker,
            exc,
        )

    # Iceberg freshness gate: if another user (or session)
    # already analysed this ticker today, return that result
    # from the file cache it would have written.  We only
    # need to check the Iceberg analysis_date; the file cache
    # is always written alongside the Iceberg insert.
    try:
        df = _sh._load_ohlcv(ticker)
        if df is None:
            return (
                f"No OHLCV data found for '{ticker}'. "
                "You MUST call fetch_stock_data for "
                "this ticker first, then call "
                "analyse_stock_price again in the "
                "next step."
            )

        df = _calculate_technical_indicators(df)
        movement = _analyse_price_movement(df)
        stats = _generate_summary_stats(df, ticker)

        repo = _sh._require_repo()
        repo.upsert_technical_indicators(ticker, df)
        _iceberg_summary = {
            **movement,
            **stats,
            "macd_signal_text": stats.get("macd_signal"),
            "support_levels": str(movement.get("support_levels", [])),
            "resistance_levels": str(movement.get("resistance_levels", [])),
        }
        repo.insert_analysis_summary(ticker, _iceberg_summary)

        ath = stats["all_time_high"]
        ath_d = stats["all_time_high_date"]
        atl = stats["all_time_low"]
        atl_d = stats["all_time_low_date"]
        tr = stats["total_return_pct"]
        aar = stats["avg_annual_return_pct"]
        vol = movement["annualized_volatility_pct"]
        sr = movement["sharpe_ratio"]
        bull = movement["bull_phase_pct"]
        bear = movement["bear_phase_pct"]
        mdd = movement["max_drawdown_pct"]
        mdd_d = movement["max_drawdown_duration_days"]
        sup = movement["support_levels"]
        res = movement["resistance_levels"]
        bm = stats["best_month"]
        bmr = stats["best_month_return_pct"]
        wm = stats["worst_month"]
        wmr = stats["worst_month_return_pct"]
        by = stats["best_year"]
        byr = stats["best_year_return_pct"]
        wy = stats["worst_year"]
        wyr = stats["worst_year_return_pct"]
        report = (
            f"=== PRICE ANALYSIS: {ticker} ===\n\n"
            f"PRICE SUMMARY\n"
            f"  Current Price   : {sym}{stats['current_price']}\n"
            f"  All Time High   : {sym}{ath} ({ath_d})\n"
            f"  All Time Low    : {sym}{atl} ({atl_d})\n"
            f"  10Y Total Return: {tr:+.1f}%\n"
            f"  Avg Annual Ret  : {aar:+.1f}%\n\n"
            f"TECHNICAL INDICATORS\n"
            f"  SMA 50          : {sym}{stats['sma_50']}"
            f" ({stats['sma_50_signal']})\n"
            f"  SMA 200         : {sym}{stats['sma_200']}"
            f" ({stats['sma_200_signal']})\n"
            f"  RSI (14)        : {stats['rsi_14']}"
            f" — {stats['rsi_signal']}\n"
            f"  MACD            : {stats['macd_signal']}\n"
            f"  Volatility      : {vol}% annualised\n"
            f"  Sharpe Ratio    : {sr}\n\n"
            f"MARKET PHASES (vs SMA 200)\n"
            f"  Bull phase      : {bull}% of time\n"
            f"  Bear phase      : {bear}% of time\n\n"
            f"DRAWDOWN\n"
            f"  Max Drawdown    : {mdd:.1f}%\n"
            f"  Max DD Duration : {mdd_d} trading days\n\n"
            f"KEY LEVELS (last 252 days)\n"
            f"  Support         : {sup}\n"
            f"  Resistance      : {res}\n\n"
            f"CALENDAR PERFORMANCE\n"
            f"  Best Month      : {bm} ({bmr:+.1f}%)\n"
            f"  Worst Month     : {wm} ({wmr:+.1f}%)\n"
            f"  Best Year       : {by} ({byr:+.1f}%)\n"
            f"  Worst Year      : {wy} ({wyr:+.1f}%)\n"
        )

        _logger.info("analyse_stock_price complete for %s", ticker)
        return report

    except Exception as e:
        _logger.error(
            "analyse_stock_price failed for %s: %s", ticker, e, exc_info=True
        )
        return f"Error analysing '{ticker}': {e}"
