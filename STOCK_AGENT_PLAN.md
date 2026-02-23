# AI Agent UI — Stock Analysis Agent Build Plan
# Feed this file to Claude Code prompt by prompt in order
# Project: ai-agent-ui | GitHub: asequitytrading-design/ai-agent-ui

---

## HOW TO USE THIS FILE

1. Open your project in VS Code
2. Open integrated terminal
3. Run: `claude`
4. Copy and paste each prompt in order
5. Wait for Claude Code to complete before moving to next prompt
6. If errors occur, share the error and ask Claude Code to fix before proceeding

---

## PHASE 1: SETUP & DEPENDENCIES

---

### PROMPT 1 — Install Required Libraries

```
Install these Python libraries for the stock analysis agent:
- yfinance (Yahoo Finance data)
- pandas (data manipulation)
- numpy (numerical analysis)
- scikit-learn (machine learning forecasting)
- prophet (Meta's time series forecasting)
- plotly (interactive charts)
- ta (technical analysis indicators)
- pyarrow (parquet file read/write)
- dash (web dashboard framework)
- dash-bootstrap-components (dashboard styling)

Install all using pip and verify each one after installation.
Show me the version of each installed library.
```

---

### PROMPT 2 — Setup Folder Structure with Data Storage

```
Inside the existing project create this new folder structure 
WITHOUT touching any existing files, agents or tools:

src/agents/stock_agent.py        ← new stock agent
src/tools/yahoo_finance.py       ← data fetcher
src/tools/price_analysis.py      ← price analyser
src/tools/forecasting.py         ← forecasting model
src/models/stock_data.py         ← data models

data/                            ← all stock data lives here
data/raw/                        ← raw OHLCV parquet files
data/processed/                  ← cleaned and enriched data
data/forecasts/                  ← saved forecast results
data/metadata/                   ← stock metadata and last fetch dates

charts/                          ← generated HTML charts
charts/analysis/                 ← price analysis charts
charts/forecasts/                ← forecast charts
charts/dashboard/                ← dashboard assets

logs/                            ← agent activity logs
logs/stock_agent.log

dashboard/                       ← web dashboard
dashboard/app.py
dashboard/layouts.py
dashboard/callbacks.py
dashboard/assets/custom.css

Create a data/metadata/stock_registry.json file to track:
- Ticker symbol
- Last fetch date
- Total rows stored
- Date range available
- File path to parquet file

Initialize stock_registry.json as an empty JSON object: {}

IMPORTANT: Do not modify, move or delete any existing 
files or folders in the project. Only add new ones.
```

---

## PHASE 2: DATA FETCHING TOOL

---

### PROMPT 3 — Build Smart Delta Fetcher with Parquet Storage

```
Create src/tools/yahoo_finance.py with intelligent 
delta fetching and parquet storage. This file must be 
completely self contained and must NOT import from 
or modify any existing project files.

Build these functions:

1. check_existing_data(ticker)
   - Check data/metadata/stock_registry.json 
   - Return last fetch date and parquet file path if exists
   - Return None if no data exists for this ticker

2. fetch_stock_data(ticker, period="10y")
   - First call check_existing_data(ticker)
   
   IF no existing data found:
     - Fetch full 10 years of OHLCV data from Yahoo Finance
     - Include: Open, High, Low, Close, Adj Close, Volume
     - Save to data/raw/{ticker}_raw.parquet using pyarrow engine
     - Update data/metadata/stock_registry.json with:
       * ticker, last_fetch_date, total_rows, date_range, file_path
     - Log: "Full fetch completed for {ticker}: {rows} rows saved"
   
   IF existing data found:
     - Calculate delta: from last_fetch_date to today
     - If delta is 0 days: skip fetch, log "Data is up to date for {ticker}"
     - If delta exists:
       * Fetch only the missing date range (delta only)
       * Load existing parquet file
       * Append new rows using pandas concat
       * Remove any duplicate dates
       * Sort by date ascending
       * Save back to same parquet file using pyarrow engine
       * Update stock_registry.json with new last_fetch_date and total_rows
       * Log: "Delta fetch for {ticker}: {new_rows} new rows added"
   
   Always return complete DataFrame loaded from parquet file

3. load_stock_data(ticker)
   - Load existing parquet file for ticker from data/raw/
   - Return DataFrame or None if not found

4. fetch_multiple_stocks(tickers_list, period="10y")
   - Loop through tickers list
   - Call fetch_stock_data() for each (handles delta automatically)
   - Return dictionary of DataFrames keyed by ticker
   - Log summary: how many full fetches, delta fetches, skipped

5. get_stock_info(ticker)
   - Fetch company name, sector, market cap, PE ratio, 52w high/low
   - Cache result in data/metadata/{ticker}_info.json
   - Return as clean dictionary

6. get_dividends_history(ticker)
   - Fetch full dividend history using yfinance
   - Save to data/processed/{ticker}_dividends.parquet
   - Return as DataFrame

7. list_available_stocks()
   - Read stock_registry.json
   - Return list of all tickers with their data summary
   - Print a clean table to terminal

Parquet file naming convention:
  data/raw/AAPL_raw.parquet
  data/raw/TSLA_raw.parquet
  data/raw/RELIANCE.NS_raw.parquet

Use pyarrow as the engine for all parquet operations.
Add proper error handling, logging to logs/stock_agent.log, 
and full docstrings to all functions.
```

