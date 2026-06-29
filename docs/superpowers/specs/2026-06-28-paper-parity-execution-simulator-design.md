# Paper Parity via Shared ExecutionSimulator (Piece B) — Design Spec

- **Date:** 2026-06-28
- **Status:** Draft
- **Scope:** Route the **paper runtime** through the shared
  `ExecutionSimulator` (built in Piece A) so paper and backtest evaluate
  trailing/stop exits through one component, fill at the same conceptual
  price, and book the same fees — differing only in **bar resolution**
  (paper 1m vs backtest 15m).
- **Branch:** `feature/paper-parity-execution-sim` off `dev` (`572b15b`,
  includes Piece A).
- **Related:** Piece A spec `2026-06-28-intraday-execution-clock-backtest-design.md`;
  Jira **ASETPLTFRM-456**; `.claude/rules/algo.md` (Intraday execution clock,
  Paper/live parity).

---

## 1. Problem

Paper and backtest both drive the **same** `TrailingStopManager` state
machine, but paper **re-implements the evaluation wrapper** inline
(`paper/runtime.py:307` `_trailing_managers` + the LOW/HIGH block at
`:685`), while backtest now uses the shared `ExecutionSimulator`
(Piece A). Two consequences:

1. **Drift risk** — the duplicated wrapper can diverge from the backtest's
   as either evolves; a promotion-gate engine should have one source of
   truth for exit logic.
2. **Fill / fee divergence** from backtest:
   - Paper fills exits at the **1m bar `last_price` ± slippage**
     (`PaperBroker.execute`, `broker.py`), whereas backtest fills stop
     exits at the **trigger price ± slippage** (models the live GTT).
   - `PaperBroker` **hardcodes `product="DELIVERY"`** (`broker.py:78`) —
     correct for CNC, **wrong for MIS** (should be INTRADAY). Backtest now
     derives product from `strategy.product`.

Paper is the promotion gate before live; its exit behaviour must match the
backtest a strategy was promoted from, modulo the inherent bar-resolution
difference.

## 2. Data reality (why paper is 1m, not 15m)

Paper is **live-tick driven**, not historical: `run(source)` drains a
`TickSource` (live Kite WS in production) and `Resampler(intervals=(60,))`
aggregates ticks into **1m bars in real time** (`runtime.py:514`). The 1m
granularity comes from watching the live market tick-by-tick — it is NOT
read from `stocks.intraday_bars` (which holds only 15m). Therefore:

- Paper at **1m** is its faithful replica of **live** (tick-level); the
  backtest's **15m** is the best *it* can do on preserved data.
- Paper and backtest **cannot** be bar-identical by construction. Parity =
  same exit *logic* + fill model + fee model, differing only in resolution.
- `intraday_coverage` / daily-fallback (Piece A) do **not** apply to paper
  — paper always has live data for its subscribed tickers.

## 3. Goals / Non-goals

**Goals**
- Paper evaluates trailing exits through the **shared `ExecutionSimulator`**
  — delete the duplicated `_trailing_managers` wrapper.
- Paper stop exits fill at **trigger ± slippage** (parity with backtest +
  live GTT).
- Paper fees use the **strategy's product** (CNC→DELIVERY, MIS→INTRADAY).
- A **parity test** proves paper and backtest produce identical
  `ExitDecision`s for the same bar series.

**Non-goals**
- No change to paper's **1m** resolution, the cadence-gated signal emitter,
  or the accounting from prior 6.x parity work (MTM equity, qty=0 events).
- No `intraday_coverage`/coverage logic in paper.
- Live runtime adopting `ExecutionSimulator` — separate later refactor.
- Generalising `ExecutionSimulator` to own hard/time stops — backtest keeps
  those in the runner; over-generalising now would create divergence
  (YAGNI).

## 4. Design

### 4.1 Paper runtime — use `ExecutionSimulator`
`backend/algo/paper/runtime.py`:
- Replace `self._trailing_managers: dict[str, TrailingStopManager]`
  (`:307`) with `self._exec_sim = ExecutionSimulator(strategy.risk.per_trade)`
  (instantiated under the existing `self._trailing_enabled` guard).
- **On confirmed BUY fill** (the manager-creation site `:1143`, where
  `_atr = wilder_atr(_bars_up, 14)`): call
  `self._exec_sim.on_buy_fill(fill.ticker, float(fill.fill_price), _atr)`
  instead of constructing a `TrailingStopManager` directly. (Same ATR
  source, unchanged.)
