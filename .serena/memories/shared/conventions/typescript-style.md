# TypeScript Style & Conventions

## Rules

- Use `apiFetch` (not `fetch`) for all backend calls — auto-refreshes
  JWT. Source: `lib/apiFetch.ts`.
- ESLint: `@next/next/no-img-element` (use `<Image />`),
  `react-hooks/*`, no unused imports.
- Suppress with block-level `/* eslint-disable rule */` + reason
  comment — never blanket-disable.
- Config: `frontend/eslint.config.mjs`.

## Lint Commands

```bash
cd frontend && npx eslint . --fix && npx eslint .
```

## Anti-Patterns

| Anti-Pattern | Correct Pattern |
|---|---|
| `any` type | `unknown` + type narrowing |
| Raw `fetch()` calls | `apiFetch()` from `lib/apiFetch.ts` |
| `<img>` elements | `<Image />` from `next/image` |
| `innerHTML` assignment | Sanitized rendering or React JSX |
| Unused imports | Remove before commit |

## Frontend Performance

- Minimize re-renders: `React.memo`, `useMemo`, `useCallback`.
- Lazy loading: Heavy components via `dynamic()` imports.
- Images: Always `<Image />` from `next/image` (enforced by ESLint).
