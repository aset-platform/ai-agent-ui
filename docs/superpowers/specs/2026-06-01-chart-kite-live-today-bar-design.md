# Live Kite Today-Bar on Stock Analysis Chart — Design

**Date:** 2026-06-01
**Status:** Draft (pending user review)
**Scope:** During NSE market hours, the stock-analysis chart at `/analytics/analysis` should overlay today's running OHLC + volume from the user's linked Kite account onto the existing yfinance-sourced OHLCV series. On-the-fly only — no Iceberg writes. End-of-day, the daily yfinance pipeline writes the canonical bar and reconciliation is automatic.

---

## 1. Goal

Give traders a market-hours chart that reflects what their broker (Kite) sees, instead of yfinance's snapshot which can lag and is sometimes stale. The Kite overlay applies only to the **last (today's) candle** — earlier history stays yfinance-sourced. Outside market hours, the existing yfinance flow runs unchanged.

## 2. Non-goals

- No tick-level streaming / WebSocket push. SWR polling at 30s is sufficient for a daily-candle chart.
- No Kite data for any history older than today.
- No Iceberg writes from Kite. No persistence whatsoever.
- No platform-wide Kite account. Only users who have linked their own Kite (`algo.broker_credentials` row + valid access_token) get the overlay.
- No support for US tickers (Kite is NSE/BSE only).
- No quote batching across watchlists/dashboards. Single-ticker call per chart load; signature accepts a list to keep batching trivially extensible later.
- No frontend changes to `StockChart.tsx`, `chartIndicators`, indicators dropdown, or OHLCV aggregation.

## 3. Architecture

Additive overlay on the existing `/v1/dashboard/chart/ohlcv` route. No new endpoints, no schema migration, no new tables.

```
GET /v1/dashboard/chart/ohlcv?ticker=RELIANCE.NS
         │
         ▼
  ┌─ load_yfinance_ohlcv(ticker)  ── existing path, unchanged
  │
  ├─ live overlay gate (all three must hold):
  │   • _is_market_open()                — Mon–Fri 09:00–15:30 IST
  │   • user has Kite creds + token       — algo.broker_credentials row
  │   • detect_market(ticker) == "NSE"   — Indian ticker
  │
  ├─ _try_kite_quote(user, ticker) → quote | None  (silent fallback)
  ├─ _splice_today_bar(df, quote, today_ist)
  │
  └─ response: OHLCVResponse(data=[…], is_live=True)
     cache key:  cache:chart:ohlcv:{user_id}:{ticker}  (per-user, 30s)
                 OR cache:chart:ohlcv:{ticker}          (shared, 300s)
                 depending on whether overlay was applied
```

Frontend: SWR `refreshInterval = ohlcv.is_live ? 30_000 : 0`. LIVE pill rendered next to the ticker symbol when `is_live === true`.

## 4. Backend changes

### 4.1 `KiteClient.quote()` — new method (`backend/algo/broker/kite_client.py`)

```python
def quote(
    self,
    tickers: list[tuple[str, int]],
) -> dict[str, dict]:
    """Fetch live OHLC + LTP + volume for one or more NSE tickers.

    Calls pykiteconnect ``kc.quote(["NSE:RELIANCE", …])`` and
    returns ``{ticker: {open, high, low, close, last_price,
    volume, last_trade_time}}``.

    Parameters
    ----------
    tickers : list[tuple[str, int]]
        ``(ticker, instrument_token)`` pairs. The token is unused
        by ``kc.quote()`` (which keys on symbol strings), but the
        signature mirrors ``fetch_intraday_historical`` so callers
        resolve the same way via ``InstrumentsRepo``.

    Returns
    -------
    dict[str, dict]
        Keyed by our internal ticker (``RELIANCE.NS``, not
        ``NSE:RELIANCE``). Values carry ``open``, ``high``,
        ``low``, ``close``, ``last_price``, ``volume``, and
        ``last_trade_time`` (datetime).

    Raises
    ------
    RuntimeError
        No access_token set; caller must complete OAuth.
    Exception
        Any Kite SDK error is re-raised — caller silently falls
        back to yfinance.
    """
    if self._access_token is None:
        raise RuntimeError("quote requires an access_token")
    self._hist_throttle()    # reuse 3-req/sec throttle
    keys = [f"NSE:{_strip_ns(t)}" for t, _ in tickers]
    raw = self._kc.quote(keys)
    out: dict[str, dict] = {}
    for (ticker, _), key in zip(tickers, keys):
        row = raw.get(key)
        if not row:
            continue
        ohlc = row.get("ohlc") or {}
        out[ticker] = {
            "open": float(ohlc.get("open", 0)),
            "high": float(ohlc.get("high", 0)),
            "low": float(ohlc.get("low", 0)),
            "close": float(ohlc.get("close", 0)),
            "last_price": float(row.get("last_price", 0)),
            "volume": int(row.get("volume", 0) or 0),
            "last_trade_time": row.get("last_trade_time"),
        }
    return out
```

