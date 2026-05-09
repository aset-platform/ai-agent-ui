# Algo Trading v1 — post-ship bug catalogue (2026-05-09)

Bugs found during real Kite + real-data end-to-end walkthrough. Every one fixed on `feature/algo-trading-v1-integration`. Listed here so they're not re-discovered.

## Module-import path mistakes (silent at test time)

`backend/algo/routes/{broker,instruments}.py::_get_session_factory` imported `backend.db.repository` (doesn't exist) instead of `backend.db.engine`. Tests fully mock `_get_session_factory` so the bad import never executed in pytest — surfaced only when an authenticated request hit the route, blowing up with `ModuleNotFoundError` and showing as "NetworkError when attempting to fetch resource" in Firefox.

**Always grep `backend.db.repository` after touching any algo route or job — should be 0 hits.** Same root cause as Session 3 Task 7's `loader.py`.

## Backtest data path

| Bug | Fix |
|---|---|
| `Decimal(str(r["close"]))` crashes on NaN/None OHLCV cells (yfinance leaks them on pre-market flat candles + dividend-only days). `str(None)` → "None" → ConversionSyntax. | `_safe_decimal` helper rejecting NaN/None/empty/string-sentinels (CLAUDE.md §6.1). SKIP rows with any unsafe OHLCV cell, log count. |
| `fee_rates.yaml` had only `effective_from: 2026-04-01` row → backtests pre-Apr 2026 raised "No fee rates configured for ...". | Backfilled `2020-01-01 → 2026-03-31` row with identical rates (Zerodha rate card unchanged across that window). |
| No `stocks.technical_indicators` Iceberg table exists; PaperRuntime + backtest needed SMAs. | Built `backend/algo/backtest/indicators.py` with O(N) rolling SMAs (windows 20/50/200) + `golden_cross_days_ago` (sentinel 999 until first cross, resets on cross-down). `DEFAULT_WARMUP_BARS=400` calendar days ≈ 270 trading days, comfortable for SMA200. |
| `set_target_weight` action was a no-op in BOTH backtest and paper runners. | `target_qty = floor(weight × current_equity / last_price)`; diff vs current qty drives BUY/SELL signal. `current_equity = initial + realised − fees` in backtest, `initial + realised` in paper. |
| Equity curve mark-to-market used `blist[-1].close` (always period_end) → unrealised P&L contribution was 0 every interior bar. | Maintain running `last_close: dict[str, Decimal]` updated as we walk; snapshot for end-of-day equity. Now traces actual market path day-by-day. |
| `event_writer.flush_events` built Arrow tables via `pa.Table.from_pylist(rows)` which infers `nullable=True` everywhere. Iceberg `algo.events` schema requires non-nullable on most fields → "Mismatch in fields" at commit. | Explicit `_EVENTS_ARROW_SCHEMA` with `nullable=False` on all required cols; pass `schema=` to `from_pylist`. |
| `resolve_universe` ignored `strategy.universe.filter` block (market + ticker_type) — only honoured `scope`. With scope=discovery on a Pro user, all 818 registry tickers (incl. 14 US) were iterated despite `market: india` filter. | Two-stage pipeline: `_scoped_tickers(scope)` → `_apply_filter(candidates, markets, ticker_types)`. `market="all"` short-circuits market gate. Tickers missing from stock_master are dropped. |
| Backtest had no risk gating — only PaperRuntime did. | Wired RiskEngine + Signal + AccountState into runner. Build per-bar AccountState (current_equity live, daily_realised delta from `day_start_realised` snapshot at top of each bar_date). Honour reject/scale/accept; emit `signal_rejected` event. |

## Paper runtime

| Bug | Fix |
|---|---|
| `_features_for_bar` only emitted `today_ltp/today_vol`; strategy referencing `sma_50` crashed with KeyError. | Maintain per-ticker bar history (Stream.Bar adapted to Decimal-based BarData via shim); recompute `compute_indicators` per close. Wrap eval in `try/except KeyError` (graceful skip during warmup). |
| `_action_to_signal` didn't handle `set_target_weight` (only buy/sell/exit) — strategies using target weights silently produced 0 signals. | Mirror of backtest's resolution; plumb `last_price` through. |

## Instruments loader

Kite's `/instruments` dump never carries `our_ticker` — that's our internal field. Loader was passing `r.get("our_ticker")` (always None) → Instruments tab "Our ticker" column showed "—" for every row.

Fix: `_derive_our_ticker(row)` — for `segment ∈ {NSE, BSE}` return `tradingsymbol + (".NS" | ".BO")`, else None (FNO/MCX/CDS/INDICES skip).

## Kite OAuth flow

- Backend `GET /v1/algo/broker/callback` requires Bearer token (`pro_or_superuser` dep). Kite redirects via plain browser navigation → no Authorization header → 401. **Fix: ship a frontend bounce page at `/algo-trading/kite-callback`** that takes Kite's `?request_token=...`, forwards via `apiFetch` (carries JWT), and bounces to `/algo-trading?tab=connect`. Configure Kite Connect app's redirect URL to `http://localhost:3000/algo-trading/kite-callback`.
- Postback URL = leave empty (v1 has no live orders → no order-update webhooks).
- `KiteClient.__init__` didn't store `_access_token` instance attr — the WS adapter (`LiveTickSource`) needs it at construction time. Set both `self._api_key` and `self._access_token` even though `KiteConnect.set_access_token()` was already called.

## Strategy edit UX

Visual builder + JSON pane were both read-only with no other edit surface → numeric thresholds (`set_target_weight.weight`, `compare.right.literal`) and risk caps were not editable through any UI. Shipped `StrategyLeversPanel` + `strategyTunables.walkTunables(root)` auto-discovery. See `shared/architecture/algo-strategy-levers-tunables`.

## Paper trading wiring

- Original `ticks_sample.jsonl` had 30 ticks for FAKE.NS over 3 minutes — far too few bars for any SMA-based strategy. Generated `ticks_indian_universe.jsonl` (3015 ticks, 9 NSE blue chips, daily closes 2025-01-01 → 2026-05-08). Each daily close becomes a tick at 09:30 IST UTC; resampler buckets each into its own 1-min bar (since they're days apart).
- `ActiveRunsPanel` had `REPLAY_FIXTURE = "ticks_sample.jsonl"` hardcoded. Added `GET /v1/algo/paper/fixtures` (lists `*.jsonl` with n_ticks/distinct_tickers/sample_tickers) + frontend dropdown. Default selection prefers non-FAKE fixtures.

## Migration / DB

`alembic_version` table had a duplicate row from Session 1's re-parenting (`a9c1b3d5e7f2` + `72a8a2cc1c1a`) → "Requested revision X overlaps with other requested revisions Y" on next upgrade. Fix: `DELETE FROM alembic_version WHERE version_num = 'a9c1b3d5e7f2'`. New head: `b3c5e7d9f1a4` (algo.runs.summary_json).

## Patterns to remember

- **Edit-then-test silently failing:** `Edit` tool requires `Read` first in any session. After several edits failing this way today, always Read before Edit.
- **Mocked tests hide import-path bugs:** When the module under test mocks its own session factory, an `import` line wrapped in that function never runs. Grep for the bad import name post-edit.
- **Default fixture choice matters:** Defaulting the fixture dropdown to `ticks_sample.jsonl` (FAKE.NS only) gave first-time users a useless paper run. Default to the rich fixture so the demo just works.
