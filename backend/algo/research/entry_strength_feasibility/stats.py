"""Rank-based winner/loser separation statistics (real-trades feasibility)."""
from __future__ import annotations

import math
import statistics

from scipy.stats import mannwhitneyu
from sklearn.metrics import roc_auc_score


def _clean(
    feature: list[float | None], label_win: list[bool]
) -> tuple[list[float], list[int]]:
    xs: list[float] = []
    ys: list[int] = []
    for f, lbl in zip(feature, label_win):
        if f is None or (isinstance(f, float) and math.isnan(f)):
            continue
        xs.append(float(f))
        ys.append(1 if lbl else 0)
    return xs, ys


def separation_auc(
    feature: list[float | None], label_win: list[bool]
) -> float:
    xs, ys = _clean(feature, label_win)
    if len(xs) < 2 or len(set(ys)) < 2:
        return 0.5
    return float(roc_auc_score(ys, xs))


def median_split_win_rate(
    feature: list[float | None], label_win: list[bool]
) -> tuple[float | None, float | None]:
    xs, ys = _clean(feature, label_win)
    if len(xs) < 2:
        return (None, None)
    med = statistics.median(xs)
    top = [y for x, y in zip(xs, ys) if x > med]
    bottom = [y for x, y in zip(xs, ys) if x <= med]
    if not top or not bottom:
        return (None, None)
    return (sum(top) / len(top), sum(bottom) / len(bottom))


def mannwhitney_p(
    feature: list[float | None], label_win: list[bool]
) -> float | None:
    xs, ys = _clean(feature, label_win)
    winners = [x for x, y in zip(xs, ys) if y == 1]
    losers = [x for x, y in zip(xs, ys) if y == 0]
    if not winners or not losers:
        return None
    if len(set(xs)) < 2:
        return None
    try:
        return float(
            mannwhitneyu(
                winners, losers, alternative="two-sided"
            ).pvalue
        )
    except ValueError:
        return None
