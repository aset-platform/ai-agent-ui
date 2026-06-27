# Live Runtime — Strategy Budget Qty Cap

Added 2026-06-24 on `feature/rsi2-exit-strategy`.

## Problem

`set_target_weight` computes qty from `current_equity × weight / price` using the strategy's
initial equity pool — not the real Kite available balance or how much is already deployed.
This caused over-sized orders (e.g. SKYGOLD qty=5 when strategy only had ₹1,628 remaining).

## Root cause

`current_equity = self._initial + total_realised_pnl_inr()` — no deduction for open positions.
Zerodha's `live_balance` is even higher (user keeps a cash buffer outside the strategy allocation),
so fetching Kite balance doesn't help either.

## Fix (commit `2266cb0`)

Gate added in `LiveRuntime._on_bar_close`, **after** `current_caps` and `committed_inr_now` are
computed, **before** `pre_trade_check`:

```python
_max_inr = Decimal(str(current_caps.get("max_inr") or 0))
if _max_inr > 0:
    _remaining = _max_inr - committed_inr_now          # strategy headroom
    _affordable = int(_remaining // last_price) if _remaining > 0 else 0
    if _affordable < 1:
        # emit signal_rejected(reason="insufficient_balance")
        return 0
    elif _affordable < signal.qty:
        # emit signal_adjusted(reason="strategy_budget_cap")
        signal = signal.model_copy(update={"qty": _affordable})
```

`committed_inr_now = sum(p.qty × p.avg_price for p in open_positions)` — same quantity
used by Cap 4 in `safety.py` (`cumulative_inr_today`). The gate reduces qty proactively
rather than letting Cap 4 reject the full order.

## Events emitted

| Type | Payload keys |
|---|---|
| `signal_adjusted` | old_qty, new_qty, max_inr, committed_inr, remaining_inr, last_price, reason="strategy_budget_cap" |
| `signal_rejected` | reason="insufficient_balance", max_inr, committed_inr, remaining_inr, last_price |

## Why NOT Kite live_balance

An earlier attempt used `fetch_kite_available_cash` (commit `60b8629`, then replaced).
The user keeps a Zerodha cash buffer beyond the strategy allocation, so Kite balance
is always higher → qty wasn't reduced enough. Strategy `max_inr` is the authoritative cap.

## Fail-open rule

`max_inr = 0` means "no cap configured" → gate is skipped entirely, original qty passes through.

## Tests

`backend/algo/live/tests/test_balance_cap.py` — 3 cases: full budget (no adj), partial (adj), zero (reject).
