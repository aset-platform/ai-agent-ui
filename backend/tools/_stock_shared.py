"""Shared path constants, Iceberg repo singleton, and small helpers for stock tools.

All sub-modules (``_stock_registry``, ``_stock_fetch``) access these values as
module attributes (``import tools._stock_shared as _ss; _ss._DATA_RAW``) so
that ``monkeypatch.setattr(tools._stock_shared, "_DATA_RAW", ...)`` works in
tests.

Constants
---------
- :data:`_PROJECT_ROOT`
- :data:`_DATA_RAW`
- :data:`_DATA_PROCESSED`
- :data:`_STOCK_REPO`
- :data:`_STOCK_REPO_INIT_ATTEMPTED`
"""

import logging
import os
import sys
from pathlib import Path
from typing import Optional

# Module-level logger; kept at module scope intentionally for shared utility use.
_logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_DATA_RAW = _PROJECT_ROOT / "data" / "raw"
_DATA_PROCESSED = _PROJECT_ROOT / "data" / "processed"

# Ensure PyIceberg can find the catalog regardless of CWD.
# The backend process runs from backend/ but .pyiceberg.yaml lives at
# the project root with a relative SQLite URI.  PyIceberg's _ENV_CONFIG
# singleton reads env vars once at import time, so these must be set
# BEFORE any pyiceberg import.
os.environ.setdefault(
    "PYICEBERG_CATALOG__LOCAL__URI",
    f"sqlite:///{_PROJECT_ROOT.resolve()}/data/iceberg/catalog.db",
)
os.environ.setdefault(
    "PYICEBERG_CATALOG__LOCAL__WAREHOUSE",
    f"file:///{_PROJECT_ROOT.resolve()}/data/iceberg/warehouse",
)

_STOCK_REPO = None
_STOCK_REPO_INIT_ATTEMPTED = False


def _get_repo():
    """Return the :class:`~stocks.repository.StockRepository` singleton.

    Initialised on first call.  Returns ``None`` when PyIceberg is
    unavailable.  After a failure, re-tries on the next call (no permanent
    caching of failures).

    Returns:
        :class:`~stocks.repository.StockRepository` instance or ``None``.
    """
    import tools._stock_shared as _ss

    if _ss._STOCK_REPO is not None:
        return _ss._STOCK_REPO
    if _ss._STOCK_REPO_INIT_ATTEMPTED:
        return None
    _ss._STOCK_REPO_INIT_ATTEMPTED = True
    try:
        _root = str(_ss._PROJECT_ROOT)
        if _root not in sys.path:
            sys.path.insert(0, _root)
        from stocks.repository import StockRepository  # noqa: PLC0415
        _ss._STOCK_REPO = StockRepository()
        _logger.debug("StockRepository initialised")
    except Exception as _e:
        _logger.warning("StockRepository unavailable: %s", _e)
        _ss._STOCK_REPO_INIT_ATTEMPTED = False  # allow retry on next call
    return _ss._STOCK_REPO


def _require_repo():
    """Return the :class:`~stocks.repository.StockRepository` singleton.

    Unlike :func:`_get_repo`, this raises :class:`RuntimeError` when the
    repository is unavailable.  Use this in code paths where Iceberg is
    the single source of truth.

    Returns:
        :class:`~stocks.repository.StockRepository` instance.

    Raises:
        RuntimeError: If the repository cannot be initialised.
    """
    repo = _get_repo()
    if repo is None:
        raise RuntimeError(
            "StockRepository is unavailable — Iceberg catalog may not be initialised. "
            "Run 'python stocks/create_tables.py' first."
        )
    return repo


def _parquet_path(ticker: str) -> Path:
    """Return the conventional raw OHLCV parquet path for *ticker*.

    Args:
        ticker: Stock ticker symbol (already uppercased).

    Returns:
        Path object like ``data/raw/{TICKER}_raw.parquet``.
    """
    import tools._stock_shared as _ss
    return _ss._DATA_RAW / f"{ticker}_raw.parquet"


# Fix #6: delegate to shared helpers module to eliminate duplication.
# Fix #5: TTL cache is implemented in _helpers._load_currency.
from tools._helpers import _currency_symbol, _load_currency  # noqa: F401
