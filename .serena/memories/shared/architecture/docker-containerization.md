# Docker Containerization

## Files
- `Dockerfile.backend`: 2-stage (builder with gcc/g++ → slim runtime), Python 3.12
- `Dockerfile.frontend`: 3-stage (deps → build → runner), Node 22 Alpine, standalone output
- `docker-compose.yml`: production-like (backend, frontend, postgres:16, redis:7)
- `docker-compose.override.yml`: dev hot-reload (source mounts, uvicorn --reload, next dev)
- `.env.example`: committed template, `.env`: secrets (gitignored)
- `.dockerignore`: root exclusions (git, tests, docs, screenshots, .claude)

## Services
| Service | Image | Port | Health Check |
|---------|-------|------|-------------|
| backend | Dockerfile.backend | 8181 | curl /v1/health |
| frontend | Dockerfile.frontend | 3000 | wget / |
| postgres | postgres:16-alpine | 5432 | pg_isready |
| redis | redis:7-alpine | 6379 | redis-cli ping |

## Key Design Decisions
- **Iceberg mount**: host `~/.ai-agent-ui` mounted at SAME host path inside container because SQLite catalog stores absolute file paths in metadata
- **Ollama**: host-native only (not containerized), accessed via `host.docker.internal:11434`
- **PostgreSQL tuning**: 512MB shared_buffers, 1GB effective_cache_size, 20 max_connections (dev)
- **PYTHONPATH**: `/app/backend:/app/auth:/app/stocks:/app/dashboard`
- **Next.js**: `output: "standalone"` in next.config.ts for minimal production image
- **Hot-reload**: override mounts source dirs + uses uvicorn --reload and next dev
- **Startup order**: depends_on with health check conditions (pg + redis → backend → frontend)

## Environment Variables
- `DATABASE_URL`: PostgreSQL connection (asyncpg driver)
- `REDIS_URL`: redis://redis:6379/0 (Docker network)
- `AI_AGENT_UI_HOME`: Iceberg data dir (host path)
- `OLLAMA_BASE_URL`: host.docker.internal:11434 in Docker, localhost:11434 on host
- `NEXT_PUBLIC_BACKEND_URL`: build ARG baked into frontend bundle

## Commands
```bash
docker compose up -d          # start all (uses override for dev)
docker compose ps             # check health
docker compose logs -f backend
docker compose down           # stop all
docker compose -f docker-compose.yml up -d  # prod-like (no override)
docker compose build backend  # rebuild single service
```
