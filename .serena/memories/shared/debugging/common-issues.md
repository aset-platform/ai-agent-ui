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
| isort/black corrupt packages | Recurse into virtualenv site-packages | Exclusions in `pyproject.toml` (`extend-exclude`, `skip_glob`) |
| `CommitFailedException` in logs | Concurrent Iceberg writes (OCC) | `_retry_commit()` handles this; check max_workers |

## Iceberg Migration: Avro Manifest Absolute Paths

When migrating Iceberg warehouse to a new directory, JSON metadata
and SQLite catalog can be rewritten with string replace. But **avro
manifest files store absolute paths in binary format** — they cannot
be rewritten.

**Symptom**: After moving `data/iceberg/` and cleaning the old dir,
dashboard shows "No stocks saved yet" with `FileNotFoundError` in logs.

**Fix**: Create a symlink from old warehouse path to new location:
```bash
ln -s ~/.ai-agent-ui/data/iceberg data/iceberg
```
Old snapshots naturally expire as new writes create fresh manifests.

## Plotly & Dash Component Gotchas

- **Plotly annotations need `captureevents=True`** for `hovertext` to work
- **`dbc.Tooltip` silently fails** on duplicate DOM IDs — make IDs column-key-specific
- **Plotly minimum chart height** is 10px (not 1px) — causes ValueError
- **Empty chart**: Use full-size figure with annotation, not hidden figure
  (avoids Plotly.js rendering state issues)
- **Unicode math symbols** (≤ ≥) are safer than `<` `>` in tooltip text

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
