# backend/algo/routes/broker.py
"""Kite OAuth + status endpoints for /v1/algo/broker/*."""
from __future__ import annotations

import logging
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
from backend.secret_loader import load_secret

_logger = logging.getLogger(__name__)
_IST = ZoneInfo("Asia/Kolkata")


def _get_session_factory():
    """Lazy import — mirrors the strategy-routes pattern."""
    from backend.db.engine import get_session_factory
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
        request_token: str = Query(..., min_length=4, max_length=128),
        user: UserContext = Depends(pro_or_superuser),
    ) -> CallbackResponse:
        api_secret = (
            load_secret("algo_kite_api_secret", default="") or ""
        ).strip()
        if not api_secret:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Server is not configured for Kite OAuth — "
                    "store via "
                    "`scripts/secrets/keychain.sh set "
                    "algo_kite_api_secret` then "
                    "`scripts/secrets/materialize.sh`, or set "
                    "ALGO_KITE_API_SECRET in env as a fallback."
                ),
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
        # Eagerly start the WS multiplexer + subscribe the full
        # NSE universe so live LTPs flow into Redis as soon as
        # the user lands back on the dashboard. Fire-and-forget
        # to keep the callback latency in the <100ms range; the
        # universe-seed task completes in 1-2s and we don't make
        # the HTTP response wait on it.
        try:
            from backend.algo.broker.ws_registry import (
                get_or_create_multiplexer,
            )
            from backend.algo.broker.universe_seeder import (
                seed_full_nse_universe_background,
            )
            mux = await get_or_create_multiplexer(
                user_id=UUID(user.user_id),
                api_key=api_key,
                access_token=access_token,
            )
            seed_full_nse_universe_background(mux, factory)
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "eager universe seed failed (callback will "
                "still return success): %s", exc,
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
