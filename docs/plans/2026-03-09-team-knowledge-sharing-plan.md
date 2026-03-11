# Team Knowledge Sharing Ecosystem — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Enable 4-5 developers using Claude Code + Serena to share architectural knowledge, conventions, and debugging insights via git-committed Serena memories, while keeping session/personal context local.

**Architecture:** Slim CLAUDE.md (~80-100 lines) always loaded for hard rules; detailed knowledge in `.serena/memories/shared/` (git-committed, PR-reviewed); session/personal memories gitignored. Two Claude Code skills (`/promote-memory`, `/check-stale-memories`) automate promotion and staleness detection. A CI script provides automated stale checks. A `dev-setup.sh` script onboards new devs in ~5 minutes.

**Tech Stack:** Bash (scripts), Markdown (memories + Claude Code commands), Serena MCP (memory management), Git (version control for shared memories)

**Design doc:** `docs/plans/2026-03-09-team-knowledge-sharing-design.md`

---

## Task 1: Update `.gitignore` Files

**Files:**
- Modify: `.gitignore:70-71`
- Modify: `.serena/.gitignore`

**Step 1: Update project `.gitignore` — replace blanket `.serena/` with selective ignores**

In `.gitignore`, replace line 71:

```gitignore
# Serena MCP project state (local per-machine)
.serena/
```

with:

```gitignore
# Serena — selective ignore (shared memories are tracked)
.serena/cache/
.serena/project.local.yml
.serena/memories/session/
.serena/memories/personal/
```

**Step 2: Update `.serena/.gitignore`**

Replace contents of `.serena/.gitignore` with:

```gitignore
/cache
/project.local.yml
/memories/session/
/memories/personal/
```

**Step 3: Create local memory directories with `.gitkeep`**

```bash
mkdir -p .serena/memories/shared/architecture
mkdir -p .serena/memories/shared/conventions
mkdir -p .serena/memories/shared/debugging
mkdir -p .serena/memories/shared/onboarding
mkdir -p .serena/memories/shared/api
mkdir -p .serena/memories/session
mkdir -p .serena/memories/personal
touch .serena/memories/session/.gitkeep
touch .serena/memories/personal/.gitkeep
```

**Step 4: Verify git tracks the right files**

Run: `git status`
Expected: `.serena/project.yml` and `.serena/memories/shared/` directories show as untracked (ready to add). `.serena/cache/`, `.serena/project.local.yml`, `memories/session/`, `memories/personal/` do NOT appear.

**Step 5: Commit**

```bash
git add .gitignore .serena/.gitignore .serena/project.yml
git add .serena/memories/session/.gitkeep .serena/memories/personal/.gitkeep
git commit -m "chore: selective .serena gitignore for team memory sharing"
```

---

## Task 2: Create Shared Architecture Memories

Migrate CLAUDE.md sections 2 (Architecture Summary) into focused Serena memory files. Each file is written via Serena `write_memory` to ensure correct format.

**Files:**
- Create: `.serena/memories/shared/architecture/system-overview.md`
- Create: `.serena/memories/shared/architecture/iceberg-data-layer.md`
- Create: `.serena/memories/shared/architecture/auth-jwt-flow.md`
- Existing (move): `.serena/memories/architecture/agent-init-pattern.md` → `shared/architecture/`
- Existing (move): `.serena/memories/architecture/groq-chunking-strategy.md` → `shared/architecture/`

**Step 1: Write `shared/architecture/system-overview.md`**

Content to extract from CLAUDE.md S2 "Core patterns":

```markdown
# System Overview

## Services

| Service | Port | Entry point | Stack |
|---------|------|-------------|-------|
| Backend | 8181 | `backend/main.py` | Python 3.12, FastAPI, LangChain 1.x |
| Frontend | 3000 | `frontend/app/page.tsx` | Next.js 16, React 19, TypeScript |
| Dashboard | 8050 | `dashboard/app.py` | Plotly Dash (FLATLY theme) |
| Docs | 8000 | `mkdocs serve` | MkDocs Material |

## Core Patterns

- **`ChatServer`** (`backend/main.py`) — owns `ToolRegistry`,
  `AgentRegistry`, FastAPI app. All state in this class, no
  module-level mutable globals.
- **`BaseAgent`** (`backend/agents/base.py`) — ABC with agentic loop
  (`MAX_ITERATIONS=15`) + streaming. Subclasses only override
  `_build_llm()`.
- **LLM**: Claude Sonnet 4.6 via `langchain_anthropic.ChatAnthropic`.
  Config in `agents/general_agent.py` and `agents/stock_agent.py`.
- **Streaming**: `POST /chat/stream` returns NDJSON events:
  `thinking`, `tool_start`, `tool_done`, `warning`, `final`, `error`.
- **Same-day cache**:
  `~/.ai-agent-ui/data/cache/{TICKER}_{key}_{YYYY-MM-DD}.txt`.
- **Centralised paths**: `backend/paths.py` — single source of truth
  for all filesystem locations. Override root with `AI_AGENT_UI_HOME`.
- **Tool registration order**: `search_market_news` registered after
  GeneralAgent, before StockAgent.
- **Ticker auto-linking**: `tools/_ticker_linker.py` uses
  `threading.local()` to pass `user_id` from HTTP handler into
  `@tool` functions.
- **Freshness gates**: Analysis skips if done today (Iceberg check);
  forecast skips if run within 7 days. Both non-blocking.

## Filesystem Layout

All runtime data under `~/.ai-agent-ui/` (override: `AI_AGENT_UI_HOME`).
Paths centralised in `backend/paths.py`.

```
~/.ai-agent-ui/
├── data/iceberg/{catalog.db,warehouse/}   # Iceberg tables
├── data/{cache,raw,forecasts,avatars}/     # runtime data
├── charts/{analysis,forecasts}/            # HTML charts
├── logs/                                   # rotating agent.log
├── backend.env                             # secrets (symlinked)
└── frontend.env.local                      # service URLs (symlinked)
```

## Key Directories

- `backend/` — agents, tools, config
- `auth/` — JWT + RBAC + OAuth PKCE + user-ticker linking
- `stocks/` — Iceberg persistence (9 tables, single source of truth)
- `frontend/` — SPA (Next.js)
- `dashboard/` — Dash + services, incl. Marketplace page
- `hooks/` — pre-commit, pre-push
```

Use Serena `write_memory` with name `shared/architecture/system-overview`.

**Step 2: Write `shared/architecture/iceberg-data-layer.md`**

Content from CLAUDE.md S2 "Iceberg (single source of truth)":

