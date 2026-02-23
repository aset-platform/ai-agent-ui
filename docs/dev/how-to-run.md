# How to Run

---

## Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.9+ | `demoenv` was created with Python 3.9.13 |
| Node.js | 18+ | Required by Next.js 16 |
| npm | 9+ | Comes with Node.js |
| GROQ_API_KEY | — | Get at [console.groq.com](https://console.groq.com) |
| SERPAPI_API_KEY | — | Get at [serpapi.com](https://serpapi.com) (100 free searches/month) |

---

## All Services at Once (Recommended)

`run.sh` in the project root starts, stops, and monitors all four services.

```bash
export GROQ_API_KEY=gsk_...
export SERPAPI_API_KEY=abc123...

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

```bash
export GROQ_API_KEY=gsk_...
export SERPAPI_API_KEY=abc123...
```

Alternatively, create `backend/.env` (never commit this file):

```dotenv
GROQ_API_KEY=gsk_...
SERPAPI_API_KEY=abc123...
LOG_LEVEL=DEBUG
LOG_TO_FILE=true
```

Optional variables and their defaults:

| Variable | Default | Description |
|----------|---------|-------------|
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

1. Type a message in the chat UI, e.g. *"What time is it?"*
2. The typing indicator (three bouncing dots) should appear.
3. The backend executes the agentic loop, calls the `get_current_time` tool, and returns the result.
4. The assistant's response appears as a chat bubble.

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
python3.9 -m venv demoenv
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
