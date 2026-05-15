"""Live + paper runtime feature emission hook (FE-10).

When a closed bar fires in the live or paper tick stream and the
underlying strategy runs on an intraday cadence (``"1m"`` /
``"5m"`` / ``"15m"``), compute the FE-2 per-ticker features for
that bar AS A SIDE EFFECT and persist a long-format row set to
``stocks.intraday_features``. The strategy evaluation path that
runs in the same handler still uses its own in-memory feature
dict computed via ``compute_indicators`` — the live emitter
exists so the FE-4 backtest loader can later read the same
features for paper / live bars (research dataset continuity,
alpha-research snapshot at bar-close time).

CRITICAL — never raise. Live trading must not be blocked by a
PyIceberg / serialization hiccup. Every exception is caught,
logged with ``exc_info=True``, and the hook returns ``None``.

Daily cadences (``"1d"``) are a no-op here. The FE-3 daily
compute job
(:mod:`backend.algo.jobs.intraday_features_daily_compute`) is the
canonical writer for daily-period features; live emission would
duplicate that work and write to the wrong cadence partition.

Cohort features (FE-8 ``rs_vs_nifty_15m`` /
``rs_vs_sector_15m`` / ``market_breadth_pct_above_sma200`` /
``advance_decline_ratio``, FE-9 ``sector_rotation_score`` /
``regime_label`` / ``stress_prob``) are deliberately NOT emitted
by this hook. Cross-sectional features need the whole universe +
index bars + sector map at the same bar, which a per-ticker
runtime cannot assemble cheaply. Those keys remain a daily-batch
responsibility — FE-3 is the canonical source.

Future optimization (FE-10.1 follow-up): batch across tickers
inside a single bar-boundary so PyIceberg sees one commit per
boundary rather than one per (ticker, bar). The v1 single-row
append meets the spec §7.2 latency budget (< 100ms / ticker / bar)
on the SQLite catalog + small Arrow batch.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from backend.algo.backtest.types import BarData
from backend.algo.features import (
    FEATURE_SET_VERSION,
    compute_intraday_features,
)
from backend.algo.jobs.intraday_features_daily_compute import (
    _panel_to_arrow_rows,
    _write_features_batch,
)

_logger = logging.getLogger(__name__)

# Mirrors backend.algo.live.intraday_bar_warmup.INTERVAL_SEC_BY_LABEL
# without taking that import — keeps this module importable from
# paper runtime without pulling in the live-only warmup chain.
_INTERVAL_SEC_BY_LABEL: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
}

_ALLOWED_INTERVAL_SECS = frozenset({60, 300, 900})


def emit_features_for_bar(
    *,
    ticker: str,
    interval_sec: int,
    history: list[BarData],
    cadence_interval: str,
    mode: str,
    feature_set_version: str = FEATURE_SET_VERSION,
) -> None:
    """Compute + persist intraday features for the latest bar in
    ``history`` (the closed bar that just fired).

    Daily-cadence strategies are no-ops here — FE-3 daily compute
    is the canonical writer for daily features. Invalid /
    unsupported ``interval_sec`` values are no-ops too.

    Args:
        ticker: e.g. ``"RELIANCE.NS"``.
        interval_sec: Bar cadence in seconds. Must be one of
            ``{60, 300, 900}``.
        history: Ascending-by-``bar_open_ts_ns`` list of bars for
            this ticker (the runtime's per-ticker history). The
            engine computes features over the full series and we
            persist only the entry for the LAST bar's
            ``bar_open_ts_ns`` (the bar that just closed).
        cadence_interval: ``strategy.schedule.interval`` literal —
            ``"1d"`` / ``"15m"`` / ``"5m"`` / ``"1m"``.
        mode: ``"paper"`` or ``"live"``. Reserved — currently used
            only in log lines. The intraday_features table is not
            partitioned on mode; readers union paper + live + the
            daily-compute output transparently.
        feature_set_version: Stamp written onto each row.

    Returns:
        ``None``. All exceptions are caught + logged with
        ``exc_info=True``.

    Latency: per spec §7.2 the budget is < 100ms per
    (ticker, bar-close). A single-row Arrow append wrapped in
    ``retry_iceberg_op`` on the SQLite catalog meets that on the
    dev box.
    """
    if cadence_interval == "1d":
        # Daily cadence — FE-3 daily compute owns this table for
        # daily features. Silent no-op.
        return None
    if cadence_interval not in _INTERVAL_SEC_BY_LABEL:
        _logger.warning(
            "[fe10] unsupported cadence_interval=%r — skipping "
            "emission (ticker=%s mode=%s)",
            cadence_interval,
            ticker,
            mode,
        )
        return None
    if interval_sec not in _ALLOWED_INTERVAL_SECS:
        _logger.warning(
            "[fe10] interval_sec=%s not in %s — skipping emission "
            "(ticker=%s mode=%s)",
            interval_sec,
            sorted(_ALLOWED_INTERVAL_SECS),
            ticker,
            mode,
        )
        return None
    if not history:
        return None
    last_bar = history[-1]
    if last_bar.bar_open_ts_ns is None:
        # Daily-shaped bar slipped through — defensive guard.
        return None

    try:
        # Engine emits the WHOLE-series panel; pick out the last
        # bar's entry. Cohort features (FE-8 / FE-9) are NOT
        # emitted here — single-ticker single-bar emission can't
        # build cross-sectional context cheaply at this latency.
        panel = compute_intraday_features(
            history,
            feature_set_version=feature_set_version,
        )
    except Exception:
        _logger.exception(
            "[fe10] compute_intraday_features failed (non-fatal): "
            "ticker=%s mode=%s interval_sec=%s",
            ticker,
            mode,
            interval_sec,
        )
        return None

    feats = panel.get(last_bar.bar_open_ts_ns)
    if not feats:
        # Warmup — engine produced nothing computable for this
        # bar yet. Skip silently; identical to FE-3's batch
        # behaviour for under-warmup tickers.
        return None

    try:
        single_ticker_panel = {ticker: {last_bar.bar_open_ts_ns: feats}}
        bars_by_ticker = {ticker: [last_bar]}
        written_at = datetime.now(timezone.utc).replace(
            microsecond=0,
            tzinfo=None,
        )
        arrow_rows = _panel_to_arrow_rows(
            panel=single_ticker_panel,
            bars_by_ticker=bars_by_ticker,
            interval_sec=interval_sec,
            feature_set_version=feature_set_version,
            written_at=written_at,
        )
        if not arrow_rows:
            return None
        _write_features_batch(arrow_rows=arrow_rows)
    except Exception:
        _logger.exception(
            "[fe10] intraday_features write failed (non-fatal): "
            "ticker=%s mode=%s interval_sec=%s ts_ns=%s",
            ticker,
            mode,
            interval_sec,
            last_bar.bar_open_ts_ns,
        )
        return None
    return None
