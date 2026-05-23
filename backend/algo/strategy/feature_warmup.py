"""Per-feature OHLCV warmup requirements (ASETPLTFRM-433).

A strategy AST references features (``FeatureRef`` nodes); each
feature has a minimum number of prior trading-day bars before its
value becomes meaningful. ``sma_200`` needs 200 prior bars,
``rsi_14`` needs 14, ``rsi_2`` needs 2.

``compute_strategy_warmup_days`` walks an AST and returns the
maximum warmup across all referenced features. The backtest job
uses this to pre-filter the universe to tickers whose OHLCV
history covers ``period_start - max_warmup_days``, eliminating
the entire ``Feature not in context`` silent-skip category.

Defaults:
- Window-derived features (``sma_N``, ``ema_N``, ``rsi_N``,
  ``atr_N``, ``roc_N``): the integer suffix.
- ``distance_from_sma_N`` / ``distance_from_smaN``: same N.
- Listed exceptions below.
- Unknown keys: ``DEFAULT_WARMUP_DAYS`` (conservative 200).

Market-level / regime / fundamental features (``regime_label``,
``stress_prob``, ``f_score``, ``nifty_above_sma200``, ...) require
universe-level history, not per-ticker history. Their warmup is
handled by the regime cache loaded at runtime start; this helper
records them as zero per-ticker warmup so they don't pollute the
filter.
"""

from __future__ import annotations

import re

DEFAULT_WARMUP_DAYS = 200

# Per-ticker warmup overrides for features whose name doesn't
# encode the window. Keys must match ``FEATURE_KEYS``.
_OVERRIDES: dict[str, int] = {
    # OHLCV scalars — single bar is enough.
    "today_ltp": 1,
    "today_open": 1,
    "today_high": 1,
    "today_low": 1,
    "today_vol": 1,
    "today_vwap": 1,
    "previous_close": 1,
    "gap_pct": 1,
    "today_range_pct": 1,
    # Technical aggregates without N in the key.
    "obv": 1,
    "volume_x_avg_20": 20,
    "volume_spike": 20,
    "up_down_vol_ratio_20": 20,
    "ema_20_slope_5bar": 25,
    "golden_cross_bars_ago": 200,
    "bb_width": 20,
    "range_expansion": 14,
    # 52-week proxies.
    "prox_52w": 252,
    "realized_vol_60d": 60,
    "sma200_slope": 200,
    # Momentum / relative strength (the suffix encodes months).
    "mom_3_1": 63,    # 3 trading months ~ 63 bars.
    "mom_6_1": 126,
    "mom_12_1": 252,
    "rs_vs_nifty_3m": 63,
    "rs_vs_nifty_6m": 126,
    "rs_vs_nifty_15m": 315,
    "rs_vs_sector_3m": 63,
    "beta_to_nifty": 60,
    "adx_14": 14,
    # Market-level / regime / fundamentals — per-ticker warmup 0.
    # Universe-level history is loaded separately at runtime start.
    "regime_label": 0,
    "stress_prob": 0,
    "pct_above_50sma": 0,
    "pct_above_200sma": 0,
    "market_breadth_pct_above_sma200": 0,
    "vix_close": 0,
    "vix_sma_20": 0,
    "nifty_above_sma200": 0,
    "nifty_30d_return_pct": 0,
    "midcap_largecap_ratio": 0,
    "advance_decline_ratio": 0,
    "minutes_since_open": 0,
    "today_vol_ratio_to_20d_avg": 20,
    "f_score": 252,  # Quarterly cadence; one year history.
    "roe": 0,
    "roce": 0,
    "pe_ratio": 0,
    "pb_ratio": 0,
    "debt_to_equity": 0,
    "current_ratio": 0,
    "operating_margin": 0,
    "earnings_growth": 0,
    "forecast_30d_return_pct": 0,
    "forecast_confidence": 0,
    "sector": 0,
    "sentiment_score": 0,
}

# Pattern: ``sma_50``, ``ema_20``, ``rsi_14``, ``roc_5``, ``atr_14``
_WINDOW_PREFIXES = ("sma_", "ema_", "rsi_", "roc_", "atr_")
# Pattern: ``distance_from_sma_5`` OR ``distance_from_sma5``
_DIST_RE = re.compile(r"^distance_from_(?:sma|ema|vwap)_?(\d+)$")


def warmup_for_feature(feature: str) -> int:
    """Return required prior-bar count for ``feature``.

    ``0`` for market-level / fundamental features whose data is not
    per-ticker. ``DEFAULT_WARMUP_DAYS`` for unrecognised keys (safe
    over-estimate; rules out IPOs across the board).
    """
    if feature in _OVERRIDES:
        return _OVERRIDES[feature]
    m = _DIST_RE.match(feature)
    if m:
        return int(m.group(1))
    for prefix in _WINDOW_PREFIXES:
        if feature.startswith(prefix):
            tail = feature[len(prefix):]
            try:
                return int(tail)
            except ValueError:
                continue
    return DEFAULT_WARMUP_DAYS


def compute_strategy_warmup_days(strategy_root_dict: dict) -> int:
    """Walk an AST root node and return the max warmup across all
    referenced features. Returns ``0`` for ASTs with no
    ``FeatureRef`` nodes (rare — usually composite roots).

    ``strategy_root_dict`` is the dump of ``Strategy.root`` —
    i.e., the AST tree. Pass ``strategy.root.model_dump(by_alias=True)``.
    """
    max_warmup = 0
    stack: list = [strategy_root_dict]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if "feature" in node and isinstance(node["feature"], str):
                w = warmup_for_feature(node["feature"])
                if w > max_warmup:
                    max_warmup = w
                continue
            for v in node.values():
                if isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(node, list):
            for item in node:
                if isinstance(item, (dict, list)):
                    stack.append(item)
    return max_warmup
