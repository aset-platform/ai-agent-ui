---
paths:
  - "backend/insights_routes.py"
  - "backend/insights_models.py"
  - "backend/insights/**"
  - "frontend/app/**/insights/**"
  - "frontend/components/insights/**"
---

# Insights rules (auto-loads when touching insights code)

## Ticker scoping (3-tier)

`insights_routes.py::_scoped_tickers(user, scope)`. Scope ∈
`{discovery, watchlist, portfolio}`:

| Tab → scope | Who sees what |
|---|---|
| `discovery` (Screener, ScreenQL, Sectors, Piotroski) | Pro/superuser: full universe (`stock`+`etf`); General: watchlist ∪ holdings |
| `watchlist` (Risk, Targets, Dividends) | Watchlist ∪ holdings |
| `portfolio` (Correlation, Quarterly) | Holdings only (`quantity > 0`) |

- Full-universe filter: `ticker_type IN ('stock', 'etf')`.
- Per-user cache key MUST include `user_id`.
