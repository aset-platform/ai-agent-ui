# Project Tooling Reference

## Python Virtualenv
- Location: `~/.ai-agent-ui/venv` (Python 3.12)
- Backwards compat symlink: `backend/demoenv`
- Activate: `source ~/.ai-agent-ui/venv/bin/activate`

## Lint Commands
```bash
python -m black backend/ auth/ stocks/ scripts/
python -m isort backend/ auth/ stocks/ scripts/ --profile black
python -m flake8 backend/ auth/ stocks/ scripts/
cd frontend && npx eslint . --fix
```

## Test Commands
```bash
python -m pytest tests/ -v              # backend (85+ tests)
cd frontend && npx vitest run           # frontend (18 tests)
cd e2e && npx playwright test --ui      # E2E (49 tests, needs live services)
```

## Demo Credentials
- Admin: `admin@demo.local` / `Admin123!`
- Test user: `test@demo.com` / `Test1234!`

## GitHub CLI
- Install: `brew install gh` (macOS) or `apt install gh` (Linux)
- Auth: `gh auth login` (required before PR creation)

## E2E Testing
- Run: `cd e2e && npx playwright test --ui`
- Output: `/tmp/e2e-test-results`
- Requires all services running (`./run.sh start`)

## Services (4)
```bash
./run.sh start    # redis, backend, frontend, docs
./run.sh stop     # stop all
./run.sh status   # health check table
./run.sh logs     # view logs
```
