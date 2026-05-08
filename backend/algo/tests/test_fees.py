# backend/algo/tests/test_fees.py
"""Unit tests for the Indian Fee Model (Slice 1).

Reference values pinned against the public Zerodha brokerage
calculator (https://zerodha.com/brokerage-calculator). When
the calculator changes, add a new row to fee_rates.yaml with
an updated ``effective_from`` and add a new test case here —
never edit existing rows.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from backend.algo.fees import (
    FeeBreakdown,
    IndianFeeModel,
    Trade,
)


@pytest.fixture
def model() -> IndianFeeModel:
    return IndianFeeModel(as_of=date(2026, 5, 8))


# ---- Delivery — buy leg --------------------------------------------


def test_delivery_buy_zero_brokerage(model: IndianFeeModel):
    t = Trade(
        symbol="RELIANCE",
        exchange="NSE",
        side="BUY",
        product="DELIVERY",
        qty=10,
        price=Decimal("2945.20"),
    )
    fb = model.compute(t)
    assert fb.brokerage_inr == Decimal("0.00")


def test_delivery_buy_stt_is_buy_rate(model: IndianFeeModel):
    t = Trade(
        symbol="RELIANCE",
        exchange="NSE",
        side="BUY",
        product="DELIVERY",
        qty=10,
        price=Decimal("2945.20"),
    )
    fb = model.compute(t)
    # Notional = 29452.00 ; STT delivery = 0.1% = 29.45
    assert fb.stt_inr == Decimal("29.45")


def test_delivery_buy_stamp_duty(model: IndianFeeModel):
    t = Trade(
        symbol="RELIANCE",
        exchange="NSE",
        side="BUY",
        product="DELIVERY",
        qty=10,
        price=Decimal("2945.20"),
    )
    fb = model.compute(t)
    # 0.015% on 29452 = 4.42
    assert fb.stamp_duty_inr == Decimal("4.42")


def test_delivery_buy_no_dp_charges(model: IndianFeeModel):
    t = Trade(
        symbol="RELIANCE",
        exchange="NSE",
        side="BUY",
        product="DELIVERY",
        qty=10,
        price=Decimal("2945.20"),
    )
    fb = model.compute(t)
    assert fb.dp_charges_inr == Decimal("0.00")


# ---- Delivery — sell leg -------------------------------------------


def test_delivery_sell_dp_charges_applied(model: IndianFeeModel):
    t = Trade(
        symbol="RELIANCE",
        exchange="NSE",
        side="SELL",
        product="DELIVERY",
        qty=10,
        price=Decimal("3000.00"),
    )
    fb = model.compute(t)
    # ₹13.5 + 18% GST = 15.93
    assert fb.dp_charges_inr == Decimal("15.93")


def test_delivery_sell_no_stamp_duty(model: IndianFeeModel):
    t = Trade(
        symbol="RELIANCE",
        exchange="NSE",
        side="SELL",
        product="DELIVERY",
        qty=10,
        price=Decimal("3000.00"),
    )
    fb = model.compute(t)
    assert fb.stamp_duty_inr == Decimal("0.00")


def test_delivery_sell_stt_same_as_buy(model: IndianFeeModel):
    t = Trade(
        symbol="RELIANCE",
        exchange="NSE",
        side="SELL",
        product="DELIVERY",
        qty=10,
        price=Decimal("3000.00"),
    )
    fb = model.compute(t)
    # 0.1% on 30000 = 30.00
    assert fb.stt_inr == Decimal("30.00")


# ---- Intraday — buy leg --------------------------------------------


def test_intraday_buy_brokerage_pct_floor(model: IndianFeeModel):
    # qty=1000 @ 100 → 100000 notional → 0.03% = 30 → capped at 20
    t = Trade(
        symbol="RELIANCE",
        exchange="NSE",
        side="BUY",
        product="INTRADAY",
        qty=1000,
        price=Decimal("100.00"),
    )
    fb = model.compute(t)
    assert fb.brokerage_inr == Decimal("20.00")


def test_intraday_buy_brokerage_pct_below_cap(model: IndianFeeModel):
    # qty=10 @ 100 → 1000 notional → 0.03% = 0.30
    t = Trade(
        symbol="RELIANCE",
        exchange="NSE",
        side="BUY",
        product="INTRADAY",
        qty=10,
        price=Decimal("100.00"),
    )
    fb = model.compute(t)
    assert fb.brokerage_inr == Decimal("0.30")


def test_intraday_buy_no_stt(model: IndianFeeModel):
    t = Trade(
        symbol="RELIANCE",
        exchange="NSE",
        side="BUY",
        product="INTRADAY",
        qty=10,
        price=Decimal("100.00"),
    )
    fb = model.compute(t)
    assert fb.stt_inr == Decimal("0.00")


def test_intraday_buy_stamp_duty_lower_rate(model: IndianFeeModel):
    # 0.003% on 1000 = 0.03
    t = Trade(
        symbol="RELIANCE",
        exchange="NSE",
        side="BUY",
        product="INTRADAY",
        qty=10,
        price=Decimal("100.00"),
    )
    fb = model.compute(t)
    assert fb.stamp_duty_inr == Decimal("0.03")


# ---- Intraday — sell leg -------------------------------------------


def test_intraday_sell_stt_lower_rate(model: IndianFeeModel):
    # 0.025% on 1000 = 0.25
    t = Trade(
        symbol="RELIANCE",
        exchange="NSE",
        side="SELL",
        product="INTRADAY",
        qty=10,
        price=Decimal("100.00"),
    )
    fb = model.compute(t)
    assert fb.stt_inr == Decimal("0.25")


def test_intraday_sell_no_stamp_duty(model: IndianFeeModel):
    t = Trade(
        symbol="RELIANCE",
        exchange="NSE",
        side="SELL",
        product="INTRADAY",
        qty=10,
        price=Decimal("100.00"),
    )
    fb = model.compute(t)
    assert fb.stamp_duty_inr == Decimal("0.00")


def test_intraday_sell_no_dp_charges(model: IndianFeeModel):
    t = Trade(
        symbol="RELIANCE",
        exchange="NSE",
        side="SELL",
        product="INTRADAY",
        qty=10,
        price=Decimal("100.00"),
    )
    fb = model.compute(t)
    assert fb.dp_charges_inr == Decimal("0.00")


# ---- Exchange / SEBI / GST -----------------------------------------


def test_nse_exchange_txn_charge(model: IndianFeeModel):
    t = Trade(
        symbol="RELIANCE",
        exchange="NSE",
        side="BUY",
        product="DELIVERY",
        qty=10,
        price=Decimal("2945.20"),
    )
    fb = model.compute(t)
    # 0.00297% on 29452 = 0.87
    assert fb.exchange_txn_inr == Decimal("0.87")


def test_bse_exchange_txn_charge_higher(model: IndianFeeModel):
    t = Trade(
        symbol="TCS",
        exchange="BSE",
        side="BUY",
        product="DELIVERY",
        qty=10,
        price=Decimal("3000.00"),
    )
    fb = model.compute(t)
    # 0.00375% on 30000 = 1.13 (rounded)
    assert fb.exchange_txn_inr == Decimal("1.13")


def test_sebi_charge_minimum(model: IndianFeeModel):
    t = Trade(
        symbol="RELIANCE",
        exchange="NSE",
        side="BUY",
        product="DELIVERY",
        qty=10,
        price=Decimal("2945.20"),
    )
    fb = model.compute(t)
    # 0.0001% on 29452 = 0.03
    assert fb.sebi_inr == Decimal("0.03")


def test_gst_18pct_on_brokerage_plus_exchange_plus_sebi(
    model: IndianFeeModel,
):
    t = Trade(
        symbol="RELIANCE",
        exchange="NSE",
        side="BUY",
        product="INTRADAY",
        qty=1000,
        price=Decimal("100.00"),
    )
    fb = model.compute(t)
    # brokerage 20.00 + exchange 2.97 + SEBI 0.10 = 23.07 ; 18% = 4.15
    expected = (
        fb.brokerage_inr + fb.exchange_txn_inr + fb.sebi_inr
    ) * Decimal("0.18")
    expected = expected.quantize(Decimal("0.01"))
    assert fb.gst_inr == expected


# ---- Total + breakdown sanity --------------------------------------


def test_total_equals_sum_of_components(model: IndianFeeModel):
    t = Trade(
        symbol="RELIANCE",
        exchange="NSE",
        side="SELL",
        product="DELIVERY",
        qty=10,
        price=Decimal("3000.00"),
    )
    fb = model.compute(t)
    expected_total = (
        fb.brokerage_inr
        + fb.stt_inr
        + fb.exchange_txn_inr
        + fb.sebi_inr
        + fb.stamp_duty_inr
        + fb.gst_inr
        + fb.dp_charges_inr
    )
    assert fb.total_inr == expected_total


def test_total_inr_is_decimal_type(model: IndianFeeModel):
    t = Trade(
        symbol="RELIANCE",
        exchange="NSE",
        side="BUY",
        product="DELIVERY",
        qty=1,
        price=Decimal("100.00"),
    )
    fb = model.compute(t)
    assert isinstance(fb.total_inr, Decimal)


def test_zero_qty_returns_all_zero(model: IndianFeeModel):
    t = Trade(
        symbol="RELIANCE",
        exchange="NSE",
        side="BUY",
        product="DELIVERY",
        qty=0,
        price=Decimal("2945.20"),
    )
    fb = model.compute(t)
    assert fb.total_inr == Decimal("0.00")


def test_fractional_price_rounding(model: IndianFeeModel):
    # 0.05 paise on a 7-digit notional must round to 2dp INR
    t = Trade(
        symbol="X",
        exchange="NSE",
        side="BUY",
        product="DELIVERY",
        qty=1,
        price=Decimal("12345.67"),
    )
    fb = model.compute(t)
    # All component fields must be 2-decimal-place quantized
    for field_name in (
        "brokerage_inr",
        "stt_inr",
        "exchange_txn_inr",
        "sebi_inr",
        "stamp_duty_inr",
        "gst_inr",
        "dp_charges_inr",
        "total_inr",
    ):
        v = getattr(fb, field_name)
        assert v.as_tuple().exponent == -2, f"{field_name} not 2dp: {v}"


# ---- Versioning ----------------------------------------------------


def test_fee_rates_version_stamp(model: IndianFeeModel):
    assert model.rates_version == "2026-04-01"


def test_unknown_date_raises(model_class=IndianFeeModel):
    with pytest.raises(ValueError, match="No fee rates"):
        IndianFeeModel(as_of=date(1999, 1, 1))


# ---- Validation ----------------------------------------------------


def test_unknown_exchange_raises(model: IndianFeeModel):
    with pytest.raises(ValueError, match="exchange"):
        Trade(
            symbol="X",
            exchange="MCX",
            side="BUY",
            product="DELIVERY",
            qty=1,
            price=Decimal("100"),
        )


def test_unknown_side_raises():
    with pytest.raises(ValueError):
        Trade(
            symbol="X",
            exchange="NSE",
            side="HOLD",
            product="DELIVERY",
            qty=1,
            price=Decimal("100"),
        )


def test_unknown_product_raises():
    with pytest.raises(ValueError):
        Trade(
            symbol="X",
            exchange="NSE",
            side="BUY",
            product="MARGIN",
            qty=1,
            price=Decimal("100"),
        )


def test_negative_qty_raises():
    with pytest.raises(ValueError):
        Trade(
            symbol="X",
            exchange="NSE",
            side="BUY",
            product="DELIVERY",
            qty=-1,
            price=Decimal("100"),
        )


def test_negative_price_raises():
    with pytest.raises(ValueError):
        Trade(
            symbol="X",
            exchange="NSE",
            side="BUY",
            product="DELIVERY",
            qty=1,
            price=Decimal("-1"),
        )


# ---- Model output shape -------------------------------------------


def test_breakdown_is_pydantic_model(model: IndianFeeModel):
    t = Trade(
        symbol="RELIANCE",
        exchange="NSE",
        side="BUY",
        product="DELIVERY",
        qty=10,
        price=Decimal("2945.20"),
    )
    fb = model.compute(t)
    assert isinstance(fb, FeeBreakdown)
    # All fields must be Decimal (no float drift)
    for k, v in fb.model_dump().items():
        if isinstance(v, str):
            continue  # rates_version
        assert isinstance(
            v, Decimal
        ), f"{k} is {type(v).__name__}, expected Decimal"


def test_compute_does_not_mutate_trade(model: IndianFeeModel):
    t = Trade(
        symbol="RELIANCE",
        exchange="NSE",
        side="BUY",
        product="DELIVERY",
        qty=10,
        price=Decimal("2945.20"),
    )
    snap = t.model_dump()
    model.compute(t)
    assert t.model_dump() == snap


# ---- Route smoke ----------------------------------------------------

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from auth.dependencies import pro_or_superuser  # noqa: E402
from auth.models import UserContext  # noqa: E402
from backend.algo.routes.fees import create_fees_router  # noqa: E402


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(create_fees_router(), prefix="/v1")
    app.dependency_overrides[pro_or_superuser] = lambda: UserContext(
        user_id="user-test", email="t@t", role="superuser",
    )
    return app


def test_route_returns_breakdown_shape():
    app = _build_app()
    client = TestClient(app)
    r = client.get(
        "/v1/algo/fees/preview"
        "?symbol=RELIANCE&exchange=NSE&side=BUY&product=DELIVERY"
        "&qty=10&price=2945.20",
    )
    assert r.status_code == 200
    body = r.json()
    for k in (
        "brokerage_inr", "stt_inr", "exchange_txn_inr", "sebi_inr",
        "stamp_duty_inr", "gst_inr", "dp_charges_inr", "total_inr",
        "rates_version",
    ):
        assert k in body


def test_route_rejects_invalid_exchange():
    app = _build_app()
    client = TestClient(app)
    r = client.get(
        "/v1/algo/fees/preview?exchange=MCX",
    )
    assert r.status_code == 422  # FastAPI Query pattern violation


def test_route_rejects_negative_qty():
    app = _build_app()
    client = TestClient(app)
    r = client.get(
        "/v1/algo/fees/preview?qty=-5",
    )
    assert r.status_code == 422
