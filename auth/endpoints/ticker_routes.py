"""User ticker management endpoints.

Provides REST endpoints for linking, unlinking, and listing
ticker symbols associated with the authenticated user.

Endpoints
---------
- ``GET /users/me/tickers`` — list linked tickers
- ``POST /users/me/tickers`` — link a new ticker
- ``DELETE /users/me/tickers/{ticker}`` — unlink a ticker
"""

import logging
import uuid
from datetime import date
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

# validation.py is in backend/ which is on sys.path
from validation import validate_ticker

import auth.endpoints.helpers as _helpers
from auth.dependencies import get_current_user
from auth.models import UserContext

_logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/users/me",
    tags=["tickers"],
)


class LinkTickerRequest(BaseModel):
    """Request body for linking a ticker to the user.

    Attributes:
        ticker: Uppercase ticker symbol (e.g. ``AAPL``).
        source: How the link was created. Defaults
            to ``"manual"``.
    """

    ticker: str = Field(
        ...,
        description="Ticker symbol to link.",
    )
    source: str = Field(
        default="manual",
        description=("How the link was created " "(e.g. 'manual', 'chat')."),
    )


@router.get("/tickers")
def get_user_tickers(
    user: UserContext = Depends(get_current_user),
) -> Dict[str, List[str]]:
    """Return the current user's linked tickers.

    Args:
        user: Authenticated user context from JWT.

    Returns:
        A dict ``{"tickers": ["AAPL", ...]}`` with
        sorted ticker symbols.
    """
    repo = _helpers._get_repo()
    tickers = repo.get_user_tickers(user.user_id)
    _logger.debug(
        "Listed %d tickers for user_id=%s",
        len(tickers),
        user.user_id,
    )
    return {"tickers": tickers}


@router.post("/tickers")
def link_ticker(
    body: LinkTickerRequest,
    user: UserContext = Depends(get_current_user),
) -> Dict[str, object]:
    """Link a ticker symbol to the current user.

    Validates the ticker format, normalises to uppercase,
    and delegates to the repository.

    Args:
        body: Request body with ``ticker`` and optional
            ``source``.
        user: Authenticated user context from JWT.

    Returns:
        ``{"linked": true, "ticker": "AAPL"}`` on success,
        or ``{"linked": false, "detail": "already linked"}``
        if the ticker was already linked.

    Raises:
        HTTPException: 422 if the ticker format is invalid.
    """
    err = validate_ticker(body.ticker)
    if err:
        raise HTTPException(
            status_code=422,
            detail=err,
        )

    ticker = body.ticker.upper().strip()
    repo = _helpers._get_repo()
    try:
        linked = repo.link_ticker(
            user.user_id,
            ticker,
            body.source,
        )
    except RuntimeError as exc:
        _logger.error("link_ticker failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=(
                "Ticker storage unavailable."
                " Run: python auth/create_tables.py"
            ),
        ) from exc

    if linked:
        _logger.info(
            "User %s linked ticker=%s source=%s",
            user.user_id,
            ticker,
            body.source,
        )
        return {"linked": True, "ticker": ticker}

    return {
        "linked": False,
        "detail": "already linked",
    }


@router.delete("/tickers/{ticker}")
def unlink_ticker(
    ticker: str,
    user: UserContext = Depends(get_current_user),
) -> Dict[str, str]:
    """Unlink a ticker symbol from the current user.

    Args:
        ticker: Ticker symbol from the URL path.
        user: Authenticated user context from JWT.

    Returns:
        ``{"detail": "unlinked"}`` on success.

    Raises:
        HTTPException: 404 if the ticker was not linked.
    """
    normalised = ticker.upper().strip()
    repo = _helpers._get_repo()
    try:
        removed = repo.unlink_ticker(
            user.user_id,
            normalised,
        )
    except RuntimeError as exc:
        _logger.error("unlink_ticker failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=(
                "Ticker storage unavailable."
                " Run: python auth/create_tables.py"
            ),
        ) from exc

    if not removed:
        raise HTTPException(
            status_code=404,
            detail=f"Ticker '{normalised}' not linked",
        )

    _logger.info(
        "User %s unlinked ticker=%s",
        user.user_id,
        normalised,
    )
    return {"detail": "unlinked"}


# ---------------------------------------------------------------
# User Preferences (localStorage + Redis sync)
# ---------------------------------------------------------------

