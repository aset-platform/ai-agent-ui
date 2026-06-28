# CI: tests/backend dashboard tests fail without Postgres (pre-existing baseline)

## Symptom

The `Backend — pytest tests/backend` CI job reports 5 failures even on a
clean `dev` (baseline: **5 failed / 288 passed**):

- `test_dashboard_routes.py::TestWatchlist::test_with_data`
- `test_dashboard_routes.py::TestRegistry::test_happy_path`
- `test_dashboard_routes.py::TestCompare::test_happy_path`
- `test_dashboard_routes.py::TestChartIndicators::test_happy_path`
- `test_dashboard_routes.py::TestChartIndicators::test_returns_sr_levels`

## Cause

That CI job provisions **no Postgres service**. The dashboard-route
happy-path tests need DB-backed data; with no DB they fall through to live
yfinance, which also fails. Log signature:

```
OSError: Multiple exceptions: [Errno 111] Connect call failed ('127.0.0.1', 5432)
HTTP Error 404: Quote not found for symbol: AAPL.NS / MSFT.NS
```

## Implication

These are **NOT PR regressions** — they fail identically on `dev`. A PR
whose only red check is `Backend — pytest tests/backend` with exactly these
5 failures is still mergeable (`gh pr view` shows `mergeable: MERGEABLE`,
state `UNSTABLE`). Before treating CI red as a blocker, diff the PR's
failing set against `dev`'s latest run; if it matches this set, it is the
known baseline, not your change.

## Permanent fix

Add a `postgres` service to the `tests/backend` CI workflow job, or mock
the DB layer in the affected dashboard-route tests so they don't depend on
a live catalog. Until then the job stays red on every PR and masks real
signal — fix is worth prioritising.
