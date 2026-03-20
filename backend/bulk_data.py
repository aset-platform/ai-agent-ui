"""Bulk data import/export API endpoints (ASETPLTFRM-16).

Provides CSV and Parquet import/export for OHLCV data with
date range filtering, column validation, and streaming
downloads.

Endpoints
---------
- ``POST /v1/bulk-import`` — upload CSV/Parquet file
- ``GET  /v1/bulk-export``  — download CSV/Parquet file

Both endpoints require JWT authentication.
"""

from __future__ import annotations

import io
import logging
from datetime import date
from urllib.parse import quote

import pandas as pd
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi.responses import StreamingResponse
from validation import validate_ticker

from auth.dependencies import get_current_user
from auth.models import UserContext

_logger = logging.getLogger(__name__)

# Required columns for OHLCV CSV import.
_REQUIRED_COLUMNS = {
    "ticker",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
}

# All valid columns (required + optional).
_VALID_COLUMNS = _REQUIRED_COLUMNS | {"adj_close"}

# Maximum file size for import (10 MB).
_MAX_IMPORT_BYTES = 10 * 1024 * 1024


def create_bulk_router() -> APIRouter:
    """Create and return the bulk data API router.

    Returns:
        :class:`~fastapi.APIRouter` with import/export
        endpoints.  Mounted at ``/v1`` by the caller in
        ``routes.py``.
    """
    router = APIRouter(
        tags=["bulk-data"],
    )

    @router.post("/bulk-import")
    async def bulk_import(
        file: UploadFile = File(...),
        user: UserContext = Depends(get_current_user),
    ):
        """Import OHLCV data from CSV or Parquet file.

        The file must contain columns: ticker, date, open,
        high, low, close, volume.  Optional: adj_close.

        Args:
            file: Uploaded CSV or Parquet file.
            user: Authenticated user context.

        Returns:
            Dict with import results.
        """
        if file.filename is None:
            raise HTTPException(
                status_code=422,
                detail="Filename is required.",
            )

        # Check Content-Length before reading body.
        if file.size is not None and file.size > _MAX_IMPORT_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    "File too large. Maximum size "
                    f"is {_MAX_IMPORT_BYTES // (1024*1024)}"
                    " MB."
                ),
            )

        content = await file.read()
        if len(content) > _MAX_IMPORT_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    "File too large. Maximum size "
                    f"is {_MAX_IMPORT_BYTES // (1024*1024)}"
                    " MB."
                ),
            )

        # Parse file based on extension.
        filename = file.filename.lower()
        try:
            if filename.endswith(".csv"):
                df = pd.read_csv(io.BytesIO(content))
            elif filename.endswith(".parquet"):
                df = pd.read_parquet(
                    io.BytesIO(content),
                )
            else:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Unsupported file format. " "Use .csv or .parquet."
                    ),
                )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Failed to parse file: {exc}",
            )

        # Normalise column names to lowercase.
        df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

        # Validate required columns.
        missing = _REQUIRED_COLUMNS - set(df.columns)
        if missing:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Missing required columns: "
                    f"{sorted(missing)}. "
                    f"Required: {sorted(_REQUIRED_COLUMNS)}"
                ),
            )

        # Drop unknown columns.
        valid_cols = [c for c in df.columns if c in _VALID_COLUMNS]
        df = df[valid_cols]

        if df.empty:
            return {
                "status": "ok",
                "rows_imported": 0,
                "message": "File contained no data rows.",
            }

        # Validate data types.
        try:
            df["date"] = pd.to_datetime(
                df["date"],
            ).dt.date
        except Exception:
            raise HTTPException(
                status_code=422,
                detail=("Column 'date' contains invalid " "date values."),
            )

        for col in ("open", "high", "low", "close", "volume"):
            try:
                df[col] = pd.to_numeric(
                    df[col],
                    errors="coerce",
                )
            except Exception:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Column '{col}' contains " f"non-numeric values."
                    ),
                )

        if "adj_close" in df.columns:
            df["adj_close"] = pd.to_numeric(
                df["adj_close"],
                errors="coerce",
            )

        # Validate ticker format.
        tickers = df["ticker"].str.upper().unique().tolist()
        for t in tickers:
            err = validate_ticker(t)
            if err:
                raise HTTPException(
                    status_code=422,
                    detail=f"Invalid ticker '{t}': {err}",
                )

        # Validate tickers exist in registry.
        from tools._stock_shared import _get_repo

        repo = _get_repo()
        if repo is None:
            raise HTTPException(
                status_code=503,
                detail="Stock repository unavailable.",
            )
        registry = repo.get_registry()

        if not registry.empty:
            known = set(
                registry["ticker"].str.upper().tolist(),
            )
        else:
            known = set()

        unknown = [t for t in tickers if t not in known]
        if unknown:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Unknown ticker(s): {unknown}. "
                    f"Register tickers before importing."
                ),
            )

        # Import data per ticker.
        total_imported = 0
        results_per_ticker = {}

        for ticker in tickers:
            ticker_df = df[df["ticker"].str.upper() == ticker].copy()

            # Prepare DataFrame in yfinance format.
            import_df = pd.DataFrame(
                {
                    "Open": ticker_df["open"].values,
                    "High": ticker_df["high"].values,
                    "Low": ticker_df["low"].values,
                    "Close": ticker_df["close"].values,
                    "Volume": ticker_df["volume"].values,
                },
                index=pd.DatetimeIndex(
                    ticker_df["date"],
                ),
            )
            if "adj_close" in ticker_df.columns:
                import_df["Adj Close"] = ticker_df["adj_close"].values

            count = repo.insert_ohlcv(ticker, import_df)
            total_imported += count
            results_per_ticker[ticker] = count

        _logger.info(
            "Bulk import by %s: %d rows across %d tickers",
            user.user_id,
            total_imported,
            len(tickers),
        )

        return {
            "status": "ok",
            "rows_imported": total_imported,
            "tickers": results_per_ticker,
        }

    @router.get("/bulk-export")
    async def bulk_export(
        ticker: str = Query(
            ...,
            description="Stock ticker symbol",
        ),
        output_format: str = Query(
            "csv",
            description="Export format: csv or parquet",
            alias="format",
        ),
        start: date | None = Query(
            None,
            description="Start date (inclusive, YYYY-MM-DD)",
        ),
        end: date | None = Query(
            None,
            description="End date (inclusive, YYYY-MM-DD)",
        ),
        user: UserContext = Depends(get_current_user),
    ):
        """Export OHLCV data as CSV or Parquet download.

        Args:
            ticker: Stock ticker to export.
            format: ``"csv"`` or ``"parquet"``.
            start: Optional start date filter.
            end: Optional end date filter.
            user: Authenticated user context.

        Returns:
            Streaming file download.
        """
        fmt = output_format.lower().strip()
        if fmt not in ("csv", "parquet"):
            raise HTTPException(
                status_code=422,
                detail=("Unsupported format. Use 'csv' " "or 'parquet'."),
            )

        from tools._stock_shared import _get_repo

        repo = _get_repo()
        if repo is None:
            raise HTTPException(
                status_code=503,
                detail="Stock repository unavailable.",
            )
        ticker = ticker.upper()

        # Verify ticker exists.
        registry = repo.get_registry(ticker=ticker)
        if registry.empty:
            raise HTTPException(
                status_code=404,
                detail=f"Ticker '{ticker}' not found.",
            )

        df = repo.get_ohlcv(
            ticker,
            start=start,
            end=end,
        )
        if df.empty:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"No OHLCV data for '{ticker}' " f"in the specified range."
                ),
            )

        _logger.info(
            "Bulk export by %s: %s %d rows (%s)",
            user.user_id,
            ticker,
            len(df),
            fmt,
        )

        safe_ticker = quote(ticker, safe="")

        if fmt == "csv":
            buf = io.StringIO()
            df.to_csv(buf, index=False)
            buf.seek(0)
            return StreamingResponse(
                iter([buf.getvalue()]),
                media_type="text/csv",
                headers={
                    "Content-Disposition": (
                        "attachment; " f"filename={safe_ticker}" "_ohlcv.csv"
                    ),
                },
            )

        # Parquet format.
        buf = io.BytesIO()
        df.to_parquet(buf, index=False)
        buf.seek(0)
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": (
                    "attachment; " f"filename={safe_ticker}" "_ohlcv.parquet"
                ),
            },
        )

    return router
