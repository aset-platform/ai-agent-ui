# Iceberg: Column Projection & Caching Optimization

## Overview
The Iceberg repository has optimization opportunities through column projection. Current implementation reads full tables where only subsets are needed.

## High-Priority Projections

| Method | Current Cols | Projected | Reduction |
|--------|-------------|-----------|-----------|
| `get_latest_forecast_run` (freshness) | 30 | ticker, horizon_months, run_date | 90% |
| `get_latest_analysis_summary` (freshness) | 25 | ticker, analysis_date | 92% |
| `get_latest_ohlcv_date` | 9 | ticker, date | 78% |
| `get_currency` | all company_info | ticker, currency | ~95% |

## Implementation
Add optional `selected_fields` parameter to `_scan_ticker` and `_scan_two_filters` internal methods. Public API stays unchanged — projection is opt-in via method parameter.

## Caching Status
- **Dashboard**: Multi-level TTL caches (5 min) — well-cached
- **Backend tools**: File-based only (same-day, same-user)
- **Repository**: No cache — consider adding optional in-memory cache for backend tools

## Risk
Column projection breaks if schema changes. Mitigation: use `selected_fields=None` as default; only optimize known patterns.
