# Troubleshooting — Algo Trading

Bug catalogue from the v1 build, recorded so they're not
re-discovered. Symptoms → root cause → fix.

## "NetworkError when attempting to fetch resource" on Connect Broker tab

**Root cause:** `backend/algo/routes/{broker,instruments}.py::_get_session_factory`
imported `backend.db.repository` (doesn't exist) instead of
`backend.db.engine`. Module not found → 500 → browser fetch
rejected at network layer.

Tests fully mock `_get_session_factory` so the bad import never
executed in pytest. **Always grep `backend.db.repository`
after touching any algo route or job — should be 0 hits.**

## "Run failed: [<class 'decimal.ConversionSyntax'>]"

**Root cause:** yfinance leaks NaN/None OHLCV cells on pre-
market flat candles + dividend-only days. `Decimal(str(None))`
→ `Decimal("None")` → ConversionSyntax.

**Fix:** `_safe_decimal` helper in
`backend/algo/backtest/data_source.py` rejects NaN / None /
empty / string-sentinels (`"None"`, `"NaN"`, `"nan"`, `"n/a"`,
`"na"`, `"NaT"`). Rows with any unsafe OHLCV cell are skipped.

## "Run failed: No fee rates configured for 2025-..."

**Root cause:** `backend/algo/fee_rates.yaml` was shipped with
only the 2026-04-01 row. Backtests on earlier periods raised
`ValueError` from `_load_rates_for(as_of)`.

**Fix:** the YAML now has a backfill row covering 2020-01-01 →
2026-03-31 with identical numbers (Zerodha rate card was
unchanged across that window). Adding a new rate row in the
future requires bumping the prior row's `effective_to`.

## "Run failed: 'Feature not in context: sma_50'"

**Root cause:** the v1 runner only put `today_ltp` and
`today_vol` in the `EvalContext.features` map. Strategies
referencing `sma_50`, `sma_200`, `golden_cross_days_ago`, etc.
crashed at the first `compare` node.

**Fix:**
- `backend/algo/backtest/indicators.py` computes SMAs (windows
  20, 50, 200) + `golden_cross_days_ago` on-the-fly via O(N)
  rolling sums.
- Runner loads with `warmup_days=400` so SMA200 is well-formed
  by `period_start`.
- Both backtest runner and PaperRuntime catch `KeyError` from
  `evaluator.eval_node` and skip the bar (graceful no-op
  during warmup before SMAs accumulate).

## Empty backtest result — 0 trades, flat equity curve

Likely causes, in order:

1. **Strategy uses `crossover` / `between` / `select_top_n` /
   `weighted` nodes.** v1 evaluator stubs these to
   `{"type": "hold"}` so no actions fire. Use `compare` + `and`
   patterns until full support arrives.
2. **Strategy uses `set_target_weight` but no `weight` value
   resolves to a non-zero qty.** Check `current_equity ×
   weight ÷ last_price`; if `< 1` you'll get 0-share intents.
3. **Universe filter is too narrow.** Open the **Strategies**
   tab → check the filter (`market`, `ticker_type`). For
   discovery scope on a Pro user, `market: india` +
   `ticker_type: [stock]` should yield ~800 NSE stocks.
4. **OHLCV history is too shallow** for the SMA windows. SMA200
   needs 200 trading days = ~280 calendar days; the runner
   loads 400 calendar days of warmup so this should rarely bite.

## Instruments tab — "Our ticker" column shows "—" for every row

**Root cause:** Kite's `/instruments` dump never carries
`our_ticker` — it's our internal field. The loader was passing
`r.get("our_ticker")` which is always None.

**Fix:** `_derive_our_ticker(row)` in
`backend/algo/instruments/loader.py`:

```
NSE/BSE equity (segment ∈ {NSE, BSE}) →
    <tradingsymbol> + (NSE: ".NS" | BSE: ".BO")
```

Anything else (FNO, MCX, CDS, INDICES) returns None — those
Kite tradingsymbols don't map to our `<symbol>.<suffix>`
convention. Re-run the Instruments refresh after this fix
lands.

## Kite OAuth callback returns 401

**Root cause:** Backend `GET /v1/algo/broker/callback` requires
a Bearer token (`pro_or_superuser` dep). Kite redirects via
plain browser navigation → no Authorization header → 401.

**Fix:** the frontend bounce page at
`/algo-trading/kite-callback` takes Kite's `?request_token=...`,
forwards via `apiFetch` (carries the JWT), and bounces to
`/algo-trading?tab=connect`.

**Configure Kite Connect app** with redirect URL:
```
http://localhost:3000/algo-trading/kite-callback
```
NOT the backend port. Postback URL = leave empty (v1 has no
live orders → no order-update webhooks).

## Backtest equity curve looks stair-stepped (only moves on closed trades)

**Root cause:** The runner used `blist[-1].close` (always
period_end's close) when building marks for the daily equity
snapshot. Result: unrealised P&L on open positions was 0 every
interior bar.

**Fix:** runner now maintains a `last_close: dict[str,
Decimal]` updated as we walk; snapshot = today's marks (or
most-recent prior close for tickers that don't trade today).
Equity curve now traces the actual market path day-by-day.

## Multiple "trade rows" with same `opened_at` but different `closed_at`

**Not a bug.** PositionTracker is one-position-per-ticker with
weighted-avg cost basis. Each SELL closes a slice that becomes
its own `TradeRow`. If `set_target_weight` re-balances trim 1
share at a time, you'll see N rows for the same continuous
position — each with the original `opened_at` and a different
`closed_at`. The final SELL closing the remaining quantity is
the largest row.

## Migration error: "Requested revision X overlaps with other requested revisions Y"

**Root cause:** `alembic_version` table has a duplicate row
from Session 1's algo-schema re-parenting. Two heads pointing
at different ancestors.

**Fix:**
```sql
DELETE FROM alembic_version WHERE version_num = 'a9c1b3d5e7f2';
```
Then `alembic upgrade head` succeeds. Current head:
`b3c5e7d9f1a4` (algo.runs.summary_json column).

## Where else to look

- [Backtest engine](backtest.md) — fill semantics, risk gating
- [Paper trading](paper-trading.md) — runtime, fixtures
- [Strategies](strategies.md) — AST, levers, tunable discovery
- [Secrets management](secrets.md) — Keychain → docker-compose
- [Overview](overview.md) — module map + flow diagrams
