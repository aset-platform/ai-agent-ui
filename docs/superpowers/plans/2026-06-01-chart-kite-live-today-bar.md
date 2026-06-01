# Live Kite Today-Bar Overlay — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** During NSE market hours (Mon–Fri 09:00–15:30 IST), overlay today's running OHLC + volume from the user's linked Kite account onto the last candle of `/v1/dashboard/chart/ohlcv` — on-the-fly, no Iceberg writes. Surface a LIVE pill on the chart and have the frontend SWR-poll every 30s while live. End-of-day yfinance pipeline reconciles automatically.

**Architecture:** Add a `KiteClient.quote()` wrapper and a new `backend/dashboard_kite_overlay.py` module (`_try_kite_quote` + `_splice_today_bar`). Extend `get_chart_ohlcv` with a 3-gate overlay block (market open ∧ user has Kite ∧ Indian ticker) and a per-user cache key with 30s TTL when live. Add `is_live: bool` to the response and a LIVE pill + SWR `refreshInterval` on the analysis page.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, pykiteconnect SDK, Next.js 16, SWR, TradingView lightweight-charts.

**Branch:** `feature/chart-kite-live-today-bar` (already cut; spec doc committed as `8ffe1fe`).

**Spec:** `docs/superpowers/specs/2026-06-01-chart-kite-live-today-bar-design.md`

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `backend/market_hours.py` | Create | Shared `IST` + `is_market_open()` (extracted from `market_routes.py`). |
| `backend/market_routes.py` | Modify | Re-export `_is_market_open = is_market_open` from new shared module. |
| `backend/cache.py` | Modify | Add `TTL_MARKET_LIVE = 30` constant. |
| `backend/algo/broker/kite_client.py` | Modify | Add `quote()` method (wraps `pykiteconnect.KiteConnect.quote()`). |
| `backend/algo/broker/tests/test_kite_client_quote.py` | Create | Unit tests for the new `quote()` method. |
| `backend/dashboard_kite_overlay.py` | Create | `_try_kite_quote` + `_splice_today_bar` helpers (keeps the overlay logic out of the already-large `dashboard_routes.py`). |
| `tests/backend/test_dashboard_kite_overlay.py` | Create | Unit tests for the overlay helpers. |
| `backend/dashboard_models.py` | Modify | `OHLCVResponse.is_live: bool = False`. |
| `backend/dashboard_routes.py` | Modify | Wire overlay block + cache key/TTL switch + update invalidation glob at line ~1516. |
| `tests/backend/test_dashboard_routes.py` | Modify | Extend `TestChartOHLCV` (or add it) with 5 overlay scenarios. |
| `frontend/lib/types.ts` | Modify | `OHLCVResponse.is_live?: boolean`. |
| `frontend/app/(authenticated)/analytics/analysis/page.tsx` | Modify | SWR `refreshInterval` gating + LIVE pill in chart header. |

---

## Task 1: Extract shared market-hours helper

**Files:**
- Create: `backend/market_hours.py`
- Modify: `backend/market_routes.py` (lines 29 + 45)

- [ ] **Step 1.1: Write the failing test**

Create `tests/backend/test_market_hours.py`:

```python
"""Tests for backend.market_hours: shared is_market_open helper."""

from datetime import datetime
from unittest.mock import patch

import pytest

from market_hours import IST, is_market_open


def _make_now(year, month, day, hour, minute):
    return datetime(year, month, day, hour, minute, tzinfo=IST)


class TestIsMarketOpen:
    @pytest.mark.parametrize(
        "hour,minute,expected",
        [
            (8, 59, False),   # pre-open
            (9, 0, True),     # open edge
            (10, 30, True),   # mid-session
            (15, 30, True),   # close edge
            (15, 31, False),  # post-close
        ],
    )
    def test_weekday_window(self, hour, minute, expected):
        # 2026-06-01 is a Monday.
        with patch("market_hours.datetime") as mock_dt:
            mock_dt.now.return_value = _make_now(
                2026, 6, 1, hour, minute,
            )
            assert is_market_open() is expected

    def test_saturday_closed(self):
        # 2026-06-06 is a Saturday.
        with patch("market_hours.datetime") as mock_dt:
            mock_dt.now.return_value = _make_now(
                2026, 6, 6, 10, 0,
            )
            assert is_market_open() is False

    def test_sunday_closed(self):
        # 2026-06-07 is a Sunday.
        with patch("market_hours.datetime") as mock_dt:
            mock_dt.now.return_value = _make_now(
                2026, 6, 7, 10, 0,
            )
            assert is_market_open() is False
```

- [ ] **Step 1.2: Run test to verify it fails**

```bash
PYTHONPATH=.:backend python -m pytest tests/backend/test_market_hours.py -v
```

Expected: 7 collection errors with `ModuleNotFoundError: No module named 'market_hours'`.

- [ ] **Step 1.3: Create the module**

Create `backend/market_hours.py`:

```python
"""Shared market-hours utilities.

Single source of truth for the NSE session window so multiple
routes (``market_routes``, ``dashboard_routes``, future overlay
modules) all agree on what "live" means.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def is_market_open() -> bool:
    """True if IST is Mon-Fri 09:00-15:30.

    NSE cash session is 09:15-15:30, but we widen the lower
    bound to 09:00 to surface pre-open auction data (which
    Kite ``quote()`` reflects).
    """
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    open_t = now.replace(
        hour=9, minute=0, second=0, microsecond=0,
    )
    close_t = now.replace(
        hour=15, minute=30, second=0, microsecond=0,
    )
    return open_t <= now <= close_t
```

- [ ] **Step 1.4: Run test to verify it passes**

```bash
PYTHONPATH=.:backend python -m pytest tests/backend/test_market_hours.py -v
```

Expected: 7 passed.

- [ ] **Step 1.5: Update market_routes.py to re-export**

Edit `backend/market_routes.py`. Replace lines 29 + 45–54 with imports + re-export:

Find this block (around line 28–54):

```python
# (pytz import + IST definition + _is_market_open definition)
```

Replace the local `IST = pytz.timezone("Asia/Kolkata")` (around line 29) with:

```python
from market_hours import IST, is_market_open as _is_market_open  # noqa: F401
```

And delete the entire `def _is_market_open() -> bool: …` block (around lines 45–54). The `_is_market_open` name remains importable from `market_routes` via the re-export, so existing callers (line 240, 396, 426 in the same file, and any external imports) keep working.

Also remove `import pytz` if it's no longer used after this change (check with `grep -n "pytz" backend/market_routes.py`).

- [ ] **Step 1.6: Run the existing market_routes tests to confirm no regression**

```bash
PYTHONPATH=.:backend python -m pytest tests/backend/test_market_routes.py -v 2>&1 | tail -15
```

Expected: all previously-passing tests still pass. (If `test_market_routes.py` doesn't exist or none of its tests touch `_is_market_open`, that's fine — the new `test_market_hours.py` covers the behavior.)