```markdown
# Iceberg Data Layer

## Core Rules

- ALL stock data lives in Iceberg at
  `~/.ai-agent-ui/data/iceberg/`.
- `_require_repo()` raises `RuntimeError` if unavailable;
  `_get_repo()` returns `None`.
- **Copy-on-write upserts**: read full table -> mutate DataFrame ->
  `table.overwrite()` (no native UPDATE in PyIceberg 0.11).
- `_load_parquet()` reads from Iceberg, not flat files.
- Dashboard uses `_get_ohlcv_cached` / `_get_forecast_cached`.
- Single repo singleton via `_stock_shared.py`.
- Writes MUST NOT be silenced — failures propagate to tool
  exception handlers.

## Anti-Patterns

| Anti-Pattern | Correct Pattern |
|---|---|
| Flat file reads for stock data | Iceberg via `_load_parquet()` / `StockRepository` |
| Duplicate repo singletons | Import from `_stock_shared.py` |
| Silencing Iceberg write failures | Let errors propagate to tool handler |

## Performance

- Copy-on-write is expensive: `table.overwrite()` reads + rewrites
  the full table. Batch updates; minimize calls.
- N+1 queries: Load all data in one call, not one query per
  iteration.
- Cache awareness: `~/.ai-agent-ui/data/cache/` provides same-day
  caching. Clear only on refresh (`_clear_tool_cache()`).

## Inspection

```python
from stocks.repository import StockRepository
repo = StockRepository()
print(sorted(repo.get_all_registry().keys()))
for t in sorted(repo.get_all_registry().keys()):
    df = repo.get_ohlcv(t)
    adj = df["adj_close"].notna().mean() * 100
    print(f"{t}: {len(df)} rows, {adj:.1f}% adj_close")
```
```

Use Serena `write_memory` with name `shared/architecture/iceberg-data-layer`.

**Step 3: Write `shared/architecture/auth-jwt-flow.md`**

Content from CLAUDE.md S2 "Auth" + S7 "Security Constraints":

```markdown
# Auth & JWT Flow

## Architecture

- JWT env propagation: `main.py` copies Pydantic settings into
  `os.environ` for `auth/dependencies.py`.
- bcrypt 5.x direct (`hashpw`/`checkpw`).
- OAuth PKCE with `code_verifier` in sessionStorage.
- Refresh token deny-list is in-memory (cleared on restart).

## Security Rules

- NEVER hardcode API keys, passwords, tokens, or connection
  strings in source.
- NEVER commit `.env` files, `credentials.json`, or private keys.
- All secrets MUST come from environment variables via
  `backend/config.py` (Pydantic Settings).
- Validate required secrets at startup — fail fast.

## Input Validation

- All user input validated at system boundaries (API endpoints,
  tool inputs).
- Pydantic models for request body validation.
- Sanitize tickers: `ticker.upper().strip()`, reject
  non-alphanumeric (except `.`).
- Dashboard: `dashboard/utils.py:check_input_safety()` checks
  length, SQL injection patterns, XSS.

## OWASP Awareness

- **Injection**: Parameterized queries only.
- **Broken auth**: JWT with proper expiry.
- **Sensitive data exposure**: Error messages MUST NOT reveal stack
  traces, file paths, or secrets to end users.
- **XSS**: No `innerHTML`, no `dangerouslySetInnerHTML` without
  sanitization.
- **SSRF**: Validate URLs before fetching.

## Critical Files

- `backend/.env` — symlink to secrets; never commit.
- `auth/password.py` — bcrypt hashing; changes can lock out users.
- `stocks/repository.py` — Iceberg schemas; breaking changes cause
  data loss.
```

Use Serena `write_memory` with name `shared/architecture/auth-jwt-flow`.

**Step 4: Move existing memories to shared taxonomy**

```bash
# Move existing architecture memories to shared/
mv .serena/memories/architecture/agent-init-pattern.md \
   .serena/memories/shared/architecture/agent-init-pattern.md
mv .serena/memories/architecture/groq-chunking-strategy.md \
   .serena/memories/shared/architecture/groq-chunking-strategy.md
rmdir .serena/memories/architecture/
```

Or use Serena `write_memory` with name `shared/architecture/agent-init-pattern` (copy content from existing), then `delete_memory` the old one.

**Step 5: Verify all architecture memories exist**

Run: `ls -la .serena/memories/shared/architecture/`
Expected: 5 files — `system-overview.md`, `iceberg-data-layer.md`, `auth-jwt-flow.md`, `agent-init-pattern.md`, `groq-chunking-strategy.md`

**Step 6: Commit**

```bash
git add .serena/memories/shared/architecture/
git commit -m "docs: add shared architecture memories (migrated from CLAUDE.md)"
```

---

## Task 3: Create Shared Convention Memories

Migrate CLAUDE.md sections 3, 4, 5, 6, 8, 9, 10, 12 into convention memory files.

**Files:**
- Create: `.serena/memories/shared/conventions/python-style.md`
- Create: `.serena/memories/shared/conventions/typescript-style.md`
- Create: `.serena/memories/shared/conventions/git-workflow.md`
- Create: `.serena/memories/shared/conventions/testing-patterns.md`
- Create: `.serena/memories/shared/conventions/performance.md`
- Create: `.serena/memories/shared/conventions/error-handling.md`

**Step 1: Write `shared/conventions/python-style.md`**

Content from CLAUDE.md S3.2 + S3.4 + S3.5 + S6 Python anti-patterns:

```markdown
# Python Style & Conventions

## Hard Rules

- **Line length: 79 chars** — black, isort, flake8 all aligned via
  `pyproject.toml` + `.flake8`.
- **Python 3.12** — use `X | None` not `Optional[X]` (PEP 604).
- **No bare `print()`** — use `logging.getLogger(__name__)`.
- **Docstrings** — Google-style Sphinx on every module, class,
  public method.
- **No bare `except:`** — always `except Exception` or specific.
- **No module-level mutable globals** — all state in class instances.

## Line-Wrapping Patterns

```python
# Function calls — break after opening paren
result = some_function(
    first_arg, second_arg, third_arg,
)

# Chained methods — break before dot
df = (
    pd.read_parquet(path)
    .dropna(subset=["close"])
    .sort_values("date")
)

# Long strings — parenthesised f-strings (NOT implicit concat)
msg = (
    f"Processed {count} rows for {ticker}"
    f" from {start} to {end}."
)

# Long imports — parentheses
from tools._forecast_accuracy import (
    _calculate_forecast_accuracy,
)
```

## black Gotchas

- black WILL NOT wrap docstrings or comments — keep <= 79 manually.
- black merges implicit string concat onto one line — use f-strings
  or explicit `+`.
- `# fmt: off` / `# fmt: on` — last resort only.

## OOP Conventions

- New agents: subclass `BaseAgent`, override only `_build_llm()`.
- New tools: `@tool`-decorated, registered via
  `ToolRegistry.register()` in `ChatServer._register_tools()`.
- New HTTP bodies: Pydantic models in `main.py`.

## Lint Commands

```bash
black backend/ auth/ stocks/ scripts/ dashboard/
isort backend/ auth/ stocks/ scripts/ dashboard/ --profile black
flake8 backend/ auth/ stocks/ scripts/ dashboard/
```

Workflow: Write code (<=79 chars) -> black + isort -> flake8 ->
git add -> commit -> push. NEVER push with lint errors.

## Anti-Patterns

