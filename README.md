# AI Agent UI

A fullstack agentic chat application powered by LangChain, FastAPI, and Next.js. The backend runs an LLM in a tool-calling loop; the frontend is a single-page app that embeds the Docs and Dashboard in-context alongside the chat interface.

---

## Services at a Glance

| Service | Stack | Port | Purpose |
|---------|-------|------|---------|
| **Frontend** | Next.js 16 + React 19 + Tailwind 4 | `3000` | Chat UI + SPA shell |
| **Backend** | FastAPI + LangChain + Groq | `8181` | Agentic loop + REST API |
| **Dashboard** | Plotly Dash + Dash Bootstrap | `8050` | Stock analysis dashboard |
| **Docs** | MkDocs Material | `8000` | Project documentation |

---

## Quick Start

```bash
# 1. Set API keys
export GROQ_API_KEY=...
export SERPAPI_API_KEY=...        # optional — needed for web search

# 2. Create the frontend env file
cp frontend/.env.local.example frontend/.env.local

# 3. Start everything
./run.sh start

# 4. Open the chat
open http://localhost:3000
```

Stop all services: `./run.sh stop` · Status: `./run.sh status`

---

## System Architecture

```mermaid
graph TD
    subgraph Browser["Browser — localhost:3000"]
        UI["Next.js Chat UI<br/><i>page.tsx</i>"]
        IF_DASH["iframe — Dashboard<br/><i>:8050</i>"]
        IF_DOCS["iframe — Docs<br/><i>:8000</i>"]
    end

    subgraph Backend["Backend — :8181"]
        API["FastAPI<br/>POST /chat<br/>GET /agents"]
        CS["ChatServer"]
        AR["AgentRegistry"]
        TR["ToolRegistry"]
    end

    subgraph Agents["Agents"]
        GA["GeneralAgent<br/><i>Groq LLM</i>"]
        SA["StockAgent<br/><i>Groq LLM</i>"]
    end

    subgraph Tools["Tools"]
        T1["get_current_time"]
        T2["search_web<br/><i>SerpAPI</i>"]
        T3["search_market_news<br/><i>wraps GeneralAgent</i>"]
        T4["fetch_stock_data<br/>load_stock_data<br/>get_stock_info<br/>…"]
        T5["analyse_stock_price<br/><i>ta + Plotly</i>"]
        T6["forecast_stock<br/><i>Prophet + Plotly</i>"]
    end

    subgraph Data["Data — local files"]
        P["Parquet<br/>data/raw/"]
        F["Forecasts<br/>data/forecasts/"]
        C["Cache<br/>data/cache/"]
    end

    subgraph Dash["Dashboard — :8050"]
        DP["Plotly Dash<br/>4 pages"]
    end

    subgraph Docs["Docs — :8000"]
        MK["MkDocs Material"]
    end

    UI -->|"POST /chat<br/>{message, history, agent_id}"| API
    API --> CS --> AR
    AR --> GA & SA
    GA --> T1 & T2
    SA --> T3 & T4 & T5 & T6
    T4 --> P
    T5 --> C
    T6 --> F & C
    DP -->|"reads directly"| P & F
    UI -->|"view=dashboard"| IF_DASH --> Dash
    UI -->|"view=docs"| IF_DOCS --> Docs
```

---

## Agentic Loop

Every message goes through an LLM-driven tool-calling loop before a response is returned.

```mermaid
sequenceDiagram
    participant U as User
    participant FE as Frontend
    participant BE as FastAPI
    participant LLM as Groq LLM
    participant T as Tool(s)

    U->>FE: sends message
    FE->>BE: POST /chat {message, history, agent_id}
    BE->>LLM: invoke(messages + tools)

    loop Agentic Loop (max 15 iterations)
        LLM-->>BE: AIMessage {tool_calls: [...]}
        BE->>T: ToolRegistry.invoke(name, args)
        T-->>BE: ToolMessage result
        BE->>LLM: invoke(messages + tool results)
    end

    LLM-->>BE: AIMessage {content: "final answer"}
    BE-->>FE: {response, agent_id}
    FE-->>U: renders markdown response
```

The loop exits when the LLM returns a message with no tool calls, or after 15 iterations (a `WARNING` is logged).

---

## Chat Message Flow

```mermaid
graph LR
    A["User types message<br/>presses Enter"] --> B["sendMessage()"]
    B --> C["Optimistic UI update<br/>append user bubble"]
    C --> D["POST /chat<br/>message + full history"]
    D --> E{{"agent_id?"}}
    E -->|"general"| F["GeneralAgent<br/>tools: time, search_web"]
    E -->|"stock"| G["StockAgent<br/>tools: 9 financial tools"]
    F --> H["Agentic loop"]
    G --> H
    H --> I["Final text response"]
    I --> J["Markdown rendered<br/>in assistant bubble"]
    J --> K["preprocessContent()<br/>chart paths → dashboard links<br/>data paths stripped"]
```

---

## Stock Analysis Pipeline

The Stock Agent follows a strict five-step pipeline enforced by its system prompt.

