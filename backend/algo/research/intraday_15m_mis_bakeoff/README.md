# Intraday 15m MIS Bake-Off

Spec: [`docs/superpowers/specs/2026-05-21-intraday-15m-mis-research-design.md`](../../../../docs/superpowers/specs/2026-05-21-intraday-15m-mis-research-design.md)

## Modes

| Mode | Purpose | Runtime |
|---|---|---|
| `--smoke` | Synthetic 5K rows; CI-safe | < 30 s |
| `--dry-run` | 3 tickers, 2 weeks; real Iceberg | ~1 min |
| (default) | F&O 200, full window, 5 seeds | ~25 min |

## Happy path

```bash
# 1. Iceberg health check
docker compose exec backend python -c \
  "from backend.db.duckdb_engine import query_iceberg_df; \
   print(query_iceberg_df('stocks.intraday_features', \
       'SELECT MAX(bar_date) FROM intraday_features'))"

# 2. Tests
docker compose exec backend python -m pytest \
    backend/algo/research/intraday_15m_mis_bakeoff/

# 3. Dry-run
docker compose exec backend python -m \
    backend.algo.research.intraday_15m_mis_bakeoff.train --dry-run

# 4. Full run
docker compose exec backend python -m \
    backend.algo.research.intraday_15m_mis_bakeoff.train \
    --train-end 2026-02-28 \
    --threshold 0.5 \
    --seeds 42,43,44,45,46
```

## Output

`~/.ai-agent-ui/research_runs/<date>-intraday-15m-bakeoff/`:

- `report.md` — primary deliverable
- `feature_ranking.csv`
- `shap_long.png`, `shap_short.png`, `feature_ranking.png`
- `model.json`
- `run_metadata.json` — reproducibility ledger
- `run_summary.json`, `class_balance.csv`

## Failure-mode playbook

See spec §8.3.

## Refreshing the F&O universe

The static `fno_200.csv` is a one-time pull from `algo.instruments`. Refresh quarterly when NSE updates the F&O list — see plan Task 1 step 1 for the source query.
