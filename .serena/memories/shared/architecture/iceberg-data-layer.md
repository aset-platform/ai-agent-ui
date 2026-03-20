# Iceberg Data Layer

## Catalog
- Type: SqlCatalog (SQLite-backed)
- Namespace: `stocks`
- Location: `~/.ai-agent-ui/warehouse/`
- Repository: `stocks/repository.py` — `StockRepository` class

## Tables (13)

### Stock Data (9)
| Table | Partition | Description |
|-------|-----------|-------------|
| `stocks.registry` | none | 1 row per ticker — fetch metadata |
| `stocks.company_info` | none | Append-only company fundamentals snapshots |
| `stocks.ohlcv` | ticker | Daily OHLCV price data (partitioned) |
| `stocks.dividends` | none | Dividend history |
| `stocks.technical_indicators` | ticker | Pre-computed SMA, RSI, MACD, BB (partitioned) |
| `stocks.analysis_summary` | none | Daily analysis snapshots (signals text only, not numeric) |
| `stocks.forecast_runs` | none | Prophet forecast metadata + 3/6/9m targets |
| `stocks.forecasts` | ticker, horizon | Full forecast time series with confidence bands |
| `stocks.quarterly_results` | none | Quarterly financial statements |

### LLM Observability (2)
| Table | Partition | Description |
|-------|-----------|-------------|
| `stocks.llm_pricing` | provider | Model pricing rate card |
| `stocks.llm_usage` | request_date | Per-request event log with cost |

### Auth (via auth/repo)
| Table | Description |
|-------|-------------|
| `auth.users` | User accounts |
| `auth.audit_log` | Auth events |
| `auth.user_tickers` | User-ticker links |

### Chat Audit (1, new)
| Table | Partition | Description |
|-------|-----------|-------------|
| `stocks.chat_audit_log` | user_id | Chat session transcripts (flushed on logout) |

## Key Gotcha: analysis_summary vs technical_indicators
- `analysis_summary` stores signal TEXT only (Bullish/Bearish/Neutral/Below/Above)
- Numeric indicator values (RSI=46.3, MACD=-12.6) live in `technical_indicators`
- Dashboard routes must fetch from BOTH tables to show name + value + signal

## Query Patterns
- Predicate push-down: `EqualTo("ticker", ticker)` for partitioned tables
- Fallback: full scan + pandas filter if predicate fails
- Dirty table tracking: `_dirty_tables` set, refresh after writes
- Retry on CommitFailedException: 3 retries with exponential backoff
