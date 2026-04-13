"""Recommendation tools for the Recommendation Agent.

Generate portfolio recommendations via the Smart Funnel
pipeline, retrieve history, and check performance.
Uses async PG functions via ``asyncio.run`` bridge.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import date, datetime, timedelta, timezone

from langchain_core.tools import tool

from tools._ticker_linker import get_current_user

_logger = logging.getLogger(__name__)

_SEVERITY_ICONS: dict[str, str] = {
    "high": "\U0001f534",    # 🔴
    "medium": "\U0001f7e1",  # 🟡
    "low": "\U0001f535",     # 🔵
}

_STATUS_ICONS: dict[str, str] = {
    "correct": "\u2705",    # ✅
    "incorrect": "\u274c",  # ❌
    "neutral": "\u26aa",    # ⚪
    "active": "\U0001f7e2", # 🟢
    "expired": "\u23f3",    # ⏳
}


def _get_user_or_error() -> str:
    """Get current user_id or raise."""
    uid = get_current_user()
    if not uid:
        raise RuntimeError(
            "No user context — cannot access "
            "recommendation data.",
        )
    return uid


def _run_async(coro):
    """Run an async coroutine from sync tool context."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # Inside an existing event loop — schedule
        fut = asyncio.run_coroutine_threadsafe(
            coro, loop,
        )
        return fut.result(timeout=120)
    return asyncio.run(coro)


def _format_recs(run: dict, recs: list[dict]) -> str:
    """Format recommendations as markdown."""
    lines = [
        "[Source: postgresql]",
        f"**Portfolio Recommendations** "
        f"(run: {run.get('run_date', 'N/A')})\n",
        f"Health: **{run.get('health_label', '?')}** "
        f"({run.get('health_score', 0):.0f}/100) | "
        f"Scanned: {run.get('candidates_scanned', 0)} "
        f"| Passed: "
        f"{run.get('candidates_passed', 0)}\n",
    ]

    if run.get("health_assessment"):
        lines.append(
            f"_{run['health_assessment']}_\n"
        )

    if not recs:
        lines.append(
            "No recommendations at this time."
        )
        return "\n".join(lines)

    lines.extend([
        "| # | Severity | Ticker | Action | "
        "Rationale | Signals |",
        "|---|----------|--------|--------|"
        "-----------|---------|",
    ])

    for i, r in enumerate(recs, 1):
        sev = r.get("severity", "low")
        icon = _SEVERITY_ICONS.get(sev, sev)
        ticker = r.get("ticker") or "—"
        action = r.get("action", "?")
        rationale = (
            r.get("rationale", "")[:80]
        )
        signals = r.get("data_signals", {})
        sig_str = ", ".join(
            f"{k}={v}"
            for k, v in (signals or {}).items()
        )[:60]
        lines.append(
            f"| {i} | {icon} {sev} | "
            f"{ticker} | {action} | "
            f"{rationale} | {sig_str} |"
        )

    lines.append(
        "\n_Recommendations are informational "
        "and not financial advice._"
    )
    return "\n".join(lines)


# -----------------------------------------------------------
# Tool 1: generate_recommendations
# -----------------------------------------------------------


