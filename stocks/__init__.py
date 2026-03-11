"""Iceberg-backed storage layer for stock market data.

This package provides table schemas, a repository class, and a backfill
script for migrating all stock data from flat JSON/parquet files into an
Apache Iceberg warehouse shared with the ``auth`` namespace.

Namespace: ``stocks`` (alongside ``auth`` in the same SQLite catalog).

Tables
------
- ``stocks.registry`` — one row per ticker
- ``stocks.company_info`` — append-only snapshots
- ``stocks.ohlcv`` — OHLCV price history
- ``stocks.dividends`` — dividend payments
- ``stocks.technical_indicators`` — computed indicators
- ``stocks.analysis_summary`` — daily analysis snapshots
- ``stocks.forecast_runs`` — Prophet run metadata
- ``stocks.forecasts`` — full Prophet output series

Usage::

    from stocks.repository import StockRepository

    repo = StockRepository()
    repo.upsert_registry("AAPL", last_fetch_date=date.today(), ...)
    df = repo.get_ohlcv("AAPL")
"""