- [ ] **Step 1.7: Lint**

```bash
black backend/market_hours.py backend/market_routes.py tests/backend/test_market_hours.py
isort backend/market_hours.py backend/market_routes.py tests/backend/test_market_hours.py --profile black
flake8 backend/market_hours.py backend/market_routes.py tests/backend/test_market_hours.py
```

Expected: no output.

- [ ] **Step 1.8: Commit**

```bash
git add backend/market_hours.py backend/market_routes.py tests/backend/test_market_hours.py
git commit -m "$(cat <<'EOF'
refactor(market-hours): extract IST + is_market_open to shared module

Single source of truth for the NSE session window so future
overlay modules (dashboard chart Kite overlay) can import without
coupling to market_routes. Backwards-compatible: market_routes
re-exports the prior _is_market_open name.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 2: Add TTL_MARKET_LIVE constant

**Files:**
- Modify: `backend/cache.py` (line 36)

- [ ] **Step 2.1: Add constant**

Edit `backend/cache.py`. Find:

```python
TTL_VOLATILE = 60  # watchlist, llm-usage
TTL_STABLE = 300  # charts, insights, registry
TTL_ADMIN = 30  # tier-health, metrics
```

Add immediately after (line 37 will be the new line):

```python
TTL_MARKET_LIVE = 30  # per-user chart overlay during NSE market hours
```

- [ ] **Step 2.2: Confirm importability**

```bash
PYTHONPATH=.:backend python -c "from cache import TTL_MARKET_LIVE; print(TTL_MARKET_LIVE)"
```

Expected: `30`.

- [ ] **Step 2.3: Lint**

```bash
black backend/cache.py && isort backend/cache.py --profile black && flake8 backend/cache.py
```

Expected: no output beyond the existing pre-existing warnings (if any). Confirm no new flake8 issues by diffing against base.

- [ ] **Step 2.4: Commit**

```bash
git add backend/cache.py
git commit -m "$(cat <<'EOF'
feat(cache): add TTL_MARKET_LIVE = 30 for chart Kite overlay

Per-user keys carrying the live Kite-spliced today bar expire
every 30s during NSE market hours. Mirrors the cadence used by
/v1/market indices for the same reason.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 3: KiteClient.quote() wrapper

**Files:**
- Modify: `backend/algo/broker/kite_client.py`
- Create: `backend/algo/broker/tests/test_kite_client_quote.py`

- [ ] **Step 3.1: Write the failing test**

Create `backend/algo/broker/tests/test_kite_client_quote.py`:

```python
"""Tests for KiteClient.quote() — live OHLC + LTP + volume fetch."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from backend.algo.broker.kite_client import KiteClient


def _make_client(access_token="tok"):
    client = KiteClient(
        api_key="k",
        access_token=access_token,
    )
    client._kc = MagicMock()
    return client


class TestKiteClientQuote:
    def test_happy_path_single_ticker(self):
        client = _make_client()
        ltt = datetime(
            2026, 6, 1, 10, 42, 30, tzinfo=timezone.utc,
        )
        client._kc.quote.return_value = {
            "NSE:RELIANCE": {
                "ohlc": {
                    "open": 2870.0,
                    "high": 2895.3,
                    "low": 2861.5,
                    "close": 2882.1,
                },
                "last_price": 2884.4,
                "volume": 1_842_900,
                "last_trade_time": ltt,
            }
        }
        with patch.object(client, "_hist_throttle") as throttle:
            out = client.quote(
                [("RELIANCE.NS", 738561)],
            )
        throttle.assert_called_once()
        client._kc.quote.assert_called_once_with(
            ["NSE:RELIANCE"],
        )
        assert "RELIANCE.NS" in out
        bar = out["RELIANCE.NS"]
        assert bar["open"] == 2870.0
        assert bar["high"] == 2895.3
        assert bar["low"] == 2861.5
        assert bar["close"] == 2882.1
        assert bar["last_price"] == 2884.4
        assert bar["volume"] == 1_842_900
        assert bar["last_trade_time"] == ltt

    def test_strips_ns_suffix_for_kite_key(self):
        client = _make_client()
        client._kc.quote.return_value = {}
        client.quote([("RELIANCE.NS", 738561)])
        client._kc.quote.assert_called_once_with(
            ["NSE:RELIANCE"],
        )

    def test_no_access_token_raises(self):
        client = _make_client(access_token=None)
        with pytest.raises(RuntimeError, match="access_token"):
            client.quote([("RELIANCE.NS", 738561)])

    def test_empty_kite_response_returns_empty_dict(self):
        client = _make_client()
        client._kc.quote.return_value = {}
        out = client.quote([("RELIANCE.NS", 738561)])
        assert out == {}

    def test_missing_ohlc_block_skips_ticker(self):
        client = _make_client()
        client._kc.quote.return_value = {
            "NSE:RELIANCE": {"last_price": 2884.4},
            # ohlc key missing entirely
        }
        out = client.quote([("RELIANCE.NS", 738561)])
        # Missing ohlc → defaults to zeros, but ticker is still
        # in the output dict. Real callers ignore zero bars.
        assert "RELIANCE.NS" in out
        assert out["RELIANCE.NS"]["open"] == 0.0

    def test_batch_two_tickers(self):
        client = _make_client()
        client._kc.quote.return_value = {
            "NSE:RELIANCE": {
                "ohlc": {
                    "open": 2870, "high": 2895,
                    "low": 2861, "close": 2882,
                },
                "last_price": 2884.4,
                "volume": 1_000_000,
                "last_trade_time": None,
            },
            "NSE:INFY": {
                "ohlc": {
                    "open": 1450, "high": 1465,
                    "low": 1442, "close": 1458,
                },
                "last_price": 1460.0,
                "volume": 500_000,
                "last_trade_time": None,
            },
        }
        out = client.quote([
            ("RELIANCE.NS", 738561),
            ("INFY.NS", 408065),
        ])
        client._kc.quote.assert_called_once_with(
            ["NSE:RELIANCE", "NSE:INFY"],
        )
        assert set(out.keys()) == {"RELIANCE.NS", "INFY.NS"}
```

- [ ] **Step 3.2: Run test to verify it fails**

```bash
PYTHONPATH=.:backend python -m pytest backend/algo/broker/tests/test_kite_client_quote.py -v
```

Expected: 6 failures with `AttributeError: 'KiteClient' object has no attribute 'quote'`.

- [ ] **Step 3.3: Implement `quote()` method**

Edit `backend/algo/broker/kite_client.py`. Find the existing `fetch_intraday_historical_window` method (around line 564) — insert the new method directly BEFORE it (so `quote` sits between `fetch_intraday_historical` at line 433 and `fetch_intraday_historical_window` at line 564). The new method:

