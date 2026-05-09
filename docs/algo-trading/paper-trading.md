# Paper trading

Tick-driven runtime that runs a strategy against a stream of
ticks (replay-fixture or live Kite WS in v2), gating every
signal through the same `RiskEngine` the backtest uses, filling
at the current tick's LTP via `PaperBroker`.

## Architecture

```
TickSource          # ReplayTickSource (.jsonl fixture)
                    # LiveTickSource (v1 per-strategy KiteTicker)
                    # LiveWsTickSource (v2 — reads from KiteWsMultiplexer)
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

## Available sources (v2)

`POST /v1/algo/paper/runs` accepts a `source` field:

| Value | Description |
|---|---|
| `"replay"` (default) | JSONL fixture; `fixture_path` required |
| `"live-ws"` | Streams from the user's `KiteWsMultiplexer`; Kite must be connected |

### live-ws mode

When `source="live-ws"`:
1. Kite credentials (api_key + access_token) are loaded from PG.
2. Tickers are resolved from the user's portfolio holdings + watchlist.
3. Instrument tokens are looked up from `algo.instruments`.
4. `KiteWsMultiplexer` is get-or-created for the user (process-local).
5. The strategy subscribes; a `LiveWsTickSource` adapter is returned
   that reads from the strategy's per-queue and unsubscribes on stop.

**Multiplexer lifecycle** (`backend/algo/broker/ws_multiplexer.py`):
- One WS per user; multiple strategies share it via ref-counted tokens.
- Exponential-backoff reconnect (1s → 60s cap).
- Gap-fill on reconnect: pulls Kite historical 1m bars for windows
  up to 1h (longer gaps emit `ws_gap_too_large` + abandon).
- Bounded queues (1 000); overflow drops oldest + logs `ws_backpressure_drop`.
- Process shutdown calls `ws_registry.shutdown_all()` via FastAPI lifespan.

### WS event types

New events emitted to `algo.events` with `mode="live-ws"`:

| Type | Payload keys |
|---|---|
| `ws_connected` | `token_count` |
| `ws_disconnected` | `code`, `reason` |
| `ws_gap_filled` | `token`, `ticker`, `missing_s`, `ticks_replayed` |
| `ws_gap_too_large` | `token`, `ticker`, `missing_s` |
| `ws_backpressure_drop` | `strategy_id`, `token` |

## What's NOT in v1

- Reconciliation loop (paper positions vs broker positions).
  Spec § 7.4 calls this "scaffold-in-place" for v2 anyway.
- Live order placement (V2-5).
- Walk-forward harness (V2-2).
