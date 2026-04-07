"""Yahoo Finance data source using yfinance."""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from functools import partial

import pandas as pd
import yfinance as yf

from backend.pipeline.sources.base import (
    SourceError,
    SourceErrorCategory,
    classify_error,
)

_logger = logging.getLogger(__name__)

_REQUIRED_COLS = [
    "date", "open", "high", "low",
    "close", "adj_close", "volume",
]


class YfinanceSource:
    """Fetches OHLCV data from Yahoo Finance.

    Accepts Yahoo-style tickers (e.g. ``RELIANCE.NS``).
    The synchronous ``yf.Ticker().history()`` call is run
    in a thread-pool executor.
    """

    async def fetch_ohlcv(
        self,
        symbol: str,
        start: date | None = None,
        end: date | None = None,
    ) -> pd.DataFrame:
        """Fetch OHLCV from Yahoo Finance for *symbol*."""
        loop = asyncio.get_running_loop()
        try:
            df: pd.DataFrame = await loop.run_in_executor(
                None,
                partial(
                    self._sync_fetch,
                    symbol=symbol,
                    start=start,
                    end=end,
                ),
            )
        except SourceError:
            raise
        except Exception as exc:
            cat = classify_error(exc)
            raise SourceError(
                cat,
                f"yfinance fetch failed for {symbol}: "
                f"{exc}",
                original=exc,
            ) from exc

        df = self._normalise_columns(df, symbol)
        _logger.debug(
            "YfinanceSource fetched %d rows for %s",
            len(df), symbol,
        )
        return df

    # --------------------------------------------------
    @staticmethod
    def _sync_fetch(
        symbol: str,
        start: date | None,
        end: date | None,
    ) -> pd.DataFrame:
        """Run the blocking yfinance call."""
        ticker = yf.Ticker(symbol)
        kwargs: dict[str, str] = {}
        if start is not None:
            kwargs["start"] = start.isoformat()
        if end is not None:
            kwargs["end"] = end.isoformat()
        if not kwargs:
            kwargs["period"] = "max"

        df = ticker.history(**kwargs)
        if df is None or df.empty:
            raise SourceError(
                SourceErrorCategory.NOT_FOUND,
                f"No data returned by yfinance for "
                f"{symbol}",
            )
        return df

    @staticmethod
    def _normalise_columns(
        df: pd.DataFrame,
        symbol: str,
    ) -> pd.DataFrame:
        """Map yfinance columns to standard names."""
        # yfinance returns Date as the index.
        if "Date" not in df.columns:
            df = df.reset_index()

        rename_map: dict[str, str] = {
            "Date": "date",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
        df = df.rename(columns=rename_map)

        # yfinance does not provide a separate adj_close
        # column in recent versions; use close.
        if "adj_close" not in df.columns:
            df["adj_close"] = df["close"]

        missing = [
            c for c in _REQUIRED_COLS
            if c not in df.columns
        ]
        if missing:
            raise SourceError(
                SourceErrorCategory.PARSE_ERROR,
                f"yfinance data for {symbol} missing "
                f"columns: {missing}",
            )

        return df[_REQUIRED_COLS].copy()
