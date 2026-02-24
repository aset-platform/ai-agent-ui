# Dashboard

The AI Stock Analysis Dashboard is a four-page interactive web app built with [Plotly Dash](https://dash.plotly.com/) and [dash-bootstrap-components](https://dash-bootstrap-components.opensource.faculty.ai/). It reads data directly from local parquet files — no API keys or backend server required.

---

## Running the Dashboard

```bash
# Start all services at once (recommended):
./run.sh start

# Dashboard only:
source backend/demoenv/bin/activate
python dashboard/app.py
```

Open [http://127.0.0.1:8050](http://127.0.0.1:8050) in your browser.

!!! note "No backend required"
    The dashboard reads `data/raw/*.parquet` and `data/forecasts/*.parquet` directly. The FastAPI backend does **not** need to be running. The only prerequisite is that stock data has been fetched at least once via the chat interface or the stock agent pipeline.

---

## Pages

### Home `/`

Stock overview cards auto-loaded from `data/metadata/stock_registry.json`.

Each card shows:

- **Ticker** and company name (from `data/metadata/{TICKER}_info.json`)
- **Current price** — last closing price from the raw parquet
- **10Y Return** — total return since the first available row
- **AI Sentiment** badge — Bullish / Neutral / Bearish derived from the latest Prophet forecast

A search box and dropdown let you jump directly to the Analysis page for any ticker. Cards refresh automatically every 5 minutes via a `dcc.Interval`.

---

### Analysis `/analysis`

Interactive 3-panel technical analysis chart.

**Controls:**

| Control | Options |
|---------|---------|
| Ticker dropdown | All tickers in the registry |
| Date range slider | 1M / 3M / 6M / 1Y / 3Y / Max |
| Overlay toggles | SMA 50, SMA 200, Bollinger Bands, Volume |

**Chart panels:**

| Panel | Height | Content |
|-------|--------|---------|
| Price | 60% | Candlestick + selected overlays; Volume on secondary y-axis (optional) |
| RSI | 20% | RSI (14) with overbought (70) / oversold (30) zones shaded |
| MACD | 20% | MACD line, signal line, histogram (green/red bars) |

**Summary stat cards** below the chart:

- All-Time High / All-Time Low
- Annualised Return
- Max Drawdown
- Annualised Volatility
- Sharpe Ratio (4% risk-free rate)

---

### Forecast `/forecast`

Prophet time-series forecast with price targets.

**Controls:**

| Control | Options |
|---------|---------|
| Ticker dropdown | All tickers in the registry |
| Forecast horizon | 3 Months / 6 Months / 9 Months |
| Run New Analysis | Button — triggers the full fetch → Prophet pipeline |

**Chart** shows:

- Historical closing price (blue line)
- 80% confidence interval (green band)
- Forecast line (dashed green)
- Today vertical marker
- Current-price horizontal reference line
- Price-target annotations at 3 / 6 / 9-month marks

**Price target cards** (3M / 6M / 9M):

- Forecast price
- % change from current price
- Confidence interval (lower – upper)

**Run New Analysis** button imports `backend.tools` directly (no HTTP) and runs:

1. `fetch_stock_data` — delta-fetch any missing OHLCV data from Yahoo Finance
2. `_prepare_data_for_prophet` → `_train_prophet_model` → `_generate_forecast` — train and generate the Prophet forecast
3. `_save_forecast` — persist result to `data/forecasts/{TICKER}_{N}m_forecast.parquet`
4. `_calculate_forecast_accuracy` — MAE, RMSE, MAPE via 12-month in-sample backtest

Model accuracy metrics appear below the chart after the run completes.

---

### Compare `/compare`

Side-by-side comparison of 2–5 stocks.

**Controls:**

| Control | Options |
|---------|---------|
| Multi-select dropdown | 2–5 tickers from the registry |

**Normalised performance chart** — all selected stocks rebased to 100 at their common start date. The best-performing stock is flagged with 🏆 in the metrics table.

**Metrics table** (one row per ticker):

| Column | Description |
|--------|-------------|
| Annual Ret | Annualised mean daily return |
| Volatility | Annualised standard deviation |
| Sharpe | Sharpe ratio (4% risk-free rate) |
| Max Drawdown | Peak-to-trough drawdown |
| RSI | Current RSI (14) |
| MACD | Bullish / Bearish signal |
| 6M Upside | Forecast % change over 6 months |
| Sentiment | Bullish / Neutral / Bearish |

**Returns correlation heatmap** — RdBu diverging colour scale, values annotated in each cell.

---

## Architecture

```
dashboard/
├── __init__.py       # Package init
├── app.py            # Dash app, FLATLY light theme, routing callback, gunicorn server attr
├── layouts.py        # Stateless page-layout factories + global NAVBAR
├── callbacks.py      # All interactive callbacks (register_callbacks factory)
└── assets/
    └── custom.css    # Light theme styles (gray-50 bg, white cards, indigo accent)
```

### Data flow

```
data/raw/{TICKER}_raw.parquet          ──► Analysis chart, Compare page
data/forecasts/{TICKER}_{N}m_forecast.parquet ──► Forecast chart, Home sentiment, Compare 6M upside
data/metadata/stock_registry.json     ──► Home cards, all dropdowns
data/metadata/{TICKER}_info.json      ──► Company name on Home cards
```

### Key design decisions

**Light theme (FLATLY)** — the dashboard uses `dbc.themes.FLATLY` (Bootstrap 5, light). `custom.css` defines a CSS-variable palette (`--bg: #f9fafb`, `--card-bg: #ffffff`, `--accent: #4f46e5`) that matches the chat interface. All Plotly charts use `template="plotly_white"` with explicit `paper_bgcolor`/`plot_bgcolor`/`gridcolor` values for consistency.

**Iframe embedding headers** — `app.py` registers a Flask `@server.after_request` hook that adds `X-Frame-Options: ALLOWALL` and `Content-Security-Policy: frame-ancestors *` to every response, allowing the dashboard to be embedded inside the Next.js SPA iframe from any origin.

**Direct parquet reads** — no HTTP call to the FastAPI backend. The dashboard can run standalone as long as parquet files exist. Fetching new data from Yahoo Finance requires the "Run New Analysis" button (or running the stock agent pipeline manually).

**`dcc.Store` for cross-page ticker** — the `nav-ticker-store` component carries the ticker selected on the Home page to the Analysis and Forecast dropdowns, enabling one-click navigation from a stock card.

**`suppress_callback_exceptions=True`** — required because the Analysis, Forecast, and Compare page components only exist in the DOM once their page is rendered. Dash would otherwise raise errors about callbacks referencing non-existent component IDs at startup.

**`allow_duplicate=True` on forecast-accuracy-row** — two callbacks write to `forecast-accuracy-row.children`: `update_forecast_chart` (placeholder text) and `run_new_analysis` (real MAE/RMSE/MAPE metrics). Dash requires `allow_duplicate=True` on the second callback's output.

---

## Deployment (gunicorn)

The `server` attribute in `app.py` exposes the underlying Flask WSGI object:

```bash
source backend/demoenv/bin/activate
gunicorn "dashboard.app:server" --bind 0.0.0.0:8050 --workers 2
```
