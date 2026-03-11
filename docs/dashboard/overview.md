# Dashboard

The AI Stock Analysis Dashboard is a multi-page interactive web app built with [Plotly Dash](https://dash.plotly.com/) and [dash-bootstrap-components](https://dash-bootstrap-components.opensource.faculty.ai/). All stock data is read from Iceberg (the single source of truth) via TTL-cached helpers — no API keys or backend server required.

---

## Running the Dashboard

```bash
# Start all services at once (recommended):
./run.sh start

# Dashboard only:
source ~/.ai-agent-ui/venv/bin/activate
python dashboard/app.py
```

Open [http://127.0.0.1:8050](http://127.0.0.1:8050) in your browser.

!!! note "No backend required"
    The dashboard reads all stock data from Iceberg tables (single source of truth). The FastAPI backend does **not** need to be running. The only prerequisite is that stock data has been fetched at least once via the chat interface or the stock agent pipeline.

---

## Pages

### Home `/`

Stock overview cards loaded from Iceberg via batch pre-fetch.

Each card shows:

- **Ticker** and company name (from Iceberg `stocks.company_info`)
- **Current price** — last closing price from Iceberg OHLCV data
- **10Y Return** — total return since the first available row
- **AI Sentiment** badge — Bullish / Neutral / Bearish derived from the latest Prophet forecast run
- **Per-card refresh button** — triggers a background `run_full_refresh()` for that ticker

A search box and dropdown let you jump directly to the Analysis page for any ticker. Cards refresh automatically every 30 minutes via a `dcc.Interval`.

#### Performance

The `refresh_stock_cards` callback uses **batch pre-fetch** to avoid per-ticker Iceberg scans:

| Step | Calls | Cached? |
|------|-------|---------|
| `_get_registry_cached()` | 1 | 5-min TTL |
| `_get_company_info_cached()` | 1 | 5-min TTL |
| `_get_forecast_runs_cached()` | 1 | 5-min TTL |
| `_get_ohlcv_cached()` per ticker | N | 5-min TTL |

Before the per-ticker loop, two batch Iceberg reads build `company_map`, `currency_map`, and `sentiment_map` dicts. The loop body uses pure dict lookups — no Iceberg calls. Cold load: ~500 ms; warm cache: ~50 ms.

#### Market filter

Two toggle buttons above the cards filter by market:

| Button | Tickers shown |
|--------|--------------|
| 🇮🇳 India | Tickers ending in `.NS` (NSE) or `.BO` (BSE) |
| 🇺🇸 US | All other tickers |

The filter defaults to **India**. Switching markets resets the page to 1.

#### Pagination

Cards are paginated at **12 per page** (configurable via a page-size dropdown: 10 / 25 / 50 / 100). A count label ("Showing 1–12 of 47") is displayed to the left of the pagination control. The pagination row is positioned above the fixed navigation FAB to avoid overlap.

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
3. `_save_forecast` — persist result to Iceberg + backup parquet at `~/.ai-agent-ui/data/forecasts/`
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

### Marketplace `/marketplace`

Browse all available tickers from the central registry and manage your watchlist. Each row shows the ticker symbol, company name, market, and last update date. Use the **Add** button to link a ticker to your account or **Remove** to unlink it. A search bar filters the table in real time.

Tickers you add here (or via the chat server) appear on your Home page as stock cards.

---

## Architecture

```
dashboard/
├── __init__.py          # Package init
├── app.py               # Dash app, FLATLY light theme, gunicorn server attr
├── app_layout.py        # Root layout + display_page routing callback
├── layouts/             # Stateless page-layout factories (package)
│   ├── home.py          # Home cards + market filter + pagination
│   ├── analysis.py      # Technical analysis chart layout + ticker cache
│   ├── insights_tabs.py # Screener/Targets/Dividends/Risk/Sectors/Correlation tabs
│   ├── admin.py         # User management + audit log layout
│   └── navbar.py        # Global navbar
├── callbacks/           # Interactive callbacks (package)
│   ├── data_loaders.py  # Iceberg reads, TTL indicator cache
│   ├── chart_builders.py # Plotly figure construction
│   ├── home_cbs.py      # Home page callbacks (batch pre-fetch)
│   ├── analysis_cbs.py  # Analysis + Compare callbacks
│   ├── insights_cbs.py  # All Insights tab callbacks
│   ├── admin_cbs.py     # User table callbacks
│   ├── admin_cbs2.py    # Add/Edit/Deactivate user modals
│   ├── iceberg.py       # Iceberg repo singleton + TTL cached helpers
│   └── utils.py         # Shared utilities (currency, market label)
└── assets/
    └── custom.css       # Light theme styles (gray-50 bg, white cards, indigo accent)
```

### Data flow

All data reads go through Iceberg (single source of truth) via TTL-cached helpers in `iceberg.py`:

```
stocks.registry          ──► Home cards, all dropdowns     (_get_registry_cached, 5-min TTL)
stocks.company_info      ──► Company names, currency       (_get_company_info_cached, 5-min TTL)
stocks.ohlcv             ──► Analysis chart, Compare page  (_get_ohlcv_cached, 5-min TTL)
stocks.forecast_runs     ──► Home sentiment, Compare 6M    (_get_forecast_runs_cached, 5-min TTL)
stocks.forecasts         ──► Forecast chart                (_get_forecast_cached, 5-min TTL)
stocks.analysis_summary  ──► Insights pages                (_get_analysis_summary_cached, 5-min TTL)
stocks.dividends         ──► Dividend history              (_get_dividends_cached, 5-min TTL)
```

### Key design decisions

**Light theme (FLATLY)** — the dashboard uses `dbc.themes.FLATLY` (Bootstrap 5, light). `custom.css` defines a CSS-variable palette (`--bg: #f9fafb`, `--card-bg: #ffffff`, `--accent: #4f46e5`) that matches the chat interface. All Plotly charts use `template="plotly_white"` with explicit `paper_bgcolor`/`plot_bgcolor`/`gridcolor` values for consistency.

**Iframe embedding headers** — `app.py` registers a Flask `@server.after_request` hook that adds `X-Frame-Options: ALLOWALL` and `Content-Security-Policy: frame-ancestors *` to every response, allowing the dashboard to be embedded inside the Next.js SPA iframe from any origin.

**Iceberg reads with TTL caching** — no HTTP call to the FastAPI backend. The dashboard reads from Iceberg tables via cached helpers (5-min TTL). Fetching new data from Yahoo Finance requires the "Run New Analysis" button, a per-card refresh, or running the stock agent pipeline manually.

**`dcc.Store` for cross-page ticker** — the `nav-ticker-store` component carries the ticker selected on the Home page to the Analysis and Forecast dropdowns, enabling one-click navigation from a stock card.

**`suppress_callback_exceptions=True`** — required because the Analysis, Forecast, and Compare page components only exist in the DOM once their page is rendered. Dash would otherwise raise errors about callbacks referencing non-existent component IDs at startup.

**`allow_duplicate=True` on forecast-accuracy-row** — two callbacks write to `forecast-accuracy-row.children`: `update_forecast_chart` (placeholder text) and `run_new_analysis` (real MAE/RMSE/MAPE metrics). Dash requires `allow_duplicate=True` on the second callback's output.

**Data/render split for Home cards** — `refresh_stock_cards` uses batch pre-fetch (2 Iceberg scans for company info + forecast runs) to build `company_map`, `currency_map`, and `sentiment_map` dicts before the per-ticker loop. The loop body uses pure dict lookups and stores raw serialisable dicts in a `dcc.Store`. A separate `render_home_cards` callback reads the store, filters by market, and paginates — making the filter and page controls fully client-side without re-fetching data.

**`allow_duplicate=True` on `home-pagination.active_page`** — both `update_market_filter` (resets to 1 on market switch) and `reset_home_page_on_size_change` (resets to 1 on page-size change) write to this output. Dash requires `allow_duplicate=True` on the second callback.

**`paddingBottom: "5rem"` on `#page-content`** — the Next.js SPA renders a fixed FAB in the bottom-right corner of the browser viewport at `bottom-6 right-6 z-50`. Adding 80 px of bottom padding to the Dash page container keeps pagination controls visible and clickable above both the FAB and the Plotly watermark.

---

## Authentication

The dashboard requires a valid JWT on every page load. The token is passed from the Next.js frontend via a `?token=<jwt>` query parameter and persisted in a `dcc.Store` so it survives in-dashboard navigation.

### Token flow

```
Next.js iframeSrc
    │  appends ?token=<access_token>
    ▼
Dash URL bar (?token=eyJ...)
    │
    ├── store_token_from_url callback
    │       extracts ?token= → writes to auth-token-store (localStorage)
    │
    └── display_page callback
            reads token from URL param (preferred) or auth-token-store
            calls _validate_token(token)
                ├── valid   → render requested page
                └── invalid → _unauth_notice() ("Authentication required" screen)
```

### Page-level access control

| Route | Access |
|---|---|
| `/`, `/analysis`, `/forecast`, `/compare` | Any authenticated user |
| `/admin/users` | Superuser only — others see `_admin_forbidden()` notice |

### Admin page — `/admin/users`

Accessible from the **Admin** link in the NAVBAR (only rendered for superusers).

**Users tab:**

| Feature | Description |
|---|---|
| Search input | Debounced text filter — matches name, email, or role |
| User table | Paginated (10 / page, configurable) with role badge and status badge |
| Add User button | Opens modal → `POST /users` |
| Edit button | Per-row modal pre-filled with user data → `PATCH /users/{id}` |
| Deactivate / Reactivate | Per-row toggle → `DELETE /users/{id}` (deactivate) or `PATCH` with `is_active: true` (reactivate) |
| Page-size dropdown | Choose 10 / 25 / 50 / 100 rows per page |

**Audit Log tab:**

| Feature | Description |
|---|---|
| Search input | Debounced text filter — matches event type, actor, target, or metadata |
| Audit table | Paginated (10 / page, configurable): timestamp, event type, actor, target, metadata JSON |
| Page-size dropdown | Choose 10 / 25 / 50 / 100 rows per page |

Entries are sorted newest-first.

### Change Password modal

Available from the **Change Password** button in the NAVBAR on any page. Two-step flow:

1. `POST /auth/password-reset/request` with `{ email }` → returns a single-use reset token.
2. `POST /auth/password-reset/confirm` with `{ reset_token, new_password }` → applies the new password.

Password must be ≥ 8 characters and contain at least one digit. The modal shows inline error messages for validation failures and API errors.

### `_api_call` helper

All admin callbacks use `_api_call(method, path, token, json_body)` to make authenticated HTTP requests to the FastAPI backend:

```python
def _api_call(method, path, token, json_body=None):
    url = f"{BACKEND_URL}{path}"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.request(method, url, json=json_body, headers=headers, timeout=10)
    return resp
```

`BACKEND_URL` defaults to `http://127.0.0.1:8181` and can be overridden via the `BACKEND_URL` environment variable.

### Environment loading

`dashboard/app.py` includes a `_load_dotenv()` helper that reads `backend/.env` into `os.environ` at module import time (before any callbacks run). This ensures `JWT_SECRET_KEY` is available to `_validate_token()` even when the dashboard process was started without the variable explicitly exported in the shell.

---

## Deployment (gunicorn)

The `server` attribute in `app.py` exposes the underlying Flask WSGI object:

```bash
source ~/.ai-agent-ui/venv/bin/activate
gunicorn "dashboard.app:server" --bind 0.0.0.0:8050 --workers 2
```
