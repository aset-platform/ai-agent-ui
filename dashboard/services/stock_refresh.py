"""Dash-agnostic full stock data refresh pipeline.

Runs the same 6-step pipeline that the Stock Agent uses:

1. **Full OHLCV re-fetch** -- fetches the entire date range
   from yfinance (not a delta) so that any gaps in the middle
   of the data are filled.  Iceberg deduplication on
   ``(ticker, date)`` ensures existing rows are not duplicated.
2. Company info (``stocks.company_info``) — non-critical
3. Dividends (``stocks.dividends``) — non-critical
4. Technical analysis (``stocks.technical_indicators``,
   ``stocks.analysis_summary``) — non-critical
5. Quarterly results (``stocks.quarterly_results``)
   — non-critical
6. Prophet forecast (``stocks.forecast_runs``,
   ``stocks.forecasts``)

All 9 Iceberg tables are refreshed.  Steps 2-5 are
non-critical: failures are recorded but do not abort the
pipeline.  Only the OHLCV fetch (step 1) and Prophet forecast
(step 6) are critical.

Usage::

    from dashboard.services.stock_refresh import run_full_refresh

    result = run_full_refresh("AAPL", horizon_months=9)
    if result.success:
        print("Accuracy:", result.accuracy)
"""

from __future__ import annotations

import glob as _glob
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

# Module-level logger — must remain module-level (no enclosing class)
_logger = logging.getLogger(__name__)

# Ensure backend/ is on sys.path so tool imports resolve
_PROJECT_ROOT = Path(__file__).parent.parent.parent
_BACKEND_DIR = str(_PROJECT_ROOT / "backend")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from paths import CACHE_DIR  # noqa: E402

_CACHE_DIR = CACHE_DIR


@dataclass
class RefreshResult:
    """Structured result from a full stock data refresh.

    Attributes:
        success: ``True`` if the critical steps (OHLCV fetch +
            forecast) completed without error.
        steps: List of per-step dicts with keys ``name``, ``ok``,
            ``message``.
        accuracy: MAE / RMSE / MAPE dict from Prophet backtest,
            or ``None``.
        error: Top-level error message if the pipeline aborted.
    """

    success: bool = False
    steps: list = field(default_factory=list)
    accuracy: dict | None = None
    error: str | None = None


def _record(result: RefreshResult, name: str, ok: bool, message: str) -> None:
    """Append a step entry to *result*.steps."""
    result.steps.append({"name": name, "ok": ok, "message": message})
    level = logging.INFO if ok else logging.WARNING
    tag = "OK" if ok else "FAIL"
    _logger.log(level, "refresh %s: %s — %s", tag, name, message)


def _clear_tool_cache(ticker: str) -> None:
    """Delete same-day cache files for *ticker*.

    The backend tools (``analyse_stock_price``, ``forecast_stock``)
    cache their text results as
    ``data/cache/{TICKER}_{key}_{YYYY-MM-DD}.txt``.  Deleting
    these files forces the tools to recompute fresh results instead
    of returning stale cached output.

    Args:
        ticker: Uppercase ticker symbol.
    """
    pattern = str(_CACHE_DIR / f"{ticker}_*")
    for path in _glob.glob(pattern):
        try:
            os.remove(path)
            _logger.debug("Removed tool cache: %s", path)
        except OSError:
            pass


