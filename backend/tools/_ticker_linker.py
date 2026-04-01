"""Auto-link tickers to the requesting user after tool execution.

Uses thread-local storage to pass the current ``user_id`` from the
HTTP handler into the stock tool functions without changing their
LangChain ``@tool`` signatures.

Typical flow::

    # In the HTTP handler (main.py):
    set_current_user(req.user_id)

    # Inside a stock tool:
    auto_link_ticker(ticker)
"""

from __future__ import annotations

import logging
import threading

_logger = logging.getLogger(__name__)

# Thread-local storage for the current user_id.
_local = threading.local()


def set_current_user(user_id: str | None) -> None:
    """Set the current user ID for auto-linking.

    Args:
        user_id: Authenticated user's UUID, or ``None``
            if the request is unauthenticated.
    """
    _local.user_id = user_id


def get_current_user() -> str | None:
    """Return the current user ID, or ``None`` if unset.

    Returns:
        The user ID stored in thread-local state, or
        ``None`` if :func:`set_current_user` was not
        called in this thread.
    """
    return getattr(_local, "user_id", None)


def auto_link_ticker(ticker: str) -> None:
    """Auto-link a ticker to the current user if set.

    This function is intentionally fire-and-forget: it
    must **never** block or fail the calling tool.

    Args:
        ticker: Uppercase ticker symbol to link.
    """
    user_id = get_current_user()
    if not user_id:
        _logger.debug(
            "auto_link_ticker(%s): no current user",
            ticker,
        )
        return
    try:
        import asyncio

        from auth.endpoints.helpers import _get_repo

        repo = _get_repo()

        async def _link():
            return await repo.link_ticker(
                user_id, ticker, source="chat",
            )

        linked = asyncio.run(_link())
        if linked:
            _logger.info(
                "Auto-linked %s to user %s",
                ticker,
                user_id,
            )
    except Exception as exc:
        _logger.warning(
            "Auto-link failed for %s/%s: %s",
            user_id,
            ticker,
            exc,
        )
