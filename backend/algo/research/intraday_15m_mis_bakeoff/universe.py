"""F&O 200 universe loader for the intraday 15m MIS bake-off."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd

_FNO_CSV = Path(__file__).parent / "fno_200.csv"


def load_fno_universe() -> list[str]:
    """Return the F&O ticker list as a sorted list of strings.

    Sourced from the static ``fno_200.csv`` packaged with this
    module. Refresh quarterly when NSE updates the F&O list.
    """
    df = pd.read_csv(_FNO_CSV)
    tickers = sorted(df["ticker"].dropna().unique().tolist())
    return tickers


def fno_universe_checksum() -> str:
    """SHA-256 of the F&O CSV — stamped in run_metadata.json."""
    return hashlib.sha256(_FNO_CSV.read_bytes()).hexdigest()