```mermaid
graph TD
    Q["User query<br/><i>e.g. 'Analyse AAPL'</i>"]

    Q --> S1

    subgraph S1["Step 1 — Fetch Data"]
        direction LR
        FSD["fetch_stock_data<br/><i>Yahoo Finance → parquet</i>"]
        GSI["get_stock_info<br/><i>company metadata</i>"]
    end

    S1 --> S2

    subgraph S2["Step 2 — Technical Analysis"]
        ASP["analyse_stock_price<br/><i>SMA/EMA/RSI/MACD/BB/ATR<br/>Sharpe, drawdown, support/resistance<br/>3-panel Plotly chart</i>"]
    end

    S2 --> S3

    subgraph S3["Step 3 — Forecast"]
        FS["forecast_stock<br/><i>Prophet + US holidays<br/>3/6/9-month price targets<br/>80% confidence band</i>"]
    end

    S3 --> S4

    subgraph S4["Step 4 — Market News"]
        SMN["search_market_news<br/><i>delegates to GeneralAgent<br/>→ SerpAPI web search</i>"]
    end

    S4 --> S5["Step 5 — Structured Report<br/><i>sentiment · price targets · recommendations</i>"]

    S2 -->|"same-day cache hit"| CACHE[("data/cache/<br/>{TICKER}_{date}.txt")]
    S3 -->|"same-day cache hit"| CACHE
    CACHE -.->|"instant return"| S2
    CACHE -.->|"instant return"| S3
```

---

## Frontend SPA Navigation

The entire UI is one mounted React component. The `view` state switches surfaces without unmounting — chat history is always preserved.

```mermaid
stateDiagram-v2
    [*] --> chat : initial load

    chat --> docs : switchView("docs")\niframeUrl = null
    chat --> dashboard : switchView("dashboard")\niframeUrl = null

    docs --> chat : switchView("chat")
    docs --> dashboard : switchView("dashboard")

    dashboard --> chat : switchView("chat")
    dashboard --> docs : switchView("docs")

    chat --> dashboard : handleInternalLink(href)\niframeUrl = href\n(e.g. /analysis?ticker=AAPL)
    chat --> docs : handleInternalLink(href)\niframeUrl = href
```

When the LLM produces a response containing a chart path, `preprocessContent()` converts it to a markdown link. Clicking the link calls `handleInternalLink`, which sets `view = "dashboard"` and loads the exact page (e.g. `/analysis?ticker=AAPL`) inside the embedded iframe.

```
┌──────────────────────────────────────────────────────────┐
│  ✦ AI Agent  [General | Stock Analysis]             [🗑]  │ ← header
│             (breadcrumb when view ≠ chat)                │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  view = "chat"          │  view = "docs" / "dashboard"  │
│  ───────────────────    │  ──────────────────────────── │
│  scrollable messages    │  <iframe src={iframeUrl ??    │
│  + typing indicator     │    baseServiceUrl}            │
│  + input textarea       │    className="flex-1 w-full"> │
│                                                          │
└──────────────────────────────────────────────────────────┘
                                              [⊞] ← FAB menu
                                                   bottom-right
```

---

## Backend Architecture

```mermaid
graph TD
    UV["uvicorn main:app"] --> CS

    subgraph CS["ChatServer (main.py)"]
        APP["FastAPI app<br/>CORS + routes"]
        TR["ToolRegistry"]
        AR["AgentRegistry"]
    end

    TR -->|"register"| T1["get_current_time"]
    TR -->|"register"| T2["search_web"]
    TR -->|"register"| T3["search_market_news"]
    TR -->|"register"| T4["fetch_stock_data"]
    TR -->|"register"| T5["analyse_stock_price"]
    TR -->|"register"| T6["forecast_stock"]
    TR -->|"register"| T7["… 4 more stock tools"]

    AR -->|"register"| GA["GeneralAgent<br/>tools: time, search_web"]
    AR -->|"register"| SA["StockAgent<br/>tools: 9 tools"]

    APP -->|"POST /chat → agent_id"| AR
    GA & SA -->|"tool calls"| TR
```

`BaseAgent.run()` owns the loop:

```
_build_messages(history + user_input)
        │
        ▼
  llm_with_tools.invoke(messages)
        │
   ┌────┴──────────────┐
   │ tool_calls?        │
   │                   │
  Yes                  No
   │                   │
   ▼                   ▼
ToolRegistry        return response.content
.invoke(each)       (or "No response")
   │
append ToolMessages
   │
   └──── loop (max 15 iterations)
```

---

## Project Structure