| Anti-Pattern | Correct Pattern |
|---|---|
| Bare `except:` | `except Exception as exc:` or specific |
| Bare `print()` | `logging.getLogger(__name__).info(...)` |
| `Optional[X]` | `X | None` (PEP 604) |
| Module-level mutable globals | State in class instances |
| Silent error swallowing | Log + re-raise or return error string |
| `eval()` / `exec()` | NEVER — find a safe alternative |
| Nested conditionals > 2 levels | Early returns / guard clauses |
| Hardcoded secrets | Environment variables via `config.py` |
| `from module import *` | Explicit named imports |
| Mutable default args `def f(x=[])` | `def f(x=None): x = x or []` |
| SQL string concatenation | Parameterized queries only |
| Lines > 79 chars | Wrap using patterns above |
| Implicit string concat | Use f-strings or explicit `+` |

## Docstring Format

```python
def update_ohlcv_adj_close(
    self, ticker: str, adj_close_map: dict
) -> int:
    """Update adj_close for existing OHLCV rows.

    Uses copy-on-write: reads all rows, merges values,
    overwrites table.

    Args:
        ticker: Uppercase ticker symbol.
        adj_close_map: ``{date: float}`` mapping.

    Returns:
        Number of rows updated.
    """
```
```

Use Serena `write_memory` with name `shared/conventions/python-style`.

**Step 2: Write `shared/conventions/typescript-style.md`**

Content from CLAUDE.md S3.3 + S6 TypeScript anti-patterns:

```markdown
# TypeScript Style & Conventions

## Rules

- Use `apiFetch` (not `fetch`) for all backend calls — auto-refreshes
  JWT. Source: `lib/apiFetch.ts`.
- ESLint: `@next/next/no-img-element` (use `<Image />`),
  `react-hooks/*`, no unused imports.
- Suppress with block-level `/* eslint-disable rule */` + reason
  comment — never blanket-disable.
- Config: `frontend/eslint.config.mjs`.

## Lint Commands

```bash
cd frontend && npx eslint . --fix && npx eslint .
```

## Anti-Patterns

| Anti-Pattern | Correct Pattern |
|---|---|
| `any` type | `unknown` + type narrowing |
| Raw `fetch()` calls | `apiFetch()` from `lib/apiFetch.ts` |
| `<img>` elements | `<Image />` from `next/image` |
| `innerHTML` assignment | Sanitized rendering or React JSX |
| Unused imports | Remove before commit |

## Frontend Performance

- Minimize re-renders: `React.memo`, `useMemo`, `useCallback`.
- Lazy loading: Heavy components via `dynamic()` imports.
- Images: Always `<Image />` from `next/image` (enforced by ESLint).
```

Use Serena `write_memory` with name `shared/conventions/typescript-style`.

**Step 3: Write `shared/conventions/git-workflow.md`**

Content from CLAUDE.md S3.1 + S4 + S5 + S9 + S12:

```markdown
# Git Workflow & PR Conventions

## Golden Rule

> Changes flow UP, sync flows DOWN. After every merge UP,
> immediately merge DOWN.

```
feature/* -> dev -> qa -> release -> main    (UP via PR)
               ^         ^          ^
         merge back merge back merge back    (DOWN immediately)
```

## Session Start (ALWAYS — NO EXCEPTIONS)

```bash
git fetch origin && git checkout dev && git pull origin dev
git checkout -b feature/<short-description>
git branch --show-current   # confirm before touching files
```

- NEVER commit directly to `dev`, `qa`, `release`, or `main`.
- ALWAYS branch off `dev`. If `feature/*` exists, check it out.

## Conventions

| Item | Format |
|------|--------|
| PR title | `[TYPE] Short description` — feat/fix/chore/refactor/hotfix/docs |
| Commit | `type: description` — feat/fix/refactor/docs/chore |
| Hotfix | Branch off `main`, PR to `main`, sync DOWN |
| Tags | `git tag -a v1.0.0 -m "Release v1.0.0"` |

## Promoting UP (e.g. dev -> qa)

```bash
git fetch origin
git checkout -b chore/promote-dev-to-qa origin/qa
git merge origin/dev
black backend/ auth/ stocks/ scripts/ dashboard/
isort backend/ auth/ stocks/ scripts/ dashboard/ --profile black
git push -u origin chore/promote-dev-to-qa && gh pr create --base qa
```

## Syncing DOWN

```bash
git fetch origin && git checkout dev && git pull origin dev
git merge origin/qa && git push origin dev
```

## Branch Protection

| Branch | Rules |
|--------|-------|
| `main` | No direct push, 2 approvals, CI pass |
| `release` | PR from `qa` only, 1 approval + QA lead |
| `qa` | PR from `dev` only, 1 approval |
| `dev` | PR from `feature/*`, 1 approval, tests + lint |

## Hard Rules

- NEVER push directly to protected branches.
- ALWAYS resolve conflicts locally before pushing.
- ALWAYS re-run lint after every merge.
- Long-lived feature branches (> 1 day) MUST merge dev daily.

## PR Review

### Focus Hierarchy

1. **Security** — auth bypass, injection, secret exposure.
2. **Correctness** — logic errors, edge cases, data loss.
3. **Breaking changes** — API contracts, schema, config.
4. **Performance** — N+1 queries, memory leaks, blocking calls.
5. **Maintainability** — readability, naming, dead code.

### What to Review

- ONLY files changed in the PR (not pre-existing issues).
- Focus on the "why" — does the change achieve its goal?
- Verify test coverage for new logic paths.
- Check error messages don't leak sensitive info.

### PR Checklist (for authors)

- [ ] All lint checks pass (black, isort, flake8, ESLint).
- [ ] All tests pass (`python -m pytest tests/ -v`).
- [ ] New code has tests (happy path + 1 error path).
- [ ] No hardcoded secrets, no `print()` statements.
- [ ] PR title follows `[TYPE] Description` format.
- [ ] PROGRESS.md updated with dated entry.

### Feedback Severity

- **CRITICAL**: MUST fix before merge.
- **WARNING**: SHOULD fix.
- **SUGGESTION**: COULD improve.

### Tone

- Be direct, not harsh. State what needs to change and why.
- Correctness over style. Only flag style issues linters miss.
- Explain the "why". One fix per comment.
- Use RFC 2119 keywords: MUST, SHOULD, MAY.

## Documentation Triggers

| Trigger | Update |
|---------|--------|
| Every session | `PROGRESS.md` — dated entry |
| New/changed API endpoint | `docs/` — relevant API page |
| Architecture change | Serena shared memory |
| New config/env var | `README.md` — env vars table |
| New Iceberg table | `stocks/create_tables.py` + `docs/` |
```

Use Serena `write_memory` with name `shared/conventions/git-workflow`.

**Step 4: Write `shared/conventions/testing-patterns.md`**

Content from CLAUDE.md S3.6:

```markdown
# Testing Patterns

## Running Tests

```bash
python -m pytest tests/ -v             # all (273 tests)
python -m pytest tests/backend/ -v     # backend
python -m pytest tests/dashboard/ -v   # dashboard
cd frontend && npx vitest run          # frontend (18 tests)

# E2E (Playwright — 49 tests, requires live services)
cd e2e && npm test                     # all projects
npx playwright test --project=frontend-chromium
npx playwright test --project=dashboard-chromium
npx playwright test --headed           # visible browser
npx playwright test --ui              # interactive UI mode
```

## Test-After-Feature Rule

