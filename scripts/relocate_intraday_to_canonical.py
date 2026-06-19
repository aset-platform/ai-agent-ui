"""One-time relocation of the three intraday Iceberg tables back into
their canonical warehouse directories.

A prior migration (``scripts/migrate_intraday_partition_spec.py``)
rebuilt ``stocks.intraday_bars``, ``stocks.index_intraday_bars`` and
``stocks.intraday_features`` onto the
``BucketTransform(16, ticker) + MonthTransform(bar_date_d)`` spec by
writing fresh ``_v2`` tables and then ``rename_table``-ing them into the
canonical names. PyIceberg's ``SqlCatalog.rename_table`` only repoints
the catalog entry — it does NOT move the underlying files. So each live
table's data physically lives under ``.../warehouse/stocks/<name>_v2/``
while the canonical-named directory (``.../warehouse/stocks/<name>/``)
still holds the ORPHANED old files.

This is a correctness problem: all maintenance / backup / compaction
code resolves a table's on-disk directory from its NAME
(``WAREHOUSE_DIR / name.replace(".", "/")``), so today those tools point
at the stale orphan dirs, not the live data. This script relocates each
live table so that ``name == directory`` again.

Per-table strategy (``relocate_table``)::

    1. rename canonical -> canonical_keep    (frees the canonical name;
       _keep still points at the live _v2-backed data)
    2. create_table(canonical, new schema/spec/sort)  (fresh table in
       the canonical dir; that dir still contains the old orphan files —
       expected and fine, they are swept later)
    3. copy ALL rows _keep -> canonical, chunked by year_month
    4. parity check: new_rows != old_rows -> RAISE, and in the failure
       path RECOVER (drop the partial canonical, rename _keep back to
       canonical) so the working pre-relocate table stays live
    5. on success: drop_table(canonical_keep)  (catalog entry only, NO
       purge — its _v2 dir files become orphans removed separately,
       CLAUDE.md §4.3 #20: never ``rm`` Iceberg files)

Idempotency / recovery from a half-done prior run: BEFORE touching a
table we inspect catalog state. If the canonical name is MISSING but a
``_keep`` exists, that is an ambiguous half-relocated state (we cannot
tell whether the canonical was already dropped intentionally or a run
died between steps 1 and 2) — we LOG and STOP for that table rather than
guessing, so an operator can inspect. If BOTH canonical and ``_keep``
exist, a prior run died after step 1 but before completing; the
canonical may be a partial fresh table — we drop that partial canonical
and rename ``_keep`` back, restoring the safe pre-relocate state, then
proceed with a clean relocate.

Run INSIDE the backend container, AFTER review, AFTER a backup exists
(the data is already backed up by the prior migration step and already
carries a correct non-null ``bar_date_d`` column, so NO backup and NO
``bar_date_d`` derivation are performed here)::

    docker compose exec -T backend \\
        python -m scripts.relocate_intraday_to_canonical

This script mutates production catalog entries. It is authored +
unit-tested here; the live run against production is gated on human
approval and executed by the controller.
"""

from __future__ import annotations

import logging

from pyiceberg.exceptions import NoSuchTableError
from pyiceberg.expressions import EqualTo

from stocks.create_tables import (
    _get_catalog,
    _index_intraday_bars_schema,
    _index_intraday_bars_sort_order,
    _intraday_bars_schema,
    _intraday_bars_sort_order,
    _intraday_features_schema,
    _intraday_features_sort_order,
    _ticker_bucket_month_partition_spec,
)

_logger = logging.getLogger(__name__)

_TABLES = [
    (
        "stocks.intraday_bars",
        _intraday_bars_schema,
        _intraday_bars_sort_order,
    ),
    (
        "stocks.index_intraday_bars",
        _index_intraday_bars_schema,
        _index_intraday_bars_sort_order,
    ),
    (
        "stocks.intraday_features",
        _intraday_features_schema,
        _intraday_features_sort_order,
    ),
]


def _table_exists(catalog, identifier: str) -> bool:
    """Return True iff ``identifier`` resolves to a catalog entry."""
    try:
        catalog.load_table(identifier)
        return True
    except NoSuchTableError:
        return False


def _prepare_state(catalog, canonical: str, keep: str) -> bool:
    """Normalise catalog state before a relocate, returning whether to
    proceed.

    Returns True when ``canonical`` exists and ``keep`` does not — the
    clean starting state for a relocate.

    Returns False (caller STOPs for this table) when ``canonical`` is
    missing but ``keep`` exists — an ambiguous half-relocated state we
    refuse to guess about.

    Self-heals when BOTH exist (a prior run died mid-relocate): the
    partial fresh ``canonical`` is dropped and ``keep`` is renamed back
    to ``canonical``, restoring the safe pre-relocate state; returns
    True so the relocate can start clean.
    """
    has_canonical = _table_exists(catalog, canonical)
    has_keep = _table_exists(catalog, keep)

    if has_canonical and not has_keep:
        return True

    if not has_canonical and has_keep:
        _logger.error(
            "[relocate] %s: canonical MISSING but %s exists — ambiguous "
            "half-relocated state; STOPPING for this table, inspect "
            "manually",
            canonical,
            keep,
        )
        return False

    if has_canonical and has_keep:
        _logger.warning(
            "[relocate] %s: both canonical and %s exist (aborted prior "
            "run) — dropping partial canonical and restoring %s -> "
            "canonical",
            canonical,
            keep,
            keep,
        )
        catalog.drop_table(canonical)
        catalog.rename_table(keep, canonical)
        return True

    _logger.error(
        "[relocate] %s: neither canonical nor %s exists — nothing to "
        "relocate; STOPPING for this table",
        canonical,
        keep,
    )
    return False


