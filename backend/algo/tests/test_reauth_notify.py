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

    with (
        patch(
            "backend.algo.jobs.reauth_notify.get_session_factory",
            return_value=_Factory(),
        ),
        patch(
            "backend.algo.jobs.reauth_notify.write_audit_event",
            new=AsyncMock(),
        ) as audit_mock,
    ):
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

    with (
        patch(
            "backend.algo.jobs.reauth_notify.get_session_factory",
            return_value=_Factory(),
        ),
        patch(
            "backend.algo.jobs.reauth_notify.write_audit_event",
            new=AsyncMock(),
        ) as audit_mock,
    ):
        result = await run_reauth_notify_job()
    assert result["notified_count"] == 0
    audit_mock.assert_not_awaited()