---

### PROMPT 4 — Test Data Fetcher

```
Write a test script called test_data_fetch.py in the project root 
to verify yahoo_finance.py works correctly.

Test in this exact order:
1. Fetch AAPL for first time (should do full 10 year fetch)
2. Fetch AAPL again (should detect existing data and skip or delta fetch)
3. Fetch TSLA for first time
4. Fetch RELIANCE.NS for first time
5. Call list_available_stocks() and print results

For each fetch print:
- Ticker symbol
- Fetch type: Full / Delta / Skipped
- Shape of the DataFrame (rows x columns)
- First and last 3 rows
- Any missing values count
- Parquet file size on disk

Run the test script and fix any errors before proceeding.
Delete test_data_fetch.py after successful test.
```

---

## PHASE 3: PRICE ANALYSIS TOOL

---

### PROMPT 5 — Build Price Movement Analyser

```
Create src/tools/price_analysis.py with these functions.
This file must be completely self contained and must NOT 
import from or modify any existing project files.

1. calculate_returns(df)
   - Daily returns
   - Monthly returns
   - Annual returns
   - Cumulative returns over full period
   - Return as dictionary of Series

2. calculate_technical_indicators(df)
   - SMA 50 day and SMA 200 day
   - EMA 20 day
   - RSI 14 day
   - MACD line, signal line, histogram
   - Bollinger Bands upper, middle, lower
   - Average True Range ATR 14 day
   - Add all as new columns to df and return df

3. analyse_price_movement(df)
   - Identify bull phases: price above SMA 200
   - Identify bear phases: price below SMA 200
   - Calculate max drawdown and drawdown duration
   - Find key support levels (recent lows)
   - Find key resistance levels (recent highs)
   - Calculate annualized volatility
   - Calculate Sharpe ratio assuming risk free rate 4%
   - Return as clean dictionary

4. generate_summary_stats(df, ticker)
   - All time high price and date
   - All time low price and date
   - Best performing month and return
   - Worst performing month and return
   - Best performing year and return
   - Worst performing year and return
   - Average annual return over full period
   - Current price vs SMA 50 and SMA 200
   - Current RSI value and signal: Overbought / Oversold / Neutral
   - Return as clean dictionary

5. create_analysis_chart(df, ticker)
   - Use Plotly to build interactive chart with 3 panels:
   
   Panel 1 (top, 60% height): Price chart
     * Candlestick OHLC data
     * SMA 50 in orange line
     * SMA 200 in red line
     * Bollinger Bands as dotted lines with shaded fill
   
   Panel 2 (middle, 20% height): Volume
     * Volume bars colored green/red based on price direction
   
   Panel 3 (bottom, 20% height): RSI
     * RSI line in purple
     * Horizontal lines at 70 (overbought) and 30 (oversold)
     * Shaded zones above 70 and below 30
   
   - Dark theme throughout
   - Save to charts/analysis/{ticker}_analysis.html
   - Return the file path

Add proper error handling, logging, and docstrings to all functions.
```

---

## PHASE 4: FORECASTING TOOL

---

### PROMPT 6 — Build Prophet Forecasting Model

