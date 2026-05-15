"""Promotion-gate non-regression test (ASETPLTFRM-402 / FE-5).

FE-5 adds per-fill snapshot writes to a SEPARATE Iceberg
table (``stocks.trade_feature_snapshots``). The strategy
promotion workflow (PR #221) decides paper→live eligibility
by scanning ``algo.events WHERE mode='paper' AND
type='order_filled'``. If a future refactor ever rerouted
that gate query to read snapshots instead of events, the
``earned re-promotion`` semantics + paper-fill freshness
checks would silently drift.

This test asserts the gate's ``_paper_fill_stats`` source
contains ONLY ``algo.events`` references — NOT
``stocks.trade_feature_snapshots``.
"""

from __future__ import annotations

import inspect

from backend.algo.strategy import promotion as _prom


def test_paper_fill_stats_reads_algo_events_only() -> None:
    """The gate's paper-fill scan must continue to read
    from ``algo.events``. Adding snapshot reads here would
    rewrite the meaning of "earned re-promotion" — caught
    here at PR review time, not in production.
    """
    src = inspect.getsource(_prom._paper_fill_stats)

    assert "algo.events" in src, "Promotion gate must scan algo.events"
    assert "stocks.trade_feature_snapshots" not in src, (
        "FE-5 must not reroute the promotion gate to "
        "snapshots — keep the algo.events query as the "
        "single source of truth for paper-fill freshness."
    )
    # The mode/type filter pair must remain — these are the
    # exact conditions the eligibility logic depends on.
    assert "mode = 'paper'" in src
    assert "type = 'order_filled'" in src


def test_promotion_module_does_not_import_snapshots() -> None:
    """Defence-in-depth: the promotion module must not
    import anything from ``backend.algo.features.snapshots``.
    Importing it here would imply the gate is reading the
    snapshot table — keep the layering clean.
    """
    module_src = inspect.getsource(_prom)
    assert "backend.algo.features.snapshots" not in module_src, (
        "Promotion module must not depend on the FE-5 " "snapshot writer."
    )
