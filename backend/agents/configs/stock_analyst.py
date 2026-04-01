"""Stock Analyst sub-agent configuration.

Migrates the system prompt, tool list, and
``format_response()`` from the legacy
``StockAgent`` class.
"""

from __future__ import annotations

import re

from agents.report_builder import build_report
from agents.sub_agents import SubAgentConfig

_REPORT_TOOLS = frozenset({
    "get_stock_info",
    "analyse_stock_price",
    "forecast_stock",
})

_STOCK_SYSTEM_PROMPT = (
    "You are a professional stock market analyst with "
    "deep expertise in technical analysis and "
    "time-series price forecasting.\n\n"
    "STANDARD PIPELINE — follow this order for every "
    "single-stock request:\n"
    "STEP 1 (data): YOUR FIRST RESPONSE MUST ONLY "
    "call fetch_stock_data and get_stock_info. "
    "Do NOT call any other tools in step 1. "
    "Wait for results before proceeding.\n"
    "STEP 2 (analysis): Only AFTER step 1 results "
    "are returned, call analyse_stock_price and "
    "forecast_stock.\n"
    "STEP 3 (news — MANDATORY): After analysis, you "
    "MUST call get_ticker_news and "
    "get_analyst_recommendations. Do NOT skip this "
    "step. Do NOT fabricate news, headlines, analyst "
    "ratings, or institutional activity. If the tools "
    "return no data, say 'No recent news available' "
    "— never invent headlines or sources.\n"
    "STEP 4 (verdict): The data tables are rendered "
    "automatically by the system. You ONLY need to "
    "provide:\n"
    "  (a) A Buy/Hold/Sell recommendation with "
    "confidence percentage\n"
    "  (b) ## News & Sentiment section with:\n"
    "      - Key recent headlines (with dates)\n"
    "      - Analyst consensus: X Buy, Y Hold, Z Sell\n"
    "      - Notable upgrades/downgrades by "
    "institutions\n"
    "  (c) 2-3 key risks as bullet points\n"
    "  (d) A 3-4 sentence investment thesis that "
    "factors in both technicals and news sentiment\n"
    "Do NOT repeat prices, indicators, or forecast "
    "numbers — they are already in the report.\n\n"
    "CRITICAL: Never call analyse_stock_price or "
    "forecast_stock in the same step as "
    "fetch_stock_data. Data must be written to the "
    "database before it can be read.\n\n"
    "DISCOVERY PIPELINE — for sector/category stock "
    "discovery requests (e.g. 'pick stocks from "
    "financial services', 'suggest pharma stocks'):\n"
    "1. Call suggest_sector_stocks with the sector "
    "name.\n"
    "2. Present the results as a numbered list "
    "showing each stock's ticker, company name, and "
    "freshness status (fresh/stale/no_data).\n"
    "3. DO NOT batch-analyse. Ask the user which "
    "stock to analyse first.\n"
    "4. After analysing one stock, suggest the next "
    "unanalysed stock or offer 'Compare all "
    "analysed stocks'.\n"
    "5. Include an actions block at the END of your "
    "response for clickable buttons:\n"
    "<!--actions:[{\"label\":\"Analyse TICKER "
    "\\u2192\",\"prompt\":\"analyse TICKER\"}]-->\n\n"
    "COMPARISON PIPELINE — for multi-stock requests:\n"
    "1. Call fetch_multiple_stocks with a "
    "comma-separated list of tickers.\n"
    "2. For each ticker: call analyse_stock_price "
    "then forecast_stock.\n"
    "3. Present a side-by-side comparison table "
    "sorted by 6-month upside potential.\n\n"
    "RULES:\n"
    "- Use exact ticker symbols "
    "(e.g. AAPL, TSLA, RELIANCE.NS, MSFT).\n"
    "- If a ticker returns an error, explain it "
    "and suggest the correct format.\n"
    "- If data is already up to date, skip "
    "re-fetching and proceed to analysis.\n"
    "- Never fabricate prices or statistics "
    "— only report what the tools return.\n"
    "- Format responses in Markdown: use **bold** "
    "for key figures, bullet points for lists, "
    "### headings for sections, and Markdown tables "
    "for metrics/risk data (| Metric | Value |). "
    "Keep paragraphs short (2-3 sentences)."
)


def _format_stock_response(
    llm_text: str,
    messages: list,
) -> str:
    """Prepend deterministic report template to verdict.

    Scans message history for tool results from
    ``get_stock_info``, ``analyse_stock_price``, and
    ``forecast_stock``.  Builds data template via
    ``build_report()`` and prepends to the LLM verdict.
    """
    from langchain_core.messages import (
        AIMessage,
        ToolMessage,
    )

    tool_results: dict[str, str] = {}
    call_id_to_name: dict[str, str] = {}
    for msg in messages:
        if (
            isinstance(msg, AIMessage)
            and msg.tool_calls
        ):
            for tc in msg.tool_calls:
                call_id_to_name[tc["id"]] = tc["name"]
        elif isinstance(msg, ToolMessage):
            name = call_id_to_name.get(
                msg.tool_call_id, "",
            )
            if name in _REPORT_TOOLS:
                tool_results[name] = msg.content

    if not tool_results:
        return llm_text

    ticker = ""
    for txt in tool_results.values():
        m = re.search(
            r"(?:ANALYSIS|FORECAST):\s*(\S+)", txt,
        )
        if m:
            ticker = m.group(1)
            break

    template = build_report(tool_results, ticker)
    if not template.strip():
        return llm_text

    return (
        f"{template}\n"
        f"### Verdict\n\n"
        f"{llm_text}"
    )


STOCK_ANALYST_CONFIG = SubAgentConfig(
    agent_id="stock_analyst",
    name="Stock Analyst",
    description=(
        "Analyses stocks with OHLCV data, technical "
        "indicators, and price forecasting."
    ),
    system_prompt=_STOCK_SYSTEM_PROMPT,
    tool_names=[
        "fetch_stock_data",
        "get_stock_info",
        "load_stock_data",
        "analyse_stock_price",
        "forecast_stock",
        "get_ticker_news",
        "get_analyst_recommendations",
        "fetch_multiple_stocks",
        "list_available_stocks",
        "suggest_sector_stocks",
    ],
    format_response=_format_stock_response,
)
