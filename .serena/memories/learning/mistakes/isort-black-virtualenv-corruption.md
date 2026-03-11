# Bug: isort/black corrupt virtualenv packages

## Root Cause
Running `isort backend/` or `black backend/` recurses into
`backend/demoenv/lib/python3.12/site-packages/` and rewrites
import orders in installed packages (pyparsing, pandas, yfinance,
anthropic, groq, etc.), causing circular import errors.

## Symptoms
```
ImportError: cannot import name '__diag__' from partially
initialized module 'pyparsing' (most likely due to a circular import)
```
Same pattern for pandas (`ArrowDtype`), yfinance (`Ticker`),
anthropic (`is_mapping_t`).

## Fix (applied Mar 7, 2026)
Added exclusions in `pyproject.toml`:
```toml
[tool.black]
extend-exclude = "backend/demoenv"

[tool.isort]
skip_glob = ["backend/demoenv/*"]
```
flake8 already had `exclude = backend/demoenv` in `.flake8`.

## Recovery when it happens
```bash
pip install --force-reinstall <package>==<version>
```
Check `backend/requirements.txt` for pinned versions.

## Prevention
- NEVER run `isort` or `black` without the config exclusion
- The `pyproject.toml` config handles this automatically now
- If running manually, use explicit dirs:
  `isort backend/agents/ backend/tools/ backend/config.py ...`
  instead of `isort backend/`
