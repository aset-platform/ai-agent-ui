"""Round-trip + idempotency tests for daily_factors repo."""
from __future__ import annotations

from datetime import date

import pytest

from backend.algo.factors.repo import (
    FactorRow,
    get_factors_window,
    upsert_factors,
)


@pytest.mark.iceberg
def test_upsert_roundtrip() -> None:
    rows = [
        FactorRow(
            ticker="TEST.NS",
            bar_date=date(2026, 5, 8),
            values={
                "mom_12_1": 0.18,
                "f_score": 7.0,
                "realized_vol_60d": 0.22,
            },
            sector="IT",
        ),
    ]
    upsert_factors(rows)
    got = get_factors_window(
        ["TEST.NS"], date(2026, 5, 8), date(2026, 5, 8),
    )
    assert len(got) == 1
    assert got[0].values["mom_12_1"] == pytest.approx(0.18)
    assert got[0].sector == "IT"


@pytest.mark.iceberg
def test_upsert_same_key_overwrites() -> None:
    upsert_factors([
        FactorRow(
            ticker="TEST2.NS", bar_date=date(2026, 5, 8),
            values={"mom_12_1": 0.10}, sector="IT",
        ),
    ])
    upsert_factors([
        FactorRow(
            ticker="TEST2.NS", bar_date=date(2026, 5, 8),
            values={"mom_12_1": 0.50}, sector="IT",
        ),
    ])
    got = get_factors_window(
        ["TEST2.NS"], date(2026, 5, 8), date(2026, 5, 8),
    )
    assert len(got) == 1
    assert got[0].values["mom_12_1"] == pytest.approx(0.50)