`_strip_ns(ticker)` removes the `.NS` suffix used internally and returns the bare NSE symbol expected by `kc.quote()`.

### 4.2 Live overlay block in `get_chart_ohlcv` (`backend/dashboard_routes.py`)

After the existing `df = stock_repo.get_ohlcv(t_upper)` + dedup block, insert:

```python
is_live = False
today_ist = datetime.now(IST).date()
if _is_market_open() and detect_market(t_upper) == "NSE":
    quote = await _try_kite_quote(user, t_upper)
    if quote is not None:
        df = _splice_today_bar(df, quote, today_ist)
        is_live = True

cache_key = (
    f"cache:chart:ohlcv:{user.user_id}:{t_upper}"
    if is_live
    else f"cache:chart:ohlcv:{t_upper}"
)
ttl = TTL_MARKET_LIVE if is_live else TTL_STABLE
```

And the response builder gains `is_live=is_live`. The cache write uses `ttl` instead of the hardcoded `TTL_STABLE`.

### 4.3 `_try_kite_quote(user, ticker)` helper

New module `backend/dashboard_kite_overlay.py` (kept out of `dashboard_routes.py` to avoid further growing that already-large file):

```python
async def _try_kite_quote(
    user: UserContext,
    ticker: str,
) -> dict | None:
    """Fetch today's running bar from Kite. Silent on any error.

    Returns ``None`` whenever any of the following is true:
    - user has no broker_credentials row
    - access_token is missing or expired
    - InstrumentsRepo has no token for the ticker
    - Kite SDK raises (timeout, auth, rate limit, anything)

    Logs a single WARNING per (user_id, ticker) per 60s on
    Kite SDK errors to avoid log spam under outage.
    """
```

Internals:
1. Look up creds via `BrokerCredentialsRepo.get_by_user_id(session, user.user_id)`. None → return None.
2. Resolve instrument_token via `InstrumentsRepo.get_tokens_for_tickers(session, [ticker])`. Missing → return None.
3. Instantiate `KiteClient(user_id=user.user_id)` (auto-resolves dry_run via Redis per `feedback_dry_run_redis_first`; we always want live data here, but the constructor handles it).
4. Wrap `client.quote([(ticker, instrument_token)])` in `asyncio.to_thread(...)` (Kite SDK is sync) with a 5s timeout.
5. Return the per-ticker dict from the quote response, or `None` on any exception.

