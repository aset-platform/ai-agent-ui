# Paper Replay Fixture Builder (from holdings + watchlist) — Design

**Date:** 2026-06-12
**Status:** Approved (pending spec review)
**Author:** Abhay Kumar Singh
**Ships on:** PR #259 branch `feature/aa-add-to-watchlist`

## 1. Problem & context

The user grew the watchlist to 55 tickers (via the new Add-to-Watchlist
button) to make RSI(2) Connors Daily v3 paper trading execute, but the
paper run still produced `fills=0`. Root cause: the run used the
**default `source="replay"`** (`backend/algo/routes/paper.py:37`), which
replays a fixed CI JSONL fixture (`backend/algo/tests/fixtures/*.jsonl`)
that contains unrelated tickers and never triggers v3. The watchlist is
irrelevant to a replay run (replay reads a fixture, not the live
subscription).

Critical mechanic: the runtime reads the **daily** RSI(2)/gate features
from a daily-overlay panel keyed by each bar's **date**
(`lookup_daily_overlay(daily_panel, ticker, bar_date)` — panel loaded via
`load_intraday_features_window(interval_sec=86400, ...)`, the same source
`PaperRuntime._ensure_daily_overlay_cache` uses). So whether a replay
*fills* is determined by the **dates** in the fixture, not just its
tickers.

**Goal:** a one-click way to generate a replay fixture from the user's
own holdings union watchlist that is engineered to actually trigger v3 —
so they can validate the strategy executes on their universe.

## 2. Scope

In scope:
- Backend endpoint that builds a replay fixture from the user's
  holdings (qty>0) union watchlist, targeting dates where RSI(2)<=5 + the
  v3 market-health gates hold, and writes it to a user-data dir.
- Extend the replay loader/list to allow the user-data fixtures dir.
- A "Build RSI(2) replay fixture" item in the WatchlistWidget overflow
  (kebab) menu + a hook.

Out of scope:
- No change to the paper runtime / strategy evaluation.
- No change to the committed CI fixtures.
- Not strategy-agnostic — v3's entry (RSI(2)<=5 + gates) is the target;
  generalising to arbitrary strategies is future work.

## 3. Approach (chosen)

**Scan the same daily-overlay source the runtime reads, emit ticks on
trigger dates, write to a gitignored user fixtures dir.** Because the
builder finds trigger dates from the *same* daily features
(`load_intraday_features_window(interval_sec=86400)`) the runtime looks
up at `_on_bar_close`, a date the builder selects is guaranteed to
reproduce the same RSI(2)/gate values in replay — no drift.

## 4. Backend design

### 4.1 Trigger scan + fixture build — `backend/algo/paper/fixture_builder.py` (new)

`build_universe_fixture(user_id, *, lookback_days=60, max_dates_per_ticker=2) -> FixtureBuildResult`:

1. **Universe:** holdings (qty>0, via `_get_stock_repo().get_portfolio_holdings`) union watchlist (`repo.get_user_tickers`), deduped, India `.NS`/`.BO` only.
2. **Daily features:** for the lookback window, load the daily panel via
   `load_intraday_features_window(interval_sec=86400, tickers=..., ...)`
   (same loader the runtime uses). Each row carries `rsi_2` (per ticker)
   plus the v3 gate inputs.
3. **Trigger dates:** for each ticker, select dates where the **v3 entry
   condition** holds: `rsi_2 <= 5` AND `distance_from_sma200 > 0` AND the
   market-health gates for that date — `stress_prob < 0.5`,
   `nifty_above_sma200 >= 1`, `nifty_30d_return_pct > -5`. Market gates
   are date-keyed (from the regime/nifty overlay, `get_regime_history`);
   compute them once per date and reuse across tickers. Keep at most
   `max_dates_per_ticker` most-recent trigger dates per ticker.
4. **Synthetic ticks:** for each (ticker, trigger_date) emit a small
   burst of `Tick`s ({ticker, ts_ns, ltp, volume}) within that date's
   NSE session, spanning > 60s so a 1-min bar closes on that date. Date
   alignment uses the runtime's convention (UTC-midnight-of-date via
   `daily_features_daily_compute._utc_midnight_ns`) so the closed bar's
   `bar_date` matches the overlay row. `ltp` = that day's close (from
   `stocks.ohlcv`); `volume` from the bar (fallback 1).
5. **Write JSONL:** to `<AI_AGENT_UI_HOME>/fixtures/<user_id>.jsonl`,
   sorted by `ts_ns` (ReplayTickSource streams in file order), with a
   leading `#`-comment header (ReplayTickSource skips `#`/blank lines).
6. **Return** `FixtureBuildResult { filename, n_tickers, n_trigger_dates, n_ticks, trigger_tickers: list[str] }`.

