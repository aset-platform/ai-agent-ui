# Backtest fill model — T+1 open-to-open (locked)

User-confirmed semantics. **Don't propose alternatives** — they reintroduce look-ahead bias.

## Rule

```
Bar T close → strategy.eval_node fires → OrderIntent emitted
Bar T+1 open → SimBroker.execute fills at next_bar.open
```

Both BUY and SELL use the same rule. With only daily OHLCV available, this is the only look-ahead-free option. Captures the realistic ~1-day lag between "I see today's close" and "the broker has my order live".

## Verified flow (COALINDIA.NS Jan 2026)

```
Bar T   = Jan 12 close → AND-condition fires (price > sma_50 AND price > sma_200 AND days_ago ≤ 10)
Bar T+1 = Jan 13 OPEN @ ₹434  → BUY 41 shares (avg_price = 434.00)
...
Bar T'  = Jan 28 close → else-branch fires (condition broke)
Bar T'+1 = Jan 29 OPEN @ ₹446 → SELL 41 shares (fill_price = 446.00)
Realised = (446 − 434) × 41 = +₹492
```

## Implications

- `today_ltp` feature in `EvalContext.features` is named for tick-mode but in daily backtest = `bar.close`. Don't try to alias to `bar.open` or `bar.high` — that would be look-ahead.
- TradeRow's `Avg ₹` column = T+1 open (entry); `Fill ₹` column = T+1 open (exit).
- Equity curve marks open positions to today's CLOSE for the unrealised P&L contribution. Live mid-day prices are out of scope for daily-bar backtest.
- Tick-level paper trading is allowed to fill at-tick LTP (PaperBroker does this) — that's correct because PaperRuntime is event-driven, not bar-driven, and fills happen on the same tick that triggered the signal.

## Don't propose

- Same-bar close-to-close (look-ahead — assumes you can place the order at exactly today's close).
- Same-bar open-to-close (worse — you'd need to know today's open before market opened).
- Tick-level intra-bar in backtest (we don't have ticks for historical periods, only daily OHLCV).
- "Use today's high/low for slippage modelling" — slippage is a separate concern from fill-price semantics; if added, layer on top of the T+1 open as an adjustment, don't change the base semantics.

## Implementation reference

- `backend/algo/backtest/sim_broker.py::SimBroker.execute` — `fill_price=next_bar.open`
- `backend/algo/backtest/runner.py::_action_to_intent` — emits intent with `intent_emitted_at = bar_date` (= T)
- `backend/algo/backtest/positions.py::PositionTracker.apply_fill` — stores `avg_price = fill_price` (= T+1 open)
