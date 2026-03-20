# Observability & Tier Health Monitoring

## Overview

`backend/observability.py` provides `ObservabilityCollector` — a
singleton that tracks per-tier health for the N-tier LLM cascade.

## Health Classification

| Status | Condition | Color |
|--------|-----------|-------|
| healthy | 0 failures in 5-min window | Green |
| degraded | 1-3 failures in 5-min window | Yellow |
| down | 4+ failures in 5-min window | Red |
| disabled | Manually toggled off by admin | Grey |

Health is computed from a sliding window of the last 5 minutes of
request outcomes per model.

## Metrics Tracked

- **Latency**: Average and p95 from sliding window of last 100 values
- **Cascade count**: How often each tier triggered a cascade to the next
- **Request outcomes**: Success/failure per tier
- **Tier status**: Current health classification

## Admin Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/v1/admin/tier-health` | Superuser | Full health report (all tiers + summary) |
| POST | `/v1/admin/tier-health/{model}/toggle` | Superuser | Enable/disable a specific tier |

### Response Shape (`GET /v1/admin/tier-health`)
```json
{
  "tiers": {
    "llama-3.3-70b-versatile": {
      "status": "healthy",
      "avg_latency_ms": 450,
      "p95_latency_ms": 890,
      "cascade_count": 2,
      "recent_failures": 0
    }
  },
  "summary": {
    "total_requests": 150,
    "cascade_rate": 0.12
  }
}
```

## Dashboard Integration

- `dashboard/layouts/observability.py` — Health card layout
- `dashboard/callbacks/observability_cbs.py` — Fetches tier health,
  renders color-coded cards (green/yellow/red/grey)
- Accessible from the admin/observability page

## Integration with FallbackLLM

`FallbackLLM` in `backend/llm_fallback.py` reports outcomes to
`ObservabilityCollector` after each tier invocation:
- `record_success(model, latency_ms)` on successful response
- `record_failure(model)` on rate limit, API error, or connection error
- Disabled tiers are skipped in the cascade loop

## Key Files

- `backend/observability.py` — `ObservabilityCollector` class
- `backend/llm_fallback.py` — Reports to collector
- `backend/routes.py` — Admin endpoint handlers
- `dashboard/callbacks/observability_cbs.py` — Dashboard UI
- `tests/backend/test_tier_health.py` — 12 unit tests
- `tests/dashboard/test_tier_health_cards.py` — 6 dashboard tests
