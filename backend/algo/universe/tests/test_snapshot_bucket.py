"""Tests for liquidity bucket derivation in snapshot_job (PR #2).

Covers:
- ``_derive_liquidity_bucket(...)`` populates the bucket on each row.
- Top-100-by-mcap downgrade rule: mcap >= 20k cr but NOT in top-100
  → midcap, not largecap.
- Both signals (mcap + adtv) combine via slippage.classify (more
  conservative wins).
"""
from __future__ import annotations

from datetime import date

import pytest

from backend.algo.universe import snapshot_job


# ----------------------------------------------------------------
# _derive_liquidity_bucket — row-level bucket annotation
# ----------------------------------------------------------------


class TestDeriveLiquidityBucket:
    """The helper takes the candidate list (already cap+adtv
    filtered, sorted by adtv desc, with included_in_top_200
    set) and returns the same list with ``liquidity_bucket`` +
    ``is_top100_mcap`` annotations."""

    def test_largecap_when_mcap_high_and_top100_and_adtv_high(
        self,
    ) -> None:
        rows = [
            {
                "ticker": "RELIANCE.NS",
                "adtv_inr_60d": 100e7,       # 100 crore/day
                "market_cap_inr": 25000e7,   # 25,000 crore
                "sector": "Energy",
            },
        ]
        out = snapshot_job._derive_liquidity_bucket(rows)
        assert out[0]["liquidity_bucket"] == "largecap"
        assert out[0]["is_top100_mcap"] is True

    def test_downgrade_largecap_to_midcap_when_not_top100(
        self,
    ) -> None:
        """mcap >= 20k cr but NOT in top-100 → midcap downgrade."""
        # Build 101 tickers all with mcap >= 20k cr; ranks 1..100
        # are top-100 (largecap). Rank 101 is downgraded.
        rows = []
        for i in range(101):
            rows.append({
                "ticker": f"T{i:03d}.NS",
                "adtv_inr_60d": 100e7,                   # large adtv
                "market_cap_inr": (50000 - i * 100) * 1e7,
                "sector": "X",
            })
        out = snapshot_job._derive_liquidity_bucket(rows)
        out_by_ticker = {r["ticker"]: r for r in out}
        # Top ranked (highest mcap) → largecap.
        assert out_by_ticker["T000.NS"]["is_top100_mcap"] is True
        assert (
            out_by_ticker["T000.NS"]["liquidity_bucket"] == "largecap"
        )
        # Rank 100 still top-100 (0-indexed: T099) → largecap.
        assert out_by_ticker["T099.NS"]["is_top100_mcap"] is True
        assert (
            out_by_ticker["T099.NS"]["liquidity_bucket"] == "largecap"
        )
        # Rank 101 (T100) is OUTSIDE top-100 → downgrade to midcap.
        assert out_by_ticker["T100.NS"]["is_top100_mcap"] is False
        # mcap=39_900 cr still >= 20k cr, adtv=large, but not
        # in top-100, so bucket downgrades.
        assert (
            out_by_ticker["T100.NS"]["liquidity_bucket"] != "largecap"
        )

    def test_midcap_bucket_when_mid_signals(self) -> None:
        """Midcap signals on both axes → midcap bucket.

        ``is_top100_mcap`` is rank-based across the cohort. With a
        single-row cohort the ticker is trivially rank #1 and the
        flag is True; that's fine and doesn't affect the bucket
        (only largecap-by-signal is downgraded by rank).
        """
        rows = [
            {
                "ticker": "MIDCAP1.NS",
                "adtv_inr_60d": 30e7,         # 30 cr — midcap range
                "market_cap_inr": 10000e7,    # 10,000 cr — mid
                "sector": "X",
            },
        ]
        out = snapshot_job._derive_liquidity_bucket(rows)
        assert out[0]["liquidity_bucket"] == "midcap"
        # Bucket-by-signal is midcap so top-100 downgrade is moot.
        # Flag is True trivially (single-row cohort).
        assert out[0]["is_top100_mcap"] is True

    def test_smallcap_bucket_when_small_signals(self) -> None:
        rows = [
            {
                "ticker": "SMALL.NS",
                "adtv_inr_60d": 5e7,          # 5 cr — small
                "market_cap_inr": 2000e7,     # 2000 cr — small
                "sector": "X",
            },
        ]
        out = snapshot_job._derive_liquidity_bucket(rows)
        assert out[0]["liquidity_bucket"] == "smallcap"

    def test_conservative_wins_largecap_mcap_midcap_adtv(
        self,
    ) -> None:
        """mcap=large + in top-100 BUT adtv=mid → midcap."""
        rows = [
            {
                "ticker": "DRYLARGE.NS",
                "adtv_inr_60d": 30e7,         # 30 cr — midcap
                "market_cap_inr": 25000e7,    # 25,000 cr — large
                "sector": "X",
            },
        ]
        out = snapshot_job._derive_liquidity_bucket(rows)
        assert out[0]["liquidity_bucket"] == "midcap"

    def test_missing_market_cap_falls_back_to_smallcap(self) -> None:
        rows = [
            {
                "ticker": "NOMCAP.NS",
                "adtv_inr_60d": 100e7,
                "market_cap_inr": 0,        # treated as missing
                "sector": "X",
            },
        ]
        out = snapshot_job._derive_liquidity_bucket(rows)
        # mcap missing, adtv present → smallcap (either-side missing).
        assert out[0]["liquidity_bucket"] == "smallcap"

    def test_preserves_existing_keys(self) -> None:
        rows = [
            {
                "ticker": "T.NS",
                "adtv_inr_60d": 100e7,
                "market_cap_inr": 25000e7,
                "sector": "Energy",
                "included_in_top_200": True,
            },
        ]
        out = snapshot_job._derive_liquidity_bucket(rows)
        assert out[0]["ticker"] == "T.NS"
        assert out[0]["sector"] == "Energy"
        assert out[0]["included_in_top_200"] is True
        # New keys present.
        assert "liquidity_bucket" in out[0]
        assert "is_top100_mcap" in out[0]