```
Create src/tools/forecasting.py with these functions.
This file must be completely self contained and must NOT 
import from or modify any existing project files.

1. prepare_data_for_prophet(df)
   - Convert DataFrame to Prophet format
   - Column ds: datetime dates
   - Column y: Adjusted Close price
   - Handle any missing or NaN dates
   - Return clean DataFrame

2. train_prophet_model(df, ticker)
   - Train Facebook Prophet model on full history
   - Enable yearly seasonality
   - Enable weekly seasonality
   - Disable daily seasonality
   - Add US market holidays as special events
   - Return trained model object

3. forecast_price(model, df, months=9)
   - Generate forecast for specified months ahead
   - Return DataFrame with columns:
     * ds: future dates
     * yhat: forecasted price
     * yhat_lower: lower confidence bound
     * yhat_upper: upper confidence bound
   - Filter to only return future dates (not historical)

4. calculate_forecast_accuracy(model, df)
   - Backtest model on last 12 months of data
   - Calculate these accuracy metrics:
     * MAE: Mean Absolute Error
     * RMSE: Root Mean Square Error
     * MAPE: Mean Absolute Percentage Error
   - Return accuracy report as dictionary

5. save_forecast(forecast_df, ticker, months)
   - Save forecast results to data/forecasts/{ticker}_{months}m_forecast.parquet
   - Using pyarrow engine
   - Return file path

6. generate_forecast_summary(forecast_df, current_price, ticker)
   - Extract price targets at 3, 6, 9 month marks
   - Calculate percentage change from current price for each
   - Determine sentiment:
     * Bullish if 9 month forecast > current price by more than 10%
     * Bearish if 9 month forecast < current price by more than 10%
     * Neutral otherwise
   - Return clean dictionary with all targets and sentiment

7. create_forecast_chart(model, forecast_df, df, ticker)
   - Use Plotly to build interactive forecast chart:
     * Historical price in solid blue line
     * Forecasted price in dashed green line
     * Confidence interval as light green shaded area
     * Vertical dotted line marking today
     * Horizontal dotted line marking current price
     * Annotations showing price targets at 3, 6, 9 months
   - Dark theme throughout
   - Save to charts/forecasts/{ticker}_forecast.html
   - Return file path

Add proper error handling, logging, and docstrings to all functions.
```

---

## PHASE 5: STOCK AGENT

---

### PROMPT 7 — Build Main Stock Agent Class

```
Create src/agents/stock_agent.py as the main orchestrator.
Import only from our new tool files:
  from src.tools.yahoo_finance import fetch_stock_data, get_stock_info
  from src.tools.price_analysis import (calculate_technical_indicators, 
    analyse_price_movement, generate_summary_stats, create_analysis_chart)
  from src.tools.forecasting import (train_prophet_model, forecast_price,
    calculate_forecast_accuracy, generate_forecast_summary, 
    create_forecast_chart, save_forecast, prepare_data_for_prophet)

Do NOT import from or modify any existing project files.

Build this class:

class StockAnalysisAgent:

  __init__(self)
    - Initialize logging to logs/stock_agent.log
    - Log: "Stock Analysis Agent initialized"

  analyse(self, ticker, forecast_months=9)
    - Orchestrate the full pipeline:
      a. Fetch data using fetch_stock_data (handles delta automatically)
      b. Get company info using get_stock_info
      c. Calculate technical indicators
      d. Run price movement analysis
      e. Generate summary stats
      f. Train Prophet model
      g. Generate forecast for specified months
      h. Calculate forecast accuracy
      i. Generate forecast summary
      j. Create analysis chart
      k. Create forecast chart
      l. Save forecast to parquet
    - Return complete results as dictionary with all outputs

  generate_report(self, ticker, forecast_months=9)
    - Call analyse() to get full results
    - Format into clean readable text report:

      ════════════════════════════════════════
      📊 STOCK ANALYSIS REPORT — {Company} ({TICKER})
      ════════════════════════════════════════

      📈 PRICE SUMMARY
        All Time High  : ${price} ({date})
        All Time Low   : ${price} ({date})
        Current Price  : ${price}
        10Y Return     : +{pct}%
        Annual Return  : +{pct}% per year

      📉 TECHNICAL INDICATORS
        SMA 50         : ${price} ({Above/Below} — {signal})
        SMA 200        : ${price} ({Above/Below} — {signal})
        RSI (14)       : {value} ({Overbought/Neutral/Oversold})
        MACD           : {signal}
        Volatility     : {pct}% annualized
        Sharpe Ratio   : {value}

      🔮 FORECAST ({months} Month Horizon)
        3 Month Target : ${price} ({pct}%)
        6 Month Target : ${price} ({pct}%)
        9 Month Target : ${price} ({pct}%)
        Confidence     : ${lower} — ${upper}
        Sentiment      : {🟢 BULLISH / 🔴 BEARISH / 🟡 NEUTRAL}
        Model Accuracy : MAPE {pct}%

      📁 Charts saved:
        Analysis : charts/analysis/{ticker}_analysis.html
        Forecast : charts/forecasts/{ticker}_forecast.html
      ════════════════════════════════════════

    - Return formatted string report

  compare_stocks(self, tickers_list, forecast_months=9)
    - Run analyse() for each ticker in list
    - Build comparison DataFrame with columns:
      * Ticker, Company, Current Price, 10Y Return
      * Annual Return, Volatility, Sharpe Ratio, Max Drawdown
      * RSI, MACD Signal, 6M Forecast, 6M Upside%, Sentiment
    - Sort by 6M Upside% descending
    - Print comparison table to terminal
    - Return DataFrame

Add full docstrings and error handling throughout.
```

