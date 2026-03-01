"""Helper utilities for the AI Stock Analysis Dashboard layouts.

Provides path constants and private helper functions used by all layout
sub-modules: loading the stock registry from disk and returning a sorted
list of tracked ticker symbols.
"""

import json
import logging
from pathlib import Path
from typing import List

# Module-level logger; cannot be moved to a class as this module uses
# only module-level functions.
_logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_DATA_METADATA = _PROJECT_ROOT / "data" / "metadata"
_REGISTRY_PATH = _DATA_METADATA / "stock_registry.json"


def _load_registry() -> dict:
    """Load the stock registry from disk.

    Returns:
        Dictionary mapping ticker symbols to registry metadata records.
        Returns an empty dict if the file is missing or unparsable.
    """
    if not _REGISTRY_PATH.exists():
        return {}
    try:
        with open(_REGISTRY_PATH) as fh:
            return json.load(fh)
    except Exception as exc:
        _logger.warning("Could not load registry: %s", exc)
        return {}


def _get_available_tickers() -> List[str]:
    """Return sorted list of ticker symbols from the stock registry.

    Returns:
        Alphabetically sorted list of ticker strings.
    """
    return sorted(_load_registry().keys())