```python
    def quote(
        self,
        tickers: list[tuple[str, int]],
    ) -> dict[str, dict]:
        """Fetch live OHLC + LTP + volume for one or more NSE tickers.

        Calls pykiteconnect ``kc.quote(["NSE:RELIANCE", …])`` and
        returns ``{ticker: {open, high, low, close, last_price,
        volume, last_trade_time}}`` keyed by our internal ticker
        (e.g. ``"RELIANCE.NS"``, not ``"NSE:RELIANCE"``).

        Parameters
        ----------
        tickers : list[tuple[str, int]]
            ``(ticker, instrument_token)`` pairs. ``instrument_token``
            is unused by ``kc.quote()`` (which keys on symbol strings)
            but the signature mirrors ``fetch_intraday_historical`` so
            callers resolve tokens the same way via ``InstrumentsRepo``.

        Raises
        ------
        RuntimeError
            No access_token set; caller must complete OAuth.
        Exception
            Any Kite SDK error is re-raised — caller silently falls
            back to yfinance.
        """
        if self._access_token is None:
            raise RuntimeError(
                "quote requires an access_token; complete the OAuth"
                " handshake first.",
            )
        self._hist_throttle()
        keys = [
            f"NSE:{t.removesuffix('.NS').removesuffix('.BO')}"
            for t, _ in tickers
        ]
        raw = self._kc.quote(keys)
        out: dict[str, dict] = {}
        for (ticker, _), key in zip(tickers, keys):
            row = raw.get(key)
            if row is None:
                continue
            ohlc = row.get("ohlc") or {}
            out[ticker] = {
                "open": float(ohlc.get("open", 0) or 0),
                "high": float(ohlc.get("high", 0) or 0),
                "low": float(ohlc.get("low", 0) or 0),
                "close": float(ohlc.get("close", 0) or 0),
                "last_price": float(
                    row.get("last_price", 0) or 0,
                ),
                "volume": int(row.get("volume", 0) or 0),
                "last_trade_time": row.get(
                    "last_trade_time",
                ),
            }
        return out
```

- [ ] **Step 3.4: Run test to verify it passes**

```bash
PYTHONPATH=.:backend python -m pytest backend/algo/broker/tests/test_kite_client_quote.py -v
```

Expected: 6 passed.

- [ ] **Step 3.5: Lint**

```bash
black backend/algo/broker/kite_client.py backend/algo/broker/tests/test_kite_client_quote.py
isort backend/algo/broker/kite_client.py backend/algo/broker/tests/test_kite_client_quote.py --profile black
flake8 backend/algo/broker/kite_client.py backend/algo/broker/tests/test_kite_client_quote.py
```

Expected: no NEW issues. Pre-existing flake8 warnings in `kite_client.py` are out of scope — confirm by spot-check.

- [ ] **Step 3.6: Commit**

```bash
git add backend/algo/broker/kite_client.py backend/algo/broker/tests/test_kite_client_quote.py
git commit -m "$(cat <<'EOF'
feat(kite): KiteClient.quote() wrapper for live OHLC + LTP

Wraps pykiteconnect kc.quote() to return a {ticker: bar} dict
keyed by our internal ticker (RELIANCE.NS, not NSE:RELIANCE).
Reuses _hist_throttle (3 req/sec). Used by the upcoming chart
overlay for live today-bar during NSE market hours.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 4: dashboard_kite_overlay helpers

**Files:**
- Create: `backend/dashboard_kite_overlay.py`
- Create: `tests/backend/test_dashboard_kite_overlay.py`

- [ ] **Step 4.1: Write the failing test**

Create `tests/backend/test_dashboard_kite_overlay.py`:

```python
"""Tests for dashboard_kite_overlay: splice today's Kite bar onto yfinance OHLCV."""

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pandas as pd
import pytest

from dashboard_kite_overlay import (
    _splice_today_bar,
    _try_kite_quote,
)


def _quote():
    return {
        "open": 2870.0,
        "high": 2895.3,
        "low": 2861.5,
        "close": 2882.1,
        "last_price": 2884.4,
        "volume": 1_842_900,
        "last_trade_time": datetime(
            2026, 6, 1, 10, 42, tzinfo=timezone.utc,
        ),
    }


def _df_with(today, include_today=True):
    rows = [
        {
            "date": date(2026, 5, 29),
            "open": 2810.0, "high": 2830.0,
            "low": 2805.0, "close": 2825.0,
            "volume": 1_500_000,
        },
    ]
    if include_today:
        rows.append(
            {
                "date": today,
                "open": 2868.0, "high": 2870.0,
                "low": 2867.0, "close": 2869.0,
                "volume": 50_000,
            },
        )
    return pd.DataFrame(rows)


class TestSpliceTodayBar:
    def test_overwrites_existing_today_row(self):
        today = date(2026, 6, 1)
        df = _df_with(today, include_today=True)
        out = _splice_today_bar(df, _quote(), today)
        last = out.iloc[-1]
        assert last["date"] == today
        assert last["open"] == 2870.0
        assert last["high"] == 2895.3
        assert last["low"] == 2861.5
        assert last["close"] == 2884.4   # last_price, not Kite ohlc.close
        assert last["volume"] == 1_842_900
        # Earlier rows untouched.
        assert out.iloc[0]["close"] == 2825.0
        assert len(out) == 2

    def test_appends_when_today_missing(self):
        today = date(2026, 6, 1)
        df = _df_with(today, include_today=False)
        out = _splice_today_bar(df, _quote(), today)
        assert len(out) == 2
        last = out.iloc[-1]
        assert last["date"] == today
        assert last["close"] == 2884.4

    def test_empty_df_appends(self):
        today = date(2026, 6, 1)
        df = pd.DataFrame(
            columns=[
                "date", "open", "high", "low", "close", "volume",
            ],
        )
        out = _splice_today_bar(df, _quote(), today)
        assert len(out) == 1
        assert out.iloc[0]["date"] == today

    def test_returns_new_df_not_mutated_input(self):
        today = date(2026, 6, 1)
        df = _df_with(today, include_today=True)
        original_close = float(df.iloc[-1]["close"])
        _ = _splice_today_bar(df, _quote(), today)
        # Caller's df must not have been mutated.
        assert float(df.iloc[-1]["close"]) == original_close


