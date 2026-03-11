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
from typing import Dict, List

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
