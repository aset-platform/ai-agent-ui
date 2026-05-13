# Spec — MIS / Intraday Strategy Support

**Jira epic**: [ASETPLTFRM-386](https://asequitytrading.atlassian.net/browse/ASETPLTFRM-386)
**Date drafted**: 2026-05-13
**Status**: DRAFT — pre-implementation skeleton, to be fleshed out before Phase 1 starts.

## TL;DR

Today every live strategy is hard-locked to **daily cadence + CNC product**. There is no path for intraday (5-min / 1-min) or MIS strategies. This epic adds the cadence × product axes additively, so existing daily/CNC strategies keep working and new intraday/MIS strategies become possible.

v1 ships the **5-min × MIS** combination. The other cells in the 2×2 are mostly research / unused.

## Problem statement

A user opens the Strategy Builder, types an RSI scalper meant to trade INFY on 5-min bars with MIS leverage, saves it, and starts a Live runtime. Today:

- The AST validator rejects `interval: "5m"` — `Literal["1d"]` only.
- Even if we widened the literal, `runtime.py` would still send `product="CNC"` to Kite.
- Even if we set `product="MIS"`, `KiteClient._ALLOWED_PRODUCTS` would reject it at the SDK boundary.
- Even if the order placed, the strategy series the runtime evaluates against is *daily* — RSI computed on 250 daily closes, not on the last 100 5-min bars. So the user's signal logic would produce wildly wrong actions.
- Even if the series were intraday, the `14:30 IST` eval gate would suppress every fire before 14:30 — the strategy would never trade in the morning.
- Even if it fired in the morning, there's no auto-square at 15:14 IST. Kite's broker-side auto-square would land at 15:15 but the fill wouldn't reach our ledger until the postback round-trip — confusing P&L.

Net: there is **no single line of code** wrong, but **six** layers each silently force the daily/CNC path. This spec sequences the changes so we don't get tripped up at one layer while the rest are still aligned.

## Goals

1. A user can create + run a 5-min × MIS strategy end-to-end through the UI Builder.
2. Existing daily × CNC strategies are unaffected (additive feature).
3. P&L for MIS positions is captured in our ledger before Kite's 15:15 auto-square forces it.
4. Migration is read-side: existing strategies missing the new fields default to CNC. No backfill / no schema migration on `algo.strategies`.

## Non-goals (v1)

- F&O / options on MIS — separate product family (`MIS` here means equity intraday only).
- Order varieties beyond `regular` (no BO / CO).
- MIS-leverage-aware position sizing (margin = notional / 5). v1 treats `max_inr` as **notional spent**, conservatively. Surface the implication in UI helper text.
- Hot-swap of a running runtime's strategy spec on Save (today's RSI<40 bug). Tracked separately.
- Intraday-cadence backtesting. Phase 2 of this epic only addresses Live; Backtest stays on daily until a follow-up epic.

## Design

### 1. AST changes (`backend/algo/strategy/ast.py`)

```python
class ScheduleBarClose(BaseModel):
    type: Literal["bar_close"] = "bar_close"
    interval: Literal["1d", "15m", "5m", "1m"]  # was Literal["1d"]
    time: str = Field(default="15:25 IST")


class Strategy(BaseModel):
    ...
    product: Literal["CNC", "MIS"] = "CNC"   # NEW
    square_off_time: str | None = None        # NEW, only honoured when MIS

    @model_validator(mode="after")
    def _mis_requires_intraday(self) -> "Strategy":
        if self.product == "MIS" and self.schedule.interval == "1d":
            raise ValueError(
                "MIS product requires intraday cadence "
                "(15m / 5m / 1m). "
                "Daily strategies must use CNC."
            )
        return self
```

**Read-path migration**: `strategy_adapter.validate_python(payload)` already defaults missing fields via Pydantic — `product` not present → `"CNC"`. No DB migration needed.

### 2. KiteClient (`backend/algo/broker/kite_client.py:423`)

```python
_ALLOWED_PRODUCTS = frozenset({"CNC", "MIS"})  # widened
```

### 3. Runtime order placement (`backend/algo/live/runtime.py`)

Three literal `"CNC"` references to replace with `self._strategy.product`:

- `runtime.py:924` — `place_order(..., product=...)`
- `runtime.py:988` — `in_flight_entry["product"] = ...`
- `runtime.py:1068` — synthetic-fill `product = "DELIVERY"` → `"DELIVERY"` for CNC, `"INTRADAY"` for MIS (for `IndianFeeModel.Trade.product`)

### 4. Eval-gate carve-out (`runtime.py:666-668`)

```python
if (
    self._strategy.schedule.interval == "1d"
    and now_ist_t < _MIN_EVAL_TIME_IST
):
    return 0
```

Daily strategies keep the 14:30 IST gate; intraday strategies fire from 09:15 IST.

### 5. Intraday bar warmup

New module or new function in `backend/algo/live/daily_bar_warmup.py`:

```python
def preload_intraday_bars(
    tickers: list[str],
    interval: Literal["15m", "5m", "1m"],
    kite_client: KiteClient,
    ticker_to_token: dict[str, int] | None = None,
) -> dict[str, list[_BackBar]]:
    """Preload N closed intraday bars per ticker.
    
    Source priority:
      1. `algo.intraday_bars` Iceberg table (already in _HOT_ICEBERG_TABLES).
      2. Kite `historical_data` REST fallback for fresh-day gaps.
    
    Window: 100 bars (RSI 14 + MACD 26+9 with ~60 bars of headroom).
    """
```

In `LiveRuntime.__init__`, branch on `strategy.schedule.interval`:

| interval | preload | series shape |
|---|---|---|
| `"1d"`  | `preload_daily_bars` (existing) | 250 closed daily bars + 1 running |
| `"15m"` | `preload_intraday_bars(interval="15m")` | 100 closed 15-min bars + 1 running |
| `"5m"`  | `preload_intraday_bars(interval="5m")` | 100 closed 5-min bars + 1 running |
| `"1m"`  | `preload_intraday_bars(interval="1m")` | 100 closed 1-min bars + 1 running |

### 6. Bar routing (`runtime.py`)

```python
# Today
self._bars_by_ticker: dict[str, list[_BackBar]] = {}

# New
self._bars_by_key: dict[tuple[str, str], list[_BackBar]] = {}
```

Key = `(ticker, interval)`. Daily and 5-min series for the same ticker are independent buckets.

Bar-close handler branches on `strategy.schedule.interval`:

- **1d**: fold each minute bar into today's running daily bar (existing behaviour, lines 643-660).
- **15m**: aggregate 15 consecutive 1-min bars into one 15-min bar; close it; append to series; evaluate.
- **5m**: aggregate 5 consecutive 1-min bars into one 5-min bar; close it; append to series; evaluate.
- **1m**: each closed 1-min bar from the resampler is a complete bar; append; evaluate.

For 5-min / 15-min cadence we can either:

- **Option A**: instantiate `Resampler(intervals=(60, 300, 900))` and consume the secondary streams directly. Cleaner but requires the resampler API to support multiple intervals natively (need to verify).
- **Option B**: keep the 1-min resampler, manually aggregate N bars in the runtime. Simpler, more flexible.

Decision deferred to impl. Lean Option B unless the resampler already supports multi-interval.

### 7. MIS auto-square-off (`runtime.py`)

```python
async def _schedule_mis_square_off(self) -> None:
    """Sleep until square_off_time, then emit SELL signals for every
    open MIS position. Cancelled if the runtime stops first."""
    if self._strategy.product != "MIS":
        return
    target_ist = parse_ist_time(
        self._strategy.square_off_time or "15:14 IST",
    )
    delay_s = seconds_until_ist(target_ist)
    if delay_s <= 0:
        return  # already past — no-op
    await asyncio.sleep(delay_s)
    for ticker, pos in list(self._positions.open_positions().items()):
        if pos.qty <= 0:
            continue
        synthetic = Signal(
            strategy_id=self._strategy.id,
            user_id=self._user_id,
            ticker=ticker,
            side="SELL",
            qty=pos.qty,
            emitted_at_ns=time.time_ns(),
            reason="mis_auto_square_off",
        )
        await self._submit_order(
            signal=synthetic,
            last_price=self._last_known_price(ticker),
        )
```

Started in `LiveRuntime.start()`; stored on `self._square_off_task` so `stop()` can cancel it.

Safety net: Kite still auto-squares at 15:15 IST. Our 15:14 fire ensures the fill lands in our ledger before Kite forces it.