---

### PROMPT 8 — Connect Stock Agent to Chat Agent

```
Review my existing chat agent code carefully.
Then add stock analysis routing to it WITHOUT 
changing any existing functionality.

Add recognition for these user message patterns:
  - "analyse stock {TICKER}"
  - "analyze stock {TICKER}"
  - "stock analysis {TICKER}"
  - "forecast {TICKER}"
  - "forecast {TICKER} for next {N} months"
  - "price forecast {TICKER}"
  - "compare stocks {TICKER1} {TICKER2} ..."
  - "compare {TICKER1} and {TICKER2}"
  - "stock report {TICKER}"

When any pattern is matched:
  1. Extract ticker symbol(s) from the message
  2. Extract forecast months if mentioned (default 9)
  3. Import and instantiate StockAnalysisAgent
  4. Call generate_report(ticker) or compare_stocks(tickers)
  5. Return the formatted report as the chat response
  6. Include links to generated HTML charts in the response

If ticker is not recognized by Yahoo Finance:
  - Return friendly message: 
    "I could not find data for {ticker}. 
     Please check the ticker symbol and try again.
     Example: AAPL for Apple, TSLA for Tesla, RELIANCE.NS for Reliance India"

Make minimal changes to existing chat agent code.
Add a comment block above your changes:
  # === STOCK AGENT ROUTING — ADDED BY PLAN PROMPT 8 ===
  # Does not modify existing chat agent functionality
```

---

## PHASE 6: TESTING

---

### PROMPT 9 — End to End Test

```
Run a complete end to end test of the stock analysis agent.

Test sequence:

Test 1: Single Stock Full Pipeline
  - Run StockAnalysisAgent().generate_report("AAPL")
  - Verify: data fetched and saved to parquet
  - Verify: analysis chart created in charts/analysis/
  - Verify: forecast chart created in charts/forecasts/
  - Verify: forecast saved to data/forecasts/
  - Print the full text report to terminal

Test 2: Delta Fetch Verification
  - Run fetch_stock_data("AAPL") a second time
  - Verify it detects existing data
  - Verify it only fetches delta (or skips if up to date)
  - Print confirmation message

Test 3: Multiple Stock Comparison
  - Run StockAnalysisAgent().compare_stocks(["AAPL", "MSFT", "GOOGL"])
  - Print the comparison table
  - Verify all charts are created

Test 4: Data Storage Verification
  - Run list_available_stocks()
  - Print registry showing all 3 stocks
  - Show parquet file sizes

Fix any errors found before proceeding to next prompt.
```

---

### PROMPT 10 — Test via Chat Interface

```
Test the stock agent through the existing chat interface 
with these messages one by one:

1. "analyse stock AAPL"
2. "forecast TSLA for next 6 months"
3. "compare AAPL and MSFT"
4. "stock report RELIANCE.NS"
5. "analyse stock INVALID123" (test error handling)

For each test verify:
  - Correct routing to stock agent
  - Proper formatted report returned
  - Charts generated successfully
  - Error handled gracefully for invalid ticker
  - Existing chat functionality still works normally

Fix any routing or formatting issues found.
```

---

## PHASE 7: DOCUMENTATION

---

### PROMPT 11 — Update Project Documentation

