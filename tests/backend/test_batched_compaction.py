import backend.maintenance.iceberg_maintenance as im


def test_bar_month_to_year_month():
    f = im._bar_month_to_year_month
    assert f(677) == "2026-06"   # current month (verified vs inspect)
    assert f(576) == "2018-01"
    assert f(675) == "2026-04"
    assert f(0) == "1970-01"
    assert f(11) == "1970-12"
    assert f(12) == "1971-01"