def relocate_table(catalog, canonical, schema_fn, sort_order_fn) -> dict:
    """Relocate ``canonical`` back into its canonical warehouse dir by
    renaming the live table aside, recreating it in the canonical dir,
    and copying all rows across with row-count parity enforcement.

    On a row-count mismatch this RAISES, and in the same failure path
    RECOVERS the pre-relocate state by dropping the partial fresh
    canonical and renaming the ``_keep`` table back to ``canonical`` —
    so a mid-copy failure leaves the original working table live with no
    data loss.

    Args:
        catalog: An open ``SqlCatalog``.
        canonical: ``namespace.table`` identifier
            (e.g. ``"stocks.intraday_bars"``).
        schema_fn: Returns the new (``bar_date_d``-bearing) Schema.
        sort_order_fn: Takes the schema, returns its SortOrder.

    Returns:
        ``{"old_rows": int, "new_rows": int, "relocated": bool}``. When
        the catalog state is a half-done prior run we cannot safely
        resolve, returns ``relocated=False`` without mutating anything.
    """
    keep = f"{canonical}_keep"

    if not _prepare_state(catalog, canonical, keep):
        return {"old_rows": 0, "new_rows": 0, "relocated": False}

    schema = schema_fn()
    spec = _ticker_bucket_month_partition_spec(schema)
    sort_order = sort_order_fn(schema)

    # Step 1: free the canonical name; _keep still points at the live
    # _v2-backed data.
    catalog.rename_table(canonical, keep)
    src = catalog.load_table(keep)

    # Baseline count via a single-column scan — NEVER an unfiltered
    # full ``.to_arrow()`` (the features table is ~70M rows and would
    # OOM the 11.7GB container).
    old_rows = src.scan(selected_fields=("ticker",)).to_arrow().num_rows
    _logger.info("[relocate] %s: %d rows to copy", canonical, old_rows)

    # Step 2: fresh table written into the canonical dir (still holding
    # orphan files — expected; swept later per CLAUDE.md §4.3 #20).
    catalog.create_table(
        identifier=canonical,
        schema=schema,
        partition_spec=spec,
        sort_order=sort_order,
    )
    new_tbl = catalog.load_table(canonical)
    target_arrow_schema = new_tbl.schema().as_arrow()

    # Step 3: copy chunked by year_month to bound peak memory. _keep's
    # schema already matches the target (built with the new schema incl.
    # a correct non-null bar_date_d) — no bar_date_d derivation needed,
    # just select-by-name + positional cast for safety.
    months = sorted(
        set(
            src.scan(selected_fields=("year_month",))
            .to_arrow()
            .column("year_month")
            .to_pylist()
        )
    )

    new_rows = 0
    try:
        for ym in months:
            chunk = src.scan(
                row_filter=EqualTo("year_month", ym)
            ).to_arrow()
            if chunk.num_rows == 0:
                continue
            chunk = chunk.select(target_arrow_schema.names).cast(
                target_arrow_schema
            )
            new_tbl.append(chunk)
            new_rows += chunk.num_rows
            _logger.info(
                "[relocate] %s %s: +%d rows",
                canonical,
                ym,
                chunk.num_rows,
            )

        # Step 4: parity. A mismatch RAISES into the except below.
        if new_rows != old_rows:
            raise RuntimeError(
                f"[relocate] {canonical} row mismatch: "
                f"old={old_rows} new={new_rows}"
            )
    except Exception:
        # Recover the pre-relocate state: drop the partial fresh
        # canonical, rename _keep back to canonical, then re-raise so
        # the original working table stays live with no data loss.
        _logger.error(
            "[relocate] %s: copy failed (old=%d new=%d) — recovering "
            "pre-relocate state",
            canonical,
            old_rows,
            new_rows,
            exc_info=True,
        )
        catalog.drop_table(canonical)
        catalog.rename_table(keep, canonical)
        raise

    # Step 5: success — drop the _keep catalog entry only (NO purge;
    # its _v2 dir files become orphans swept separately, never ``rm``,
    # CLAUDE.md §4.3 #20).
    catalog.drop_table(keep)
    _logger.info(
        "[relocate] %s: relocated, %d rows now in canonical dir",
        canonical,
        new_rows,
    )
    return {
        "old_rows": old_rows,
        "new_rows": new_rows,
        "relocated": True,
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    catalog = _get_catalog()
    for canonical, schema_fn, sort_order_fn in _TABLES:
        _logger.info("[relocate] starting %s", canonical)
        res = relocate_table(
            catalog, canonical, schema_fn, sort_order_fn
        )
        _logger.info("[relocate] %s done: %s", canonical, res)


if __name__ == "__main__":
    main()
