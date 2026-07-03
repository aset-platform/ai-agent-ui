"""hydrate() must recover from Kite's Content-Type-mismatch
DataException instead of falling back to positions-only/holdings-
only, per the same bug documented in
test_kite_client_content_type_recovery.py.
"""
from __future__ import annotations

import importlib
import sys
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from kiteconnect.exceptions import DataException

_RUNTIME_AVAILABLE = (
    importlib.util.find_spec("pyarrow") is not None
    and sys.version_info >= (3, 10)
)

pytestmark = pytest.mark.skipif(
    not _RUNTIME_AVAILABLE,
    reason="Requires the backend Docker stack (pyarrow + py>=3.10)",
)


def _content_type_exception(body: str) -> DataException:
    return DataException(
        f"Unknown Content-Type (text/plain; charset=utf-8) with "
        f"response: (b'{body}')",
    )


def _strategy():
    strategy = MagicMock()
    strategy.id = uuid4()
    return strategy


class TestHydrationContentTypeRecovery:
    def test_recovers_positions_despite_content_type_mismatch(self):
        from backend.algo.live.position_hydration import hydrate

        kite = MagicMock()
        kite._kc.positions.side_effect = _content_type_exception(
            '{"status":"success","data":{"net":[{"tradingsymbol":'
            '"MMTC","quantity":42,"product":"CNC",'
            '"average_price":68.84}]}}',
        )
        kite._kc.holdings.return_value = []

        out = hydrate(
            kite, _strategy(), user_id=uuid4(),
            events_reader=lambda uid, sym: None,
        )

        assert len(out) == 1
        assert out[0].symbol == "MMTC.NS"
        assert out[0].qty == 42
        assert out[0].source == "positions"

    def test_recovers_holdings_despite_content_type_mismatch(self):
        from backend.algo.live.position_hydration import hydrate

        kite = MagicMock()
        kite._kc.positions.return_value = {"net": []}
        kite._kc.holdings.side_effect = _content_type_exception(
            '{"status":"success","data":[{"tradingsymbol":'
            '"ANGELONE","quantity":3,"t1_quantity":0,'
            '"average_price":328.4}]}',
        )

        out = hydrate(
            kite, _strategy(), user_id=uuid4(),
            events_reader=lambda uid, sym: None,
        )

        assert len(out) == 1
        assert out[0].symbol == "ANGELONE.NS"
        assert out[0].qty == 3
        assert out[0].source == "holdings"

    def test_genuine_failure_still_falls_back_gracefully(self):
        """A non-recoverable error must still degrade gracefully
        (positions-only / holdings-only), not raise."""
        from backend.algo.live.position_hydration import hydrate

        kite = MagicMock()
        kite._kc.positions.side_effect = RuntimeError("kite down")
        kite._kc.holdings.return_value = [{
            "tradingsymbol": "MMTC", "quantity": 42, "t1_quantity": 0,
            "average_price": 68.84,
        }]

        out = hydrate(
            kite, _strategy(), user_id=uuid4(),
            events_reader=lambda uid, sym: None,
        )

        assert len(out) == 1
        assert out[0].symbol == "MMTC.NS"
        assert out[0].source == "holdings"
