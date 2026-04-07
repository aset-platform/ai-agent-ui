"""Download Nifty 500 constituents from NSE and generate seed CSV.

Fetches live index data for Nifty 50, Nifty 100, and Nifty 500
via jugaad-data, merges into a single CSV with pipe-delimited tags
suitable for the pipeline seed command.

Usage::

    source ~/.ai-agent-ui/venv/bin/activate
    python scripts/download_nifty500.py

    # Then seed:
    PYTHONPATH=.:backend python -m backend.pipeline.runner seed \\
        --csv data/universe/nifty500.csv

Output: data/universe/nifty500.csv
"""

import csv
import logging
import os
import sys
import time

_logger = logging.getLogger(__name__)

# Market-cap classification thresholds (INR crore)
_LARGECAP_THRESHOLD = 50_000  # top 100 by convention
_MIDCAP_THRESHOLD = 15_000    # 101-250 by convention


def _fetch_index(nse, index_name: str) -> set[str]:
    """Fetch constituent symbols for an NSE index."""
    _logger.info("Fetching %s...", index_name)
    data = nse.live_index(index_name)
    symbols = set()
    for entry in data.get("data", []):
        sym = entry.get("symbol", "")
        # Skip the index summary row (no symbol or
        # symbol matches index name)
        if sym and sym != index_name.replace(" ", ""):
            symbols.add(sym)
    _logger.info(
        "%s: %d constituents", index_name, len(symbols),
    )
    return symbols


def _classify_cap(
    symbol: str,
    nifty50: set,
    nifty100: set,
    nifty500: set,
) -> str:
    """Classify market cap tier."""
    if symbol in nifty50 or symbol in nifty100:
        return "largecap"
    # Nifty 500 minus top 100 → midcap (101-250) or
    # smallcap (251-500). Without exact ranking we
    # approximate: all non-top-100 in Nifty 500 as midcap.
    return "midcap"


def main() -> None:
    """Download and generate nifty500.csv."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    from jugaad_data.nse import NSELive
    nse = NSELive()

    # Fetch index constituents -----------------------------------
    nifty50_syms = _fetch_index(nse, "NIFTY 50")
    time.sleep(1)  # rate limit
    nifty100_syms = _fetch_index(nse, "NIFTY 100")
    time.sleep(1)
    nifty500_data = nse.live_index("NIFTY 500")
    _logger.info(
        "NIFTY 500: %d entries",
        len(nifty500_data.get("data", [])),
    )

    # Build rows from Nifty 500 data -----------------------------
    rows = []
    skipped = 0
    for entry in nifty500_data.get("data", []):
        symbol = entry.get("symbol", "")
        if not symbol:
            continue

        meta = entry.get("meta", {})
        name = meta.get("companyName", "")
        isin = meta.get("isin", "")
        industry = meta.get("industry", "")

        # Skip index summary row
        if not name or not isin:
            skipped += 1
            continue

        # Skip non-EQ series
        series_list = meta.get("activeSeries", [])
        if "EQ" not in series_list:
            skipped += 1
            continue

        # Build tags
        tags = []
        if symbol in nifty50_syms:
            tags.append("nifty50")
        if symbol in nifty100_syms:
            tags.append("nifty100")
        tags.append("nifty500")

        cap = _classify_cap(
            symbol, nifty50_syms, nifty100_syms, set(),
        )
        tags.append(cap)

        # Sector from industry (NSE doesn't give sector
        # directly; yfinance fundamentals job fills it later)
        sector = ""

        rows.append({
            "symbol": symbol,
            "name": name,
            "isin": isin,
            "exchange": "NSE",
            "series": "EQ",
            "sector": sector,
            "industry": industry,
            "tags": "|".join(tags),
        })

    _logger.info(
        "Parsed %d stocks (%d skipped)",
        len(rows), skipped,
    )

    # Sort by symbol for deterministic output --------------------
    rows.sort(key=lambda r: r["symbol"])

    # Write CSV --------------------------------------------------
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    out_dir = os.path.join(project_root, "data", "universe")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "nifty500.csv")

    fieldnames = [
        "symbol", "name", "isin", "exchange",
        "series", "sector", "industry", "tags",
    ]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    _logger.info("Written %d rows to %s", len(rows), out_path)

    # Summary ----------------------------------------------------
    n50 = sum(
        1 for r in rows
        if "nifty50|" in r["tags"]
        or r["tags"].endswith("nifty50")
    )
    n100 = sum(
        1 for r in rows if "nifty100" in r["tags"]
    )
    _logger.info(
        "Tags: %d nifty50, %d nifty100, %d nifty500",
        n50, n100, len(rows),
    )
    _logger.info(
        "Next: PYTHONPATH=.:backend python -m "
        "backend.pipeline.runner seed --csv %s",
        out_path,
    )


if __name__ == "__main__":
    main()
