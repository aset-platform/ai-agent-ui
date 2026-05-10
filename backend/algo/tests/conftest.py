"""Pytest configuration for algo trading unit tests.

Provides a module-level stub for ``kiteconnect`` so that the
``KiteClient`` wrapper can be imported without the real SDK being
installed.  In production the SDK is installed in the Docker
container (see requirements.txt).

The stub is deliberately minimal — tests that exercise broker
paths mock ``KiteConnect`` further via ``unittest.mock.patch``.
"""
from __future__ import annotations

import sys
import types

# ---------------------------------------------------------------
# kiteconnect stub
# ---------------------------------------------------------------
# Create a minimal stub that satisfies the ``from kiteconnect
# import KiteConnect`` import used by kite_client.py.

_kc_stub = types.ModuleType("kiteconnect")


class _StubKiteConnect:
    """Minimal stand-in.  Real methods are replaced by mocks
    in individual test fixtures."""

    def __init__(self, api_key: str = "") -> None:
        self.api_key = api_key

    def set_access_token(self, token: str) -> None:
        pass

    def login_url(self) -> str:
        return "https://kite.zerodha.com/connect/login?stub=1"

    def generate_session(
        self, request_token: str, *, api_secret: str,
    ) -> dict:
        return {"access_token": "stub_token"}

    def profile(self) -> dict:
        return {"user_id": "STUB", "user_name": "Stub User"}

    def instruments(self, exchange: str | None = None) -> list:
        return []

    def positions(self) -> dict:
        return {"net": [], "day": []}

    def place_order(self, variety: str = "regular", **kwargs) -> dict:
        return {"order_id": "STUB_ORDER"}

    def cancel_order(
        self, variety: str = "regular", order_id: str = "",
    ) -> dict:
        return {"order_id": order_id}

    def modify_order(
        self, variety: str = "regular", order_id: str = "",
        **kwargs,
    ) -> dict:
        return {"order_id": order_id}

    def orders(self) -> list:
        return []


class _StubKiteTicker:
    """Minimal KiteTicker stub — tests that exercise the WS path
    monkey-patch ``kiteconnect.KiteTicker`` with their own shim
    (``mock_kite_ws_server.KiteTickerShim``). The class only needs
    to be a real attribute on the stub module so ``unittest.mock.
    patch("kiteconnect.KiteTicker", ...)`` can resolve it."""

    MODE_LTP = "ltp"
    MODE_QUOTE = "quote"
    MODE_FULL = "full"

    def __init__(self, api_key: str = "", access_token: str = "") -> None:
        self.api_key = api_key
        self.access_token = access_token

    def connect(self, threaded: bool = True) -> None:
        pass

    def close(self) -> None:
        pass

    def subscribe(self, tokens) -> None:
        pass

    def unsubscribe(self, tokens) -> None:
        pass

    def set_mode(self, mode, tokens) -> None:
        pass


_kc_stub.KiteConnect = _StubKiteConnect  # type: ignore[attr-defined]
_kc_stub.KiteTicker = _StubKiteTicker  # type: ignore[attr-defined]
sys.modules.setdefault("kiteconnect", _kc_stub)
