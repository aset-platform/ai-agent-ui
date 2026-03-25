# CLAUDE.md — AI Agent UI

> Slim project instructions for Claude Code. Detailed knowledge
> lives in Serena shared memories — run `list_memories` to browse.

---

## Project Overview

Fullstack agentic chat app with stock analysis and Prophet forecasting.
Native portfolio dashboard with TradingView lightweight-charts +
react-plotly.js. Dual payment gateways (Razorpay INR + Stripe USD).
All pages fully migrated from Dash to Next.js.

| Service | Port | Entry point | Stack |
|---------|------|-------------|-------|
| Backend | 8181 | `backend/main.py` | Python 3.12, FastAPI, LangChain 1.x |
| Frontend | 3000 | `frontend/app/page.tsx` | Next.js 16, React 19, lightweight-charts |
| Docs | 8000 | `mkdocs serve` | MkDocs Material |

```bash
./run.sh start                              # all services
./run.sh status                             # health check
source ~/.ai-agent-ui/venv/bin/activate      # Python virtualenv
```

**Key dirs**: `backend/` (agents, tools, config), `auth/` (JWT + RBAC + OAuth PKCE), `stocks/` (Iceberg — 11 tables), `frontend/` (SPA), `hooks/` (pre-commit, pre-push).

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
13. **Co-Authored-By in commits** — always use:
    `Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>`
14. **No `@traceable` on `FallbackLLM.invoke()`** — breaks LangChain
    tool call parsing. Inner ChatGroq/ChatAnthropic are auto-traced.
15. **`NEXT_PUBLIC_BACKEND_URL` = `http://localhost:8181`** — never
    `127.0.0.1`. Hostname mismatch breaks HttpOnly refresh cookies.
16. **LHCI can't audit authenticated routes** — Lighthouse clears
    localStorage per navigation. Use `npm run perf:full` (Playwright)
    instead. See `PERFORMANCE.md`.

---

## Serena Shared Memories

For detailed architecture, conventions, debugging, and onboarding
knowledge, use Serena's shared memories. Run `list_memories` to
see all available topics.

| Category | Topics |
|----------|--------|
| `shared/architecture/` | system-overview, agent-init-pattern, groq-chunking, iceberg-data-layer, auth-jwt-flow, langsmith-observability, lighthouse-performance-workflow, subscription-billing, portfolio-analytics, currency-aware-agent, token-budget-concurrency, payment-transaction-ledger |
| `shared/conventions/` | python-style, typescript-style, git-workflow, testing-patterns, performance, error-handling, llm-tool-forcing, jira-mcp-usage, security-hardening, e2e-test-patterns |
| `shared/debugging/` | common-issues, mock-patching-gotchas, chat-session-recording, cookie-hostname-mismatch, ohlcv-nan-close-price, razorpay-integration-gotchas, iceberg-epoch-dates |
| `shared/onboarding/` | setup-guide, test-venv-setup, tooling |
| `shared/api/` | streaming-protocol |

Load any memory with `read_memory` when you need the details.

---

## Gotchas (learned the hard way)

- **`settings.local.json` deny rules**: No parentheses in
  `Bash(...)` patterns — Claude Code parser treats `()` as
  pattern delimiters. Fork bomb rule crashed the CLI.
- **slowapi rate limiter**: Module-level singleton — state
  bleeds across test files. Use `limiter.enabled = False` in
  test fixtures, not `limiter.reset()`.
- **`get_settings().debug`**: May not exist in test context.
  Use `getattr(_get_settings(), "debug", True)` with fallback.
- **TokenBudget**: Use `reserve()`/`release()` (atomic), not
  `can_afford()`/`record()` (TOCTOU race). See memory
  `shared/architecture/token-budget-concurrency`.
- **StockRepository**: Always use `_require_repo()` from
  `tools/_stock_shared.py` — never instantiate directly.
- **E2E demo passwords**: Run `seed_demo_data.py` if login
  fails. Previous test runs may have changed passwords.

---

## Quick Reference

```bash
# Lint
black backend/ auth/ stocks/ scripts/
isort backend/ auth/ stocks/ scripts/ --profile black
flake8 backend/ auth/ stocks/ scripts/
cd frontend && npx eslint . --fix

# Test
python -m pytest tests/ -v        # all (548 tests)
cd frontend && npx vitest run     # frontend (18 tests)
cd e2e && npm test                # E2E (~219 tests, needs live services)

# Seed (required before first E2E run)
PYTHONPATH=backend python scripts/seed_demo_data.py

# Performance (run from frontend/)
npm run perf:check                # LHCI on /login (pre-PR gate)
npm run perf:audit                # Playwright 10-route quick check
npm run perf:full                 # Full 42-point surface audit
npm run analyze                   # Bundle treemap (ANALYZE=true)
```
