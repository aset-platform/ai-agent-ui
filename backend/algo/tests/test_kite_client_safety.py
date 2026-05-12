"""Tests for KiteClient.place_order order-safety hardening (PR #1).

Covers:
- LTP staleness gate (within / over / unset).
- Full-payload ``order_submitted_live`` event emission.
- Dry-run preserves event emission and bypasses staleness gate.
- Payload shape contract (request / context / response.raw).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from backend.algo.broker.exceptions import LtpStaleError
from backend.algo.broker.kite_client import KiteClient

UTC = timezone.utc


@pytest.fixture()
def kite_client():
    """KiteClient with a mocked KiteConnect SDK."""
    with patch(
        "backend.algo.broker.kite_client.KiteConnect",
    ) as MockKC:
        kc_instance = MagicMock()
        MockKC.return_value = kc_instance
        client = KiteClient(
            api_key="test_api_key",
            access_token="test_access_token",
            dry_run=False,
        )
        client._kc = kc_instance
        yield client, kc_instance


@pytest.fixture()
def events_buffer():
    """Plain list sink mirroring runtime._events."""
    return []


# -----------------------------------------------------------------
# LTP staleness gate
# -----------------------------------------------------------------


class TestLtpStaleGate:
    def test_within_budget_submission_proceeds(
        self, kite_client, events_buffer,
    ):
        """LTP age <= ALGO_MAX_LTP_AGE_S → SDK called, event emitted."""
        client, mock_kc = kite_client
        mock_kc.place_order.return_value = {"order_id": "KITE_FRESH"}
        fresh_ts = datetime.now(UTC) - timedelta(seconds=1)
        with patch.dict(
            "os.environ", {"ALGO_MAX_LTP_AGE_S": "5"},
        ):
            oid = client.place_order(
                tradingsymbol="ITC",
                exchange="NSE",
                transaction_type="BUY",
                quantity=8,
                order_type="LIMIT",
                price=307.35,
                last_price=307.30,
                last_price_ts=fresh_ts,
                liquidity_bucket="largecap",
                slippage_bps_applied=20,
                events_sink=events_buffer.append,
            )
        assert oid == "KITE_FRESH"
        mock_kc.place_order.assert_called_once()
        submitted = [
            e for e in events_buffer
            if e["type"] == "order_submitted_live"
        ]
        assert len(submitted) == 1, "Expected one submission event"

    def test_over_budget_raises_and_emits_blocked_event(
        self, kite_client, events_buffer,
    ):
        """LTP age > budget → LtpStaleError, blocked event, no SDK call."""
        client, mock_kc = kite_client
        stale_ts = datetime.now(UTC) - timedelta(seconds=30)
        with patch.dict(
            "os.environ", {"ALGO_MAX_LTP_AGE_S": "5"},
        ):
            with pytest.raises(LtpStaleError, match="LTP age"):
                client.place_order(
                    tradingsymbol="ITC",
                    exchange="NSE",
                    transaction_type="BUY",
                    quantity=8,
                    order_type="LIMIT",
                    price=307.35,
                    last_price=307.30,
                    last_price_ts=stale_ts,
                    events_sink=events_buffer.append,
                )
        mock_kc.place_order.assert_not_called()
        blocked = [
            e for e in events_buffer
            if e["type"] == "order_ltp_stale_blocked"
        ]
        assert len(blocked) == 1, "Expected one blocked event"
        p = json.loads(blocked[0]["payload_json"])
        assert p["symbol"] == "ITC"
        assert p["age_seconds"] >= 30
        assert p["max_age_seconds"] == 5

    def test_unset_ts_skips_gate_with_warning(
        self, kite_client, events_buffer, caplog,
    ):
        """last_price_ts=None → gate skipped, submission proceeds."""
        client, mock_kc = kite_client
        mock_kc.place_order.return_value = {"order_id": "KITE_NO_TS"}
        with patch.dict(
            "os.environ", {"ALGO_MAX_LTP_AGE_S": "5"},
        ):
            oid = client.place_order(
                tradingsymbol="ITC",
                exchange="NSE",
                transaction_type="BUY",
                quantity=8,
                order_type="LIMIT",
                price=307.35,
                last_price=307.30,
                last_price_ts=None,
                events_sink=events_buffer.append,
            )
        assert oid == "KITE_NO_TS"
        mock_kc.place_order.assert_called_once()

    def test_env_default_999999_effectively_disables_gate(
        self, kite_client, events_buffer,
    ):
        """ALGO_MAX_LTP_AGE_S=999999 → gate never triggers (ship config)."""
        client, mock_kc = kite_client
        mock_kc.place_order.return_value = {"order_id": "KITE_DEFAULT"}
        very_stale = datetime.now(UTC) - timedelta(seconds=600)
        with patch.dict(
            "os.environ", {"ALGO_MAX_LTP_AGE_S": "999999"},
        ):
            oid = client.place_order(
                tradingsymbol="ITC",
                exchange="NSE",
                transaction_type="BUY",
                quantity=1,
                order_type="LIMIT",
                price=307.35,
                last_price=307.30,
                last_price_ts=very_stale,
                events_sink=events_buffer.append,
            )
        assert oid == "KITE_DEFAULT"


# -----------------------------------------------------------------
# Payload shape — order_submitted_live full request/context/response
# -----------------------------------------------------------------


class TestSubmittedPayloadShape:
    def test_payload_has_request_context_response(
        self, kite_client, events_buffer,
    ):
        client, mock_kc = kite_client
        raw_resp = {"order_id": "KITE_PAYLOAD", "status": "ok"}
        mock_kc.place_order.return_value = raw_resp
        fresh = datetime.now(UTC) - timedelta(seconds=1)
        client.place_order(
            tradingsymbol="ITC",
            exchange="NSE",
            transaction_type="BUY",
            quantity=8,
            order_type="LIMIT",
            price=307.35,
            tag="algo-7c3a1b8d",
            last_price=307.30,
            last_price_ts=fresh,
            liquidity_bucket="largecap",
            slippage_bps_applied=20,
            chunk_index=None,
            chunk_total=None,
            events_sink=events_buffer.append,
        )
        submitted = [
            e for e in events_buffer
            if e["type"] == "order_submitted_live"
        ]
        assert len(submitted) == 1
        p = json.loads(submitted[0]["payload_json"])
        # Request block
        assert p["request"]["tradingsymbol"] == "ITC"
        assert p["request"]["exchange"] == "NSE"
        assert p["request"]["transaction_type"] == "BUY"
        assert p["request"]["quantity"] == 8
        assert p["request"]["order_type"] == "LIMIT"
        assert p["request"]["product"] == "CNC"
        assert p["request"]["variety"] == "regular"
        assert p["request"]["price"] == 307.35
        assert p["request"]["tag"] == "algo-7c3a1b8d"
        # Context block
        assert p["context"]["last_price"] == 307.30
        assert p["context"]["liquidity_bucket"] == "largecap"
        assert p["context"]["slippage_bps_applied"] == 20
        assert p["context"]["chunk_index"] is None
        assert p["context"]["chunk_total"] is None
        assert p["context"]["ltp_age_seconds"] >= 0
        # Response block
        assert p["response"]["raw"] == raw_resp
        # Top-level legacy keys preserved for PaperEventsTimeline
        assert p["kite_order_id"] == "KITE_PAYLOAD"
        assert p["symbol"] == "ITC"
        assert p["side"] == "BUY"
        assert p["qty"] == 8
        # Live events omit dry_run entirely — absence = real.
        # Dry-run rehearsals still set "dry_run": true (see
        # TestDryRunSubmissionEvent below).
        assert "dry_run" not in p


# -----------------------------------------------------------------
# Dry-run
# -----------------------------------------------------------------


class TestDryRunSubmissionEvent:
    def test_dry_run_emits_event_with_synthetic_id(
        self, events_buffer,
    ):
        with patch("backend.algo.broker.kite_client.KiteConnect"):
            client = KiteClient(
                api_key="k", access_token="t", dry_run=True,
            )
            oid = client.place_order(
                tradingsymbol="ITC",
                exchange="NSE",
                transaction_type="BUY",
                quantity=1,
                order_type="LIMIT",
                price=307.35,
                last_price=307.30,
                last_price_ts=datetime.now(UTC),
                events_sink=events_buffer.append,
            )
        assert oid.startswith("DRY_")
        submitted = [
            e for e in events_buffer
            if e["type"] == "order_submitted_live"
        ]
        assert len(submitted) == 1
        p = json.loads(submitted[0]["payload_json"])
        assert p["dry_run"] is True
        assert p["kite_order_id"] == oid
        assert p["request"]["tradingsymbol"] == "ITC"

    def test_dry_run_skips_staleness_gate(
        self, events_buffer,
    ):
        """Dry-run is a dev convenience — stale LTP must NOT block."""
        with patch("backend.algo.broker.kite_client.KiteConnect"):
            client = KiteClient(
                api_key="k", access_token="t", dry_run=True,
            )
            with patch.dict(
                "os.environ", {"ALGO_MAX_LTP_AGE_S": "5"},
            ):
                stale_ts = datetime.now(UTC) - timedelta(seconds=300)
                oid = client.place_order(
                    tradingsymbol="ITC",
                    exchange="NSE",
                    transaction_type="BUY",
                    quantity=1,
                    order_type="LIMIT",
                    price=1.0,
                    last_price=1.0,
                    last_price_ts=stale_ts,
                    events_sink=events_buffer.append,
                )
        assert oid.startswith("DRY_")
        blocked = [
            e for e in events_buffer
            if e["type"] == "order_ltp_stale_blocked"
        ]
        assert blocked == []


# -----------------------------------------------------------------
# events_sink=None legacy callers
# -----------------------------------------------------------------


class TestNoEventsSinkLegacy:
    def test_no_sink_still_works(self, kite_client):
        """Legacy callers without events_sink must still get an
        order_id back — event emission silently no-ops."""
        client, mock_kc = kite_client
        mock_kc.place_order.return_value = {"order_id": "KITE_LEGACY"}
        oid = client.place_order(
            tradingsymbol="ITC",
            exchange="NSE",
            transaction_type="BUY",
            quantity=1,
            order_type="LIMIT",
            price=307.0,
        )
        assert oid == "KITE_LEGACY"