class TestTryKiteQuote:
    @pytest.mark.asyncio
    async def test_no_creds_returns_none(self):
        user = MagicMock(user_id=uuid4())
        with patch(
            "dashboard_kite_overlay.BrokerCredentialsRepo",
        ) as repo_cls:
            repo = repo_cls.return_value
            repo.load = AsyncMock(return_value=None)
            with patch(
                "dashboard_kite_overlay.disposable_pg_session",
            ) as sess_ctx:
                sess_ctx.return_value.__aenter__.return_value = (
                    AsyncMock()
                )
                out = await _try_kite_quote(
                    user, "RELIANCE.NS",
                )
        assert out is None

    @pytest.mark.asyncio
    async def test_expired_token_returns_none(self):
        user = MagicMock(user_id=uuid4())
        with patch(
            "dashboard_kite_overlay.BrokerCredentialsRepo",
        ) as repo_cls:
            repo = repo_cls.return_value
            repo.load = AsyncMock(
                return_value={
                    "api_key": "k",
                    "access_token": "tok",
                    "access_token_expired": True,
                    "access_token_expires_at": None,
                    "kite_user_id": "ZX1234",
                    "last_login_at": None,
                },
            )
            with patch(
                "dashboard_kite_overlay.disposable_pg_session",
            ) as sess_ctx:
                sess_ctx.return_value.__aenter__.return_value = (
                    AsyncMock()
                )
                out = await _try_kite_quote(
                    user, "RELIANCE.NS",
                )
        assert out is None

    @pytest.mark.asyncio
    async def test_no_instrument_token_returns_none(self):
        user = MagicMock(user_id=uuid4())
        with patch(
            "dashboard_kite_overlay.BrokerCredentialsRepo",
        ) as repo_cls, patch(
            "dashboard_kite_overlay.InstrumentsRepo",
        ) as inst_cls, patch(
            "dashboard_kite_overlay.disposable_pg_session",
        ) as sess_ctx:
            sess_ctx.return_value.__aenter__.return_value = (
                AsyncMock()
            )
            repo_cls.return_value.load = AsyncMock(
                return_value={
                    "api_key": "k",
                    "access_token": "tok",
                    "access_token_expired": False,
                    "access_token_expires_at": None,
                    "kite_user_id": "ZX1234",
                    "last_login_at": None,
                },
            )
            inst_cls.return_value.get_tokens_for_tickers = (
                AsyncMock(return_value={})
            )
            out = await _try_kite_quote(
                user, "UNKNOWN.NS",
            )
        assert out is None

    @pytest.mark.asyncio
    async def test_kite_raises_returns_none(self):
        user = MagicMock(user_id=uuid4())
        with patch(
            "dashboard_kite_overlay.BrokerCredentialsRepo",
        ) as repo_cls, patch(
            "dashboard_kite_overlay.InstrumentsRepo",
        ) as inst_cls, patch(
            "dashboard_kite_overlay.disposable_pg_session",
        ) as sess_ctx, patch(
            "dashboard_kite_overlay.KiteClient",
        ) as kite_cls:
            sess_ctx.return_value.__aenter__.return_value = (
                AsyncMock()
            )
            repo_cls.return_value.load = AsyncMock(
                return_value={
                    "api_key": "k",
                    "access_token": "tok",
                    "access_token_expired": False,
                    "access_token_expires_at": None,
                    "kite_user_id": "ZX1234",
                    "last_login_at": None,
                },
            )
            inst_cls.return_value.get_tokens_for_tickers = (
                AsyncMock(return_value={738561: "RELIANCE.NS"}),
            )[0]
            client = kite_cls.return_value
            client.quote = MagicMock(
                side_effect=RuntimeError("boom"),
            )
            out = await _try_kite_quote(
                user, "RELIANCE.NS",
            )
        assert out is None

    @pytest.mark.asyncio
    async def test_happy_path_returns_quote(self):
        user = MagicMock(user_id=uuid4())
        expected = _quote()
        with patch(
            "dashboard_kite_overlay.BrokerCredentialsRepo",
        ) as repo_cls, patch(
            "dashboard_kite_overlay.InstrumentsRepo",
        ) as inst_cls, patch(
            "dashboard_kite_overlay.disposable_pg_session",
        ) as sess_ctx, patch(
            "dashboard_kite_overlay.KiteClient",
        ) as kite_cls:
            sess_ctx.return_value.__aenter__.return_value = (
                AsyncMock()
            )
            repo_cls.return_value.load = AsyncMock(
                return_value={
                    "api_key": "k",
                    "access_token": "tok",
                    "access_token_expired": False,
                    "access_token_expires_at": None,
                    "kite_user_id": "ZX1234",
                    "last_login_at": None,
                },
            )
            inst_cls.return_value.get_tokens_for_tickers = (
                AsyncMock(return_value={738561: "RELIANCE.NS"})
            )
            client = kite_cls.return_value
            client.quote = MagicMock(
                return_value={"RELIANCE.NS": expected},
            )
            out = await _try_kite_quote(
                user, "RELIANCE.NS",
            )
        assert out == expected
```

- [ ] **Step 4.2: Run test to verify it fails**

```bash
PYTHONPATH=.:backend python -m pytest tests/backend/test_dashboard_kite_overlay.py -v
```

Expected: collection errors with `ModuleNotFoundError: No module named 'dashboard_kite_overlay'`.

- [ ] **Step 4.3: Create the overlay module**

Create `backend/dashboard_kite_overlay.py`:

```python
"""Live Kite OHLC overlay for /v1/dashboard/chart/ohlcv.

Splices today's running OHLC + volume from the user's linked
Kite account onto the last bar of the yfinance-sourced series.
Used only during NSE market hours; outside hours the overlay is
skipped and the existing yfinance flow runs unchanged.

The module is intentionally separate from ``dashboard_routes.py``
to keep that already-large file from growing further and to make
the overlay logic independently testable.
"""

from __future__ import annotations

import logging
from datetime import date
from uuid import UUID

import pandas as pd

from algo.broker.credentials_repo import BrokerCredentialsRepo
from algo.broker.kite_client import KiteClient
from algo.instruments.repo import InstrumentsRepo
from db.session import disposable_pg_session

_logger = logging.getLogger(__name__)


async def _try_kite_quote(
    user, ticker: str,
) -> dict | None:
    """Return today's Kite bar for ``ticker``, or ``None`` on any failure.

    Silent fallback by design — Kite hiccups must never break the
    chart. Logged at WARNING (with ``exc_info=False``) on Kite SDK
    errors so outages are visible without spamming.
    """
    user_id = user.user_id
    try:
        async with disposable_pg_session() as session:
            creds_repo = BrokerCredentialsRepo()
            creds = await creds_repo.load(session, user_id)
            if creds is None:
                return None
            if creds.get("access_token") is None:
                return None
            if creds.get("access_token_expired"):
                return None

            inst_repo = InstrumentsRepo()
            tokens = await inst_repo.get_tokens_for_tickers(
                session, [ticker],
            )
            # tokens is {instrument_token: our_ticker} — reverse
            # lookup to find the token for our ticker.
            instrument_token = next(
                (
                    tok
                    for tok, t in tokens.items()
                    if t == ticker
                ),
                None,
            )
            if instrument_token is None:
                return None

        client = KiteClient(
            api_key=creds["api_key"],
            access_token=creds["access_token"],
            user_id=user_id,
        )
        result = client.quote(
            [(ticker, instrument_token)],
        )
        return result.get(ticker)
    except Exception as exc:  # noqa: BLE001 — silent fallback by design
        _logger.warning(
            "kite quote failed user=%s ticker=%s: %s",
            user_id, ticker, exc,
        )
        return None


