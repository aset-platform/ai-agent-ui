# Stock Analysis Agent

The Stock Analysis Agent is a specialist agent added in the Feb 23, 2026 session. It performs full financial analysis — fetching historical price data, calculating technical indicators, generating a 9-month Prophet forecast, and producing interactive Plotly charts — entirely driven by the LLM's agentic loop.

---

## How to Use

Select **Stock Analysis** in the agent toggle at the top of the chat UI, then type naturally:

| Example prompt | What happens |
|---|---|
| `analyse stock AAPL` | Full pipeline: fetch → technical analysis → 9-month forecast → charts |
| `forecast TSLA for next 6 months` | Fetches data + runs Prophet forecast for 6-month horizon |
| `stock report RELIANCE.NS` | Full report including INR prices for NSE-listed stocks |
| `compare AAPL and MSFT` | Runs full pipeline for both, side-by-side comparison table |
| `what stocks do you have data for?` | Lists the local data registry |
| `get dividend history for AAPL` | Fetches and stores dividend payment history |

---

## Architecture

The stock agent follows the same **Option B** pattern as every other agent in this project:

```
StockAgent(BaseAgent)
    └── _build_llm()  →  ChatGroq (or ChatAnthropic when switched)
    └── config.tool_names  →  8 @tool functions
    └── run()  (inherited)  →  agentic loop: LLM calls tools until done
```

The LLM drives the pipeline autonomously. No pattern matching or routing code exists — the system prompt instructs the model to always call `fetch_stock_data` first, then `analyse_stock_price`, then `forecast_stock`.

### Tools

| Tool | File | What it does |
|---|---|---|
| `fetch_stock_data` | `backend/tools/stock_data_tool.py` | Smart delta fetch from Yahoo Finance; saves to parquet |
| `get_stock_info` | `backend/tools/stock_data_tool.py` | Company name, sector, market cap, PE, 52w range |
| `load_stock_data` | `backend/tools/stock_data_tool.py` | Summarises locally stored parquet (no network call) |
| `fetch_multiple_stocks` | `backend/tools/stock_data_tool.py` | Batch fetch for comma-separated list of tickers |
| `get_dividend_history` | `backend/tools/stock_data_tool.py` | Full dividend history saved to `data/processed/` |
| `list_available_stocks` | `backend/tools/stock_data_tool.py` | Reads `stock_registry.json` and prints a table |
| `analyse_stock_price` | `backend/tools/price_analysis_tool.py` | Technical indicators + 3-panel Plotly chart |
| `forecast_stock` | `backend/tools/forecasting_tool.py` | Prophet forecast + confidence chart |

---

## Data Storage

All data is persisted locally at the project root under `data/`:

```
data/
├── raw/                          ← OHLCV parquet files (gitignored)
│   ├── AAPL_raw.parquet
│   ├── TSLA_raw.parquet
│   └── ...
├── processed/                    ← Dividend history parquet (gitignored)
├── forecasts/                    ← Prophet forecast parquet (gitignored)
│   ├── AAPL_9m_forecast.parquet
│   └── ...
└── metadata/                     ← Tracked by git
    ├── stock_registry.json       ← Fetch registry (ticker, date range, row count)
    └── AAPL_info.json            ← Company metadata cache (refreshed daily)
```

Charts are saved to:

```
charts/
├── analysis/    ← {TICKER}_analysis.html  (candlestick + volume + RSI)
└── forecasts/   ← {TICKER}_forecast.html  (price + confidence band)
```

---

## Delta Fetching

Every call to `fetch_stock_data` is idempotent and bandwidth-efficient:

```
First call for a ticker
  └── Full 10-year OHLCV fetch from Yahoo Finance
  └── Saved to data/raw/{TICKER}_raw.parquet
  └── Registry entry created in stock_registry.json

Subsequent call (same day)
  └── Registry shows last_fetch_date == today
  └── Fetch skipped: "Data is already up to date for AAPL"

Subsequent call (next day or later)
  └── Only the missing date range is fetched (delta)
  └── New rows appended; duplicates removed; sorted ascending
  └── Registry updated with new last_fetch_date and total_rows
```

This means a stock analysed yesterday costs a single small API call today rather than a full 10-year re-download.

---

## Forecast Methodology

The forecasting tool uses **Meta Prophet** (open source, installed as `prophet`):

| Setting | Value |
|---|---|
| Yearly seasonality | Enabled |
| Weekly seasonality | Enabled |
| Daily seasonality | Disabled |
| Holidays | US federal holidays via `holidays` package |
| Confidence interval | 80% |
| Price series | Adjusted Close (`Adj Close`) |
| Forecast horizon | Configurable (default 9 months) |

**Accuracy** is evaluated by in-sample backtesting over the last 12 months:

- **MAE** — Mean Absolute Error (same units as price)
- **RMSE** — Root Mean Square Error
- **MAPE** — Mean Absolute Percentage Error (%), lower is better

**Sentiment** is determined by the 9-month (or longest available) forecast:

| Condition | Sentiment |
|---|---|
| Forecast > current price by > 10% | 🟢 Bullish |
| Forecast < current price by > 10% | 🔴 Bearish |
| Within ±10% | 🟡 Neutral |

---

## Technical Indicators

Calculated by the `ta` library on top of the stored OHLCV data:

| Indicator | Period | Column name |
|---|---|---|
| Simple Moving Average | 50-day | `SMA_50` |
| Simple Moving Average | 200-day | `SMA_200` |
| Exponential Moving Average | 20-day | `EMA_20` |
| Relative Strength Index | 14-day | `RSI_14` |
| MACD line | — | `MACD` |
| MACD Signal line | — | `MACD_Signal` |
| MACD Histogram | — | `MACD_Hist` |
| Bollinger Bands | 20-day, 2σ | `BB_Upper`, `BB_Middle`, `BB_Lower` |
| Average True Range | 14-day | `ATR_14` |

**RSI signals:** Overbought ≥ 70 · Neutral 30–70 · Oversold ≤ 30

**Sharpe ratio** calculated with a 4% risk-free rate assumption.

---

## Switching to Claude Sonnet 4.6

The stock agent uses the same 2-line swap as the general agent:

```python
# backend/agents/stock_agent.py

# Line 1 — change import
from langchain_anthropic import ChatAnthropic

# Line 2 — change return in StockAgent._build_llm()
return ChatAnthropic(model=self.config.model, temperature=self.config.temperature)
```

Also update `model` in `create_stock_agent()` to `"claude-sonnet-4-6"` and set `ANTHROPIC_API_KEY`.
