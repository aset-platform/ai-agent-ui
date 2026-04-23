"""Deterministic markdown report builder for stock analysis.

Parses tool output text and renders consistent tabular markdown
regardless of which LLM tier produced the final response.

The builder extracts structured data from the text output of
:func:`~tools.price_analysis_tool.analyse_stock_price` and
:func:`~tools.forecasting_tool.forecast_stock`, then renders
five deterministic sections.  A sixth section (verdict) is
left for the LLM to fill with a focused recommendation.

Typical usage::

    from agents.report_builder import build_report

    report = build_report(tool_results)
    # report is a markdown string with 5 data sections
    # + a verdict prompt for the LLM
"""

import logging
import re
from typing import Any

_logger = logging.getLogger(__name__)


def _extract(
    text: str | None,
    label: str,
) -> str | None:
    """Extract a value after ``Label : value``."""
    if not text:
        return None
    m = re.search(
        rf"{re.escape(label)}\s*:\s*(.+)",
        text,
    )
    return m.group(1).strip() if m else None


def _parse_analysis(
    text: str | None,
) -> dict[str, Any]:
    """Parse analyse_stock_price output."""
    if text is None:
        return {}
    d: dict[str, Any] = {}
    d["current_price"] = _extract(text, "Current Price")
    d["all_time_high"] = _extract(text, "All Time High")
    d["all_time_low"] = _extract(text, "All Time Low")
    d["total_return"] = _extract(text, "10Y Total Return")
    d["avg_annual_return"] = _extract(text, "Avg Annual Ret")
    d["sma_50"] = _extract(text, "SMA 50")
    d["sma_200"] = _extract(text, "SMA 200")
    d["rsi"] = _extract(text, "RSI (14)")
    d["macd"] = _extract(text, "MACD")
    d["volatility"] = _extract(text, "Volatility")
    d["sharpe"] = _extract(text, "Sharpe Ratio")
    d["bull_phase"] = _extract(text, "Bull phase")
    d["bear_phase"] = _extract(text, "Bear phase")
    d["max_drawdown"] = _extract(text, "Max Drawdown")
    d["max_dd_duration"] = _extract(text, "Max DD Duration")
    d["support"] = _extract(text, "Support")
    d["resistance"] = _extract(text, "Resistance")
    d["best_month"] = _extract(text, "Best Month")
    d["worst_month"] = _extract(text, "Worst Month")
    d["best_year"] = _extract(text, "Best Year")
    d["worst_year"] = _extract(text, "Worst Year")
    return d


def _parse_forecast(
    text: str | None,
) -> dict[str, Any]:
    """Parse forecast_stock output."""
    if text is None:
        return {}
    d: dict[str, Any] = {}
    d["current_price"] = _extract(text, "CURRENT PRICE")
    d["sentiment"] = _extract(text, "SENTIMENT")
    # Extract targets: "3M Target : $250 (+5.2%) [$230 – $270]"
    d["targets"] = {}
    for horizon in ["3M", "6M", "9M"]:
        m = re.search(
            rf"{horizon}\s+Target\s*:\s*(.+)",
            text,
        )
        if m:
            d["targets"][horizon] = m.group(1).strip()

    # Accuracy metrics
    d["mae"] = _extract(text, "MAE")
    d["rmse"] = _extract(text, "RMSE")
    d["mape"] = _extract(text, "MAPE")
    d["accuracy_error"] = _extract(text, "Accuracy")
    return d


def _parse_stock_info(text: str) -> dict[str, Any]:
    """Parse get_stock_info JSON text into a dict."""
    import json

    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}


def _section_header(
    ticker: str,
    info: dict[str, Any],
) -> str:
    """Section 1: Stock header."""
    from market_utils import safe_str

    name = safe_str(info.get("company_name")) or ticker
    sector = safe_str(info.get("sector")) or "\u2014"
    industry = safe_str(info.get("industry")) or "\u2014"
    mcap = info.get("market_cap")
    pe = info.get("pe_ratio")
    currency = safe_str(info.get("currency")) or "USD"
    price = info.get("current_price", "\u2014")

    mcap_str = "—"
    if mcap:
        try:
            mcap_f = float(mcap)
            if mcap_f >= 1e12:
                mcap_str = f"{mcap_f / 1e12:.2f}T"
            elif mcap_f >= 1e9:
                mcap_str = f"{mcap_f / 1e9:.2f}B"
            elif mcap_f >= 1e6:
                mcap_str = f"{mcap_f / 1e6:.1f}M"
            else:
                mcap_str = f"{mcap_f:,.0f}"
        except (ValueError, TypeError):
            mcap_str = str(mcap)

    try:
        pe_str = f"{float(pe):.1f}" if pe else "—"
    except (ValueError, TypeError):
        pe_str = str(pe)

    try:
        price_str = (
            f"{float(price):,.2f}"
            if price and price != "—"
            else "—"
        )
    except (ValueError, TypeError):
        price_str = str(price)

    return (
        f"## {name} ({ticker})\n\n"
        f"| Metric | Value |\n"
        f"|--------|-------|\n"
        f"| Sector | {sector} |\n"
        f"| Industry | {industry} |\n"
        f"| Current Price | {currency} {price_str} |\n"
        f"| Market Cap | {currency} {mcap_str} |\n"
        f"| P/E Ratio | {pe_str} |\n"
    )


