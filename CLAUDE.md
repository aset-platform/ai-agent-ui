# CLAUDE.md — AI Agent UI

Project context for Claude Code. Read this before making any changes.

---

## Session Rules (ALWAYS FOLLOW — NO EXCEPTIONS)

Before making **any** changes, every session:

```bash
git fetch origin && git checkout dev && git pull origin dev
git checkout -b feature/<short-description>
git branch --show-current   # confirm before touching files
```

- **NEVER** commit directly to `dev`, `qa`, `release`, or `main`
- **ALWAYS** branch off `dev`; after work remind user to raise PR: `feature/*` → `dev`
- If `feature/*` already exists for the task, check it out instead of creating a new one

---

## Pre-Commit / Pre-Push Lint Checklist (ALWAYS — NO EXCEPTIONS)

**Every commit and PR must be lint-clean.** Run these locally before pushing — CI will reject lint failures.

### Frontend (from `frontend/`)

```bash
cd frontend
npx eslint . --fix          # auto-fix what it can
npx eslint .                # verify zero errors remain
```

- ESLint flat config: `frontend/eslint.config.mjs`
- Key rules enforced by CI: `@next/next/no-img-element` (use `<Image />` from `next/image`), `react-hooks/*`, no unused imports
- If a rule must be suppressed, use **block-level** `/* eslint-disable rule-name */` with a comment explaining why — never blanket-disable

### Backend (from project root, inside virtualenv)

```bash
source backend/demoenv/bin/activate
black backend/ auth/ stocks/ scripts/ dashboard/ --check           # formatting
isort backend/ auth/ stocks/ scripts/ dashboard/ --profile black --check  # import order
flake8 backend/ auth/ stocks/ scripts/ dashboard/                  # style + errors
```

To auto-fix:

```bash
black backend/ auth/ stocks/ scripts/ dashboard/
isort backend/ auth/ stocks/ scripts/ dashboard/ --profile black
# Then fix any remaining flake8 issues manually
```

> **Always use `--profile black` with isort** — without it, isort and black fight over import formatting in an infinite loop.

### Workflow

```
Write code → lint & auto-fix → git add → git commit (pre-commit hook runs) → push
```

> **Never push code with linting errors.** Pre-commit hooks catch most issues, but always verify with a manual lint pass before creating a PR.

---

## Project Overview

Fullstack agentic chat app. See `README.md` for full architecture, quick start, and tech stack.

| Service | Port | Entry point |
|---------|------|-------------|
| Backend | 8181 | `backend/main.py` |
| Frontend | 3000 | `frontend/app/page.tsx` |
| Dashboard | 8050 | `dashboard/app.py` |
| Docs | 8000 | `mkdocs serve` |

Run all: `./run.sh start`  ·  Status: `./run.sh status`  ·  Virtualenv: `source backend/demoenv/bin/activate`

---

## Key File Locations

```
setup.sh                     # First-time installer — run once on fresh clone
                             #   Usage: ./setup.sh (interactive) or ./setup.sh --non-interactive

backend/
  main.py                  # ChatServer (owns ToolRegistry + AgentRegistry + FastAPI app)
  config.py                # Pydantic Settings — reads backend/.env
  agents/base.py           # BaseAgent ABC + agentic loop (MAX_ITERATIONS=15) + stream()
  agents/general_agent.py  # GeneralAgent — Groq, tools: [get_current_time, search_web]
  agents/stock_agent.py    # StockAgent — Groq, 9 stock tools
  tools/                   # @tool functions; registered in ChatServer._register_tools()

auth/                      # JWT auth + RBAC; Iceberg/SQLite storage
  create_tables.py         # Idempotent init — run once per deployment
  migrate_users_table.py   # Iceberg schema migration — run once per deployment
  oauth_service.py         # Google/Facebook SSO (PKCE)

stocks/                    # Iceberg persistence layer for all stock data (single source of truth)
  create_tables.py         # Idempotent init of 8 stocks.* tables — called by run.sh on every start
  repository.py            # StockRepository — CRUD for all 8 tables + registry/company_info queries
  backfill_metadata.py     # One-time JSON→Iceberg migration for registry + company_info (idempotent)

scripts/seed_admin.py      # Bootstrap superuser from env vars

frontend/app/page.tsx      # Full SPA — chat, docs, dashboard, admin views
frontend/lib/auth.ts       # JWT token helpers
frontend/lib/apiFetch.ts   # Authenticated fetch wrapper (auto-refresh)

dashboard/app.py           # Dash entry — loads backend/.env via _load_dotenv()

hooks/pre-commit           # 4-check quality gate (Bash + hooks/pre_commit_checks.py)
hooks/pre-push             # Blocks push to main on print() or mkdocs build failure
```

