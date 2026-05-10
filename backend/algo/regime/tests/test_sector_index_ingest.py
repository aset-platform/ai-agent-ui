"""Verify NIFTY sector indices + INDIAVIX are in the gap-filler list."""
from __future__ import annotations

import inspect

from backend.jobs import gap_filler


def test_sector_indices_in_refresh_list() -> None:
    """All NIFTY sector indices required by REGIME-1 must be in
    the gap-filler indices list, in addition to ^INDIAVIX + ^NSEI
    that already existed."""
    body = inspect.getsource(gap_filler.refresh_market_indices)
    required = [
        "^INDIAVIX",   # already present (regression guard)
        "^NSEI",       # already present (regression guard)
        "^NSEBANK",
        "^CNXIT",
        "^CNXAUTO",
        "^CNXPHARMA",
        "^CNXFMCG",
        "^CNXMETAL",
        "^CNXENERGY",
        "^CNXREALTY",
        "^CNXPSUBANK",
        "^CNXFINANCE",
        "^NIFMDCP150",
    ]
    missing = [t for t in required if t not in body]
    assert not missing, f"Missing from refresh list: {missing}"
