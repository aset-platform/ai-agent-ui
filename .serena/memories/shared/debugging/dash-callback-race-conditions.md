# Dash Callback Race Conditions & Patterns

## Token Auth Race Condition

When a Dash page loads with `?token=JWT` in the URL:
1. `store_token_from_url()` (Input: `url.search`) extracts and stores token
2. `display_page()` (Input: `url.pathname`, `url.search`) renders the page
3. Chart callbacks fire when dropdowns get default values

Steps 1-3 happen concurrently. Chart callbacks read
`State("auth-token-store")` which may still be `None`.

### Fix Pattern
1. **`display_page`**: Use `Input("auth-token-store")` not `State`
   so it re-fires after `store_token_from_url` updates the store.
2. **Chart callbacks**: Add `State("url", "search")` and call
   `_resolve_token(token, search)` from `auth_utils.py` before
   `_validate_token(token)`. This extracts token from URL as fallback.
3. **`prevent_initial_call=True` callbacks**: Don't need the fix —
   they only fire on user interaction (token is stored by then).

### `_resolve_token` Helper
Located at `dashboard/callbacks/auth_utils.py`.
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

### Files Affected
- `dashboard/app_layout.py` — `display_page`
- `dashboard/callbacks/analysis_cbs.py` — 3 callbacks
- `dashboard/callbacks/forecast_cbs.py` — 2 callbacks
- `dashboard/callbacks/home_cbs.py` — 1 callback

## State vs Input

- **State**: reads value but doesn't trigger callback
- **Input**: reads AND triggers

For token stores, MUST use `Input` on `display_page` to re-render
after `store_token_from_url` fires.

## Multi-Callback Overlay Pattern

When multiple callbacks write to the same output (e.g. error overlay):

- ALL callbacks use `allow_duplicate=True` for the shared output
- Auto-dismiss via `dbc.Alert(duration=8000)` — no separate timer
- No "primary" callback needed in Dash 4.0 with
  `suppress_callback_exceptions=True`

```python
# Layout:
html.Div(id="error-overlay-container")  # empty placeholder

# Each callback:
Output("error-overlay-container", "children", allow_duplicate=True)

# Error banner:
dbc.Alert(message, color="danger", dismissable=True,
          is_open=True, duration=8000)
```

**Anti-pattern**: Do NOT use a dynamically-created `dcc.Interval`
inside the overlay as a primary callback Input — it blocks all
duplicate callbacks from updating the output.

## In-Place Store Update (No Reload)

Instead of: action → increment trigger → reload all data → re-render
Use: action → update client-side store directly → render callback
picks up change via `Input`.

This avoids Iceberg re-fetch, API re-call, spinner flash, and
scroll jump.

## E2E Note

Single-threaded Dash server can't handle parallel browser
connections. Use `npx playwright test --workers=1` for reliable
full-suite pass.
