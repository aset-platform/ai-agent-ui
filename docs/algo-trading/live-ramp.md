# Live Trading Ramp Procedure

**V2-5 — Live Order Placement**

This document describes the step-by-step procedure for safely ramping
up from ₹0 to full production capital on a new live strategy.

---

## Pre-requisites

Before placing a single real order:

1. **Kite connected** — complete OAuth via Connect Broker tab.
   Verify `algo:creds:{user_id}` in Redis has a valid
   `access_token_fernet`.

2. **Backtest with hold-out data** — run at least one walk-forward
   backtest with the **last 90 days excluded** from training.
   The walk-forward `aggregate_win_rate` must be > 0.

3. **Paper trading smoke test** — run the strategy in paper mode
   (replay fixture) for at least 2 market days to verify the
   signal-to-fill loop works end-to-end.

4. **Reconciliation check** — verify the Reconciliation tab shows
   zero drift. Open drifts indicate a position mismatch that must
   be resolved before live trading.

5. **Kill switch disarmed** — confirm kill switch is inactive on
   the Settings tab.

---

## Safety Belts (Caps)

Set caps conservatively and loosen only when each tier is validated:

| Cap | Initial | Description |
|---|---|---|
| `max_inr` | ₹1,000 | Maximum daily notional |
| `max_orders_per_day` | 2 | Hard ceiling on new orders |
| `allowed_tickers` | 1 ticker | Explicit allow-list |

These can be changed at any time from the Paper tab → Safety belts
panel. Changes take effect on the next bar evaluation.

---

## Ramp Schedule

Use this schedule as a **minimum** — extend any tier if you see
unexpected behaviour.

### Tier 1 — ₹1,000 (days 1–5)
- Max ₹: `1000`
- Max orders/day: `2`
- Tickers: 1 (most liquid)
- Goal: verify order lifecycle (submit → filled → position recorded).
- Pass criteria: 5 consecutive days with no unhandled errors in the
  algo events log, fills match Kite order book.

### Tier 2 — ₹2,500 (days 6–10)
- Max ₹: `2500`
- Max orders/day: `3`
- Tickers: 1–2
- Pass criteria: P&L within ±5% of backtest expectation over 5 days.
  Zero reconciliation drifts.

### Tier 3 — ₹5,000 (days 11–15)
- Max ₹: `5000`
- Max orders/day: `5`
- Tickers: up to 3
- Pass criteria: same as Tier 2 but over 5 more days.
  Repeat paper-mode walk-forward with latest 90-day hold-out.

### Tier 4 — ₹10,000 (days 16–25)
- Max ₹: `10000`
- Max orders/day: `8`
- Tickers: up to 5
- Pass criteria: 10 consecutive trading days, drawdown < 10%,
  Sharpe > 0.5.

### Tier 5 — ₹25,000 (days 26–40)
- Max ₹: `25000`
- Max orders/day: `10`
- Tickers: up to 8
- Pass criteria: same as Tier 4 extended to 15 days.
  Optional: add second strategy at ₹2,500 (start over at Tier 1
  for new strategy).

### Tier 6 — ₹50,000 (days 41–55)
- Max ₹: `50000`
- Max orders/day: `15`
- Pass criteria: 15 days, monthly return > benchmark (Nifty 50).

### Tier 7 — ₹1,00,000 (day 56+)
- Max ₹: `100000`
- Max orders/day: `20`
- Full production capital.

---

## Emergency Stop

### Immediate halt (kill switch)
Navigate to **Settings → Kill switch → Arm kill switch**.

This:
1. Sets `kill_switch_active=True` in Redis (sub-millisecond path).
2. Blocks ALL new signal emissions immediately.
3. Cancels any in-flight orders via `cancel_in_flight_orders()`.
4. Does NOT close held positions — manage those manually via your
   Zerodha web app or mobile app.

### Disable live mode only
Navigate to **Paper tab → Live order placement → Disable**.

This:
1. Sets `live_orders_enabled=False` for the strategy.
2. New bar evaluations will not place orders.
3. Does NOT cancel in-flight orders already submitted.
4. Use kill switch if you need immediate in-flight cancellation.

### Reduce caps without disabling
Update the Safety Belts form to lower `max_inr` to `0` and
`max_orders_per_day` to `0`. This creates a soft stop —
all new signals are rejected at Cap 3/4 without touching
the enabled flag.

---

## Monitoring

Check these daily during the ramp:

| What to check | Where |
|---|---|
| Fill accuracy | Paper tab → In-flight orders list |
| Reconciliation drifts | Paper tab → Reconciliation panel |
| Events log | Paper tab → Events timeline |
| Daily counters | Safety belts form → Today's usage |
| Equity curve | Performance tab → Walk-forward equity curves |

---

## Rollback

If a tier shows unacceptable behaviour:

1. Arm kill switch immediately.
2. Go to the position in Zerodha and close manually if needed.
3. Lower `max_inr` and `max_orders_per_day` to safe values.
4. Investigate the events log for the root cause.
5. Fix the strategy, re-run backtest with fresh hold-out data.
6. Re-start the ramp from Tier 1.

Never skip tiers when re-starting after a rollback.

---

## Notes

- Zerodha resets day-level counters at market open (~09:00 IST).
  Our `algo_live_caps_daily_reset` job mirrors this (09:00 IST
  Mon–Fri). Counters accumulate throughout the market day.
- MARKET and LIMIT orders only. SL/SLM/BO/CO orders are rejected
  at the broker layer (`ValueError`) to prevent unintended
  OCO position sizing.
- All live events are written to the Iceberg `algo.events` table
  (`order_submitted_live`, `order_filled_live`, etc.) — they appear
  in the Replay tab for post-trade analysis.