_PREFS_TTL = 7 * 86400  # 7 days sliding TTL


@router.get("/preferences")
def get_preferences(
    user: UserContext = Depends(get_current_user),
) -> Dict:
    """Return stored preferences for the current user.

    Reads from Redis with key ``prefs:{user_id}``.
    Returns empty dict if no preferences are stored.
    Extends the sliding TTL on every read.
    """
    import json

    try:
        from cache import get_cache
    except ImportError:
        return {}

    cache = get_cache()
    key = f"prefs:{user.user_id}"
    raw = cache.get(key)
    if raw is None:
        return {}
    try:
        prefs = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    # Extend sliding TTL on read
    cache.set(key, raw, _PREFS_TTL)
    return prefs


class PreferencesBody(BaseModel):
    """Full preferences payload from the frontend."""

    chart: Dict | None = None
    dashboard: Dict | None = None
    insights: Dict | None = None
    admin: Dict | None = None
    navigation: Dict | None = None
    last_login: str | None = None


@router.put("/preferences")
def put_preferences(
    body: PreferencesBody,
    user: UserContext = Depends(get_current_user),
) -> Dict[str, str]:
    """Upsert preferences for the current user.

    Merges with existing preferences so partial
    updates are supported.  Sets a sliding 7-day TTL.
    """
    import json

    try:
        from cache import get_cache
    except ImportError:
        return {"detail": "cache unavailable"}

    cache = get_cache()
    key = f"prefs:{user.user_id}"

    # Merge with existing
    existing: dict = {}
    raw = cache.get(key)
    if raw:
        try:
            existing = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            pass

    incoming = body.model_dump(exclude_none=True)
    for section, values in incoming.items():
        if isinstance(values, dict) and isinstance(
            existing.get(section), dict
        ):
            existing[section].update(values)
        else:
            existing[section] = values

    cache.set(
        key, json.dumps(existing), _PREFS_TTL,
    )
    _logger.info(
        "Preferences saved for user %s",
        user.user_id,
    )
    return {"detail": "saved"}


# ---------------------------------------------------------------
# Portfolio holdings (CRUD)
# ---------------------------------------------------------------

def _get_stock_repo():
    """Lazy import to avoid circular deps."""
    from tools._stock_shared import _require_repo
    return _require_repo()


class AddPortfolioRequest(BaseModel):
    """Add a stock to the portfolio."""

    ticker: str
    quantity: float
    price: float
    trade_date: str  # YYYY-MM-DD
    notes: str = ""


class EditPortfolioRequest(BaseModel):
    """Edit portfolio transaction fields."""

    quantity: float | None = None
    price: float | None = None
    trade_date: str | None = None


@router.get("/portfolio")
def get_portfolio(
    user: UserContext = Depends(get_current_user),
) -> Dict[str, Any]:
    """Return computed holdings + raw transactions."""
    stock_repo = _get_stock_repo()
    holdings_df = stock_repo.get_portfolio_holdings(
        user.user_id,
    )

    # Get raw transactions for transaction_id lookup
    txn_df = stock_repo.get_portfolio_transactions(
        user.user_id,
    )
    # Map ticker → latest transaction_id
    txn_id_map: Dict[str, str] = {}
    if not txn_df.empty and (
        "transaction_id" in txn_df.columns
    ):
        for _, t in txn_df.iterrows():
            txn_id_map[str(t["ticker"])] = str(
                t["transaction_id"]
            )

    # Enrich with current prices from OHLCV
    holdings = []
    for _, row in holdings_df.iterrows():
        ticker = str(row["ticker"])
        current_price = None
        try:
            ohlcv = stock_repo.get_ohlcv(ticker)
            if not ohlcv.empty:
                valid = ohlcv.dropna(
                    subset=["close"],
                )
                if not valid.empty:
                    current_price = float(
                        valid.iloc[-1]["close"]
                    )
        except Exception:
            pass

        qty = float(row["quantity"])
        avg = float(row["avg_price"])
        invested = round(qty * avg, 2)
        current_val = (
            round(qty * current_price, 2)
            if current_price
            else None
        )
        gain_pct = (
            round(
                (
                    (current_price - avg)
                    / avg
                    * 100
                ),
                2,
            )
            if current_price and avg
            else None
        )

        holdings.append({
            "ticker": ticker,
            "transaction_id": txn_id_map.get(
                ticker, ""
            ),
            "quantity": qty,
            "avg_price": round(avg, 2),
            "current_price": current_price,
            "currency": str(
                row.get("currency", "USD")
            ),
            "market": str(
                row.get("market", "us")
            ),
            "invested": invested,
            "current_value": current_val,
            "gain_loss_pct": gain_pct,
        })

    # Portfolio totals per currency
    totals: Dict[str, float] = {}
    for h in holdings:
        ccy = h["currency"]
        val = h["current_value"] or 0
        totals[ccy] = totals.get(ccy, 0) + val

    return {
        "holdings": holdings,
        "totals": totals,
    }


