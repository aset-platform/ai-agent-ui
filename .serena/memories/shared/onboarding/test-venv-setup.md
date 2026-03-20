# Test Virtual Environment Setup

## Problem
Running `python -m pytest` uses the system Python (conda base, 3.9) instead of the project's Python 3.12 venv. Tests fail with `TypeError: unsupported operand type(s) for |` because PEP 604 (`X | None`) requires Python 3.10+.

## Root Cause
- Conda initializes in `~/.zshrc` and activates the `base` environment (Python 3.9) by default
- `/opt/anaconda3/bin/python` takes precedence in `$PATH`
- The project venv at `backend/demoenv` (Python 3.12.9) is not activated

## Solution

### Symlink (one-time setup)
```bash
ln -s /path/to/ai-agent-ui/backend/demoenv ~/.ai-agent-ui/venv
```

### Running tests
```bash
# Always activate venv first
source ~/.ai-agent-ui/venv/bin/activate
python --version   # Should show 3.12.9

# Backend tests
python -m pytest tests/backend/ -v

# Frontend tests
cd frontend && npx vitest run
```

### Verify correct Python
```bash
which python     # Should be .../demoenv/bin/python, NOT /opt/anaconda3/bin/python
which pytest     # Should be .../demoenv/bin/pytest (9.0.2), NOT conda's (7.1.2)
```

## Why `backend/demoenv` Exists
- The primary venv path `~/.ai-agent-ui/venv` was defined in docs but never created on this machine
- `backend/demoenv` is the actual Python 3.12 venv with all project dependencies
- `run.sh` and git hooks have fallback logic: check `~/.ai-agent-ui/venv` first, then `backend/demoenv`
- The symlink makes both paths work

## CI Pipeline
GitHub Actions CI (`ci.yml`) creates a fresh `.venv` with Python 3.12 — it doesn't rely on `backend/demoenv` or the symlink. CI is unaffected by this local setup issue.
