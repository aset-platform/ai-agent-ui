# LLM Portfolio Recommendation Engine (ASETPLTFRM-298)

## Status: Implemented + tested (Apr 12-13, 2026)

## Architecture: Smart Funnel (3 stages)

### Stage 1: DuckDB Pre-Filter (user-independent, 1h cache per scope)
- File: `backend/jobs/recommendation_engine.py`
- Single CTE query joins: piotroski_scores, analysis_summary, sentiment_scores, forecast_runs, ohlcv
- Hard gates: Piotroski >= 4, volume >= 10K, forecast within 30d, sentiment within 7d, MAPE < 80
- Market filtering: post-query filter via is_indian_market() based on scope param
- 6-factor composite score (0-100): Piotroski 25%, Sharpe 20%, momentum 15%, accuracy-adjusted forecast 20%, sentiment 10%, technical 10%
- Accuracy factor: `0.5*mape_f + 0.3*mae_f + 0.2*rmse_f`

### Stage 2: Portfolio Gap Analysis (per-user)
- Holdings enrichment: merge with candidates for sector/price; fallback to company_info DuckDB for holdings not in candidates (low Piotroski)
- Sector gaps vs universe distribution
- Nifty 50 index tracking (stock_tags PG table, async NullPool)
- Market cap distribution vs 60/25/15 benchmark
- Correlation alerts > 0.85 on holdings
- Gap-fill bonus: up to +20 points (sector 10, index 5, cap 5)
- Holdings filtered by market scope before analysis

### Stage 3: LLM Reasoning (Groq cascade, temp 0.3)
- Structured JSON prompt with portfolio summary + 40 candidates
- Validation: reject hallucinated tickers
- Falls back to deterministic top-5 when: LLM fails, JSON parse fails, OR all recs hallucinated
- Health score: base 70, penalties for concentration/correlation/low diversification, bonus for Nifty50 overlap
- ObservabilityCollector via get_obs_collector() singleton

## Unified Quota Gate
- `check_recommendation_quota(user_id, scope)` — single function for all routes
- Max 5 runs per user per rolling 30 days (all types combined: scheduled+manual+chat+cli)
- Only superusers bypass with force=true
- Uses async NullPool (safe in thread pool workers)
- Returns cached latest run when quota exceeded

## 4 Generation Routes (same pipeline)
| Route | run_type | Quota | Force Override |
|-------|----------|-------|----------------|
| Scheduler (executor.py) | scheduled | Yes | force flag |
| Dashboard Refresh (recommendation_routes.py) | manual | Yes (non-superuser) | Superuser bypasses |
| Chat Agent (recommendation_tools.py) | chat | Yes | force_refresh param |
| CLI (pipeline/runner.py) | cli | Yes | --force flag |

All use async NullPool for PG access (not session_factory — fails in thread pool workers).

## Database (3 PG tables in stocks schema)
- `recommendation_runs`: run_id, user_id, run_date, run_type, scope, portfolio_snapshot (JSONB), health_score/label/assessment, candidates_scanned/passed, llm_model/tokens, duration_secs
- `recommendations`: id, run_id, tier, category, ticker, action, severity, rationale, expected_impact, data_signals (JSONB), price_at_rec, target_price, expected_return_pct, index_tags, status, acted_on_date
- `recommendation_outcomes`: id, recommendation_id, check_date, days_elapsed, actual_price, return_pct, benchmark_return_pct, excess_return_pct, outcome_label

## Frontend
- Compact dashboard widget: HealthScoreBadge + top 3 preview rows + "View All N →"
- Centered modal (RecommendationSlideOver): full cards with expanded rationale, tier/severity filters
- Recommendation History tab: scope filter (All/India/US), time range (7D-1Y), pagination (10/page), scope+run_type badges, eye icon to view any run's recs in modal
- RecommendationCard: expanded prop for full rationale, View link opens in new tab

## Key Files
| File | Purpose |
|------|---------|
| `backend/jobs/recommendation_engine.py` | Smart Funnel stages 1-3 + quota check |
| `backend/db/models/recommendation.py` | 3 ORM models |
| `backend/tools/recommendation_tools.py` | Chat agent tools (3) |
| `backend/agents/configs/recommendation.py` | 6th LangGraph agent config |
| `backend/recommendation_routes.py` | 5 API endpoints |
| `backend/recommendation_models.py` | Pydantic response models |
| `backend/jobs/executor.py` | Scheduler job + outcome tracker |
| `backend/pipeline/runner.py` | CLI recommend command |
| `frontend/components/widgets/RecommendationsWidget.tsx` | Compact dashboard widget |
| `frontend/components/widgets/RecommendationSlideOver.tsx` | Centered modal |
| `frontend/components/insights/RecommendationHistoryTab.tsx` | History tab |

## Critical Gotchas
- asyncio.run() + session_factory fails in thread pool workers → use async NullPool
- get_portfolio_holdings() lacks sector/price → enrich from candidates + company_info fallback
- /{run_id} route must be registered AFTER /history and /stats (FastAPI path matching)
- cache.set(key, value, ttl) not cache.setex(); cache.invalidate(pattern) not cache.delete()
- Stage 1 SQL: analysis_summary uses analysis_date, ohlcv uses lowercase close/volume, piotroski uses total_score
- Groq TPD limits exhaust across recommendation runs → cascade to Qwen3-32B → Anthropic
- Hallucinated tickers (e.g. "SMALLCAP ETF") → deterministic fallback when all recs removed
