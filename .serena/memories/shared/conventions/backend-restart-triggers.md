---
name: backend-restart-triggers
description: When `docker compose restart backend` is required vs `--force-recreate` vs `.pyc` clear vs nothing
type: convention
---

# Backend restart triggers — single checklist

`uvicorn --reload` (the dev default in `docker-compose.override.yml`)
detects file changes and reloads modules — but several classes of
change are NOT picked up by reload alone. This checklist covers
every case we've hit.

## Decision matrix

| Change | Action | Why |
|---|---|---|
| Edit existing function body | nothing — reload handles it | StatReload re-imports the file |
| Edit existing route handler body | nothing | same |
| Change Pydantic field **type** on existing field | nothing | OpenAPI re-generates on next request |
| Add NEW `@router.get/post/put/delete` decorator | `restart` | `app.include_router()` ran ONCE at startup |
| Add NEW `app.include_router(...)` call | `restart` | startup-time wiring |
| Add NEW field to a class used as `response_model=...` | `restart` | response_model schema bound at decoration time |
| Add NEW `@register_job(...)` decorator (scheduler executors) | `restart` | registry built at startup |
| Add a NEW Pydantic model referenced from response_model | `restart` | OpenAPI components don't auto-add |
| Iceberg `update_schema().add_column()` ran | `restart` + Redis FLUSHALL | in-process DuckDB connection caches old schema |
| New env var added to `.env` | `up -d --force-recreate backend` | restart does NOT re-read env_file |
| Renamed Alembic migration file | edit `revision: str = "..."` AND clear `__pycache__/*.pyc` AND `restart` | bytecode cache survives rename |
| New module added (any new `*.py`) | usually nothing | reload picks up imports |
| Container's `Dockerfile.backend` changed (new apt pkg) | `compose build backend` + `up -d` | image needs rebuild |
| `requirements.txt` changed | `compose build backend` + `up -d` | image needs rebuild |
| Frontend env (`NEXT_PUBLIC_*`) changed | `restart frontend` (or `--force-recreate`) | baked into client bundle at build/start |

## Verification commands

After any restart, confirm the change is live:

```bash
# New route landed in OpenAPI:
curl -s http://localhost:8181/openapi.json \
  | python3 -c "import sys, json; d=json.load(sys.stdin);
                print('/v1/users/me/portfolio/{ticker}/transactions'
                      in d['paths'])"

# New field present on response model:
curl -s http://localhost:8181/openapi.json \
  | python3 -c "import sys, json; d=json.load(sys.stdin);
                print(list(d['components']['schemas']
                            ['PortfolioPerformanceResponse']
                            ['properties'].keys()))"

# Iceberg schema visible to live process:
curl -s http://localhost:8181/v1/insights/screener \
  -H 'Authorization: Bearer ...' \
  | jq '.rows[0] | keys'
```

## Restart timing gotcha — asyncpg shutdown race

`docker compose restart backend` first request returns 500 with
empty body for ~5s while the asyncpg pool finishes terminating
("Event loop is closed" in logs).

Sleep 5s before any auth-dependent test/curl, or use a poll loop:

```bash
docker compose restart backend
for i in 1 2 3 4 5 6 7; do
  curl -fsS http://localhost:8181/v1/health >/dev/null && break
  sleep 3
done
```

## Iceberg schema-evolution full sequence

After `tbl.update_schema().add_column(...)`:

```bash
# 1. Verify on disk
docker compose exec backend python3 -c \
  "from stocks.repository import _get_catalog;
   print([f.name for f in
          _get_catalog().load_table('stocks.company_info')
                        .schema().fields])"

# 2. Flush response cache
docker compose exec redis redis-cli FLUSHALL

# 3. Restart — this is the critical step
docker compose restart backend

# 4. Wait for health
sleep 5
curl -s http://localhost:8181/v1/health

# 5. Verify column visible in API response
```

`invalidate_metadata()` alone is NOT enough — that handles snapshot
visibility for write-then-read in the same connection. Schema
changes need a full process recycle.

Apply this sequence in EVERY env (dev/qa/release/main) after running
an `evolve_*` function.

## Related

- `shared/debugging/uvicorn-reload-routes-models-gotcha` — the
  router/model case in detail
- `shared/debugging/iceberg-schema-evolution-backend-restart` — the
  Iceberg case in detail
- CLAUDE.md Hard Rules → Infra & Config → "Container TZ", "scheduler
  catchup", "BACKEND_URL" — startup-time settings that also need
  recycle when changed