@tool
def generate_recommendations(
    force_refresh: str = "false",
    scope: str = "india",
) -> str:
    """Generate portfolio recommendations.

    Runs the Smart Funnel pipeline (pre-filter,
    gap analysis, LLM reasoning) to produce
    data-driven buy/sell/hold recommendations.

    Args:
        force_refresh: "true" to skip cache, "false"
            to use cached if available.
        scope: Market scope (india/us/all).

    Source: Iceberg + PostgreSQL.
    """
    user_id = _get_user_or_error()
    _force = str(force_refresh).lower() in (
        "true", "1", "yes",
    )

    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )
    from sqlalchemy.pool import NullPool
    from config import get_settings

    _eng = create_async_engine(
        get_settings().database_url,
        poolclass=NullPool,
    )
    _factory = async_sessionmaker(
        _eng, class_=AsyncSession,
    )

    async def _generate():
        from backend.db.pg_stocks import (
            expire_old_recommendations,
            get_latest_recommendation_run,
            get_recommendations_for_run,
            insert_recommendation_run,
            insert_recommendations,
        )

        # ── Step 1: Reuse existing run if available ──
        # Non-force calls always return the latest
        # existing run. Only superusers with force
        # can trigger a new pipeline run.
        if not _force:
            async with _factory() as session:
                latest = (
                    await get_latest_recommendation_run(
                        session, user_id,
                        scope=scope,
                    )
                )
            if latest and latest.get("run_id"):
                async with _factory() as session:
                    recs = (
                        await
                        get_recommendations_for_run(
                            session,
                            latest["run_id"],
                        )
                    )
                if recs:
                    await _eng.dispose()
                    return _format_recs(
                        latest, recs,
                    )

        # ── Step 2: Quota gate for new generation ────
        if not _force:
            from jobs.recommendation_engine import (
                check_recommendation_quota,
            )

            quota = check_recommendation_quota(
                user_id, scope=scope,
            )
            if not quota.get("allowed"):
                await _eng.dispose()
                return (
                    "[Source: recommendation_engine]\n"
                    f"**Quota reached**: "
                    f"{quota.get('reason', '')}\n"
                    "Only superusers can force-"
                    "generate beyond the quota."
                )

        # Run full pipeline
        from jobs.recommendation_engine import (
            stage1_prefilter,
            stage2_gap_analysis,
            stage3_llm_reasoning,
        )
        import time as _time

        t0 = _time.monotonic()

        s1 = stage1_prefilter(scope=scope)
        if s1.empty:
            await _eng.dispose()
            return (
                "No candidates passed pre-filter. "
                "Try again later when more data "
                "is available."
            )

        s2 = stage2_gap_analysis(
            user_id, s1, scope=scope,
        )
        s3 = stage3_llm_reasoning(s2)

        duration = _time.monotonic() - t0

        # Persist to PG
        run_id = str(uuid.uuid4())
        run_data = {
            "run_id": run_id,
            "user_id": user_id,
            "run_date": date.today(),
            "run_type": "chat",
            "scope": scope,
            "portfolio_snapshot": (
                s2.get("portfolio_summary", {})
            ),
            "health_score": s3.get(
                "health_score", 0,
            ),
            "health_label": s3.get(
                "health_label", "unknown",
            ),
            "health_assessment": s3.get(
                "portfolio_health_assessment",
            ),
            "candidates_scanned": len(s1),
            "candidates_passed": len(
                s2.get("candidates", []),
            ),
            "llm_model": s3.get("llm_model"),
            "llm_tokens_used": s3.get(
                "llm_tokens_used",
            ),
            "duration_secs": round(duration, 2),
        }

        raw_recs = s3.get("recommendations", [])
        cand_map = {
            c["ticker"]: c
            for c in s2.get("candidates", [])
        }
        rec_rows = []
        for r in raw_recs:
            ticker = r.get("ticker")
            c = cand_map.get(ticker, {})
            rec_rows.append({
                "id": str(uuid.uuid4()),
                "run_id": run_id,
                "tier": r.get("tier", "explore"),
                "category": r.get(
                    "category", "general",
                ),
                "ticker": ticker,
                "action": r.get("action", "hold"),
                "severity": r.get(
                    "severity", "low",
                ),
                "rationale": r.get(
                    "rationale", "",
                ),
                "expected_impact": r.get(
                    "expected_impact",
                ),
                "data_signals": r.get(
                    "data_signals", {},
                ),
                "price_at_rec": (
                    r.get("price_at_rec")
                    or c.get("current_price")
                ),
                "target_price": (
                    r.get("target_price")
                    or c.get("target_price")
                ),
                "expected_return_pct": (
                    r.get("expected_return_pct")
                    or c.get("forecast_3m_pct")
                ),
                "index_tags": r.get("index_tags"),
                "status": "active",
            })

        async with _factory() as session:
            await insert_recommendation_run(
                session, run_data,
            )
            if rec_rows:
                await insert_recommendations(
                    session, run_id, rec_rows,
                )
            await expire_old_recommendations(
                session, user_id, run_id,
            )

        # Return formatted
        async with _factory() as session:
            recs = (
                await get_recommendations_for_run(
                    session, run_id,
                )
            )

        await _eng.dispose()
        run_dict = dict(run_data)
        return _format_recs(run_dict, recs)

    return _run_async(_generate())


# -----------------------------------------------------------
# Tool 2: get_recommendation_history
# -----------------------------------------------------------