```
Update the project documentation for the stock agent addition:

1. Update CLAUDE.md:
   - Add stock agent module to project structure section
   - Add data/ folder explanation
   - Add new run commands for stock agent
   - Add new dependencies list

2. Update or create PROGRESS.md:
   - Log today's date
   - List what was built: stock analysis agent
   - List all new files created
   - List pending items or known issues

3. Create docs/stock_agent.md:
   - What the stock agent does
   - How to use it via chat interface
   - Example prompts
   - Data storage explanation
   - How delta fetching works
   - Forecast methodology explanation

4. Update mkdocs.yml navigation:
   - Add Stock Agent under Modules section
   - Rebuild MkDocs docs

Do not modify any code files, only documentation files.
```

---

### PROMPT 12 — Commit Everything to GitHub

```
Review all new files created for the stock analysis agent.
Make sure no sensitive data or API keys are in any file.
Make sure .gitignore includes:
  - data/raw/
  - data/processed/
  - data/forecasts/
  - logs/
  - site/
  - __pycache__/
  - *.pyc

BUT make sure these ARE tracked by git:
  - data/metadata/stock_registry.json (empty registry)
  - src/ all new agent and tool files
  - dashboard/ all dashboard files
  - docs/ all documentation
  - mkdocs.yml

Stage all new files, commit with this message:
"feat: Add stock analysis agent with Yahoo Finance delta fetching, 
Prophet forecasting, price analysis, and Plotly Dash dashboard"

Push to GitHub main branch.
Confirm push was successful and show the GitHub repo URL.
```

---

## PHASE 8: WEB DASHBOARD

---

### PROMPT 13 — Build Plotly Dash Web Dashboard

```
Build a complete web dashboard using Plotly Dash.
Create these files only. Do NOT touch any existing files:
  dashboard/app.py
  dashboard/layouts.py  
  dashboard/callbacks.py
  dashboard/assets/custom.css

The dashboard has 4 pages with a dark theme throughout:

─────────────────────────────────────
PAGE 1: HOME / STOCK OVERVIEW
─────────────────────────────────────
- App title: "AI Stock Analysis Dashboard"
- Search bar: enter any ticker and click Analyse button
- Dropdown: select from stocks already in stock_registry.json
- Stock cards grid showing for each saved stock:
  * Company name and ticker symbol
  * Current price
  * 10 year total return %
  * Last updated date
  * Sentiment badge: 🟢 Bullish / 🔴 Bearish / 🟡 Neutral
- Clicking a stock card navigates to its analysis page

─────────────────────────────────────
PAGE 2: PRICE ANALYSIS
─────────────────────────────────────
- Ticker selector dropdown (populated from stock_registry.json)
- Date range slider to filter the chart view
- Toggle buttons: SMA 50, SMA 200, Bollinger Bands, Volume
- Main candlestick chart with selected overlays
- RSI panel below main chart
- MACD panel below RSI panel
- Summary stats cards row:
  * All Time High | All Time Low | Annual Return
  * Max Drawdown | Volatility | Sharpe Ratio

─────────────────────────────────────
PAGE 3: FORECAST
─────────────────────────────────────
- Ticker selector dropdown
- Forecast horizon radio buttons: 3 months / 6 months / 9 months
- Forecast chart:
  * Historical price in solid blue
  * Forecast in dashed green
  * Confidence interval shaded in light green
  * Today marker as vertical dotted line
  * Price target annotations at 3, 6, 9 month marks
- Price target cards row:
  * 3 Month: ${price} (+{pct}%)
  * 6 Month: ${price} (+{pct}%)
  * 9 Month: ${price} (+{pct}%)
- Model accuracy metrics: MAE, RMSE, MAPE
- Run New Analysis button: triggers StockAnalysisAgent
  and refreshes all data and charts on completion

─────────────────────────────────────
PAGE 4: COMPARE STOCKS
─────────────────────────────────────
- Multi select dropdown: choose 2 to 5 stocks
- Normalised performance chart (all stocks start at 100)
- Metrics comparison table:
  * Ticker | Annual Return | Volatility | Sharpe
  * Max Drawdown | RSI | 6M Forecast Upside% | Sentiment
- Correlation heatmap between selected stocks returns
- Best performer highlighted with 🏆 badge

─────────────────────────────────────
DASHBOARD TECHNICAL REQUIREMENTS:
─────────────────────────────────────
- Use dash-bootstrap-components with DARKLY theme
- Navbar at top with page links and app name
- All charts use Plotly dark template
- Load data from data/ parquet files only
- Auto refresh stock registry every 5 minutes
- Responsive layout for desktop and tablet
- Add loading spinners while charts are rendering

dashboard/app.py entry point:
  if __name__ == "__main__":
      app.run(debug=True, port=8050)
```

