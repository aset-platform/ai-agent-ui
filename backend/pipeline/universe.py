"""Stock universe CRUD operations for stock_master + stock_tags."""
import logging

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models.stock_master import StockMaster
from backend.db.models.stock_tag import StockTag

_logger = logging.getLogger(__name__)


async def get_stock_by_symbol(
    session: AsyncSession,
    symbol: str,
) -> StockMaster | None:
    """Lookup stock_master by symbol."""
    stmt = select(StockMaster).where(
        StockMaster.symbol == symbol,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_all_stocks(
    session: AsyncSession,
    active_only: bool = True,
) -> list[StockMaster]:
    """Get all stocks, optionally filtered to active."""
    stmt = select(StockMaster)
    if active_only:
        stmt = stmt.where(StockMaster.is_active.is_(True))
    stmt = stmt.order_by(StockMaster.symbol)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_active_tags(
    session: AsyncSession,
    stock_id: int,
) -> list[StockTag]:
    """Get active tags (removed_at IS NULL) for a stock."""
    stmt = (
        select(StockTag)
        .where(
            StockTag.stock_id == stock_id,
            StockTag.removed_at.is_(None),
        )
        .order_by(StockTag.tag)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def upsert_stock(
    session: AsyncSession,
    data: dict,
) -> tuple[StockMaster, bool]:
    """Insert or update stock_master. Returns (stock, is_new).

    If existing: update sector, industry if changed.
    If new: insert with derived yf_ticker={symbol}.NS,
    nse_symbol=symbol.
    """
    symbol = data["symbol"]
    existing = await get_stock_by_symbol(session, symbol)

    if existing:
        changed = False
        for field in ("sector", "industry"):
            new_val = data.get(field)
            if new_val and getattr(existing, field) != new_val:
                setattr(existing, field, new_val)
                changed = True
        # Also update name/isin/exchange if provided
        for field in ("name", "isin", "exchange"):
            new_val = data.get(field)
            if new_val and getattr(existing, field) != new_val:
                setattr(existing, field, new_val)
                changed = True
        if changed:
            existing.updated_at = func.now()
        return existing, False

    stock = StockMaster(
        symbol=symbol,
        name=data["name"],
        isin=data.get("isin"),
        exchange=data.get("exchange", "NSE"),
        yf_ticker=data.get(
            "yf_ticker", f"{symbol}.NS",
        ),
        nse_symbol=data.get("nse_symbol", symbol),
        sector=data.get("sector"),
        industry=data.get("industry"),
        market_cap=data.get("market_cap"),
        currency=data.get("currency", "INR"),
        is_active=True,
    )
    session.add(stock)
    await session.flush()
    return stock, True


async def sync_tags(
    session: AsyncSession,
    stock_id: int,
    new_tags: set[str],
) -> dict:
    """Reconcile tags for a stock.

    - New tags: insert with added_at=now()
    - Missing tags: set removed_at=now()
    - Existing unchanged: no-op

    Returns {"added": [...], "removed": [...]}.
    """
    active = await get_active_tags(session, stock_id)
    current_tags = {t.tag for t in active}
    tag_map = {t.tag: t for t in active}

    to_add = new_tags - current_tags
    to_remove = current_tags - new_tags

    added = []
    for tag_name in sorted(to_add):
        tag = StockTag(
            stock_id=stock_id,
            tag=tag_name,
        )
        session.add(tag)
        added.append(tag_name)

    removed = []
    for tag_name in sorted(to_remove):
        tag_obj = tag_map[tag_name]
        tag_obj.removed_at = func.now()
        removed.append(tag_name)

    return {"added": added, "removed": removed}
