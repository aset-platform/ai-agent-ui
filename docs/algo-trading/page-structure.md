# Algo Trading — Page Structure

> Last updated: 2026-05-11. Replaces the legacy single-page
> `/algo-trading` (now redirected — see § Redirects).

## Three pages

Each lives under the `Algo Trading` sidebar group:

### 1. Zerodha Connect — `/algo-trading/broker`

Single-screen broker connection page. Hosts the existing
`ConnectBrokerTab` (login URL, callback handler, status chip).
No tabs.

### 2. Strategies — `/algo-trading/strategies`

8 URL-synced tabs (`?tab=`):

| Tab | Description |
|---|---|
| Instruments | Universe management |
| Strategies | Strategy CRUD + builder |
| Backtest | Historical run + walk-forward |
| **Paper** | Replay-fixture runs against the synthetic broker. Indigo accent. PaperRuntime. |
| **Dry run** | Live-runtime rehearsal with synthetic Kite responses. Amber accent. LiveRuntime, real Kite WS, but order placement is intercepted. |
| Performance | Aggregated PnL across modes |
| Replay | Cross-mode event-log timeline |
| Settings | Fee preview / slippage |

### 3. Live Trading — `/algo-trading/live`

Real-money only. **No dry-run toggle on this page.** Rose accent.

4 tabs:

| Tab | Content |
|---|---|
| **Live** | Sticky KPI strip + 2×2 zone grid (Open Positions / Regime+Stress / Active Strategy / Recent Fills) + collapsible Attribution footer. |
| Positions | Today's open positions (Kite REST truth) with strategy attribution joined from `algo.events`. |
| Holdings | Multi-day CNC holdings with days-held + strategy origin. |
| Settings | Kill switch + drift threshold + per-strategy 4-gate LiveModeToggle + safety belts. |

## Color contract (anti-mistake)

| Surface | Accent | Why |
|---|---|---|
| Paper tab | indigo-600 | Dev / replay rehearsal |
| Dry run tab | amber-500 | Live-runtime rehearsal — be careful |
| Live Trading page | rose-600 | Real money |

Switching mode = switching tab = switching URL. The pre-2026-05-11
in-page toggle that silently flipped per-user dry-run state is gone.

## Redirects (preserve bookmarks)

Handled by `frontend/app/(authenticated)/algo-trading/redirectMap.ts`
via the `/algo-trading` index page server-side redirect:

| Legacy `?tab=` | Redirect to |
|---|---|
| `connect` | `/algo-trading/broker` |
| `instruments` `strategies` `backtest` `performance` `replay` | `/algo-trading/strategies?tab=<same>` |
| `paper` | `/algo-trading/strategies?tab=paper` |
| `settings` | `/algo-trading/strategies?tab=settings` |
| *(no tab)* | `/algo-trading/strategies` |

## Backend endpoints added

- `GET /algo/live/dashboard-summary` — 8-field KPI aggregate, 15s cache.
- `GET /algo/live/positions` — Kite REST positions + `algo.events` strategy join.
- `GET /algo/live/holdings` — Kite REST holdings + days-held + strategy origin.

All three under the existing `create_live_router()` prefix.

## See also

- Spec: `docs/superpowers/specs/2026-05-11-algo-trading-three-page-split-design.md`
- Implementation plan: `docs/superpowers/plans/2026-05-11-algo-trading-three-page-split.md`
