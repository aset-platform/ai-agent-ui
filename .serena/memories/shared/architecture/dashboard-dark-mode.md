# Dashboard Dark Mode — Design Reference

**Status**: Not implemented. This is a design plan for future work.

## Current Theme Architecture

- **Theme**: FLATLY (light only) via `dbc.themes.FLATLY`
- **CSS Variables**: All colors defined in `:root` in `custom.css`
  (enables dark mode via variable swapping — no HTML changes needed)
- **Navbar**: `color="light", dark=False` hardcoded in `navbar.py`

### CSS Variables (current light theme)
```css
:root {
  --bg:             #f9fafb;   /* gray-50 */
  --card-bg:        #ffffff;
  --border:         #e5e7eb;   /* gray-200 */
  --text-primary:   #111827;   /* gray-900 */
  --text-secondary: #6b7280;   /* gray-500 */
  --accent:         #4f46e5;   /* indigo-600 */
  --accent-hover:   #4338ca;   /* indigo-700 */
  --accent-light:   #eef2ff;   /* indigo-50 */
}
```

## Dark Mode Blockers

**Hardcoded inline colors** in Python files:
- `callbacks/table_builders.py` — `"color": "#6b7280"`
- `callbacks/auth_utils.py` — various inline styles
- These need refactoring to CSS variables or conditional logic

**Plotly charts**: Need `template="plotly_dark"` conditional in
`chart_builders.py` and `chart_builders2.py`.

## Implementation Plan (4 phases)

1. **CSS Variables**: Add dark `:root` overrides via
   `@media (prefers-color-scheme: dark)` or `.dark-mode` class
2. **Theme Toggle**: Button in navbar + `dcc.Store(storage_type="local")`
   + callback to swap CSS class on document root
3. **Refactor Hardcoded Colors**: Extract inline hex values to CSS vars
4. **Plotly Dark Mode**: Conditional `template="plotly_dark"` in chart builders

### Proposed Dark Palette
```css
html.dark-mode {
  --bg:             #0f172a;   /* slate-900 */
  --card-bg:        #1e293b;   /* slate-800 */
  --border:         #334155;   /* slate-700 */
  --text-primary:   #f1f5f9;   /* slate-100 */
  --text-secondary: #94a3b8;   /* slate-400 */
  --accent:         #6366f1;   /* indigo-500 */
  --accent-hover:   #818cf8;   /* indigo-400 */
  --accent-light:   #312e81;   /* indigo-900 */
}
```

## Best Bootstrap Dark Theme Candidates
- **DARKLY** — professional (gray/slate), matches FLATLY well
- **CYBORG** — modern (gray/cyan)
- **SLATE** — clean dark

## Key Files to Modify
- `dashboard/app_init.py` — external stylesheets
- `dashboard/assets/custom.css` — CSS variables
- `dashboard/layouts/navbar.py` — toggle button
- `dashboard/app_layout.py` — theme store
- New: `dashboard/callbacks/theme_cbs.py`
- Refactor: `table_builders.py`, `chart_builders*.py`
