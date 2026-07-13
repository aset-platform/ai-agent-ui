"""Composite QM Score — Sharpe(6M)/Blended RS/MDD(6M) percentile-rank
blend + ATR%/SMA200-distance closeness curves.

Factored out (Task 15, ASETPLTFRM Entry Strength Score plan) from the
post-loop QM Score block that used to live inline in
``insights_routes.py::_watchlist_stocks()`` (Task 11), so
``backend/jobs/entry_quality_snapshot.py`` (Task 14) can compute the
same real QM Score instead of shipping ``None`` placeholders.

**Behavior-preservation note:** the ``_ATR_PTS``/``_SMA_PTS`` curves,
``_pct_rank``, ``_closeness``, and the final weighted-blend formula
below are copied VERBATIM from the original inline block — do not
tweak any number here without separately re-validating against the
live Watchlist Stocks page, since real users see this score today.

**Input-shape note:** the original block operated on an already-built
``rows: list[WatchlistStockRow]`` whose ``sharpe_ratio``/
``blended_rs``/``mdd_6m``/``atr_pct``/``dist_sma200`` attributes were
themselves derived EARLIER in ``_watchlist_stocks()`` from OHLCV via a
separate per-ticker computation (Sharpe ratio, RS(3M)/RS(6M) vs.
Nifty, max drawdown, ATR%, distance-above-SMA200) — those five values
are the actual free variables this blend closes over, not raw OHLCV.
``compute_qm_scores`` therefore accepts a pre-computed
``{ticker: {"sharpe_ratio", "blended_rs", "mdd_6m", "atr_pct",
"dist_sma200"}}`` mapping rather than an OHLCV DataFrame — this is the
same five-field shape both call sites (the route's
``WatchlistStockRow`` objects, and the job's per-ticker
indicator-derived values) can already produce.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class QmResult:
    """Composite QM Score + its five weighted sub-factors for one
    ticker, computed within the batch passed to ``compute_qm_scores``.
    """

    score: float | None
    sharpe_pctile: float | None
    rs_pctile: float | None
    mdd_pctile: float | None
    atr_closeness: float | None
    sma200_closeness: float | None


# Composite score:
#   Sharpe(6M) 30% + Blended RS 30% + MDD(6M) 20%  → percentile rank
#   ATR%       10% + SMA200 Distance 10%            → closeness score
#
# Percentile rank: cross-stock rank 0-100 (higher = better).
# MDD(6M) is negative; shallower drawdown -> higher rank.
#
# Closeness score: fixed piecewise-linear curve (0-100) where
# the ideal range scores 100 and scores decay on both sides.
# ATR ideal: 3-4% (best risk-adjusted volatility for mean-rev).
# SMA200 ideal: 20-30% above (healthy trend, not overextended).
# Outside the defined range the curve is extrapolated linearly
# and clamped to [0, 100].
_ATR_PTS: list[tuple[float, float]] = [
    (2, 90),
    (3, 100),
    (4, 100),
    (5, 90),
    (6, 80),
    (7, 60),
    (8, 30),
    (10, 0),
]
_SMA_PTS: list[tuple[float, float]] = [
    (5, 70),
    (10, 90),
    (20, 100),
    (30, 100),
    (40, 90),
    (50, 70),
    (70, 30),
    (90, 0),
]


def _pct_rank(vals: list[float], v: float, n: int) -> float:
    if n <= 1:
        return 50.0
    return sorted(vals).index(v) / (n - 1) * 100


def _closeness(pts: list[tuple[float, float]], v: float) -> float:
    """Piecewise-linear score; extrapolates + clamps to [0,100]."""
    if v <= pts[0][0]:
        x0, y0 = pts[0]
        x1, y1 = pts[1]
        s = (y1 - y0) / (x1 - x0)
        return max(0.0, min(100.0, y0 + s * (v - x0)))
    if v >= pts[-1][0]:
        x0, y0 = pts[-2]
        x1, y1 = pts[-1]
        s = (y1 - y0) / (x1 - x0)
        return max(0.0, min(100.0, y1 + s * (v - x1)))
    for i in range(len(pts) - 1):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        if x0 <= v <= x1:
            t = (v - x0) / (x1 - x0)
            return max(0.0, min(100.0, y0 + t * (y1 - y0)))
    return 0.0


def compute_qm_scores(
    inputs: dict[str, dict[str, float | None]],
) -> dict[str, QmResult]:
    """Composite QM Score per ticker, ranked within *inputs*' batch.

    ``inputs`` maps ``ticker -> {"sharpe_ratio", "blended_rs",
    "mdd_6m", "atr_pct", "dist_sma200"}`` (each ``float | None``).

    Percentile ranks (Sharpe/RS/MDD) are computed across every ticker
    present in *inputs* — this mirrors the original block's behavior
    of ranking within whatever batch of rows it ran over, so callers
    MUST pass the full set of tickers they want ranked together in a
    single call (do not call this once per ticker).
    """
    _sharpe_vals = [
        v["sharpe_ratio"]
        for v in inputs.values()
        if v.get("sharpe_ratio") is not None
    ]
    _brs_vals = [
        v["blended_rs"]
        for v in inputs.values()
        if v.get("blended_rs") is not None
    ]
    _mdd_vals = [
        v["mdd_6m"] for v in inputs.values() if v.get("mdd_6m") is not None
    ]
    _ns = len(_sharpe_vals)
    _nb = len(_brs_vals)
    _nm = len(_mdd_vals)

    results: dict[str, QmResult] = {}
    for ticker, row in inputs.items():
        _sharpe = row.get("sharpe_ratio")
        _brs = row.get("blended_rs")
        _mdd = row.get("mdd_6m")
        _atr = row.get("atr_pct")
        _dist = row.get("dist_sma200")

        _sp = (
            _pct_rank(_sharpe_vals, _sharpe, _ns)
            if _sharpe is not None and _ns > 0
            else None
        )
        _bp = (
            _pct_rank(_brs_vals, _brs, _nb)
            if _brs is not None and _nb > 0
            else None
        )
        _mp = (
            _pct_rank(_mdd_vals, _mdd, _nm)
            if _mdd is not None and _nm > 0
            else None
        )
        _ap = _closeness(_ATR_PTS, _atr) if _atr is not None else None
        _wp = _closeness(_SMA_PTS, _dist) if _dist is not None else None

        _parts = [
            (_sp, 0.30),
            (_bp, 0.30),
            (_mp, 0.20),
            (_ap, 0.10),
            (_wp, 0.10),
        ]
        _avail = [(v, w) for v, w in _parts if v is not None]
        _score: float | None = None
        if _avail:
            _tw = sum(w for _, w in _avail)
            _score = round(sum(v * w for v, w in _avail) / _tw, 4)

        results[ticker] = QmResult(
            score=_score,
            sharpe_pctile=_sp,
            rs_pctile=_bp,
            mdd_pctile=_mp,
            atr_closeness=_ap,
            sma200_closeness=_wp,
        )
    return results
