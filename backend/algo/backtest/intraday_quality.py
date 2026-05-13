"""Pipeline data-quality assertions for ``stocks.intraday_bars``
(ASETPLTFRM-400 slice 1e).

Five assertions run after each backfill batch:

1. ``intraday-bar-count-floor`` (warn)
   Each ``(ticker, bar_date)`` group hits the minimum closed-bar
   count for the cadence (15m: 23, 5m: 70, 1m: 350). Warn (not
   error) because half-day sessions like Diwali Muhurat trading
   are real and legitimately below the floor.

2. ``intraday-ohlc-not-nan`` (error)
   Every bar's open / high / low / close is finite. NaN at this
   layer means a write barrier was bypassed — the Iceberg schema
   uses ``required=True`` so this only fires if the slice 1c
   filter was sidestepped.

3. ``intraday-bar-open-ts-monotonic`` (error)
   Bars per ``(ticker, interval_sec)`` are strictly ascending by
   ``bar_open_ts_ns``. Detects Kite duplicates or out-of-order
   bars (rare but real on a flaky session boundary).

4. ``intraday-ohlc-self-consistent`` (error)
   For every bar: high ≥ low, high ≥ max(open, close), low ≤
   min(open, close). Trivial-but-essential — a bad upstream that
   inverts these has bitten us before in daily OHLCV.

5. ``intraday-cross-source-close-match`` (warn, optional)
   When the caller supplies a ``kite_ltp`` for a given ticker,
   the latest bar's close must be within 0.5 % of that LTP.
   Catches the same class of bug as the 2026-05-12 stale-VIX
   incident — a writer that committed but read stale upstream.

The factories return ``Assertion`` instances from
``backend.algo.pipeline.quality`` so the executor / event sink
machinery (registered in PR #213) handles the rest.
"""

from __future__ import annotations

import logging
import math
from collections import Counter, defaultdict
from typing import Any

from backend.algo.pipeline.quality import (
    Assertion,
    StepAssertionReport,
    emit_violation_events,
    evaluate_assertions,
)

_logger = logging.getLogger(__name__)


# Minimum closed-bar count per trading day, keyed on interval_sec.
# NSE session = 09:15 → 15:30 IST = 6.25 h.
#   15m → 25 bars; floor 23 absorbs Diwali half-days.
#    5m → 75 bars; floor 70.
#    1m → 375 bars; floor 350.
_BAR_COUNT_FLOORS: dict[int, int] = {60: 350, 300: 70, 900: 23}


def bar_count_floor(interval_sec: int) -> Assertion:
    """Per-day minimum closed-bar count. Severity=warn — half-day
    sessions are legitimately below the floor."""
    floor = _BAR_COUNT_FLOORS[interval_sec]

    def _check(ctx: dict[str, Any]):
        bars = ctx.get("bars") or []
        if not bars:
            return (True, "", {})
        counts: Counter[tuple[str, str]] = Counter(
            (b.ticker, b.date.isoformat()) for b in bars
        )
        below = sorted((t, d, c) for (t, d), c in counts.items() if c < floor)
        if not below:
            return (True, "", {})
        return (
            False,
            (
                f"{len(below)} (ticker, day) group(s) below "
                f"{floor}-bar floor for interval_sec={interval_sec}"
            ),
            {
                "interval_sec": interval_sec,
                "floor": floor,
                "below_floor_count": len(below),
                "sample": [
                    {"ticker": t, "bar_date": d, "count": c}
                    for t, d, c in below[:5]
                ],
            },
        )

    return Assertion(
        name=f"intraday-bar-count-floor:{interval_sec}s",
        severity="warn",
        check_fn=_check,
    )


def ohlc_not_nan() -> Assertion:
    """No bar may have a NaN or non-finite open/high/low/close.
    Severity=error — these can't have legitimately reached the
    Iceberg layer (required=True). Fires only on slice-1c bypass."""

    def _check(ctx: dict[str, Any]):
        bars = ctx.get("bars") or []
        bad: list[dict[str, Any]] = []
        for b in bars:
            for field in ("open", "high", "low", "close"):
                try:
                    v = float(getattr(b, field))
                except (TypeError, ValueError):
                    bad.append(
                        {
                            "ticker": b.ticker,
                            "bar_open_ts_ns": b.bar_open_ts_ns,
                            "field": field,
                            "value": "non-numeric",
                        }
                    )
                    break
                if math.isnan(v) or math.isinf(v):
                    bad.append(
                        {
                            "ticker": b.ticker,
                            "bar_open_ts_ns": b.bar_open_ts_ns,
                            "field": field,
                            "value": "NaN" if math.isnan(v) else "inf",
                        }
                    )
                    break
        if not bad:
            return (True, "", {})
        return (
            False,
            f"{len(bad)} bar(s) have NaN / non-finite OHLC",
            {"bad_count": len(bad), "sample": bad[:5]},
        )

    return Assertion(
        name="intraday-ohlc-not-nan",
        severity="error",
        check_fn=_check,
    )


