# Fixing eslint-plugin-react-hooks v5+ rules (Next 16) without weakening config

`eslint-config-next` 16.x ships **React Compiler-aligned strictness rules** from `eslint-plugin-react-hooks` v5+. They flag legitimate React 18/19 patterns en masse. The repo has a **pre-commit `config-protection` hook** that BLOCKS edits to `frontend/eslint.config.mjs` ("Fix the source code … instead of weakening the config"). So all fixes have to be source-side. The four rules and their canonical fix patterns are below — reused across the 28-error → 0 cleanup on `feature/sprint8` (commit `aacb77f`, 2026-04-28).

## 1. `react-hooks/refs` — "Cannot access refs during render"

**Symptom:**
```tsx
const sessionIdRef = useRef<string>("");
if (typeof window !== "undefined" && !sessionIdRef.current) {
  sessionIdRef.current = crypto.randomUUID();   // ← Cannot access ref during render
}
```

**Fix — lazy useState init:** When the ref is "initialize once on client, then never mutate" use a lazy initializer instead.
```tsx
const [sessionId, setSessionId] = useState<string>(
  () => (typeof window !== "undefined" ? crypto.randomUUID() : ""),
);
```
Bonus: any setter (`startFromSession`) becomes `setSessionId(crypto.randomUUID())` — no functional change. SSR returns `""` and the initializer is skipped server-side.

**Symptom (mutation):**
```tsx
const messagesRef = useRef(messages);
messagesRef.current = messages;   // ← Cannot update ref during render
```

**Fix — sync inside an effect:**
```tsx
const messagesRef = useRef<Message[]>([]);
useEffect(() => { messagesRef.current = messages; }, [messages]);
```

## 2. `react-hooks/set-state-in-effect` — "Calling setState synchronously within an effect"

The rule fires when `setState` is in the **synchronous** body of `useEffect` (i.e. before any `await`). It traces through `useCallback` indirection too. Three patterns covered all 14 sites in this codebase:

### Pattern A — fetch + interval (e.g. MarketTicker)

`useCallback` indirection trips the rule. Inline the fetch into the effect, drop the useCallback:
```tsx
// BEFORE
const fetchIndices = useCallback(async () => { …; setData(json); }, []);
useEffect(() => {
  fetchIndices();                     // ← traced as sync setState in effect
  const id = setInterval(fetchIndices, POLL_INTERVAL);
  return () => clearInterval(id);
}, [fetchIndices]);

// AFTER
useEffect(() => {
  let cancelled = false;
  const fetchIndices = async () => {
    try {
      const res = await apiFetch(`${API_URL}/market/indices`);
      if (cancelled || !res.ok) return;
      const json: MarketIndices = await res.json();
      if (cancelled) return;
      setData(json);                  // ✓ after await — async-callback context
    } catch { /* keep last */ }
  };
  void fetchIndices();
  const id = setInterval(fetchIndices, POLL_INTERVAL);
  return () => { cancelled = true; clearInterval(id); };
}, []);
```

### Pattern B — fetch with refresh button (e.g. BackupHealthPanel)

When the same fetch needs to be invocable from a refresh button AND the effect, lift the trigger to a key:
```tsx
const [refreshKey, setRefreshKey] = useState(0);
const fetchData = useCallback(() => setRefreshKey((k) => k + 1), []);

useEffect(() => {
  let cancelled = false;
  setLoading(true);
  void (async () => {
    try { …; if (!cancelled) setHealth(await hRes.json()); }
    finally { if (!cancelled) setLoading(false); }
  })();
  return () => { cancelled = true; };
}, [refreshKey]);
```
**Note:** `setLoading(true)` here is still in the sync body. If the rule still fires, fall back to Pattern C for that one line.

### Pattern C — synchronous setStates that can't be moved (microtask defer)

For "setLoading(true); setError(null);" before a fetch, or "setPage(1); setSelectedTickers(new Set());" on filter change, or any one-shot state derivation that genuinely needs to fire after the effect mounts — defer past the sync body:
```tsx
useEffect(() => {
  let alive = true;
  void Promise.resolve().then(() => {
    if (!alive) return;
    setLoading(true);
    setError(null);
  });
  // … fetch chain
  return () => { alive = false; };
}, [open, scope]);
```
Or, when there's an async block, prepend `await Promise.resolve();`:
```tsx
useEffect(() => {
  let alive = true;
  void (async () => {
    await Promise.resolve();          // moves following lines past the sync body
    if (!alive) return;
    setLoading(true);
    setError(null);
    try { … } finally { if (alive) setLoading(false); }
  })();
  return () => { alive = false; };
}, [open, scope]);
```
Imperceptible timing difference (one microtask). Loading flicker is preserved.

