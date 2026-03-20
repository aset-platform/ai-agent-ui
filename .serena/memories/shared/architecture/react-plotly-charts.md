# React Plotly Charts (Reduced Role)

## Status: Partially superseded by lightweight-charts v5

### Still uses Plotly (`plotly.js-basic-dist`)
- **Forecast chart**: Line + confidence band fill (`buildForecastChart` in `chartBuilders.ts`)
- **Correlation heatmap**: `buildCorrelationHeatmap` in insights + compare
- **Insights bar charts**: Sector averages in insights page
- **Comparison chart**: Normalized price lines

### Migrated to lightweight-charts
- **Analysis page**: Candlestick + Volume + RSI + MACD → `StockChart.tsx`
- Reason: `plotly.js-basic-dist` doesn't include `candlestick` chart type

### PlotlyChart Component: `frontend/components/charts/PlotlyChart.tsx`
- Dynamic import with `ssr: false` (Next.js)
- Lazy loads both `plotly.js-basic-dist` and `react-plotly.js/factory`
- Shows skeleton during hydration
- Exports `CHART_COLORS` array for consistent palette

### Chart Builder Utilities: `frontend/components/charts/chartBuilders.ts`
- `buildForecastChart()` — with sentiment emoji and green confidence band
- `buildForecastShapes()` — today marker, current price line, target annotations
- `buildCorrelationHeatmap()` — RdBu colorscale
- `buildComparisonChart()` — normalized price lines
- `buildPriceChart()`, `buildRSIChart()`, `buildMACDChart()` — legacy (unused after migration)