After every feature addition and successful smoke test, update the
test suite immediately — happy path + 1 error path minimum. Do NOT
defer test writing to a later session.

## E2E (Playwright) Gotchas

- Use `pressSequentially()` not `fill()` for React 19 controlled
  inputs (textarea/input with `value={state}` + `onChange`).
- `dbc.*` components (dash_bootstrap_components 2.0.4) do NOT accept
  `data-testid` — wrap in `html.Div(**{"data-testid": "..."})`.
- Keep Playwright `outputDir` outside the project tree (`/tmp/`) to
  avoid triggering the Dash debug reloader.
- Use `{ force: true }` for clicks blocked by Dash debug toolbar.
```

Use Serena `write_memory` with name `shared/conventions/testing-patterns`.

**Step 5: Write `shared/conventions/performance.md`**

Content from CLAUDE.md S8:

```markdown
# Performance Guidelines

## Thresholds

| Metric | Target |
|--------|--------|
| API p95 (non-LLM) | < 500ms |
| LLM first token | < 2s |
| LLM full response | < 30s |
| Dashboard page load | < 3s |
| Test suite | < 30s (currently ~17s) |

## Python

- Prefer vectorized pandas/numpy over `iterrows()`.
- Avoid blocking I/O in async paths.
- Release large DataFrames after use.
- Use f-strings or `"".join()`, not repeated `+` in loops.

## Frontend

- Minimize re-renders: `React.memo`, `useMemo`, `useCallback`.
- Lazy loading: Heavy components via `dynamic()` imports.
- Always `<Image />` from `next/image`.
```

Use Serena `write_memory` with name `shared/conventions/performance`.

**Step 6: Write `shared/conventions/error-handling.md`**

Content from CLAUDE.md S10:

```markdown
# Error Handling & Logging

## Logging Standards

- Every module: `_logger = logging.getLogger(__name__)`.
- Levels: `DEBUG` (detail), `INFO` (normal), `WARNING` (recoverable),
  `ERROR` (failure).
- Include context: `_logger.info("Fetched %d rows for %s", count, ticker)`.
- NEVER log secrets, tokens, or passwords at INFO level.

## Patterns

```python
# Backend tools — return error strings, not exceptions
@tool
def my_tool(ticker: str) -> str:
    try:
        result = do_work(ticker)
        return f"Success: {result}"
    except ValueError as exc:
        return f"Error: {exc}"

# API endpoints — HTTPException with correct codes
raise HTTPException(status_code=404, detail="Ticker not found")

# Iceberg writes — MUST NOT be silenced
repo = _require_repo()
repo.insert_ohlcv(...)   # let exceptions propagate

# Non-critical pipeline steps — log and continue
try:
    info_msg = get_stock_info.invoke({"ticker": ticker})
    _record(result, "Company info", True, info_msg[:120])
except Exception as exc:
    _record(result, "Company info", False, str(exc)[:120])
```

## Error Categories

| Category | Handling | Example |
|----------|----------|---------|
| User input error | Return 400/422 | Invalid ticker format |
| Auth error | Return 401/403 | Expired JWT |
| External service failure | Log WARNING, retry/degrade | yfinance rate limit |
| Data integrity error | Log ERROR, abort | Iceberg schema mismatch |
| Configuration error | Log CRITICAL, fail fast | Missing `ANTHROPIC_API_KEY` |
```

Use Serena `write_memory` with name `shared/conventions/error-handling`.

**Step 7: Verify all convention memories exist**

Run: `ls -la .serena/memories/shared/conventions/`
Expected: 6 files

**Step 8: Commit**

```bash
git add .serena/memories/shared/conventions/
git commit -m "docs: add shared convention memories (migrated from CLAUDE.md)"
```

---

## Task 4: Create Shared Debugging & Onboarding & API Memories

**Files:**
- Create: `.serena/memories/shared/debugging/common-issues.md`
- Create: `.serena/memories/shared/debugging/mock-patching-gotchas.md`
- Create: `.serena/memories/shared/onboarding/setup-guide.md`
- Create: `.serena/memories/shared/api/streaming-protocol.md`

**Step 1: Write `shared/debugging/common-issues.md`**

Content from CLAUDE.md S13:

```markdown
# Common Issues & Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| `ModuleNotFoundError: tools` | `backend/` not on `sys.path` | Add `backend/` to `sys.path` |
| `RuntimeError: StockRepository unavailable` | Iceberg catalog missing | Run `python stocks/create_tables.py` |
| yfinance returns empty DataFrame | Ticker invalid or rate-limited | Verify on Yahoo Finance; wait and retry |
| `table.overwrite()` fails | Schema mismatch | Verify Arrow schema matches table definition |
| black + flake8 line length conflict | Missing `pyproject.toml` | Ensure `line-length = 79` |
| `isort` and `black` fight | Missing `--profile black` | Always use `isort --profile black` |
| Pre-commit hook fails | `ANTHROPIC_API_KEY` not set | Export key or `SKIP_PRE_COMMIT=1` |
| Dashboard can't read `.env` | `_load_dotenv()` not called | Dashboard is separate process |
| JWT auth fails across services | Missing env propagation | `main.py` copies settings to `os.environ` |
| Tests fail with `AttributeError` | Patching lazy import on wrong module | Patch at SOURCE module |

## Debug Logging

```bash
export LOG_LEVEL=DEBUG
python -c "
import logging; logging.basicConfig(level=logging.DEBUG)
from stocks.repository import StockRepository
repo = StockRepository()
print(repo.get_all_registry().keys())
"
```
```

Use Serena `write_memory` with name `shared/debugging/common-issues`.

**Step 2: Write `shared/debugging/mock-patching-gotchas.md`**

```markdown
# Mock Patching Gotchas

## Lazy Import Rule

Lazy imports inside functions CANNOT be patched on the importing
module. Patch at the SOURCE:

```python
# WRONG
@patch("stocks.backfill_adj_close.StockRepository")

# RIGHT
@patch("stocks.repository.StockRepository")
```

For `tools.*` in dashboard, use `patch.object()` on the imported
module.

## DataFrame Mutation

Functions that mutate DataFrames in-place also mutate the mock's
return value. Save lookup data BEFORE calling the function under
test.

## Cross-Package Imports

Dashboard tests needing `tools.*` MUST add `backend/` to `sys.path`.
```

Use Serena `write_memory` with name `shared/debugging/mock-patching-gotchas`.

**Step 3: Write `shared/onboarding/setup-guide.md`**

Content from CLAUDE.md S11 + Appendix:

```markdown
# Onboarding & Setup Guide

## Quick Start

```bash
./setup.sh                              # first-time setup
./scripts/dev-setup.sh                  # AI tooling setup
./run.sh start                          # all services
source ~/.ai-agent-ui/venv/bin/activate # Python virtualenv
```

## Python Dependencies

- Virtualenv: `~/.ai-agent-ui/venv` (Python 3.12.9).
- Install: `pip install <package>` inside venv, update
  `requirements.txt`.
- Key deps: FastAPI, LangChain 1.x, PyIceberg 0.11, Prophet,
  yfinance, pandas, pyarrow, bcrypt 5.x.

## Frontend Dependencies

