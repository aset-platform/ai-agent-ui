# How to Run

---

## Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Docker Desktop | 29+ | [Install](https://docs.docker.com/desktop/setup/install/mac-install/) — runs all services |
| Python | 3.12+ | Virtualenv at `~/.ai-agent-ui/venv` (for native dev) |
| Node.js | 22+ | Required by Next.js 16 (for native dev) |
| GROQ_API_KEY | — | Get at [console.groq.com](https://console.groq.com) (free tier) |
| ANTHROPIC_API_KEY | — | Get at [console.anthropic.com](https://console.anthropic.com) |
| SERPAPI_API_KEY | — | Get at [serpapi.com](https://serpapi.com) (100 free searches/month) |
| Ollama (optional) | — | [ollama.com](https://ollama.com) — local LLM for sentiment/experiments |

---

## First-Time Setup

Before running for the first time, create `backend/.env` with your API keys and a JWT secret:

```bash
# Generate a secure JWT secret
python -c "import secrets; print(secrets.token_hex(32))"

# Create backend/.env  (never commit this file)
cat > backend/.env <<EOF
ANTHROPIC_API_KEY=sk-ant-...
SERPAPI_API_KEY=abc123...
JWT_SECRET_KEY=<paste-output-above>
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=Admin1234
EOF

# Copy the frontend env template
cp frontend/.env.local.example frontend/.env.local
```

`./run.sh start` will automatically create the Iceberg tables and seed the superuser on first run (detected by the absence of `~/.ai-agent-ui/data/iceberg/catalog.db`). If upgrading from a project-local data layout, it auto-migrates data to `~/.ai-agent-ui/`.

---

## AI Tooling Setup

After running the first-time setup, developers using Claude Code + Serena should also run:

```bash
./scripts/dev-setup.sh
```

This script:
1. Verifies Python virtualenv, Node.js, and git are ready
2. Checks Claude Code CLI is installed
3. Verifies Serena MCP server is configured
4. Validates shared Serena memories exist (15 files)
5. Creates local memory directories (`session/`, `personal/`)
6. Installs git hooks if missing
7. Checks GitHub CLI authentication

**Shared memories** at `.serena/memories/shared/` are tracked in git and updated via PRs. Session and personal memories are gitignored.

**Useful slash commands:**
- `/promote-memory` — promote session insight to shared team memory
- `/check-stale-memories` — detect outdated references in shared memories

---

## Docker Compose (Recommended)

The preferred way to run all services. Mirrors the production
environment exactly.

### Prerequisites
- **Docker Desktop** — [Install from Docker](https://docs.docker.com/desktop/setup/install/mac-install/)
- **`.env` file** — copy from template: `cp .env.example .env` and fill in API keys

### Start
```bash
docker compose up -d          # start all (backend, frontend, postgres, redis)
docker compose ps             # verify all healthy
docker compose logs -f backend  # tail backend logs
```

### Services
| Service | Port | Health Check |
|---------|------|-------------|
| Backend (FastAPI) | 8181 | `curl localhost:8181/v1/health` |
| Frontend (Next.js) | 3000 | `http://localhost:3000` |
| PostgreSQL 16 | 5432 | `pg_isready` |
| Redis 7 | 6379 | `redis-cli ping` |

### Dev Hot-Reload
`docker-compose.override.yml` is auto-loaded and mounts source
directories for live reload. Edit Python/TypeScript files and
changes take effect immediately.

### Stop
```bash
docker compose down           # stop all containers
docker compose down -v        # stop + remove volumes (reset data)
```

### Ollama (Local LLM — Optional)
Ollama runs on the host (not in Docker). Install from
[ollama.com](https://ollama.com), then:
```bash
ollama-profile coding         # load Qwen 2.5 Coder 14B
ollama-profile reasoning      # load GPT-OSS 20B
ollama-profile status         # check loaded model
ollama-profile unload         # free RAM
```
If Ollama is not running, the LLM cascade falls back to
Groq (free cloud) → Anthropic (paid).

---

## Legacy: run.sh (Still Works)

`run.sh` in the project root starts, stops, and monitors all four services.

```bash
./run.sh start      # launch all four services in the background
./run.sh status     # show PID + URL for each service
./run.sh stop       # stop everything
./run.sh restart    # stop then start
```

After `start`, the table output looks like:

```
  Service       PID       URL                               Status
  ──────────────────────────────────────────────────────────────────────
  backend       31842     http://127.0.0.1:8181             ● up
  frontend      31901     http://localhost:3000             ● up
  docs          31967     http://127.0.0.1:8000             ● up
  dashboard     32014     http://127.0.0.1:8050             ● up
```

Logs are written to `~/.ai-agent-ui/logs/`:

| File | Service |
|------|---------|
| `backend.log` | FastAPI / uvicorn output |
| `frontend.log` | Next.js dev server output |
| `docs.log` | MkDocs serve output |
| `dashboard.log` | Plotly Dash output |
| `agent.log` | Rotating application log (7-day retention) |

---

## Backend

### 1. Activate the virtual environment

```bash
cd backend
source ~/.ai-agent-ui/venv/bin/activate
```

You should see `(venv)` in your shell prompt.

### 2. Set environment variables

Create `backend/.env` (never commit this file):

```dotenv
# Required
ANTHROPIC_API_KEY=sk-ant-...
JWT_SECRET_KEY=<min-32-random-chars>

# Required on first run only (used by seed_admin.py)
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=Admin1234

# Optional
SERPAPI_API_KEY=abc123...
ACCESS_TOKEN_EXPIRE_MINUTES=60
REFRESH_TOKEN_EXPIRE_DAYS=7
LOG_LEVEL=DEBUG
LOG_TO_FILE=true
```

All variables and their defaults:

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | Required for chat (Claude Sonnet 4.6) |
| `JWT_SECRET_KEY` | — | Required for auth — min 32 chars |
| `ADMIN_EMAIL` | — | First-run seed only |
| `ADMIN_PASSWORD` | — | First-run seed only |
| `SERPAPI_API_KEY` | — | Required for `search_web` tool |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | JWT access token TTL |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | JWT refresh token TTL |
| `LOG_LEVEL` | `DEBUG` | Minimum log severity |
| `LOG_TO_FILE` | `true` | Write logs to `~/.ai-agent-ui/logs/agent.log` |

### 3. Start the server

```bash
uvicorn main:app --port 8181 --reload
```

`--reload` watches for file changes and restarts automatically during development.

On successful startup you will see log lines like:

```
2026-02-22 14:37:55,001 | INFO | main | Tools registered: ['get_current_time', 'search_web']
2026-02-22 14:37:55,002 | INFO | main | Agents registered: ['general']
INFO:     Uvicorn running on http://127.0.0.1:8181 (Press CTRL+C to quit)
```

### 4. Verify

```bash
curl http://127.0.0.1:8181/agents
# → {"agents":[{"id":"general","name":"General Agent","description":"..."}]}
```

---

## Frontend

Open a **separate terminal**.

### 1. Install dependencies (first time only)

```bash
cd frontend
npm install
```

### 2. Start the dev server

```bash
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) in your browser. You should see the chat UI with the empty-state prompt.

---

## Verify End-to-End

1. Open [http://localhost:3000](http://localhost:3000) — you will be redirected to `/login`.
2. Log in with the `ADMIN_EMAIL` / `ADMIN_PASSWORD` you set in `backend/.env`.
3. After login you land on the chat UI.
4. Type *"What time is it?"* — the streaming status badge (`Thinking...`) should appear.
5. The backend executes the agentic loop, calls `get_current_time`, and streams the result.
6. The assistant's response appears as a chat bubble rendered in Markdown.
7. Open the nav menu (bottom-right ⊞) — Chat / Docs / Dashboard / Admin (superuser only).

Check the backend terminal for log output showing the full loop:

```
INFO  | agent.general | Request start | agent=general | input_len=17
DEBUG | agent.general | Iteration 1 | message_count=1
INFO  | agent.general | Tools called: ['get_current_time']
DEBUG | agent.general | Tool result | get_current_time: 2026-02-22 14:37:55...
DEBUG | agent.general | Iteration 2 | message_count=3
INFO  | agent.general | Request end | agent=general | iterations=2
```

---

## Installing New Python Packages

```bash
source ~/.ai-agent-ui/venv/bin/activate
pip install <package-name>
pip freeze > backend/requirements.txt   # update the frozen deps file
```

Always update `requirements.txt` after installing new packages so the file stays accurate.

---

## Rebuilding the Virtual Environment

If `~/.ai-agent-ui/venv` is missing or corrupted:

```bash
python3.12 -m venv ~/.ai-agent-ui/venv
source ~/.ai-agent-ui/venv/bin/activate
pip install -r backend/requirements.txt
```

---

## Dashboard

The dashboard can be started on its own — no API keys required.

```bash
source ~/.ai-agent-ui/venv/bin/activate
python dashboard/app.py
# → http://127.0.0.1:8050
```

The Home page will be empty until stock data has been fetched at least once via the chat interface or the stock pipeline below.

---

## MkDocs (this documentation)

```bash
cd ai-agent-ui
source ~/.ai-agent-ui/venv/bin/activate
mkdocs serve                           # → http://127.0.0.1:8000
```

Build the static site:

```bash
mkdocs build --site-dir site/
```

---

## E2E Tests (Playwright)

The `e2e/` directory contains 49 Playwright tests covering all 3
app surfaces.

### First-time setup

```bash
cd e2e
npm install
npx playwright install chromium
```

### Running tests

```bash
npm test                            # all tests (headless)
npx playwright test --headed        # visible browser
npx playwright test --ui            # interactive UI mode
npx playwright test --project=frontend-chromium   # frontend only
npx playwright test --project=dashboard-chromium  # dashboard only
npx playwright test --project=auth-chromium       # auth only
```

All 3 services must be running (`./run.sh start`) before tests
execute. The config will auto-start services via `webServer` if
they are not already running.

### Test output

- **HTML report**: `npx playwright show-report`
- **Traces** (on retry): `npx playwright show-trace <path>`
- **Screenshots** (on failure): saved to `/tmp/e2e-test-results/`

### CI

`.github/workflows/e2e.yml` runs on every PR — chromium only,
caches browsers, uploads HTML report (14 days) and traces on
failure (7 days).
