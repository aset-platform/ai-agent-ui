#!/usr/bin/env python
"""Rebuild corrupt auth Iceberg tables."""

import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(message)s")
_logger = logging.getLogger(__name__)

from pyiceberg.catalog import load_catalog

catalog = load_catalog("local")

damaged = [
    "auth.audit_log",
    "auth.payment_transactions",
    "auth.user_tickers",
]

for fqn in damaged:
    try:
        catalog.drop_table(fqn)
        _logger.info(f"Dropped: {fqn}")
    except Exception as e:
        _logger.info(f"Drop failed {fqn}: {e}")

# Recreate via auth module.
sys.path.insert(0, "auth")
from create_tables import create_tables

create_tables()
_logger.info("Auth tables recreated.")
