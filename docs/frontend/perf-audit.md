# Perf Audit — Lighthouse + Playwright

Containerized Lighthouse audit across **34 routes** (9 base
pages + 25 tabbed variants on `/analytics/analysis`,
`/analytics/insights`, `/admin`). Runs against a production
Next.js build inside the compose network — reproducible, zero
CORS, baseline-comparable.

## Run

```bash
# One-time: build the perf + frontend-perf images.
docker compose --profile perf build

# Bring up the stack (backend + postgres + redis + frontend-perf).
# `up -d` without `--profile perf` starts only the default services;
# we add the flag so `frontend-perf` joins.
docker compose --profile perf up -d \
    postgres redis backend frontend-perf

# Run the audit (exits on completion, --rm auto-cleans).
docker compose --profile perf run --rm perf
```

Output lands in `./.lighthouseci/` on the host (bind-mounted
into the perf container):

- `pw-lh-summary.json` — consolidated table + metadata
- `pw-<route>.json` — full LHR per route

## Architecture

```
┌─────────────┐      ┌──────────────────┐      ┌──────────┐
│  perf       │─────▶│  frontend-perf   │─────▶│ backend  │
│ (Playwright │ HTTP │ (Next.js prod +  │ /v1/* │ (FastAPI)│
│  Chromium + │ 3000 │  rewrite proxy)  │ proxy │  8181    │
│  Lighthouse)│      └──────────────────┘      └──────────┘
└─────────────┘
```

**Why `frontend-perf` and not `frontend`:**
the dev `frontend` service bakes `NEXT_PUBLIC_BACKEND_URL=
http://localhost:8181`, which resolves to the perf container's
own localhost — no backend there. `frontend-perf` is built
with `NEXT_PUBLIC_BACKEND_URL=""`; `lib/config.ts` detects the
empty sentinel, sets `API_URL="/v1"`, and the Next.js rewrite
(`next.config.ts`) proxies `/v1/*` to
`BACKEND_URL=http://backend:8181` at runtime. Same-origin —
no CORS.

## Script entry point

- **Runner**: `scripts/perf-lighthouse-all-routes.js`
- Opens Chromium via Playwright with persistent profile
  (cookies/localStorage survive the CDP tab that Lighthouse
  opens)
- Logs in with `pressSequentially()` (React `onChange`
  requires real keystrokes; `fill()` silently no-ops on prod)
- Runs `onlyCategories=[performance, a11y, best-practices,
  seo]` on each route, desktop form factor, devtools
  throttling

## Routes (34)

- **Base (9)**: `/login`, `/dashboard`, `/analytics`,
  `/analytics/analysis`, `/analytics/compare`,
  `/analytics/insights`, `/admin`, `/docs`, `/insights`
- **`/analytics/analysis?tab=` (6)**: `portfolio`,
  `portfolio-forecast`, `analysis`, `forecast`, `compare`,
  `recommendations`
- **`/analytics/insights?tab=` (9)**: `screener`, `risk`,
  `sectors`, `targets`, `dividends`, `correlation`,
  `quarterly`, `piotroski`, `screenql`
- **`/admin?tab=` (10)**: `users`, `audit`, `observability`,
  `transactions`, `scheduler`, `recommendations`,
  `maintenance`, `my_account`, `my_audit`, `my_llm`

Dropped: `/analytics/marketplace` — legacy redirect to
`/analytics`.

## Env overrides

| Var | Default | Purpose |
|---|---|---|
| `PERF_BASE` | `http://frontend-perf:3000` | Frontend URL from inside perf container |
| `PERF_TEST_EMAIL` | `admin@demo.com` | Seeded via `seed_demo_data.py` |
| `PERF_TEST_PASSWORD` | `Admin123!` | — |

## Host-run alternative (legacy)

For quick one-off runs without Docker:

```bash
PERF_TEST_EMAIL=admin@demo.com \
PERF_TEST_PASSWORD=Admin123! \
node scripts/perf-lighthouse-all-routes.js
```

Uses the host Chromium and expects `frontend` on `:3000` and
`backend` on `:8181`. Not reproducible across machines —
prefer the containerized path for any baseline comparisons.

## Expected runtime

- 10 routes host-run: ~3 min
- 34 routes containerized: ~10 min (target, per acceptance
  criteria on ASETPLTFRM-330)
