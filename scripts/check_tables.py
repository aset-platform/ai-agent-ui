"""Print all Iceberg tables with row counts."""

import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(message)s")
_logger = logging.getLogger(__name__)

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "backend"),
)
os.chdir(os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/.ai-agent-ui/backend.env"))

from pyiceberg.catalog import load_catalog

cat = load_catalog("local")

_logger.info(f"{'Table':<45s} {'Rows':>10s}")
_logger.info("-" * 57)

for ns in ("stocks", "auth"):
    for ns_name, tbl_name in sorted(cat.list_tables(ns)):
        fqn = f"{ns_name}.{tbl_name}"
        try:
            tbl = cat.load_table(fqn)
            snaps = tbl.metadata.snapshots
            if not snaps:
                _logger.info(f"{fqn:<45s} {'0 (empty)':>10s}")
                continue
            count = len(tbl.scan().to_arrow())
            _logger.info(f"{fqn:<45s} {count:>10,}")
        except FileNotFoundError:
            # Count snapshots to show table isn't truly empty
            try:
                n_snaps = len(tbl.metadata.snapshots)
                _logger.info(
                    f"{fqn:<45s}  CORRUPT "
                    f"({n_snaps} snapshots, data files missing)"
                )
            except Exception:
                _logger.info(f"{fqn:<45s}  CORRUPT")
        except Exception as e:
            err = str(e).split("\n")[0][:50]
            _logger.info(f"{fqn:<45s}    ERROR: {err}")
