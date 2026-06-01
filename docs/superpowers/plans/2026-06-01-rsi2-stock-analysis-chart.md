# RSI(2) on Stock Analysis Chart — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a toggleable RSI(2) pane (Wilder, 2-period, 90/10 reference lines, off by default) to the stock-analysis chart at `/analytics/analysis`, mirroring the existing RSI(14) pipeline.

**Architecture:** Additive end-to-end. Backend extends `_calculate_technical_indicators` to emit an `RSI_2` column, plumbs it through `IndicatorPoint` and the `/v1/dashboard/chart/indicators` route. Frontend extends `IndicatorVisibility` with `rsi2`, adds an `IndicatorRow.rsi_2` field, adds a new TradingView pane gated on `vis.rsi2`, and exposes the toggle in the chart's indicators dropdown.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, `ta` library (RSI computation), Next.js 16, React 19, TradingView `lightweight-charts`.

**Branch:** `feature/rsi2-stock-analysis-chart` (already cut; spec doc committed as `c05003c`).

**Spec:** `docs/superpowers/specs/2026-06-01-rsi2-stock-analysis-chart-design.md`

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `backend/tools/_analysis_indicators.py` | Modify | Add `df["RSI_2"]` column inside `_calculate_technical_indicators`. |
| `backend/dashboard_models.py` | Modify | Add `rsi_2: float \| None = None` to `IndicatorPoint`. |
| `backend/dashboard_routes.py` | Modify | Append `rsi_2=_safe(row.get("RSI_2"))` inside `/chart/indicators` per-row append. |
| `tests/backend/test_analysis_indicators.py` | Create | Unit test: `_calculate_technical_indicators` emits `RSI_2` column, NaN warmup, `[0, 100]` range. |
| `tests/backend/test_dashboard_routes.py` | Modify | Extend `TestChartIndicators` with `test_rsi_2_in_response`. |
| `frontend/components/charts/StockChart.types.ts` | Modify | Add `rsi2: boolean` to `IndicatorVisibility`; `rsi2: false` in `DEFAULT_INDICATORS`. |
| `frontend/lib/types.ts` | Modify | Add `rsi_2: number \| null` to `IndicatorPoint` (the SWR response shape the analysis page imports). |
| `frontend/components/charts/StockChart.tsx` | Modify | Add `rsi_2: number \| null` to `IndicatorRow`; render new RSI(2) pane gated on `vis.rsi2`. |
| `frontend/app/(authenticated)/analytics/analysis/page.tsx` | Modify | Add `{ key: "rsi2", label: "RSI (2)" }` to `INDICATOR_OPTIONS`; add `rsi_2: d.rsi_2` to `chartIndicators` memo. |

---

## Task 1: Backend — emit RSI_2 column

**Files:**
- Create: `tests/backend/test_analysis_indicators.py`
- Modify: `backend/tools/_analysis_indicators.py` (around line 51)

- [ ] **Step 1.1: Write the failing test**

Create `tests/backend/test_analysis_indicators.py`:

