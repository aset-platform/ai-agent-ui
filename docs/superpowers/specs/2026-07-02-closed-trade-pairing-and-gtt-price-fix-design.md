# Closed-Trade Pairing + GTT Fill-Price Fix — Design

## Problem

Found 2026-07-02 while reviewing the Strategy Performance page against
Zerodha's actual positions: several rows showed wrong fill prices, and
one ticker (KTKBANK) showed a materially wrong quantity. Root cause is
two independent bugs, both live in production today.

### Bug 1 — index-based fill pairing ignores quantity

`backend/algo/attribution/trade_pairing.py` and
`backend/algo/routes/attribution.py` (which `trade_pairing.py` was
modeled on, per its own docstring — same bug in both, never shared)
both pair BUY/SELL fills by list *index*, not by matching quantity:

```python
for i in range(min(len(buys), len(sells))):
    buy_fill, sell_fill = buys[i], sells[i]
    qty = int(buy_fill["_payload"].get("qty") or 0)  # always the BUY's qty
```

This is correct only when a ticker's fills form a clean 1:1 sequence
(one buy, one sell). It silently breaks whenever a position is
exited via multiple partial sells, or entered via multiple partial
buys against fewer sells.

Concrete case — KTKBANK, strategy RSI(2) Connors Daily v5, live mode:
one BUY of 16 shares (2026-06-24), then three separate SELLs: 1 @
₹267.00, 1 @ ₹267.00 (same instant), and 14 @ ₹270.15 (2026-07-02,
`user_exit`) — totaling 16, matching the buy exactly. Because there
is 1 buy and 3 sells, `min(1,3)=1`: the loop pairs the buy with only
the *first* sell, reports the full buy quantity (16) against that
first sell's price (267.00), and silently drops the other two sell
events — including the 14-share, ₹270.15 exit, which never appears
anywhere in the Performance page or Attribution panel.

A full scan of `algo.events` (both live and paper) found **8 of 65**
`(mode, strategy_id, ticker)` buckets with mismatched buy/sell fill
counts: `live/KTKBANK` (1 buy, 3 sells), `live/COALINDIA` (10/7),
`paper/ADANIPORTS` (6/5), `paper/MOVALUE` (7/4), `paper/FEDERALBNK`
(4/2), `paper/SANSERA` (4/2), `paper/CUPID` (5/2),
`paper/MTARTECH` (2/1).

### Bug 2 — GTT-triggered fill price is an estimate, not the real fill

Three tickers with otherwise-correct quantities (SKYGOLD, SOUTHBANK,
ZENTEC — all exited via `gtt_triggered` today, 2026-07-02) show a
fill price consistently *lower* than Zerodha's actual executed price
(a SELL, so lower is unfavorable-looking though the real fills were
actually all favorable vs. our estimate):

| Ticker | Our recorded fill | Zerodha actual avg |
|---|---|---|
| SKYGOLD | 551.45 | 553.30 |
| SOUTHBANK | 45.85 | 46.05 |
| ZENTEC | 1,753.20 | 1,760.70 |

`live/runtime.py::_ratchet_all_gtts` (Piece A, the GTT-poll fallback
added 2026-07-02 per ASETPLTFRM-466) sources the fill price from
`_kite_order.get('price') or _stop_price` — the GTT's *configured*
order price at last poll, not Kite's actual post-trigger execution
price. `kite.get_gtts()` only exposes the GTT's condition/order
config, never the realized fill. This was flagged as a documented
estimate for HSCL specifically at the time of that fix, but it's
actually systematic for every Piece-A-captured GTT trigger, not a
one-off.

## Design

Two independent fixes, one shared component.

### Shared quantity-aware FIFO matcher

New `backend/algo/attribution/fifo_matcher.py`:

```python
def match_fifo(
    buys: list[dict],   # each: event_id, qty, price, ts_ns
    sells: list[dict],  # each: event_id, qty, price, ts_ns
) -> list[dict]:
    """Quantity-aware FIFO match. Returns closed lots -- each
    carries the buy/sell event ids, the qty actually closed, the
    buy price, the sell price, and both timestamps. A buy lot
    larger than the sell that (partially) closes it produces
    multiple output records against the same buy_event_id, one
    per consuming sell. Unconsumed buy quantity (open position,
    no sell yet) is NOT included in the output -- unchanged from
    today's semantics."""
```

Algorithm: sort buys and sells by `ts_ns` (already required by both
callers). Walk a mutable FIFO queue of `(event_id, remaining_qty,
price, ts_ns)` buy-lots. For each sell in order, consume from the
front of the buy queue: `slice_qty = min(sell.remaining_qty,
buy_lot.remaining_qty)`. Emit one closed-lot record per slice
(`qty=slice_qty`, `avg_price=buy_lot.price`, `fill_price=sell.price`,
`opened_at/closed_at` from the respective `ts_ns`, both event ids).
Decrement both remaining quantities; when a buy lot reaches 0,
advance to the next one; when a sell's quantity is fully consumed,
advance to the next sell. A sell with quantity left over after the
buy queue is exhausted (more sold than bought — should not happen in
practice but is a real data-quality signal) is dropped with a
`_logger.warning` rather than silently ignored, so a genuine data
problem surfaces instead of masquerading as a clean pairing.

