# CLAUDE.md — AI Agent UI

Project context for Claude Code. Read this before making any changes.

---

## What This Project Is

A fullstack agentic chat application:
- **Frontend**: Next.js 16 + React 19 + TypeScript + Tailwind CSS 4
- **Backend**: Python FastAPI + LangChain + Claude Sonnet 4.6 (Anthropic)

The UI is a chat interface. The backend runs an agentic loop — Claude can call tools (currently `get_current_time` and `search_web`) and keeps looping until it has a final answer before responding to the user.

---

## Project Structure

```
ai-agent-ui/
├── frontend/              # Next.js app
│   ├── app/
│   │   ├── page.tsx       # Main chat UI (the only page)
│   │   ├── layout.tsx     # Root layout
│   │   └── globals.css    # Tailwind global styles
│   ├── package.json
│   ├── tsconfig.json
│   └── next.config.ts
│
└── backend/               # FastAPI server
    ├── main.py            # HTTP server, /chat endpoint
    ├── agent.py           # LangChain agent + tool definitions
    └── requirements.txt   # (deps may be in a virtualenv: demoenv)
```

---

## How to Run

### Backend
```bash
cd backend
# Activate virtualenv if present
source demoenv/bin/activate   # or: source venv/bin/activate

export ANTHROPIC_API_KEY=sk-ant-...

uvicorn main:app --port 8181 --reload
```

### Frontend
```bash
cd frontend
npm install
npm run dev
# Runs on http://localhost:3000
```

The frontend hardcodes the backend URL as `http://127.0.0.1:8181`.

---

## Backend Details

### `backend/main.py`
- FastAPI app with CORS open to all origins
- Single POST endpoint: `/chat`
- Request body:
  ```json
  { "message": "user text", "history": [{"role": "user"|"assistant", "content": "..."}] }
  ```
- Response: `{ "response": "assistant text" }`

### `backend/agent.py`
- Uses `langchain_anthropic.ChatAnthropic` with `model="claude-sonnet-4-6"`, `temperature=0`
- Tools bound via `llm.bind_tools(tools)`
- **Agentic loop**: keeps invoking Claude, executes any tool calls, feeds `ToolMessage` results back, repeats until no more tool calls — then returns `response.content`
- History dicts are converted to `HumanMessage` / `AIMessage` objects before the loop
- Two tools:
  - `get_current_time()` — returns `datetime.datetime.now()`
  - `search_web(query: str)` — stub, returns dummy string (replace with real search API)

### Key dependency
```bash
pip install langchain-anthropic
```

---

## Frontend Details

### `frontend/app/page.tsx`
- Single-page chat UI, `"use client"` component
- State: `messages` (array of `{role, content, timestamp}`), `input`, `loading`
- On send: appends user message, POSTs to backend with full `history` array, appends assistant reply
- Multi-turn: every request sends the full prior conversation as `history`

**UI elements:**
- Header with "✦ AI Agent / Claude Sonnet 4.6" badge + clear chat button (trash icon, only shown when messages exist)
- Chat bubbles: indigo for user (right), white card for Claude (left)
- Avatars: gradient "✦" circle for Claude, "You" circle for user
- Timestamps below each bubble
- Three-dot bouncing typing indicator while loading
- Auto-growing textarea (max 160px), resets after send
- Enter to send, Shift+Enter for newline
- Empty state with centered prompt when no messages

---

## Migration History

Originally used `langchain_groq` with `openai/gpt-oss-120b`. Migrated to `langchain_anthropic` / `claude-sonnet-4-6`. The original agent loop was also broken — it called one tool and returned early without feeding the result back to Claude. The loop was fixed as part of this migration.

---

## Known Limitations / TODOs

- `search_web` tool returns dummy data — needs a real search API (e.g. Tavily: `pip install tavily-python`, or SerpAPI)
- No streaming — the backend waits for the full agent loop to finish before responding; could be improved with SSE/WebSockets
- No auth, no session persistence — history is kept only in React state (lost on refresh)
- Backend URL is hardcoded in `page.tsx` as `http://127.0.0.1:8181` — move to `.env.local` if deploying
- `requirements.txt` is empty; actual deps live in a virtualenv (`demoenv`). Freeze deps with `pip freeze > requirements.txt`
