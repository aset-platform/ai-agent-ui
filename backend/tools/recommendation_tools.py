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
    force_refresh: bool = False,
    scope: str = "india",
) -> str:
    """Generate portfolio recommendations.

    Runs the Smart Funnel pipeline (pre-filter,
    gap analysis, LLM reasoning) to produce
    data-driven buy/sell/hold recommendations.

    Args:
        force_refresh: Skip cache and regenerate.
        scope: Market scope (india/us/all).

    Source: Iceberg + PostgreSQL.
    """
    user_id = _get_user_or_error()

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

        # Check for fresh run (<24h, same scope)
        async with _factory() as session:
            latest = (
                await get_latest_recommendation_run(
                    session, user_id,
                    scope=scope,
                )
            )

        if latest and not force_refresh:
            ca = latest.get("created_at")
            if ca:
                if isinstance(ca, str):
                    ca = datetime.fromisoformat(ca)
                if ca.tzinfo is None:
                    ca = ca.replace(
                        tzinfo=timezone.utc,
                    )
                age = datetime.now(timezone.utc) - ca
                if age < timedelta(days=1):
                    async with _factory() as session:
                        recs = (
                            await
                            get_recommendations_for_run(
                                session,
                                latest["run_id"],
                            )
                        )
                    await _eng.dispose()
                    return _format_recs(latest, recs)

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
