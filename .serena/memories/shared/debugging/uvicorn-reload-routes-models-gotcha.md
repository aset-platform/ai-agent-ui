# uvicorn `--reload` doesn't re-register routes / Pydantic fields

`uvicorn main:app --reload` (the dev-mode default in `docker-compose.override.yml`) uses `StatReload` which re-imports changed Python modules. **It does NOT replay app-startup-time work**:

- `app.include_router(some_router)` runs once at startup. New `@router.get(...)` decorators added to a router after startup don't get included.
- `response_model=MyModel` is bound at decoration time. Adding a new field to `MyModel` re-imports the file but the OpenAPI schema and route's serialization layer keep the OLD shape.

Result: the file appears reloaded (you see "WARNING: StatReload detected changes in '...'. Reloading...") but the live worker still serves the old behaviour. New routes return 404; new fields are silently dropped from responses.

## Symptoms observed in this codebase

- Adding a new endpoint `GET /v1/users/me/portfolio/{ticker}/transactions` — frontend got 404 even though the file was edited and reload fired. `curl /openapi.json | jq` confirmed the route was missing.
- Adding `stale_tickers: list[StalePriceTicker]` field to `PortfolioPerformanceResponse` — frontend received responses without the field; `curl /openapi.json` showed the schema unchanged.
- Adding `unanalyzed_tickers: list[str]` to `PortfolioNewsResponse` — same story.

## Fix

`docker compose restart backend` (or `--force-recreate` if .env also changed). Verifies via:

```bash
curl -s http://localhost:8181/openapi.json | python3 -c "
import sys, json
d = json.load(sys.stdin)
schema = d['components']['schemas']['MyModel']
print(list(schema['properties'].keys()))
print('MyNewModel:', 'MyNewModel' in d['components']['schemas'])
"
```

If the new field/schema doesn't appear → restart.

## Related but distinct cases

- **`env_file` reload** (CLAUDE.md gotcha) — `docker compose restart backend` does NOT re-read `.env`. New env vars need `docker compose up -d --force-recreate backend`.
- **Alembic `.pyc` cache** — renaming a migration file isn't enough; clear the bytecode cache + edit the `revision: str = "..."` inside.
- **Hot-reload IS sufficient** for changes to existing function bodies, existing route handlers, existing model field types — only NEW route registrations and NEW schema-bound fields need restart.

## When this matters

Most often during PR-style iterations where you add a new field then test the API. Default to `restart backend` whenever you've added:
- A new `@router.get/post/put/delete` decorator
- A new field on any class extending `BaseModel` that's referenced as `response_model=`
- A new `app.include_router()` call
- A new `@register_job(...)` decorator (for the sentiment/scheduler executors)

Verified by added gotcha to `CLAUDE.md` (Docker & Infra section) on 2026-04-23 alongside `env_file` reload + Alembic `.pyc` notes.
