# Playwright Testing Patterns for React 19 + Dash

## React 19 Controlled Inputs
- `fill()` does NOT reliably trigger React 19's synthetic `onChange` on controlled `<textarea>` / `<input>` with `value={state}` + `onChange={handler}`
- **Always use `pressSequentially(text, { delay: 30 })` for controlled inputs**
- This simulates real keystrokes → fires native `input` events → React picks up via `e.target.value`
- Also `click()` the input first to focus it before typing

## Dash Bootstrap Components (dbc 2.0.4)
- `dbc.Tabs`, `dbc.Button`, `dbc.Row`, `dbc.Input`, `dbc.Tab` do NOT accept arbitrary kwargs like `data-testid`
- Wrap in `html.Div(**{"data-testid": "..."})` or `html.Span(...)` for test attributes
- If the component already has an `id=`, use `page.locator("#id")` instead of testid

## Dash Debug Mode
- Debug toolbar (`dash-debug-menu__outer--expanded`) overlays bottom of page
- Use `{ force: true }` on clicks near bottom (pagination, footers)
- File watcher restarts Dash on ANY file change in project tree
- **Keep Playwright `outputDir` outside project tree** (e.g. `/tmp/e2e-test-results`)

## Dash Page Loading
- `waitForDashLoading()` waits for `._dash-loading` spinner appear/disappear
- Always check for blank pages (Dash restart) and "Callback error" toolbar
- Pattern: navigate → waitForDashLoading → check navbar exists → retry if blank

## Agent Selector (Button Group)
- Dash/React button groups render as `<div>` with `<button>` children
- Use `getByRole("button", { name })` not `getByRole("option")`
- Active state via CSS class (e.g. `button.bg-white`) — wait with `toHaveClass(/bg-white/)`

## NDJSON Stream Mocking
- Frontend expects `{ type: "final", response: "..." }` (NOT `content`)
- Mock with `route.fulfill({ contentType: "application/x-ndjson", body: JSON.stringify({...}) + "\n" })`

## Test Stability
- Serial mode for stateful tests: `test.describe.configure({ mode: "serial" })`
- API login retry (3 attempts, 1s delay) handles transient 500s during concurrent test startup
- `retries: 1` locally, `retries: 2` in CI for environmental flakiness