### 8. Strategy template (`frontend/components/algo-trading/builder/templates.ts`)

```ts
mis_rsi_scalper: {
  name: "MIS RSI Scalper (5-min)",
  universe: {
    type: "scope",
    scope: "watchlist",
    filter: { kind: "all" },
  },
  schedule: { type: "bar_close", interval: "5m", time: "15:14 IST" },
  rebalance: { type: "daily", max_positions: 3 },
  root: {
    type: "if",
    cond: {
      op: ">",
      left: { feature: "rsi" },
      right: { literal: 70 },
    },
    then: { type: "exit", scope: "this_symbol" },
    else: {
      type: "if",
      cond: {
        op: "<",
        left: { feature: "rsi" },
        right: { literal: 30 },
      },
      then: { type: "set_target_weight", weight: 0.2 },
      else: { type: "hold" },
    },
  },
  risk: {
    per_trade: { stop_loss_pct: 1.0, max_qty: 100 },
    portfolio: {
      max_exposure_pct: 50,
      max_concentration_pct: 20,
    },
    daily: { max_loss_pct: 2.0, max_open_positions: 3 },
  },
  product: "MIS",
  square_off_time: "15:14 IST",
}
```

### 9. UI Strategy Builder

Two new radio groups in `StrategyBuilder.tsx`:

```
Cadence:  ( ) Daily (1d)    ( ) 15-min    ( ) 5-min    ( ) 1-min
Product:  ( ) CNC (Delivery)    ( ) MIS (Intraday)
```

When **Product = MIS** selected:

- **Daily** cadence option disabled (tooltip: "MIS must use intraday cadence.").
- New time picker appears: "Square-off time" (default 15:14 IST, range 14:30–15:14 IST).
- Helper text under `max_inr`: *"For MIS strategies, this is your notional cap. Kite's 5× MIS margin means ₹3000 here uses ~₹600 of margin but opens up to ₹3000 of position."*

Existing Daily / CNC behaviour is unchanged when the radios are at their defaults.

## Risk callouts

### 1. MIS leverage UX trap

Kite's 5× MIS margin means `max_inr=₹3000` allows ₹15 000 of position notional. v1 keeps `max_inr` as the **notional** cap (conservative — user can only "spend" ₹3K of position value), but the user might *expect* it to be margin and be surprised by smaller-than-expected fills.

**Mitigation**: explicit UI helper text on `max_inr` for MIS strategies (story P2.4).
**Follow-up**: a separate spec on leverage-aware position sizing — out of scope for v1.

### 2. Slippage band mis-tune

`_slippage.bps_for(bucket)` returns 20/50/100/30 bps for large/mid/small/unknown caps respectively. These were tuned against **daily entries** where the strategy enters at the close-bar price and Kite fills against the next morning's open. For 5-min scalps the relevant spread is tighter (a few ticks intra-bar). 100 bps on smallcaps means our LIMIT price floats ~1% off LTP — likely outside the bid/ask spread, fill drift will be poor.

**Mitigation**: keep defaults for v1; log fill drift per order so we can tune in a follow-up once we have data.
**Follow-up**: `MIS Slippage Tune (post-v1)` — to be filed after the smoke E2E lands.

### 3. Auto-square robustness

