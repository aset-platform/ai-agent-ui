# Algo Trading — Session 3: Kite OAuth + Instrument Master

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land Slices 2 + 3 from the Algo Trading epic spec — per-user Kite OAuth handshake (read-only; no orders) + daily 05:30 IST re-auth notification + the Instrument Master populated from Kite's `/instruments` dump with a searchable read-only frontend tab.

**Architecture:** Per-user Kite credentials encrypted at rest with the existing `BYO_SECRET_KEY` Fernet (reused from `backend/crypto/byo_secrets.py`). OAuth flow follows Kite's documented `request_token → checksum → access_token` exchange. Instrument list pulled once per day via a single shared call (any connected user's access_token suffices) and persisted to `algo.instruments` (already migrated in Session 1).

**Tech Stack:** Python 3.12 / FastAPI / `kiteconnect` SDK / Pydantic 2 / pytest. Next.js 16 / React 19 / SWR / vitest. Fernet (`cryptography`) reused.

**Spec:** `docs/superpowers/specs/2026-05-08-algo-trading-platform-design.md` (§ 7 Broker Abstraction)

**Branch:** `feature/algo-trading-session-3-kite-instruments` (already cut off Session 2's tip `459f687`).

**Conventions reminders:**
- Branch off `dev`; squash-only merge; Co-Authored-By Abhay; line length 79; `X | None`; `_logger`; backend restart after route/decorator changes; Redis FLUSHALL after cache code changes.
- Reuse existing Fernet helpers (`encrypt_key`, `decrypt_key` from `backend.crypto.byo_secrets`) — do NOT introduce a second master key.
- Job registration via `@register_job("type_name")` in `backend/jobs/executor.py` style.
- Auth dependency `pro_or_superuser` from `auth.dependencies`; user context from `auth.models.UserContext`.

---

## File Structure

### Slice 2 — Kite OAuth + broker credentials

**Backend (new):**
- `backend/algo/broker/__init__.py`
- `backend/algo/broker/base.py` — `BrokerAdapter` ABC (placeholder for Slice 6+).
- `backend/algo/broker/kite_client.py` — thin `KiteClient` wrapper around the official `kiteconnect.KiteConnect` (login_url, generate_session, profile fetch). v1 = read-only; `place_order` raises `NotImplementedError("Live trading is v2")`.
- `backend/algo/broker/credentials_repo.py` — async CRUD over `algo.broker_credentials`; encrypts api_key + access_token with the existing Fernet.
- `backend/algo/routes/broker.py` — endpoints: `POST /algo/broker/api-key`, `GET /algo/broker/login`, `GET /algo/broker/callback`, `GET /algo/broker/status`, `DELETE /algo/broker`.
- `backend/algo/jobs/__init__.py`
- `backend/algo/jobs/reauth_notify.py` — daily 05:30 IST job that scans for credentials whose `access_token_expires_at` is past or imminent, emits a `broker_reauth_required` audit event per affected user.
- `backend/algo/tests/test_broker_credentials_repo.py` — encrypt round-trip + CRUD smokes.
- `backend/algo/tests/test_broker_routes.py` — endpoint smokes (mocked KiteConnect SDK).
- `backend/algo/tests/test_reauth_notify.py` — job-level test.

**Backend (modified):**
- `backend/algo/routes/__init__.py` — re-export `create_broker_router`.
- `backend/routes.py` — register the broker router.
- `backend/jobs/executor.py` — register the new `algo_kite_reauth_notify` job type.
- `requirements.txt` — pin `kiteconnect==5.0.1` (or latest 5.x).
- `Dockerfile.backend` — no-op (pip install runs from requirements.txt).

**Frontend (new):**
- `frontend/lib/types/algoBroker.ts` — `BrokerStatus` literal, `BrokerProfile` shape.
- `frontend/hooks/useBrokerStatus.ts` — SWR hook polling `/v1/algo/broker/status` every 60 s.
- `frontend/components/algo-trading/ConnectBrokerTab.tsx` — full tab content.
- `frontend/components/algo-trading/__tests__/ConnectBrokerTab.test.tsx` — vitest.

**Frontend (modified):**
- `frontend/app/(authenticated)/algo-trading/AlgoTradingClient.tsx` — `case "connect"` returns `<ConnectBrokerTab />`.

### Slice 3 — Instrument Master

**Backend (new):**
- `backend/algo/instruments/__init__.py`
- `backend/algo/instruments/repo.py` — async list/upsert over `algo.instruments`.
- `backend/algo/instruments/loader.py` — pulls `KiteClient.instruments()` and bulk-upserts.
- `backend/algo/jobs/instrument_refresh.py` — `@register_job("algo_kite_instruments_refresh")` daily 07:00 IST.
- `backend/algo/routes/instruments.py` — `GET /algo/instruments` (search/filter/paginate), `POST /algo/instruments/refresh` (manual trigger).
- `backend/algo/tests/test_instruments_repo.py`
- `backend/algo/tests/test_instruments_routes.py`

**Backend (modified):**
- `backend/algo/routes/__init__.py` — re-export `create_instruments_router`.
- `backend/routes.py` — register the instruments router.
- `backend/jobs/executor.py` — register `algo_kite_instruments_refresh`.

**Frontend (new):**
- `frontend/hooks/useInstruments.ts`
- `frontend/components/algo-trading/InstrumentsTab.tsx` — searchable table reusing the existing column-selector + CSV-download pattern.
- `frontend/components/algo-trading/__tests__/InstrumentsTab.test.tsx` — vitest.

**Frontend (modified):**
- `frontend/app/(authenticated)/algo-trading/AlgoTradingClient.tsx` — `case "instruments"` returns `<InstrumentsTab />`.

---

## Task 1: Add `kiteconnect` to requirements + Kite client wrapper

**Files:**
- Modify: `requirements.txt`
- Create: `backend/algo/broker/__init__.py`
- Create: `backend/algo/broker/base.py`
- Create: `backend/algo/broker/kite_client.py`

- [ ] **Step 1: Pin `kiteconnect` in `requirements.txt`**

Open `requirements.txt`. Add a new line in alphabetical order:

```
kiteconnect==5.0.1
```

If `kiteconnect` is already present, leave as-is.

- [ ] **Step 2: Build the backend image**

```bash
docker compose build backend 2>&1 | tail -20
```

Expected: `Successfully built` or equivalent. If the build fails on a `kiteconnect` dep (`pyOpenSSL`, `service_identity`), check that `requirements.txt` doesn't pin those at incompatible versions; the SDK pulls them transitively.

- [ ] **Step 3: Create the package marker**

```python
# backend/algo/broker/__init__.py
"""Broker abstraction — Slice 2+ of the Algo Trading epic."""
```

- [ ] **Step 4: Create the ABC**

```python
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
```

- [ ] **Step 5: Build the Kite client wrapper**

```python
# backend/algo/broker/kite_client.py
"""Thin wrapper over the official ``kiteconnect.KiteConnect`` client.

v1 = READ-ONLY. ``place_order`` is wired to raise so an accidental
import in the strategy runtime can't push a real order. Instrument
list + profile + WebSocket ticker are the only live paths.

Constructor takes the per-user api_key + (optional) access_token —
both decrypted at the call site by ``credentials_repo``.
"""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator

from kiteconnect import KiteConnect

_logger = logging.getLogger(__name__)


class KiteClient:
    """Per-user Kite SDK wrapper.

    Construct with ``api_key``; pass ``access_token`` once you've
    completed the OAuth handshake. The ``api_secret`` is only
    required for the request_token → access_token exchange and
    must NOT be persisted.
    """

    def __init__(
        self,
        api_key: str,
        access_token: str | None = None,
    ) -> None:
        self._kc = KiteConnect(api_key=api_key)
        if access_token:
            self._kc.set_access_token(access_token)

    # ---- OAuth ----------------------------------------------------

    def login_url(self) -> str:
        """Public Kite login URL the user clicks to authenticate."""
        return self._kc.login_url()

    def generate_session(
        self, request_token: str, api_secret: str,
    ) -> dict[str, Any]:
        """Exchange a request_token for an access_token + user_id.

        Returns the SDK's ``session`` dict; the caller persists
        ``access_token`` (Fernet-encrypted) and the
        ``access_token_expires_at`` derived from Kite's docs
        (tokens expire daily ~06:00 IST, so we set
        ``next 06:00 IST`` as the expiry).
        """
        return self._kc.generate_session(
            request_token, api_secret=api_secret,
        )

    # ---- Read paths ----------------------------------------------

    def profile(self) -> dict[str, Any]:
        """Authenticated user's Kite profile."""
        return self._kc.profile()

    def instruments(
        self, exchange: str | None = None,
    ) -> list[dict[str, Any]]:
        """Full instrument dump (or filtered by exchange).

        Kite returns a list of ~80 000 entries — caller is expected
        to bulk-upsert into ``algo.instruments``.
        """
        return self._kc.instruments(exchange=exchange) if exchange \
            else self._kc.instruments()

    async def stream_ticks(
        self, symbols: list[str],
    ) -> AsyncIterator[dict]:
        """Slice 6 fills this in. Stub for the ABC."""
        raise NotImplementedError("Tick streaming lands in Slice 6")

    # ---- Write paths (BLOCKED in v1) -----------------------------

    def place_order(self, intent) -> str:  # noqa: ANN001
        raise NotImplementedError(
            "Live trading is v2 — see epic spec § 1 non-goals.",
        )

    def cancel_order(self, order_id: str) -> None:
        raise NotImplementedError(
            "Live trading is v2 — see epic spec § 1 non-goals.",
        )

    def get_positions(self) -> list[dict]:
        raise NotImplementedError(
            "Live position polling lands in Slice 8 (paper).",
        )
```

- [ ] **Step 6: Smoke import**

```bash
docker compose exec backend python -c "
from backend.algo.broker.kite_client import KiteClient
from backend.algo.broker.base import BrokerAdapter
print('imports ok')
" 2>&1 | tail -5
```

Expected: `imports ok`. If `kiteconnect` import fails, rebuild the image (`docker compose build backend`).

- [ ] **Step 7: Commit**

```bash
git add requirements.txt backend/algo/broker/__init__.py backend/algo/broker/base.py backend/algo/broker/kite_client.py
git commit -m "$(cat <<'EOF'
feat(algo): KiteClient wrapper + BrokerAdapter ABC

Slice 2 of the Algo Trading epic. Thin wrapper over kiteconnect
SDK exposing login_url + generate_session + profile + instruments.
Write paths (place_order / cancel / positions) raise
NotImplementedError so v1 read-only safety is enforced at the
import layer. Pins kiteconnect==5.0.1.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 2: Broker credentials repo (Fernet round-trip)

**Files:**
- Create: `backend/algo/broker/credentials_repo.py`
- Create: `backend/algo/tests/test_broker_credentials_repo.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/algo/tests/test_broker_credentials_repo.py
"""Round-trip + per-user-isolation tests for broker_credentials_repo.

Uses the in-memory ``_StubSession`` pattern from the Slice 4
strategies-route tests. Real DB writes covered indirectly by
the Alembic migration smoke (Session 1 Task 1) + the route
smokes in Task 4 of this session.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

# Ensure Fernet is set up for the test process. The host CI runs
# may not export BYO_SECRET_KEY — set a deterministic dev key.
os.environ.setdefault(
    "BYO_SECRET_KEY",
    # Static test key — NEVER use in prod.
    "Q3RZ8h3tQq2c5rVH0hWv0cHXh2OtdJv6f4M6Y9pQ8mE=",
)

from backend.algo.broker.credentials_repo import (  # noqa: E402
    BrokerCredentialsRepo,
)


class _StubSession:
    """In-memory async-session stub mirroring the strategy-repo style."""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.last_sql = ""
        self.last_params: dict | None = None

    async def execute(self, q, params=None):
        self.last_sql = str(q)
        self.last_params = params or {}

        class _Res:
            def __init__(self, items):
                self._items = items
            def mappings(self):
                return self
            def first(self):
                return self._items[0] if self._items else None
            @property
            def rowcount(self):
                return len(self._items)

        if "INSERT INTO algo.broker_credentials" in self.last_sql:
            existing = [
                r for r in self.rows
                if str(r["user_id"]) == str(params["user_id"])
            ]
            if existing:
                # ON CONFLICT update path
                existing[0].update(params)
                return _Res(existing)
            self.rows.append(dict(params))
            return _Res([self.rows[-1]])

        if "SELECT" in self.last_sql:
            hit = [
                r for r in self.rows
                if str(r["user_id"]) == str(params["user_id"])
            ]
            return _Res(hit)

        if "UPDATE algo.broker_credentials" in self.last_sql:
            hit = [
                r for r in self.rows
                if str(r["user_id"]) == str(params["user_id"])
            ]
            for h in hit:
                if "access_token_fernet" in params:
                    h["access_token_fernet"] = (
                        params["access_token_fernet"]
                    )
                if "access_token_expires_at" in params:
                    h["access_token_expires_at"] = (
                        params["access_token_expires_at"]
                    )
                if "kite_user_id" in params:
                    h["kite_user_id"] = params["kite_user_id"]
                if "last_login_at" in params:
                    h["last_login_at"] = params["last_login_at"]
            return _Res(hit)

        if "DELETE FROM algo.broker_credentials" in self.last_sql:
            before = len(self.rows)
            self.rows = [
                r for r in self.rows
                if str(r["user_id"]) != str(params["user_id"])
            ]
            return _Res([None] * (before - len(self.rows)))

        return _Res([])

    async def commit(self):
        return None


@pytest.fixture
def repo() -> BrokerCredentialsRepo:
    return BrokerCredentialsRepo()


@pytest.mark.asyncio
async def test_save_and_load_api_key_round_trip(repo):
    sess = _StubSession()
    user_id = uuid4()
    await repo.save_api_key(sess, user_id, "test_kite_api_key_12345")
    loaded = await repo.load_api_key(sess, user_id)
    assert loaded == "test_kite_api_key_12345"


@pytest.mark.asyncio
async def test_save_and_load_access_token_round_trip(repo):
    sess = _StubSession()
    user_id = uuid4()
    await repo.save_api_key(sess, user_id, "api_key_xyz")
    expires = datetime.now(timezone.utc) + timedelta(hours=12)
    await repo.save_access_token(
        sess, user_id, "access_token_abc", expires, "AB1234",
    )
    state = await repo.load(sess, user_id)
    assert state is not None
    assert state["api_key"] == "api_key_xyz"
    assert state["access_token"] == "access_token_abc"
    assert state["kite_user_id"] == "AB1234"


@pytest.mark.asyncio
async def test_load_returns_none_for_unknown_user(repo):
    sess = _StubSession()
    state = await repo.load(sess, uuid4())
    assert state is None


@pytest.mark.asyncio
async def test_per_user_isolation(repo):
    sess = _StubSession()
    u1, u2 = uuid4(), uuid4()
    await repo.save_api_key(sess, u1, "u1_key")
    await repo.save_api_key(sess, u2, "u2_key")
    assert (await repo.load_api_key(sess, u1)) == "u1_key"
    assert (await repo.load_api_key(sess, u2)) == "u2_key"


@pytest.mark.asyncio
async def test_delete_removes_row(repo):
    sess = _StubSession()
    user_id = uuid4()
    await repo.save_api_key(sess, user_id, "k")
    await repo.delete(sess, user_id)
    assert (await repo.load(sess, user_id)) is None


@pytest.mark.asyncio
async def test_token_expiry_predicate(repo):
    sess = _StubSession()
    user_id = uuid4()
    await repo.save_api_key(sess, user_id, "k")
    # Past expiry
    expired = datetime.now(timezone.utc) - timedelta(minutes=5)
    await repo.save_access_token(
        sess, user_id, "tok", expired, "AB1234",
    )
    state = await repo.load(sess, user_id)
    assert state["access_token_expired"] is True

    # Fresh expiry
    fresh = datetime.now(timezone.utc) + timedelta(hours=2)
    await repo.save_access_token(
        sess, user_id, "tok2", fresh, "AB1234",
    )
    state = await repo.load(sess, user_id)
    assert state["access_token_expired"] is False
```

- [ ] **Step 2: Run tests, expect ImportError**

```bash
docker compose exec backend python -m pytest backend/algo/tests/test_broker_credentials_repo.py -v 2>&1 | tail -10
```

Expected: ImportError on `backend.algo.broker.credentials_repo`.

- [ ] **Step 3: Implement the repo**

```python
# backend/algo/broker/credentials_repo.py
"""Async CRUD over ``algo.broker_credentials`` with Fernet-encrypted
api_key + access_token columns.

Reuses the existing ``BYO_SECRET_KEY`` Fernet from
``backend.crypto.byo_secrets`` — a single master key keeps the
secret-management surface small. Plaintext leaves the repo only
inside the Kite SDK call path; never returned in API responses.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.crypto.byo_secrets import decrypt_key, encrypt_key

_logger = logging.getLogger(__name__)


class BrokerCredentialsRepo:
    """One row per (user_id) in ``algo.broker_credentials``."""

    async def save_api_key(
        self,
        session: AsyncSession,
        user_id: UUID,
        api_key: str,
    ) -> None:
        """Persist the user's Kite API key (encrypted)."""
        ciphertext = encrypt_key(api_key)
        now = datetime.now(timezone.utc)
        await session.execute(
            text(
                "INSERT INTO algo.broker_credentials "
                "(user_id, api_key_fernet, created_at, updated_at) "
                "VALUES (:user_id, :api_key_fernet, :now, :now) "
                "ON CONFLICT (user_id) DO UPDATE SET "
                "api_key_fernet = EXCLUDED.api_key_fernet, "
                "updated_at = EXCLUDED.updated_at"
            ),
            {
                "user_id": user_id,
                "api_key_fernet": ciphertext,
                "now": now,
            },
        )
        await session.commit()

    async def save_access_token(
        self,
        session: AsyncSession,
        user_id: UUID,
        access_token: str,
        expires_at: datetime,
        kite_user_id: str,
    ) -> None:
        """Persist a freshly-issued access_token + expiry + kite_user_id."""
        ciphertext = encrypt_key(access_token)
        now = datetime.now(timezone.utc)
        await session.execute(
            text(
                "UPDATE algo.broker_credentials SET "
                "access_token_fernet = :access_token_fernet, "
                "access_token_expires_at = :access_token_expires_at, "
                "kite_user_id = :kite_user_id, "
                "last_login_at = :last_login_at, "
                "updated_at = :updated_at "
                "WHERE user_id = :user_id"
            ),
            {
                "user_id": user_id,
                "access_token_fernet": ciphertext,
                "access_token_expires_at": expires_at,
                "kite_user_id": kite_user_id,
                "last_login_at": now,
                "updated_at": now,
            },
        )
        await session.commit()

    async def load(
        self,
        session: AsyncSession,
        user_id: UUID,
    ) -> dict[str, Any] | None:
        """Return decrypted secrets + expiry metadata, or None if absent."""
        row = (
            await session.execute(
                text(
                    "SELECT api_key_fernet, access_token_fernet, "
                    "access_token_expires_at, kite_user_id, "
                    "last_login_at "
                    "FROM algo.broker_credentials "
                    "WHERE user_id = :user_id"
                ),
                {"user_id": user_id},
            )
        ).mappings().first()
        if row is None:
            return None

        api_key = decrypt_key(row["api_key_fernet"])
        access_token = (
            decrypt_key(row["access_token_fernet"])
            if row["access_token_fernet"]
            else None
        )
        expires_at = row["access_token_expires_at"]
        expired = (
            expires_at is None
            or expires_at <= datetime.now(timezone.utc)
        )
        return {
            "api_key": api_key,
            "access_token": access_token,
            "access_token_expires_at": expires_at,
            "access_token_expired": expired and access_token is not None,
            "kite_user_id": row["kite_user_id"],
            "last_login_at": row["last_login_at"],
        }

    async def load_api_key(
        self,
        session: AsyncSession,
        user_id: UUID,
    ) -> str | None:
        """Convenience getter for just the api_key (used in OAuth flow)."""
        state = await self.load(session, user_id)
        return state["api_key"] if state else None

    async def delete(
        self,
        session: AsyncSession,
        user_id: UUID,
    ) -> bool:
        """Remove the row entirely. Returns False on miss."""
        res = await session.execute(
            text(
                "DELETE FROM algo.broker_credentials "
                "WHERE user_id = :user_id"
            ),
            {"user_id": user_id},
        )
        await session.commit()
        return res.rowcount > 0
```

- [ ] **Step 4: Run tests, expect 6 passed**

```bash
docker compose exec backend python -m pytest backend/algo/tests/test_broker_credentials_repo.py -v 2>&1 | tail -12
```

Expected: 6 passed.

- [ ] **Step 5: Lint**

```bash
docker compose exec backend python -m flake8 backend/algo/broker/credentials_repo.py backend/algo/tests/test_broker_credentials_repo.py 2>&1 | tail -3
```

Expected: zero violations.

- [ ] **Step 6: Commit**

```bash
git add backend/algo/broker/credentials_repo.py backend/algo/tests/test_broker_credentials_repo.py
git commit -m "$(cat <<'EOF'
feat(algo): broker credentials repo with Fernet round-trip

Slice 2 of the Algo Trading epic. Async CRUD over
algo.broker_credentials reusing the existing BYO_SECRET_KEY
Fernet — no second master key. Encrypted columns: api_key,
access_token. load() returns access_token_expired flag derived
from access_token_expires_at vs UTC now. 6 unit tests cover
round-trip, per-user isolation, deletion, and expiry detection.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 3: Broker OAuth route handlers

**Files:**
- Create: `backend/algo/routes/broker.py`
- Create: `backend/algo/tests/test_broker_routes.py`
- Modify: `backend/algo/routes/__init__.py` (re-export)
- Modify: `backend/routes.py` (register router)

- [ ] **Step 1: Write failing route tests**

```python
# backend/algo/tests/test_broker_routes.py
"""Endpoint smoke tests for /v1/algo/broker/*. KiteConnect SDK is
mocked end-to-end so the tests run without real network calls.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault(
    "BYO_SECRET_KEY",
    "Q3RZ8h3tQq2c5rVH0hWv0cHXh2OtdJv6f4M6Y9pQ8mE=",
)
# Required by the OAuth callback — matched against Kite's
# response in the route handler.
os.environ.setdefault("ALGO_KITE_API_SECRET", "fake_secret_for_tests")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.dependencies import pro_or_superuser
from auth.models import UserContext
from backend.algo.routes.broker import create_broker_router


@pytest.fixture
def app(monkeypatch):
    """Build a FastAPI app with the broker router + stubbed deps."""
    app = FastAPI()
    app.include_router(create_broker_router(), prefix="/v1")
    app.dependency_overrides[pro_or_superuser] = lambda: UserContext(
        user_id="00000000-0000-0000-0000-000000000001",
        email="t@t",
        role="superuser",
    )

    # Stub the session factory + repo + KiteClient.
    rows: dict = {"items": []}

    class _Stub:
        async def execute(self, q, params=None):
            sql = str(q)
            class _Res:
                def __init__(self, items):
                    self._items = items
                def mappings(self):
                    return self
                def first(self):
                    return self._items[0] if self._items else None
                @property
                def rowcount(self):
                    return len(self._items)

            if "INSERT INTO algo.broker_credentials" in sql:
                rows["items"] = [dict(params)]
                return _Res(rows["items"])
            if "SELECT" in sql:
                return _Res(rows["items"])
            if "UPDATE" in sql:
                if rows["items"]:
                    rows["items"][0].update(
                        {k: v for k, v in (params or {}).items()},
                    )
                return _Res(rows["items"])
            if "DELETE" in sql:
                before = len(rows["items"])
                rows["items"] = []
                return _Res([None] * before)
            return _Res([])

        async def commit(self):
            return None

    class _Factory:
        def __call__(self):
            return self
        async def __aenter__(self):
            return _Stub()
        async def __aexit__(self, *args):
            return None

    import backend.algo.routes.broker as broker_routes
    monkeypatch.setattr(
        broker_routes, "_get_session_factory", lambda: _Factory(),
    )
    return app


def test_status_returns_disconnected_when_no_creds(app):
    client = TestClient(app)
    r = client.get("/v1/algo/broker/status")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "disconnected"
    assert body["kite_user_id"] is None


def test_post_api_key_persists_and_status_flips(app):
    client = TestClient(app)
    r = client.post(
        "/v1/algo/broker/api-key",
        json={"api_key": "api_key_xyz"},
    )
    assert r.status_code == 204
    r = client.get("/v1/algo/broker/status")
    body = r.json()
    # api_key set but no access_token yet → key_set, awaiting login
    assert body["status"] == "key_set"


def test_login_url_requires_api_key(app):
    client = TestClient(app)
    r = client.get("/v1/algo/broker/login")
    assert r.status_code == 400


def test_login_url_returns_url_when_api_key_present(app):
    client = TestClient(app)
    client.post(
        "/v1/algo/broker/api-key", json={"api_key": "api_key_xyz"},
    )
    with patch(
        "backend.algo.routes.broker.KiteClient",
    ) as MockKite:
        MockKite.return_value.login_url.return_value = (
            "https://kite.zerodha.com/connect/login?api_key=xxx"
        )
        r = client.get("/v1/algo/broker/login")
    assert r.status_code == 200
    assert "kite.zerodha.com" in r.json()["url"]


def test_callback_exchanges_request_token(app):
    client = TestClient(app)
    client.post(
        "/v1/algo/broker/api-key", json={"api_key": "api_key_xyz"},
    )
    with patch(
        "backend.algo.routes.broker.KiteClient",
    ) as MockKite:
        MockKite.return_value.generate_session.return_value = {
            "access_token": "tok123",
            "user_id": "AB1234",
        }
        r = client.get(
            "/v1/algo/broker/callback?request_token=req_abc",
        )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "connected"
    assert body["kite_user_id"] == "AB1234"


def test_callback_400_when_no_api_key(app):
    client = TestClient(app)
    r = client.get("/v1/algo/broker/callback?request_token=req_abc")
    assert r.status_code == 400


def test_delete_removes_credentials(app):
    client = TestClient(app)
    client.post(
        "/v1/algo/broker/api-key", json={"api_key": "api_key_xyz"},
    )
    r = client.delete("/v1/algo/broker")
    assert r.status_code == 204
    r = client.get("/v1/algo/broker/status")
    assert r.json()["status"] == "disconnected"
```

- [ ] **Step 2: Implement the broker router**

```python
# backend/algo/routes/broker.py
"""Kite OAuth + status endpoints for /v1/algo/broker/*."""
from __future__ import annotations

import logging
import os
from datetime import datetime, time, timedelta, timezone
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from auth.dependencies import pro_or_superuser
from auth.models import UserContext
from backend.algo.broker.credentials_repo import BrokerCredentialsRepo
from backend.algo.broker.kite_client import KiteClient

_logger = logging.getLogger(__name__)
_IST = ZoneInfo("Asia/Kolkata")


def _get_session_factory():
    """Lazy import — mirrors the strategy-routes pattern."""
    from backend.db.repository import get_session_factory
    return get_session_factory()


def _next_token_expiry_ist() -> datetime:
    """Kite access_tokens expire daily ~06:00 IST. Compute the next
    boundary in UTC for storage."""
    now_ist = datetime.now(_IST)
    today_06_ist = datetime.combine(
        now_ist.date(), time(6, 0), tzinfo=_IST,
    )
    if now_ist >= today_06_ist:
        # Past today's 06:00 → next expiry is tomorrow 06:00.
        return (today_06_ist + timedelta(days=1)).astimezone(
            timezone.utc,
        )
    return today_06_ist.astimezone(timezone.utc)


class ApiKeyRequest(BaseModel):
    api_key: str = Field(min_length=4, max_length=128)


class LoginUrlResponse(BaseModel):
    url: str


class CallbackResponse(BaseModel):
    status: str
    kite_user_id: str


class BrokerStatusResponse(BaseModel):
    status: str  # one of: disconnected | key_set | connected | expired
    kite_user_id: str | None = None
    last_login_at: Any | None = None
    access_token_expires_at: Any | None = None


def create_broker_router() -> APIRouter:
    router = APIRouter(prefix="/algo/broker", tags=["algo-trading"])
    repo = BrokerCredentialsRepo()

    @router.get("/status", response_model=BrokerStatusResponse)
    async def status_endpoint(
        user: UserContext = Depends(pro_or_superuser),
    ) -> BrokerStatusResponse:
        factory = _get_session_factory()
        async with factory() as session:
            state = await repo.load(session, UUID(user.user_id))
        if state is None:
            return BrokerStatusResponse(status="disconnected")
        if state["access_token"] is None:
            return BrokerStatusResponse(status="key_set")
        if state["access_token_expired"]:
            return BrokerStatusResponse(
                status="expired",
                kite_user_id=state["kite_user_id"],
                last_login_at=state["last_login_at"],
                access_token_expires_at=state["access_token_expires_at"],
            )
        return BrokerStatusResponse(
            status="connected",
            kite_user_id=state["kite_user_id"],
            last_login_at=state["last_login_at"],
            access_token_expires_at=state["access_token_expires_at"],
        )

    @router.post(
        "/api-key", status_code=status.HTTP_204_NO_CONTENT,
    )
    async def post_api_key(
        body: ApiKeyRequest,
        user: UserContext = Depends(pro_or_superuser),
    ) -> None:
        factory = _get_session_factory()
        async with factory() as session:
            await repo.save_api_key(
                session, UUID(user.user_id), body.api_key,
            )

    @router.get("/login", response_model=LoginUrlResponse)
    async def login_url(
        user: UserContext = Depends(pro_or_superuser),
    ) -> LoginUrlResponse:
        factory = _get_session_factory()
        async with factory() as session:
            api_key = await repo.load_api_key(
                session, UUID(user.user_id),
            )
        if not api_key:
            raise HTTPException(
                status_code=400,
                detail="Save your Kite api_key first via "
                       "POST /algo/broker/api-key",
            )
        try:
            client = KiteClient(api_key=api_key)
            return LoginUrlResponse(url=client.login_url())
        except Exception as exc:
            _logger.exception("kite login_url failed: %s", exc)
            raise HTTPException(
                status_code=502, detail="Kite SDK error",
            )

    @router.get("/callback", response_model=CallbackResponse)
    async def callback(
        request_token: str = Query(..., min_length=8, max_length=128),
        user: UserContext = Depends(pro_or_superuser),
    ) -> CallbackResponse:
        api_secret = os.environ.get("ALGO_KITE_API_SECRET", "").strip()
        if not api_secret:
            raise HTTPException(
                status_code=503,
                detail="Server is not configured for Kite OAuth — "
                       "set ALGO_KITE_API_SECRET in env.",
            )
        factory = _get_session_factory()
        async with factory() as session:
            api_key = await repo.load_api_key(
                session, UUID(user.user_id),
            )
            if not api_key:
                raise HTTPException(
                    status_code=400,
                    detail="Save your Kite api_key first.",
                )
            try:
                client = KiteClient(api_key=api_key)
                session_data = client.generate_session(
                    request_token, api_secret=api_secret,
                )
            except Exception as exc:
                _logger.exception("kite callback failed: %s", exc)
                raise HTTPException(
                    status_code=400,
                    detail="Kite OAuth handshake failed — "
                           "verify the request_token is fresh.",
                )
            access_token = str(session_data["access_token"])
            kite_user_id = str(
                session_data.get("user_id", "")
                or session_data.get("kite_user_id", ""),
            )
            await repo.save_access_token(
                session,
                UUID(user.user_id),
                access_token,
                _next_token_expiry_ist(),
                kite_user_id,
            )
        return CallbackResponse(
            status="connected", kite_user_id=kite_user_id,
        )

    @router.delete("", status_code=status.HTTP_204_NO_CONTENT)
    async def disconnect(
        user: UserContext = Depends(pro_or_superuser),
    ) -> None:
        factory = _get_session_factory()
        async with factory() as session:
            await repo.delete(session, UUID(user.user_id))

    return router
```

- [ ] **Step 3: Re-export in `backend/algo/routes/__init__.py`**

Replace contents:

```python
"""HTTP routers for the algo trading module."""

from backend.algo.routes.broker import create_broker_router
from backend.algo.routes.fees import create_fees_router
from backend.algo.routes.strategies import create_strategies_router

__all__ = [
    "create_broker_router",
    "create_fees_router",
    "create_strategies_router",
]
```

- [ ] **Step 4: Register in `backend/routes.py`**

Read the file. Find where `create_fees_router()` and `create_strategies_router()` are included (Session 1 / 2 work). Add:

```python
from backend.algo.routes import (
    create_broker_router,
    create_fees_router,
    create_strategies_router,
)
app.include_router(create_broker_router(), prefix="/v1")
```

Place the new include alongside the existing two algo includes; consolidate the imports into one block if they were separate.

- [ ] **Step 5: Restart backend**

```bash
docker compose restart backend
sleep 6
```

- [ ] **Step 6: Run tests**

```bash
docker compose exec backend python -m pytest backend/algo/tests/test_broker_routes.py -v 2>&1 | tail -15
```

Expected: 7 passed.

If a test fails:
- 422 vs 400: pydantic regex / range validators on `Field` are strict.
- The `_StubSession.execute` SQL-string match must cover INSERT/UPDATE/SELECT/DELETE for `algo.broker_credentials`.
- The `_get_session_factory` monkeypatch must point at the route module's namespaced reference.

- [ ] **Step 7: Lint + commit**

```bash
docker compose exec backend python -m flake8 backend/algo/routes/broker.py backend/algo/tests/test_broker_routes.py backend/algo/routes/__init__.py 2>&1 | tail -3
git add backend/algo/routes/broker.py backend/algo/routes/__init__.py backend/routes.py backend/algo/tests/test_broker_routes.py
git commit -m "$(cat <<'EOF'
feat(algo): /v1/algo/broker/* OAuth + status endpoints

Slice 2 of the Algo Trading epic. POST /api-key persists the
Fernet-encrypted Kite api_key; GET /login returns the public
Kite login URL; GET /callback exchanges request_token →
access_token via the SDK and stamps the next 06:00 IST as the
expiry; GET /status returns disconnected | key_set | connected
| expired; DELETE wipes the row. ALGO_KITE_API_SECRET must be
set server-side; 503 if missing. 7 endpoint smoke tests with
mocked KiteConnect SDK.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 4: Daily 05:30 IST re-auth notification job

**Files:**
- Create: `backend/algo/jobs/__init__.py`
- Create: `backend/algo/jobs/reauth_notify.py`
- Create: `backend/algo/tests/test_reauth_notify.py`
- Modify: `backend/jobs/executor.py` (register the new job type)

- [ ] **Step 1: Package marker**

```python
# backend/algo/jobs/__init__.py
"""Algo Trading scheduled jobs."""
```

- [ ] **Step 2: Implement the job**

```python
# backend/algo/jobs/reauth_notify.py
"""Daily 05:30 IST job that flags users whose Kite access_token
is past or imminent its 06:00 IST expiry, so the UI can prompt
re-authentication BEFORE strategies need a fresh token.

Emits an audit-event row per affected user; the frontend WS
broker-status hook picks it up via existing event fan-out. v2
adds an optional one-tap re-auth email link; v1 ships the
audit-row only.

Idempotent — running twice on the same morning produces the
same set of audit events because the predicate is over
``access_token_expires_at`` which is a stable column.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from backend.audit_persistence import write_audit_event
from backend.db.repository import get_session_factory

_logger = logging.getLogger(__name__)

# Re-auth notice fires when expiry is within the window below.
_NOTICE_WINDOW = timedelta(hours=1)


async def run_reauth_notify_job(
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Scan algo.broker_credentials for tokens expiring soon."""
    factory = get_session_factory()
    cutoff = datetime.now(timezone.utc) + _NOTICE_WINDOW
    notified: list[str] = []

    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT user_id, kite_user_id, "
                    "access_token_expires_at "
                    "FROM algo.broker_credentials "
                    "WHERE access_token_fernet IS NOT NULL "
                    "  AND access_token_expires_at <= :cutoff"
                ),
                {"cutoff": cutoff},
            )
        ).mappings().all()

        for row in rows:
            user_id = str(row["user_id"])
            await write_audit_event(
                session=session,
                user_id=user_id,
                event_type="ALGO_BROKER_REAUTH_REQUIRED",
                metadata={
                    "kite_user_id": row["kite_user_id"],
                    "expires_at": (
                        row["access_token_expires_at"].isoformat()
                        if row["access_token_expires_at"]
                        else None
                    ),
                },
            )
            notified.append(user_id)

    _logger.info(
        "algo_kite_reauth_notify: notified %d user(s)",
        len(notified),
    )
    return {"notified_count": len(notified), "user_ids": notified}
