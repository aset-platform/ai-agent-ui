---
paths:
  - "auth/endpoints/admin_routes.py"
  - "auth/endpoints/user_routes.py"
  - "auth/dependencies.py"
  - "frontend/components/admin/**"
  - "frontend/app/**/admin/**"
  - "frontend/hooks/useAdminData.ts"
---

# Admin scope-aware rules — pro vs superuser (auto-loads when touching admin code)

> Lazy-loaded via `paths:` frontmatter. Mirrors what was CLAUDE.md §5.7.
> Deep detail → Serena memories `pro-user-role-scoped-admin`, `observability`, `auth-jwt-flow`.

`?scope=self|all` query param; pro forced to `self` (403 on `all`), superuser defaults to `all`. Applies to `/admin/audit-log`, `/admin/metrics`, `/admin/usage-stats` (guard: `pro_or_superuser`). Other ~45 endpoints: `superuser_only`.

`TabDef.roles: Role[]` filters admin tab strip. Pro = 3-tab (`my_account`, `my_audit`, `my_llm`). General → `/dashboard`.

Tier→role auto-sync: `free→general`, `pro|premium→pro`, superuser sticky. Fires `ROLE_PROMOTED`/`ROLE_DEMOTED`. Frontend calls `refreshAccessToken()` after subscription writes.

**Audit event vocabulary**: `LOGIN`, `OAUTH_LOGIN`, `PASSWORD_RESET`, `USER_CREATED/UPDATED/DELETED`, `ADMIN_PASSWORD_RESET`, `ROLE_PROMOTED/DEMOTED`, `BYO_KEY_ADDED/UPDATED/DELETED`. New events MUST be enum + test. → `pro-user-role-scoped-admin`, `observability`, `auth-jwt-flow`
