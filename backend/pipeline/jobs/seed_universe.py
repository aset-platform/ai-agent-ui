"""Seed job: populate stock_master + stock_tags from CSV."""
import csv
import logging
import os

from sqlalchemy import select

from backend.db.engine import get_session_factory
from backend.db.models.ingestion_cursor import IngestionCursor
from backend.pipeline.universe import sync_tags, upsert_stock

_logger = logging.getLogger(__name__)


async def seed_from_csv(
    csv_path: str,
    update: bool = False,
) -> dict:
    """Parse CSV, seed/update stock_master + stock_tags.

    CSV format::

        symbol,name,isin,exchange,series,sector,industry,tags
        RELIANCE,Reliance Industries,...,nifty50|nifty100|largecap

    Args:
        csv_path: Path to the CSV file.
        update: If True, update existing stocks + reconcile
                tags.  If False, skip existing stocks.

    Returns:
        Summary dict with counts and errors.
    """
    summary: dict = {
        "inserted": 0,
        "updated": 0,
        "skipped": 0,
        "tags_added": 0,
        "tags_removed": 0,
        "errors": [],
    }

    rows = _read_csv(csv_path, summary)
    if not rows:
        return summary

    factory = get_session_factory()
    async with factory() as session:
        async with session.begin():
            new_ids = await _process_rows(
                session, rows, update, summary,
            )
            cursor_name = _derive_cursor_name(csv_path)
            await _ensure_cursor(
                session,
                cursor_name,
                len(rows),
                new_ids,
                update,
            )

    _logger.info(
        "seed_from_csv done: inserted=%d updated=%d "
        "skipped=%d tags_added=%d tags_removed=%d "
        "errors=%d",
        summary["inserted"],
        summary["updated"],
        summary["skipped"],
        summary["tags_added"],
        summary["tags_removed"],
        len(summary["errors"]),
    )
    return summary


def _read_csv(csv_path: str, summary: dict) -> list[dict]:
    """Read and validate CSV rows."""
    rows: list[dict] = []
    try:
        with open(csv_path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for i, row in enumerate(reader, start=2):
                symbol = (row.get("symbol") or "").strip()
                name = (row.get("name") or "").strip()
                if not symbol or not name:
                    msg = (
                        f"Row {i}: missing symbol or name, "
                        "skipped"
                    )
                    _logger.warning(msg)
                    summary["errors"].append(msg)
                    continue
                isin = (row.get("isin") or "").strip()
                if not isin:
                    _logger.warning(
                        "Row %d (%s): missing ISIN",
                        i, symbol,
                    )
                rows.append(row)
    except FileNotFoundError:
        msg = f"CSV not found: {csv_path}"
        _logger.error(msg)
        summary["errors"].append(msg)
    return rows


async def _process_rows(
    session,
    rows: list[dict],
    update: bool,
    summary: dict,
) -> list[int]:
    """Upsert stocks and sync tags. Return new stock ids."""
    new_ids: list[int] = []

    for row in rows:
        symbol = row["symbol"].strip()
        data = {
            "symbol": symbol,
            "name": row["name"].strip(),
            "isin": (row.get("isin") or "").strip() or None,
            "exchange": (
                (row.get("exchange") or "").strip() or "NSE"
            ),
            "sector": (
                (row.get("sector") or "").strip() or None
            ),
            "industry": (
                (row.get("industry") or "").strip() or None
            ),
        }

        try:
            stock, is_new = await upsert_stock(session, data)
        except Exception as exc:
            msg = f"{symbol}: upsert failed — {exc}"
            _logger.error(msg)
            summary["errors"].append(msg)
            continue

        if is_new:
            summary["inserted"] += 1
            new_ids.append(stock.id)
        elif update:
            summary["updated"] += 1
        else:
            summary["skipped"] += 1
            continue

        # Sync tags for new stocks or when updating
        raw_tags = (row.get("tags") or "").strip()
        tag_set = {
            t.strip().lower()
            for t in raw_tags.split("|")
            if t.strip()
        }
        if tag_set:
            try:
                result = await sync_tags(
                    session, stock.id, tag_set,
                )
                summary["tags_added"] += len(
                    result["added"],
                )
                summary["tags_removed"] += len(
                    result["removed"],
                )
            except Exception as exc:
                msg = f"{symbol}: tag sync failed — {exc}"
                _logger.error(msg)
                summary["errors"].append(msg)

    return new_ids


def _derive_cursor_name(csv_path: str) -> str:
    """Derive cursor name from CSV filename."""
    base = os.path.splitext(os.path.basename(csv_path))[0]
    return f"{base}_bulk"


async def _ensure_cursor(
    session,
    cursor_name: str,
    total: int,
    new_ids: list[int],
    update: bool,
) -> None:
    """Create IngestionCursor if appropriate.

    On update mode: only create if new tickers were added.
    """
    if update and not new_ids:
        return

    stmt = select(IngestionCursor).where(
        IngestionCursor.cursor_name == cursor_name,
    )
    result = await session.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        existing.total_tickers = total
        existing.status = "pending"
        _logger.info(
            "Updated cursor %s (total=%d)",
            cursor_name, total,
        )
    else:
        cursor = IngestionCursor(
            cursor_name=cursor_name,
            total_tickers=total,
            last_processed_id=0,
            batch_size=50,
            status="pending",
        )
        session.add(cursor)
        _logger.info(
            "Created cursor %s (total=%d)",
            cursor_name, total,
        )