Replay is exempt from the 14:30 IST daily-eval gate (#250), so a
trigger-date entry fires immediately on replay.

### 4.2 Endpoint — `backend/algo/routes/paper.py`

`POST /v1/algo/paper/fixtures/build` (`Depends(pro_or_superuser)`, like
the other paper routes). Body optional `{ lookback_days?: int }`
(default 60, clamp 5..250). Calls `build_universe_fixture`; on empty
universe → 400 ("add tickers to watchlist/holdings first"); on zero
trigger dates → 200 with `n_trigger_dates=0` + a message (NOT an error —
it's a valid "nothing oversold in the window" outcome). Returns the
`FixtureBuildResult`.

### 4.3 Loader allowlist — `backend/algo/paper/supervisor.py`

Today `build_replay_source` + `list_replay_fixtures` restrict to
`_FIXTURES_ROOT = backend/algo/tests/fixtures`. Add a second allowed
root `_USER_FIXTURES_ROOT = AI_AGENT_UI_HOME/fixtures`:
- `build_replay_source(path)` resolves the candidate against BOTH roots
  and accepts it if it lives under either (keep the traversal-safety
  `startswith` check per root).
- `list_replay_fixtures()` enumerates `*.jsonl` in both dirs (tag each
  with its source so the dropdown can distinguish CI vs user fixtures).
Create `_USER_FIXTURES_ROOT` lazily (mkdir parents) on build.

## 5. Frontend design

- **`frontend/components/widgets/WatchlistOverflowMenu.tsx`**: add a
  "Build RSI(2) replay fixture" menu item (alongside the existing bulk
  ops). `data-testid="watchlist-build-fixture"`.
- **`frontend/hooks/useBuildReplayFixture.ts`** (new): `apiFetch` POST
  `${API_URL}/algo/paper/fixtures/build`; returns
  `{ submit, submitting, result, error }`.
- On success, inline/toast-style feedback consistent with the existing
  watchlist bulk-op result pattern:
  `Built <filename> · <n_tickers> tickers · <n_trigger_dates> trigger dates`
  (or "no oversold setups found in the last N days" when 0). No global
  toast lib — reuse the menu's existing result/messaging affordance.
- The user then opens the paper-run form and selects the generated
  fixture (now listed by the extended `list_replay_fixtures`) with
  `source=replay`.

## 6. Data flow

```
WatchlistWidget kebab menu -> "Build RSI(2) replay fixture"
   |  POST /v1/algo/paper/fixtures/build { lookback_days }
   v
build_universe_fixture(user_id):
   holdings union watchlist
   -> load_intraday_features_window(interval_sec=86400)   # daily panel
   -> per ticker: dates where rsi_2<=5 AND v3 gates hold
   -> synthetic Ticks on those dates (close a 1-min bar)
   -> write <AI_AGENT_UI_HOME>/fixtures/<user_id>.jsonl
   v
FixtureBuildResult { filename, n_tickers, n_trigger_dates, n_ticks }
   v
(user) paper-run form -> source=replay, fixture=<user>.jsonl -> run
   v
PaperRuntime replays -> lookup_daily_overlay hits the same rsi_2<=5 rows
   -> v3 entry fires (replay exempt from 14:30 gate) -> paper fills
```

## 7. Error handling

- Empty universe → 400.
- Zero trigger dates → 200 with `n_trigger_dates=0` + message (valid).
- Missing daily features for a ticker → skip it (counts toward "no
  trigger"), never fail the whole build.
- Fixture write failure → 500 with detail.
- Loader: a user fixture path that escapes both roots → rejected (same
  traversal guard as today).

## 8. Testing

Backend:
- `build_universe_fixture` unit (mock the daily-feature loader +
  holdings/watchlist + ohlcv): a ticker with an rsi_2<=5 date that passes
  gates → emits ticks dated to that date; a ticker that never qualifies →
  excluded; market-gate-fail date → excluded; `max_dates_per_ticker`
  respected; output `Tick`s validate against the `Tick` model.
- `build_replay_source` / `list_replay_fixtures` accept a file under the
  user-fixtures root and still reject traversal outside both roots.
- Endpoint: empty universe → 400; happy → result shape; 0-trigger → 200.

Frontend:
- vitest for `useBuildReplayFixture` (success/error).
- E2E (POM): open the kebab menu, click "Build RSI(2) replay fixture",
  assert the result message.

## 9. Files touched

- `backend/algo/paper/fixture_builder.py` (new) — scan + build.
- `backend/algo/routes/paper.py` — `POST /fixtures/build` + response model.
- `backend/algo/paper/supervisor.py` — user-fixtures root in
  `build_replay_source` + `list_replay_fixtures`.
- `tests/backend/test_paper_fixture_builder.py` (+ a supervisor loader test).
- `frontend/components/widgets/WatchlistOverflowMenu.tsx` — menu item.
- `frontend/hooks/useBuildReplayFixture.ts` (new).
- `e2e/` — selector + POM + spec.

## 10. Risks / notes

- "Guaranteed fill" = *entry-signal* guaranteed (builder + runtime read
  the same overlay). The runtime still applies budget reservation + risk
  pre-trade checks; for paper these normally pass, but a fill is not
  metaphysically guaranteed.
- Fixture size is bounded (`max_dates_per_ticker` x tickers x ticks/burst)
  to keep replay fast.
- User-scoped filename `<user_id>.jsonl` — one current fixture per user;
  rebuild overwrites. (Multi-fixture history is YAGNI.)
- Reuses the runtime's daily-feature loader, so daily-feature semantics
  stay consistent between builder and runtime.
- The generated fixture lives under `AI_AGENT_UI_HOME` (outside the repo),
  so no CI fixture is committed and determinism of existing tests holds.
