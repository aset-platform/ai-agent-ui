# ECharts Dark/Light Theme Hydration Issue

## Problem
`useTheme()` hook's `resolvedTheme` lags behind the actual DOM class toggle in Next.js.
When theme switches, `resolvedTheme` updates in a different React render cycle than
when `document.documentElement.classList` changes. ECharts options computed from stale
`resolvedTheme` render with wrong colors.

Symptoms:
- Dark mode shows light-mode grid lines/labels (or vice versa)
- Toggling theme appears to "swap" styles instead of fixing them
- `useMemo` with `[isDark]` dependency doesn't help — `isDark` is stale at compute time

## Root Cause
`echarts-for-react` wrapped by `next/dynamic` doesn't trigger `componentDidUpdate`
reliably when options change via prop updates. Even with `notMerge={true}`, the
component may merge old state.

## Solution: MutationObserver

```typescript
function useDarkMode(): boolean {
  const [dark, setDark] = useState(false);
  const sync = useCallback(() => {
    setDark(document.documentElement.classList.contains("dark"));
  }, []);
  useEffect(() => {
    sync();
    const obs = new MutationObserver(sync);
    obs.observe(document.documentElement, {
      attributes: true, attributeFilter: ["class"],
    });
    return () => obs.disconnect();
  }, [sync]);
  return dark;
}
```

This observes the actual `dark` class on `<html>` and updates state immediately.

## Additional Requirements
- `notMerge={true}` on `ReactECharts` — forces full option replacement
- `key={isDark ? "dark" : "light"}` — forces React to destroy/recreate component
- Don't use `useMemo` for option computation — compute fresh each render

## Where Applied
- `AssetPerformanceWidget.tsx` — uses `useDarkMode()` hook
- Other ECharts widgets (SectorAllocation, PLTrend) use `useTheme()` + `notMerge` which works
  because they don't have fine-grained dark/light color differences in grid lines