def _splice_today_bar(
    df: pd.DataFrame,
    quote: dict,
    today: date,
) -> pd.DataFrame:
    """Overlay today's running OHLCV from a Kite quote.

    If ``df.iloc[-1].date == today`` (yfinance already has a partial
    bar): overwrite that row's open/high/low/close/volume.
    Otherwise (yfinance hasn't refreshed today yet): append a new
    row sorted by date.

    Sets ``close`` to ``quote["last_price"]`` (the running close,
    unambiguous semantics) rather than ``quote["close"]`` which is
    the prior-day close during pre-market.

    Returns a new DataFrame; the input is not mutated.
    """
    out = df.copy()
    new_row = {
        "date": today,
        "open": float(quote["open"]),
        "high": float(quote["high"]),
        "low": float(quote["low"]),
        "close": float(quote["last_price"]),
        "volume": int(quote["volume"]),
    }
    if not out.empty and out.iloc[-1]["date"] == today:
        last_idx = out.index[-1]
        for col, val in new_row.items():
            out.at[last_idx, col] = val
        return out
    return pd.concat(
        [out, pd.DataFrame([new_row])],
        ignore_index=True,
    )
```

- [ ] **Step 4.4: Run test to verify it passes**

```bash
PYTHONPATH=.:backend python -m pytest tests/backend/test_dashboard_kite_overlay.py -v
```

Expected: 9 passed (4 splice + 5 try_kite_quote).

If `pytest-asyncio` isn't installed or configured, the async tests may skip — confirm by output. If skipped, add `@pytest.mark.asyncio` decorator + ensure `pytest.ini` has `asyncio_mode = auto` or use `[tool.pytest.ini_options]` in `pyproject.toml`. Project already runs async tests elsewhere (see `tests/backend/test_chat_stream.py`) so this should "just work".

- [ ] **Step 4.5: Lint**

```bash
black backend/dashboard_kite_overlay.py tests/backend/test_dashboard_kite_overlay.py
isort backend/dashboard_kite_overlay.py tests/backend/test_dashboard_kite_overlay.py --profile black
flake8 backend/dashboard_kite_overlay.py tests/backend/test_dashboard_kite_overlay.py
```

Expected: no output.

- [ ] **Step 4.6: Commit**

```bash
git add backend/dashboard_kite_overlay.py tests/backend/test_dashboard_kite_overlay.py
git commit -m "$(cat <<'EOF'
feat(chart-overlay): dashboard_kite_overlay helpers for live today bar

_try_kite_quote: silently loads user creds + instrument token,
calls KiteClient.quote, returns None on any failure. _splice_today_bar:
overwrites or appends today's running OHLCV (close = last_price).
Pure helpers, fully tested; route wiring lands next.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 5: OHLCVResponse.is_live field

**Files:**
- Modify: `backend/dashboard_models.py`

- [ ] **Step 5.1: Add field**

Edit `backend/dashboard_models.py`. Find the `OHLCVResponse` class (search for `class OHLCVResponse`). Add `is_live: bool = False` after the existing `data` field:

```python
class OHLCVResponse(BaseModel):
    ticker: str
    data: list[OHLCVPoint] = Field(default_factory=list)
    is_live: bool = False
```

- [ ] **Step 5.2: Lint**

```bash
black backend/dashboard_models.py && isort backend/dashboard_models.py --profile black && flake8 backend/dashboard_models.py
```

Expected: no output.

- [ ] **Step 5.3: Commit**

