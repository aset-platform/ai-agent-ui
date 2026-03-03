# How to Run

---

## Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.12+ | `demoenv` was created with Python 3.12.9 |
| Node.js | 18+ | Required by Next.js 16 |
| npm | 9+ | Comes with Node.js |
| GROQ_API_KEY | — | Get at [console.groq.com](https://console.groq.com) |
| SERPAPI_API_KEY | — | Get at [serpapi.com](https://serpapi.com) (100 free searches/month) |

---

## First-Time Setup

Before running for the first time, create `backend/.env` with your API keys and a JWT secret:

```bash
# Generate a secure JWT secret
python -c "import secrets; print(secrets.token_hex(32))"

# Create backend/.env  (never commit this file)
cat > backend/.env <<EOF
GROQ_API_KEY=gsk_...
SERPAPI_API_KEY=abc123...
JWT_SECRET_KEY=<paste-output-above>
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=Admin1234
EOF

# Copy the frontend env template
cp frontend/.env.local.example frontend/.env.local
```

`./run.sh start` will automatically create the Iceberg tables and seed the superuser on first run (detected by the absence of `data/iceberg/catalog.db`).

---

## All Services at Once (Recommended)

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

Logs are written to `/tmp/ai-agent-ui-logs/`:

| File | Service |
|------|---------|
| `backend.log` | FastAPI / uvicorn output |
| `frontend.log` | Next.js dev server output |
| `docs.log` | MkDocs serve output |
| `dashboard.log` | Plotly Dash output |

---

## Backend

### 1. Activate the virtual environment

```bash
cd backend
source demoenv/bin/activate
```

You should see `(demoenv)` in your shell prompt.

### 2. Set environment variables

Create `backend/.env` (never commit this file):

```dotenv
# Required
GROQ_API_KEY=gsk_...
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
| `GROQ_API_KEY` | — | Required for chat |
| `JWT_SECRET_KEY` | — | Required for auth — min 32 chars |
| `ADMIN_EMAIL` | — | First-run seed only |
| `ADMIN_PASSWORD` | — | First-run seed only |
| `SERPAPI_API_KEY` | — | Required for `search_web` tool |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | JWT access token TTL |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | JWT refresh token TTL |
| `LOG_LEVEL` | `DEBUG` | Minimum log severity |
| `LOG_TO_FILE` | `true` | Write logs to `backend/logs/agent.log` |

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
source backend/demoenv/bin/activate
pip install <package-name>
pip freeze > backend/requirements.txt   # update the frozen deps file
```

Always update `requirements.txt` after installing new packages so the file stays accurate.

---

## Rebuilding the Virtual Environment

If `demoenv/` is missing or corrupted:

```bash
cd backend
python3.12 -m venv demoenv
source demoenv/bin/activate
pip install -r requirements.txt
```

---

## Switching to Claude Sonnet 4.6

When the Anthropic API is available, switch the backend in three steps (all in `backend/agents/general_agent.py`):

```python
# 1. Change the import at the top of the file
from langchain_anthropic import ChatAnthropic   # was: from langchain_groq import ChatGroq

# 2. Change _build_llm() in GeneralAgent
def _build_llm(self) -> ChatAnthropic:
    return ChatAnthropic(model=self.config.model, temperature=self.config.temperature)

# 3. Change model name in create_general_agent()
model="claude-sonnet-4-6",   # was: "openai/gpt-oss-120b"
```

Then update your environment:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
# Remove or unset GROQ_API_KEY
```

---

## Dashboard

The dashboard can be started on its own — no API keys required.

```bash
source backend/demoenv/bin/activate
python dashboard/app.py
# → http://127.0.0.1:8050
```

The Home page will be empty until stock data has been fetched at least once via the chat interface or the stock pipeline below.

---

## MkDocs (this documentation)

```bash
cd ai-agent-ui
source backend/demoenv/bin/activate   # mkdocs is installed in demoenv
mkdocs serve                           # → http://127.0.0.1:8000
```

Build the static site:

```bash
mkdocs build --site-dir site/
```
