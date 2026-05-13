"""Pytest configuration for algo-jobs unit tests.

Pytest discovers ``conftest.py`` files only along the directory
ancestry of the test module, so the kiteconnect SDK stub in
``backend/algo/tests/conftest.py`` (which is a *sibling* of this
directory) is invisible here. Re-stub it locally so importing
``backend.algo.broker.kite_client`` from a jobs test doesn't blow
up on a missing real SDK in the local venv (the SDK is only
installed inside the Docker container).
"""

from __future__ import annotations

import sys
import types

if "kiteconnect" not in sys.modules:
    _kc_stub = types.ModuleType("kiteconnect")

    class _StubKiteConnect:
        def __init__(self, api_key: str = "") -> None:
            self.api_key = api_key

        def set_access_token(self, token: str) -> None:
            pass

    class _StubKiteTicker:
        MODE_LTP = "ltp"
        MODE_QUOTE = "quote"
        MODE_FULL = "full"

        def __init__(
            self,
            api_key: str = "",
            access_token: str = "",
        ) -> None:
            self.api_key = api_key
            self.access_token = access_token

    _kc_stub.KiteConnect = _StubKiteConnect  # type: ignore[attr-defined]
    _kc_stub.KiteTicker = _StubKiteTicker  # type: ignore[attr-defined]
    sys.modules["kiteconnect"] = _kc_stub