```python
"""Tests for backend.tools._analysis_indicators.

Exercises ``_calculate_technical_indicators`` to ensure the
RSI(2) column is emitted with correct NaN-warmup behaviour and
range bounds.
"""

import numpy as np
import pandas as pd
import pytest

from tools._analysis_indicators import _calculate_technical_indicators


def _synthetic_ohlcv(n: int = 50) -> pd.DataFrame:
    """Build a synthetic OHLCV frame with an oscillating close."""
    # Oscillating closes drive RSI off the floor/ceiling so the
    # [0, 100] bounds check is meaningful.
    rng = np.random.default_rng(seed=42)
    base = 100.0 + np.cumsum(rng.normal(0, 1, size=n))
    df = pd.DataFrame(
        {
            "Open": base,
            "High": base + 1.0,
            "Low": base - 1.0,
            "Close": base,
            "Volume": rng.integers(1_000, 10_000, size=n),
        },
        index=pd.date_range("2024-01-01", periods=n, freq="D"),
    )
    return df


class TestRSI2Column:
    """``_calculate_technical_indicators`` must emit RSI_2."""

    def test_rsi_2_column_present(self):
        df = _calculate_technical_indicators(_synthetic_ohlcv(50))
        assert "RSI_2" in df.columns

    def test_rsi_2_warmup_is_nan(self):
        df = _calculate_technical_indicators(_synthetic_ohlcv(50))
        # Wilder RSI with window=2 needs >= 2 prior closes.
        # First row is NaN; subsequent rows are finite.
        assert pd.isna(df["RSI_2"].iloc[0])

    def test_rsi_2_in_valid_range(self):
        df = _calculate_technical_indicators(_synthetic_ohlcv(50))
        non_nan = df["RSI_2"].dropna()
        assert len(non_nan) > 0
        assert (non_nan >= 0).all() and (non_nan <= 100).all()

    def test_rsi_2_does_not_displace_rsi_14(self):
        """Regression: RSI_14 column must remain present."""
        df = _calculate_technical_indicators(_synthetic_ohlcv(50))
        assert "RSI_14" in df.columns
        assert "RSI_2" in df.columns
```

- [ ] **Step 1.2: Run test to verify it fails**

```bash
PYTHONPATH=.:backend python -m pytest \
  tests/backend/test_analysis_indicators.py -v
```

Expected: 4 failures with `AssertionError: 'RSI_2' not in df.columns` (or similar — column absent).

- [ ] **Step 1.3: Implement RSI_2 column**

Edit `backend/tools/_analysis_indicators.py`. Find the existing line:

```python
df["RSI_14"] = ta.momentum.RSIIndicator(close=close, window=14).rsi()
```

Insert immediately after it:

```python
df["RSI_2"] = ta.momentum.RSIIndicator(close=close, window=2).rsi()
```

- [ ] **Step 1.4: Run test to verify it passes**

```bash
PYTHONPATH=.:backend python -m pytest \
  tests/backend/test_analysis_indicators.py -v
```

Expected: 4 passed.

- [ ] **Step 1.5: Lint**

```bash
black backend/tools/_analysis_indicators.py tests/backend/test_analysis_indicators.py
isort backend/tools/_analysis_indicators.py tests/backend/test_analysis_indicators.py --profile black
flake8 backend/tools/_analysis_indicators.py tests/backend/test_analysis_indicators.py
```

Expected: no output (clean).

- [ ] **Step 1.6: Commit**

