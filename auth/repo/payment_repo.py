"""Payment transaction operations — PostgreSQL via SQLAlchemy."""
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models.payment import PaymentTransaction

log = logging.getLogger(__name__)


async def record_transaction(
    session: AsyncSession,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Insert a payment transaction record."""
    txn = PaymentTransaction(
        transaction_id=data.get(
            "transaction_id", str(uuid.uuid4()),
        ),
        user_id=data["user_id"],
        gateway=data["gateway"],
        event_type=data["event_type"],
        gateway_event_id=data.get("gateway_event_id"),
        subscription_id=data.get("subscription_id"),
        customer_id=data.get("customer_id"),
        amount=data.get("amount"),
        currency=data.get("currency"),
        tier_before=data.get("tier_before"),
        tier_after=data.get("tier_after"),
        status=data["status"],
        raw_payload=data.get("raw_payload"),
        created_at=datetime.now(timezone.utc),
    )
    session.add(txn)
    await session.commit()
    await session.refresh(txn)
    log.info(
        "Recorded %s txn %s",
        data["gateway"], txn.transaction_id,
    )
    return {
        c.name: getattr(txn, c.name)
        for c in txn.__table__.columns
    }


async def update_status(
    session: AsyncSession,
    transaction_id: str,
    status: str,
) -> dict[str, Any]:
    """Update transaction status (reconciliation)."""
    result = await session.execute(
        select(PaymentTransaction).where(
            PaymentTransaction.transaction_id
            == transaction_id
        )
    )
    txn = result.scalar_one_or_none()
    if not txn:
        raise ValueError(
            f"Transaction {transaction_id} not found"
        )

    txn.status = status
    await session.commit()
    await session.refresh(txn)
    log.info(
        "Updated txn %s status to %s",
        transaction_id, status,
    )
    return {
        c.name: getattr(txn, c.name)
        for c in txn.__table__.columns
    }


async def get_by_user(
    session: AsyncSession,
    user_id: str,
) -> list[dict[str, Any]]:
    """Return all transactions for a user."""
    result = await session.execute(
        select(PaymentTransaction).where(
            PaymentTransaction.user_id == user_id
        )
    )
    return [
        {
            c.name: getattr(t, c.name)
            for c in t.__table__.columns
        }
        for t in result.scalars().all()
    ]