### Pattern D — "reset state on prop change" (UserModal)

The cleanest fix is **NOT** a microtask defer — it's hoisting the gating to the parent and using a key for remount-on-identity-change. The form then inits state from props lazily and never needs the reset effect:

**Parent:**
```tsx
{modalOpen && (
  <UserModal
    key={editUser?.user_id ?? "new"}
    mode={modalMode}
    user={editUser}
    /* … no isOpen */
  />
)}
```

**UserModal:** drop `isOpen` prop, drop the reset useEffect, drop `if (!isOpen) return null` — parent's conditional render handles visibility. State defaults derive from props in `useState` lazy init.

Eliminates a whole class of "reset state on prop change" effect-cascades.

## 3. `react-hooks/immutability` — "Cannot reassign variable after render completes"

**Symptom:**
```tsx
let offset = 0;
const segments = models.map((m) => {
  const seg = { …, dashoffset: -offset, … };
  offset += dashLen;                  // ← reassign after render
  return seg;
});
```

**Fix — pre-compute via reduce (functional cumulative):**
```tsx
const dashLengths = models.map(
  (m) => (m.request_count / totalRequests) * circumference,
);
const offsets = dashLengths.reduce<number[]>(
  (acc, len) => [...acc, (acc[acc.length - 1] ?? 0) + len],
  [0],
);
const segments = models.map((m, i) => ({
  …, dashoffset: -offsets[i], …,
}));
```

## 4. `react-hooks/preserve-manual-memoization` — "Inferred different dependency than source"

The Compiler infers a dependency the hand-written deps array doesn't list. Usually it's right.

**Symptom:** `useCallback` reads `tickerCurrency(ticker, market)` but deps are `[ticker]`. Compiler infers `market`.

**Fix:** Add the missing dep — `[ticker, market]`. Don't argue with the inference unless the variable is genuinely render-stable.

## 5. `react-hooks/exhaustive-deps` (warning, not error)

### "logical expression in deps" warnings

Wrapping a `data.value?.X ?? []` derivation in the consumer's deps creates new identity each render. Lift to `useMemo`:
```tsx
const tickers = useMemo(
  () => data.value?.tickers ?? [],
  [data.value],
);
```

### Missing deps

If the dep is a stable trigger (e.g. dark-mode toggle that triggers a chart rebuild but isn't read inside the body), reference it once with `void`:
```tsx
const buildChart = useCallback(() => {
  void actualDark;                    // rebuild trigger; live values read via DOM below
  // …
}, [aggOhlcv, …, actualDark, …]);
```
Rare. Prefer adding the dep and using it normally when possible.

### "would re-run on every X" — capture initial value in ref

When an effect needs to read a prefs value once but the prefs object identity changes often:
```tsx
const initialSavedTickerRef = useRef<string | undefined>(
  chartPrefs.ticker as string | undefined,
);
useEffect(() => {
  // …
  const savedTicker = initialSavedTickerRef.current;
  // …
}, [tickerParam]);                    // chartPrefs.ticker NOT in deps
```

## 6. `@typescript-eslint/no-require-imports` for CommonJS Node scripts

For non-ESM build scripts that genuinely need `require()` (e.g. `frontend/scripts/patch-lightningcss.js`), file-level eslint-disable is acceptable:
```js
#!/usr/bin/env node
/* eslint-disable @typescript-eslint/no-require-imports -- CommonJS Node build script */
```

## 7. `_prefix` unused-args — file's no-unused-vars doesn't honour underscore

Workaround: `void _refresh;` inside the body. Keeps the param signature for back-compat (e.g. `setTokens` test fixtures pass two args).

## What NOT to do

- **Do not edit `eslint.config.mjs`** to disable rules globally — pre-commit `config-protection` hook BLOCKS this with: "Fix the source code to satisfy linter/formatter rules instead of weakening the config." Only inline `eslint-disable` comments are tolerated, and only with a `--` justification.
- **Do not refactor production widgets to SWR mid-cleanup** — that's its own ticket. Use Patterns A/B/C above to satisfy the rule with minimal blast radius.
- **Do not remove the rule's "rebuild trigger" deps.** If the rule says a dep is "unnecessary" but you actually use it as a re-render signal (e.g. `actualDark` for chart rebuilds), reference it via `void` so the rule sees it as used.

## Sprint 9 follow-up

These rules are aspirational under React Compiler. Once we enable the Compiler properly, the microtask defers and `void actualDark` workarounds should largely disappear. Tracked as "React Compiler readiness" carry-over.