def _section_technicals(a: dict[str, Any]) -> str:
    """Section 2: Technical analysis table."""
    if not a.get("current_price"):
        return ""
    rows = [
        ("Current Price", a.get("current_price", "—")),
        ("All-Time High", a.get("all_time_high", "—")),
        ("All-Time Low", a.get("all_time_low", "—")),
        ("SMA 50", a.get("sma_50", "—")),
        ("SMA 200", a.get("sma_200", "—")),
        ("RSI (14)", a.get("rsi", "—")),
        ("MACD", a.get("macd", "—")),
        ("Volatility", a.get("volatility", "—")),
        ("Sharpe Ratio", a.get("sharpe", "—")),
        ("Bull Phase", a.get("bull_phase", "—")),
        ("Bear Phase", a.get("bear_phase", "—")),
        ("Max Drawdown", a.get("max_drawdown", "—")),
        ("DD Duration", a.get("max_dd_duration", "—")),
        ("Support", a.get("support", "—")),
        ("Resistance", a.get("resistance", "—")),
    ]
    lines = [
        "\n### Technical Analysis\n",
        "| Indicator | Value |",
        "|-----------|-------|",
    ]
    for label, val in rows:
        lines.append(f"| {label} | {val} |")
    return "\n".join(lines) + "\n"


def _section_forecast(f: dict[str, Any]) -> str:
    """Section 3: Forecast & price targets table."""
    if not f.get("targets"):
        return ""
    lines = [
        "\n### Forecast & Price Targets\n",
        "| Horizon | Target |",
        "|---------|--------|",
    ]
    for h in ["3M", "6M", "9M"]:
        t = f["targets"].get(h, "—")
        lines.append(f"| {h} | {t} |")

    sentiment = f.get("sentiment", "—")
    lines.append(f"\n**Sentiment**: {sentiment}\n")

    # Accuracy
    if f.get("mae"):
        lines.append("**Model Accuracy**\n")
        lines.append(f"- MAE: {f['mae']}")
        if f.get("rmse"):
            lines.append(f"- RMSE: {f['rmse']}")
        if f.get("mape"):
            lines.append(f"- MAPE: {f['mape']}")
    elif f.get("accuracy_error"):
        lines.append(f"**Accuracy**: {f['accuracy_error']}")

    return "\n".join(lines) + "\n"


def _section_calendar(a: dict[str, Any]) -> str:
    """Section 4: Calendar performance."""
    if not a.get("best_month"):
        return ""
    rows = [
        ("Best Month", a.get("best_month", "—")),
        ("Worst Month", a.get("worst_month", "—")),
        ("Best Year", a.get("best_year", "—")),
        ("Worst Year", a.get("worst_year", "—")),
        ("10Y Return", a.get("total_return", "—")),
        ("Avg Annual", a.get("avg_annual_return", "—")),
    ]
    lines = [
        "\n### Calendar Performance\n",
        "| Period | Return |",
        "|--------|--------|",
    ]
    for label, val in rows:
        lines.append(f"| {label} | {val} |")
    return "\n".join(lines) + "\n"


def build_report(
    tool_results: dict[str, str],
    ticker: str = "",
) -> str:
    """Build a deterministic markdown report from tool output.

    Args:
        tool_results: Dict mapping tool names to their
            text output.  Expected keys:
            ``"get_stock_info"``,
            ``"analyse_stock_price"``,
            ``"forecast_stock"``.
        ticker: Ticker symbol (fallback if not in info).

    Returns:
        Markdown string with 5 data sections.
    """
    info_text = tool_results.get("get_stock_info", "")
    analysis_text = tool_results.get("analyse_stock_price", "")
    forecast_text = tool_results.get("forecast_stock", "")

    info = _parse_stock_info(info_text)
    analysis = _parse_analysis(analysis_text)
    forecast = _parse_forecast(forecast_text)

    if not ticker:
        ticker = info.get("ticker", "UNKNOWN")

    sections = [
        _section_header(ticker, info),
        _section_technicals(analysis),
        _section_forecast(forecast),
        _section_calendar(analysis),
    ]

    report = "\n".join(s for s in sections if s)

    _logger.info(
        "Report template built for %s (%d chars)",
        ticker,
        len(report),
    )
    return report


# ── Verdict prompt for LLM ─────────────────────────
VERDICT_SYSTEM_PROMPT = (
    "You are a stock analyst. Given the data sections "
    "above, provide ONLY:\n"
    "1. A Buy/Hold/Sell recommendation with confidence "
    "(e.g. 'Buy — 75% confidence')\n"
    "2. 2-3 key risks (bullet points)\n"
    "3. A 3-4 sentence investment thesis\n\n"
    "Do NOT repeat any data from the tables. "
    "Keep your response under 250 tokens."
)