@tool
def get_recommendation_history(
    months_back: int = 6,
) -> str:
    """Get recommendation run history with hit rates.

    Shows past recommendation runs, how many picks
    each had, and aggregate performance stats.

    Args:
        months_back: How many months to look back.

    Source: PostgreSQL.
    """
    user_id = _get_user_or_error()

    from backend.db.engine import get_session_factory

    factory = get_session_factory()

    async def _history():
        from backend.db.pg_stocks import (
            get_recommendation_history as _get_hist,
            get_recommendation_stats,
        )

        async with factory() as session:
            runs = await _get_hist(
                session, user_id, months_back,
            )
            stats = await get_recommendation_stats(
                session, user_id,
            )

        if not runs:
            return (
                "No recommendation history found. "
                "Ask me to generate recommendations "
                "first."
            )

        lines = [
            "[Source: postgresql]",
            f"**Recommendation History** "
            f"(last {months_back} months)\n",
            "| Date | Health | Score | "
            "Recs | Type |",
            "|------|--------|-------|"
            "-----|------|",
        ]
        for r in runs:
            lines.append(
                f"| {r.get('run_date', '?')} "
                f"| {r.get('health_label', '?')} "
                f"| {r.get('health_score', 0):.0f} "
                f"| {r.get('rec_count', 0)} "
                f"| {r.get('run_type', '?')} |"
            )

        # Aggregate stats
        if stats.get("total_runs", 0) > 0:
            lines.extend([
                "",
                "**Aggregate Stats**",
                f"- Total runs: "
                f"{stats['total_runs']}",
                f"- Total recommendations: "
                f"{stats['total_recs']}",
                f"- Outcomes tracked: "
                f"{stats['total_outcomes']}",
                f"- Hit rate: "
                f"{stats['hit_rate_pct']}%",
                f"- Avg return: "
                f"{stats['avg_return_pct']}%",
                f"- Avg excess return: "
                f"{stats['avg_excess_return_pct']}%",
            ])

        return "\n".join(lines)

    return _run_async(_history())


# -----------------------------------------------------------
# Tool 3: get_recommendation_performance
# -----------------------------------------------------------


@tool
def get_recommendation_performance(
    run_id: str | None = None,
    ticker: str | None = None,
) -> str:
    """Get performance details for recommendations.

    Shows individual recommendation outcomes with
    status icons.

    Args:
        run_id: Specific run to inspect.
        ticker: Filter by ticker symbol.

    Source: PostgreSQL.
    """
    user_id = _get_user_or_error()

    from backend.db.engine import get_session_factory

    factory = get_session_factory()

    async def _performance():
        from backend.db.pg_stocks import (
            get_latest_recommendation_run,
            get_recommendations_for_run,
        )

        # Resolve run_id
        rid = run_id
        if not rid:
            async with factory() as session:
                latest = (
                    await get_latest_recommendation_run(
                        session, user_id,
                    )
                )
            if not latest:
                return (
                    "No recommendations found. "
                    "Generate some first."
                )
            rid = latest["run_id"]

        async with factory() as session:
            recs = (
                await get_recommendations_for_run(
                    session, rid,
                )
            )

        if ticker:
            recs = [
                r for r in recs
                if r.get("ticker") == ticker.upper()
            ]

        if not recs:
            msg = "No recommendations found"
            if ticker:
                msg += f" for {ticker}"
            return f"{msg}."

        lines = [
            "[Source: postgresql]",
            f"**Recommendation Performance** "
            f"(run: {rid[:8]}...)\n",
            "| Status | Ticker | Action | "
            "Severity | Price@Rec | Target | "
            "Rationale |",
            "|--------|--------|--------|"
            "----------|-----------|--------|"
            "-----------|",
        ]
        for r in recs:
            status = r.get("status", "active")
            icon = _STATUS_ICONS.get(
                status, status,
            )
            tkr = r.get("ticker") or "—"
            action = r.get("action", "?")
            sev = r.get("severity", "?")
            price = r.get("price_at_rec")
            price_s = (
                f"{price:.2f}" if price else "—"
            )
            target = r.get("target_price")
            target_s = (
                f"{target:.2f}" if target else "—"
            )
            rationale = (
                r.get("rationale", "")[:60]
            )
            lines.append(
                f"| {icon} {status} | {tkr} | "
                f"{action} | {sev} | {price_s} | "
                f"{target_s} | {rationale} |"
            )

        lines.append(
            "\n_Recommendations are informational "
            "and not financial advice._"
        )
        return "\n".join(lines)

    return _run_async(_performance())


