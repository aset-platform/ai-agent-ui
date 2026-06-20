from unittest.mock import patch

from backend.maintenance import backup as bk


def test_list_backups_full_only_sizes_only_snapshots(tmp_path):
    root = tmp_path
    (root / "backup-2026-06-20").mkdir()
    (root / "backup-2026-06-19").mkdir()
    for i in range(5):
        (root / f"backup-2026-06-19-stocks-t{i}").mkdir()
    with patch.object(bk, "_dir_size_mb", return_value=1.0) as sz:
        res = bk.list_backups(str(root), full_only=True)
    assert {b["date"] for b in res} == {"2026-06-20", "2026-06-19"}
    assert sz.call_count == 2  # not 7
