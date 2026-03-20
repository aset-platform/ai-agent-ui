# SSR Hydration Mismatches — TradingView Charts

## Problem
TradingView lightweight-charts renders with dark background on a light mode page (or vice versa). The chart `useEffect` runs with stale `isDark` value from SSR/initial render.

## Root Cause
`useTheme()` hook resolves theme from localStorage on client, but during SSR or first hydration, the value may differ from the DOM's actual `<html class="dark">` state. The chart builds once with the wrong colors and the `useEffect` deps don't catch the mismatch.

## Fix: `useDomDark` Hook
File: `frontend/components/charts/useDarkMode.ts`

```typescript
export function useDomDark(isDark: boolean) {
  const [domDark, setDomDark] = useState(() =>
    document.documentElement.classList.contains("dark")
  );
  useEffect(() => {
    const el = document.documentElement;
    const update = () => setDomDark(el.classList.contains("dark"));
    update();
    const obs = new MutationObserver(update);
    obs.observe(el, { attributes: true, attributeFilter: ["class"] });
    return () => obs.disconnect();
  }, []);
  return isDark || domDark;
}
```

Usage in chart components:
```typescript
export function MyChart({ isDark: isDarkProp, ... }) {
  const isDark = useDomDark(isDarkProp);
  // ... use isDark for chart colors
}
```

## Applied To
All 5 chart components: StockChart (already had inline version), PortfolioChart, PortfolioForecastChart, ForecastChart, CompareChart.

## Key Insight
The `StockChart.tsx` already solved this with an inline MutationObserver + `actualDark = isDark || domDark`. The shared hook extracts this pattern for reuse.
