---
paths:
  - "backend/tools/*forecast*.py"
---

# Forecast pipeline rules (auto-loads when touching forecast code)

> Lazy-loaded via `paths:` frontmatter. Mirrors what was CLAUDE.md §5.10.
> Deep detail → Serena memory `forecast-enrichment-sanity-gates`.

- Vol regime: stable (<30%), moderate (30-60%), volatile (≥60%) — different Prophet config per regime.
- Log-transform for moderate/volatile (`np.log(y)` fit, `np.exp(yhat)` after — non-negative guarantee).
- Technical bias: RSI/MACD/volume dampen forecast ±15%, taper 30d.
- 5-component confidence score (direction/MASE/coverage/interval/completeness). <0.25 rejected.
- Sanity gates: log-transform exp cap (`np.exp(last_log_y ± 1.5)`, max 4.5×); >200% deviation → series skip.
- Run dedup: `computed_at` (UTC ts), NOT `run_date`.
- Backtest: `horizon_months=0`, actual in `lower_bound`. → `forecast-enrichment-sanity-gates`
