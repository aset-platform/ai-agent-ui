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
