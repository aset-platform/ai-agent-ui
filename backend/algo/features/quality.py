"""Pipeline data-quality assertions for the centralized feature
engine's daily compute step (ASETPLTFRM-402 / FE-3).

Three assertions run against the (bars-in, panel-out, rows-out)
triplet produced by the daily compute job:

1. ``intraday-features-coverage-floor`` (error)
   At least 95% of ``(ticker, bar_open_ts_ns)`` pairs in the input
   bar set emit ≥ 5 feature rows. The five are the always-emittable
   features: ``today_ltp``, ``today_vol``, ``minutes_since_open``,
   ``time_of_day_bucket``, and at least one of
   ``vwap`` / ``rsi`` / ``sma_*``. Anything less indicates the
   engine failed silently for that bar.

2. ``intraday-features-no-nan`` (error)
   No NaN / inf in ``feature_value``. PyArrow ``DoubleType``
   accepts NaN but the reader interprets NaN as "feature missing"
   so writing NaN is a silent-fail vector.

3. ``intraday-features-version-stamped`` (error)
   Every emitted row has a non-empty ``feature_set_version``
   matching the expected version. Catches a writer that bypasses
   the canonical stamp.

The factories return :class:`Assertion` instances from
:mod:`backend.algo.pipeline.quality` so the executor's event-sink
machinery (PR #213, ASETPLTFRM-380) handles violation routing
automatically.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from typing import Any

from backend.algo.features.version import FEATURE_SET_VERSION
from backend.algo.pipeline.quality import (
    Assertion,
    StepAssertionReport,
    emit_violation_events,
    evaluate_assertions,
)

_logger = logging.getLogger(__name__)


_COVERAGE_FLOOR_PCT = 95.0
_MIN_FEATURES_PER_BAR = 5

_STEP_NAME = "stocks.intraday_features.write"


def features_coverage_floor(
    *,
    min_pct: float = _COVERAGE_FLOOR_PCT,
    min_features_per_bar: int = _MIN_FEATURES_PER_BAR,
) -> Assertion:
    """Per-(ticker, bar) feature count floor.

    Reads from ``ctx['arrow_rows']`` (the long-format rows the
    writer will persist) plus ``ctx['expected_bar_keys']`` — the
    set of ``(ticker, bar_open_ts_ns)`` tuples the engine was
    asked to compute. Both must be supplied; missing context
    fails the assertion (silent-success is the bug we are guarding
    against).
    """

    def _check(ctx: dict[str, Any]):
        rows = ctx.get("arrow_rows")
        expected = ctx.get("expected_bar_keys")
        if rows is None or expected is None:
            return (
                False,
                "missing arrow_rows or expected_bar_keys in "
                "assertion context",
                {},
            )
        if not expected:
            return (True, "", {})
        counts: dict[tuple[str, int], int] = defaultdict(int)
        for r in rows:
            try:
                key = (str(r["ticker"]), int(r["bar_open_ts_ns"]))
            except (KeyError, TypeError, ValueError):
                continue
            counts[key] += 1
        below = [
            {
                "ticker": t,
                "bar_open_ts_ns": ts,
                "count": counts.get(
                    (t, ts),
                    0,
                ),
            }
            for (t, ts) in expected
            if counts.get((t, ts), 0) < min_features_per_bar
        ]
        total = len(expected)
        emitted_ok = total - len(below)
        coverage_pct = (emitted_ok / total) * 100.0 if total else 100.0
        if coverage_pct >= min_pct:
            return (True, "", {})
        return (
            False,
            (
                f"feature coverage {coverage_pct:.2f}% below "
                f"floor {min_pct:.2f}% "
                f"({len(below)}/{total} bars emit "
                f"< {min_features_per_bar} features)"
            ),
            {
                "coverage_pct": round(coverage_pct, 3),
                "min_pct": min_pct,
                "min_features_per_bar": min_features_per_bar,
                "below_floor_count": len(below),
                "total": total,
                "sample": below[:5],
            },
        )

    return Assertion(
        name="intraday-features-coverage-floor",
        severity="error",
        check_fn=_check,
    )


def features_no_nan() -> Assertion:
    """No NaN / inf in ``feature_value`` across the batch.

    Reads from ``ctx['arrow_rows']``. PyArrow accepts NaN in a
    DoubleType column, so a buggy writer can poison the table and
    every downstream reader will silently treat the field as
    missing.
    """

    def _check(ctx: dict[str, Any]):
        rows = ctx.get("arrow_rows") or []
        bad: list[dict[str, Any]] = []
        for r in rows:
            try:
                v = float(r["feature_value"])
            except (KeyError, TypeError, ValueError):
                bad.append(
                    {
                        "ticker": r.get("ticker"),
                        "feature_name": r.get("feature_name"),
                        "value": "non-numeric",
                    }
                )
                continue
            if math.isnan(v) or math.isinf(v):
                bad.append(
                    {
                        "ticker": r.get("ticker"),
                        "feature_name": r.get("feature_name"),
                        "value": "NaN" if math.isnan(v) else "inf",
                    }
                )
        if not bad:
            return (True, "", {})
        return (
            False,
            f"{len(bad)} feature row(s) have NaN / non-finite value",
            {"bad_count": len(bad), "sample": bad[:5]},
        )

    return Assertion(
        name="intraday-features-no-nan",
        severity="error",
        check_fn=_check,
    )


def features_version_stamped(
    *,
    expected_version: str = FEATURE_SET_VERSION,
) -> Assertion:
    """Every emitted row carries a non-empty ``feature_set_version``
    matching ``expected_version``.

    Reads from ``ctx['arrow_rows']``. Severity=error — a missing /
    wrong version means consumers pinned to ``v1.0`` can't filter
    correctly and may unknowingly mix feature regimes.
    """

    def _check(ctx: dict[str, Any]):
        rows = ctx.get("arrow_rows") or []
        bad: list[dict[str, Any]] = []
        for r in rows:
            v = r.get("feature_set_version")
            if not v or str(v) != expected_version:
                bad.append(
                    {
                        "ticker": r.get("ticker"),
                        "feature_name": r.get("feature_name"),
                        "feature_set_version": v,
                    }
                )
        if not bad:
            return (True, "", {})
        return (
            False,
            (
                f"{len(bad)} feature row(s) missing or wrong "
                f"feature_set_version (expected={expected_version!r})"
            ),
            {
                "expected_version": expected_version,
                "bad_count": len(bad),
                "sample": bad[:5],
            },
        )

    return Assertion(
        name="intraday-features-version-stamped",
        severity="error",
        check_fn=_check,
    )


def build_assertions(
    *,
    expected_version: str = FEATURE_SET_VERSION,
) -> list[Assertion]:
    """Return all three assertions for one feature-write batch.
    Caller passes ``arrow_rows`` and ``expected_bar_keys`` into
    the context.
    """
    return [
        features_coverage_floor(),
        features_no_nan(),
        features_version_stamped(expected_version=expected_version),
    ]


def run_post_compute_assertions(
    *,
    arrow_rows: list[dict[str, Any]],
    expected_bar_keys: set[tuple[str, int]],
    expected_version: str = FEATURE_SET_VERSION,
    pipeline_id: str = "intraday_features_daily_compute",
    run_id: str | None = None,
    user_id: str | None = None,
    events_sink=None,
) -> StepAssertionReport:
    """Evaluate the three assertions against a freshly-computed
    batch and emit one ``data_quality_violation`` event per
    failure. Returns the :class:`StepAssertionReport` so callers
    can decide whether to mark the run ``success_with_warnings``.
    """
    ctx = {
        "arrow_rows": arrow_rows,
        "expected_bar_keys": expected_bar_keys,
    }
    report = evaluate_assertions(
        _STEP_NAME,
        build_assertions(expected_version=expected_version),
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
