# Performance Workflow

> Lighthouse-based performance monitoring for the frontend.
> Every developer runs `perf:check` before raising a PR.

---

## Quick Reference

```bash
cd frontend

# Before raising PR (required — pick one)
npm run perf:check         # LHCI on /login only (~2 min)
npm run perf:audit         # Playwright quick-check, 10 routes (~2 min)
npm run perf:full          # Full surface audit, 42+ points (~2 min)

# Bundle analysis (optional, investigative)
npm run analyze            # opens treemap in browser

# Sprint-end full-site audit
npm run perf:sweep         # npx unlighthouse → HTML report
```

---

## Performance Budgets

| Route | Perf Score | LCP | TBT | CLS |
|-------|-----------|-----|-----|-----|
| `/` (chat) | >= 90 | <= 2.5s | <= 200ms | <= 0.1 |
| `/login` | >= 90 | <= 2.0s | <= 150ms | <= 0.05 |
| `/dashboard` | >= 80 | <= 2.5s | <= 300ms | <= 0.1 |
| `/analytics` | >= 80 | <= 2.5s | <= 300ms | <= 0.1 |
| `/analytics/*` | >= 75 | <= 3.0s | <= 400ms | <= 0.1 |
| `/admin` | >= 70 | <= 3.5s | <= 500ms | <= 0.15 |
| `/docs` | >= 85 | <= 2.0s | <= 200ms | <= 0.1 |
| `/insights` | >= 75 | <= 3.0s | <= 400ms | <= 0.1 |

**Global**: Accessibility >= 95, first-load JS < 500KB gzipped.

---

## Pre-PR Workflow

Every developer must run this before raising a PR from `feature/*` to `dev`:

```bash
cd frontend
npm run perf:check
```

This script:
1. Builds the frontend (`npm run build`)
2. Starts the Next.js server in background
3. Runs `lhci autorun` against all routes
4. Prints pass/fail summary per route
5. Kills the server and exits with appropriate code

If assertions fail, fix the performance issues before raising the PR.

### Auth for Authenticated Routes

LHCI uses a Puppeteer script (`scripts/lighthouse-auth.js`) to log in
before auditing protected routes. Set these env vars:

```bash
export PERF_TEST_EMAIL="demo@example.com"
export PERF_TEST_PASSWORD="demo1234"
```

---

## Sprint-End Audit

At the end of each sprint:

1. **Run full-site sweep**:
   ```bash
   cd frontend
   npm run perf:sweep
   ```
   This generates an HTML report in `frontend/perf-reports/` (gitignored).

2. **Review the report**: Open `perf-reports/index.html` in a browser.

3. **Compare against baseline**: Check `perf-baselines/sprint-N.json`
   against the previous sprint's file.

4. **Save new baseline**: After the sweep, update
   `perf-baselines/sprint-N+1.json` with the new scores.

5. **Create stories**: If any page dropped below its budget,
   create a Jira story for the next sprint.

---

## Baseline Tracking

Baselines are version-controlled JSON files in `perf-baselines/`:

```
perf-baselines/
  sprint-3.json    # initial baseline (pre-optimization)
  sprint-4.json    # after Track A optimizations
  ...
```

Each file records scores per route:
```json
{
  "sprint": 4,
  "date": "2026-04-01",
  "routes": {
    "/dashboard": {
      "performance": 82,
      "accessibility": 96,
      "lcp_ms": 2100,
      "tbt_ms": 180,
      "cls": 0.03
    }
  }
}
```

---

## Bundle Analysis

To investigate what's in the JS bundle:

```bash
cd frontend
npm run analyze
```

This opens the `@next/bundle-analyzer` treemap in your browser.
Look for:
- Oversized chunks (> 100KB)
- Duplicate dependencies
- Libraries that should be dynamically imported

---

## GitHub Actions (Future)

A Lighthouse CI workflow exists at `.github/workflows/lighthouse.yml`
but the PR trigger is **commented out**. To enable:

1. Uncomment the `on: pull_request` trigger
2. Set these GitHub Secrets:
   - `PERF_TEST_EMAIL` / `PERF_TEST_PASSWORD` — test user creds
   - `LHCI_GITHUB_APP_TOKEN` — (optional) for PR comments
   - `BUNDLEWATCH_GITHUB_TOKEN` — (optional) for bundle size checks
3. Uncomment the bundlewatch step if desired

---

## Full Surface Audit (`perf:full`)

The comprehensive audit covers 63 audit points across 4 sections:

| Section | Points | What it measures |
|---------|--------|-----------------|
| Pages | 10 | Full page load (FCP, LCP, CLS, TBT) |
| Tabs | 21 | Tab switch time + CLS + TBT |
| Modals | 8 | Modal open time + CLS + TBT |
| Interactive | 24 | Control response time + CLS + TBT |

```bash
# Requires backend running + env vars
export PERF_TEST_EMAIL="user@example.com"
export PERF_TEST_PASSWORD="password"
export PERF_ADMIN_EMAIL="admin@example.com"    # optional
export PERF_ADMIN_PASSWORD="admin-password"     # optional

cd frontend && npm run perf:full
```

Admin audit points are skipped if `PERF_ADMIN_EMAIL` is not set.

Results saved to `perf-baselines/sprint-N-full.json`.

---

## Key Metrics & Weights

Lighthouse Performance score is a weighted average:

| Metric | Weight | Target |
|--------|--------|--------|
| TBT (Total Blocking Time) | 30% | < 200ms |
| LCP (Largest Contentful Paint) | 25% | < 2.5s |
| CLS (Cumulative Layout Shift) | 25% | < 0.1 |
| FCP (First Contentful Paint) | 10% | < 1.8s |
| SI (Speed Index) | 10% | < 3.4s |

Mobile emulation (4x CPU throttle, slow 4G) is the scoring baseline.

---

## Troubleshooting

**Scores vary between runs**: Normal — Lighthouse has ±5 point variance.
LHCI runs 3 times and uses the median.

**Auth routes show login page**: Check `PERF_TEST_EMAIL`/`PERF_TEST_PASSWORD`
env vars, and ensure the test user exists.

**"Port 3000 in use"**: Kill existing Next.js processes:
`lsof -ti:3000 | xargs kill -9`

**perf:check hangs**: The server has a 30-second startup timeout.
If the build is slow, increase the wait in `scripts/perf-check.sh`.
