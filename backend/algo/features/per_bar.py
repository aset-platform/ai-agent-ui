"""Shared per-bar feature-assembly helper — FE-15b.

Single source of truth for the ``EvalContext.features`` dict
that every strategy AST evaluator sees. Used by:

- :mod:`backend.algo.backtest.runner` (backtest)
- :mod:`backend.algo.paper.runtime` (paper)
- :mod:`backend.algo.live.runtime` (live + dry-run)

Before FE-15b each runtime assembled this dict inline with
slightly different code paths — a known drift hazard. The
shared helper guarantees byte-identical features dicts across
all three runtimes for the same input.

It also implements the **cross-cadence overlay** rule from FE-15
spec §5: features at a higher cadence (e.g. daily,
``interval_sec=86400``) get injected under suffixed keys
(``{name}_1d``) when the strategy's primary cadence is lower
(intraday). Primary-cadence features stay unsuffixed.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

CadenceSuffix = str  # one of "_1d", "_1h", "_15m" (currently used)
PerBarFeatures = dict[str, Decimal | str | int]


def _suffix_keys(
    overlay: dict[str, Decimal | str] | None,
    suffix: CadenceSuffix,
) -> dict[str, Decimal | str]:
    """Return a new dict with every key renamed
    ``{original}{suffix}``. ``None`` input → empty dict."""
    if not overlay:
        return {}
    return {f"{k}{suffix}": v for k, v in overlay.items()}


def assemble_per_bar_features(
    *,
    bar_feats: dict[str, Decimal | str],
    market_regime: Decimal | None = None,
    market_trend: Decimal | None = None,
    factor_row: dict[str, Decimal] | None = None,
    regime_row: dict[str, Any] | None = None,
    daily_overlay: dict[str, Decimal | str] | None = None,
    hourly_overlay: dict[str, Decimal | str] | None = None,
    fifteen_min_overlay: dict[str, Decimal | str] | None = None,
) -> PerBarFeatures:
    """Assemble the per-bar features dict passed to the AST
    evaluator. Single source of truth across all 3 runtimes.

    Args:
        bar_feats: PRIMARY-cadence per-bar features (unsuffixed
            in the output). Already cadence-specific — caller
            looked these up at ``(ticker, ts_ns)`` for intraday
            or ``(ticker, bar_date)`` for daily.
        market_regime: ``nifty_above_sma200`` daily-derived
            value. Defaults to ``Decimal("0")`` if absent.
        market_trend: ``nifty_30d_return_pct`` daily-derived
            value. Defaults to ``Decimal("0")`` if absent.
        factor_row: ``stocks.daily_factors`` row for this
            ``(ticker, bar_date)``. Keys are factor-library
            names (``mom_12_1``, ``f_score``, etc.) — never
            collide with technical features. Merged unsuffixed.
        regime_row: ``stocks.regime_history`` row for this
            ``bar_date`` (``regime_label``, ``stress_prob``).
            Merged unsuffixed.
        daily_overlay: cross-cadence overlay from
            ``stocks.intraday_features`` at ``interval_sec=86400``
            for this ``(ticker, bar_date)``. Injected under
            ``{name}_1d`` keys. ONLY used when the strategy's
            primary cadence is < 86400 — the caller decides
            whether to pass it.
        hourly_overlay: future — cross-cadence overlay from
            ``interval_sec=3600``. Injected under ``{name}_1h``.
        fifteen_min_overlay: future — cross-cadence overlay
            from ``interval_sec=900`` for sub-15m strategies.
            Injected under ``{name}_15m``.

    Returns:
        A fresh dict ready for ``EvalContext.features``.

    Collision policy:
        - Primary cadence (``bar_feats``) wins over any overlay
          at the same key — but cadence-suffixed keys cannot
          collide with primary by construction.
        - ``factor_row`` keys never collide with primary keys
          by design (factor library uses disjoint names —
          ``mom_*``, ``f_score``, ``rs_vs_nifty_3m``, …).
        - ``regime_row`` keys (``regime_label``, ``stress_prob``)
          are unique to the regime surface — no collision.
        - Multi-overlay collision (``_1d`` + ``_1h`` of the same
          feature name) cannot occur within a key because the
          suffixes differ.
    """
    out: PerBarFeatures = {}
    out.update(bar_feats)
    out["nifty_above_sma200"] = (
        market_regime if market_regime is not None else Decimal("0")
    )
    out["nifty_30d_return_pct"] = (
        market_trend if market_trend is not None else Decimal("0")
    )
    if factor_row:
        out.update(factor_row)
    if regime_row:
        out.update(regime_row)
    # Cross-cadence overlays — suffix-renamed, never clobber
    # primary keys.
    if daily_overlay:
        out.update(_suffix_keys(daily_overlay, "_1d"))
    if hourly_overlay:
        out.update(_suffix_keys(hourly_overlay, "_1h"))
    if fifteen_min_overlay:
        out.update(_suffix_keys(fifteen_min_overlay, "_15m"))
    return out


def lookup_daily_overlay(
    *,
    daily_panel: dict[str, dict[int, dict[str, Decimal | str]]],
    ticker: str,
    bar_date: date,
    utc_midnight_ns: int | None = None,
) -> dict[str, Decimal | str] | None:
    """Locate the daily-feature row for a given ``(ticker,
    bar_date)`` in a panel loaded via
    ``load_intraday_features_window(interval_sec=86400, ...)``.

    The panel is keyed by ``{ticker: {bar_open_ts_ns: feats}}``.
    For daily rows, ``bar_open_ts_ns`` is UTC midnight of
    ``bar_date`` (deterministic via
    :func:`backend.algo.jobs.daily_features_daily_compute._utc_midnight_ns`).
    Caller can pass ``utc_midnight_ns`` explicitly if it has it;
    otherwise we compute it here.

    Returns ``None`` (not empty dict) if the row is absent — the
    caller distinguishes "no daily features for this day" from
    "daily features present but no spec-listed keys".
    """
    by_ts = daily_panel.get(ticker)
    if not by_ts:
        return None
    if utc_midnight_ns is None:
        from datetime import datetime, time, timezone

        utc_midnight_ns = int(
            datetime.combine(
                bar_date, time.min, tzinfo=timezone.utc
            ).timestamp()
            * 1_000_000_000
        )
    return by_ts.get(utc_midnight_ns)
