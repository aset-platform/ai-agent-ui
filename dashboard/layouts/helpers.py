"""Helper utilities for the AI Stock Analysis Dashboard layouts.

Provides path constants and private helper functions used by all layout
sub-modules: loading the stock registry from Iceberg and returning a sorted
list of tracked ticker symbols.
"""

import logging
import sys
from pathlib import Path
from typing import List

# Module-level logger; cannot be moved to a class as this module uses
# only module-level functions.
_logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _load_registry() -> dict:
    """Load the stock registry from Iceberg.

    Returns:
        Dictionary mapping ticker symbols to registry metadata records.
        Returns an empty dict if Iceberg is unavailable.
    """
    try:
        from dashboard.callbacks.iceberg import (
            _get_iceberg_repo,
            _get_registry_cached,
        )

        repo = _get_iceberg_repo()
        if repo is not None:
            return _get_registry_cached(repo)
    except Exception as exc:
        _logger.warning(
            "Could not load registry from Iceberg: %s",
            exc,
        )
    return {}


def _get_available_tickers() -> List[str]:
    """Return sorted list of ticker symbols from the stock registry.

    Returns:
        Alphabetically sorted list of ticker strings.
    """
    return sorted(_load_registry().keys())


def _get_available_sectors() -> List[str]:
    """Return sorted unique sector names from company info.

    Reads the cached ``stocks.company_info`` table and extracts
    distinct, non-empty sector values.

    Returns:
        Alphabetically sorted list of sector strings.
    """
    try:
        from dashboard.callbacks.iceberg import (  # noqa: PLC0415
            _get_company_info_cached,
            _get_iceberg_repo,
        )

        repo = _get_iceberg_repo()
        if repo is not None:
            df = _get_company_info_cached(repo)
            if not df.empty and "sector" in df.columns:
                sectors = (
                    df["sector"].dropna().loc[lambda s: s != "N/A"].unique()
                )
                return sorted(sectors)
    except Exception as exc:
        _logger.warning("Could not load sectors: %s", exc)
    return []
