---
paths:
  - "frontend/app/**"
  - "frontend/components/**"
  - "frontend/lib/**"
---

# Performance budgets (auto-loads on frontend app/component work)

> Deep detail → `lighthouse-performance-workflow`, `auth-layout-ssr-unlock`,
> `lighthouse-runner-gotchas`, `loading-gate-lcp-anti-pattern`,
> `suspense-fallback-null-ssr-hole`.

Pre-PR soft gate: `npm run perf:check` (LHCI on /login).
Full audit: 34-route containerized Lighthouse (see CLAUDE.md §7) before major frontend ship.

**Iterating on LCP fixes**: save `pw-lh-summary.json` per cycle, diff LCP **and** CLS
(every gate removal can spike CLS — verify ≤ 0.02). Phase 0: read LCP element + phase
breakdown from per-route JSON before fixing. `Render Delay = 100% of LCP` ≠ "chart
paints late". → `loading-gate-lcp-anti-pattern`, `suspense-fallback-null-ssr-hole`

| Bucket | Perf | LCP | CLS |
|---|---:|---:|---:|
| `/`, `/login`, `/auth/oauth/callback` | ≥ 90 | ≤ 2.0–2.5 s | ≤ 0.1 |
| `/dashboard`, `/analytics` | ≥ 80 | ≤ 2.5 s | ≤ 0.1 |
| `/analytics/*`, `/insights` | ≥ 75 | ≤ 3.0 s | ≤ 0.1 |
| `/admin` | ≥ 70 | ≤ 3.5 s | ≤ 0.1 |
| `/docs` | ≥ 85 | ≤ 2.0 s | ≤ 0.1 |
| All pages | TBT ≤ 200 ms | — | ≤ 0.02 |

Hard constraints: JS < 500 KB gzipped/route · mobile baseline (4× CPU, slow 4G) ·
heavy chart libs via `next/dynamic({ssr:false})` · `<Suspense>` around chart Client
Components in RSC migration · `react-markdown` lazy.