- Package manager: npm. Lockfile: `frontend/package-lock.json`.
- Add packages: `cd frontend && npm install <package>`.

## Dependency Rules

- NEVER install packages globally.
- NEVER upgrade multiple major deps in same PR.
- ALWAYS run full test suite after dependency changes.

## First-Time Deployment

```bash
python auth/create_tables.py
python auth/migrate_users_table.py
python stocks/create_tables.py
python stocks/backfill_metadata.py
python stocks/backfill_adj_close.py
```

## Git Hooks

```bash
cp hooks/pre-commit .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
cp hooks/pre-push .git/hooks/pre-push && chmod +x .git/hooks/pre-push
```

Pre-commit: auto-fix + freshness checks (requires `ANTHROPIC_API_KEY`).
Bypass: `SKIP_PRE_COMMIT=1`.

## Known Limitations

- Facebook SSO: code complete, credentials are placeholders.
- `SERPAPI_API_KEY` required for `search_web` (100 free/month).
- Refresh token deny-list is in-memory (cleared on restart).
- Copy-on-write Iceberg upserts do not scale to very large tables.

## Env Files

- `~/.ai-agent-ui/backend.env` — master secrets
- `~/.ai-agent-ui/frontend.env.local` — service URLs
- `backend/.env` and `frontend/.env.local` are symlinks
```

Use Serena `write_memory` with name `shared/onboarding/setup-guide`.

**Step 4: Write `shared/api/streaming-protocol.md`**

```markdown
# Streaming Protocol

## Endpoint

`POST /chat/stream` — returns NDJSON (newline-delimited JSON).

## Event Types

| Event | Description |
|-------|-------------|
| `thinking` | LLM reasoning step |
| `tool_start` | Tool invocation beginning |
| `tool_done` | Tool result returned |
| `warning` | Non-fatal issue |
| `final` | Complete response |
| `error` | Fatal error |

## Frontend Consumption

Use `apiFetch` (not raw `fetch`) for all backend calls — it
auto-refreshes JWT tokens.
```

Use Serena `write_memory` with name `shared/api/streaming-protocol`.

**Step 5: Clean up old session/project memories**

Move `session/2026-03-09-progress` and `project/tooling` to the new local directories:

```bash
mv .serena/memories/session/2026-03-09-progress.md \
   .serena/memories/session/2026-03-09-progress.md  # already in right place
mv .serena/memories/project/tooling.md \
   .serena/memories/personal/tooling.md
rmdir .serena/memories/project/
```

Or use Serena `delete_memory` for old paths and `write_memory` for new paths.

**Step 6: Verify all memories**

Run: `find .serena/memories/shared/ -name "*.md" | sort`
Expected: 15 files total across architecture/ (5), conventions/ (6), debugging/ (2), onboarding/ (1), api/ (1)

**Step 7: Commit**

```bash
git add .serena/memories/shared/debugging/ .serena/memories/shared/onboarding/ .serena/memories/shared/api/
git commit -m "docs: add shared debugging, onboarding, and API memories"
```

---

## Task 5: Slim Down `CLAUDE.md`

**Files:**
- Modify: `CLAUDE.md` (rewrite from ~650 lines to ~80-100 lines)

**Step 1: Read current CLAUDE.md to confirm content**

Run: Read `CLAUDE.md`
Verify all content has been migrated to shared memories in Tasks 2-4.

**Step 2: Write the slim CLAUDE.md**

Replace entire file with:

```markdown
# CLAUDE.md — AI Agent UI

> Slim project instructions for Claude Code. Detailed knowledge
> lives in Serena shared memories — run `list_memories` to browse.

---

## Project Overview

Fullstack agentic chat app with stock analysis and Prophet forecasting.

| Service | Port | Entry point | Stack |
|---------|------|-------------|-------|
| Backend | 8181 | `backend/main.py` | Python 3.12, FastAPI, LangChain 1.x |
| Frontend | 3000 | `frontend/app/page.tsx` | Next.js 16, React 19, TypeScript |
| Dashboard | 8050 | `dashboard/app.py` | Plotly Dash (FLATLY theme) |
| Docs | 8000 | `mkdocs serve` | MkDocs Material |

```bash
./run.sh start                              # all services
./run.sh status                             # health check
source ~/.ai-agent-ui/venv/bin/activate      # Python virtualenv
```

**Key dirs**: `backend/` (agents, tools, config), `auth/` (JWT + RBAC + OAuth PKCE), `stocks/` (Iceberg — 9 tables), `frontend/` (SPA), `dashboard/` (Dash), `hooks/` (pre-commit, pre-push).

**Config**: `pyproject.toml` + `.flake8` (79 chars), `frontend/eslint.config.mjs`.

**Data**: `~/.ai-agent-ui/` (override: `AI_AGENT_UI_HOME`). Paths in `backend/paths.py`.

**Env**: `~/.ai-agent-ui/backend.env` + `frontend.env.local` (master); `backend/.env` + `frontend/.env.local` are symlinks.

---

## Hard Rules (NON-NEGOTIABLE)

These rules MUST be followed in every interaction:

1. **Line length 79 chars** — black, isort, flake8 aligned.
2. **No bare `print()`** — use `logging.getLogger(__name__)`.
3. **`X | None`** not `Optional[X]` (Python 3.12, PEP 604).
4. **No module-level mutable globals** — all state in class instances.
5. **No bare `except:`** — always `except Exception` or specific.
6. **Branch off `dev`** — NEVER push to `dev`, `qa`, `release`, `main`.
7. **`apiFetch`** not `fetch` — auto-refreshes JWT.
8. **`<Image />`** not `<img>` — enforced by ESLint.
9. **Patch at SOURCE module** — not the importing module.
10. **Iceberg writes MUST NOT be silenced** — let errors propagate.
11. **Update `PROGRESS.md`** after every session (dated entry).
12. **Test-after-feature** — happy path + 1 error path minimum.

---

## Serena Shared Memories

For detailed architecture, conventions, debugging, and onboarding
knowledge, use Serena's shared memories. Run `list_memories` to
see all available topics.

| Category | Topics |
|----------|--------|
| `shared/architecture/` | system-overview, agent-init-pattern, groq-chunking, iceberg-data-layer, auth-jwt-flow |
| `shared/conventions/` | python-style, typescript-style, git-workflow, testing-patterns, performance, error-handling |
| `shared/debugging/` | common-issues, mock-patching-gotchas |
| `shared/onboarding/` | setup-guide |
| `shared/api/` | streaming-protocol |

Load any memory with `read_memory` when you need the details.

---

## Quick Reference

```bash
# Lint
black backend/ auth/ stocks/ scripts/ dashboard/
isort backend/ auth/ stocks/ scripts/ dashboard/ --profile black
flake8 backend/ auth/ stocks/ scripts/ dashboard/
cd frontend && npx eslint . --fix

# Test
python -m pytest tests/ -v        # all (273 tests)
cd frontend && npx vitest run     # frontend (18 tests)
cd e2e && npm test                # E2E (49 tests, needs live services)
```
```

**Step 3: Verify line count**

Run: `wc -l CLAUDE.md`
Expected: ~85-95 lines

**Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: slim CLAUDE.md to ~90 lines, details in Serena shared memories"
```

