"""Pipeline data-quality assertion framework (ASETPLTFRM-380).

Background
----------
2026-05-12 surfaced the silent-success antipattern: yesterday's
``India Regime Daily Pipeline`` ran and reported every step as
``status='success'``, but the row it wrote to
``stocks.regime_history`` for 2026-05-11 was dirty:

  - ``vix_close = 16.84``   (actual ^INDIAVIX close = 18.55)
  - ``pct_above_50sma = NaN``
  - ``stress_prob = None``

Three orthogonal failures stacked (DuckDB metadata cache race,
NaN breadth, retried Iceberg overwrites). Each step individually
looked fine; the *output* was wrong.

Solution
--------
Every pipeline step declares its own data-quality assertions. The
executor evaluates them after the step runs. Failures:

  - mark the run row ``status='success_with_warnings'`` (or
    ``'dirty'`` for hard errors)
  - write a ``data_quality_violation`` event to ``algo.events``
    so the admin Data Health card can surface them
  - never block the next step (silent-success is the bug, not
    fail-loud; we want the pipeline to finish and just be honest
    about what it produced)

Conventions
-----------
- Assertions are pure functions over their own context — keep them
  cheap, idempotent, and stateless. Heavy data quality checks belong
  on a separate background job, not inline with the daily pipeline.
- ``severity='warn'`` for "looks suspicious — investigate";
  ``severity='error'`` for "this output is unusable downstream".
- Assertion names are kebab-case snake-ish identifiers
  (``regime-row-exists-today``); they double as the unique key for
  surfacing repeat violations on the admin card.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable
from uuid import uuid4

_logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))


# ---------------------------------------------------------------
# Types
# ---------------------------------------------------------------


Severity = str  # Literal["warn", "error"] — kept as plain str so the
# emitted event payload stays JSON-safe without a custom encoder.


@dataclass(frozen=True)
class AssertionResult:
    """Outcome of one assertion evaluation."""

    name: str
    severity: Severity
    passed: bool
    message: str
    # Optional structured detail — surfaces verbatim on the admin
    # card so the trader can see e.g. ``expected_close=18.55,
    # actual_close=16.84``.
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        return not self.passed


@dataclass(frozen=True)
class Assertion:
    """Declarative data-quality check on a pipeline step output.

    ``check_fn`` is invoked with the step's context dict (output
    payload + any auxiliary data the step plumbs through). It must
    return ``(passed: bool, message: str, detail: dict)``.
    """

    name: str
    severity: Severity
    check_fn: Callable[[dict[str, Any]], tuple[bool, str, dict]]

    def evaluate(self, ctx: dict[str, Any]) -> AssertionResult:
        try:
            passed, message, detail = self.check_fn(ctx)
        except Exception as exc:  # noqa: BLE001
            # An assertion that crashes IS itself a violation —
            # surface as an error rather than silently swallowing.
            return AssertionResult(
                name=self.name,
                severity="error",
                passed=False,
                message=f"assertion crashed: {exc}",
                detail={"exception": str(exc)},
            )
        return AssertionResult(
            name=self.name,
            severity=self.severity,
            passed=bool(passed),
            message=str(message),
            detail=detail or {},
        )


# ---------------------------------------------------------------
# Standard assertion factories
# ---------------------------------------------------------------


def value_is_not_nan(
    field_name: str, *, severity: Severity = "error",
) -> Assertion:
    """Assert ``ctx[field_name]`` is not NaN / None."""

    def _check(ctx: dict[str, Any]):
        v = ctx.get(field_name)
        if v is None:
            return (
                False,
                f"{field_name} is None",
                {"field": field_name, "value": None},
            )
        try:
            if isinstance(v, float) and math.isnan(v):
                return (
                    False,
                    f"{field_name} is NaN",
                    {"field": field_name, "value": "NaN"},
                )
        except TypeError:
            pass
        return (True, "", {})

    return Assertion(
        name=f"value-is-not-nan:{field_name}",
        severity=severity,
        check_fn=_check,
    )


def value_in_range(
    field_name: str,
    lo: float,
    hi: float,
    *,
    severity: Severity = "warn",
) -> Assertion:
    """Assert ``ctx[field_name]`` falls within the inclusive
    ``[lo, hi]`` band. None / NaN → passes (use
    :func:`value_is_not_nan` separately to gate that).
    """

    def _check(ctx: dict[str, Any]):
        v = ctx.get(field_name)
        if v is None:
            return (True, "", {})
        try:
            f = float(v)
            if math.isnan(f):
                return (True, "", {})
        except (TypeError, ValueError):
            return (
                False,
                f"{field_name} not numeric: {v!r}",
                {"field": field_name, "value": str(v)},
            )
        if lo <= f <= hi:
            return (True, "", {})
        return (
            False,
            f"{field_name}={f} outside [{lo}, {hi}]",
            {
                "field": field_name,
                "value": f,
                "lo": lo,
                "hi": hi,
            },
        )

    return Assertion(
        name=f"value-in-range:{field_name}",
        severity=severity,
        check_fn=_check,
    )


def cross_source_close_enough(
    field_name: str,
    expected_key: str,
    *,
    tolerance_pct: float = 5.0,
    severity: Severity = "warn",
) -> Assertion:
    """Cross-source freshness check. Asserts that
    ``ctx[field_name]`` is within ``tolerance_pct`` of
    ``ctx[expected_key]`` — useful for "the value we wrote is
    within 5% of the upstream value we read from Iceberg in the
    same run".

    None / NaN on either side → passes (combine with
    :func:`value_is_not_nan` for stricter gating).
    """

    def _check(ctx: dict[str, Any]):
        v = ctx.get(field_name)
        expected = ctx.get(expected_key)
        if v is None or expected is None:
            return (True, "", {})
        try:
            fv = float(v)
            fe = float(expected)
        except (TypeError, ValueError):
            return (True, "", {})
        if math.isnan(fv) or math.isnan(fe) or fe == 0:
            return (True, "", {})
        delta_pct = abs(fv - fe) / abs(fe) * 100.0
        if delta_pct <= tolerance_pct:
            return (True, "", {})
        return (
            False,
            (
                f"{field_name}={fv} differs from {expected_key}"
                f"={fe} by {delta_pct:.2f}% (>{tolerance_pct}%)"
            ),
            {
                "field": field_name,
                "value": fv,
                "expected_key": expected_key,
                "expected": fe,
                "delta_pct": delta_pct,
                "tolerance_pct": tolerance_pct,
            },
        )

    return Assertion(
        name=(
            f"cross-source-close-enough:{field_name}"
            f":{expected_key}"
        ),
        severity=severity,
        check_fn=_check,
    )


# ---------------------------------------------------------------
# Evaluation + violation event emission
# ---------------------------------------------------------------


@dataclass
class StepAssertionReport:
    """Aggregated outcome — what the pipeline executor consumes."""

    step: str
    results: list[AssertionResult]

    @property
    def status(self) -> str:
        """Roll-up status across this step's assertions."""
        if any(
            r.failed and r.severity == "error"
            for r in self.results
        ):
            return "error"
        if any(r.failed for r in self.results):
            return "warn"
        return "ok"

    @property
    def failed(self) -> list[AssertionResult]:
        return [r for r in self.results if r.failed]


