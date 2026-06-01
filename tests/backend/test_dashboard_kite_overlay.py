"""Tests for dashboard_kite_overlay.

Splice today's Kite bar onto yfinance OHLCV.
"""

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
                AsyncMock(return_value={738561: "RELIANCE.NS"})
            )
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
