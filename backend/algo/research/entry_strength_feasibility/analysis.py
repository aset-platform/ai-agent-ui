"""Orchestrate per-feature + composite winner/loser separation."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from backend.algo.research.entry_strength_feasibility.dimensions import (
    COMPOSITE,
    DIMENSIONS,
)
from backend.algo.research.entry_strength_feasibility.stats import (
    mannwhitney_p,
    median_split_win_rate,
    separation_auc,
)


@dataclass(frozen=True)
class FeatureStat:
    dimension: str
    feature: str
    n: int
    auc: float
    separation: float
    win_rate_top: float | None
    win_rate_bottom: float | None
    mwu_p: float | None
    direction: str


@dataclass(frozen=True)
class CompositeStat:
    n: int
    auc: float
    win_rate_top: float | None
    win_rate_bottom: float | None


@dataclass(frozen=True)
class FeasibilityResult:
    mode: str
    n_total: int
    n_win: int
    n_loss: int
    first_date: str | None
    last_date: str | None
    min_n_ok: bool
    feature_stats: tuple[FeatureStat, ...]
    composite: CompositeStat


def composite_score(df: pd.DataFrame) -> pd.Series:
    total = pd.Series(0.0, index=df.index)
    for feat, sign in COMPOSITE:
        col = pd.to_numeric(df[feat], errors="coerce")
        std = col.std(ddof=0)
        if std and std > 0:
            z = (col - col.mean()) / std
        else:
            z = col * 0.0
        total = total + sign * z.fillna(0.0)
    return total


def _feature_stat(df: pd.DataFrame, dim: str, feat: str) -> FeatureStat:
    vals = df[feat].tolist()
    labels = df["label_win"].tolist()
    n = int(df[feat].notna().sum())
    auc = separation_auc(vals, labels)
    top, bottom = median_split_win_rate(vals, labels)
    return FeatureStat(
        dimension=dim, feature=feat, n=n, auc=auc,
        separation=abs(auc - 0.5), win_rate_top=top,
        win_rate_bottom=bottom, mwu_p=mannwhitney_p(vals, labels),
        direction="higher" if auc >= 0.5 else "lower",
    )


def analyze(df: pd.DataFrame, min_n: int) -> FeasibilityResult:
    mode = str(df["mode"].iloc[0]) if "mode" in df and len(df) else "?"
    n = len(df)
    n_win = int(df["label_win"].sum()) if n else 0
    dates = (
        pd.to_datetime(df["trade_date"]).dt.date
        if "trade_date" in df.columns and n else None
    )
    stats: list[FeatureStat] = []
    for dim, feats in DIMENSIONS.items():
        for feat in feats:
            if feat in df.columns:
                stats.append(_feature_stat(df, dim, feat))
    stats.sort(key=lambda s: (-s.separation, s.feature))

    comp = composite_score(df) if n else pd.Series(dtype=float)
    c_top, c_bottom = (
        median_split_win_rate(comp.tolist(), df["label_win"].tolist())
        if n else (None, None)
    )
    composite = CompositeStat(
        n=n,
        auc=separation_auc(comp.tolist(), df["label_win"].tolist())
        if n else 0.5,
        win_rate_top=c_top, win_rate_bottom=c_bottom,
    )
    return FeasibilityResult(
        mode=mode, n_total=n, n_win=n_win, n_loss=n - n_win,
        first_date=str(dates.min()) if dates is not None else None,
        last_date=str(dates.max()) if dates is not None else None,
        min_n_ok=n >= min_n,
        feature_stats=tuple(stats), composite=composite,
    )
