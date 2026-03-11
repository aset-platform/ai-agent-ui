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

## AI Tooling

- **Shared memories**: `.serena/memories/shared/` (git-tracked)
- **Session memories**: `.serena/memories/session/` (gitignored)
- **Personal memories**: `.serena/memories/personal/` (gitignored)
- **Skills**: `/promote-memory`, `/check-stale-memories`
- Use `/sc:save` at end of session to persist context
- Use `/promote-memory` to share reusable insights with team
