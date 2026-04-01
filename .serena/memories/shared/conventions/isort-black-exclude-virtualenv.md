# isort/black: Exclude virtualenv directories

## Problem
Running `isort backend/` or `black backend/` recurses into `backend/demoenv/lib/python3.12/site-packages/` and rewrites import orders in installed packages, causing circular import errors.

## Symptoms
```
ImportError: cannot import name '__diag__' from partially
initialized module 'pyparsing' (most likely due to a circular import)
```
Same pattern for pandas, yfinance, anthropic, groq.

## Fix
In `pyproject.toml`:
```toml
[tool.black]
extend-exclude = "backend/demoenv"

[tool.isort]
skip_glob = ["backend/demoenv/*"]
```
flake8 in `.flake8`: `exclude = backend/demoenv`

## Recovery
```bash
pip install --force-reinstall <package>==<version>
```

## Prevention
- Never run `isort` or `black` without the config exclusion
- The `pyproject.toml` config handles this automatically
- If running manually, use explicit dirs: `isort backend/agents/ backend/tools/`