# -----------------------------------------------------------
# Tool 4: get_recommendation_detail
# -----------------------------------------------------------


@tool
def get_recommendation_detail(
    ticker: str,
) -> str:
    """Get comprehensive fundamentals, technicals,
    forecast, and quality data for a specific stock.

    Pulls from all Iceberg analytics tables to show
    why a stock was recommended and its current state.

    Args:
        ticker: Stock symbol (e.g. AHLUCONT.NS).

    Source: DuckDB (Iceberg tables).
    """
    from db.duckdb_engine import (
        get_connection,
        _create_view,
    )

    ticker = ticker.strip().upper()
    conn = get_connection()
    sections: list[str] = []

    try:
        # ── Company Info ──────────────────────────
        _create_view(conn, "stocks.company_info")
        ci = conn.execute(
            "SELECT * FROM company_info "
            f"WHERE ticker = '{ticker}' "
            "ORDER BY fetched_at DESC LIMIT 1",
        ).fetchone()
        ci_cols = [
            d[0]
            for d in conn.description
        ] if conn.description else []
        ci_d = (
            dict(zip(ci_cols, ci)) if ci else {}
        )

        if ci_d:
            name = ci_d.get(
                "company_name", ticker,
            )
            sections.append(
                f"## {name} ({ticker})\n"
            )
            sections.append("### Company Profile")
            sections.append(
                f"| Metric | Value |"
            )
            sections.append("| --- | --- |")
            _ci_fields = [
                ("Sector", "sector"),
                ("Industry", "industry"),
                ("Market Cap", "market_cap"),
                ("Current Price", "current_price"),
                ("P/E Ratio", "pe_ratio"),
                ("Price/Book", "price_to_book"),
                ("Book Value", "book_value"),
                ("Beta", "beta"),
                ("Dividend Yield", "dividend_yield"),
                ("Earnings Growth", "earnings_growth"),
                ("Revenue Growth", "revenue_growth"),
                ("Profit Margins", "profit_margins"),
                ("Analyst Target", "analyst_target"),
                ("Analyst Rating", "recommendation"),
            ]
            for label, key in _ci_fields:
                val = ci_d.get(key)
                if val is not None:
                    if key == "market_cap":
                        val = f"{val / 1e9:.2f}B"
                    elif key in (
                        "dividend_yield",
                        "earnings_growth",
                        "revenue_growth",
                        "profit_margins",
                    ):
                        val = f"{val * 100:.1f}%"
                    elif key == "recommendation":
                        labels = {
                            1: "Strong Buy",
                            2: "Buy",
                            3: "Hold",
                            4: "Sell",
                            5: "Strong Sell",
                        }
                        val = labels.get(
                            round(val),
                            f"{val:.1f}",
                        )
                    elif isinstance(val, float):
                        val = f"{val:.2f}"
                    sections.append(
                        f"| {label} | {val} |"
                    )
        else:
            sections.append(
                f"## {ticker}\n"
                "No company info available."
            )

        # ── Piotroski F-Score ─────────────────────
        _create_view(conn, "stocks.piotroski_scores")
        pi = conn.execute(
            "SELECT * FROM piotroski_scores "
            f"WHERE ticker = '{ticker}' "
            "ORDER BY score_date DESC LIMIT 1",
        ).fetchone()
        pi_cols = [
            d[0]
            for d in conn.description
        ] if conn.description else []
        pi_d = (
            dict(zip(pi_cols, pi)) if pi else {}
        )

        if pi_d:
            score = pi_d.get("total_score", 0)
            quality = (
                "Strong"
                if score >= 7
                else "Moderate"
                if score >= 4
                else "Weak"
            )
            sections.append(
                f"\n### Piotroski F-Score: "
                f"**{score}/9** ({quality})"
            )
            _pi_criteria = [
                ("ROA positive", "roa_positive"),
                (
                    "Operating CF positive",
                    "operating_cf_positive",
                ),
                ("ROA increasing", "roa_increasing"),
                (
                    "CF > Net Income",
                    "cf_gt_net_income",
                ),
                (
                    "Leverage decreasing",
                    "leverage_decreasing",
                ),
                (
                    "Current ratio up",
                    "current_ratio_increasing",
                ),
                ("No dilution", "no_dilution"),
                (
                    "Gross margin up",
                    "gross_margin_increasing",
                ),
                (
                    "Asset turnover up",
                    "asset_turnover_increasing",
                ),
            ]
            for label, key in _pi_criteria:
                val = pi_d.get(key)
                icon = (
                    "pass"
                    if val
                    else "fail"
                )
                sections.append(
                    f"- {label}: **{icon}**"
                )

        # ── Technical Analysis ────────────────────
        _create_view(conn, "stocks.analysis_summary")
        an = conn.execute(
            "SELECT * FROM analysis_summary "
            f"WHERE ticker = '{ticker}' "
            "ORDER BY analysis_date DESC "
            "LIMIT 1",
        ).fetchone()
        an_cols = [
            d[0]
            for d in conn.description
        ] if conn.description else []
        an_d = (
            dict(zip(an_cols, an)) if an else {}
        )

        if an_d:
            sections.append(
                "\n### Technical Indicators"
            )
            sections.append(
                "| Indicator | Value |"
            )
            sections.append("| --- | --- |")
            _an_fields = [
                ("Sharpe Ratio", "sharpe_ratio"),
                (
                    "Annualized Return",
                    "annualized_return_pct",
                ),
                (
                    "Volatility",
                    "annualized_volatility_pct",
                ),
                (
                    "Max Drawdown",
                    "max_drawdown_pct",
                ),
                ("SMA 50", "sma_50_signal"),
                ("SMA 200", "sma_200_signal"),
                ("RSI", "rsi_signal"),
                ("MACD", "macd_signal_text"),
            ]
            for label, key in _an_fields:
                val = an_d.get(key)
                if val is not None:
                    if isinstance(val, float):
                        val = f"{val:.2f}"
                        if "pct" in key:
                            val += "%"
                    sections.append(
                        f"| {label} | {val} |"
                    )

        # ── Forecast ──────────────────────────────
        _create_view(conn, "stocks.forecast_runs")
        fc = conn.execute(
            "SELECT * FROM forecast_runs "
            f"WHERE ticker = '{ticker}' "
            "AND horizon_months > 0 "
            "ORDER BY run_date DESC LIMIT 1",
        ).fetchone()
        fc_cols = [
            d[0]
            for d in conn.description
        ] if conn.description else []
        fc_d = (
            dict(zip(fc_cols, fc)) if fc else {}
        )

        if fc_d:
            sections.append("\n### Price Forecast")
            sections.append(
                "| Horizon | Target | Change | "
                "Confidence |"
            )
            sections.append(
                "| --- | --- | --- | --- |"
            )
            for h in (3, 6, 9):
                tgt = fc_d.get(
                    f"target_{h}m_price",
                )
                pct = fc_d.get(
                    f"target_{h}m_pct_change",
                )
                lo = fc_d.get(f"target_{h}m_lower")
                hi = fc_d.get(f"target_{h}m_upper")
                if tgt is not None:
                    band = ""
                    if lo and hi:
                        band = (
                            f"{lo:.0f} – {hi:.0f}"
                        )
                    sections.append(
                        f"| {h}M | "
                        f"{tgt:.2f} | "
                        f"{pct:+.1f}% | "
                        f"{band} |"
                    )
            mape = fc_d.get("mape")
            if mape is not None:
                sections.append(
                    f"\nForecast accuracy "
                    f"(MAPE): **{mape:.1f}%**"
                )

        # ── Sentiment ─────────────────────────────
        _create_view(conn, "stocks.sentiment_scores")
        se = conn.execute(
            "SELECT * FROM sentiment_scores "
            f"WHERE ticker = '{ticker}' "
            "ORDER BY score_date DESC LIMIT 1",
        ).fetchone()
        se_cols = [
            d[0]
            for d in conn.description
        ] if conn.description else []
        se_d = (
            dict(zip(se_cols, se)) if se else {}
        )

        if se_d:
            score = se_d.get("avg_score", 0)
            count = se_d.get("headline_count", 0)
            label = (
                "Bullish"
                if score > 0.3
                else "Bearish"
                if score < -0.3
                else "Neutral"
            )
            sections.append(
                f"\n### Sentiment: "
                f"**{label}** ({score:+.2f}, "
                f"{count} headlines)"
            )

    finally:
        conn.close()

    if not sections:
        return (
            f"No data found for {ticker}. "
            "It may not be in the analysis "
            "universe."
        )

    return "\n".join(sections)