@router.post("/portfolio")
def add_portfolio_holding(
    body: AddPortfolioRequest,
    user: UserContext = Depends(get_current_user),
) -> Dict[str, str]:
    """Add a stock to the user's portfolio."""
    ticker = body.ticker.upper().strip()
    mkt = (
        "india"
        if ticker.endswith((".NS", ".BO"))
        else "us"
    )
    ccy = "INR" if mkt == "india" else "USD"

    stock_repo = _get_stock_repo()
    txn = {
        "transaction_id": str(uuid.uuid4()),
        "user_id": user.user_id,
        "ticker": ticker,
        "side": "BUY",
        "quantity": body.quantity,
        "price": body.price,
        "currency": ccy,
        "market": mkt,
        "trade_date": date.fromisoformat(
            body.trade_date,
        ),
        "fees": 0,
        "notes": body.notes,
    }
    stock_repo.add_portfolio_transaction(txn)

    # Invalidate portfolio caches
    try:
        from cache import get_cache
        cache = get_cache()
        cache.invalidate(
            f"cache:portfolio:{user.user_id}",
        )
        cache.invalidate(
            f"cache:portfolio:perf:"
            f"{user.user_id}:*",
        )
        cache.invalidate(
            f"cache:portfolio:forecast:"
            f"{user.user_id}:*",
        )
    except ImportError:
        pass

    _logger.info(
        "Portfolio: user %s added %s qty=%.2f"
        " price=%.2f",
        user.user_id,
        ticker,
        body.quantity,
        body.price,
    )
    return {
        "detail": "added",
        "transaction_id": txn["transaction_id"],
    }


@router.put("/portfolio/{transaction_id}")
def edit_portfolio_holding(
    transaction_id: str,
    body: EditPortfolioRequest,
    user: UserContext = Depends(get_current_user),
) -> Dict[str, str]:
    """Edit price, quantity, or trade_date."""
    updates: dict = {}
    if body.quantity is not None:
        updates["quantity"] = body.quantity
    if body.price is not None:
        updates["price"] = body.price
    if body.trade_date is not None:
        updates["trade_date"] = (
            date.fromisoformat(body.trade_date)
        )

    if not updates:
        raise HTTPException(
            status_code=422,
            detail="No fields to update",
        )

    stock_repo = _get_stock_repo()
    ok = stock_repo.update_portfolio_transaction(
        transaction_id, user.user_id, updates,
    )
    if not ok:
        raise HTTPException(
            status_code=404,
            detail="Transaction not found",
        )

    # Invalidate portfolio caches
    try:
        from cache import get_cache
        cache = get_cache()
        cache.invalidate(
            f"cache:portfolio:perf:"
            f"{user.user_id}:*",
        )
        cache.invalidate(
            f"cache:portfolio:forecast:"
            f"{user.user_id}:*",
        )
    except ImportError:
        pass

    return {"detail": "updated"}


@router.delete("/portfolio/{transaction_id}")
def delete_portfolio_holding(
    transaction_id: str,
    user: UserContext = Depends(get_current_user),
) -> Dict[str, str]:
    """Delete a portfolio transaction."""
    stock_repo = _get_stock_repo()
    ok = stock_repo.delete_portfolio_transaction(
        transaction_id, user.user_id,
    )
    if not ok:
        raise HTTPException(
            status_code=404,
            detail="Transaction not found",
        )

    # Invalidate portfolio caches
    try:
        from cache import get_cache
        cache = get_cache()
        cache.invalidate(
            f"cache:portfolio:perf:"
            f"{user.user_id}:*",
        )
        cache.invalidate(
            f"cache:portfolio:forecast:"
            f"{user.user_id}:*",
        )
    except ImportError:
        pass

    return {"detail": "deleted"}