```

- [ ] **Step 3: Register the job type in `backend/jobs/executor.py`**

Read the existing file. Find the section with `@register_job("recommendations")` etc. Add at the bottom of the registrations block:

```python
@register_job("algo_kite_reauth_notify")
async def _job_algo_kite_reauth_notify(payload: dict | None = None):
    """Daily 05:30 IST notify users with expired/expiring Kite tokens."""
    from backend.algo.jobs.reauth_notify import run_reauth_notify_job
    return await run_reauth_notify_job(payload)
```

If the file's import block uses lazy imports for algo modules, follow the same pattern (the lazy import inside the wrapper is intentional to avoid cycles).

- [ ] **Step 4: Write the test**

```python
# backend/algo/tests/test_reauth_notify.py
"""Job-level test for the daily Kite re-auth notification."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

os.environ.setdefault(
    "BYO_SECRET_KEY",
    "Q3RZ8h3tQq2c5rVH0hWv0cHXh2OtdJv6f4M6Y9pQ8mE=",
)

from backend.algo.jobs.reauth_notify import run_reauth_notify_job


@pytest.mark.asyncio
async def test_notifies_when_token_expired():
    expired = datetime.now(timezone.utc) - timedelta(minutes=5)
    fake_rows = [
        {
            "user_id": uuid4(),
            "kite_user_id": "AB1234",
            "access_token_expires_at": expired,
        },
    ]

    class _Session:
        async def execute(self, q, params=None):
            class _Res:
                def mappings(self):
                    return self
                def all(self):
                    return fake_rows
            return _Res()
        async def commit(self):
            return None

    class _Factory:
        async def __aenter__(self):
            return _Session()
        async def __aexit__(self, *args):
            return None
        def __call__(self):
            return self

    with patch(
        "backend.algo.jobs.reauth_notify.get_session_factory",
        return_value=_Factory(),
    ), patch(
        "backend.algo.jobs.reauth_notify.write_audit_event",
        new=AsyncMock(),
    ) as audit_mock:
        result = await run_reauth_notify_job()
    assert result["notified_count"] == 1
    audit_mock.assert_awaited_once()
    args, kwargs = audit_mock.call_args
    assert kwargs["event_type"] == "ALGO_BROKER_REAUTH_REQUIRED"


@pytest.mark.asyncio
async def test_returns_zero_when_no_expired_tokens():
    fake_rows: list[dict] = []

    class _Session:
        async def execute(self, q, params=None):
            class _Res:
                def mappings(self):
                    return self
                def all(self):
                    return fake_rows
            return _Res()
        async def commit(self):
            return None

    class _Factory:
        async def __aenter__(self):
            return _Session()
        async def __aexit__(self, *args):
            return None
        def __call__(self):
            return self

    with patch(
        "backend.algo.jobs.reauth_notify.get_session_factory",
        return_value=_Factory(),
    ), patch(
        "backend.algo.jobs.reauth_notify.write_audit_event",
        new=AsyncMock(),
    ) as audit_mock:
        result = await run_reauth_notify_job()
    assert result["notified_count"] == 0
    audit_mock.assert_not_awaited()
```

- [ ] **Step 5: Run + lint + commit**

```bash
docker compose restart backend
sleep 6
docker compose exec backend python -m pytest backend/algo/tests/test_reauth_notify.py -v 2>&1 | tail -8
docker compose exec backend python -m flake8 backend/algo/jobs/reauth_notify.py backend/algo/tests/test_reauth_notify.py 2>&1 | tail -3
git add backend/algo/jobs/__init__.py backend/algo/jobs/reauth_notify.py backend/algo/tests/test_reauth_notify.py backend/jobs/executor.py
git commit -m "$(cat <<'EOF'
feat(algo): daily 05:30 IST Kite re-auth notification job

Slice 2 of the Algo Trading epic. Scans algo.broker_credentials
for access_tokens past or within 1 hour of expiry; emits an
ALGO_BROKER_REAUTH_REQUIRED audit event per affected user.
Idempotent. Registered via @register_job under
"algo_kite_reauth_notify"; the scheduler row to fire it daily
at 05:30 IST is provisioned manually post-deploy (one-time
admin step). 2 unit tests cover the expired and no-op paths.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

> **Note for implementer:** if `write_audit_event` doesn't exist at the imported path, search for the actual audit helper in `backend/audit_persistence.py` or `auth/repo/audit_repo.py` and adjust the import + signature accordingly. The contract is "write a row with event_type, user_id, metadata"; exact signature may differ.

---

## Task 5: ConnectBrokerTab frontend

**Files:**
- Create: `frontend/lib/types/algoBroker.ts`
- Create: `frontend/hooks/useBrokerStatus.ts`
- Create: `frontend/components/algo-trading/ConnectBrokerTab.tsx`
- Create: `frontend/components/algo-trading/__tests__/ConnectBrokerTab.test.tsx`
- Modify: `frontend/app/(authenticated)/algo-trading/AlgoTradingClient.tsx` (wire `case "connect"`)

- [ ] **Step 1: Type literals**

```ts
// frontend/lib/types/algoBroker.ts
export type BrokerStatus =
  | "disconnected"
  | "key_set"
  | "connected"
  | "expired";

export interface BrokerStatusResponse {
  status: BrokerStatus;
  kite_user_id: string | null;
  last_login_at: string | null;
  access_token_expires_at: string | null;
}

export const BROKER_STATUS_LABEL: Record<BrokerStatus, string> = {
  disconnected: "Not connected",
  key_set: "API key saved — click Connect Zerodha",
  connected: "Connected",
  expired: "Re-auth required (Kite token expired)",
};
```

- [ ] **Step 2: SWR hook**

```ts
// frontend/hooks/useBrokerStatus.ts
"use client";

import useSWR, { mutate } from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import type { BrokerStatusResponse } from "@/lib/types/algoBroker";

const KEY = `${API_URL}/algo/broker/status`;

async function fetcher(url: string): Promise<BrokerStatusResponse> {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export function useBrokerStatus() {
  const { data, error, isLoading } = useSWR<BrokerStatusResponse>(
    KEY,
    fetcher,
    {
      revalidateOnFocus: false,
      refreshInterval: 60_000,  // poll every minute
    },
  );
  return {
    value: data ?? null,
    loading: isLoading,
    error: error
      ? error instanceof Error
        ? error.message
        : "Failed to load broker status"
      : null,
  };
}

export async function saveApiKey(apiKey: string): Promise<void> {
  const r = await apiFetch(`${API_URL}/algo/broker/api-key`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: apiKey }),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  await mutate(KEY);
}

export async function getLoginUrl(): Promise<string> {
  const r = await apiFetch(`${API_URL}/algo/broker/login`);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const body = (await r.json()) as { url: string };
  return body.url;
}

export async function disconnectBroker(): Promise<void> {
  const r = await apiFetch(`${API_URL}/algo/broker`, {
    method: "DELETE",
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  await mutate(KEY);
}
```

- [ ] **Step 3: ConnectBrokerTab**

```tsx
// frontend/components/algo-trading/ConnectBrokerTab.tsx
"use client";
/**
 * Connect Broker tab — Slice 2 of the Algo Trading epic.
 *
 * Three-state UI driven by /v1/algo/broker/status:
 * - disconnected → API-key form
 * - key_set      → "Connect Zerodha" button (opens Kite login URL)
 * - connected    → success card with kite_user_id + Disconnect
 * - expired      → amber banner "Re-auth required" + same Connect button
 */

import { useCallback, useState } from "react";

import {
  disconnectBroker,
  getLoginUrl,
  saveApiKey,
  useBrokerStatus,
} from "@/hooks/useBrokerStatus";
import { BROKER_STATUS_LABEL } from "@/lib/types/algoBroker";

export function ConnectBrokerTab() {
  const { value, loading, error } = useBrokerStatus();
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const handleSaveKey = useCallback(async () => {
    if (!apiKey.trim()) return;
    setBusy(true);
    setActionError(null);
    try {
      await saveApiKey(apiKey.trim());
      setApiKey("");
    } catch (e) {
      setActionError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, [apiKey]);

  const handleConnect = useCallback(async () => {
    setBusy(true);
    setActionError(null);
    try {
      const url = await getLoginUrl();
      window.location.href = url;
    } catch (e) {
      setActionError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, []);

  const handleDisconnect = useCallback(async () => {
    setBusy(true);
    setActionError(null);
    try {
      await disconnectBroker();
    } catch (e) {
      setActionError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, []);

  if (loading && !value) {
    return <p className="text-sm text-gray-500">Loading…</p>;
  }
  if (error) {
    return (
      <div role="alert" className="text-xs text-red-600 dark:text-red-400">
        {error}
      </div>
    );
  }

  const status = value?.status ?? "disconnected";

  return (
    <div className="space-y-4" data-testid="algo-connect-broker-tab">
      <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">
        Connect Broker
      </h2>
      <p className="text-sm text-gray-600 dark:text-gray-400">
        Connect your Zerodha account so paper-trading strategies
        can read live tick data. v1 is read-only — orders never
        leave the app, even with a valid token.
      </p>

      <div
        data-testid={`algo-broker-status-${status}`}
        className={`rounded-md border p-3 text-sm ${
          status === "connected"
            ? "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900/50 dark:bg-emerald-900/20 dark:text-emerald-300"
            : status === "expired"
              ? "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900/50 dark:bg-amber-900/20 dark:text-amber-300"
              : "border-gray-200 bg-gray-50 text-gray-700 dark:border-gray-700 dark:bg-gray-800/40 dark:text-gray-300"
        }`}
      >
        {BROKER_STATUS_LABEL[status]}
        {value?.kite_user_id && (
          <span className="ml-2 text-xs text-gray-500">
            (Kite ID: {value.kite_user_id})
          </span>
        )}
      </div>

      {actionError && (
        <div role="alert" className="text-xs text-red-600 dark:text-red-400">
          {actionError}
        </div>
      )}

      {status === "disconnected" && (
        <div className="space-y-2 max-w-md">
          <label className="block text-xs font-semibold text-gray-700 dark:text-gray-200">
            Kite API key
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              data-testid="algo-broker-api-key-input"
              className="mt-1 w-full rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-1.5 text-sm font-mono"
              placeholder="api_key_xxx"
            />
          </label>
          <button
            type="button"
            onClick={handleSaveKey}
            disabled={busy || !apiKey.trim()}
            data-testid="algo-broker-save-key"
            className="rounded-md bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-1.5 text-sm disabled:opacity-40"
          >
            {busy ? "Saving…" : "Save API key"}
          </button>
        </div>
      )}

      {(status === "key_set" || status === "expired") && (
        <button
          type="button"
          onClick={handleConnect}
          disabled={busy}
          data-testid="algo-broker-connect"
          className="rounded-md bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-1.5 text-sm disabled:opacity-40"
        >
          {busy ? "Opening Kite login…" : "Connect Zerodha"}
        </button>
      )}

      {(status === "connected" || status === "key_set" || status === "expired") && (
        <button
          type="button"
          onClick={handleDisconnect}
          disabled={busy}
          data-testid="algo-broker-disconnect"
          className="ml-2 rounded-md border border-gray-300 dark:border-gray-700 px-3 py-1.5 text-sm disabled:opacity-40"
        >
          Disconnect
        </button>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Wire into `AlgoTradingClient.tsx`**

Read the file. Find the `tabPanel` `useMemo` switch. Add a `case "connect"` branch BEFORE the default placeholder:

```tsx
import { ConnectBrokerTab } from "@/components/algo-trading/ConnectBrokerTab";
```

```tsx
case "connect":
  return <ConnectBrokerTab />;
```

- [ ] **Step 5: Vitest**

```tsx
// frontend/components/algo-trading/__tests__/ConnectBrokerTab.test.tsx
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";

const swrData = { current: null as unknown };

vi.mock("swr", () => ({
  default: () => ({
    data: swrData.current,
    error: null,
    isLoading: false,
  }),
  mutate: vi.fn(),
}));
vi.mock("@/lib/apiFetch", () => ({ apiFetch: vi.fn() }));
vi.mock("@/lib/config", () => ({ API_URL: "http://test/api" }));

import { ConnectBrokerTab } from "../ConnectBrokerTab";

afterEach(() => {
  cleanup();
  swrData.current = null;
});

describe("ConnectBrokerTab", () => {
  it("renders disconnected state with api-key form", () => {
    swrData.current = { status: "disconnected" };
    render(<ConnectBrokerTab />);
    expect(
      screen.getByTestId("algo-broker-status-disconnected"),
    ).toBeTruthy();
    expect(screen.getByTestId("algo-broker-api-key-input")).toBeTruthy();
  });

  it("renders key_set state with Connect Zerodha button", () => {
    swrData.current = { status: "key_set" };
    render(<ConnectBrokerTab />);
    expect(screen.getByTestId("algo-broker-connect")).toBeTruthy();
  });

  it("renders connected state with kite_user_id", () => {
    swrData.current = {
      status: "connected",
      kite_user_id: "AB1234",
    };
    render(<ConnectBrokerTab />);
    const card = screen.getByTestId("algo-broker-status-connected");
    expect(card.textContent).toContain("AB1234");
  });

  it("renders expired state with amber banner", () => {
    swrData.current = { status: "expired", kite_user_id: "AB1234" };
    render(<ConnectBrokerTab />);
    expect(
      screen.getByTestId("algo-broker-status-expired"),
    ).toBeTruthy();
    // Reconnect button visible
    expect(screen.getByTestId("algo-broker-connect")).toBeTruthy();
  });

  it("save-key button is disabled when input is empty", () => {
    swrData.current = { status: "disconnected" };
    render(<ConnectBrokerTab />);
    const btn = screen.getByTestId("algo-broker-save-key") as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });
});
```

- [ ] **Step 6: Run + lint + commit**

```bash
cd frontend && npx vitest run components/algo-trading/__tests__/ConnectBrokerTab.test.tsx 2>&1 | tail -10
cd frontend && npx eslint hooks/useBrokerStatus.ts components/algo-trading/ConnectBrokerTab.tsx components/algo-trading/__tests__/ConnectBrokerTab.test.tsx lib/types/algoBroker.ts 'app/(authenticated)/algo-trading/AlgoTradingClient.tsx' --fix 2>&1 | tail -3
cd ..
git add frontend/lib/types/algoBroker.ts frontend/hooks/useBrokerStatus.ts frontend/components/algo-trading/ConnectBrokerTab.tsx frontend/components/algo-trading/__tests__/ConnectBrokerTab.test.tsx 'frontend/app/(authenticated)/algo-trading/AlgoTradingClient.tsx'
git commit -m "$(cat <<'EOF'
feat(algo): Connect Broker tab + useBrokerStatus hook

Slice 2 of the Algo Trading epic. Four-state UI
(disconnected / key_set / connected / expired) driven by
/v1/algo/broker/status polling every 60 s. API-key save form,
Kite login redirect, disconnect button. 5 vitest cases.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 6: Instrument Master repo + loader

**Files:**
- Create: `backend/algo/instruments/__init__.py`
- Create: `backend/algo/instruments/repo.py`
- Create: `backend/algo/instruments/loader.py`
- Create: `backend/algo/tests/test_instruments_repo.py`

- [ ] **Step 1: Package marker**

```python
# backend/algo/instruments/__init__.py
"""Instrument master — Slice 3 of the Algo Trading epic."""
```

- [ ] **Step 2: Implement the repo**

```python
# backend/algo/instruments/repo.py
"""Async list/upsert over ``algo.instruments``.