---

### PROMPT 14 — Create Launch Script and Final Verification

```
Create a launch script run_dashboard.sh in the project root:

#!/bin/bash
echo "Starting AI Stock Analysis Dashboard..."
conda activate base
cd /Users/abhaysingh/ai-agent-ui
python dashboard/app.py

Make it executable:
chmod +x run_dashboard.sh

Then run a final verification checklist:
1. Start the dashboard: python dashboard/app.py
2. Confirm it starts without errors on port 8050
3. Verify all 4 pages load correctly
4. Check stock cards appear on home page
5. Verify charts render on analysis and forecast pages
6. Test the Run New Analysis button on forecast page
7. Test compare page with AAPL and MSFT selected

After verification:
- Commit run_dashboard.sh to GitHub
- Commit any dashboard bug fixes
- Update PROGRESS.md with dashboard completion status
- Push to GitHub

Final commit message:
"feat: Add Plotly Dash dashboard with 4 pages and launch script"

Print the live dashboard URL and GitHub Pages docs URL.
```

---

## COMPLETE PROMPT REFERENCE

```
PHASE 1 — Setup
  Prompt 1  : Install all required libraries
  Prompt 2  : Create folder structure and data storage

PHASE 2 — Data Fetching
  Prompt 3  : Build smart delta fetcher with parquet storage
  Prompt 4  : Test data fetcher end to end

PHASE 3 — Price Analysis
  Prompt 5  : Build price movement analyser and chart

PHASE 4 — Forecasting
  Prompt 6  : Build Prophet forecasting model and chart

PHASE 5 — Stock Agent
  Prompt 7  : Build main StockAnalysisAgent class
  Prompt 8  : Connect stock agent to existing chat agent

PHASE 6 — Testing
  Prompt 9  : Full end to end pipeline test
  Prompt 10 : Test via chat interface

PHASE 7 — Documentation & GitHub
  Prompt 11 : Update all project documentation
  Prompt 12 : Commit everything to GitHub

PHASE 8 — Dashboard
  Prompt 13 : Build Plotly Dash web dashboard
  Prompt 14 : Launch script and final verification
```

---

## FINAL PROJECT STRUCTURE

```
ai-agent-ui/
├── src/
│   ├── agents/
│   │   ├── chat_agent.py          ← existing (unchanged)
│   │   └── stock_agent.py         ← new
│   ├── tools/
│   │   ├── yahoo_finance.py       ← new: delta fetch + parquet
│   │   ├── price_analysis.py      ← new: technical analysis
│   │   └── forecasting.py         ← new: Prophet forecasting
│   └── models/
│       └── stock_data.py          ← new: data models
│
├── data/
│   ├── raw/                       ← OHLCV parquet files (git ignored)
│   ├── processed/                 ← enriched data (git ignored)
│   ├── forecasts/                 ← forecast results (git ignored)
│   └── metadata/
│       └── stock_registry.json    ← tracked by git (empty on init)
│
├── charts/
│   ├── analysis/                  ← price analysis HTML charts
│   └── forecasts/                 ← forecast HTML charts
│
├── dashboard/
│   ├── app.py                     ← Dash app entry point
│   ├── layouts.py                 ← page layouts
│   ├── callbacks.py               ← interactivity
│   └── assets/
│       └── custom.css             ← custom styles
│
├── docs/                          ← MkDocs documentation
├── logs/                          ← agent logs (git ignored)
│
├── CLAUDE.md                      ← Claude Code project context
├── PROGRESS.md                    ← daily progress log
├── mkdocs.yml                     ← MkDocs config
├── run_dashboard.sh               ← dashboard launch script
└── .gitignore
```

---

## IMPORTANT RULES FOR CLAUDE CODE

```
1. Never modify existing chat agent files
2. Never modify existing tool files
3. All new code must be self contained
4. All data stored in parquet format using pyarrow
5. Always check for existing data before fetching
6. Always log actions to logs/stock_agent.log
7. Always handle errors gracefully
8. Commit after each phase is complete
```

---
*Plan Version: 1.0 | Project: ai-agent-ui | Author: Abhay Singh*
*GitHub: asequitytrading-design/ai-agent-ui*