---

## Task 6: Create `/promote-memory` Claude Code Skill

Claude Code custom commands are `.md` files with YAML frontmatter in `.claude/commands/`.

**Files:**
- Create: `.claude/commands/promote-memory.md`

**Step 1: Create the commands directory**

```bash
mkdir -p .claude/commands
```

**Step 2: Write the promote-memory command**

```markdown
---
description: Promote a session/personal Serena memory to shared (team) memory with AI cleanup
allowed-tools: [Read, Write, Edit, Bash, Glob, Grep, mcp__serena__list_memories, mcp__serena__read_memory, mcp__serena__write_memory, mcp__serena__delete_memory]
---

# /promote-memory — Promote Session Memory to Shared

Promote a personal or session Serena memory to the shared team
knowledge base with intelligent cleanup.

## Process

### Step 1: List available memories

Use `mcp__serena__list_memories` to list all memories. Present
the session/ and personal/ memories to the user. Ask which one
to promote.

If $ARGUMENTS is provided, use it as the memory name directly.

### Step 2: Read the source memory

Use `mcp__serena__read_memory` to read the selected memory content.

### Step 3: Ask target category

Present these categories and ask the user to pick one:

- `shared/architecture/` — system design decisions
- `shared/conventions/` — coding standards, workflow rules
- `shared/debugging/` — gotchas, workarounds, common issues
- `shared/onboarding/` — setup steps, env config
- `shared/api/` — endpoint contracts, data flows

Ask the user to provide a short name for the memory file
(e.g., `new-caching-pattern`).

### Step 4: Clean the content

Transform the session memory into team-quality documentation:

1. **Remove session-specific context**: dates ("today", "this
   session"), references to uncommitted work, personal progress
   notes, "I did X" language.
2. **Generalize findings**: Replace specific debugging sessions
   with reusable patterns. Turn "I found that X breaks when Y"
   into "X breaks when Y — fix by doing Z."
3. **Structure consistently**: Use headers, bullet points, code
   blocks. Match the style of existing shared memories.
4. **Keep it focused**: One topic per memory. If the source
   covers multiple topics, ask the user which to extract.

### Step 5: Write the shared memory

Use `mcp__serena__write_memory` with the name
`shared/<category>/<name>` and the cleaned content.

### Step 6: Create branch and commit

```bash
git checkout -b docs/promote-memory-<name>
git add .serena/memories/shared/<category>/<name>.md
git commit -m "docs: add shared memory — <category>/<name>"
```

### Step 7: Instruct the user

Tell the user:

> Memory promoted to `shared/<category>/<name>`.
> Branch: `docs/promote-memory-<name>`.
>
> Next steps:
> 1. `git push -u origin docs/promote-memory-<name>`
> 2. Create PR to `dev` with title `[docs] Add shared memory: <name>`
> 3. Get 1 approval, then merge.

### Step 8: Optionally delete the source

Ask the user if they want to delete the original session/personal
memory. If yes, use `mcp__serena__delete_memory`.
```

**Step 3: Verify the command file is valid**

Run: `head -5 .claude/commands/promote-memory.md`
Expected: YAML frontmatter with `---` delimiters

**Step 4: Commit**

```bash
git add .claude/commands/promote-memory.md
git commit -m "feat: add /promote-memory Claude Code skill"
```

---

## Task 7: Create `/check-stale-memories` Claude Code Skill

**Files:**
- Create: `.claude/commands/check-stale-memories.md`

**Step 1: Write the check-stale-memories command**

```markdown
---
description: Check shared Serena memories for stale references to renamed/deleted code
allowed-tools: [Read, Glob, Grep, Bash, mcp__serena__list_memories, mcp__serena__read_memory, mcp__serena__find_symbol, mcp__serena__search_for_pattern, mcp__serena__write_memory]
---

# /check-stale-memories — Detect Stale Shared Memories

Scan shared Serena memories for references to code that no longer
exists or has been significantly refactored.

## Process

### Step 1: List shared memories

Use `mcp__serena__list_memories` filtered to `shared/` topic.

### Step 2: For each shared memory

Use `mcp__serena__read_memory` to read the content. Extract:

1. **File paths** — any path like `backend/foo.py`, `auth/bar.py`
2. **Symbol names** — class names, function names, variable names
   referenced in code blocks or backtick references
3. **Config references** — env var names, config keys

### Step 3: Validate references

For each extracted reference:

- **File paths**: Use `Glob` to check if the file exists.
- **Symbol names**: Use `mcp__serena__find_symbol` to check if the
  symbol exists in the codebase.
- **Config references**: Use `Grep` to search for the config key.

### Step 4: Assess conceptual staleness

Beyond just missing references, check if the memory's description
of behavior still matches reality. For example:

- Memory says "function X does Y" — read function X and verify.
- Memory says "pattern A is used in module B" — verify the pattern.

Use `mcp__serena__search_for_pattern` for flexible matching.

### Step 5: Report findings

Present a table:

| Memory | Status | Issues |
|--------|--------|--------|
| shared/architecture/system-overview | OK | — |
| shared/conventions/python-style | STALE | `_ticker_linker.py` renamed |
| ... | ... | ... |

### Step 6: Suggest fixes

For each stale memory, suggest one of:

- **Update**: Provide the corrected content.
- **Remove**: If the memory is entirely obsolete.
- **Merge**: If the memory should be combined with another.

Ask the user which action to take for each stale memory.
If updating, use `mcp__serena__write_memory` to write the fix,
then commit on a `docs/fix-stale-memory-<name>` branch.
```

**Step 2: Commit**

```bash
git add .claude/commands/check-stale-memories.md
git commit -m "feat: add /check-stale-memories Claude Code skill"
```

---

## Task 8: Create `scripts/check-stale-memories.sh` CI Script

**Files:**
- Create: `scripts/check-stale-memories.sh`

**Step 1: Write the CI script**

