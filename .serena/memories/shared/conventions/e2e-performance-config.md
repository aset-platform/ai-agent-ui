# E2E Performance Configuration

## Worker Budget
- **Local**: 1 worker (single Chromium browser). Runs alongside 5 Docker services.
- **CI**: 2 workers on dedicated runner.
- **Never use 3+ workers locally** — starves backend APIs, causes cascade failures.

## Video Recording
- **Local**: OFF (`video: "off"`). Saves ~30% CPU per browser.
- **CI**: `retain-on-failure` for failure artifact collection.

## maxFailures
- **Local**: 10 — stops early when services are down.
- **CI**: 0 (unlimited) — run all tests for full report.

## Chromium Flags
`--disable-gpu`, `--disable-dev-shm-usage`, `--disable-extensions`

## Wait Strategy
- **Never use `networkidle`** on dashboard — continuous polling/WebSocket never settles.
- Use explicit element waits: `page.getByTestId("sidebar").toBeVisible()`.
- For below-fold widgets: `element.waitFor({ state: "attached" })` then `scrollIntoViewIfNeeded()`.

## Artifact Storage
- `/tmp/e2e-test-results/` — screenshots + traces (18 MB, auto-overwritten)
- `~/Library/Caches/ms-playwright/` — browser binaries (1.1 GB, do NOT delete)
- No periodic cleanup needed.
