---
paths:
  - "e2e/**"
---

# E2E (Playwright) rules (auto-loads when touching e2e code)

> Lazy-loaded via `paths:` frontmatter. Mirrors what was CLAUDE.md §5.14.
> Deep detail → Serena memories `e2e-test-patterns`, `test-isolation-gotchas`.

Every new interactive element MUST have `data-testid`. Every new page test MUST use Page Object Model.

- Testid registry: `e2e/utils/selectors.ts` `FE` object. Pattern `component-element`. NEVER hardcode in spec.
- POM: extend `BasePage` (`e2e/pages/frontend/`). Use `this.tid(FE.name)`.
- Auth fixtures: storage state pre-loaded for `general-user`, `superuser`. NEVER call `/auth/login` in spec (rate-limit). `auth.setup.ts` captures Set-Cookie + rewrites domain. `e2e/.auth/*.json` cookies need access+refresh tokens with `domain: "localhost"`. → `playwright-cookie-fixture`
- Locator scoping: chat tests use `[data-testid="chat-panel"]` scope.
- NEVER `networkidle` (dashboard polls 30s + WS). Use element waits.
- Below-fold: `waitFor({state: "attached"})` then `scrollIntoViewIfNeeded()`.
- Strict mode: `/^cancel$/i` not `/cancel|close/i` (matches both Cancel + Close X).
- Workers: 1 local, 2 CI. NEVER raise (>3 starves Docker).
- `maxFailures: 10` local cap; `--max-failures=0` for tech-debt sweeps.
