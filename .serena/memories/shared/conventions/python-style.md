# Python Style & Conventions

## Hard Rules

- **Line length: 79 chars** — black, isort, flake8 all aligned via
  `pyproject.toml` + `.flake8`.
- **Python 3.12** — use `X | None` not `Optional[X]` (PEP 604).
- **No bare `print()`** — use `logging.getLogger(__name__)`.
- **Docstrings** — Google-style Sphinx on every module, class,
  public method.
- **No bare `except:`** — always `except Exception` or specific.
- **No module-level mutable globals** — all state in class instances.

## Line-Wrapping Patterns

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

## black Gotchas

- black WILL NOT wrap docstrings or comments — keep <= 79 manually.
- black merges implicit string concat onto one line — use f-strings
  or explicit `+`.
- `# fmt: off` / `# fmt: on` — last resort only.

## OOP Conventions

- New agents: subclass `BaseAgent`, override only `_build_llm()`.
- New tools: `@tool`-decorated, registered via
  `ToolRegistry.register()` in `ChatServer._register_tools()`.
- New HTTP bodies: Pydantic models in `main.py`.

## Lint Commands

```bash
black backend/ auth/ stocks/ scripts/ dashboard/
isort backend/ auth/ stocks/ scripts/ dashboard/ --profile black
flake8 backend/ auth/ stocks/ scripts/ dashboard/
```

Workflow: Write code (<=79 chars) -> black + isort -> flake8 ->
git add -> commit -> push. NEVER push with lint errors.

## Anti-Patterns

| Anti-Pattern | Correct Pattern |
|---|---|
| Bare `except:` | `except Exception as exc:` or specific |
| Bare `print()` | `logging.getLogger(__name__).info(...)` |
| `Optional[X]` | `X \| None` (PEP 604) |
| Module-level mutable globals | State in class instances |
| Silent error swallowing | Log + re-raise or return error string |
| `eval()` / `exec()` | NEVER — find a safe alternative |
| Nested conditionals > 2 levels | Early returns / guard clauses |
| Hardcoded secrets | Environment variables via `config.py` |
| `from module import *` | Explicit named imports |
| Mutable default args `def f(x=[])` | `def f(x=None): x = x or []` |
| SQL string concatenation | Parameterized queries only |
| Lines > 79 chars | Wrap using patterns above |
| Implicit string concat | Use f-strings or explicit `+` |

## Docstring Format

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
