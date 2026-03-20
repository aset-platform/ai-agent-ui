# Dash: Multi-Callback Overlay Pattern (Learned Mar 7, 2026)

## Problem
Multiple Dash callbacks need to write to the same output (`error-overlay-container.children`).

## What DOESN'T Work
- Having a "primary" callback (no `allow_duplicate`) whose `Input` is a dynamically-created component (e.g., `dcc.Interval` inside the overlay itself)
- Even with `suppress_callback_exceptions=True`, the primary callback's unresolvable Input blocks ALL duplicate callbacks from updating the output

## What WORKS
- ALL callbacks use `allow_duplicate=True` for the shared output
- Auto-dismiss via `dbc.Alert(duration=8000)` instead of a separate timer callback
- No need for a primary callback at all in Dash 4.0 with `suppress_callback_exceptions=True`

## Pattern
```python
# In layout (app_layout.py):
html.Div(id="error-overlay-container")  # empty placeholder

# In each callback file:
Output("error-overlay-container", "children", allow_duplicate=True)

# Error banner:
dbc.Alert(message, color="danger", dismissable=True, is_open=True, duration=8000)
```

## CSS
```css
.error-overlay-wrapper {
  position: fixed; top: 0; left: 0; width: 100%; z-index: 9999;
}
```
