"""Thread-safe background-refresh state manager.

Replaces the module-level mutable ``_executor`` / ``_*_future`` dicts
that previously lived in ``home_cbs``, ``analysis_cbs``, and
``forecast_cbs``.  All mutation is guarded by a :class:`threading.Lock`
so concurrent Dash callbacks cannot corrupt the future map.

Usage::

    mgr = RefreshManager(max_workers=2)
    # inside a Dash callback:
    if mgr.submit_if_idle("AAPL", run_full_refresh, "AAPL", 9):
        ...  # show spinner
    for ticker, fut in mgr.harvest_done():
        ...  # process result
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor

_logger = logging.getLogger(__name__)


class RefreshManager:
    """Thread-safe manager for background refresh futures.

    Each instance owns a :class:`~concurrent.futures.ThreadPoolExecutor`
    and a ``{ticker: Future}`` map protected by a lock.

    Args:
        max_workers: Maximum threads in the pool.
    """

    def __init__(self, max_workers: int = 2) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._futures: dict[str, Future] = {}
        self._lock = threading.Lock()

    # ----------------------------------------------------------
    # Public API
    # ----------------------------------------------------------

    def submit_if_idle(
        self,
        ticker: str,
        fn,
        *args,
    ) -> bool:
        """Submit *fn* if no in-flight future for *ticker*.

        Args:
            ticker: Stock ticker key.
            fn: Callable to run in the background.
            *args: Positional args forwarded to *fn*.

        Returns:
            ``True`` if a new future was submitted,
            ``False`` if one is already running.
        """
        with self._lock:
            existing = self._futures.get(ticker)
            if existing and not existing.done():
                return False
            self._futures[ticker] = self._executor.submit(fn, *args)
        return True

    def get(self, ticker: str) -> Future | None:
        """Return the future for *ticker*, or ``None``.

        Args:
            ticker: Stock ticker key.
        """
        with self._lock:
            return self._futures.get(ticker)

    def harvest_done(self) -> list[tuple[str, Future]]:
        """Pop and return all completed futures.

        Returns:
            List of ``(ticker, future)`` pairs that are done.
        """
        with self._lock:
            done = [(k, v) for k, v in self._futures.items() if v.done()]
            for k, _ in done:
                del self._futures[k]
        return done

    def pop(self, ticker: str) -> Future | None:
        """Remove and return the future for *ticker*.

        Args:
            ticker: Stock ticker key.

        Returns:
            The removed future or ``None``.
        """
        with self._lock:
            return self._futures.pop(ticker, None)
