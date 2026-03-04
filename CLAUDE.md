# CLAUDE.md — AI Agent UI

> Project instructions for Claude Code. Read before making any changes.
> For deep dives, see `README.md`, `PROGRESS.md`, and `docs/`.

---

## 1. Project Overview

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
source backend/demoenv/bin/activate         # Python virtualenv
```

**Key directories**: `backend/` (agents, tools, config), `auth/` (JWT + RBAC + OAuth PKCE), `stocks/` (Iceberg persistence — 8 tables, single source of truth), `frontend/` (SPA), `dashboard/` (Dash + services), `hooks/` (pre-commit, pre-push).

**Config files**: `pyproject.toml` (black + isort, 79 chars), `.flake8` (flake8, 79 chars), `frontend/eslint.config.mjs`.

**Data**: Iceberg is primary (`data/iceberg/`). `data/raw/` and `data/forecasts/` are local backup only. `data/cache/` is same-day, gitignored.

**Env**: `~/.ai-agent-ui/backend.env` and `frontend.env.local` (master); `backend/.env` and `frontend/.env.local` are symlinks created by `setup.sh`.

---

## 2. Architecture Summary

### Core patterns

- **`ChatServer`** (`backend/main.py`) — owns `ToolRegistry`, `AgentRegistry`, FastAPI app. All state in this class, no module-level mutable globals.
- **`BaseAgent`** (`backend/agents/base.py`) — ABC with agentic loop (`MAX_ITERATIONS=15`) + streaming. Subclasses only override `_build_llm()`.
- **LLM**: Claude Sonnet 4.6 via `langchain_anthropic.ChatAnthropic`. Config in `agents/general_agent.py` and `agents/stock_agent.py`.
- **Streaming**: `POST /chat/stream` returns NDJSON events: `thinking`, `tool_start`, `tool_done`, `warning`, `final`, `error`.
- **Same-day cache**: `data/cache/{TICKER}_{key}_{YYYY-MM-DD}.txt` — repeat tool calls return instantly.
- **Tool registration order**: `search_market_news` registered _after_ GeneralAgent, _before_ StockAgent.

### Iceberg (single source of truth)

- ALL stock data lives in Iceberg. `_require_repo()` raises `RuntimeError` if unavailable; `_get_repo()` returns `None`.
- **Copy-on-write upserts**: read full table -> mutate DataFrame -> `table.overwrite()` (no native UPDATE in PyIceberg 0.11).
- `_load_parquet()` reads from Iceberg, not flat files. Dashboard uses `_get_ohlcv_cached` / `_get_forecast_cached`.
- Single repo singleton via `_stock_shared.py`. Writes MUST NOT be silenced — failures propagate to tool exception handlers.

### Auth

- JWT env propagation: `main.py` copies Pydantic settings into `os.environ` for `auth/dependencies.py`.
- bcrypt 5.x direct (`hashpw`/`checkpw`). OAuth PKCE with `code_verifier` in sessionStorage.

---

## 3. Coding Standards

### 3.1 Session start (ALWAYS — NO EXCEPTIONS)

```bash
git fetch origin && git checkout dev && git pull origin dev
git checkout -b feature/<short-description>
git branch --show-current   # confirm before touching files
```

- **NEVER** commit directly to `dev`, `qa`, `release`, or `main`.
- **ALWAYS** branch off `dev`. If `feature/*` exists, check it out.

### 3.2 Python rules

- **Line length: 79 chars** — black, isort, flake8 all aligned via `pyproject.toml` + `.flake8`. Write short from the start.
- **Python 3.12** — use `X | None` not `Optional[X]` (PEP 604).
- **No bare `print()`** — use `logging.getLogger(__name__)`.
- **Docstrings** — Google-style Sphinx on every module, class, public method.
- **No bare `except:`** — always `except Exception` or specific.
- **No module-level mutable globals** — all state in class instances.

#### Line-wrapping patterns (when > 79 chars)

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

#### black gotchas

- black WILL NOT wrap docstrings or comments — keep ≤ 79 manually.
- black merges implicit string concatenation onto one line — use f-strings or explicit `+`.
- `# fmt: off` / `# fmt: on` — last resort only.

### 3.3 TypeScript rules

- Use `apiFetch` (not `fetch`) for all backend calls — auto-refreshes JWT.
- ESLint: `@next/next/no-img-element` (use `<Image />`), `react-hooks/*`, no unused imports.
- Suppress with block-level `/* eslint-disable rule */` + reason comment — never blanket-disable.

### 3.4 OOP conventions

- New agents: subclass `BaseAgent`, override only `_build_llm()`.
- New tools: `@tool`-decorated, registered via `ToolRegistry.register()` in `ChatServer._register_tools()`.
- New HTTP bodies: Pydantic models in `main.py`.

### 3.5 Lint commands

```bash
# Python auto-fix + verify
black backend/ auth/ stocks/ scripts/ dashboard/
isort backend/ auth/ stocks/ scripts/ dashboard/ --profile black
flake8 backend/ auth/ stocks/ scripts/ dashboard/

