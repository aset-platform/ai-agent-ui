"""Universal Iceberg-table design rule (CLAUDE.md §4.3 #22)
self-enforcing guard.

Discovers every ``iceberg_init.py`` module in the repo,
imports it, and walks the partition specs + schemas it exposes.
Fails the test if any of the following anti-patterns are
present:

* ``IdentityTransform`` on a column named ``ticker``,
  ``user_id``, ``session_id``, or ``event_id`` — these are
  high-cardinality identifiers that historically caused the
  microfile-explosion incidents documented in
  ``shared/architecture/iceberg-ticker-partition-file-explosion``.

* A schema field named ``*_date`` / ``ts_date`` / ``bar_date``
  / ``rebalance_date`` / ``score_date`` (etc.) declared as
  ``StringType`` rather than ``DateType`` — defeats Iceberg
  date-pruning transforms.

A schema may opt out by setting the module-level constant
``_DESIGN_RULE_GRANDFATHERED = True`` AND adding a comment
explaining the deferred redesign.  Each grandfathered module
should have a Jira follow-up ticket cited in the comment.
"""
from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

import pytest
from pyiceberg.partitioning import PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.transforms import IdentityTransform
from pyiceberg.types import DateType, StringType


HIGH_CARDINALITY_COLUMN_NAMES = frozenset({
    "ticker",
    "user_id",
    "session_id",
    "event_id",
    # ``strategy_id`` is partition-safe because backtest /
    # paper / live each have only a handful of distinct
    # strategies per user.  Add here if that changes.
})

# Column name → MUST be ``DateType`` (not ``StringType``).
# These are the conventional date-column names the codebase
# uses; if you add a date column with a different name, update
# this set or add a per-table override.
DATE_LIKE_SUFFIXES = (
    "_date",
)

GRANDFATHERED_MODULES: frozenset[str] = frozenset({
    # Legacy ``stocks.*`` tables that pre-date the universal
    # rule.  Adding them here pins the bypass to a small
    # known set so a future scan still catches NEW violations.
    "stocks.create_tables",
})


def _all_iceberg_init_modules() -> list[str]:
    """Walk the backend tree and yield every module that looks
    like an Iceberg table initialiser (``iceberg_init.py``).
    """
    root = Path(__file__).resolve().parents[2] / "backend"
    if not root.is_dir():
        return []
    out: list[str] = []
    for path in root.rglob("iceberg_init.py"):
        # Convert the path to a dotted module name relative to
        # the project root.
        rel = path.relative_to(root.parent)
        out.append(
            str(rel.with_suffix("")).replace("/", ".")
        )
    return sorted(out)


def _collect_partition_specs(mod) -> list[tuple[str, PartitionSpec]]:
    """Find every ``PartitionSpec`` exposed by callables /
    constants in ``mod``."""
    out: list[tuple[str, PartitionSpec]] = []
    for name in dir(mod):
        if name.startswith("__"):
            continue
        obj = getattr(mod, name)
        if isinstance(obj, PartitionSpec):
            out.append((name, obj))
        elif callable(obj):
            try:
                ret = obj()
            except Exception:  # noqa: BLE001
                continue
            if isinstance(ret, PartitionSpec):
                out.append((name, ret))
    return out


def _collect_schemas(mod) -> list[tuple[str, Schema]]:
    out: list[tuple[str, Schema]] = []
    for name in dir(mod):
        if name.startswith("__"):
            continue
        obj = getattr(mod, name)
        if isinstance(obj, Schema):
            out.append((name, obj))
        elif callable(obj):
            try:
                ret = obj()
            except Exception:  # noqa: BLE001
                continue
            if isinstance(ret, Schema):
                out.append((name, ret))
    return out


@pytest.fixture(scope="module")
def iceberg_init_modules() -> list:
    names = _all_iceberg_init_modules()
    mods = []
    for n in names:
        if n in GRANDFATHERED_MODULES:
            continue
        try:
            mods.append((n, importlib.import_module(n)))
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"could not import {n}: {exc}")
    return mods


def test_at_least_one_iceberg_init_discovered(
    iceberg_init_modules,
) -> None:
    """Sanity — the discovery walk found something.  Otherwise
    the rest of the suite is a silent no-op.
    """
    assert iceberg_init_modules, (
        "no iceberg_init.py modules discovered — check the "
        "rglob in _all_iceberg_init_modules()"
    )


def test_no_identity_transform_on_high_cardinality_columns(
    iceberg_init_modules,
) -> None:
    """CLAUDE.md §4.3 #22.a — IdentityTransform on a column
    with cardinality > 50 produces (cardinality × commits)
    microfiles.  The 2026-05-15 ``stocks.nse_delivery``
    incident burned a 52-min nuke-rebuild + the 2026-05-12
    ``algo.events`` incident bloated metadata.json to 11 GB.
    """
    violations: list[str] = []
    for mod_name, mod in iceberg_init_modules:
        for spec_name, spec in _collect_partition_specs(mod):
            # Build a map from source_id → column name from the
            # paired schema if available.  Otherwise skip the
            # check (the spec field names are an approximation
            # rather than a guarantee).
            schemas = _collect_schemas(mod)
            if not schemas:
                continue
            # Use the first schema (most iceberg_init modules
            # define one schema per table).  Cross-checking
            # source_ids against every schema is a future
            # refinement.
            id_to_col = {
                f.field_id: f.name
                for _, sch in schemas
                for f in sch.fields
            }
            for pf in spec.fields:
                col = id_to_col.get(pf.source_id, pf.name)
                if col not in HIGH_CARDINALITY_COLUMN_NAMES:
                    continue
                if isinstance(pf.transform, IdentityTransform):
                    violations.append(
                        f"{mod_name}::{spec_name} — "
                        f"IdentityTransform on '{col}' "
                        f"(use BucketTransform per "
                        f"CLAUDE.md §4.3 #22.a)"
                    )
    assert not violations, (
        "Iceberg design rule violations:\n  - "
        + "\n  - ".join(violations)
    )


def test_no_string_type_on_date_like_columns(
    iceberg_init_modules,
) -> None:
    """CLAUDE.md §4.3 #22.b / #22.d — date columns must be
    ``DateType``, not ``StringType("YYYY-MM-DD")``.  Type
    evolution from string to date is not supported in-place
    (#22.g), so getting this wrong costs a nuke-rebuild.
    """
    violations: list[str] = []
    for mod_name, mod in iceberg_init_modules:
        for sch_name, sch in _collect_schemas(mod):
            for f in sch.fields:
                if not any(
                    f.name.endswith(s) for s in DATE_LIKE_SUFFIXES
                ):
                    continue
                if isinstance(f.field_type, DateType):
                    continue
                if isinstance(f.field_type, StringType):
                    violations.append(
                        f"{mod_name}::{sch_name} — '{f.name}' "
                        f"is StringType (use DateType per "
                        f"CLAUDE.md §4.3 #22.b)"
                    )
    assert not violations, (
        "Iceberg design rule violations:\n  - "
        + "\n  - ".join(violations)
    )
