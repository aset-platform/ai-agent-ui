from datetime import date
from unittest.mock import patch

from backend.maintenance import backup as bk


def test_backup_table_skips_when_dest_nonempty(tmp_path):
    src = tmp_path / "wh" / "algo" / "events"
    src.mkdir(parents=True)
    (src / "x.parquet").write_text("data")
    root = tmp_path / "backups"
    today = date.today().isoformat()
    dest = root / f"backup-{today}-algo-events"
    dest.mkdir(parents=True)
    (dest / "existing").write_text("prior")
    with patch.object(bk, "subprocess") as sp:
        out = bk.backup_table(
            "algo.events",
            warehouse=str(tmp_path / "wh"),
            backup_root=str(root),
        )
    assert out == str(dest)
    sp.run.assert_not_called()  # no rsync