# Frontend
cd frontend && npx eslint . --fix && npx eslint .
```

**Workflow**: Write code (≤79 chars) -> black + isort -> flake8 -> git add -> commit -> push. NEVER push with lint errors.

### 3.6 Testing

```bash
python -m pytest tests/ -v             # all (133 tests)
python -m pytest tests/backend/ -v     # backend
python -m pytest tests/dashboard/ -v   # dashboard
cd frontend && npx vitest run          # frontend (18 tests)
```

**Mock patching gotcha**: Lazy imports inside functions CANNOT be patched on the importing module. Patch at the SOURCE: `@patch("stocks.repository.StockRepository")` not `@patch("stocks.backfill_adj_close.StockRepository")`. For `tools.*` in dashboard, use `patch.object()` on the imported module.

**DataFrame mutation**: Functions that mutate DataFrames in-place also mutate the mock's return value. Save lookup data BEFORE calling the function under test.

**Cross-package imports**: Dashboard tests needing `tools.*` MUST add `backend/` to `sys.path`.

**Test-after-feature rule**: After every feature addition and successful smoke test, update the test suite immediately — add unit tests covering the new logic (happy path + 1 error path minimum). Do NOT defer test writing to a later session.

---

## 4. Git Branching Strategy and Workflow

### Golden rule

> **Changes flow UP, sync flows DOWN. After every merge UP, immediately merge DOWN.**

```
feature/* -> dev -> qa -> release -> main    (UP via PR)
               ^         ^          ^
         merge back merge back merge back    (DOWN immediately)
```

### Conventions

| Item | Format |
|------|--------|
| PR title | `[TYPE] Short description` — feat/fix/chore/refactor/hotfix/docs |
| Commit | `type: description` — feat/fix/refactor/docs/chore |
| Hotfix | Branch off `main`, PR to `main`, sync DOWN |
| Tags | `git tag -a v1.0.0 -m "Release v1.0.0"` |

### Promoting UP (e.g. dev -> qa)

```bash
git fetch origin
git checkout -b chore/promote-dev-to-qa origin/qa
git merge origin/dev       # resolve conflicts locally (source wins)
black backend/ auth/ stocks/ scripts/ dashboard/
isort backend/ auth/ stocks/ scripts/ dashboard/ --profile black
git push -u origin chore/promote-dev-to-qa && gh pr create --base qa
```

### Syncing DOWN (immediately after every upward merge)

```bash
git fetch origin && git checkout dev && git pull origin dev
git merge origin/qa && git push origin dev
```

### Branch protection

| Branch | Rules |
|--------|-------|
| `main` | No direct push, 2 approvals, CI pass |
| `release` | PR from `qa` only, 1 approval + QA lead |
| `qa` | PR from `dev` only, 1 approval |
| `dev` | PR from `feature/*`, 1 approval, tests + lint |

### Hard rules (NO EXCEPTIONS)

- NEVER push directly to `dev`, `qa`, `release`, or `main`.
- ALWAYS resolve conflicts locally before pushing.
- ALWAYS re-run lint after every merge.
- Long-lived feature branches (> 1 day) MUST merge dev daily.

---

## 5. PR Review Rules

### Review focus hierarchy (in priority order)

1. **Security** — auth bypass, injection, secret exposure, OWASP Top 10.
2. **Correctness** — logic errors, edge cases, data loss risk.
3. **Breaking changes** — API contracts, database schema, config format.
4. **Performance** — N+1 queries, memory leaks, blocking calls.
5. **Maintainability** — readability, naming, dead code.

### What to review

- ONLY files changed in the PR (not pre-existing issues).
- Focus on the "why" — does the change achieve its stated goal?
- Verify test coverage for new logic paths.
- Check that error messages do not leak sensitive information.

### What NOT to review

- Pre-existing issues not introduced by this PR.
- Style/formatting issues that linters catch.
- Lines with `# noqa` or `// eslint-disable` (already justified).
- Nitpicks that don't affect correctness or security.

### PR checklist (for authors)

- [ ] All lint checks pass (black, isort, flake8, ESLint).
- [ ] All tests pass (`python -m pytest tests/ -v`).
- [ ] New code has tests (target: happy path + 1 error path).
- [ ] No hardcoded secrets, no `print()` statements.
- [ ] PR title follows `[TYPE] Description` format.
- [ ] PROGRESS.md updated with dated entry.

### Feedback severity labels

- **CRITICAL**: MUST fix before merge (security, correctness, data loss).
- **WARNING**: SHOULD fix (convention violation, performance).
- **SUGGESTION**: COULD improve (naming, readability, optional refactor).

---

## 6. Anti-Patterns to Detect

### Python

| Anti-Pattern | Correct Pattern |
|---|---|
| Bare `except:` | `except Exception as exc:` or specific type |
| Bare `print()` | `logging.getLogger(__name__).info(...)` |
| `Optional[X]` | `X \| None` (Python 3.12 PEP 604) |
| Module-level mutable globals | State in class instances |
| Silent error swallowing (`except: pass`) | Log + re-raise or return error string |
| `eval()` / `exec()` | NEVER — find a safe alternative |
| Nested conditionals > 2 levels | Early returns / guard clauses |
| Hardcoded secrets in source | Environment variables via `config.py` |
| `from module import *` | Explicit named imports |
| Mutable default args (`def f(x=[])`) | `def f(x=None): x = x or []` |
| SQL string concatenation | Parameterized queries only |
| Lines > 79 chars | Wrap using patterns in section 3.2 |
| Implicit string concat on long lines | Use f-strings or explicit `+` |

### TypeScript

| Anti-Pattern | Correct Pattern |
|---|---|
| `any` type | `unknown` + type narrowing |
| Raw `fetch()` calls | `apiFetch()` from `lib/apiFetch.ts` |
| `<img>` elements | `<Image />` from `next/image` |
| `innerHTML` assignment | Sanitized rendering or React JSX |
| Unused imports | Remove before commit |

### Architecture

| Anti-Pattern | Correct Pattern |
|---|---|
| Flat file reads for stock data | Iceberg via `_load_parquet()` / `StockRepository` |
| Duplicate repo singletons | Import from `_stock_shared.py` |
| Silencing Iceberg write failures | Let errors propagate to tool handler |
| Patching lazy imports on importing module | Patch at the SOURCE module |
| Creating new files when editing suffices | Edit existing files first |
| Premature abstraction / over-engineering | Minimum complexity for current task |
| Adding features/refactors not requested | Only change what is asked |

---

## 7. Security Constraints

### Secrets and credentials

- **NEVER** hardcode API keys, passwords, tokens, or connection strings in source.
- **NEVER** commit `.env` files, `credentials.json`, or private keys.
- All secrets MUST come from environment variables via `backend/config.py` (Pydantic Settings).
- Validate required secrets at startup — fail fast with a clear error.

### Input validation

- All user input MUST be validated at system boundaries (API endpoints, tool inputs).
- Use Pydantic models for request body validation.
- Sanitize ticker symbols: `ticker.upper().strip()`, reject non-alphanumeric (except `.`).
- Dashboard: `dashboard/utils.py:check_input_safety()` checks length, SQL injection patterns, XSS.

### OWASP Top 10 awareness

- **Injection**: Parameterized queries only. No string concatenation for SQL or shell commands.
- **Broken auth**: JWT with proper expiry. Refresh token deny-list (in-memory).
- **Sensitive data exposure**: Error messages MUST NOT reveal stack traces, file paths, or secrets to end users. Log full details server-side only.
- **XSS**: No `innerHTML`, no `dangerouslySetInnerHTML` without sanitization.
- **SSRF**: Validate URLs before fetching. No user-controlled URLs in server-side requests.

### Critical files (modify with extra care)

- `backend/.env` — symlink to secrets; never commit.
- `auth/password.py` — bcrypt hashing; changes can lock out users.
- `stocks/repository.py` — Iceberg schemas; breaking changes cause data loss.
- `hooks/*` — quality gates; disabling degrades the pipeline.

---

## 8. Review for Code Performance

### Database / Iceberg

- **Copy-on-write is expensive**: `table.overwrite()` reads + rewrites the full table. Batch updates; minimize calls.
- **N+1 queries**: Load all data in one call where possible, not one query per iteration.
- **Cache awareness**: `data/cache/` provides same-day caching. Clear only on refresh (`_clear_tool_cache()`).

### Python

- **Prefer vectorized pandas/numpy** over `iterrows()`. Use `iterrows()` only for dict-building or < 1000 rows.
- **Avoid blocking I/O in async paths**: Long-running tasks run in the agentic loop's thread, not in async handlers.
- **Memory**: Release large DataFrames after use. Use `.copy()` only when mutation isolation is needed.
- **Strings**: Use f-strings or `"".join()`, not repeated `+` in loops.

### Frontend

- **Minimize re-renders**: `React.memo`, `useMemo`, `useCallback` for stable objects/functions.
- **Lazy loading**: Heavy components via `dynamic()` imports.
- **Images**: Always `<Image />` from `next/image` (enforced by ESLint).

### Thresholds

| Metric | Target |
|--------|--------|
| API p95 (non-LLM) | < 500ms |
| LLM first token | < 2s |
| LLM full response | < 30s |
| Dashboard page load | < 3s |
| Test suite | < 30s (currently ~17s) |

---

## 9. Review Tone Guidance

### Principles

- **Be direct, not harsh.** State what needs to change and why.
- **Correctness over style.** Only flag style issues linters miss.
- **Acknowledge good work** briefly when a solution is elegant.
- **Explain the "why"** — not just "change X to Y".
- **One fix per comment.** Don't bundle unrelated feedback.

### Language precision (RFC 2119)

| Keyword | Meaning |
|---------|---------|
| **MUST** / **MUST NOT** | Hard requirement. PR cannot merge without this. |
| **SHOULD** / **SHOULD NOT** | Strong recommendation. Override only with reason. |
| **MAY** / **CONSIDER** | Optional suggestion. Author decides. |

### Comment format

```
[SEVERITY] Brief title

Explanation of the issue and why it matters.
Suggested fix (if not obvious).
```

### Avoid

- Passive-aggressive tone.
- Vague feedback ("This could be better").
- Bikeshedding unless it causes genuine confusion.
- Reviewing code not changed in the PR.
- Requesting changes based on personal preference.

---

## 10. Error Handling & Logging

### Logging standards

- Every module: `_logger = logging.getLogger(__name__)` at module level.
- Use appropriate levels: `DEBUG` (developer detail), `INFO` (normal flow), `WARNING` (recoverable issue), `ERROR` (failure requiring attention).
- Log messages MUST include context: `_logger.info("Fetched %d rows for %s", count, ticker)` — not `_logger.info("Done")`.
- NEVER log secrets, tokens, passwords, or full stack traces at INFO level. Use `DEBUG` or `exc_info=True` at `ERROR` level.

### Error handling patterns

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
repo = _require_repo()   # raises RuntimeError if unavailable
repo.insert_ohlcv(...)   # let exceptions propagate

# Non-critical pipeline steps — log and continue
try:
    info_msg = get_stock_info.invoke({"ticker": ticker})
    _record(result, "Company info", True, info_msg[:120])
except Exception as exc:
    _record(result, "Company info", False, str(exc)[:120])
```

### Error categories

| Category | Handling | Example |
|----------|----------|---------|
| User input error | Return 400/422 with clear message | Invalid ticker format |
| Auth error | Return 401/403 | Expired JWT, missing role |
| External service failure | Log WARNING, retry or degrade gracefully | yfinance rate limit |
| Data integrity error | Log ERROR, abort operation | Iceberg schema mismatch |
| Configuration error | Log CRITICAL, fail fast at startup | Missing `ANTHROPIC_API_KEY` |

---

## 11. Dependency Management

### Python (backend)

- **Virtualenv**: `backend/demoenv` (Python 3.12.9). Activate with `source backend/demoenv/bin/activate`.
- **Installing new packages**: `pip install <package>` inside the virtualenv, then update `requirements.txt` or `setup.sh` accordingly.
- **Version pinning**: Pin major versions in `setup.sh` for stability. Use `pip show <package>` to verify installed version.
- **Upgrade protocol**: Test in feature branch -> verify all 133+ tests pass -> verify lint clean -> PR to dev.
- **Key dependencies**: FastAPI, LangChain 1.x, PyIceberg 0.11, Prophet, yfinance, pandas, pyarrow, bcrypt 5.x.

### Frontend (Next.js)

- **Package manager**: npm. Lockfile: `frontend/package-lock.json`.
- **Adding packages**: `cd frontend && npm install <package>`. Commit the updated `package-lock.json`.
- **Upgrade protocol**: Same as Python — feature branch, tests pass, lint clean, PR.

### Rules

- NEVER install packages globally. Always use the project virtualenv or project-local `node_modules`.
- NEVER upgrade multiple major dependencies in the same PR. One major upgrade per PR for clean rollback.
- ALWAYS run the full test suite after dependency changes.
- Check for breaking changes in changelogs before upgrading.

---

## 12. Documentation Standards

### What MUST be updated

| Trigger | Update |
|---------|--------|
| Every session | `PROGRESS.md` — dated entry summarising changes |
| New/changed API endpoint | `docs/` — relevant API page |
| Architecture change | `CLAUDE.md` — Section 2 (Architecture Summary) |
| New config/env var | `README.md` — env vars table |
| New deployment step | `CLAUDE.md` — Appendix (Deployment) |
| New Iceberg table | `stocks/create_tables.py` + `docs/` |

### Docstring requirements

- **Every Python module**: top-of-file docstring describing the module's purpose.
- **Every class**: docstring with description and key attributes.
- **Every public method / `@tool` function**: Google-style Sphinx with `Args:`, `Returns:`, `Raises:` sections.
- **Private methods**: docstring recommended if logic is non-obvious.

### Docstring example

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

### Markdown style

- Use ATX headers (`#`, `##`, `###`). No more than 3 levels deep.
- Code blocks MUST have language tags (```python, ```bash, ```typescript).
- Tables for structured data. Bullet lists for sequences.

---

## 13. Debugging & Troubleshooting

### Common issues

| Problem | Cause | Fix |
|---------|-------|-----|
| `ModuleNotFoundError: tools` | `backend/` not on `sys.path` | Add `backend/` to `sys.path` (see Section 3.6) |
| `RuntimeError: StockRepository unavailable` | Iceberg catalog missing | Run `python stocks/create_tables.py` |
| yfinance returns empty DataFrame | Ticker invalid or rate-limited | Verify ticker on Yahoo Finance; wait and retry |
| `table.overwrite()` fails | Schema mismatch after column addition | Verify Arrow schema matches table definition |
| black + flake8 line length conflict | Missing `pyproject.toml` | Ensure `pyproject.toml` has `line-length = 79` |
| `isort` and `black` fight over imports | Missing `--profile black` | Always use `isort --profile black` |
| Pre-commit hook fails | `ANTHROPIC_API_KEY` not set | Export key or `SKIP_PRE_COMMIT=1` |
| Dashboard can't read `.env` | `_load_dotenv()` not called | Dashboard is a separate process; dotenv loads at import |
| JWT auth fails across services | Missing env propagation | `main.py` copies settings to `os.environ` at startup |
| Tests fail with `AttributeError: module has no attribute` | Patching lazy import on wrong module | Patch at the SOURCE module (see Section 3.6) |

### Debug logging

```bash
# Enable debug logging for a specific module
export LOG_LEVEL=DEBUG
python -c "
import logging; logging.basicConfig(level=logging.DEBUG)
from stocks.repository import StockRepository
repo = StockRepository()
print(repo.get_all_registry().keys())
"
```

### Iceberg data inspection

```python
from stocks.repository import StockRepository
repo = StockRepository()

# Check registry
print(sorted(repo.get_all_registry().keys()))

# Check OHLCV coverage
for t in sorted(repo.get_all_registry().keys()):
    df = repo.get_ohlcv(t)
    adj = df["adj_close"].notna().mean() * 100
    print(f"{t}: {len(df)} rows, {adj:.1f}% adj_close")
```

---

## Appendix: Deployment & Known Limitations

### First-time deployment

```bash
python auth/create_tables.py
python auth/migrate_users_table.py
python stocks/create_tables.py
python stocks/backfill_metadata.py
python stocks/backfill_adj_close.py
```

### Hooks (one-time install)

```bash
cp hooks/pre-commit .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
cp hooks/pre-push .git/hooks/pre-push && chmod +x .git/hooks/pre-push
```

Pre-commit: auto-fix + freshness checks (requires `ANTHROPIC_API_KEY`). Bypass: `SKIP_PRE_COMMIT=1`.
Pre-push: blocks on bare `print()` or `mkdocs build` failure.

### Known limitations

- Facebook SSO: code complete, credentials are placeholders (button hidden).
- `SERPAPI_API_KEY` required for `search_web` (100 free/month).
- Refresh token deny-list is in-memory (cleared on restart).
- Copy-on-write Iceberg upserts do not scale to very large tables (fine for current dataset sizes).

### After every session

Update `PROGRESS.md` (dated entry) + `CLAUDE.md` (if structure changed) + relevant `docs/` pages.