Data paths:
- Raw OHLCV: `data/raw/{TICKER}_raw.parquet` (local backup only; Iceberg is primary)
- Forecasts: `data/forecasts/{TICKER}_{N}m_forecast.parquet` (local backup only; Iceberg is primary)
- Cache (same-day, gitignored): `data/cache/`
- Iceberg catalog: `data/iceberg/catalog.db` (SQLite); warehouse: `data/iceberg/warehouse/`
- Metadata JSON files (legacy, gitignored): `data/metadata/*.json` — replaced by Iceberg tables

Env files (external — safe from git):
- `~/.ai-agent-ui/backend.env` — master backend secrets + config
- `~/.ai-agent-ui/frontend.env.local` — master frontend service URLs
- `backend/.env` → symlink to `~/.ai-agent-ui/backend.env`
- `frontend/.env.local` → symlink to `~/.ai-agent-ui/frontend.env.local`
- `setup.sh` auto-migrates existing real files and creates symlinks

---

## Code Standards

### Python (backend/)
- **Python 3.9** — use `Optional[X]` not `X | Y` (PEP 604 is 3.10+)
- **No bare `print()`** — use `logging.getLogger(__name__)` per module
- **Docstrings** — Google-style Sphinx on every module, class, and public method/`@tool`
- **Error handling** — `HTTPException` with correct status codes; tool failures return error strings (not exceptions) so the LLM receives a `ToolMessage`
- **No module-level mutable globals** — all state in class instances

### OOP rules
- New agents: subclass `BaseAgent`, only override `_build_llm()`
- New tools: `@tool`-decorated functions, registered via `ToolRegistry.register()` in `ChatServer._register_tools()`
- New HTTP bodies: Pydantic models in `main.py`
- No bare `except:` — always `except Exception` or a specific type

### TypeScript (frontend/)
- Use `apiFetch` (not `fetch`) for all backend calls — handles JWT auto-refresh and 401 redirect

---

## Branching & Promotion Strategy

### Golden Rule

> **Changes flow UP, sync flows DOWN. After every merge UP, immediately merge DOWN.**

```
feature/* → dev → qa → release → main      (changes flow UP via PR)
                ↑         ↑          ↑
          merge back  merge back  merge back  (sync flows DOWN immediately)
            to dev      to qa       to release
```

This keeps all branches in sync and prevents the 80+ conflict nightmare on the next promotion.

### Branch flow

| | |
|---|---|
| **Promotion** | `feature/* → dev → qa → release → main` (one direction only) |
| **Hotfix** | Branch off `main`, PR to `main`, then sync DOWN to `dev` |
| **PR title** | `[TYPE] Short description` — feat / fix / chore / refactor / hotfix / docs |
| **Commit format** | `type: description` — feat / fix / refactor / docs / chore |
| **Tag releases** | `git tag -a v1.0.0 -m "Release v1.0.0"` |

### Promoting UP (e.g. dev → qa)

```bash
# 1. Create a promotion branch from the TARGET
git fetch origin
git checkout -b chore/promote-dev-to-qa origin/qa

# 2. Merge the SOURCE into it — resolve all conflicts locally (source wins)
git merge origin/dev
# If conflicts → resolve (accept dev's version), then: git add <files> && git commit

# 3. Lint after merge — merges can introduce formatting drift
black backend/ auth/ stocks/ scripts/ dashboard/
isort backend/ auth/ stocks/ scripts/ dashboard/ --profile black

# 4. Push and PR
git push -u origin chore/promote-dev-to-qa
gh pr create --base qa
```

### Syncing DOWN after merge (CRITICAL — do this immediately)

After `chore/promote-dev-to-qa` is merged to `qa`:

```bash
# Sync qa back DOWN to dev so dev has everything qa has
git fetch origin
git checkout dev && git pull origin dev
git merge origin/qa        # should be fast-forward or trivial
git push origin dev        # if protected, do via PR
```

Repeat for every tier: after qa→release merge, sync release→qa→dev. After release→main, sync main→release→qa→dev.

**Why this works:** The 80+ conflict problem happens because qa accumulates merge commits that dev never sees. Syncing DOWN after every promotion keeps the branches identical except for in-flight features.

### Feature branch workflow

```bash
# Before pushing a feature branch
git fetch origin
git merge origin/dev          # catch conflicts early
# resolve if needed, re-run lint
git push origin HEAD

# Before raising PR
git fetch origin
git merge origin/dev          # final sync
git push origin HEAD
gh pr create --base dev
```

### Rules (NO EXCEPTIONS)

- **No conflicts on GitHub** — all conflicts resolved locally before pushing
- **No direct pushes** to `dev`, `qa`, `release`, or `main` — always PR
- **Sync DOWN immediately** after every upward promotion merge
- **Re-run lint after every merge** — formatting drift is the #1 conflict source
- **Long-lived feature branches** (> 1 day) — merge dev into them daily

### Branch protection (GitHub → Settings → Branches)

- `main`: no direct push, 2 approvals, CI must pass
- `release`: PR from `qa` only, 1 approval + QA lead
- `qa`: PR from `dev` only, 1 approval
- `dev`: PR from `feature/*`, 1 approval, unit tests + lint must pass