# ----------------------------------------------------------------
# rebuild_universe_snapshot integration — buckets propagate
# ----------------------------------------------------------------


class TestRebuildPropagatesBuckets:
    def test_rebuild_writes_bucket_per_row(self, monkeypatch) -> None:
        candidates = [
            {
                "ticker": "BIG.NS",
                "adtv_inr_60d": 100e7,        # 100 cr — largecap adtv
                "market_cap_inr": 30000e7,    # 30000 cr — large mcap
                "sector": "X",
            },
            {
                "ticker": "MID.NS",
                "adtv_inr_60d": 25e7,         # 25 cr — mid adtv
                "market_cap_inr": 10000e7,    # 10000 cr — mid mcap
                "sector": "X",
            },
            {
                "ticker": "SML.NS",
                # 5 cr — filtered out by 10 cr ADTV floor.
                "adtv_inr_60d": 5e7,
                "market_cap_inr": 4000e7,
                "sector": "X",
            },
        ]
        monkeypatch.setattr(
            snapshot_job, "_load_candidates", lambda d: candidates,
        )
        captured: list = []
        monkeypatch.setattr(
            snapshot_job,
            "_upsert_snapshot",
            lambda d, rows: captured.extend(rows),
        )
        snapshot_job.rebuild_universe_snapshot(date(2026, 5, 1))
        by_t = {r["ticker"]: r for r in captured}
        # SML.NS filtered out by the 10 cr ADTV floor.
        assert "SML.NS" not in by_t
        assert by_t["BIG.NS"]["liquidity_bucket"] == "largecap"
        assert by_t["MID.NS"]["liquidity_bucket"] == "midcap"