```bash
git add backend/dashboard_models.py
git commit -m "$(cat <<'EOF'
feat(dashboard): add is_live: bool to OHLCVResponse

Signals the frontend to enable 30s SWR polling and render the
LIVE pill when the chart's today-bar reflects a live Kite quote
rather than the yfinance snapshot. Default False keeps old
cached payloads safe.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 6: Wire overlay into get_chart_ohlcv + cache invalidation glob

**Files:**
- Modify: `backend/dashboard_routes.py` (lines ~20-22 imports, ~1085-1159 route body, ~1516 invalidation)
- Modify: `tests/backend/test_dashboard_routes.py` (new test class `TestChartOHLCVLiveOverlay`)

- [ ] **Step 6.1: Write the failing tests**

Append to `tests/backend/test_dashboard_routes.py` (at the end of the file or after the existing `TestChartIndicators` class):

```python
class TestChartOHLCVLiveOverlay:
    """GET /v1/dashboard/chart/ohlcv — Kite live overlay during market hours."""

    def _mock_repo_returns_one_row(self, mock_repo_fn):
        repo = MagicMock()
        repo.get_ohlcv.return_value = pd.DataFrame(
            [
                {
                    "date": "2026-06-01",
                    "open": 2868.0, "high": 2870.0,
                    "low": 2867.0, "close": 2869.0,
                    "volume": 50_000,
                },
            ],
        )
        mock_repo_fn.return_value = repo
        return repo

    def _mock_cache(self, mock_cache_fn):
        cache = MagicMock()
        cache.get.return_value = None
        mock_cache_fn.return_value = cache
        return cache

    @patch(
        "dashboard_kite_overlay._try_kite_quote",
        new_callable=AsyncMock,
    )
    @patch("dashboard_routes.is_market_open")  # imported INTO dashboard_routes — patch at importer (see CLAUDE.md §4.2 #16 — exception: imported-name, not module-attribute)
    @patch("dashboard_routes.get_cache")
    @patch("dashboard_routes._get_stock_repo")
    def test_live_path_sets_is_live_true(
        self,
        mock_repo_fn,
        mock_cache_fn,
        mock_market_open,
        mock_kite,
        client,
    ):
        mock_market_open.return_value = True
        mock_kite.return_value = {
            "open": 2870.0, "high": 2895.3,
            "low": 2861.5, "close": 2882.1,
            "last_price": 2884.4, "volume": 1_842_900,
            "last_trade_time": None,
        }
        self._mock_repo_returns_one_row(mock_repo_fn)
        cache = self._mock_cache(mock_cache_fn)

        resp = client.get(
            "/v1/dashboard/chart/ohlcv?ticker=RELIANCE.NS",
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["is_live"] is True
        # cache key includes user_id; TTL is TTL_MARKET_LIVE (30).
        assert cache.set.call_count == 1
        cache_key = cache.set.call_args.args[0]
        ttl = cache.set.call_args.args[2]
        assert "RELIANCE.NS" in cache_key
        assert cache_key != "cache:chart:ohlcv:RELIANCE.NS"
        assert ttl == 30

    @patch(
        "dashboard_kite_overlay._try_kite_quote",
        new_callable=AsyncMock,
    )
    @patch("dashboard_routes.is_market_open")  # imported INTO dashboard_routes — patch at importer (see CLAUDE.md §4.2 #16 — exception: imported-name, not module-attribute)
    @patch("dashboard_routes.get_cache")
    @patch("dashboard_routes._get_stock_repo")
    def test_market_closed_skips_overlay(
        self,
        mock_repo_fn,
        mock_cache_fn,
        mock_market_open,
        mock_kite,
        client,
    ):
        mock_market_open.return_value = False
        self._mock_repo_returns_one_row(mock_repo_fn)
        cache = self._mock_cache(mock_cache_fn)

        resp = client.get(
            "/v1/dashboard/chart/ohlcv?ticker=RELIANCE.NS",
        )

        assert resp.status_code == 200
        assert resp.json()["is_live"] is False
        mock_kite.assert_not_called()
        # shared cache key + TTL_STABLE (300)
        cache_key = cache.set.call_args.args[0]
        ttl = cache.set.call_args.args[2]
        assert cache_key == "cache:chart:ohlcv:RELIANCE.NS"
        assert ttl == 300

    @patch(
        "dashboard_kite_overlay._try_kite_quote",
        new_callable=AsyncMock,
    )
    @patch("dashboard_routes.is_market_open")  # imported INTO dashboard_routes — patch at importer (see CLAUDE.md §4.2 #16 — exception: imported-name, not module-attribute)
    @patch("dashboard_routes.get_cache")
    @patch("dashboard_routes._get_stock_repo")
    def test_us_ticker_skips_overlay(
        self,
        mock_repo_fn,
        mock_cache_fn,
        mock_market_open,
        mock_kite,
        client,
    ):
        mock_market_open.return_value = True
        self._mock_repo_returns_one_row(mock_repo_fn)
        self._mock_cache(mock_cache_fn)

        resp = client.get(
            "/v1/dashboard/chart/ohlcv?ticker=AAPL",
        )

        assert resp.status_code == 200
        assert resp.json()["is_live"] is False
        mock_kite.assert_not_called()

    @patch(
        "dashboard_kite_overlay._try_kite_quote",
        new_callable=AsyncMock,
    )
    @patch("dashboard_routes.is_market_open")  # imported INTO dashboard_routes — patch at importer (see CLAUDE.md §4.2 #16 — exception: imported-name, not module-attribute)
    @patch("dashboard_routes.get_cache")
    @patch("dashboard_routes._get_stock_repo")
    def test_kite_returns_none_falls_back(
        self,
        mock_repo_fn,
        mock_cache_fn,
        mock_market_open,
        mock_kite,
        client,
    ):
        mock_market_open.return_value = True
        mock_kite.return_value = None
        self._mock_repo_returns_one_row(mock_repo_fn)
        cache = self._mock_cache(mock_cache_fn)

        resp = client.get(
            "/v1/dashboard/chart/ohlcv?ticker=RELIANCE.NS",
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["is_live"] is False
        # User-fallback path: shared key + TTL_STABLE.
        cache_key = cache.set.call_args.args[0]
        ttl = cache.set.call_args.args[2]
        assert cache_key == "cache:chart:ohlcv:RELIANCE.NS"
        assert ttl == 300
```

Also add to the imports at the top of the file: `from unittest.mock import AsyncMock` (if not already imported).

- [ ] **Step 6.2: Run tests to verify they fail**

```bash
PYTHONPATH=.:backend python -m pytest tests/backend/test_dashboard_routes.py::TestChartOHLCVLiveOverlay -v
```

Expected: 4 failures (route doesn't have the overlay yet → `is_live` field missing OR fails on import of `is_market_open`/`dashboard_kite_overlay`).

- [ ] **Step 6.3: Wire imports into dashboard_routes.py**

Edit `backend/dashboard_routes.py`. Near the top of the file (around line 18-22 where `from cache import …` is imported), add:

```python
from cache import (
    TTL_ADMIN,
    TTL_MARKET_LIVE,    # ← new
    TTL_STABLE,
    TTL_VOLATILE,
    get_cache,
)
from market_hours import is_market_open
from market_utils import detect_market
import dashboard_kite_overlay
```

(Place the new imports in alphabetical order within their respective import blocks; `import dashboard_kite_overlay` lives in the local-module block alongside other `import …` lines.)

- [ ] **Step 6.4: Add overlay block to get_chart_ohlcv**

Edit `backend/dashboard_routes.py`. In `get_chart_ohlcv` (around lines 1085–1159), find the section AFTER the dedup block (around line 1135) and BEFORE the `points: list[OHLCVPoint] = []` loop (around line 1137). Insert:

```python
        # ── Live Kite overlay (NSE market hours only) ──
        is_live = False
        if (
            is_market_open()
            and detect_market(t_upper) == "india"
        ):
            quote = await (
                dashboard_kite_overlay._try_kite_quote(
                    user, t_upper,
                )
            )
            if quote is not None:
                from datetime import date as _date
                today_ist = _date.today()  # IST container TZ
                df = (
                    dashboard_kite_overlay._splice_today_bar(
                        df, quote, today_ist,
                    )
                )
                is_live = True
```

Then update the response construction + cache write. Find:

```python
        result = OHLCVResponse(
            ticker=t_upper,
            data=points,
        )
        cache.set(
            cache_key,
            result.model_dump_json(),
            TTL_STABLE,
        )
        return result
```

Replace with:

```python
        result = OHLCVResponse(
            ticker=t_upper,
            data=points,
            is_live=is_live,
        )
        live_cache_key = (
            f"cache:chart:ohlcv:{user.user_id}:{t_upper}"
            if is_live
            else cache_key
        )
        cache.set(
            live_cache_key,
            result.model_dump_json(),
            TTL_MARKET_LIVE if is_live else TTL_STABLE,
        )
        return result
```

Note: `cache_key` at line 1103 stays as the shared `f"cache:chart:ohlcv:{t_upper}"` for the early cache-hit check at line 1104. The per-user key is only used on write when the overlay is applied — non-Kite users still hit the shared cache on subsequent reads.

(There is a subtle cache-stratification trade-off: if user A populates the per-user key and user B (no Kite) hits the shared key path, both work correctly. But user A's per-user write does NOT also populate the shared key, so user A pays one Kite call every 30s as designed.)

- [ ] **Step 6.5: Update cache invalidation glob (line ~1516)**

Edit `backend/dashboard_routes.py`. Find the per-ticker refresh callback (around line 1516):

```python
            for pattern in [
                "cache:dash:*",
                f"cache:chart:ohlcv:{t}",       # ← change this line
                f"cache:chart:indicators:{t}",
                f"cache:chart:forecast:{t}:*",
                "cache:insights:*",
            ]:
```

Change `f"cache:chart:ohlcv:{t}"` to `f"cache:chart:ohlcv:*{t}"` (prepend `*` so the existing `if "*" in pattern: cache.invalidate(pattern)` branch is taken — Redis SCAN-based glob clears both shared and per-user keys):

```python
            for pattern in [
                "cache:dash:*",
                f"cache:chart:ohlcv:*{t}",   # glob: shared + per-user
                f"cache:chart:indicators:{t}",
                f"cache:chart:forecast:{t}:*",
                "cache:insights:*",
            ]:
```

- [ ] **Step 6.6: Run tests to verify they pass**

```bash
PYTHONPATH=.:backend python -m pytest tests/backend/test_dashboard_routes.py::TestChartOHLCVLiveOverlay -v
```

Expected: 4 passed.

Also run the full `TestChartOHLCV` class (or its equivalent — likely already passing tests around line 605) and `TestChartIndicators` to confirm no regression:

```bash
PYTHONPATH=.:backend python -m pytest tests/backend/test_dashboard_routes.py::TestChartIndicators tests/backend/test_dashboard_routes.py::TestChartOHLCVLiveOverlay -v
```

Expected: previously-passing tests still pass; new tests pass. Pre-existing network-call failures (`test_happy_path`, `test_empty_data` in TestChartIndicators per `feedback_admin_merge_through_red_ci`) are out of scope.

- [ ] **Step 6.7: Lint**

```bash
black backend/dashboard_routes.py tests/backend/test_dashboard_routes.py
isort backend/dashboard_routes.py tests/backend/test_dashboard_routes.py --profile black
flake8 backend/dashboard_routes.py tests/backend/test_dashboard_routes.py
```

Expected: no NEW warnings. Pre-existing `F841 stock_repo`/`F811 math` warnings in `dashboard_routes.py` are out of scope (confirmed pre-existing on parent SHA — same as during the RSI(2) PR).

- [ ] **Step 6.8: Commit**

```bash
git add backend/dashboard_routes.py tests/backend/test_dashboard_routes.py
git commit -m "$(cat <<'EOF'
feat(dashboard): wire Kite today-bar overlay into /chart/ohlcv

Three-gate overlay (market open ∧ Kite linked ∧ Indian ticker)
splices today's running Kite quote onto the last yfinance bar.
Per-user cache key + 30s TTL when live; shared key + 300s TTL
otherwise. Invalidation glob at line 1516 now clears both keys
on per-ticker refresh.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 7: Frontend OHLCVResponse type

**Files:**
- Modify: `frontend/lib/types.ts`

- [ ] **Step 7.1: Add is_live field**

Edit `frontend/lib/types.ts`. Find the `OHLCVResponse` interface (search for `export interface OHLCVResponse`):

```ts
export interface OHLCVResponse {
  ticker: string;
  data: OHLCVPoint[];
  support_levels?: number[];
  resistance_levels?: number[];
}
```

(The exact shape may vary slightly; the change is the same.) Add `is_live?: boolean;` as an optional field:

```ts
export interface OHLCVResponse {
  ticker: string;
  data: OHLCVPoint[];
  is_live?: boolean;
  support_levels?: number[];
  resistance_levels?: number[];
}
```

(Position `is_live` directly after `data`; if `support_levels`/`resistance_levels` don't exist on the OHLCV type, place `is_live` after `data` and that's it.)

- [ ] **Step 7.2: Type-check**

```bash
cd frontend && npx tsc --noEmit 2>&1 | grep -E "is_live|OHLCVResponse"
```

Expected: zero matches (additive optional field doesn't break consumers).

- [ ] **Step 7.3: Commit**

```bash
git add frontend/lib/types.ts
git commit -m "$(cat <<'EOF'
feat(types): add is_live?: boolean to OHLCVResponse

Matches the new backend field. Optional for back-compat with any
client that hasn't been redeployed.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 8: Frontend SWR refreshInterval + LIVE pill

**Files:**
- Modify: `frontend/app/(authenticated)/analytics/analysis/page.tsx`

- [ ] **Step 8.1: Add 30s polling to the existing OHLCV useEffect**

Edit `frontend/app/(authenticated)/analytics/analysis/page.tsx`. The current OHLCV fetch is in a `useEffect` block around lines 282–334 (NOT a `useSWR` hook — the analysis page predates the §5.3 SWR convention; migrating it is a separate refactor and out of scope here).

The effect currently runs `Promise.all([ohlcv_fetch, indicators_fetch])` once per `[ticker]` change and calls `setOhlcv(o)` on success. Add a `setInterval` that refetches ONLY the OHLCV endpoint every 30s while `is_live` is true.

Find the `return () => { cancelled = true; };` cleanup at line 331–333 and replace the entire effect body so it:
1. Defines a `fetchOhlcv()` async helper that calls `/chart/ohlcv` and calls `setOhlcv(...)`.
2. Calls `fetchOhlcv()` once immediately.
3. Reads the latest `is_live` flag inside the helper and schedules `setTimeout(fetchOhlcv, 30_000)` only when `is_live` is true.
4. Cleanup clears any pending timer + sets `cancelled = true`.

Concrete replacement (the full updated effect body):

```ts
  useEffect(() => {
    let cancelled = false;
    let liveTimer: ReturnType<typeof setTimeout> | null = null;

    void Promise.resolve().then(() => {
      if (cancelled) return;
      setLoading(true);
      setError(null);
      setSupportLevels([]);
      setResistanceLevels([]);
    });

    const q = encodeURIComponent(ticker);

    const fetchOhlcv = async () => {
      try {
        const r = await apiFetch(
          `${API_URL}/dashboard/chart/ohlcv?ticker=${q}`,
        );
        if (!r.ok) throw new Error(`OHLCV: HTTP ${r.status}`);
        const o = (await r.json()) as OHLCVResponse;
        if (cancelled) return;
        setOhlcv(o);
        if (o.is_live) {
          liveTimer = setTimeout(fetchOhlcv, 30_000);
        }
      } catch (e) {
        if (!cancelled) setError(String(e));
      }
    };

    // Initial load: OHLCV + indicators in parallel.
    Promise.all([
      fetchOhlcv(),
      apiFetch(
        `${API_URL}/dashboard/chart/indicators?ticker=${q}`,
      ).then((r) => {
        if (!r.ok) {
          throw new Error(`Indicators: HTTP ${r.status}`);
        }
        return r.json() as Promise<IndicatorsResponse>;
      }),
    ])
      .then(([_, ind]) => {
        if (cancelled) return;
        setIndicators(ind);
        setSupportLevels(
          Array.isArray(ind?.support_levels)
            ? (ind.support_levels as number[])
            : [],
        );
        setResistanceLevels(
          Array.isArray(ind?.resistance_levels)
            ? (ind.resistance_levels as number[])
            : [],
        );
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
      if (liveTimer) clearTimeout(liveTimer);
    };
  }, [ticker]);
```

The key behaviors this preserves and adds:
- Initial parallel load of OHLCV + indicators is unchanged (loading state flips correctly).
- After the first OHLCV response, if `is_live` is true, a 30s timer fires and re-fetches OHLCV only (cheaper than re-fetching indicators).
- Each subsequent response can independently extend or stop the polling — when `is_live` flips false (market close, ticker change race, Kite outage), the next response doesn't schedule another timer.
- Ticker change unmounts the effect; cleanup clears the pending timer.

The same change pattern applies to the SECOND `setOhlcv`/`useEffect` instance starting at line ~575 (this is a separate tab/instance of `AnalysisTab`). Apply the same `fetchOhlcv` + `setTimeout` pattern there.

- [ ] **Step 8.2: Add the LIVE pill**

Find the chart header section in the same file (search for the area that renders the ticker symbol prominently — typically a `<div>` with the ticker name or company name near the chart). Insert the pill directly after the ticker label:

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

If the header is structured such that adding a sibling `<span>` is awkward (e.g. flex layout where it would push other elements), wrap with the parent's spacing utility (`ml-2` or `gap-2`) as needed.

- [ ] **Step 8.3: Type-check**

```bash
cd frontend && npx tsc --noEmit 2>&1 | grep -E "analysis/page|is_live"
```

Expected: zero matches.

- [ ] **Step 8.4: Lint**

```bash
cd frontend && npx eslint "app/(authenticated)/analytics/analysis/page.tsx" --fix
```

Expected: no errors.

- [ ] **Step 8.5: Commit**

```bash
git add "frontend/app/(authenticated)/analytics/analysis/page.tsx"
git commit -m "$(cat <<'EOF'
feat(analysis): SWR 30s refresh + LIVE pill when chart is live

When OHLCVResponse.is_live is true, SWR polls /chart/ohlcv every
30s and the chart header renders an emerald LIVE pill with a
pulsing dot. Both auto-disable when is_live flips false (market
close, ticker change, Kite outage).

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 9: Manual smoke test

**Files:** none

- [ ] **Step 9.1: Restart backend + flush Redis**

```bash
./run.sh restart backend
sleep 5
docker compose exec redis redis-cli FLUSHALL
```

Expected: backend restarts (new `is_live` field on `response_model` per §6.2); Redis returns `OK`.

- [ ] **Step 9.2: Verify schema includes is_live**

```bash
curl -s http://localhost:8181/openapi.json | python3 -c "
import json, sys
d = json.load(sys.stdin)
props = d['components']['schemas']['OHLCVResponse']['properties']
print('is_live present:', 'is_live' in props)
print(' default:', props.get('is_live', {}).get('default'))
"
```

Expected: `is_live present: True`, default `False`.

- [ ] **Step 9.3: Hit the route directly during market hours**

If running during NSE hours (Mon–Fri 09:00–15:30 IST) with a linked Kite account, hit the chart endpoint via the browser after logging in. In the Network tab, inspect the `/chart/ohlcv` response payload and confirm `is_live: true`. Confirm subsequent automatic refetches every ~30s.

- [ ] **Step 9.4: Visual smoke test in browser**

1. Open `http://localhost:3000/analytics/analysis?ticker=RELIANCE.NS` while logged in (Kite linked, during market hours).
2. Confirm the chart loads; today's last candle reflects the current Kite OHLC + volume.
3. Confirm the LIVE pill appears next to the ticker symbol with a pulsing emerald dot.
4. Network tab: confirm `/chart/ohlcv` is refetched roughly every 30s.
5. Switch ticker to `AAPL` (US): pill disappears; polling stops.
6. Wait until 15:30 IST (or simulate by stopping the backend container, faking system time, restarting — only if you want to test the close transition): next refetch returns `is_live: false`; pill disappears; polling stops.
7. Outside market hours OR without linked Kite: chart looks identical to today; no pill; no polling.

- [ ] **Step 9.5: Dark mode pass**

Toggle dark mode and confirm the LIVE pill text/dot remain legible against the dark background.

---

## Task 10: Push branch and open PR

**Files:** none

- [ ] **Step 10.1: Push the branch**

```bash
git push -u origin feature/chart-kite-live-today-bar
```

- [ ] **Step 10.2: Open the PR**

```bash
gh pr create --base dev --title "feat(chart): live Kite today-bar overlay on stock analysis chart" --body "$(cat <<'EOF'
## Summary

- During NSE market hours (Mon-Fri 09:00-15:30 IST), `/v1/dashboard/chart/ohlcv` overlays today's running OHLC + volume from the user's linked Kite account onto the yfinance-sourced series.
- Only applies when (market open ∧ user has Kite linked ∧ Indian ticker `.NS`/`.BO`) — silent fallback to yfinance on any failure.
- New `is_live: bool` field on `OHLCVResponse` drives a 30s SWR refresh and a LIVE pill on the chart.
- On-the-fly only: no Iceberg writes. EOD yfinance pipeline reconciles automatically.
- Per-user cache key with `TTL_MARKET_LIVE = 30s` when live; existing shared key + 300s when not.

Spec: `docs/superpowers/specs/2026-06-01-chart-kite-live-today-bar-design.md`
Plan: `docs/superpowers/plans/2026-06-01-chart-kite-live-today-bar.md`

## Deploy steps (per CLAUDE.md §4.5 / §6.2)

After merge:

\`\`\`bash
./run.sh restart backend     # new is_live field on response_model
docker compose exec redis redis-cli FLUSHALL  # cache key shape changed
\`\`\`

## Test plan

- [x] \`pytest tests/backend/test_market_hours.py -v\` — 7 tests covering NSE session window.
- [x] \`pytest backend/algo/broker/tests/test_kite_client_quote.py -v\` — 6 tests covering quote() wrapper.
- [x] \`pytest tests/backend/test_dashboard_kite_overlay.py -v\` — 9 tests covering _try_kite_quote + _splice_today_bar.
- [x] \`pytest tests/backend/test_dashboard_routes.py::TestChartOHLCVLiveOverlay -v\` — 4 route integration scenarios.
- [x] \`npx tsc --noEmit\` clean for any is_live matches.
- [x] \`npx eslint\` clean on the touched analysis page.
- [ ] Manual smoke test (Task 9): linked-Kite user on \`/analytics/analysis?ticker=RELIANCE.NS\` during NSE hours sees live candle + LIVE pill + 30s polling; non-Kite/US/outside-hours sees prior behavior unchanged.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Notes for the implementer

- **DRY:** `_splice_today_bar` is intentionally a single-row mutation — do not generalize to multi-day splicing.
- **YAGNI:** no quote batching beyond a single ticker per route call; no WebSocket; no shared cross-user cache. All deferred per spec §12.
- **TDD:** every backend task is test-first. Frontend has no chart-level test harness — Task 9's manual smoke covers it.
- **Silent fallback:** `_try_kite_quote` MUST catch every exception and return None. The chart is never allowed to 500 because Kite blinked.
- **Branch is already cut.** `feature/chart-kite-live-today-bar` carries the spec commit (`8ffe1fe`). Do not re-branch.
- **Pre-existing CI red:** Same five `test_dashboard_routes.py` network-call tests will fail in CI per `feedback_admin_merge_through_red_ci`. Out of scope.
