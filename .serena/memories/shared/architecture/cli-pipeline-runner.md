# Pipeline Runner CLI — Full Command Reference

## New commands (Sprint 6)
| Command | Wraps | Args |
|---|---|---|
| `analytics` | `execute_compute_analytics()` | `--scope`, `--force` |
| `sentiment` | `execute_run_sentiment()` | `--scope`, `--force` |
| `forecast` | `execute_run_forecasts()` | `--scope`, `--force` |
| `indices` | `refresh_market_indices()` | none |
| `refresh` | Full pipeline chain | `--scope`, `--force`, `--skip-forecast` |

## New flags on existing commands
- `bulk`, `daily`, `fundamentals`: `--scope` + `--force`
- `quarterly`, `screen`, `fill-gaps`: `--scope`

## Force parameter
Added `force: bool = False` to all executor functions:
- `execute_data_refresh(force=)` → `batch_data_refresh(force=)` clears all freshness gates
- `execute_compute_analytics(force=)` — skips "analysed today" check
- `execute_run_sentiment(force=)` — skips "scored today" check
- `execute_run_forecasts(force=)` — skips 7-day freshness check

## CLI runs tracked
All CLI runs write to Iceberg `scheduler_runs` with `trigger_type="cli"` for visibility in Run History UI.

## Usage
```bash
PYTHONPATH=.:backend python -m backend.pipeline.runner refresh --scope india --force
PYTHONPATH=.:backend python -m backend.pipeline.runner analytics --scope india
PYTHONPATH=.:backend python -m backend.pipeline.runner sentiment --force
```
