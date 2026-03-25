# Lighthouse Performance Workflow — ASETPLTFRM-177

## Story: Frontend Lighthouse 45 → 80+ & Recurring Workflow (8 pts, Sprint 4)

## Two Tracks
- **Track A** — Fix the score (optimize bundle, lazy-load, skeletons)
- **Track B** — Recurring workflow (LHCI, budgets, baselines, sprint-end sweep)

## Current Baseline (2026-03-25)
- Performance: 45, Accessibility: 88, Best Practices: 100, SEO: 100
- Only `PlotlyChart.tsx` uses `dynamic()` — TradingView charts NOT lazy-loaded
- No bundle analyzer, no LHCI, bare next.config, zero loading.tsx skeletons
- Font loading already optimal (next/font/google Geist)

## 12 Frontend Routes
Public: `/`, `/login`, `/auth/oauth/callback`
Authenticated: `/dashboard`, `/analytics`, `/analytics/analysis`, `/analytics/compare`,
`/analytics/insights`, `/analytics/marketplace`, `/admin`, `/docs`, `/insights`
Note: `/billing` is a ProfileModal tab, not a route.

## Performance Budgets
- `/`, `/login`, `/auth/oauth/callback`: >= 90 perf, LCP <= 2.0-2.5s
- `/dashboard`, `/analytics`: >= 80 perf, LCP <= 2.5s
- `/analytics/*`, `/insights`: >= 75 perf, LCP <= 3.0s
- `/admin`: >= 70 perf, LCP <= 3.5s
- `/docs`: >= 85 perf, LCP <= 2.0s
- All pages: CLS <= 0.1, Accessibility >= 95, JS < 500KB gzipped

## Track A Deliverables
- A1: @next/bundle-analyzer behind ANALYZE=true + `npm run analyze`
- A2: Dynamic imports (TradingView charts, admin, analytics tabs) → next/dynamic ssr:false
- A3: loading.tsx for dashboard, analytics, admin, login
- A4: Image audit — priority on above-fold, sizes prop
- A5: next.config.ts enhancements

## Track B Deliverables
- B1: lighthouserc.js — all 12 routes, budgets, temporary-public-storage
- B2: scripts/lighthouse-auth.js — Puppeteer login, extract cookies for LHCI
- B3: `npm run perf:check` — build+serve+lhci+cleanup (pre-PR gate)
- B4: .github/workflows/lighthouse.yml — trigger COMMENTED OUT
- B5: bundlewatch — JS < 500KB (also commented out in GH Actions)
- B6: `npm run perf:sweep` — npx unlighthouse full-site audit
- B7: perf-baselines/sprint-N.json — version controlled
- B8: PERFORMANCE.md — workflow documentation

## Key Decisions
- LHCI auth: Puppeteer login script (collect.puppeteerScript)
- Unlighthouse: npx (no devDependency)
- Baselines: JSON in perf-baselines/ (version controlled)
- GH Actions: commented out, enable on deployment
- Env vars: PERF_TEST_EMAIL, PERF_TEST_PASSWORD for auth script

## Developer Workflow
- Pre-PR: `cd frontend && npm run perf:check` (required before feature→dev)
- Sprint-end: `cd frontend && npm run perf:sweep` → compare baselines → story if regression

## Scoring Weights
TBT ~30%, LCP ~25%, CLS ~25%, FCP ~10%, SI ~10%
Mobile emulation (4x CPU, slow 4G) is the scoring baseline.