def evaluate_assertions(
    step: str,
    assertions: Iterable[Assertion],
    ctx: dict[str, Any],
) -> StepAssertionReport:
    """Run ``assertions`` against ``ctx`` and return the report."""
    results = [a.evaluate(ctx) for a in assertions]
    return StepAssertionReport(step=step, results=results)


def emit_violation_events(
    report: StepAssertionReport,
    *,
    pipeline_id: str,
    run_id: str | None = None,
    user_id: str | None = None,
    events_sink: Callable[[dict], None] | None = None,
) -> int:
    """Emit one ``data_quality_violation`` event per failed
    assertion. Returns the count emitted.

    ``events_sink`` (when provided) receives the raw event dicts —
    the pipeline executor typically passes its in-memory buffer's
    ``append`` so the events flush in the same Iceberg commit as
    the step's other events. ``None`` → log only (still useful for
    unit tests).
    """
    from backend.algo.backtest.event_writer import event_row

    sink = events_sink
    count = 0
    for r in report.failed:
        ev = event_row(
            session_id=uuid4(),
            user_id=user_id,
            strategy_id=None,
            mode="pipeline",
            type_="data_quality_violation",
            payload={
                "pipeline_id": pipeline_id,
                "run_id": run_id,
                "step": report.step,
                "assertion": r.name,
                "severity": r.severity,
                "message": r.message,
                "detail": r.detail,
                "ts_ist": datetime.now(IST).isoformat(),
            },
        )
        _logger.warning(
            "data_quality_violation: pipeline=%s step=%s "
            "assertion=%s severity=%s — %s",
            pipeline_id, report.step, r.name, r.severity,
            r.message,
        )
        if sink is not None:
            try:
                sink(ev)
            except Exception:  # noqa: BLE001
                _logger.warning(
                    "events_sink raised on violation event",
                    exc_info=True,
                )
        count += 1
    return count