def _full_ohlcv_refresh(ticker: str) -> str:
    """Re-fetch the full OHLCV history and fill any gaps.

    Unlike ``fetch_stock_data`` (which only appends rows after
    ``date_range_end``), this function fetches the **entire**
    date range so that any gaps in the middle of the data are
    filled.  Iceberg deduplication on ``(ticker, date)``
    ensures existing rows are not inserted twice.

    Args:
        ticker: Uppercase ticker symbol.

    Returns:
        Summary message string.

    Raises:
        ValueError: If yfinance returns no data.
    """
    import yfinance as yf
    from tools._stock_registry import (
        _check_existing_data,
        _update_registry,
    )
    from tools._stock_shared import _parquet_path, _require_repo

    existing = _check_existing_data(ticker)
    repo = _require_repo()

    if existing:
        # Re-fetch the full range to fill any mid-range gaps.
        dr_start = existing.get("date_range", {}).get("start", "")
        start_arg = {"start": dr_start} if dr_start else {"period": "10y"}
    else:
        start_arg = {"period": "10y"}

    _logger.info("Full OHLCV refresh for %s (%s)", ticker, start_arg)
    yf_df = yf.Ticker(ticker).history(auto_adjust=False, **start_arg)

    if yf_df.empty:
        raise ValueError(f"No data returned from yfinance for '{ticker}'.")

    yf_df.index = pd.to_datetime(yf_df.index).tz_localize(None)
    inserted = repo.insert_ohlcv(ticker, yf_df)

    # Fill any NaN adj_close with close price for this ticker
    ice_df = repo.get_ohlcv(ticker)
    if not ice_df.empty:
        nan_mask = ice_df["adj_close"].isna()
        if nan_mask.any():
            fill_map = {}
            for _, r in ice_df[nan_mask].iterrows():
                if pd.notna(r["close"]):
                    d = r["date"]
                    if hasattr(d, "date"):
                        d = d.date()
                    fill_map[d] = float(r["close"])
            if fill_map:
                filled = repo.update_ohlcv_adj_close(ticker, fill_map)
                _logger.info(
                    "Filled %d NaN adj_close with close for %s",
                    filled,
                    ticker,
                )

    # Rebuild local parquet backup from Iceberg
    ice_df = repo.get_ohlcv(ticker)
    file_path = _parquet_path(ticker)
    if not ice_df.empty:
        ice_df["date"] = pd.to_datetime(ice_df["date"])
        ice_df = ice_df.sort_values("date").set_index("date")
        backup = pd.DataFrame(
            {
                "Open": ice_df["open"],
                "High": ice_df["high"],
                "Low": ice_df["low"],
                "Close": ice_df["close"],
                "Adj Close": (
                    ice_df["adj_close"]
                    if "adj_close" in ice_df.columns
                    else ice_df["close"]
                ),
                "Volume": ice_df["volume"],
            }
        )
        backup.index.name = "Date"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        backup.to_parquet(file_path, engine="pyarrow", index=True)
        total = len(backup)
        _update_registry(ticker, backup, file_path)
    else:
        total = len(yf_df)
        _update_registry(ticker, yf_df, file_path)

    msg = (
        f"Full refresh for {ticker}: {inserted} new rows "
        f"inserted. Total: {total} rows "
        f"({ice_df.index.min().date()} to "
        f"{ice_df.index.max().date()})."
    )
    _logger.info(msg)
    return msg


