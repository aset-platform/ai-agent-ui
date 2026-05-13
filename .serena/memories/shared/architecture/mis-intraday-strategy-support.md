# Architecture — MIS / intraday strategy support

Shipped via PR #219 (ASETPLTFRM-386) on 2026-05-13. This memory captures the durable shape — what lives where, the cadence × product axes, and the daily-strategy invariant.

## Two orthogonal axes

The system used to be locked at daily × CNC. Now both axes are configurable independently:

| Cadence | Product | Use case |
|---|---|---|
| `1d` | `CNC` | Existing default — swing strategies holding positions overnight. **No code path changed for this combination.** |
| `5m` / `15m` / `1m` | `CNC` | Intraday entry, hold to delivery (unusual but valid) |
| `5m` / `15m` / `1m` | `MIS` | Canonical MIS scalper — Zerodha auto-squares at 15:15 |
| `1d` | `MIS` | **Rejected by AST validator** — degenerate (open at close, broker squares within seconds) |

## Where each axis lives

**Cadence** = `Strategy.schedule.interval: Literal["1d", "15m", "5m", "1m"]`

**Product** = `Strategy.product: Literal["CNC", "MIS"] = "CNC"`

**Square-off time** = `Strategy.square_off_time: str | None = None` (only honoured when `product=="MIS"`; defaults to "15:14 IST" at runtime if MIS + unset)

All three are at the top of `backend/algo/strategy/ast.py`. Model validator `_mis_requires_intraday` rejects (`MIS`, `1d`) at parse time.

## Surface map

| Concern | Module |
|---|---|
| AST + validation | `backend/algo/strategy/ast.py` |
| KiteClient SDK boundary | `backend/algo/broker/kite_client.py` (`_ALLOWED_PRODUCTS = {CNC, MIS}`) |
| Kite historical 15m / 5m / 1m fetch | `KiteClient.fetch_intraday_historical(interval_sec, ...)` |
| Intraday bar preload at runtime spawn | `backend/algo/live/intraday_bar_warmup.py` (`preload_intraday_bars`) |
| Live-stream intraday bar storage | `algo.intraday_bars` Iceberg table (existing — populated by the resampler) |
| Bar routing (cadence-aware bucketing) | `backend/algo/live/runtime.py::_on_bar_close` |
| Order product forwarding | `runtime.py::_submit_order` reads `self._strategy.product` |
| MIS auto-square-off | `runtime.py::_schedule_mis_square_off` (asyncio task started in `run()`, cancelled in `finally:`) |
| Frontend cadence/product UI | `frontend/components/algo-trading/builder/CadenceProductPanel.tsx` |
| Strategy template | `frontend/components/algo-trading/builder/templates.ts::mis_rsi_scalper` |

## Bar bucketing — the key intraday primitive

```python
if strategy_interval == "1d":
    bucket_key = bar_date_obj   # date — daily path unchanged
else:
    interval_sec = INTERVAL_SEC_BY_LABEL[strategy_interval]
    interval_ns = interval_sec * 1_000_000_000
    bucket_open_ns = (bar.bar_open_ts_ns // interval_ns) * interval_ns
    bucket_key = bucket_open_ns
```

`BarData.bar_open_ts_ns: int | None = None` — daily bars leave it None; intraday bars carry the bucket start. The runtime's append-vs-update decision uses date for daily, bar_open_ts_ns for intraday.

## MIS auto-square-off lifecycle

1. `__init__` sets `self._square_off_task: asyncio.Task | None = None`
2. `run()` schedules `_schedule_mis_square_off()` ONLY when `strategy.product == "MIS"`
3. The task sleeps until `square_off_time` IST today (default 15:14 IST)
4. On wake, iterates `self._positions.open_positions()` and emits synthetic `SELL` signals (`reason="mis_auto_square_off"`) via the normal `_submit_order` path — caps + slippage + audit all apply
5. `finally:` block in `run()` cancels the task on session stop (None-safe for CNC)

The 15:14 timing leaves one minute before Zerodha's broker-side 15:15 auto-square — our SELL fills land in `algo.events` first.

## Daily-strategy invariant (verified across 9+ existing tests)

- `Strategy.product` defaults to `"CNC"` → every existing AST in `algo.strategies.ast_json` parses unchanged
- `BarData.bar_open_ts_ns` defaults to `None` → every existing construction site produces identical objects
- Bar routing branches on `interval == "1d"` first → daily path is bit-for-bit identical to pre-change
- `_ALLOWED_PRODUCTS` widened (not narrowed) → CNC still accepted
- Auto-square gate skips when product ≠ "MIS" → CNC strategies see no new background tasks
- Eval-gate carve-out skips when interval == "1d" → 14:30 IST gate still applies to daily

## Known v1 gaps (tracked as separate tickets)

- **ASETPLTFRM-397** — Kite postback subscription (auto-square fills lag ~30 s via REST poll)
- **ASETPLTFRM-398** — MIS slippage tune (bps bands daily-tuned; intraday spreads tighter)
- **ASETPLTFRM-399** — Strategy hot-reload (live runtime ignores AST edits until Stop+Start)
- **ASETPLTFRM-400** — Intraday backtest support (Backtest stays daily — no historical validation for MIS)
- **ASETPLTFRM-401** — F&O / NRML support (equity-only for v1)

## Spec doc

`docs/superpowers/specs/2026-05-13-mis-intraday-strategy.md` — per-layer design, risk callouts, phasing, open questions.