- **Per 1m bar** in `_on_bar_close` (replace the block `:685–~770`):
  ```python
  if self._trailing_enabled and self._exec_sim.has(bar.ticker):
      dec = self._exec_sim.evaluate_bar(
          bar.ticker, bar.low, bar.high
      )
      if dec is not None:
          # build SELL Signal(reason=dec.exit_reason,
          #   emitted_at_ns=bar.bar_open_ts_ns, qty=existing_pos.qty),
          # fill via PaperBroker with trigger_price=dec.trigger_price,
          # apply_fill + budget lifecycle + order_filled event
          # (payload trailing_phase=dec.phase, trailing_hwm=dec.hwm),
          # then self._exec_sim.drop(bar.ticker).
  ```
  The event payload and budget-lifecycle wiring are preserved verbatim;
  only the *source* of the decision (phase/hwm/reason/trigger) changes from
  the inline `_stop_event` to `dec`.
- **Preserve unchanged**: the flat %-stop path (used when trailing is
  disabled, `:811`), any time-stop, and the cooldown-hydration call after a
  trailing exit. Only the trailing block is swapped — symmetric with
  backtest (trailing-on → simulator owns hard + trail; trailing-off → flat
  path).

### 4.2 `PaperBroker` — trigger fill + product
`backend/algo/paper/broker.py`:
- **Trigger fill**: `execute(*, signal, last_price, fill_date,
  trigger_price: Decimal | None = None)`. When `trigger_price` is not None:
  `fill_price = _slipped_price(trigger_price, signal.side)` and fees are
  computed on the **unslipped `trigger_price`** (mirrors
  `SimBroker._execute_trigger_fill`). When None, behaviour is unchanged
  (fills at `last_price ± slippage`) — so entries and the flat-stop path
  are untouched.
- **Fee product**: add `product: str` to `PaperBroker.__init__`
  (`runtime.py:194` passes `"DELIVERY" if strategy.product == "CNC" else
  "INTRADAY"`). Replace the hardcoded `product="DELIVERY"` in `execute`
  with `self._product`.
- Reuses existing `ALGO_PAPER_SLIPPAGE_BPS` — **no new env var**.

### 4.3 Decision → fill mapping
`ExecutionSimulator.evaluate_bar` returns
`ExitDecision(ticker, exit_reason, trigger_price: Decimal, phase: int,
hwm: float)`. Paper builds the SELL `Signal` with `reason=exit_reason` and
fills via `PaperBroker.execute(..., trigger_price=dec.trigger_price)`. The
phase→reason mapping is no longer in paper — it lives once in
`ExecutionSimulator._reason_for_phase` (verified identical to paper's
former inline mapping: 1→phase1_stop, 15→phase1_ratchet, 2→trail_stop).

## 5. Testing

- **Parity test** (the headline): drive the SAME synthetic bar series
  through (a) `ExecutionSimulator` directly (backtest path) and (b) the
  paper exit path, with the same `RiskPerTrade`, entry price, and ATR.
  Assert identical `ExitDecision`s — same `exit_reason`, `phase`,
  `trigger_price` — at the same bar. (Confirms structural parity; bar
  resolution is the only intended difference.)
- **Paper trigger fill**: a paper trailing exit fills at
  `trigger ± ALGO_PAPER_SLIPPAGE_BPS` (BUY up / SELL down), fees on the
  unslipped trigger; `bps=0` → fills exactly at trigger.
- **Fee product**: a CNC paper strategy books DELIVERY fees; an MIS paper
  strategy books INTRADAY fees (regression for the hardcoded-DELIVERY bug).
- **No-regression**: existing paper suite stays green — trailing-disabled
  paper (flat stop path) and entries are byte-identical; the swapped
  trailing block produces the same exits/events as before for a covered
  scenario.
- Happy path + ≥1 error path each (§26). Run in-container:
  `docker compose exec -T backend python -m pytest backend/algo/paper/tests/<file> -v`.

## 6. Backward compatibility

- Trailing-**disabled** paper strategies are unaffected (flat-stop path
  unchanged; `_exec_sim` simply has no managers).
- `PaperBroker.execute` without `trigger_price` behaves exactly as today —
  entries and flat-stop exits unchanged.
- Fee-product change is a **fix**: CNC unchanged (was/stays DELIVERY); MIS
  corrected (was wrongly DELIVERY → now INTRADAY). Flag in the PR as a
  behaviour change for MIS paper P&L.

## 7. Out of scope / follow-ups

- Live runtime adopting `ExecutionSimulator` (separate refactor).
- Paper does not gain coverage/daily-fallback (not applicable).

## 8. Global constraints

- Python 3.12; line ≤ 79; `X | None`; no bare `except`; caught exceptions
  in the paper run loop log `exc_info=True` (already the pattern at `:725`).
- Money is `Decimal`; `TrailingStopManager` prices are `float` — convert at
  the boundary (already handled inside `ExecutionSimulator`).
- No new `Signal` field — `trigger_price` is a `PaperBroker.execute`
  parameter and `product` a `PaperBroker.__init__` parameter (deliberate
  signature additions, both backward-compatible defaults).
- Tests in `backend/algo/paper/tests/`; host has no pytest (use the
  container).