def run_full_refresh(ticker: str, horizon_months: int = 9) -> RefreshResult:
    """Execute the full 6-step stock data refresh pipeline.

    Step 1 performs a **full** re-fetch (not a delta) so that any
    gaps in the OHLCV data are filled.

    Args:
        ticker: Uppercase ticker symbol (e.g. ``"AAPL"``).
        horizon_months: Prophet forecast horizon in months
            (default ``9``).

    Returns:
        :class:`RefreshResult` with per-step status, accuracy
        metrics, and an overall success flag.
    """
    ticker = ticker.upper().strip()
    result = RefreshResult()

    # Clear same-day tool cache so analysis / forecast tools
    # recompute from the freshly updated Iceberg data.
    _clear_tool_cache(ticker)

    try:
        # ── Step 1: OHLCV fetch (skip if today's data exists) ─
        from tools._stock_shared import _require_repo

        _repo = _require_repo()
        _latest_date = _repo.get_latest_ohlcv_date(ticker)
        _ohlcv_fresh = (
            _latest_date is not None
            and _latest_date >= date.today()
        )
        if _ohlcv_fresh:
            fetch_msg = (
                f"OHLCV for {ticker} already up-to-date "
                f"(latest: {_latest_date}). Skipped."
            )
            _record(
                result,
                "Fetch OHLCV",
                True,
                fetch_msg,
            )
        else:
            fetch_msg = _full_ohlcv_refresh(ticker)
            _record(
                result,
                "Fetch OHLCV",
                True,
                fetch_msg[:200],
            )

        # ── Step 2: Company info (non-critical) ──────────────
        try:
            from tools.stock_data_tool import get_stock_info

            info_msg = get_stock_info.invoke({"ticker": ticker})
            _record(result, "Company info", True, info_msg[:120])
        except Exception as exc:
            _record(result, "Company info", False, str(exc)[:120])

        # ── Step 3: Dividends (non-critical) ─────────────────
        try:
            from tools.stock_data_tool import get_dividend_history

            div_msg = get_dividend_history.invoke({"ticker": ticker})
            _record(result, "Dividends", True, div_msg[:120])
        except Exception as exc:
            _record(result, "Dividends", False, str(exc)[:120])

        # ── Step 4: Technical analysis (non-critical) ────────
        try:
            from tools.price_analysis_tool import (
                analyse_stock_price,
            )

            analysis_msg = analyse_stock_price.invoke({"ticker": ticker})
            _record(
                result,
                "Technical analysis",
                True,
                analysis_msg[:120],
            )
        except Exception as exc:
            _record(
                result,
                "Technical analysis",
                False,
                str(exc)[:120],
            )

        # ── Step 5: Quarterly results (non-critical) ─────────
        try:
            from tools.stock_data_tool import (
                fetch_quarterly_results,
            )

            qtr_msg = fetch_quarterly_results.invoke({"ticker": ticker})
            _record(
                result,
                "Quarterly results",
                True,
                qtr_msg[:120],
            )
        except Exception as exc:
            _record(
                result,
                "Quarterly results",
                False,
                str(exc)[:120],
            )

        # ── Step 6: Prophet forecast (skip if <7 days old) ──
        _fc_run = _repo.get_latest_forecast_run(
            ticker,
            horizon_months,
        )
        _fc_fresh = False
        if _fc_run:
            _rd = _fc_run.get("run_date")
            if _rd is not None:
                if hasattr(_rd, "date"):
                    _rd = _rd.date()
                _cutoff = date.today() - timedelta(days=7)
                _fc_fresh = _rd >= _cutoff

        if _fc_fresh:
            fc_msg = (
                f"Forecast for {ticker} ({horizon_months}m) "
                f"already run on {_rd}. Skipped."
            )
            _record(
                result,
                "Prophet forecast",
                True,
                fc_msg,
            )
            _acc = {}
            if _fc_run.get("mae") is not None:
                _acc["MAE"] = _fc_run["mae"]
            if _fc_run.get("rmse") is not None:
                _acc["RMSE"] = _fc_run["rmse"]
            if _fc_run.get("mape") is not None:
                _acc["MAPE_pct"] = _fc_run["mape"]
            result.accuracy = _acc or None
            result.success = True
        else:
            from tools._forecast_accuracy import (
                _calculate_forecast_accuracy,
            )
            from tools._forecast_model import (
                _generate_forecast,
                _prepare_data_for_prophet,
                _train_prophet_model,
            )
            from tools._forecast_persist import (
                _save_forecast,
            )
            from tools._forecast_shared import _load_parquet

            df = _load_parquet(ticker)
            if df is None:
                raise ValueError(
                    f"No data loaded for {ticker} " f"after fetch."
                )

            prophet_df = _prepare_data_for_prophet(df)
            model = _train_prophet_model(prophet_df)
            forecast_df = _generate_forecast(
                model,
                prophet_df,
                horizon_months,
            )
            accuracy = _calculate_forecast_accuracy(
                model,
                prophet_df,
            )
            _save_forecast(
                forecast_df,
                ticker,
                horizon_months,
            )

            # Persist forecast run + series to Iceberg
            from tools._forecast_accuracy import (
                _generate_forecast_summary,
            )

            current_price = float(prophet_df["y"].iloc[-1])
            summary = _generate_forecast_summary(
                forecast_df,
                current_price,
                ticker,
                horizon_months,
            )
            _run_date = date.today()
            _run_dict: dict = {
                "run_date": _run_date,
                "sentiment": summary.get("sentiment"),
                "current_price_at_run": current_price,
            }
            for _m_key in ["3m", "6m", "9m"]:
                _t = summary.get("targets", {}).get(
                    _m_key,
                )
                if _t:
                    _run_dict[f"target_{_m_key}_date"] = _t.get("date")
                    _run_dict[f"target_{_m_key}_price"] = _t.get("price")
                    _run_dict[f"target_{_m_key}_pct_change"] = _t.get(
                        "pct_change"
                    )
                    _run_dict[f"target_{_m_key}_lower"] = _t.get("lower")
                    _run_dict[f"target_{_m_key}_upper"] = _t.get("upper")
            if "error" not in accuracy:
                _run_dict["mae"] = accuracy.get("MAE")
                _run_dict["rmse"] = accuracy.get(
                    "RMSE",
                )
                _run_dict["mape"] = accuracy.get(
                    "MAPE_pct",
                )
            _repo.insert_forecast_run(
                ticker,
                horizon_months,
                _run_dict,
            )
            _repo.insert_forecast_series(
                ticker,
                horizon_months,
                _run_date,
                forecast_df,
            )

            _record(
                result,
                "Prophet forecast",
                True,
                "Forecast complete.",
            )
            result.accuracy = accuracy
            result.success = True

    except Exception as exc:
        _logger.error(
            "run_full_refresh failed for %s: %s",
            ticker,
            exc,
            exc_info=True,
        )
        result.error = str(exc)
        _record(result, "Pipeline", False, str(exc)[:200])

    return result