```bash
#!/usr/bin/env bash
# check-stale-memories.sh — CI script to detect stale Serena shared memories.
#
# Scans .serena/memories/shared/*.md files for references to files and
# symbols that no longer exist in the codebase.
#
# Usage:
#   ./scripts/check-stale-memories.sh          # run from project root
#
# Exit codes:
#   0 — no stale references found (or warnings only)
#   0 — stale references found (non-blocking, warning only)
#
# Designed for CI: runs on PRs that touch backend/, auth/, stocks/,
# dashboard/, or frontend/. Skip for docs-only PRs.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MEMORIES_DIR="$PROJECT_ROOT/.serena/memories/shared"

# ANSI colours
if [[ -t 1 ]]; then
    R='\033[0;31m' G='\033[0;32m' Y='\033[1;33m' B='\033[1m' N='\033[0m'
else
    R='' G='' Y='' B='' N=''
fi

if [[ ! -d "$MEMORIES_DIR" ]]; then
    echo -e "${Y}[WARN]${N} No shared memories directory found at $MEMORIES_DIR"
    exit 0
fi

STALE_COUNT=0
CHECKED_COUNT=0

echo -e "${B}Checking shared memories for stale references...${N}"
echo ""

# Find all .md files in shared memories
while IFS= read -r -d '' memory_file; do
    rel_path="${memory_file#$PROJECT_ROOT/}"
    memory_name="${memory_file#$MEMORIES_DIR/}"
    memory_name="${memory_name%.md}"
    issues=""

    # Extract file path references (backtick-quoted paths with extensions)
    while IFS= read -r ref_path; do
        # Skip common non-file references
        [[ "$ref_path" == *"~/"* ]] && continue
        [[ "$ref_path" == *"http"* ]] && continue
        [[ "$ref_path" == *"localhost"* ]] && continue
        [[ "$ref_path" == *"YYYY"* ]] && continue
        [[ "$ref_path" == *"{TICKER}"* ]] && continue
        [[ "$ref_path" == *"example"* ]] && continue

        # Check if the file exists relative to project root
        if [[ ! -f "$PROJECT_ROOT/$ref_path" ]] && \
           [[ ! -d "$PROJECT_ROOT/$ref_path" ]]; then
            issues="${issues}\n    Missing: $ref_path"
        fi
    done < <(grep -oE '`[a-zA-Z][a-zA-Z0-9_/.-]+\.(py|tsx?|jsx?|md|sh|yml|yaml|json|toml|cfg)`' "$memory_file" | tr -d '`' | sort -u)

    CHECKED_COUNT=$((CHECKED_COUNT + 1))

    if [[ -n "$issues" ]]; then
        STALE_COUNT=$((STALE_COUNT + 1))
        echo -e "${Y}[STALE]${N} $memory_name"
        echo -e "$issues"
        echo ""
    fi
done < <(find "$MEMORIES_DIR" -name "*.md" -print0 | sort -z)

# Summary
echo "────────────────────────────────────────"
if [[ $STALE_COUNT -eq 0 ]]; then
    echo -e "${G}All $CHECKED_COUNT shared memories are up to date.${N}"
else
    echo -e "${Y}$STALE_COUNT/$CHECKED_COUNT memories have potentially stale references.${N}"
    echo "Run /check-stale-memories for deeper AI-powered analysis."
fi

# Always exit 0 — this is a non-blocking warning
exit 0
```

**Step 2: Make it executable**

```bash
chmod +x scripts/check-stale-memories.sh
```

**Step 3: Test it**

Run: `./scripts/check-stale-memories.sh`
Expected: Scans all shared memories, reports any missing file references, exits 0.

**Step 4: Commit**

```bash
git add scripts/check-stale-memories.sh
git commit -m "feat: add CI script for stale memory detection"
```

---

## Task 9: Create `scripts/dev-setup.sh` Onboarding Script

**Files:**
- Create: `scripts/dev-setup.sh`

**Step 1: Write the dev-setup script**

```bash
#!/usr/bin/env bash
# dev-setup.sh — AI tooling setup for new developers.
#
# Verifies Claude Code + Serena are configured, validates shared
# memories, creates local memory directories, and runs Serena
# onboarding.
#
# Prerequisites: Run ./setup.sh first (Python, Node, env files).
#
# Usage:
#   ./scripts/dev-setup.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# ANSI colours
if [[ -t 1 ]]; then
    R='\033[0;31m' G='\033[0;32m' Y='\033[1;33m' C='\033[0;36m' B='\033[1m' N='\033[0m'
else
    R='' G='' Y='' B='' N=''
fi

ok()   { echo -e "  ${G}[OK]${N} $1"; }
warn() { echo -e "  ${Y}[WARN]${N} $1"; }
fail() { echo -e "  ${R}[FAIL]${N} $1"; exit 1; }
info() { echo -e "  ${C}[INFO]${N} $1"; }
step() { echo ""; echo -e "${B}[$1]${N} $2"; echo "────────────────────────────────────────────────────────────────"; }

echo ""
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║            AI Agent UI — Developer AI Tooling Setup             ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""

PASS=0
TOTAL=0

_check() {
    local label="$1" condition="$2"
    TOTAL=$((TOTAL + 1))
    if eval "$condition" &>/dev/null; then
        ok "$label"
        PASS=$((PASS + 1))
        return 0
    else
        warn "$label"
        return 1
    fi
}

# ══════════════════════════════════════════════════════════════════════
# Step 1: Verify prerequisites (setup.sh already ran)
# ══════════════════════════════════════════════════════════════════════
step "1/7" "Verifying prerequisites"

VENV_DIR="${HOME}/.ai-agent-ui/venv"
VENV_PYTHON="$VENV_DIR/bin/python"

_check "Python virtualenv exists" "[[ -f '$VENV_PYTHON' ]]" || \
    fail "Virtualenv not found. Run ./setup.sh first."

_check "Node.js available" "command -v node" || \
    fail "Node.js not found. Run ./setup.sh first."

_check "npm available" "command -v npm" || \
    fail "npm not found. Run ./setup.sh first."

_check "Git repository" "[[ -d '$PROJECT_ROOT/.git' ]]" || \
    fail "Not a git repository."

_check "backend/.env exists" "[[ -f '$PROJECT_ROOT/backend/.env' ]]" || \
    fail "backend/.env missing. Run ./setup.sh first."

# ══════════════════════════════════════════════════════════════════════
# Step 2: Verify Claude Code
# ══════════════════════════════════════════════════════════════════════
step "2/7" "Checking Claude Code CLI"

if command -v claude &>/dev/null; then
    ok "Claude Code CLI found ($(claude --version 2>/dev/null || echo 'version unknown'))"
else
    warn "Claude Code CLI not found"
    echo ""
    echo "  Install Claude Code:"
    echo "    npm install -g @anthropic-ai/claude-code"
    echo ""
    echo "  Then re-run this script."
fi

# ══════════════════════════════════════════════════════════════════════
# Step 3: Check Serena MCP configuration
# ══════════════════════════════════════════════════════════════════════
step "3/7" "Checking Serena MCP server"

SERENA_CONFIG="$PROJECT_ROOT/.serena/project.yml"
if [[ -f "$SERENA_CONFIG" ]]; then
    ok "Serena project config found"
else
    warn "Serena project.yml not found at .serena/project.yml"
    echo "  Serena MCP server may not be configured."
    echo "  See: https://github.com/oraios/serena"
fi

# Check if Serena is in Claude Code MCP settings
MCP_CONFIG="$HOME/.claude/settings/mcp.json"
PROJ_MCP_CONFIG="$PROJECT_ROOT/.mcp.json"
SERENA_FOUND=0

if [[ -f "$MCP_CONFIG" ]] && grep -q "serena" "$MCP_CONFIG" 2>/dev/null; then
    SERENA_FOUND=1
fi
if [[ -f "$PROJ_MCP_CONFIG" ]] && grep -q "serena" "$PROJ_MCP_CONFIG" 2>/dev/null; then
    SERENA_FOUND=1
fi

if [[ $SERENA_FOUND -eq 1 ]]; then
    ok "Serena found in MCP configuration"
else
    warn "Serena not found in MCP config"
    echo "  Add Serena to your Claude Code MCP settings."
    echo "  Check ~/.claude/settings/mcp.json or .mcp.json"
fi