Exception handling is `except Exception` (not bare; per §4.2 #13). Logged at WARNING with `exc_info=False` and rate-limited via an in-process LRU keyed by `(user_id, ticker, minute_bucket)`.

### 4.4 `_splice_today_bar(df, quote, today)` helper

```python
def _splice_today_bar(
    df: pd.DataFrame,
    quote: dict,
    today: date,
) -> pd.DataFrame:
    """Overlay today's running OHLCV from a Kite quote.

    If df.iloc[-1].date == today (yfinance already has a partial
    bar): overwrite open/high/low/close/volume with the quote.
    Otherwise (yfinance hasn't refreshed today yet, common at
    market open): append a new row.

    close is set to ``quote["last_price"]`` (the running close);
    ``quote["close"]`` from Kite is the previous-day close in
    pre-market and the running close after open — we pick
    last_price for unambiguous semantics.
    """
```

The pandas mutation is row-level; the function returns a new DataFrame to keep the caller side-effect free. Uses `df.copy()` to avoid mutating shared cache state.

### 4.5 `OHLCVResponse.is_live` field (`backend/dashboard_models.py`)

```python
class OHLCVResponse(BaseModel):
    ticker: str
    data: list[OHLCVPoint] = Field(default_factory=list)
    is_live: bool = False    # ← new
```

Default `False` keeps the field optional from old cached payloads (Pydantic v2 will deserialize a missing key to the default).

### 4.6 Cache TTL constant (per CLAUDE.md §5.13)

Add to the constants block in `dashboard_routes.py` (or wherever `TTL_VOLATILE/STABLE/ADMIN` live):

```python
TTL_MARKET_LIVE = 30  # per-user live overlay during market hours
```

### 4.7 Cache invalidation

The per-ticker refresh callback at `backend/dashboard_routes.py:1516` currently passes a literal key `f"cache:chart:ohlcv:{t}"` to `cache.invalidate_exact(...)`. That clears only the shared (non-Kite) key; per-user keys would remain stale after the post-close yfinance write.

Change that one entry to a glob pattern so both shared and per-user keys are cleared by the same call:

```python
# backend/dashboard_routes.py:1516 — was: f"cache:chart:ohlcv:{t}"
f"cache:chart:ohlcv:*{t}",   # matches:
                              #   cache:chart:ohlcv:{t}                (shared)
                              #   cache:chart:ohlcv:{user_id}:{t}      (per-user)
```

The `*` prefix uses `cache.invalidate(pattern)` (SCAN-based glob, line 168 of `backend/cache.py`) automatically because the existing loop at line 1521 picks the glob branch whenever the pattern contains `*`.

`backend/cache_warmup.py:135` writes the shared key only; per-user warm-up is not needed (per-user keys are short-lived and only relevant during an active session). No change there.

No `_CACHE_INVALIDATION_MAP` table exists in the repo — the per-ticker refresh callback is the only invalidator for `cache:chart:ohlcv:*`. Updating the single line above is the entire change.

### 4.8 Imports + shared market-hours helper

To avoid `dashboard_routes` ↔ `market_routes` coupling, move `_is_market_open` (currently at `backend/market_routes.py:45`) and the `IST` timezone constant to a new shared module `backend/market_hours.py`:

```python
# backend/market_hours.py
from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def is_market_open() -> bool:
    """True if IST is Mon-Fri 09:00-15:30."""
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    open_t = now.replace(hour=9, minute=0, second=0, microsecond=0)
    close_t = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return open_t <= now <= close_t
```

Make `_is_market_open` in `market_routes.py` a one-line re-export of the new public name for back-compat. `dashboard_routes.py` and `dashboard_kite_overlay.py` import directly from `market_hours`.

## 5. Frontend changes

### 5.1 `OHLCVResponse` shared type (`frontend/lib/types.ts`)

```ts
export interface OHLCVResponse {
  ticker: string;
  data: OHLCVPoint[];
  is_live?: boolean;
}
```

Optional, matches Pydantic's default.

### 5.2 SWR refresh gating (`frontend/app/(authenticated)/analytics/analysis/page.tsx`)

The existing OHLCV fetch hook becomes live-aware:

```ts
const { data: ohlcv } = useSWR<OHLCVResponse>(
  ticker ? `${API_URL}/dashboard/chart/ohlcv?ticker=${ticker}` : null,
  apiFetch,
  {
    revalidateOnFocus: false,
    dedupingInterval: 120_000,
    refreshInterval: ohlcv?.is_live ? 30_000 : 0,
  },
);
```

When `is_live` flips false (market close, ticker change, or any time SWR refetches and the backend returns false), `refreshInterval` drops to `0` and SWR stops polling.

### 5.3 LIVE pill component (chart header)

Render conditionally inside the existing chart header, immediately after the ticker symbol:

```tsx
{ohlcv?.is_live && (
  <span
    data-testid="stock-analysis-live-pill"
    className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
    title="Live from Kite — updated every 30s during market hours"
  >
    <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
    LIVE
  </span>
)}
```

The `data-testid` allows a future E2E smoke without bloating selectors.

## 6. Data flow

```
User opens /analytics/analysis?ticker=RELIANCE.NS  (10:42 IST Mon)
         │
         ▼
GET /v1/dashboard/chart/ohlcv?ticker=RELIANCE.NS
         │
         ├─ cache:chart:ohlcv:{user_id}:RELIANCE.NS  → MISS
         ├─ stock_repo.get_ohlcv("RELIANCE.NS")  → yfinance base
         ├─ _is_market_open() = True; user has Kite; detect_market = "NSE"
         ├─ _try_kite_quote(user, "RELIANCE.NS")
         │      → {"RELIANCE.NS": {open: 2870.0, high: 2895.3, low: 2861.5,
         │                          close: 2882.1, last_price: 2884.4,
         │                          volume: 1_842_900, last_trade_time: …}}
         ├─ _splice_today_bar(df, quote, 2026-06-01)
         │      df.iloc[-1] = (2026-06-01, 2870.0, 2895.3, 2861.5, 2884.4, 1842900)
         ├─ is_live = True
         ├─ cache.set("cache:chart:ohlcv:{user_id}:RELIANCE.NS", json, ttl=30)
         └─ OHLCVResponse(data=[…], is_live=True)
         │
         ▼
Frontend SWR  → refreshInterval = 30_000
StockChart renders, today's last candle = Kite running bar
LIVE pill appears next to ticker symbol
         │
         │  …30s later…
         ▼
SWR refetches → cache HIT or fresh Kite quote → candle updates in place

15:30 IST passes → next refetch:
         _is_market_open()=False → overlay block skipped
         is_live=False → SWR refreshInterval=0 → pill disappears
         cache key = shared cache:chart:ohlcv:RELIANCE.NS, TTL 300s
```

## 7. Error & edge cases

| Case | Behavior |
|---|---|
| User has no `algo.broker_credentials` row | Skip overlay; shared yfinance cache; `is_live=false`. No log. |
| Access_token expired/revoked | `_try_kite_quote` catches Kite SDK error, logs WARNING rate-limited (1/60s/user), returns None. Silent fallback. |
| Kite network timeout (5s) | Same as above. Chart still loads with yfinance base. |
| Ticker missing from `InstrumentsRepo` | Returns None before Kite call. Logged at DEBUG only. |
| `detect_market(ticker) != "NSE"` (US ticker, BSE-only ETF) | Overlay block skipped. `is_live=false`. |
| Pre-market window (09:00–09:14 IST) | Market open per `_is_market_open()`, but `kc.quote()` last_trade_time may be from yesterday. We trust any successful quote — chart shows yesterday's close in the today slot until first tick. Harmless. |
| Daily yfinance pipeline writes today's true bar after close | `cache.invalidate("cache:chart:ohlcv:*")` glob clears both shared and per-user keys. Next load shows canonical yfinance bar. |
| User switches ticker mid-session (NSE → NASDAQ) | New request → `detect_market` returns non-NSE → no overlay → `is_live=false` → SWR refresh drops to 0. Pill disappears. |
| Two concurrent users hit the same ticker | Per-user cache keys avoid quote contention. Each pays one Kite call per 30s. |
| Kite rate-limit hit (>3 req/s class throttle) | `_hist_throttle` blocks; if blocked >5s, `asyncio.to_thread` timeout fires; fallback to yfinance. Logged WARNING. |

## 8. Testing

### 8.1 `KiteClient.quote()` unit test (`backend/algo/broker/tests/test_kite_client_quote.py`)

- **Happy path**: mock pykiteconnect `kc.quote()` to return a canonical NSE response shape; assert mapping into our internal dict (`open`/`high`/`low`/`close`/`last_price`/`volume`/`last_trade_time` keys; `.NS`-suffixed internal ticker as the outer key).
- **Throttle**: assert `_hist_throttle` is called before the SDK call.
- **No access_token**: assert raises `RuntimeError`.
- **Empty response**: `kc.quote()` returns `{}` → wrapper returns `{}`.

### 8.2 `_try_kite_quote` test

- **No creds**: returns `None`, no Kite SDK call attempted.
- **Kite raises**: returns `None`, WARNING logged once.
- **Success**: returns the per-ticker dict.

### 8.3 `_splice_today_bar` test

- **df has today**: overwrite that row.
- **df missing today**: append a new row sorted by date.
- **df empty**: append; result has length 1.

### 8.4 Route integration test (`tests/backend/test_dashboard_routes.py::TestChartOHLCV`)

- **Live path**: market open + user has Kite + ticker is `RELIANCE.NS` → `is_live=true`; cache `set` called with `ttl=30` and per-user key.
- **Closed market**: `_is_market_open()=False` → `is_live=false`; cache `set` with `ttl=300` and shared key.
- **No Kite creds**: `_try_kite_quote` returns None → `is_live=false`.
- **Non-NSE ticker**: `detect_market="NASDAQ"` → overlay skipped; `is_live=false`.
- **Kite SDK error**: `_try_kite_quote` returns None → falls back; `is_live=false`.

### 8.5 Frontend smoke (manual)

Open `/analytics/analysis?ticker=RELIANCE.NS` during NSE hours with a linked Kite account:
1. Chart loads; today's candle reflects current Kite OHLC + volume.
2. LIVE pill renders next to ticker symbol, dot pulses.
3. Within 30s, candle updates in place; Network tab shows a polled `/chart/ohlcv` call.
4. Switch to `AAPL` (US ticker) → pill disappears; no polling.
5. Wait until 15:30 IST → next poll returns `is_live=false`; pill disappears; polling stops.
6. Reload after market close → today's candle = canonical yfinance bar.

No E2E spec (per `feedback_e2e_single_worker` — visual smoke covers it for incremental polish).

## 9. Performance

- **Backend**: one extra Kite `quote()` call per ticker per 30s per linked-Kite user during market hours. Bounded by `_hist_throttle` (3 req/sec). For 100 concurrent users all watching different tickers, ~3.3 calls/sec — within budget. For 100 users all watching the SAME ticker, each pays their own quote since the cache is per-user; if this becomes a concern, a single shared `cache:chart:ohlcv:kite:{ticker}` overlay cache with 30s TTL could be added later.
- **Network**: `+1 boolean` per response (`is_live`). Negligible.
- **Frontend**: 30s SWR polling during market hours only; idle outside. No new bundle weight (LIVE pill is inline JSX, no new component file).
- No impact on §5.15 perf budgets — `/analytics/analysis` is already in the ≥75 bucket.

## 10. Rollout

1. Merge to `dev`. Squash per §4.4 #27.
2. After merge: `./run.sh restart backend` (new `is_live` field on `OHLCVResponse` — §6.2). `redis-cli FLUSHALL` (cache key shape changed).
3. Smoke test: open `/analytics/analysis?ticker=RELIANCE.NS` during NSE hours, confirm pill + polling. Outside hours, confirm exact prior behavior.
4. Watch for `WARNING ... kite quote failed` spikes in backend logs — indicates expired tokens / Kite outage.

## 11. References

- `CLAUDE.md` §5.13 (Redis caching TTL constants), §5.16 (algo / dry_run resolution), §6.2 (backend restart triggers), §5.15 (perf budgets).
- Existing files: `backend/algo/broker/kite_client.py:433` (`fetch_intraday_historical`), `backend/algo/broker/credentials_repo.py` (Kite creds CRUD), `backend/market_routes.py:45` (`_is_market_open`), `backend/dashboard_routes.py:1085` (`get_chart_ohlcv`), `backend/market_utils.py` (`detect_market`).
- Memories: `feedback_dry_run_redis_first`, `feedback_kite_limit_only_real_orders` (unrelated — order placement, not quote), `feedback_admin_merge_through_red_ci`.

## 12. Out of scope / future

- WebSocket push for sub-30s freshness (would multiplex into the live tick stream — significant scope).
- Quote batching across watchlist / dashboard widgets to amortize Kite calls.
- Shared cross-user overlay cache (`cache:chart:ohlcv:kite:{ticker}`) if many users watch the same ticker.
- Extending overlay to BSE tickers (Kite supports both; we currently only resolve NSE instrument tokens).
- Visual styling: a thin colored border on the today candle to signal it's live vs historical. Defer; the LIVE pill is enough for v1.