The Kite ``/instruments`` endpoint returns ~80 000 rows per
exchange — we bulk-upsert with ``ON CONFLICT (instrument_token)
DO UPDATE`` so the daily refresh idempotently re-syncs.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_logger = logging.getLogger(__name__)


class InstrumentsRepo:
    async def list_instruments(
        self,
        session: AsyncSession,
        *,
        search: str | None = None,
        exchange: str | None = None,
        segment: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if search:
            clauses.append(
                "(tradingsymbol ILIKE :needle "
                "OR our_ticker ILIKE :needle)"
            )
            params["needle"] = f"%{search}%"
        if exchange:
            clauses.append("exchange = :exchange")
            params["exchange"] = exchange.upper()
        if segment:
            clauses.append("segment = :segment")
            params["segment"] = segment
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = (
            await session.execute(
                text(
                    f"SELECT instrument_token, tradingsymbol, exchange, "
                    f"segment, lot_size, tick_size, our_ticker, "
                    f"loaded_at "
                    f"FROM algo.instruments "
                    f"{where} "
                    f"ORDER BY tradingsymbol "
                    f"LIMIT :limit OFFSET :offset"
                ),
                params,
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    async def count_instruments(
        self,
        session: AsyncSession,
        *,
        search: str | None = None,
        exchange: str | None = None,
        segment: str | None = None,
    ) -> int:
        clauses: list[str] = []
        params: dict[str, Any] = {}
        if search:
            clauses.append(
                "(tradingsymbol ILIKE :needle "
                "OR our_ticker ILIKE :needle)"
            )
            params["needle"] = f"%{search}%"
        if exchange:
            clauses.append("exchange = :exchange")
            params["exchange"] = exchange.upper()
        if segment:
            clauses.append("segment = :segment")
            params["segment"] = segment
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        row = (
            await session.execute(
                text(
                    f"SELECT COUNT(*) AS c "
                    f"FROM algo.instruments {where}"
                ),
                params,
            )
        ).mappings().first()
        return int(row["c"]) if row else 0

    async def bulk_upsert(
        self,
        session: AsyncSession,
        rows: list[dict[str, Any]],
    ) -> int:
        """Insert-or-update a batch of instrument rows.

        Each row must have at least: instrument_token, tradingsymbol,
        exchange, segment, lot_size, tick_size. ``our_ticker`` is
        optional — soft-linked when populated.
        """
        if not rows:
            return 0
        from datetime import datetime, timezone
        loaded_at = datetime.now(timezone.utc)
        for r in rows:
            await session.execute(
                text(
                    "INSERT INTO algo.instruments "
                    "(instrument_token, tradingsymbol, exchange, "
                    " segment, lot_size, tick_size, our_ticker, "
                    " loaded_at) "
                    "VALUES (:instrument_token, :tradingsymbol, "
                    "        :exchange, :segment, :lot_size, "
                    "        :tick_size, :our_ticker, :loaded_at) "
                    "ON CONFLICT (instrument_token) DO UPDATE SET "
                    "  tradingsymbol = EXCLUDED.tradingsymbol, "
                    "  exchange = EXCLUDED.exchange, "
                    "  segment = EXCLUDED.segment, "
                    "  lot_size = EXCLUDED.lot_size, "
                    "  tick_size = EXCLUDED.tick_size, "
                    "  our_ticker = EXCLUDED.our_ticker, "
                    "  loaded_at = EXCLUDED.loaded_at"
                ),
                {
                    "instrument_token": r["instrument_token"],
                    "tradingsymbol": r["tradingsymbol"],
                    "exchange": r["exchange"],
                    "segment": r["segment"],
                    "lot_size": int(r.get("lot_size") or 1),
                    "tick_size": float(r.get("tick_size") or 0.05),
                    "our_ticker": r.get("our_ticker"),
                    "loaded_at": loaded_at,
                },
            )
        await session.commit()
        return len(rows)
```

- [ ] **Step 3: Implement the loader**

```python
# backend/algo/instruments/loader.py
"""Pulls Kite ``/instruments`` once per day and bulk-upserts into
``algo.instruments``.

Picks the first available connected user's api_key + access_token
to make the call — Kite's instruments endpoint returns universal
data so per-user fan-out is wasteful and rate-limit-prone.

Returns a dict summary suitable for the @register_job wrapper:
``{"instruments_loaded": N}`` on success, raises on failure.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text

from backend.algo.broker.credentials_repo import BrokerCredentialsRepo
from backend.algo.broker.kite_client import KiteClient
from backend.algo.instruments.repo import InstrumentsRepo
from backend.db.repository import get_session_factory

_logger = logging.getLogger(__name__)


async def _pick_first_connected_user_creds() -> dict[str, Any] | None:
    """Find any user with a fresh access_token to make the call."""
    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT user_id "
                    "FROM algo.broker_credentials "
                    "WHERE access_token_fernet IS NOT NULL "
                    "  AND access_token_expires_at > :now "
                    "ORDER BY last_login_at DESC NULLS LAST "
                    "LIMIT 1"
                ),
                {"now": datetime.now(timezone.utc)},
            )
        ).mappings().first()
        if row is None:
            return None
        creds_repo = BrokerCredentialsRepo()
        return await creds_repo.load(session, row["user_id"])


async def run_instruments_refresh(
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Daily 07:00 IST: pull /instruments, bulk-upsert."""
    creds = await _pick_first_connected_user_creds()
    if creds is None:
        _logger.warning(
            "algo_kite_instruments_refresh: no connected user; "
            "skipping run.",
        )
        return {"instruments_loaded": 0, "skipped": True}

    client = KiteClient(
        api_key=creds["api_key"],
        access_token=creds["access_token"],
    )
    instruments = client.instruments()

    factory = get_session_factory()
    instruments_repo = InstrumentsRepo()
    async with factory() as session:
        loaded = await instruments_repo.bulk_upsert(
            session, instruments,
        )
    _logger.info(
        "algo_kite_instruments_refresh: upserted %d rows", loaded,
    )
    return {"instruments_loaded": loaded}
```

- [ ] **Step 4: Repo unit tests**

```python
# backend/algo/tests/test_instruments_repo.py
"""Async unit tests for InstrumentsRepo."""
from __future__ import annotations

import pytest

from backend.algo.instruments.repo import InstrumentsRepo


class _StubSession:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def execute(self, q, params=None):
        sql = str(q)
        params = params or {}

        class _Res:
            def __init__(self, items):
                self._items = items
            def mappings(self):
                return self
            def all(self):
                return self._items
            def first(self):
                return self._items[0] if self._items else None

        if "INSERT INTO algo.instruments" in sql:
            tok = params["instrument_token"]
            existing = [
                r for r in self.rows
                if r["instrument_token"] == tok
            ]
            if existing:
                existing[0].update(params)
                return _Res(existing)
            self.rows.append(dict(params))
            return _Res([self.rows[-1]])
        if "SELECT COUNT(*)" in sql:
            return _Res([{"c": len(self.rows)}])
        if "SELECT instrument_token" in sql:
            limit = params.get("limit", len(self.rows))
            offset = params.get("offset", 0)
            slice_ = self.rows[offset:offset + limit]
            return _Res([dict(r) for r in slice_])
        return _Res([])

    async def commit(self):
        return None


def _row(token: int, sym: str, exchange: str = "NSE") -> dict:
    return {
        "instrument_token": token,
        "tradingsymbol": sym,
        "exchange": exchange,
        "segment": f"{exchange}-EQ",
        "lot_size": 1,
        "tick_size": 0.05,
        "our_ticker": None,
    }


@pytest.mark.asyncio
async def test_bulk_upsert_inserts_new_rows():
    sess = _StubSession()
    repo = InstrumentsRepo()
    n = await repo.bulk_upsert(sess, [_row(1, "RELIANCE"), _row(2, "TCS")])
    assert n == 2
    assert len(sess.rows) == 2


@pytest.mark.asyncio
async def test_bulk_upsert_updates_existing():
    sess = _StubSession()
    repo = InstrumentsRepo()
    await repo.bulk_upsert(sess, [_row(1, "RELIANCE")])
    # Re-upsert with changed lot_size
    r = _row(1, "RELIANCE")
    r["lot_size"] = 50
    await repo.bulk_upsert(sess, [r])
    assert sess.rows[0]["lot_size"] == 50
    assert len(sess.rows) == 1


@pytest.mark.asyncio
async def test_count_instruments_returns_total():
    sess = _StubSession()
    repo = InstrumentsRepo()
    await repo.bulk_upsert(
        sess, [_row(i, f"S{i}") for i in range(7)],
    )
    n = await repo.count_instruments(sess)
    assert n == 7


@pytest.mark.asyncio
async def test_list_instruments_paginates():
    sess = _StubSession()
    repo = InstrumentsRepo()
    await repo.bulk_upsert(
        sess, [_row(i, f"S{i}") for i in range(20)],
    )
    page = await repo.list_instruments(sess, limit=5, offset=5)
    assert len(page) == 5
```

- [ ] **Step 5: Run + lint + commit**

```bash
docker compose exec backend python -m pytest backend/algo/tests/test_instruments_repo.py -v 2>&1 | tail -8
docker compose exec backend python -m flake8 backend/algo/instruments/ backend/algo/tests/test_instruments_repo.py 2>&1 | tail -3
git add backend/algo/instruments/ backend/algo/tests/test_instruments_repo.py
git commit -m "$(cat <<'EOF'
feat(algo): instrument master repo + Kite loader

Slice 3 of the Algo Trading epic. InstrumentsRepo: paginated +
filterable list, COUNT, bulk_upsert with ON CONFLICT(instrument_token).
Loader picks the first connected user's access_token (Kite's
/instruments returns universal data — no per-user fan-out
needed) and idempotently re-syncs ~80k rows. 4 unit tests.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 7: Instrument refresh job + listing route

**Files:**
- Create: `backend/algo/jobs/instrument_refresh.py`
- Create: `backend/algo/routes/instruments.py`
- Create: `backend/algo/tests/test_instruments_routes.py`
- Modify: `backend/algo/routes/__init__.py` (re-export)
- Modify: `backend/routes.py` (register router)
- Modify: `backend/jobs/executor.py` (register job)

- [ ] **Step 1: Job wrapper**

```python
# backend/algo/jobs/instrument_refresh.py
"""Daily 07:00 IST instrument-master refresh.

Wraps backend.algo.instruments.loader.run_instruments_refresh
behind the @register_job dispatch so the scheduler can fire it.
"""
from __future__ import annotations

from typing import Any

from backend.algo.instruments.loader import run_instruments_refresh


async def run(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return await run_instruments_refresh(payload)
```

Register in `backend/jobs/executor.py` near the other algo registration:

```python
@register_job("algo_kite_instruments_refresh")
async def _job_algo_kite_instruments_refresh(payload: dict | None = None):
    """Daily 07:00 IST refresh of algo.instruments from Kite."""
    from backend.algo.jobs.instrument_refresh import run
    return await run(payload)
```

- [ ] **Step 2: Listing route**

```python
# backend/algo/routes/instruments.py
"""GET /v1/algo/instruments  + POST /v1/algo/instruments/refresh."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from auth.dependencies import pro_or_superuser
from auth.models import UserContext
from backend.algo.instruments.loader import run_instruments_refresh
from backend.algo.instruments.repo import InstrumentsRepo

_logger = logging.getLogger(__name__)


def _get_session_factory():
    from backend.db.repository import get_session_factory
    return get_session_factory()


class InstrumentRow(BaseModel):
    instrument_token: int
    tradingsymbol: str
    exchange: str
    segment: str
    lot_size: int
    tick_size: float
    our_ticker: str | None
    loaded_at: str | None = None


class InstrumentsResponse(BaseModel):
    rows: list[InstrumentRow]
    total: int
    page: int
    page_size: int


class RefreshResponse(BaseModel):
    instruments_loaded: int
    skipped: bool = False


def create_instruments_router() -> APIRouter:
    router = APIRouter(prefix="/algo/instruments", tags=["algo-trading"])
    repo = InstrumentsRepo()

    @router.get("", response_model=InstrumentsResponse)
    async def list_(
        user: UserContext = Depends(pro_or_superuser),
        search: str = Query("", max_length=64),
        exchange: str = Query(
            "", pattern="^(|NSE|BSE|NFO|BFO|MCX|CDS)$",
        ),
        segment: str = Query("", max_length=32),
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
    ) -> InstrumentsResponse:
        factory = _get_session_factory()
        offset = (page - 1) * page_size
        async with factory() as session:
            rows = await repo.list_instruments(
                session,
                search=search or None,
                exchange=exchange or None,
                segment=segment or None,
                limit=page_size,
                offset=offset,
            )
            total = await repo.count_instruments(
                session,
                search=search or None,
                exchange=exchange or None,
                segment=segment or None,
            )
        return InstrumentsResponse(
            rows=[
                InstrumentRow(
                    **r,
                    loaded_at=(
                        r["loaded_at"].isoformat()
                        if r.get("loaded_at") else None
                    ),
                ) if False else InstrumentRow(  # noqa: E501
                    instrument_token=r["instrument_token"],
                    tradingsymbol=r["tradingsymbol"],
                    exchange=r["exchange"],
                    segment=r["segment"],
                    lot_size=r["lot_size"],
                    tick_size=float(r["tick_size"]),
                    our_ticker=r.get("our_ticker"),
                    loaded_at=(
                        r["loaded_at"].isoformat()
                        if r.get("loaded_at") else None
                    ),
                )
                for r in rows
            ],
            total=total,
            page=page,
            page_size=page_size,
        )

    @router.post("/refresh", response_model=RefreshResponse)
    async def refresh(
        user: UserContext = Depends(pro_or_superuser),
    ) -> RefreshResponse:
        try:
            result = await run_instruments_refresh()
        except Exception as exc:
            _logger.exception("manual instruments refresh failed: %s", exc)
            raise HTTPException(
                status_code=502,
                detail="Failed to refresh from Kite — check broker connection.",
            )
        return RefreshResponse(
            instruments_loaded=result.get("instruments_loaded", 0),
            skipped=result.get("skipped", False),
        )

    return router
```

- [ ] **Step 3: Re-export + register**

`backend/algo/routes/__init__.py`:

```python
"""HTTP routers for the algo trading module."""

from backend.algo.routes.broker import create_broker_router
from backend.algo.routes.fees import create_fees_router
from backend.algo.routes.instruments import create_instruments_router
from backend.algo.routes.strategies import create_strategies_router

__all__ = [
    "create_broker_router",
    "create_fees_router",
    "create_instruments_router",
    "create_strategies_router",
]
```

In `backend/routes.py`, extend the existing import block + add `app.include_router(create_instruments_router(), prefix="/v1")`.

- [ ] **Step 4: Endpoint smoke tests**

```python
# backend/algo/tests/test_instruments_routes.py
"""Endpoint smokes for /v1/algo/instruments."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.dependencies import pro_or_superuser
from auth.models import UserContext
from backend.algo.routes.instruments import create_instruments_router


@pytest.fixture
def app(monkeypatch):
    app = FastAPI()
    app.include_router(create_instruments_router(), prefix="/v1")
    app.dependency_overrides[pro_or_superuser] = lambda: UserContext(
        user_id="00000000-0000-0000-0000-000000000001",
        email="t@t",
        role="superuser",
    )

    rows: dict = {"items": []}

    class _Stub:
        async def execute(self, q, params=None):
            sql = str(q)

            class _Res:
                def __init__(self, items):
                    self._items = items
                def mappings(self):
                    return self
                def all(self):
                    return self._items
                def first(self):
                    return self._items[0] if self._items else None

            if "SELECT COUNT(*)" in sql:
                return _Res([{"c": len(rows["items"])}])
            if "SELECT instrument_token" in sql:
                page = (params or {}).get("limit", 50)
                offset = (params or {}).get("offset", 0)
                return _Res(rows["items"][offset:offset + page])
            return _Res([])

        async def commit(self):
            return None

    class _Factory:
        def __call__(self):
            return self
        async def __aenter__(self):
            return _Stub()
        async def __aexit__(self, *args):
            return None

    import backend.algo.routes.instruments as inst
    monkeypatch.setattr(
        inst, "_get_session_factory", lambda: _Factory(),
    )
    rows["items"] = [
        {
            "instrument_token": i,
            "tradingsymbol": f"SYM{i}",
            "exchange": "NSE",
            "segment": "NSE-EQ",
            "lot_size": 1,
            "tick_size": 0.05,
            "our_ticker": None,
            "loaded_at": None,
        }
        for i in range(7)
    ]
    return app


def test_list_returns_paginated(app):
    client = TestClient(app)
    r = client.get("/v1/algo/instruments?page=1&page_size=5")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 7
    assert len(body["rows"]) == 5


def test_list_rejects_unknown_exchange(app):
    client = TestClient(app)
    r = client.get("/v1/algo/instruments?exchange=XYZ")
    assert r.status_code == 422


def test_refresh_endpoint_calls_loader(app):
    client = TestClient(app)
    with patch(
        "backend.algo.routes.instruments.run_instruments_refresh",
        new=AsyncMock(return_value={"instruments_loaded": 42}),
    ):
        r = client.post("/v1/algo/instruments/refresh")
    assert r.status_code == 200
    assert r.json()["instruments_loaded"] == 42


def test_refresh_endpoint_502_on_error(app):
    client = TestClient(app)
    with patch(
        "backend.algo.routes.instruments.run_instruments_refresh",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        r = client.post("/v1/algo/instruments/refresh")
    assert r.status_code == 502
```

- [ ] **Step 5: Run + commit**

```bash
docker compose restart backend
sleep 6
docker compose exec backend python -m pytest backend/algo/tests/test_instruments_routes.py -v 2>&1 | tail -8
docker compose exec backend python -m flake8 backend/algo/jobs/instrument_refresh.py backend/algo/routes/instruments.py backend/algo/routes/__init__.py backend/algo/tests/test_instruments_routes.py 2>&1 | tail -3
git add backend/algo/jobs/instrument_refresh.py backend/algo/routes/instruments.py backend/algo/routes/__init__.py backend/routes.py backend/algo/tests/test_instruments_routes.py backend/jobs/executor.py
git commit -m "$(cat <<'EOF'
feat(algo): /v1/algo/instruments listing + manual refresh

Slice 3 of the Algo Trading epic. GET /algo/instruments with
search/exchange/segment filters + pagination; POST /refresh
manually triggers the daily Kite /instruments pull. Job is
also registered as algo_kite_instruments_refresh for the
07:00 IST scheduler. 4 endpoint smokes.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 8: Instruments tab frontend

**Files:**
- Create: `frontend/hooks/useInstruments.ts`
- Create: `frontend/components/algo-trading/InstrumentsTab.tsx`
- Create: `frontend/components/algo-trading/__tests__/InstrumentsTab.test.tsx`
- Modify: `frontend/app/(authenticated)/algo-trading/AlgoTradingClient.tsx` (wire `case "instruments"`)

- [ ] **Step 1: SWR hook**

```ts
// frontend/hooks/useInstruments.ts
"use client";

import useSWR, { mutate } from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

export interface InstrumentRow {
  instrument_token: number;
  tradingsymbol: string;
  exchange: string;
  segment: string;
  lot_size: number;
  tick_size: number;
  our_ticker: string | null;
  loaded_at: string | null;
}

export interface InstrumentsResponse {
  rows: InstrumentRow[];
  total: number;
  page: number;
  page_size: number;
}

export interface InstrumentsParams {
  search: string;
  exchange: string;
  page: number;
  pageSize: number;
}

function buildKey(p: InstrumentsParams): string {
  const sp = new URLSearchParams({
    search: p.search,
    exchange: p.exchange,
    page: String(p.page),
    page_size: String(p.pageSize),
  });
  return `${API_URL}/algo/instruments?${sp.toString()}`;
}

async function fetcher(url: string): Promise<InstrumentsResponse> {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export function useInstruments(params: InstrumentsParams) {
  const key = buildKey(params);
  const { data, error, isLoading } = useSWR<InstrumentsResponse>(
    key,
    fetcher,
    { revalidateOnFocus: false, dedupingInterval: 60_000 },
  );
  return {
    value: data ?? null,
    loading: isLoading,
    error: error
      ? error instanceof Error
        ? error.message
        : "Failed to load instruments"
      : null,
    refreshKey: key,
  };
}

export async function refreshInstruments(): Promise<number> {
  const r = await apiFetch(`${API_URL}/algo/instruments/refresh`, {
    method: "POST",
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const body = (await r.json()) as { instruments_loaded: number };
  // Trigger SWR re-fetch on every cached instruments key.
  await mutate(
    (k) => typeof k === "string" && k.startsWith(`${API_URL}/algo/instruments`),
  );
  return body.instruments_loaded;
}
```

- [ ] **Step 2: InstrumentsTab**

```tsx
// frontend/components/algo-trading/InstrumentsTab.tsx
"use client";
/**
 * Instruments tab — Slice 3 of the Algo Trading epic.
 *
 * Searchable, filterable, paginated table over the Kite-derived
 * algo.instruments master. Manual refresh button triggers a
 * synchronous Kite /instruments pull (also runs daily at 07:00 IST
 * via the scheduler).
 */

import { useCallback, useEffect, useState } from "react";

import {
  refreshInstruments,
  useInstruments,
} from "@/hooks/useInstruments";

const EXCHANGES = ["", "NSE", "BSE", "NFO", "BFO", "MCX", "CDS"] as const;

export function InstrumentsTab() {
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [exchange, setExchange] = useState<string>("");
  const [page, setPage] = useState(1);
  const pageSize = 50;
  const [refreshing, setRefreshing] = useState(false);
  const [refreshNote, setRefreshNote] = useState<string | null>(null);

  // Debounce search 300 ms.
  useEffect(() => {
    const id = window.setTimeout(() => {
      setSearch((prev) => {
        const next = searchInput.trim();
        if (next !== prev) setPage(1);
        return next;
      });
    }, 300);
    return () => window.clearTimeout(id);
  }, [searchInput]);

  const { value, loading, error } = useInstruments({
    search,
    exchange,
    page,
    pageSize,
  });

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    setRefreshNote(null);
    try {
      const n = await refreshInstruments();
      setRefreshNote(`Refreshed ${n.toLocaleString("en-IN")} instruments`);
    } catch (e) {
      setRefreshNote(`Refresh failed: ${(e as Error).message}`);
    } finally {
      setRefreshing(false);
    }
  }, []);

  const totalPages = value
    ? Math.max(1, Math.ceil(value.total / pageSize))
    : 1;

  return (
    <div className="space-y-4" data-testid="algo-instruments-tab">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">
          Instruments
        </h2>
        <div className="flex items-center gap-2 flex-wrap">
          <input
            type="search"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Search symbol…"
            data-testid="algo-instruments-search"
            className="rounded-md border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 px-2 py-0.5 text-xs text-gray-700 dark:text-gray-200 w-32 sm:w-40"
          />
          <select
            value={exchange}
            onChange={(e) => {
              setExchange(e.target.value);
              setPage(1);
            }}
            data-testid="algo-instruments-exchange"
            className="rounded-md border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 px-2 py-0.5 text-xs"
          >
            {EXCHANGES.map((x) => (
              <option key={x} value={x}>
                {x === "" ? "All exchanges" : x}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={handleRefresh}
            disabled={refreshing}
            data-testid="algo-instruments-refresh"
            className="rounded-md border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 px-2 py-0.5 text-xs disabled:opacity-40"
          >
            {refreshing ? "Refreshing…" : "Refresh"}
          </button>
        </div>
      </div>

      {refreshNote && (
        <div className="text-xs text-gray-500 dark:text-gray-400">
          {refreshNote}
        </div>
      )}

      {error && (
        <div role="alert" className="text-xs text-red-600 dark:text-red-400">
          {error}
        </div>
      )}

      <div className="overflow-x-auto rounded-lg border border-gray-200 dark:border-gray-700">
        <table className="min-w-full text-sm">
          <thead className="bg-gray-50 dark:bg-gray-800/50">
            <tr>
              <Th>Symbol</Th>
              <Th>Exchange</Th>
              <Th>Segment</Th>
              <Th align="right">Lot</Th>
              <Th align="right">Tick</Th>
              <Th>Our ticker</Th>
            </tr>
          </thead>
          <tbody
            data-testid="algo-instruments-tbody"
            className="divide-y divide-gray-100 dark:divide-gray-800"
          >
            {loading && !value ? (
              <tr>
                <td colSpan={6} className="px-3 py-6 text-center text-xs text-gray-500">
                  Loading…
                </td>
              </tr>
            ) : value && value.rows.length > 0 ? (
              value.rows.map((r) => (
                <tr key={r.instrument_token} className="hover:bg-gray-50 dark:hover:bg-gray-800/50">
                  <td className="px-3 py-2 font-mono">{r.tradingsymbol}</td>
                  <td className="px-3 py-2 text-gray-500">{r.exchange}</td>
                  <td className="px-3 py-2 text-gray-500">{r.segment}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{r.lot_size}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{r.tick_size}</td>
                  <td className="px-3 py-2 text-gray-500">
                    {r.our_ticker ?? "—"}
                  </td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={6} className="px-3 py-6 text-center text-xs text-gray-500">
                  No instruments. Click &ldquo;Refresh&rdquo; to pull from Kite.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {value && value.total > 0 && (
        <div className="flex items-center justify-between text-xs text-gray-500">
          <span>
            Showing {(value.page - 1) * pageSize + 1}–
            {Math.min(value.page * pageSize, value.total)} of{" "}
            {value.total.toLocaleString("en-IN")} rows
          </span>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page <= 1}
              data-testid="algo-instruments-prev"
              className="rounded-md border border-gray-300 dark:border-gray-700 px-2 py-1 disabled:opacity-40"
            >
              Prev
            </button>
            <span className="px-2">Page {value.page} / {totalPages}</span>
            <button
              type="button"
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages}
              data-testid="algo-instruments-next"
              className="rounded-md border border-gray-300 dark:border-gray-700 px-2 py-1 disabled:opacity-40"
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function Th({
  children, align = "left",
}: { children: React.ReactNode; align?: "left" | "right" }) {
  return (
    <th
      scope="col"
      className={`whitespace-nowrap px-3 py-2 text-${align} text-xs font-medium text-gray-600 dark:text-gray-300`}
    >
      {children}
    </th>
  );
}
```

- [ ] **Step 3: Wire into `AlgoTradingClient.tsx`**

```tsx
import { InstrumentsTab } from "@/components/algo-trading/InstrumentsTab";
```

```tsx
case "instruments":
  return <InstrumentsTab />;
```

- [ ] **Step 4: Vitest**

```tsx
// frontend/components/algo-trading/__tests__/InstrumentsTab.test.tsx
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

const swr = { current: null as unknown };
vi.mock("swr", () => ({
  default: () => ({ data: swr.current, error: null, isLoading: false }),
  mutate: vi.fn(),
}));
vi.mock("@/lib/apiFetch", () => ({ apiFetch: vi.fn() }));
vi.mock("@/lib/config", () => ({ API_URL: "http://test/api" }));

import { InstrumentsTab } from "../InstrumentsTab";

afterEach(() => {
  cleanup();
  swr.current = null;
});

describe("InstrumentsTab", () => {
  it("renders empty state when no instruments", () => {
    swr.current = { rows: [], total: 0, page: 1, page_size: 50 };
    render(<InstrumentsTab />);
    expect(
      screen.getByTestId("algo-instruments-tbody").textContent,
    ).toContain("No instruments");
  });

  it("renders rows when data is present", () => {
    swr.current = {
      rows: [
        {
          instrument_token: 1,
          tradingsymbol: "RELIANCE",
          exchange: "NSE",
          segment: "NSE-EQ",
          lot_size: 1,
          tick_size: 0.05,
          our_ticker: "RELIANCE.NS",
          loaded_at: null,
        },
      ],
      total: 1,
      page: 1,
      page_size: 50,
    };
    render(<InstrumentsTab />);
    expect(
      screen.getByTestId("algo-instruments-tbody").textContent,
    ).toContain("RELIANCE");
  });

  it("Refresh button is enabled by default", () => {
    swr.current = { rows: [], total: 0, page: 1, page_size: 50 };
    render(<InstrumentsTab />);
    const btn = screen.getByTestId(
      "algo-instruments-refresh",
    ) as HTMLButtonElement;
    expect(btn.disabled).toBe(false);
  });
});
```

- [ ] **Step 5: Run + commit**

```bash
cd frontend && npx vitest run components/algo-trading/__tests__/InstrumentsTab.test.tsx 2>&1 | tail -8
cd frontend && npx eslint hooks/useInstruments.ts components/algo-trading/InstrumentsTab.tsx components/algo-trading/__tests__/InstrumentsTab.test.tsx 'app/(authenticated)/algo-trading/AlgoTradingClient.tsx' --fix 2>&1 | tail -3
cd ..
git add frontend/hooks/useInstruments.ts frontend/components/algo-trading/InstrumentsTab.tsx frontend/components/algo-trading/__tests__/InstrumentsTab.test.tsx 'frontend/app/(authenticated)/algo-trading/AlgoTradingClient.tsx'
git commit -m "$(cat <<'EOF'
feat(algo): Instruments tab — searchable Kite-derived master

Slice 3 of the Algo Trading epic. Searchable / filterable /
paginated table backed by /v1/algo/instruments. "Refresh"
button manually triggers the Kite /instruments pull (same
pipeline as the daily 07:00 IST job). 3 vitest cases.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 9: PROGRESS.md + push

**Files:**
- Modify: `PROGRESS.md`

- [ ] **Step 1: Insert entry**

After the `---` separator at the top, before the Session 2 entry:

```markdown
## 2026-05-08 (later 3) — Algo Trading Slices 2 + 3: Kite OAuth + instrument master

**Branch:** `feature/algo-trading-session-3-kite-instruments` (built off Session 2's tip)
**Epic:** Algo Trading Platform v1
**Spec:** `docs/superpowers/specs/2026-05-08-algo-trading-platform-design.md`
**Plan:** `docs/superpowers/plans/2026-05-08-algo-trading-session-3-kite-instruments.md`

**Shipped:**
- Slice 2: KiteClient SDK wrapper (read-only; place_order raises); per-user broker_credentials repo with Fernet (reusing BYO_SECRET_KEY); `/v1/algo/broker/{api-key,login,callback,status,disconnect}`; daily 05:30 IST `algo_kite_reauth_notify` job; ConnectBrokerTab UI (4-state: disconnected/key_set/connected/expired).
- Slice 3: InstrumentsRepo (paginated + filterable + bulk_upsert); Kite `/instruments` loader using first-connected-user token; `/v1/algo/instruments` listing + `/refresh`; `algo_kite_instruments_refresh` job for the 07:00 IST scheduler; InstrumentsTab UI.

**Tests:** 6 broker creds repo + 7 broker route + 2 reauth job + 4 instruments repo + 4 instruments route + 5 vitest ConnectBrokerTab + 3 vitest InstrumentsTab. All passing.

**Deferred:** Slices 6 / 7 / 8 / 9 / 10. Tick streaming and backtest engine are next.

---
```

- [ ] **Step 2: Commit + push**

```bash
git add PROGRESS.md
git commit -m "$(cat <<'EOF'
docs(progress): log Algo Trading session 3 — Slices 2 + 3

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
git push -u origin feature/algo-trading-session-3-kite-instruments 2>&1 | tail -5
```

> **Push only — no PR per the established Session 1+2 pattern.**

---

## Self-Review (post-write)

**1. Spec coverage:**
- §7.3 Kite OAuth flow → Tasks 1, 2, 3 (client + repo + routes) ✓
- §7.4 Rate limits + reconciliation → deferred to Slice 8 (paper); not in scope here.
- §3.4 algo.broker_credentials columns → all consumed by Task 2 repo ✓
- Daily 05:30 IST re-auth job → Task 4 ✓
- §3.4 algo.instruments columns → Task 6 repo ✓
- Daily 07:00 IST instruments refresh → Task 7 ✓
- ConnectBroker tab → Task 5 ✓
- Instruments tab → Task 8 ✓

**2. Placeholder scan:**
- One implementer note in Task 4 about `write_audit_event` import path (audit helper location may differ). Flagged inline as "search for actual helper if import fails".
- No TBDs / "implement later" / unfilled code blocks.

**3. Type consistency:**
- `BrokerStatus` literal consistent in `algoBroker.ts` (Task 5) and the backend `BrokerStatusResponse.status` field literal in `broker.py` (Task 3): `disconnected | key_set | connected | expired`.
- `InstrumentRow` shape matches backend Pydantic model in `instruments.py` (Task 7) and TS interface in `useInstruments.ts` (Task 8).
- `BrokerCredentialsRepo` API consistent across Tasks 2 + 3 + 4 + 6.
- Job names consistent: `algo_kite_reauth_notify` (Task 4) and `algo_kite_instruments_refresh` (Task 7) — both registered in `backend/jobs/executor.py`.
- Testid prefixes (`algo-broker-*`, `algo-instruments-*`) consistent across Tasks 5 + 8.

No gaps; no inconsistencies. Plan ready for execution.