If the runtime crashes between 15:14 (our square-off fire) and 15:15 (Kite's broker-side auto-square), Kite still squares the position. But:

- The Kite-driven square fill doesn't surface as `order_submitted_live` (we never sent the order).
- It arrives as a postback event that hydration picks up on next runtime spawn.
- Our P&L lags by one postback cycle (≤30 s in practice).

**Mitigation**: the existing position-hydration logic (ASETPLTFRM-376) already handles broker-driven position changes on next spawn. No new code needed; just acknowledge in the runbook.
**Follow-up**: optionally add a Kite postback subscription that auto-replays into our ledger in near-real-time, so even crashed-runtime scenarios reconcile within seconds.

### 4. No retro-compat break

Existing strategies in `algo.strategies.ast_json` lack `product` / `square_off_time`. Pydantic defaults `product="CNC"` on read; `square_off_time=None` is only honoured for MIS so missing-on-CNC is a no-op.

**Verification**: in Phase 1 dev, run the migration locally against today's `algo.strategies` table dump — confirm every existing row parses with `product="CNC"`.

### 5. Strategy hot-reload

A user editing a running strategy's RSI threshold doesn't take effect until the runtime is stopped and re-started (today's session bug). The same applies to changing `product` or `interval`. The UI should make this clear with a "Restart runtime to apply changes" banner when the strategy's `updated_at` is newer than the current run's `started_at`.

**Mitigation**: separate ticket — already filed in passing as a follow-up to today's "RSI<40 didn't take effect" investigation.

## Story → ticket map

| Story | Ticket | SP | Phase |
|---|---|---|---|
| P1.1 Widen AST + product field | ASETPLTFRM-387 | 2 | 1 |
| P1.2 Widen KiteClient _ALLOWED_PRODUCTS | ASETPLTFRM-388 | 1 | 1 |
| P1.3 Runtime reads strategy.product | ASETPLTFRM-389 | 2 | 1 |
| P1.4 Eval-gate carve-out for non-1d | ASETPLTFRM-390 | 1 | 1 |
| P1.5 Strategy template mis_rsi_scalper | ASETPLTFRM-391 | 1 | 1 |
| P2.1 Intraday bar preload | ASETPLTFRM-392 | 5 | 2 |
| P2.2 Bar routing keyed by (ticker, interval) | ASETPLTFRM-393 | 5 | 2 |
| P2.3 MIS auto-square-off task | ASETPLTFRM-394 | 3 | 2 |
| P2.4 Builder UI radios | ASETPLTFRM-395 | 3 | 2 |
| P2.5 Smoke E2E | ASETPLTFRM-396 | 2 | 2 |
| **Total** | | **25 SP** | |

## Phasing & merge order

**Phase 1 (~7 SP, ~1 day)** — Toggle wiring. Lands the AST + KiteClient + runtime-product changes so a CNC-cadence (1d) MIS strategy is at least *creatable* end-to-end. Smoke-test the path by hand without intraday runtime yet.

- Merge order: 387 → 388 → 389 → 390 → 391
- Each story is independently revertable; 388 and 389 must land together to avoid a brief window where AST accepts MIS but Kite rejects it.

**Phase 2 (~16 SP, ~1.5 days)** — Intraday cadence runtime + auto-square + UI. The real value. Each story can be independently reviewed; only 393 (bar routing) is on the critical path.

- Merge order: 392 → 393 → 394 → 395 → 396

## Acceptance criteria

Spec is "done" when:

- [ ] All 10 stories merged and Done in Jira.
- [ ] One MIS 5-min strategy created end-to-end via the UI Builder.
- [ ] Smoke E2E (P2.5) green in CI.
- [ ] Existing live-trading test suite green.
- [ ] Runbook delta in `docs/runbooks/algo-live.md` covering: how to create an MIS strategy, what to expect at 15:14 IST, how to read MIS rows in the Positions tab.
- [ ] Migration verified on dev data: existing strategies still parse, default to CNC, run unchanged.
- [ ] Risk callout #2 (slippage) has a follow-up ticket on the backlog.

## Open questions

1. **5-min resampling source of truth**: do we trust `Resampler` for the in-process aggregation, or query `algo.intraday_bars` which is Bhavcopy-style? Decision affects P2.1 + P2.2 boundary.
2. **MIS leverage display**: do we show "Margin used: ₹600 of ₹3000 cap" anywhere in the live dashboard, or keep the v1 surface minimal? Probably the latter — defer to a follow-up UX ticket.
3. **Should daily × MIS be allowed for back-compat?** Currently the AST validator rejects this combo. Anyone wanting to test MIS without intraday plumbing could swap to a 5-min strategy. Vote: keep the rejection — clearer invariant.
4. **F&O scope**: do we explicitly carve F&O out of `Strategy.product` (would need `product_type: Literal["equity", "fno"]`) or rely on the universe filter to keep instruments restricted to NSE_EQ? Vote: rely on universe filter for v1; revisit if we ever add NFO instruments.

## Out-of-scope follow-ups (filed elsewhere or to be filed)

- MIS slippage tune (post-v1 data analysis)
- Strategy hot-reload (separate from this epic)
- Kite postback live subscription (P&L lag mitigation)
- Intraday-cadence backtest support (separate epic)
- F&O / options on MIS (separate product family epic)
