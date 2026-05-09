# Paper trading

Tick-driven runtime that runs a strategy against a stream of
ticks (replay-fixture or live Kite WS in v2), gating every
signal through the same `RiskEngine` the backtest uses, filling
at the current tick's LTP via `PaperBroker`.

## Architecture

```
TickSource          # ReplayTickSource (.jsonl fixture)
                    # LiveTickSource (KiteTicker WS, lifecycle in v2)
   ↓ async iterator
PaperRuntime        # one instance per (user, strategy) — process-local
   ├─ Resampler     # tick → 1m + 5m bars
   ├─ Evaluator     # AST → action dict
   ├─ RiskEngine    # 3-tier gate
   ├─ PaperBroker   # at-tick LTP fills + IndianFeeModel
   └─ PositionTracker
   ↓
events flushed to algo.events on shutdown (single Iceberg commit)
```

`PaperSupervisor` is a per-process registry of running tasks
keyed by `(user_id, strategy_id)`. `start_run` spawns,
`stop_run` cancels + awaits, `list_active` filters by user.

## Replay-fixture mode

A "fixture" is a `.jsonl` file in
`backend/algo/tests/fixtures/`. Each line is one tick:

```json
{"ticker": "RELIANCE.NS", "ts_ns": 1735723800000000000, "ltp": 1234.5, "volume": 12345}
```

The Resampler buckets ticks into 1-minute bars by `ts_ns //
60_000_000_000`. If ticks are days apart (one tick per daily
close), each ends up in its own bar — same SMA semantics as the
backtest.

### Available fixtures

| File | Ticks | Tickers | Purpose |
|---|---|---|---|
| `ticks_sample.jsonl` | 30 | FAKE.NS only | Smoke-test the runtime — too few bars for SMA-based strategies |
| `ticks_indian_universe.jsonl` | 3,015 | 9 NSE blue chips (RELIANCE, TCS, INFY, HDFCBANK, ICICIBANK, COALINDIA, SBIN, WIPRO, ITC) | Real strategy validation — daily closes 2025-01-01 → 2026-05-08 |

The frontend `ActiveRunsPanel` lists these via `GET
/v1/algo/paper/fixtures`. Default selection prefers the first
non-FAKE fixture so the rich one is picked up automatically.

### Generating a new fixture from OHLCV

```python
docker compose exec backend python -c "
import json
from datetime import datetime, time, timezone
from pathlib import Path
from backend.db.duckdb_engine import query_iceberg_table

TICKERS = ['RELIANCE.NS', 'TCS.NS', ...]
ph = ','.join(f\"'{t}'\" for t in TICKERS)
rows = query_iceberg_table(
    'stocks.ohlcv',
    f'SELECT ticker, date, close, volume FROM ohlcv WHERE ticker IN ({ph}) AND date >= ? ORDER BY date, ticker',
    ['2025-01-01'],
)
out = Path('/app/backend/algo/tests/fixtures/<your-name>.jsonl')
with out.open('w') as f:
    for r in rows:
        dt = datetime.combine(r['date'], time(9, 30), tzinfo=timezone.utc)
        f.write(json.dumps({
            'ticker': r['ticker'],
            'ts_ns': int(dt.timestamp() * 1_000_000_000),
            'ltp': float(r['close']),
            'volume': int(r['volume'] or 0),
        }) + '\n')
"
```

The frontend dropdown picks up the new fixture on the next
SWR revalidation (60-second dedup).

## Indicator engine in paper

`PaperRuntime` keeps a per-ticker rolling bar history
(`self._bars_by_ticker`) and re-runs `compute_indicators` on
every closed bar. Strategy gets the same SMA / golden_cross
features as the backtest. KeyErrors during warmup (before SMA
windows fill) are caught — that bar no-ops gracefully.

Stream's `Bar` uses `float`; backtest indicators expect `Decimal`.
A small adapter shims a `BarData` out of the Stream Bar before
pushing into the history list.

## set_target_weight semantics

Mirror of backtest. `target_qty = floor(weight × current_equity
/ last_price)`; diff vs current qty drives BUY or SELL.

```
current_equity = initial_capital + realised_pnl
                 (paper doesn't yet track unrealised the way
                  backtest's mark-to-market does)
```

## Kill switch

`KillSwitchRepo` writes both PG (`algo.kill_switch`, durable)
and Redis (`algo:kill:{user_id}`, sub-ms reads). The runtime
checks Redis only — graceful fallback to False if Redis
unavailable so the cache being down never falsely blocks
trading.

When armed, every signal in the runtime emits a
`signal_rejected` event with `reason: kill_switch` instead of
firing through to the broker.

End-to-end verification (Golden Cross v1 against
`ticks_indian_universe.jsonl`):

| Run | kill_switch | signal_generated | signal_rejected | order_filled |
|---|---|---|---|---|
| 1 | OFF | 16 | 0 | 16 (8× WIPRO, 5× COALINDIA, 3× INFY) |
| 2 | ARMED | 33 | 33 | 0 |

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/algo/paper/runs` | Start a run (returns 201 + supervisor row) |
| DELETE | `/v1/algo/paper/runs/{strategy_id}` | Cancel a running task |
| GET | `/v1/algo/paper/runs` | List active runs for the caller |
| GET | `/v1/algo/paper/events?limit=100` | Recent paper events for the caller |
| GET | `/v1/algo/paper/fixtures` | List replay fixtures with summary stats |
| GET | `/v1/algo/kill-switch` | Read kill switch state |
| POST | `/v1/algo/kill-switch/arm` | Arm with optional reason |
| POST | `/v1/algo/kill-switch/disarm` | Disarm |

All gated `pro_or_superuser`.

## What's NOT in v1

- Live Kite WebSocket multiplexing (one WS per user → fan out
  to multiple strategy instances). Slice 6 ships the
  `LiveTickSource` adapter; the supervisor v2 will pool WS
  connections.
- Reconciliation loop (paper positions vs broker positions).
  Spec § 7.4 calls this "scaffold-in-place" for v2 anyway.
- Restart-replay rebuild of `algo.risk_state` from
  `algo.events` — helper exists in
  `backend/algo/paper/replay_rebuilder.py` but isn't auto-wired
  to backend startup yet.