```
ai-agent-ui/
├── run.sh                    # Unified launcher (start/stop/status/restart)
├── README.md
├── CLAUDE.md                 # Claude Code project context
├── PROGRESS.md               # Session log
│
├── frontend/                 # Next.js 16
│   ├── app/
│   │   ├── page.tsx          # Entire SPA (chat + docs + dashboard views)
│   │   ├── layout.tsx
│   │   └── globals.css
│   ├── .env.local            # Gitignored — copy from .env.local.example
│   └── .env.local.example    # Committed reference
│
├── backend/                  # FastAPI
│   ├── main.py               # ChatServer, routes
│   ├── config.py             # Pydantic Settings (.env support)
│   ├── logging_config.py     # Rotating file + console logging
│   ├── agents/
│   │   ├── base.py           # BaseAgent ABC + agentic loop
│   │   ├── registry.py       # AgentRegistry
│   │   ├── general_agent.py  # GeneralAgent (Groq)
│   │   └── stock_agent.py    # StockAgent (Groq)
│   └── tools/
│       ├── registry.py       # ToolRegistry
│       ├── time_tool.py      # get_current_time
│       ├── search_tool.py    # search_web (SerpAPI)
│       ├── agent_tool.py     # search_market_news (wraps GeneralAgent)
│       ├── stock_data_tool.py      # 6 Yahoo Finance tools
│       ├── price_analysis_tool.py  # analyse_stock_price
│       └── forecasting_tool.py     # forecast_stock (Prophet)
│
├── dashboard/                # Plotly Dash
│   ├── app.py                # Entry point, routing, dcc.Store
│   ├── layouts.py            # Page layout factories
│   ├── callbacks.py          # All interactive callbacks
│   └── assets/custom.css     # Dark theme overrides
│
├── data/
│   ├── raw/                  # OHLCV parquet (gitignored)
│   ├── forecasts/            # Prophet output parquet (gitignored)
│   ├── cache/                # Same-day text cache (gitignored)
│   └── metadata/             # Stock registry + company info (tracked)
│
├── charts/                   # Generated Plotly HTML (gitignored)
│   ├── analysis/
│   └── forecasts/
│
├── docs/                     # MkDocs source
└── mkdocs.yml
```

---

## Tech Stack

### Frontend
| Package | Version | Role |
|---------|---------|------|
| Next.js | 16 | Framework |
| React | 19 | UI |
| Tailwind CSS | 4 | Styling |
| axios | latest | HTTP client |
| react-markdown + remark-gfm | 10 / 4 | Markdown rendering |
| TypeScript | 5 | Type safety |

### Backend
| Package | Role |
|---------|------|
| FastAPI + uvicorn | HTTP server |
| LangChain | Agentic loop + tool binding |
| langchain-groq | Groq LLM provider |
| Pydantic v2 | Request/response models + settings |
| yfinance | Yahoo Finance OHLCV data |
| Prophet | Time-series forecasting |
| ta | Technical analysis indicators |
| Plotly | Interactive HTML charts |
| pyarrow | Parquet read/write |
| pandas / numpy | Data manipulation |

### Dashboard
| Package | Role |
|---------|------|
| Dash 4 | Web framework |
| dash-bootstrap-components | DARKLY theme |
| Plotly | Charts |

---

## Environment Variables

| Variable | Where | Required | Default |
|----------|-------|----------|---------|
| `GROQ_API_KEY` | shell / `backend/.env` | Yes | — |
| `SERPAPI_API_KEY` | shell / `backend/.env` | No | `search_web` returns error string |
| `NEXT_PUBLIC_BACKEND_URL` | `frontend/.env.local` | No | `http://127.0.0.1:8181` |
| `NEXT_PUBLIC_DASHBOARD_URL` | `frontend/.env.local` | No | `http://127.0.0.1:8050` |
| `NEXT_PUBLIC_DOCS_URL` | `frontend/.env.local` | No | `http://127.0.0.1:8000` |
| `LOG_LEVEL` | shell / `backend/.env` | No | `DEBUG` |
| `LOG_TO_FILE` | shell / `backend/.env` | No | `true` |

---

## Extending the App

### Add a new tool

1. Create `backend/tools/my_tool.py` with a `@tool`-decorated function.
2. Register it in `ChatServer._register_tools()` in `main.py`.
3. Add the tool name to the relevant agent's `tool_names` list.

### Add a new agent

1. Subclass `BaseAgent` in `backend/agents/my_agent.py` — only implement `_build_llm()`.
2. Register it in `ChatServer._register_agents()`.
3. Add the agent ID to the `AGENTS` array in `frontend/app/page.tsx`.

### Switch to Claude Sonnet 4.6

Two-line change in `agents/general_agent.py` and `agents/stock_agent.py`:

```python
# Line 1 — change import
from langchain_anthropic import ChatAnthropic

# Line 2 — change return in _build_llm()
return ChatAnthropic(model="claude-sonnet-4-6", temperature=self.config.temperature)
```

Also set `ANTHROPIC_API_KEY` instead of `GROQ_API_KEY`.

---

## Known Limitations

| Issue | Notes |
|-------|-------|
| **Groq LLM** | Claude Sonnet 4.6 is the intended model; Groq is a temporary workaround |
| **No streaming** | Full response appears after the complete agentic loop; SSE/WebSockets would improve perceived speed |
| **No request timeout** | A hung backend will block the UI until the browser times out |
| **iframe cross-origin** | Dashboard and Docs are embedded via `<iframe>`; JavaScript bridge calls across frames are not supported (not needed) |
