"""NSE data source using jugaad-data."""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from functools import partial

import pandas as pd
from jugaad_data.nse import stock_df

from backend.pipeline.sources.base import (
    SourceError,
    SourceErrorCategory,
    classify_error,
)

_logger = logging.getLogger(__name__)

# Column mapping from jugaad-data to standard names.
# jugaad-data column names can vary; we handle both
# uppercase and mixed-case variants defensively.
_COLUMN_MAP: dict[str, str] = {
    "DATE": "date",
    "OPEN": "open",
    "HIGH": "high",
    "LOW": "low",
    "CLOSE": "adj_close",
    "LTP": "close",
    "VOLUME": "volume",
    # Lowercase variants
    "date": "date",
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "adj_close",
    "ltp": "close",
    "volume": "volume",
    # Title-case variants
    "Date": "date",
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "adj_close",
    "Ltp": "close",
    "Volume": "volume",
}

_REQUIRED_COLS = [
    "date", "open", "high", "low",
    "close", "adj_close", "volume",
]


class NseSource:
    """Fetches OHLCV data from NSE via jugaad-data.

    Accepts plain NSE symbols (e.g. ``RELIANCE``, no
    ``.NS`` suffix).  The synchronous ``stock_df`` call is
    run in a thread-pool executor.
    """

    async def fetch_ohlcv(
        self,
        symbol: str,
        start: date | None = None,
        end: date | None = None,
    ) -> pd.DataFrame:
        """Fetch OHLCV from NSE for *symbol*."""
        if start is None or end is None:
            raise SourceError(
                SourceErrorCategory.UNKNOWN,
                f"NseSource requires both start and end "
                f"dates for {symbol}",
            )

        loop = asyncio.get_running_loop()
        try:
            df: pd.DataFrame = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    partial(
                        stock_df,
                        symbol=symbol,
                        from_date=start,
                        to_date=end,
                    ),
                ),
                timeout=60.0,
            )
        except asyncio.TimeoutError:
            raise SourceError(
                SourceErrorCategory.TIMEOUT,
                f"NSE fetch timed out for {symbol} "
                f"(60s limit)",
            )
        except Exception as exc:
            cat = classify_error(exc)
            raise SourceError(
                cat,
                f"NSE fetch failed for {symbol}: {exc}",
                original=exc,
            ) from exc

        df = self._normalise_columns(df, symbol)
        _logger.debug(
            "NseSource fetched %d rows for %s",
            len(df), symbol,
        )
        return df

    # --------------------------------------------------
    @staticmethod
    def _normalise_columns(
        df: pd.DataFrame,
        symbol: str,
    ) -> pd.DataFrame:
        """Map jugaad-data columns to standard names."""
        rename = {}
        for col in df.columns:
            if col in _COLUMN_MAP:
                rename[col] = _COLUMN_MAP[col]

        df = df.rename(columns=rename)

        # If adj_close present but close missing, copy it.
        if (
            "adj_close" in df.columns
            and "close" not in df.columns
        ):
            df["close"] = df["adj_close"]

        # If close present but adj_close missing, copy it.
        if (
            "close" in df.columns
            and "adj_close" not in df.columns
        ):
            df["adj_close"] = df["close"]

        missing = [
            c for c in _REQUIRED_COLS
            if c not in df.columns
        ]
        if missing:
            raise SourceError(
                SourceErrorCategory.PARSE_ERROR,
                f"NSE data for {symbol} missing columns: "
                f"{missing}",
            )

        return df[_REQUIRED_COLS].copy()
