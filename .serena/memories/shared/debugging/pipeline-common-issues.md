# Pipeline Common Issues

## Redis cache poisoning
**Symptom**: Market shows "US" for Indian stocks, prices missing after restart.
**Cause**: cache_warmup.py caches bare registry (no OHLCV enrichment) for 300s.
**Fix**: Disabled registry in cache_warmup. Always flush Redis after code changes:
`docker compose exec redis redis-cli FLUSHALL`

## Iceberg not accessible in Docker
**Symptom**: company_info batch returns empty, "URI missing" errors.
**Cause**: `.pyiceberg.yaml` not mounted in container.
**Fix**: Added `.pyiceberg.yaml:/app/.pyiceberg.yaml:ro` to docker-compose.override.yml.

## Duplicate registry entries (RELIANCE + RELIANCE.NS)
**Symptom**: Tickers processed twice, registry grows unbounded.
**Cause**: Pipeline stores canonical, scheduler stores .NS format.
**Fix**: Standardized on .NS everywhere. _update_registry preserves existing market.

## Scheduler fails with "No data returned from yfinance"
**Symptom**: All 519 India tickers fail during scheduler refresh.
**Cause**: Executor passes canonical symbol (RELIANCE) but yfinance needs .NS.
**Fix**: yf_map in executor.py appends .NS for tickers with registry market=NSE/india.

## jugaad-data hangs indefinitely
**Symptom**: Bulk job stuck, no progress for 5+ minutes.
**Cause**: jugaad-data's stock_df has no timeout.
**Fix**: Wrapped in asyncio.wait_for(timeout=60.0) in NseSource.

## Query object has no attribute 'upper'
**Symptom**: Dashboard 500 error, "Could not load" on all widgets.
**Cause**: get_forecasts_summary called internally without ticker= kwarg.
**Fix**: Pass ticker=None explicitly in internal calls.
