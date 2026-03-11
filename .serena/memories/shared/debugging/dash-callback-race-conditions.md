# Dash Callback Race Conditions — Token Auth

## Problem Pattern
When a Dash page loads with `?token=JWT` in the URL:
1. `store_token_from_url()` (Input: `url.search`) extracts and stores token
2. `display_page()` (Input: `url.pathname`, `url.search`) renders the page
3. Chart callbacks fire when dropdowns get default values

Steps 1-3 happen concurrently. Chart callbacks read
`State("auth-token-store")` which may still be `None`.

## Fix Pattern
1. **`display_page`**: Use `Input("auth-token-store")` not `State`
   so it re-fires after `store_token_from_url` updates the store.
2. **Chart callbacks**: Add `State("url", "search")` and call
   `_resolve_token(token, search)` from `auth_utils.py` before
   `_validate_token(token)`. This extracts token from URL as fallback.
3. **`prevent_initial_call=True` callbacks**: Don't need the fix —
   they only fire on user interaction (token is stored by then).

## `_resolve_token` Helper
Located at `dashboard/callbacks/auth_utils.py:154`.
Prefers URL token over stored token:
```python
def _resolve_token(stored_token, url_search):
    token = stored_token
    if url_search:
        qs = parse_qs(url_search.lstrip("?"))
        url_token = qs.get("token", [None])[0]
        if url_token:
            token = url_token
    return token
```

## Files Affected
- `dashboard/app_layout.py` — `display_page`
- `dashboard/callbacks/analysis_cbs.py` — 3 callbacks
- `dashboard/callbacks/forecast_cbs.py` — 2 callbacks
- `dashboard/callbacks/home_cbs.py` — 1 callback

## E2E Note
Single-threaded Dash server can't handle parallel browser
connections. Use `npx playwright test --workers=1` for reliable
full-suite pass (50/50). With 2 workers, dashboard tests flake.
