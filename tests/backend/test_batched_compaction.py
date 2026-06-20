from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pyarrow as pa

import backend.maintenance.iceberg_maintenance as im


def test_bar_month_to_year_month():
    f = im._bar_month_to_year_month
    assert f(677) == "2026-06"   # current month (verified vs inspect)
    assert f(576) == "2018-01"
    assert f(675) == "2026-04"
    assert f(0) == "1970-01"
    assert f(11) == "1970-12"
    assert f(12) == "1971-01"


def _fake_table(*, partitions, file_counts, has_ym=True):
    tbl = MagicMock()
    names = ["year_month"] if has_ym else ["ticker"]
    fields = []
    for n in names:
        fld = MagicMock()
        fld.name = n
        fields.append(fld)
    sch = MagicMock()
    sch.fields = fields
    sch.as_arrow.return_value = pa.schema(
        [("year_month", pa.string())]
    )
    tbl.schema.return_value = sch
    insp = MagicMock()
    insp.partitions.return_value.to_pydict.return_value = {
        "partition": partitions,
        "file_count": file_counts,
    }
    tbl.inspect = insp
    tbl.scan.return_value.to_arrow.return_value.cast.return_value = (
        pa.table({"year_month": ["2026-06"]})
    )
    return tbl


def _patches(tbl, tmp_path):
    repo = MagicMock()
    repo.load_table.return_value = tbl
    return [
        patch(
            "tools._stock_shared._require_repo",
            return_value=repo,
        ),
        patch.object(
            im, "retry_iceberg_op", lambda ident, op: op()
        ),
        patch.object(
            im, "invalidate_metadata", lambda *a, **k: None
        ),
        patch.object(
            im, "_count_parquet_files", lambda d: 0
        ),
        patch.object(im, "WAREHOUSE_DIR", tmp_path),
    ]


def test_by_month_rewrites_only_non_optimal(tmp_path):
    # bar_month 677: 2 partitions / 5 files (non-optimal);
    # bar_month 676: 2 partitions / 2 files (optimal).
    tbl = _fake_table(
        partitions=[
            {"ticker_bucket": 0, "bar_month": 677},
            {"ticker_bucket": 1, "bar_month": 677},
            {"ticker_bucket": 0, "bar_month": 676},
            {"ticker_bucket": 1, "bar_month": 676},
        ],
        file_counts=[3, 2, 1, 1],
    )
    with ExitStack() as es:
        for p in _patches(tbl, tmp_path):
            es.enter_context(p)
        res = im._compact_table_by_month(
            "stocks.intraday_features"
        )
    assert res["months_rewritten"] == 1
    assert res["months_skipped"] == 1
    assert res["errors"] == []
    assert tbl.overwrite.call_count == 1
    _, kw = tbl.overwrite.call_args
    flt = kw["overwrite_filter"]
    # PyIceberg EqualTo: flt.term.name, flt.value.value
    assert flt.term.name == "year_month"
    assert flt.value.value == "2026-06"


def test_by_month_isolates_per_month_errors(tmp_path):
    tbl = _fake_table(
        partitions=[
            {"ticker_bucket": 0, "bar_month": 677},
            {"ticker_bucket": 0, "bar_month": 676},
        ],
        file_counts=[5, 5],  # both non-optimal (1 part / 5 files)
    )
    tbl.overwrite.side_effect = [RuntimeError("boom"), None]
    with ExitStack() as es:
        for p in _patches(tbl, tmp_path):
            es.enter_context(p)
        res = im._compact_table_by_month(
            "stocks.intraday_features"
        )
    assert res["months_rewritten"] == 1
    assert len(res["errors"]) == 1
    # must not abort after the first error
    assert tbl.overwrite.call_count == 2


def test_by_month_no_year_month_falls_back_to_skip(tmp_path):
    tbl = _fake_table(
        partitions=[], file_counts=[], has_ym=False
    )
    with ExitStack() as es:
        for p in _patches(tbl, tmp_path):
            es.enter_context(p)
        res = im._compact_table_by_month("stocks.huge_no_ym")
    assert res.get("skipped_too_large_bytes") is True
    assert tbl.overwrite.call_count == 0