---

## Architectural Decisions

- **`ChatServer` in `main.py`** — all state (registries, FastAPI app) in one class; no module-level globals
- **`BaseAgent` pattern** — subclasses implement only `_build_llm()`; loop and streaming live in base
- **`search_market_news` tool** — factory in `tools/agent_tool.py` wraps GeneralAgent as a `@tool`; must be registered _after_ GeneralAgent and _before_ StockAgent
- **Same-day cache** — `data/cache/{TICKER}_{key}_{YYYY-MM-DD}.txt`; repeat tool calls return instantly
- **Streaming** — `POST /chat/stream` returns NDJSON events: `thinking`, `tool_start`, `tool_done`, `warning`, `final`, `error`; daemon thread + `queue.Queue` + timeout
- **JWT env propagation** — `backend/main.py` copies Pydantic settings into `os.environ` at startup so `auth/dependencies.py` (which reads env directly) can find `JWT_SECRET_KEY`
- **Dashboard dotenv** — `dashboard/app.py` calls `_load_dotenv()` at import time; Dash is a separate process that never inherits `backend/.env` otherwise
- **PyIceberg 0.10 quirks** — `table.append()` requires `pa.Table`; `TimestampType` → `pa.timestamp("us")` (naive UTC datetimes only)
- **bcrypt pinned to 4.0.1** — passlib 1.7.4 is incompatible with bcrypt 5.x
- **OAuth PKCE** — `code_verifier` in sessionStorage; `code_challenge = base64url(SHA-256(verifier))`; Facebook button hidden until real credentials provided
- **SerpAPI over Google CSE** — simpler (one key, no Google Cloud project); 100 free/month sufficient
- **pyarrow pinned to <18** — no pre-built wheel for Python 3.9 macOS x86_64 in 18+
- **Iceberg single source of truth** — ALL stock data (OHLCV, metadata, analysis, forecasts) lives exclusively in Iceberg; `_require_repo()` raises `RuntimeError` if unavailable; `_load_parquet()` reads from Iceberg (not flat files); `data/raw/` and `data/forecasts/` are local backup only
- **Iceberg writes must not be silenced** — `price_analysis_tool` and `forecasting_tool` call `_require_repo()` directly (no `try/except`); write failures propagate to the tool's main exception handler, returning an error string to the LLM
- **Single repo singleton** — `_analysis_shared` and `_forecast_shared` import `_get_repo`/`_require_repo` from `_stock_shared` (no duplicate singletons)
- **Iceberg upserts via copy-on-write** — no native UPDATE; pattern: read full table → mutate pandas DataFrame → `table.overwrite()`; used for `stocks.registry` and `stocks.technical_indicators`
- **Dashboard reads Iceberg only** — `_load_raw()` and `_load_forecast()` in `data_loaders.py` use cached Iceberg helpers (`_get_ohlcv_cached`, `_get_forecast_cached`); no flat parquet file reads; `/screener`, `/targets`, `/dividends`, `/risk`, `/sectors`, `/correlation` pages all via `StockRepository`

---

## LLM Switch (when Anthropic API is available)

In `agents/general_agent.py` and `agents/stock_agent.py` (2-line change each):

```python
from langchain_anthropic import ChatAnthropic                                          # replace langchain_groq import
return ChatAnthropic(model="claude-sonnet-4-6", temperature=self.config.temperature)   # in _build_llm()
```
Update `model` field in factory to `"claude-sonnet-4-6"`. Set `ANTHROPIC_API_KEY` in `backend/.env`.

---

## Hooks

Install (one-time):
```bash
cp hooks/pre-commit .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
cp hooks/pre-push .git/hooks/pre-push && chmod +x .git/hooks/pre-push
```

**Pre-commit** (runs on staged files): code quality auto-fix, meta-file freshness, docs freshness, changelog order. Requires `ANTHROPIC_API_KEY`. Bypass: `SKIP_PRE_COMMIT=1`.

**Pre-push** (hard blocks pushes to `main`): no bare `print()` in backend Python; `mkdocs build` must pass.

**After every session**: update `PROGRESS.md` (dated entry) + `CLAUDE.md` (if structure/API changed) + relevant `docs/` page(s).

---

## Known Limitations / TODOs

- **Groq is temporary** — intended model is Claude Sonnet 4.6; switch with the 2-line change above
- **Facebook SSO** — code complete, credentials are placeholders; button hidden on login page
- **`SERPAPI_API_KEY` required** for `search_web` tool (100 free/month at serpapi.com)
- **Refresh token deny-list is in-memory** — cleared on backend restart; tokens remain valid until natural expiry
- **Run once per new deployment**: `python auth/create_tables.py` + `python auth/migrate_users_table.py` + `python stocks/create_tables.py` + `python stocks/backfill_metadata.py`