`trade_pairing.py` and `attribution.py` both keep their existing
bucketing (`(strategy_id, ticker)` / `ticker`), payload parsing,
price-key fallback (`fill_price` / `price`), and per-record
enrichment (dry_run, exit_reason, attribution-specific signal/regime
context) — only the pairing loop itself is replaced with a call to
`match_fifo`.

### GTT true-fill-price lookup

`live/runtime.py::_ratchet_all_gtts`, in the block that currently
computes `_fill_price = float(_kite_order.get("price") or
_stop_price)`: before falling back to the estimate, call
`self._kite._kc.orders()` (raw KiteConnect client — `KiteClient`
does not wrap `orders()`, same gotcha as its missing `ltp()`
wrapper; matches the existing precedent in
`live/order_timeout.py::_fetch_orders`, which already does
`getattr(self._kite, "_kc", None)` then `kc.orders()`, with a
fallback to a directly-patched `orders` attribute for test
fixtures) — today-scoped Kite order history — and look for a
matching real order: `tradingsymbol == ticker bare symbol`,
`transaction_type == "SELL"`, `status == "COMPLETE"`,
`order_timestamp >= <trigger poll time>`. If found, use its
`average_price`. If not found (order history doesn't have it yet,
API error, or genuinely no match), fall back to today's estimate
unchanged. Either way, tag the `order_filled_live` event payload
with a new `price_source` field: `"kite_orders"` (confirmed) or
`"gtt_config_estimate"` (estimate) — additive, no schema change
(event payloads are a JSON blob), makes the data quality visible to
anyone reading `algo.events` directly without guessing.

### Backfill

Both fixes are logic-only, no schema change to `algo.closed_trades`
or `algo.events`.

1. **GTT price correction (SKYGOLD/SOUTHBANK/ZENTEC only)** — one-off
   script, run once, before the pairing backfill: call
   `kite._kc.orders()` now (still today, so today's order history is
   still available), find each ticker's matching real SELL order,
   and patch the *already-written* `order_filled_live` events'
   `payload.price` (Iceberg is append-only — this is a scoped
   delete-and-reinsert of just those 3 event rows via
   `In("event_id", [...])`, not a mutation). HSCL (2026-07-01,
   yesterday) is out of scope — Kite's order history is day-scoped,
   its trigger is no longer recoverable via this API; it keeps its
   documented estimate from ASETPLTFRM-466.
2. **Pairing re-derive (full)** — `TRUNCATE algo.closed_trades`, then
   re-run `scripts/backfill_closed_trades.py` (window_days=3650)
   against the now-corrected `algo.events` history with the fixed
   `match_fifo`-based pairing. Truncate-then-rebuild (not
   incremental) because the existing `ON CONFLICT (buy_event_id,
   sell_event_id) DO NOTHING` upsert would never touch the
   already-wrong KTKBANK row — its key already exists from the
   buggy pairing run. A full rebuild is simplest and correct; the
   underlying event data is unchanged, only the derivation logic is,
   so re-deriving from scratch is safe and idempotent.

## Testing

- `fifo_matcher.py`: new test file. Cases: 1 buy/1 sell (regression,
  must match today's output exactly), 1 buy/N sells (KTKBANK shape —
  split into N records), N buys/1 sell (reverse split), buy quantity
  left over with no sell (still excluded from output, unchanged),
  sell quantity exceeding available buy quantity (dropped + warning
  logged, not silently produced as a phantom trade).
- `trade_pairing.py` / `attribution.py`: existing tests re-run
  unchanged (1:1 cases must produce identical output through the new
  matcher) plus one new KTKBANK-shaped multi-sell test in each
  file's existing test suite.
- GTT price lookup: mock `kite._kc.orders()` returning a matching
  COMPLETE SELL order → wrapper uses its `average_price`, tags
  `price_source="kite_orders"`. No match / empty order list → falls
  back to the existing estimate, tags
  `price_source="gtt_config_estimate"`. Existing
  `test_ratchet_gtt_poll_emits_fill.py` regression test must still
  pass unchanged (it doesn't mock `orders()`, so it exercises the
  fallback path).

## Out of scope

- HSCL's already-recorded estimated price (not recoverable, see
  Backfill section).
- Any change to the dual-bar RSI(2) entry-confirmation gate or other
  strategy logic — unrelated.
- Extending `price_source` tagging to non-GTT fills (`user_exit`,
  plain `signal` exits) — those already carry Kite's real order
  price directly from the order-placement response, no estimation
  involved.
