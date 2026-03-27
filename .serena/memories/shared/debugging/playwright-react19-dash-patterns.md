# Playwright Testing: React 19 + Dash Patterns

## React 19 Controlled Inputs
- `fill()` does NOT reliably trigger React 19's synthetic `onChange` on controlled inputs
- **Always use `pressSequentially(text, { delay: 30 })` for controlled inputs**
- Click the input first to focus before typing

## Dash Bootstrap Components (dbc)
- `dbc.Tabs`, `dbc.Button`, etc. do NOT accept `data-testid`
- Wrap in `html.Div(**{"data-testid": "..."})` or use `#id` locators
- Debug toolbar overlays page bottom — use `{ force: true }` for clicks near footer

## Dash Page Loading
- `waitForDashLoading()` waits for `._dash-loading` spinner appear/disappear
- Always check for blank pages (Dash restart) and "Callback error" toolbar

## NDJSON Stream Mocking
- Frontend expects `{ type: "final", response: "..." }` (NOT `content`)
- Mock: `route.fulfill({ contentType: "application/x-ndjson", body: JSON.stringify({...}) + "\\n" })`

## Test Stability
- Serial mode for stateful tests: `test.describe.configure({ mode: "serial" })`
- API login retry (3 attempts, 1s delay) for transient 500s
- `retries: 1` locally, `retries: 2` in CI
