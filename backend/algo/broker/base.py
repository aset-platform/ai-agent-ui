# backend/algo/broker/base.py
"""Broker adapter ABC — single interface for SimBroker (v1 backtest /
paper) and KiteAdapter (v1 read-only ticks; v2 live).

The v1 ``KiteAdapter`` only implements read paths
(``profile``, ``stream_ticks``, ``instruments``); ``place_order``
intentionally raises so live trading can't slip in by accident.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator


class BrokerAdapter(ABC):
    """Common interface across SimBroker / KiteAdapter."""

    @abstractmethod
    def place_order(self, intent) -> str:  # noqa: ANN001
        """Submit an order. Raises NotImplementedError in v1."""

    @abstractmethod
    def cancel_order(self, order_id: str) -> None: ...

    @abstractmethod
    def get_positions(self) -> list[dict]: ...

    @abstractmethod
    async def stream_ticks(
        self, symbols: list[str],
    ) -> AsyncIterator[dict]: ...
