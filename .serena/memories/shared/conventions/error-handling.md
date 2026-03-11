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