```bash
git add backend/tools/_analysis_indicators.py tests/backend/test_analysis_indicators.py
git commit -m "$(cat <<'EOF'
feat(indicators): emit RSI_2 column from _calculate_technical_indicators

Adds Wilder RSI(2) alongside RSI(14) for downstream consumers
(stock-analysis chart). Pure additive change; existing columns
untouched.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 2: Backend — plumb rsi_2 through IndicatorPoint and route

**Files:**
- Modify: `backend/dashboard_models.py` (around line 219 — `IndicatorPoint`)
- Modify: `backend/dashboard_routes.py` (around line 1214 — per-row append in `get_chart_indicators`)
- Modify: `tests/backend/test_dashboard_routes.py` (extend `TestChartIndicators`)

- [ ] **Step 2.1: Write the failing route test**

Append a new method inside `class TestChartIndicators` in `tests/backend/test_dashboard_routes.py` (immediately after `test_returns_sr_levels`):

```python
    @patch("tools._analysis_movement._analyse_price_movement")
    @patch("tools._analysis_shared.compute_indicators")
    @patch("dashboard_routes.get_cache")
    def test_rsi_2_in_response(
        self,
        mock_cache_fn,
        mock_compute,
        mock_movement,
        client,
    ):
        """rsi_2 is surfaced per IndicatorPoint."""
        mock_compute.return_value = pd.DataFrame(
            [
                {
                    "Close": 2500.0,
                    "SMA_50": None,
                    "SMA_200": None,
                    "EMA_20": None,
                    "RSI_14": 55.0,
                    "RSI_2": 87.3,
                    "MACD": None,
                    "MACD_Signal": None,
                    "MACD_Hist": None,
                    "BB_Upper": None,
                    "BB_Lower": None,
                }
            ],
            index=pd.DatetimeIndex(["2024-01-01"]),
        )
        mock_movement.return_value = {
            "support_levels": [],
            "resistance_levels": [],
        }
        cache = MagicMock()
        cache.get.return_value = None
        mock_cache_fn.return_value = cache

        resp = client.get(
            "/v1/dashboard/chart/indicators?ticker=RELIANCE.NS",
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"][0]["rsi_2"] == 87.3
        # Sanity: rsi_14 still flows through untouched.
        assert body["data"][0]["rsi_14"] == 55.0
```

- [ ] **Step 2.2: Run test to verify it fails**

```bash
PYTHONPATH=.:backend python -m pytest \
  tests/backend/test_dashboard_routes.py::TestChartIndicators::test_rsi_2_in_response -v
```

Expected: FAIL with `KeyError: 'rsi_2'` or `assert None == 87.3` (field not yet on the model).

- [ ] **Step 2.3: Add rsi_2 to IndicatorPoint**

Edit `backend/dashboard_models.py`. Inside `class IndicatorPoint(BaseModel):` (around line 214), find:

```python
    rsi_14: float | None = None
```

Insert immediately after it:

```python
    rsi_2: float | None = None
```

- [ ] **Step 2.4: Wire rsi_2 into the route**

Edit `backend/dashboard_routes.py`. Inside `get_chart_indicators`, find the per-row append (around line 1207) and add `rsi_2=...` right after the existing `rsi_14=...` line:

```python
points.append(
    IndicatorPoint(
        date=str(idx.date()),
        sma_50=_safe(row.get("SMA_50")),
        sma_200=_safe(
            row.get("SMA_200"),
        ),
        ema_20=_safe(row.get("EMA_20")),
        rsi_14=_safe(row.get("RSI_14")),
        rsi_2=_safe(row.get("RSI_2")),
        macd=_safe(row.get("MACD")),
        # … unchanged fields below
```

- [ ] **Step 2.5: Run test to verify it passes**

```bash
PYTHONPATH=.:backend python -m pytest \
  tests/backend/test_dashboard_routes.py::TestChartIndicators::test_rsi_2_in_response -v
```

Expected: PASS.

- [ ] **Step 2.6: Run the full TestChartIndicators class to catch regressions**

```bash
PYTHONPATH=.:backend python -m pytest \
  tests/backend/test_dashboard_routes.py::TestChartIndicators -v
```

Expected: previously-passing tests still pass; new test passes. (Per `feedback_admin_merge_through_red_ci`, some pre-existing tests in this file are red due to network calls — those are out of scope. Focus on `TestChartIndicators::test_rsi_2_in_response`, `::test_empty_data`, and `::test_returns_sr_levels` being green.)

- [ ] **Step 2.7: Lint**

```bash
black backend/dashboard_models.py backend/dashboard_routes.py tests/backend/test_dashboard_routes.py
isort backend/dashboard_models.py backend/dashboard_routes.py tests/backend/test_dashboard_routes.py --profile black
flake8 backend/dashboard_models.py backend/dashboard_routes.py tests/backend/test_dashboard_routes.py
```

Expected: no output.

- [ ] **Step 2.8: Commit**

```bash
git add backend/dashboard_models.py backend/dashboard_routes.py tests/backend/test_dashboard_routes.py
git commit -m "$(cat <<'EOF'
feat(dashboard): surface rsi_2 in /chart/indicators response

Adds rsi_2 field to IndicatorPoint and wires it through
get_chart_indicators so the stock-analysis chart can render an
RSI(2) pane. Field defaults None; pre-warmup bars stay null.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 3: Frontend — extend IndicatorVisibility type

**Files:**
- Modify: `frontend/components/charts/StockChart.types.ts`

- [ ] **Step 3.1: Add rsi2 to IndicatorVisibility and DEFAULT_INDICATORS**

Edit `frontend/components/charts/StockChart.types.ts`. Replace the whole file body (lines 5–25) with:

```ts
export type ChartInterval = "D" | "W" | "M";

export interface IndicatorVisibility {
  sma50: boolean;
  sma200: boolean;
  bollinger: boolean;
  volume: boolean;
  rsi: boolean;
  rsi2: boolean;
  macd: boolean;
  supportResistance: boolean;
}

export const DEFAULT_INDICATORS: IndicatorVisibility = {
  sma50: true,
  sma200: true,
  bollinger: false,
  volume: false,
  rsi: true,
  rsi2: false,
  macd: true,
  supportResistance: false,
};
```

- [ ] **Step 3.2: Type-check**

```bash
cd frontend && npx tsc --noEmit
```

Expected: no errors. (If the type-checker flags `rsi2` as missing in other usages — e.g. anywhere `IndicatorVisibility` is constructed without spread from `DEFAULT_INDICATORS` — note the file:line and fix in Task 5; do not fix here.)

- [ ] **Step 3.3: Commit**

```bash
git add frontend/components/charts/StockChart.types.ts
git commit -m "$(cat <<'EOF'
feat(charts): add rsi2 to IndicatorVisibility (off by default)

Pure type/default extension. New pane wiring lands in StockChart
next; menu wiring in the analysis page after that.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 4: Frontend — extend IndicatorPoint in shared types

**Files:**
- Modify: `frontend/lib/types.ts` (around line 216)

This module is the canonical shape used by the analysis page's SWR `IndicatorsResponse`. Without `rsi_2` on the shared `IndicatorPoint`, the `chartIndicators` memo in Task 6 fails type-check.

- [ ] **Step 4.1: Add rsi_2 to IndicatorPoint**

Edit `frontend/lib/types.ts`. Find:

```ts
export interface IndicatorPoint {
  date: string;
  sma_50: number | null;
  sma_200: number | null;
  ema_20: number | null;
  rsi_14: number | null;
  macd: number | null;
  macd_signal: number | null;
  macd_hist: number | null;
  bb_upper: number | null;
  bb_lower: number | null;
}
```

Replace with (adds `rsi_2` directly after `rsi_14`):

```ts
export interface IndicatorPoint {
  date: string;
  sma_50: number | null;
  sma_200: number | null;
  ema_20: number | null;
  rsi_14: number | null;
  rsi_2: number | null;
  macd: number | null;
  macd_signal: number | null;
  macd_hist: number | null;
  bb_upper: number | null;
  bb_lower: number | null;
}
```

**Do not** touch the other `rsi_14` occurrences in this file (lines 186 and 345) — those belong to unrelated compare/insights row types.

- [ ] **Step 4.2: Type-check**

```bash
cd frontend && npx tsc --noEmit
```

Expected: no errors. (The field is additive; nothing references it yet.)

- [ ] **Step 4.3: Commit**

```bash
git add frontend/lib/types.ts
git commit -m "$(cat <<'EOF'
feat(types): add rsi_2 to IndicatorPoint shared type

Matches the new backend field on /v1/dashboard/chart/indicators.
Pure type extension; no runtime impact.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 5: Frontend — render RSI(2) pane in StockChart

**Files:**
- Modify: `frontend/components/charts/StockChart.tsx` (`IndicatorRow` interface around line 53; new pane after line 645)

- [ ] **Step 5.1: Extend IndicatorRow**

Edit `frontend/components/charts/StockChart.tsx`. Find the `IndicatorRow` interface (around line 53):

```ts
export interface IndicatorRow {
  date: string;
  sma_50: number | null;
  sma_200: number | null;
  rsi_14: number | null;
  macd: number | null;
  macd_signal: number | null;
  macd_hist: number | null;
  bb_upper: number | null;
  bb_lower: number | null;
}
```

Add `rsi_2` right after `rsi_14`:

```ts
export interface IndicatorRow {
  date: string;
  sma_50: number | null;
  sma_200: number | null;
  rsi_14: number | null;
  rsi_2: number | null;
  macd: number | null;
  macd_signal: number | null;
  macd_hist: number | null;
  bb_upper: number | null;
  bb_lower: number | null;
}
```

- [ ] **Step 5.2: Add the RSI(2) pane after the existing RSI(14) pane**

Still in `frontend/components/charts/StockChart.tsx`, find the end of the RSI(14) pane block (around line 645, immediately after the closing `}` of `if (vis.rsi) { … }` and before the `// ── Pane 4: MACD ───` comment). Insert this block:

```ts
    // ── Pane 3b: RSI (2) ───────────────────────

    if (vis.rsi2) {
      const rsi2Pane = chart.addPane();
      subPanes.push(rsi2Pane);
      const rsi2Series = rsi2Pane.addSeries(
        LineSeries,
        {
          color: "#ec4899",
          lineWidth: 2,
          priceLineVisible: false,
          lastValueVisible: true,
          title: "",
        },
      );
      rsi2Series.setData(
        filterNull(
          aggIndicators.map((d) => ({
            time: toTime(d.date),
            value: d.rsi_2,
          })),
        ),
      );
      rsi2Series.createPriceLine({
        price: 90,
        color: "rgba(251,191,36,0.5)",
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: "",
      });
      rsi2Series.createPriceLine({
        price: 10,
        color: "rgba(251,191,36,0.5)",
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: "",
      });
    }
```

- [ ] **Step 5.3: Add rsi2 to the `vis` memo and its deps**

Still in `frontend/components/charts/StockChart.tsx`, find the `vis` memo (lines 266–285). It currently reads:

```ts
const vis = useMemo(
  () => ({
    sma50: visibleIndicators.sma50,
    sma200: visibleIndicators.sma200,
    bollinger: visibleIndicators.bollinger,
    volume: visibleIndicators.volume,
    rsi: visibleIndicators.rsi,
    macd: visibleIndicators.macd,
    supportResistance: visibleIndicators.supportResistance,
  }),
  [
    visibleIndicators.sma50,
    visibleIndicators.sma200,
    visibleIndicators.bollinger,
    visibleIndicators.volume,
    visibleIndicators.rsi,
    visibleIndicators.macd,
    visibleIndicators.supportResistance,
  ],
);
```

Replace with (adds `rsi2:` line and matching deps entry beside the existing `rsi` siblings):

```ts
const vis = useMemo(
  () => ({
    sma50: visibleIndicators.sma50,
    sma200: visibleIndicators.sma200,
    bollinger: visibleIndicators.bollinger,
    volume: visibleIndicators.volume,
    rsi: visibleIndicators.rsi,
    rsi2: visibleIndicators.rsi2,
    macd: visibleIndicators.macd,
    supportResistance: visibleIndicators.supportResistance,
  }),
  [
    visibleIndicators.sma50,
    visibleIndicators.sma200,
    visibleIndicators.bollinger,
    visibleIndicators.volume,
    visibleIndicators.rsi,
    visibleIndicators.rsi2,
    visibleIndicators.macd,
    visibleIndicators.supportResistance,
  ],
);
```

- [ ] **Step 5.4: Type-check**

```bash
cd frontend && npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 5.5: Lint**

```bash
cd frontend && npx eslint components/charts/StockChart.tsx --fix
```

Expected: no errors.

- [ ] **Step 5.6: Commit**

```bash
git add frontend/components/charts/StockChart.tsx
git commit -m "$(cat <<'EOF'
feat(charts): render RSI(2) pane with 90/10 reference lines

Adds a separate pane below RSI(14), gated on visibleIndicators.rsi2
(off by default). Pink line (#ec4899) distinguishes it from
RSI(14)'s violet. Renders nothing when the toggle is off.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 6: Frontend — wire menu option + memo in analysis page

**Files:**
- Modify: `frontend/app/(authenticated)/analytics/analysis/page.tsx` (`INDICATOR_OPTIONS` at line 128; `chartIndicators` memo at line 367)

- [ ] **Step 6.1: Add the menu entry**

Edit `frontend/app/(authenticated)/analytics/analysis/page.tsx`. Find `INDICATOR_OPTIONS` (line 128) and add the RSI (2) entry directly after the RSI (14) entry:

```ts
const INDICATOR_OPTIONS: {
  key: keyof IndicatorVisibility;
  label: string;
}[] = [
  { key: "sma50", label: "SMA 50" },
  { key: "sma200", label: "SMA 200" },
  { key: "bollinger", label: "Bollinger Bands" },
  { key: "volume", label: "Volume" },
  { key: "rsi", label: "RSI (14)" },
  { key: "rsi2", label: "RSI (2)" },
  { key: "macd", label: "MACD" },
  { key: "supportResistance", label: "Support/Resistance" },
];
```

- [ ] **Step 6.2: Add rsi_2 to the chartIndicators memo**

Still in the same file, find the `chartIndicators` memo (line 367) and add `rsi_2: d.rsi_2` right after the existing `rsi_14: d.rsi_14`:

```ts
const chartIndicators = useMemo(
  () =>
    indicators?.data.map((d) => ({
      date: d.date,
      sma_50: d.sma_50,
      sma_200: d.sma_200,
      rsi_14: d.rsi_14,
      rsi_2: d.rsi_2,
      macd: d.macd,
      macd_signal: d.macd_signal,
      macd_hist: d.macd_hist,
      bb_upper: d.bb_upper,
      bb_lower: d.bb_lower,
    })) ?? [],
  [indicators],
);
```

The SWR response type is imported from `@/lib/types` (see Task 4); no local type tweak needed here.

- [ ] **Step 6.3: Type-check**

```bash
cd frontend && npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 6.4: Lint**

```bash
cd frontend && npx eslint "app/(authenticated)/analytics/analysis/page.tsx" --fix
```

Expected: no errors.

- [ ] **Step 6.5: Commit**

```bash
git add "frontend/app/(authenticated)/analytics/analysis/page.tsx"
git commit -m "$(cat <<'EOF'
feat(analysis): expose RSI (2) toggle in indicators menu

Adds RSI (2) entry to INDICATOR_OPTIONS and threads rsi_2 through
chartIndicators memo so StockChart can render the new pane.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 7: Manual smoke test

**Files:** none

- [ ] **Step 7.1: Restart backend and flush Redis**

```bash
./run.sh restart backend
sleep 5
docker compose exec redis redis-cli FLUSHALL
```

Expected: backend container restarts (per CLAUDE.md §6.2, new `response_model` fields require restart); Redis returns `OK`.

- [ ] **Step 7.2: Hit the route directly**

```bash
curl -s http://localhost:8181/v1/dashboard/chart/indicators?ticker=RELIANCE.NS \
  -H "Cookie: $(grep -E '^access_token=' ~/.ai-agent-ui/dev-cookies 2>/dev/null || echo '')" \
  | python -c "import sys, json; d = json.load(sys.stdin); print(json.dumps(d['data'][-1], indent=2))"
```

Expected: the latest `IndicatorPoint` JSON contains a `rsi_2` key with a `float` value in `[0, 100]` (RELIANCE.NS has full history; the last bar should not be NaN). If you don't have a dev cookie handy, hit the endpoint via the browser DevTools network tab after logging in to the app.

- [ ] **Step 7.3: Visual smoke test in the browser**

1. Open `http://localhost:3000/analytics/analysis?ticker=RELIANCE.NS` while logged in.
2. Confirm the chart still renders today's panes (price, RSI(14), MACD) — RSI(2) should NOT be visible by default.
3. Click the "Indicators" dropdown — confirm "RSI (2)" appears between "RSI (14)" and "MACD".
4. Toggle "RSI (2)" on. Confirm:
   - A new pane appears below RSI(14).
   - The pane has a pink line.
   - Dashed amber reference lines render at the 90 and 10 levels.
   - The last value label on the right-hand axis matches the curl payload from Step 6.2.
5. Reload the page. Confirm RSI(2) stays on (preference persisted via localStorage).
6. Toggle RSI(2) off. Confirm the pane disappears and chart height returns to the baseline.

- [ ] **Step 7.4: Optional dark-mode pass**

Toggle dark mode and confirm the pink line and reference lines remain legible against the dark background. (No code change should be needed — RSI(14) uses the same pattern.)

---

## Task 8: Push branch and open PR

**Files:** none

- [ ] **Step 8.1: Push the branch**

```bash
git push -u origin feature/rsi2-stock-analysis-chart
```

- [ ] **Step 8.2: Open the PR**

```bash
gh pr create --base dev --title "feat(charts): RSI(2) pane on stock analysis chart" --body "$(cat <<'EOF'
## Summary

- Adds a toggleable RSI(2) pane to the stock-analysis chart at `/analytics/analysis`.
- Wilder 2-period RSI, 90/10 reference lines, pink line — visually distinct from the violet RSI(14).
- Off by default (`DEFAULT_INDICATORS.rsi2 = false`) — zero render cost until toggled on.
- Backend: extends `_calculate_technical_indicators` with an `RSI_2` column and surfaces `rsi_2` on `IndicatorPoint` / `/v1/dashboard/chart/indicators`.
- Frontend: extends `IndicatorVisibility`, `IndicatorRow`, and the analysis page's indicators dropdown.

Spec: `docs/superpowers/specs/2026-06-01-rsi2-stock-analysis-chart-design.md`

## Test plan

- [x] `pytest tests/backend/test_analysis_indicators.py -v` — RSI_2 column + NaN warmup + range bounds.
- [x] `pytest tests/backend/test_dashboard_routes.py::TestChartIndicators::test_rsi_2_in_response -v` — route surfaces rsi_2.
- [x] `npx tsc --noEmit` clean in `frontend/`.
- [x] Manual smoke test (Task 6) on `/analytics/analysis?ticker=RELIANCE.NS`: toggle on → pink pane with 90/10 lines; toggle off → pane removed; reload → preference persisted.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: PR opens against `dev`. Wait for review.

---

## Notes for the implementer

- **DRY:** the new RSI(2) pane block is intentionally a near-copy of the RSI(14) pane block — do not refactor them into a shared helper for this PR. The diff stays small and reviewable; consolidation can be a follow-up if a third RSI variant ever appears.
- **YAGNI:** no period picker, no strategy markers, no Insights-table integration. All explicitly out of scope per the spec (§10).
- **TDD:** Tasks 1 and 2 each follow the test-first cycle (write failing test → implement → green). Tasks 3–5 are frontend-only and the spec defers chart-level unit tests (no `StockChart.test.tsx` exists today); rely on `tsc --noEmit` + the manual smoke test in Task 6.
- **Commits are bite-sized:** one logical change per commit, all squash-merged when the PR lands (per CLAUDE.md §4.4 #27).
- **Branch is already cut.** Do not branch again; `feature/rsi2-stock-analysis-chart` already contains the spec doc commit (`c05003c`).