def bar_open_ts_monotonic() -> Assertion:
    """Bars per (ticker, interval) ascend strictly by
    ``bar_open_ts_ns``. Severity=error — duplicates / out-of-order
    bars break the runner's bar-by-bar loop."""

    def _check(ctx: dict[str, Any]):
        bars = ctx.get("bars") or []
        # Caller supplies ``interval_sec`` so the assertion knows
        # how to bucket; default to None to surface the missing
        # context as a violation rather than silently passing.
        interval_sec = ctx.get("interval_sec")
        if interval_sec is None:
            return (
                False,
                "missing interval_sec in assertion context",
                {},
            )
        groups: dict[str, list[int]] = defaultdict(list)
        for b in bars:
            if b.bar_open_ts_ns is None:
                continue
            groups[b.ticker].append(int(b.bar_open_ts_ns))
        violations: list[dict[str, Any]] = []
        for ticker, ns_list in groups.items():
            for i in range(1, len(ns_list)):
                if ns_list[i] <= ns_list[i - 1]:
                    violations.append(
                        {
                            "ticker": ticker,
                            "prev_ns": ns_list[i - 1],
                            "this_ns": ns_list[i],
                        }
                    )
                    break  # one violation per ticker is enough
        if not violations:
            return (True, "", {})
        return (
            False,
            f"{len(violations)} ticker(s) have non-monotonic bars",
            {"violation_count": len(violations), "sample": violations[:5]},
        )

    return Assertion(
        name="intraday-bar-open-ts-monotonic",
        severity="error",
        check_fn=_check,
    )


def ohlc_self_consistent() -> Assertion:
    """high ≥ low, high ≥ max(open, close), low ≤ min(open, close).
    Severity=error — inverted bars break every indicator."""

    def _check(ctx: dict[str, Any]):
        bars = ctx.get("bars") or []
        bad: list[dict[str, Any]] = []
        for b in bars:
            try:
                o, h, lo, c = (
                    float(b.open),
                    float(b.high),
                    float(b.low),
                    float(b.close),
                )
            except (TypeError, ValueError):
                # Caught by ohlc_not_nan — don't double-count.
                continue
            if math.isnan(h) or math.isnan(lo):
                continue
            if h < lo or h < max(o, c) or lo > min(o, c):
                bad.append(
                    {
                        "ticker": b.ticker,
                        "bar_open_ts_ns": b.bar_open_ts_ns,
                        "open": o,
                        "high": h,
                        "low": lo,
                        "close": c,
                    }
                )
        if not bad:
            return (True, "", {})
        return (
            False,
            f"{len(bad)} bar(s) violate OHLC self-consistency",
            {"bad_count": len(bad), "sample": bad[:5]},
        )

    return Assertion(
        name="intraday-ohlc-self-consistent",
        severity="error",
        check_fn=_check,
    )


def cross_source_close_match(
    *,
    tolerance_pct: float = 0.5,
) -> Assertion:
    """Latest bar close per ticker must agree with the
    ``ctx['kite_ltp']`` map within ``tolerance_pct``.

    Severity=warn — the LTP may legitimately lag the bar close by
    several seconds during heavy volume. Skips silently when the
    LTP map is absent (the daily keeper provides it; ad-hoc
    backfills may not).
    """

    def _check(ctx: dict[str, Any]):
        bars = ctx.get("bars") or []
        ltp_map = ctx.get("kite_ltp") or {}
        if not bars or not ltp_map:
            return (True, "", {})
        # Latest bar per ticker (max bar_open_ts_ns).
        latest: dict[str, Any] = {}
        for b in bars:
            if b.bar_open_ts_ns is None:
                continue
            prev = latest.get(b.ticker)
            if prev is None or (
                (b.bar_open_ts_ns or 0) > (prev.bar_open_ts_ns or 0)
            ):
                latest[b.ticker] = b
        breaches: list[dict[str, Any]] = []
        for ticker, bar in latest.items():
            expected = ltp_map.get(ticker)
            if expected is None:
                continue
            try:
                close = float(bar.close)
                exp = float(expected)
            except (TypeError, ValueError):
                continue
            if exp == 0 or math.isnan(close) or math.isnan(exp):
                continue
            delta_pct = abs(close - exp) / abs(exp) * 100.0
            if delta_pct > tolerance_pct:
                breaches.append(
                    {
                        "ticker": ticker,
                        "bar_close": close,
                        "kite_ltp": exp,
                        "delta_pct": round(delta_pct, 3),
                    }
                )
        if not breaches:
            return (True, "", {})
        return (
            False,
            (
                f"{len(breaches)} ticker(s) deviate from Kite LTP "
                f"by > {tolerance_pct}%"
            ),
            {
                "tolerance_pct": tolerance_pct,
                "breach_count": len(breaches),
                "sample": breaches[:5],
            },
        )

    return Assertion(
        name="intraday-cross-source-close-match",
        severity="warn",
        check_fn=_check,
    )


# ----------------------------------------------------------------
# Orchestrator helper
# ----------------------------------------------------------------


_STEP_NAME = "stocks.intraday_bars.write"


def build_assertions(interval_sec: int) -> list[Assertion]:
    """Return all 5 assertions for a given cadence. Caller passes
    the ``bars`` + (optional) ``kite_ltp`` map into the context."""
    return [
        bar_count_floor(interval_sec),
        ohlc_not_nan(),
        bar_open_ts_monotonic(),
        ohlc_self_consistent(),
        cross_source_close_match(),
    ]


def run_post_ingest_assertions(
    bars,
    *,
    interval_sec: int,
    pipeline_id: str = "intraday_bars_ingest",
    run_id: str | None = None,
    user_id: str | None = None,
    kite_ltp: dict[str, float] | None = None,
    events_sink=None,
) -> StepAssertionReport:
    """Evaluate the 5 assertions against the freshly-written batch
    and emit a ``data_quality_violation`` event per failure.

    Returns the ``StepAssertionReport`` so callers can decide
    whether to mark the run ``success_with_warnings``.
    """
    ctx = {
        "bars": bars,
        "interval_sec": interval_sec,
        "kite_ltp": kite_ltp or {},
    }
    report = evaluate_assertions(
        _STEP_NAME,
        build_assertions(interval_sec),
        ctx,
    )
    emit_violation_events(
        report,
        pipeline_id=pipeline_id,
        run_id=run_id,
        user_id=user_id,
        events_sink=events_sink,
    )
    return report
