# Pipeline data-quality assertion framework (ASETPLTFRM-380)

Declarative assertion DSL that codifies a structural fix for the silent-success antipattern (pipelines reporting `status='success'` while producing dirty output). Surfaced 2026-05-11 when the India Regime Daily Pipeline wrote `vix_close=16.84` against an actual `^INDIAVIX` close of 18.55, `pct_above_50sma=NaN`, and `stress_prob=None` — every step marked success, no alert raised.

## Module

`backend/algo/pipeline/quality.py`.

## Building blocks

- `Assertion(name, severity, check_fn)` — crash-safe wrapper. An assertion whose `check_fn` raises is itself a failed assertion with `severity=error` — fail-loud rather than silent-bypass.
- `AssertionResult(name, severity, passed, message, detail)` — single check outcome.
- `StepAssertionReport(step, results)` — aggregates results; `status` rolls up to `ok` / `warn` / `error`.
- `evaluate_assertions(step, assertions, ctx)` — runs them against a dict-style context.
- `emit_violation_events(report, pipeline_id, run_id, events_sink)` — writes one `data_quality_violation` event per failure into `algo.events` (mode=`pipeline`). Caller flushes via `flush_events`.

## Standard factories

- `value_is_not_nan(field, severity="error")`
- `value_in_range(field, lo, hi, severity="warn")` — None/NaN passes (combine with `value_is_not_nan` for stricter gating; keeps each check single-concern).
- `cross_source_close_enough(field, expected_key, tolerance_pct=5.0, severity="warn")` — the 2026-05-11 bug catcher.

## Initial wiring

`backend/algo/regime/pipeline_steps.py::_run_regime_classifier_assertions` runs post-step assertions against the classifier's `rule_inputs` + `stress_prob`. Five assertions wired:

- `vix_close` not NaN
- `pct_above_50sma` not NaN AND in `[0, 1]`
- `stress_prob` in `[0, 1]`
- `vix_close` within 5% of same-day `stocks.ohlcv ^INDIAVIX` close (the cross-source freshness gate)

The whole call is wrapped fail-soft — a DuckDB / Iceberg lookup failure inside the assertion path never crashes the classifier step itself. Assertions surface honesty about output; they never block.

## Admin surface

- `GET /admin/data-health/pipeline-assertions?days=7&severity=` (superuser-only) → `{rows, counts}`.
- `frontend/components/admin/PipelineAssertionsCard.tsx` — mounted on Data Health panel; self-hides on the all-green path so admins only see it when something needs attention. Severity colour-coded (amber warn, rose error), filter chips, IST timestamps via `formatIstDateTime` (ASETPLTFRM-373 shared helper).

## How to apply to a new pipeline step

```python
from backend.algo.pipeline.quality import (
    evaluate_assertions, emit_violation_events,
    value_is_not_nan, value_in_range,
    cross_source_close_enough,
)

def my_pipeline_step(...):
    out = run_the_real_work(...)
    try:
        ctx = {"some_metric": out.get("some_metric"), ...}
        report = evaluate_assertions(
            "my_step_name",
            [value_is_not_nan("some_metric"), ...],
            ctx,
        )
        if report.failed:
            events: list[dict] = []
            emit_violation_events(
                report,
                pipeline_id="my_pipeline",
                run_id=run_id,
                events_sink=events.append,
            )
            flush_events(events)  # land them in algo.events
    except Exception:
        _logger.warning("assertion run failed", exc_info=True)
    return out
```

Always wrap the assertion block in try/except — the framework's job is to surface honesty, not to add new crash points.

## Validated in production

By 17:00 IST on 2026-05-12, the framework had already emitted 4 real `data_quality_violation` events from production pipeline activity — confirms the assertions are firing correctly in prod, not just in unit tests.

## Deferred follow-ups (not yet ticketed, called out in PR #213)

- **DuckDB metadata-cache invalidation barrier audit** across pipeline steps — the structural root cause of the 2026-05-11 race that surfaced this framework. The framework surfaces; the audit fixes. Invasive cross-cutting work; deserves its own focused PR.
- **Slack/email dispatch** for violations (currently log + admin card only).
- **Re-run-with-force action button** on the admin card.
- **Assertions on additional steps**: `compute_daily_factors`, `data_refresh`, `iceberg_maintenance`. Framework's in place; wire one step at a time when each is next touched.

## Related

- `shared/architecture/iceberg-daily-pipeline-compaction` — pipeline executor design.
- `shared/architecture/pipeline-chaining-dag` — step ordering + skip-on-fail semantics.
- `shared/conventions/iceberg-maintenance-enrollment` — sister convention; also written 2026-05-12.
- `shared/architecture/iceberg-data-layer` — overall data-write story.