# ══════════════════════════════════════════════════════════════════════
# Step 4: Verify shared memories
# ══════════════════════════════════════════════════════════════════════
step "4/7" "Verifying shared memories"

SHARED_DIR="$PROJECT_ROOT/.serena/memories/shared"
EXPECTED_DIRS=("architecture" "conventions" "debugging" "onboarding" "api")

if [[ -d "$SHARED_DIR" ]]; then
    ok "Shared memories directory exists"
    MEMORY_COUNT=$(find "$SHARED_DIR" -name "*.md" | wc -l | tr -d ' ')
    info "Found $MEMORY_COUNT shared memory files"

    for dir in "${EXPECTED_DIRS[@]}"; do
        if [[ -d "$SHARED_DIR/$dir" ]]; then
            count=$(find "$SHARED_DIR/$dir" -name "*.md" | wc -l | tr -d ' ')
            ok "shared/$dir/ ($count files)"
        else
            warn "shared/$dir/ missing"
        fi
    done
else
    warn "Shared memories directory not found"
    echo "  Pull latest from dev to get shared memories:"
    echo "    git fetch origin && git pull origin dev"
fi

# ══════════════════════════════════════════════════════════════════════
# Step 5: Create local memory directories
# ══════════════════════════════════════════════════════════════════════
step "5/7" "Creating local memory directories"

for dir in "session" "personal"; do
    LOCAL_DIR="$PROJECT_ROOT/.serena/memories/$dir"
    if [[ ! -d "$LOCAL_DIR" ]]; then
        mkdir -p "$LOCAL_DIR"
        touch "$LOCAL_DIR/.gitkeep"
        ok "Created memories/$dir/"
    else
        ok "memories/$dir/ already exists"
    fi
done

# ══════════════════════════════════════════════════════════════════════
# Step 6: Install git hooks (if not already)
# ══════════════════════════════════════════════════════════════════════
step "6/7" "Checking git hooks"

HOOKS_DIR="$PROJECT_ROOT/.git/hooks"
for hook in "pre-commit" "pre-push"; do
    if [[ -x "$HOOKS_DIR/$hook" ]]; then
        ok "Git $hook hook installed"
    else
        if [[ -f "$PROJECT_ROOT/hooks/$hook" ]]; then
            cp "$PROJECT_ROOT/hooks/$hook" "$HOOKS_DIR/$hook"
            chmod +x "$HOOKS_DIR/$hook"
            ok "Git $hook hook installed (just now)"
        else
            warn "hooks/$hook source not found"
        fi
    fi
done

# ══════════════════════════════════════════════════════════════════════
# Step 7: Verify GitHub CLI
# ══════════════════════════════════════════════════════════════════════
step "7/7" "Checking GitHub CLI"

if command -v gh &>/dev/null; then
    if gh auth status &>/dev/null 2>&1; then
        ok "GitHub CLI authenticated"
    else
        warn "GitHub CLI installed but not authenticated"
        echo "  Run: gh auth login"
    fi
else
    warn "GitHub CLI (gh) not installed"
    echo "  Install: brew install gh (macOS) or see https://cli.github.com"
fi

# ══════════════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════════════
echo ""
echo "════════════════════════════════════════════════════════════════════"
if [[ $PASS -eq $TOTAL ]]; then
    echo -e "${G}  All $TOTAL checks passed. AI tooling ready!${N}"
else
    echo -e "${Y}  $PASS/$TOTAL checks passed. Review warnings above.${N}"
fi
echo "════════════════════════════════════════════════════════════════════"
echo ""
echo -e "  ${B}AI Tooling:${N}"
echo "    Claude Code + Serena MCP — project: ai-agent-ui"
echo ""
echo -e "  ${B}Shared Memories:${N}"
echo "    .serena/memories/shared/    (git-tracked, PR-reviewed)"
echo "    .serena/memories/session/   (local, gitignored)"
echo "    .serena/memories/personal/  (local, gitignored)"
echo ""
echo -e "  ${B}Useful Commands:${N}"
echo "    /promote-memory           Promote session memory to shared"
echo "    /check-stale-memories     Check for outdated shared memories"
echo "    /sc:save                  Save session context"
echo ""
echo -e "  ${B}Next Steps:${N}"
echo "    1. Start a Claude Code session in the project directory"
echo "    2. Serena will auto-load shared memories as needed"
echo "    3. Use /sc:save at end of session to persist discoveries"
echo "    4. Use /promote-memory to share reusable insights with team"
echo ""
```

**Step 2: Make it executable**

```bash
chmod +x scripts/dev-setup.sh
```

**Step 3: Test it**

Run: `./scripts/dev-setup.sh`
Expected: All checks pass (or warnings for missing optional tools). Summary prints correctly.

**Step 4: Commit**

```bash
git add scripts/dev-setup.sh
git commit -m "feat: add dev-setup.sh for AI tooling onboarding"
```

---

## Task 10: Update Serena Memory (auto-memory sync)

Update Claude Code auto-memory and Serena memory to reflect the new setup.

**Files:**
- Modify: `~/.claude/projects/-Users-abhay-Documents-projects/memory/MEMORY.md`

**Step 1: Update Claude Code auto-memory**

Add a section about the team knowledge sharing setup:

```markdown
## Team Knowledge Sharing (implemented)

- **Shared memories**: `.serena/memories/shared/` — git-committed, PR-reviewed
- **Local memories**: `session/` and `personal/` — gitignored
- **Slim CLAUDE.md**: ~90 lines (hard rules only), details in Serena
- **Skills**: `/promote-memory`, `/check-stale-memories`
- **Scripts**: `scripts/dev-setup.sh`, `scripts/check-stale-memories.sh`
- **Taxonomy**: architecture/, conventions/, debugging/, onboarding/, api/
```

**Step 2: Update Serena project memory**

Use Serena `write_memory` with name `shared/onboarding/setup-guide` to include reference to `/promote-memory` and `/check-stale-memories` skills.

**Step 3: Commit all remaining changes**

```bash
git add -A
git status  # verify no secrets or unwanted files
git commit -m "chore: finalize team knowledge sharing ecosystem"
```

---

## Execution Order Summary

| Task | Description | Depends On | Est. Time |
|------|-------------|------------|-----------|
| 1 | Update `.gitignore` files | — | 3 min |
| 2 | Create shared architecture memories | 1 | 10 min |
| 3 | Create shared convention memories | 1 | 12 min |
| 4 | Create shared debugging/onboarding/API memories | 1 | 8 min |
| 5 | Slim down `CLAUDE.md` | 2, 3, 4 | 5 min |
| 6 | Create `/promote-memory` skill | 1 | 5 min |
| 7 | Create `/check-stale-memories` skill | 1 | 5 min |
| 8 | Create `check-stale-memories.sh` CI script | 1 | 5 min |
| 9 | Create `dev-setup.sh` onboarding script | 1 | 8 min |
| 10 | Update auto-memory + final commit | 1-9 | 3 min |

**Parallelizable groups:**
- Tasks 2, 3, 4 can run in parallel (independent memory files)
- Tasks 6, 7, 8 can run in parallel (independent new files)
- Task 5 depends on 2-4 completing first
- Task 9 is independent of 2-8
- Task 10 is the final step
