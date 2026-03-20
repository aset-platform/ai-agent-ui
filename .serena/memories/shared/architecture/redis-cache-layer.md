# Redis Cache Layer for Dashboard & API Performance

## Overview
Write-through Redis cache sitting between FastAPI endpoints and Iceberg tables.
Populated on write (cache invalidation) and on startup (cache warm-up).
Falls back to Iceberg on cache miss or Redis unavailability.

## Key Module: `backend/cache.py`
- `CacheService` — thin wrapper around shared `get_redis_client()` from `auth/token_store.py`
- `get_cache()` — `@lru_cache` singleton, returns `_NoOpCache` when `REDIS_URL` is empty
- Methods: `get(key)`, `set(key, value, ttl)`, `invalidate(pattern)`, `invalidate_exact(*keys)`
- TTL constants: `TTL_VOLATILE=60`, `TTL_STABLE=300`, `TTL_ADMIN=30`

## Cache Key Schema (22 endpoints)
```
cache:dash:watchlist:{user_id}       # 60s
cache:dash:forecasts:{user_id}       # 300s
cache:dash:analysis:{user_id}        # 300s
cache:dash:llm-usage:{user_id|all}   # 60s
cache:dash:registry                  # 300s (shared)
cache:dash:compare:{tickers_hash}    # 300s
cache:dash:home:{user_id}            # 60s (aggregate)
cache:chart:ohlcv:{ticker}           # 300s
cache:chart:indicators:{ticker}      # 300s
cache:chart:forecast:{ticker}:{hz}   # 300s
cache:insights:screener:{user_id}    # 300s
cache:insights:targets:{user_id}     # 300s
cache:insights:dividends:{user_id}   # 300s
cache:insights:risk:{user_id}        # 300s
cache:insights:sectors:{uid}:{mkt}   # 300s
cache:insights:correlation:{uid}:{p}:{m}  # 300s
cache:insights:quarterly:{uid}:{st}  # 300s
cache:admin:audit                    # 60s
cache:admin:users                    # 120s
cache:admin:metrics                  # 30s
cache:admin:tier-health              # 30s
cache:user:{user_id}:tickers         # 300s
cache:user:{user_id}:profile         # 600s
```

## Write-Through Invalidation
- Hook point: `StockRepository._retry_commit()` calls `_invalidate_cache(identifier)`
- `_CACHE_INVALIDATION_MAP` maps each Iceberg table to cache key patterns to invalidate
- Example: writing to `stocks.ohlcv` invalidates `cache:chart:ohlcv:*`, `cache:dash:watchlist:*`, etc.

## Cache Warm-Up (`backend/cache_warmup.py`)
- `warm_shared()` — runs synchronously at FastAPI startup (registry + audit log)
- `warm_tickers()` — runs in background daemon thread (OHLCV + indicators per ticker)
- Integrated via FastAPI lifespan in `backend/routes.py`

## Route Integration Pattern
```python
cache = get_cache()
hit = cache.get(cache_key)
if hit is not None:
    return Response(content=hit, media_type="application/json")
# ... compute from Iceberg ...
cache.set(cache_key, result.model_dump_json(), TTL_STABLE)
return result
```

## Performance
- Redis hit: ~60ms total (5ms RTT + 1ms Redis + 50ms render)
- Redis miss: ~130-230ms (Iceberg scan + serialize + cache SET)
- Previous (no cache): 175-445ms per endpoint
