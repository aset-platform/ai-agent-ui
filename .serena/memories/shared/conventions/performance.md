# Performance Guidelines

## Thresholds

| Metric | Target |
|--------|--------|
| API p95 (non-LLM) | < 500ms |
| LLM first token | < 2s |
| LLM full response | < 30s |
| Dashboard page load | < 3s |
| Test suite | < 30s (currently ~17s) |

## Python

- Prefer vectorized pandas/numpy over `iterrows()`.
- Avoid blocking I/O in async paths.
- Release large DataFrames after use.
- Use f-strings or `"".join()`, not repeated `+` in loops.

## Frontend

- Minimize re-renders: `React.memo`, `useMemo`, `useCallback`.
- Lazy loading: Heavy components via `dynamic()` imports.
- Always `<Image />` from `next/image`.
