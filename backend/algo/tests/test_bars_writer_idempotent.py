"""Idempotency tests for ``backend.algo.stream.bars_writer``
(Task 7.4).

Test plan:
1. PREDICATE EXACTNESS (pure unit, no Iceberg) — the load-bearing
   safety test.  ``_dedup_predicate`` for a multi-bucket, multi-
   ticker batch must cover exactly the input triplets and MUST NOT
   produce a cross-product that covers (A,60,t2) / (B,60,t1) when
   the batch is [(A,60,t1), (B,60,t2)].
2. IDEMPOTENT ROUND-TRIP (real Iceberg) — flush the same 3-bar
   batch twice; assert exactly 3 rows in the table (not 6).
3. NO COLLATERAL DELETE (real Iceberg, data-loss guard) — pre-seed
   (PFX_A,60,t2); then flush [(PFX_A,60,t1),(PFX_B,60,t2)]; assert
   (PFX_A,60,t2) still exists.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.algo.stream.bars_writer import _dedup_predicate
from backend.algo.stream.types import Bar

# ────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────

_TEST_TICKER_PREFIX = "TSTBW_"

# Two synthetic nanosecond bar-open timestamps for two different
# 60-second buckets.  Chosen so that t1 != t2 and both are in the
# same calendar month (2026-04-01) to land in one Iceberg partition.
_T1_NS = 1743472500_000_000_000  # 2026-04-01 09:15:00 UTC
_T2_NS = 1743472560_000_000_000  # 2026-04-01 09:16:00 UTC


def _bar(
    ticker: str,
    ts_ns: int,
    interval_sec: int = 60,
) -> Bar:
    """Build a minimal Bar for use in flush / predicate tests."""
    return Bar(
        ticker=ticker,
        interval_sec=interval_sec,
        bar_open_ts_ns=ts_ns,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=1000,
        written_at=datetime.fromtimestamp(
            ts_ns / 1_000_000_000, tz=timezone.utc,
        ),
    )


# ────────────────────────────────────────────────────────────────
# Real-Iceberg fixtures
# ────────────────────────────────────────────────────────────────

_ALGO_INTRADAY_BARS_TABLE = "algo.intraday_bars"


@pytest.fixture(autouse=True)
def _clean_test_rows():
    """Remove any leftover TSTBW_ rows before and after each test
    so real-Iceberg tests are order-independent."""
    from pyiceberg.expressions import StartsWith

    from stocks.create_tables import _get_catalog

    def _wipe():
        try:
            cat = _get_catalog()
            tbl = cat.load_table(_ALGO_INTRADAY_BARS_TABLE)
            tbl.delete(StartsWith("ticker", _TEST_TICKER_PREFIX))
        except Exception:
            pass  # empty table on first run — fine

    _wipe()
    yield
    _wipe()


# ────────────────────────────────────────────────────────────────
# 1. PREDICATE EXACTNESS (pure unit — the safety-critical test)
# ────────────────────────────────────────────────────────────────


def test_dedup_predicate_covers_exact_triplets_not_cross_product():
    """The predicate for batch [(A,60,t1),(B,60,t2)] must match
    exactly those two triplets and NOT match (A,60,t2) or (B,60,t1)
    — the cross-product bug.

    We verify structurally: the predicate reduces to an Or of two
    exact And terms.  We extract which (ticker, interval_sec,
    bar_open_ts_ns) combinations the predicate would accept by
    inspecting what triplets are embedded in the term tree.  The
    covered set must equal exactly the input set — no extras.
    """
    from pyiceberg.expressions import And as _And
    from pyiceberg.expressions import EqualTo as _EQ
    from pyiceberg.expressions import Or as _Or

    ticker_a = f"{_TEST_TICKER_PREFIX}A.NS"
    ticker_b = f"{_TEST_TICKER_PREFIX}B.NS"

    bars = [
        _bar(ticker_a, _T1_NS, interval_sec=60),
        _bar(ticker_b, _T2_NS, interval_sec=60),
    ]
    pred = _dedup_predicate(bars)

    # Predicate must be an Or (two keys -> Or of two terms)
    assert isinstance(pred, _Or), (
        f"Expected pyiceberg Or, got {type(pred)}"
    )

    def _extract_triplets(node) -> set[tuple]:
        """Recursively collect (ticker, interval_sec, bar_open_ts_ns)
        triplets from an Or/And expression tree."""
        if isinstance(node, _Or):
            return _extract_triplets(node.left) | _extract_triplets(
                node.right,
            )
        if isinstance(node, _And):
            # Leaf And: And(EQ(ticker), And(EQ(interval), EQ(ts)))
            # Collect all EqualTo leaves in this sub-tree.
            leaves: dict[str, object] = {}

            def _collect(n) -> None:
                if isinstance(n, _EQ):
                    leaves[n.term.name] = n.literal.value
                elif isinstance(n, _And):
                    _collect(n.left)
                    _collect(n.right)

            _collect(node)
            return {(
                leaves["ticker"],
                leaves["interval_sec"],
                leaves["bar_open_ts_ns"],
            )}
        return set()

    covered = _extract_triplets(pred)
    expected = {
        (ticker_a, 60, _T1_NS),
        (ticker_b, 60, _T2_NS),
    }
    cross_product_extras = {
        (ticker_a, 60, _T2_NS),
        (ticker_b, 60, _T1_NS),
    }

    assert covered == expected, (
        f"Predicate covers wrong triplets: {covered}"
    )
    assert not (covered & cross_product_extras), (
        "Predicate covers cross-product keys — data-loss risk"
    )


def test_dedup_predicate_single_bar_returns_non_none():
    ticker = f"{_TEST_TICKER_PREFIX}S.NS"
    pred = _dedup_predicate([_bar(ticker, _T1_NS)])
    assert pred is not None


def test_dedup_predicate_empty_returns_none():
    assert _dedup_predicate([]) is None


def test_dedup_predicate_deduplicates_repeated_triplets():
    """Duplicate Bar objects with the same triplet collapse to ONE
    predicate term (no redundant OR arms)."""
    from pyiceberg.expressions import Or as _Or

    ticker = f"{_TEST_TICKER_PREFIX}D.NS"
    bars = [
        _bar(ticker, _T1_NS),
        _bar(ticker, _T1_NS),  # exact duplicate
    ]
    pred = _dedup_predicate(bars)
    # Only one distinct key -> must NOT be an Or
    assert not isinstance(pred, _Or), (
        "Single distinct key should not produce Or"
    )


# ────────────────────────────────────────────────────────────────
# 2. IDEMPOTENT ROUND-TRIP (real Iceberg)
# ────────────────────────────────────────────────────────────────


def test_flush_bars_idempotent_no_duplicates():
    """Flush the same 3-bar batch twice; assert exactly 3 rows."""
    from backend.algo.stream.bars_writer import flush_bars
    from backend.db.duckdb_engine import query_iceberg_table

    ticker = f"{_TEST_TICKER_PREFIX}IDM.NS"
    batch = [
        _bar(ticker, _T1_NS),
        _bar(ticker, _T2_NS),
        _bar(ticker, _T1_NS + 2 * 60 * 1_000_000_000),
    ]

    flush_bars(batch)
    flush_bars(batch)  # second flush — must be idempotent

    rows = query_iceberg_table(
        _ALGO_INTRADAY_BARS_TABLE,
        "SELECT COUNT(*) AS c FROM intraday_bars "
        "WHERE ticker = ?",
        [ticker],
    )
    assert rows[0]["c"] == 3, (
        f"Expected 3 rows after double-flush, got {rows[0]['c']}"
    )


# ────────────────────────────────────────────────────────────────
# 3. NO COLLATERAL DELETE (real Iceberg — data-loss guard)
# ────────────────────────────────────────────────────────────────


def test_flush_bars_no_collateral_delete():
    """Cross-product bug guard.

    Pre-seed (ticker_a,60,t2); then flush [(ticker_a,60,t1),
    (ticker_b,60,t2)].  The seed row (ticker_a,60,t2) is NOT in the
    flush batch; it must still exist after the flush (a cross-product
    delete would have wiped it because t2 appears in the batch via
    ticker_b).
    """
    from backend.algo.stream.bars_writer import flush_bars
    from backend.db.duckdb_engine import query_iceberg_table

    ticker_a = f"{_TEST_TICKER_PREFIX}NCA.NS"
    ticker_b = f"{_TEST_TICKER_PREFIX}NCB.NS"

    # Pre-seed the row that must NOT be deleted by the subsequent
    # flush.
    flush_bars([_bar(ticker_a, _T2_NS)])

    # Now flush a batch containing (ticker_a, t1) and (ticker_b, t2)
    # — neither of which is (ticker_a, t2).
    flush_bars([
        _bar(ticker_a, _T1_NS),
        _bar(ticker_b, _T2_NS),
    ])

    # The pre-seeded (ticker_a, t2) must survive.
    rows = query_iceberg_table(
        _ALGO_INTRADAY_BARS_TABLE,
        "SELECT COUNT(*) AS c FROM intraday_bars "
        "WHERE ticker = ? AND bar_open_ts_ns = ?",
        [ticker_a, _T2_NS],
    )
    assert rows[0]["c"] == 1, (
        f"(ticker_a, t2) was deleted by collateral delete bug; "
        f"count={rows[0]['c']}"
    )
