# Backtest engine

Headless Python pipeline reading `stocks.ohlcv` via DuckDB,
evaluating a strategy AST per (ticker, bar), simulating fills via
`SimBroker` against a fee-aware `IndianFeeModel`, gating every
signal through the same `RiskEngine` that paper-trading uses.

## Fill semantics — T+1 open-to-open (locked)

**Both BUY and SELL fills happen at the NEXT bar's OPEN price.**
Strategy evaluates against today's CLOSE; order fills at the
next session's open.

```
Bar T close → strategy.eval_node fires → OrderIntent emitted
Bar T+1 open → SimBroker.execute fills at next_bar.open
```

Why open-to-open and not other variants:

| Variant | Realistic? | What it models |
|---|---|---|
| Same-day close → close | ❌ | Look-ahead bias — assumes you can place the order at exactly today's close |
| Same-day open → close | ❌ | Worse — needs to know today's open before market opened |
| **T+1 open → T+1 open** | ✅ | "Decided after close, ordered at next session's open" — the standard daily-bar convention |

**Implications for the trade table:**

- `Avg ₹` = T+1 OPEN at entry
- `Fill ₹` = T+1 OPEN at exit
- `today_ltp` feature in the strategy's eval context is named for tick-mode but in daily backtest = `bar.close`. Don't try to "fix" it — that would be look-ahead.

Verified flow (COALINDIA.NS, real-data run):

```
Jan 12 close → AND-condition fires (price > sma_50 + price > sma_200 + days_ago ≤ 10)
Jan 13 OPEN @ ₹434  → BUY 41 shares (avg_price = 434.00)
...
Jan 28 close → else-branch fires (condition broke)
Jan 29 OPEN @ ₹446  → SELL 41 shares (fill_price = 446.00)
Realised = (446 − 434) × 41 = +₹492
```

## Indicator engine — on-the-fly

There is **no** `stocks.technical_indicators` Iceberg table.
SMAs and `golden_cross_days_ago` are computed by
`backend/algo/backtest/indicators.py` directly from OHLCV via
O(N) rolling sums. Default windows: 20, 50, 200.

```python
# backend/algo/backtest/indicators.py
DEFAULT_WARMUP_BARS = 400   # ~270 trading days, comfortable for SMA200

def compute_indicators(bars, sma_windows=(20, 50, 200)):
    # Returns {bar_date: {feature_name: Decimal}}
    # SMA only added when window has filled (≥N points).
    # golden_cross_days_ago resets on cross-DOWN so a stale
    # up-cross isn't treated as fresh.
```

The runner calls `load_ohlcv_window(warmup_days=DEFAULT_WARMUP_BARS)`
so SMA200 is well-formed by `period_start`. Bars in the warmup
window feed the indicator engine but are never passed to
strategy evaluation.

**Sentinel:** for bars before the first SMA50 × SMA200 cross-up,
`golden_cross_days_ago = 999` so a strategy condition like
`<= 10` fails until a real cross fires.

## Risk gating

The same `RiskEngine.gate(signal, account, risk, last_price)` that
PaperRuntime uses runs in the backtest too — strategies behave
identically across modes.

3-tier check, in order: **kill switch → per-trade → daily → portfolio**.

| Tier | Cap | Behaviour on breach |
|---|---|---|
| Per-trade | `max_qty` | Hard reject (`reason: max_qty`) |
| Daily | `max_loss_pct` | Hard reject (`reason: daily_loss_cap`) |
| Daily | `max_open_positions` | Hard reject (`reason: max_open_positions`) |
| Portfolio | `max_concentration_pct` | Hard reject (`reason: position_cap`) |
| Portfolio | `max_exposure_pct` | **Scale qty** down to fit headroom; reject only if 0 headroom (`reason: exposure_cap`) |

SELL signals skip portfolio caps (they reduce exposure).

The runner emits a `signal_rejected` event with `reason +
threshold + observed_value` for every block — surfaces in the
**Replay** tab with `mode: backtest`.

## Equity curve mark-to-market

End-of-day equity at each bar uses today's CLOSE (or most-recent
prior close if the ticker doesn't trade today — holiday gap,
new listing) for the unrealised P&L contribution. Maintained as
a running `last_close: dict[str, Decimal]` updated as we walk.

```
equity = initial_capital
       + realised_pnl
       + unrealised_pnl(marks=last_close)
       − total_fees
```

## Trade table semantics

`PositionTracker` is one-position-per-ticker with weighted-avg
cost basis on additional BUYs. Each SELL closes either the full
position or a slice; each closed slice becomes its own
`TradeRow` in the run summary.

If you see multiple rows for the same ticker with the same
`opened_at` but different `closed_at` / `holding_days`, those
are **partial closes** of one continuous position (typically
caused by `set_target_weight` re-balances), not separate
entries.

## Async-job wrapper

`POST /v1/algo/backtest/run` accepts a `BacktestRequest`,
creates an `algo.runs` row in `pending` state, schedules a
`BackgroundTasks` job, and returns 202 with `run_id` immediately.
The frontend polls `GET /runs/{id}` every 2 seconds until status
ends in `completed` or `failed`.

```python
class BacktestRequest(BaseModel):
    strategy_id: UUID
    period_start: date
    period_end: date
    initial_capital_inr: Decimal = Decimal("100000.00")
```

## Reading the result

| Field | Meaning |
|---|---|
| `total_trades` | Closed-position count (= `trade_list` length) |
| `total_pnl_inr` / `total_pnl_pct` | `final_equity − initial_capital` |
| `win_rate_pct` | % of closed trades with realised PnL > 0 |
| `max_drawdown_pct` | Peak-to-trough draw on the equity curve |
| `total_fees_inr` | Sum of `fees_inr` on every Fill |
| `equity_curve` | `[{bar_date, equity_inr}]` — one snapshot per trading day |
| `trade_list` | `[{ticker, qty, avg_price, fill_price, opened_at, closed_at, holding_days, realised_pnl_inr, return_pct}]` |
| `fee_rates_version` | `effective_from` of the YAML row used (every fill stamps this) |

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/algo/backtest/run` | Start a run (returns 202 + run_id) |
| GET | `/v1/algo/backtest/runs/{run_id}` | Single-run summary |
| GET | `/v1/algo/backtest/runs?limit=50&offset=0` | List runs newest-first |

All gated `pro_or_superuser`.
