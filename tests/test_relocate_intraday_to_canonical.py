"""Unit tests for ``scripts.relocate_intraday_to_canonical``.

These exercise the catalog state-machine (``_prepare_state``) and the
relocate happy path + failure-recovery path with lightweight fakes —
no real Iceberg catalog, no warehouse I/O.
"""

from __future__ import annotations

import pytest

from pyiceberg.exceptions import NoSuchTableError

import scripts.relocate_intraday_to_canonical as m


class _FakeArrow:
    """Stand-in for a PyArrow table returned by ``scan().to_arrow()``."""

    def __init__(self, rows):
        self._rows = rows

    @property
    def num_rows(self):
        return len(self._rows)

    def column(self, _name):
        # Only used for the year_month distinct scan.
        return self

    def to_pylist(self):
        return [r["year_month"] for r in self._rows]

    def select(self, _names):
        return self

    def cast(self, _schema):
        return self


class _FakeScan:
    def __init__(self, rows):
        self._rows = rows

    def to_arrow(self):
        return _FakeArrow(self._rows)


class _FakeArrowSchema:
    names = ("ticker", "year_month", "bar_date_d")


class _FakeIcebergSchema:
    def as_arrow(self):
        return _FakeArrowSchema()


class _FakeTable:
    """Source table: serves scans; target table: collects appends."""

    def __init__(self, rows):
        self._rows = rows
        self.appended = []

    def scan(self, selected_fields=None, row_filter=None):
        if row_filter is not None:
            ym = row_filter.literal.value
            sel = [r for r in self._rows if r["year_month"] == ym]
            return _FakeScan(sel)
        return _FakeScan(self._rows)

    def schema(self):
        return _FakeIcebergSchema()

    def append(self, chunk):
        self.appended.append(chunk)


class _FakeCatalog:
    def __init__(self, tables):
        # tables: dict[identifier -> _FakeTable]
        self.tables = dict(tables)
        self.created = []
        self.dropped = []
        self.renames = []

    def load_table(self, identifier):
        if identifier not in self.tables:
            raise NoSuchTableError(identifier)
        return self.tables[identifier]

    def rename_table(self, src, dst):
        self.renames.append((src, dst))
        self.tables[dst] = self.tables.pop(src)

    def create_table(self, identifier, schema, partition_spec,
                     sort_order):
        self.created.append(identifier)
        # Fresh empty target table.
        self.tables[identifier] = _FakeTable([])

    def drop_table(self, identifier):
        self.dropped.append(identifier)
        self.tables.pop(identifier, None)


def _src_rows():
    return [
        {"ticker": "AAA", "year_month": "2026-05", "bar_date_d": 1},
        {"ticker": "AAA", "year_month": "2026-05", "bar_date_d": 1},
        {"ticker": "BBB", "year_month": "2026-06", "bar_date_d": 2},
    ]


def _schema_fn():
    return _FakeIcebergSchema()


def _sort_order_fn(_schema):
    return object()


@pytest.fixture(autouse=True)
def _patch_spec(monkeypatch):
    # Partition-spec builder needs a real Schema; stub it for the fakes.
    monkeypatch.setattr(
        m, "_ticker_bucket_month_partition_spec", lambda _s: object()
    )


def test_happy_path_relocates_and_drops_keep():
    canonical = "stocks.intraday_bars"
    src = _FakeTable(_src_rows())
    cat = _FakeCatalog({canonical: src})

    res = m.relocate_table(
        cat, canonical, _schema_fn, _sort_order_fn
    )

    assert res == {"old_rows": 3, "new_rows": 3, "relocated": True}
    keep = f"{canonical}_keep"
    # canonical renamed to _keep, then a fresh canonical created.
    assert (canonical, keep) in cat.renames
    assert canonical in cat.created
    # _keep dropped on success (catalog only, no purge arg).
    assert keep in cat.dropped
    # canonical table now exists and received the copied rows.
    assert canonical in cat.tables
    appended_rows = sum(
        c.num_rows for c in cat.tables[canonical].appended
    )
    assert appended_rows == 3


class _DropMonthTable(_FakeTable):
    """Source whose baseline (ticker) scan counts ALL rows but whose
    per-month copy silently skips one month — forcing a row mismatch
    (new_rows < old_rows) to drive the recovery path.
    """

    def scan(self, selected_fields=None, row_filter=None):
        if (
            row_filter is None
            and selected_fields == ("year_month",)
        ):
            # Hide one month so its rows are never copied.
            visible = [r for r in self._rows if r["year_month"]
                       != "2026-06"]
            return _FakeScan(visible)
        return super().scan(selected_fields, row_filter)


def test_parity_mismatch_recovers_pre_relocate_state():
    canonical = "stocks.intraday_bars"
    src = _DropMonthTable(_src_rows())
    cat = _FakeCatalog({canonical: src})

    with pytest.raises(RuntimeError, match="row mismatch"):
        m.relocate_table(cat, canonical, _schema_fn, _sort_order_fn)

    keep = f"{canonical}_keep"
    # Recovery: partial canonical dropped, _keep renamed back.
    assert canonical in cat.dropped
    assert (keep, canonical) in cat.renames
    # The live table is back under the canonical name (the original
    # source object), and _keep is gone.
    assert cat.tables[canonical] is src
    assert keep not in cat.tables


def test_prepare_state_ambiguous_missing_canonical_stops():
    canonical = "stocks.intraday_bars"
    keep = f"{canonical}_keep"
    cat = _FakeCatalog({keep: _FakeTable(_src_rows())})

    res = m.relocate_table(
        cat, canonical, _schema_fn, _sort_order_fn
    )

    assert res == {"old_rows": 0, "new_rows": 0, "relocated": False}
    # No mutation attempted.
    assert cat.created == []
    assert cat.dropped == []
    assert cat.renames == []


def test_prepare_state_both_exist_self_heals_then_relocates():
    canonical = "stocks.intraday_bars"
    keep = f"{canonical}_keep"
    # Live data under _keep; a partial fresh canonical also present.
    live = _FakeTable(_src_rows())
    partial = _FakeTable([])
    cat = _FakeCatalog({canonical: partial, keep: live})

    res = m.relocate_table(
        cat, canonical, _schema_fn, _sort_order_fn
    )

    assert res["relocated"] is True
    assert res["old_rows"] == 3
    assert res["new_rows"] == 3
    # Self-heal dropped the partial canonical and restored _keep first.
    assert canonical in cat.dropped
    assert (keep, canonical) in cat.renames
