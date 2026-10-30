"""Load + filter the labeled cohort from algo.entry_labeled_outcomes."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import pandas as pd
from sqlalchemy import text

_logger = logging.getLogger(__name__)

_NUMERIC = (
    "rsi2_at_entry", "dist_sma50_pct", "dist_sma200_pct",
    "ret_1d_prior", "ret_3d_prior", "gap_pct",
    "ess_absorption_volume_score", "ess_selling_deceleration_score",
    "ess_trend_stability_score", "qm_score", "ess_score",
    "qm_mdd_pctile", "qm_rs_pctile", "qm_sharpe_pctile",
    "return_pct", "mfe_pct", "mae_pct",
)


def filter_cohort(rows: list[dict], mode: str) -> list[dict]:
    """Filter cohort: filled AND outcome_settled AND label_win NOT None
    AND not dry_run AND (mode=='all' or row mode matches)."""
    out = []
    for r in rows:
        if not r.get("filled"):
            continue
        if not r.get("outcome_settled"):
            continue
        if r.get("label_win") is None:
            continue
        if r.get("dry_run"):
            continue
        if mode != "all" and r.get("mode") != mode:
            continue
        out.append(r)
    return out


def to_frame(rows: list[dict]) -> pd.DataFrame:
    """Build DataFrame, coerce numerics, derive breadth_oversold_pct,
    cast label_win to bool."""
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    for col in _NUMERIC:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    bt = pd.to_numeric(df.get("breadth_total"), errors="coerce")
    bo = pd.to_numeric(df.get("breadth_oversold"), errors="coerce")
    df["breadth_oversold_pct"] = (bo / bt).where(bt > 0, other=0.0)
    df["label_win"] = df["label_win"].astype(bool)
    return df


def load_labeled_rows(mode: str) -> list[dict]:
    """Sync entrypoint — async PG read via disposable_pg_session."""
    return asyncio.run(_load(mode))


async def _load(mode: str) -> list[dict]:
    """Async PG SELECT from algo.entry_labeled_outcomes."""
    from backend.db.engine import disposable_pg_session

    where_mode = "" if mode == "all" else "AND mode = :mode"
    sql = text(
        "SELECT * FROM algo.entry_labeled_outcomes "
        "WHERE filled AND outcome_settled AND label_win IS NOT NULL "
        f"AND NOT dry_run {where_mode}"
    )
    params: dict[str, Any] = (
        {} if mode == "all" else {"mode": mode}
    )
    async with disposable_pg_session() as s:
        res = await s.execute(sql, params)
        return [dict(m) for m in res.mappings().all()]
