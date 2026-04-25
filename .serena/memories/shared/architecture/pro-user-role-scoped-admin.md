# Pro User Role — Tier-Driven + Scoped Admin View

Three-role model for the ASET Platform: `general`, `pro`,
`superuser`. Every future user type folds into this set —
the enum is frozen at the role column.

## Role semantics

| Role | Source | Access |
|---|---|---|
| `general` | default on sign-up | Dashboard + chat, no admin |
| `pro` | auto-sync from paid subscription | Dashboard + chat + Insights (full) + scoped admin (3 tabs) |
| `superuser` | manual grant | Everything (sticky — never auto-demoted) |

## Subscription → role auto-sync

Single pinch point: `auth/repo/user_writes.py::update()`. Every
tier change flows through it via `_safe_update()` in
`subscription_routes.py`. Rule:

- `free` → `general`
- `pro` / `premium` → `pro`
- Superuser stays superuser (never demoted).

Fires `ROLE_PROMOTED` / `ROLE_DEMOTED` audit event post-commit
with metadata `{old_role, new_role, reason:
"subscription_tier_change", new_tier}`.

## Scoped admin — `?scope=self|all`

The same `/admin/*` URL surface is shared between superuser
and pro, but a `scope` query param selects the view:

- Default: `scope="self"` for pro, `scope="all"` for superuser.
- Pro requesting `scope="all"` → 403.
- Superuser requesting `scope="self"` → their own events only.

Applies to:

- `GET /v1/admin/audit-log` — pro sees rows where `actor_user_id
  OR target_user_id = self`.
- `GET /v1/admin/metrics` — `scope=self` returns per-user LLM
  usage (quota, providers, per-model rollup with free/user
  split, daily trend).
- `GET /v1/admin/usage-stats` — same pattern.

## Dependency guards

- `auth/dependencies.py::require_role(*allowed)` — factory
  matching the shape of `require_tier`.
- `pro_or_superuser = require_role("pro", "superuser")` — the
  named alias for the three scope-self endpoints.
- `superuser_only` stays as-is for ~45 other admin endpoints.

## Pro admin tab strip

The pro role sees a **three-tab** strip on `/admin`: My Account,
My Audit Log, My LLM Usage. Superuser sees the full ~7-tab
strip unchanged. Tab filtering in
`frontend/app/(authenticated)/admin/page.tsx` via a
`roles: Role[]` field on each `TabDef`:

```ts
{ id: "users",        roles: ["superuser"] }
{ id: "my_account",   roles: ["pro", "superuser"] }
{ id: "my_audit",     roles: ["pro"] }
{ id: "my_llm",       roles: ["pro"] }
```

Route-level gate at the top of `AdminPageInner`: if
`profile.role === "general"`, `router.replace("/dashboard")`.

## JWT caching caveat

`get_current_user` reads the role from the JWT claim — no DB
re-read per request. A role change only propagates to the
client after `/auth/refresh` (up to 60 min).
`BillingTab.tsx` already calls `refreshAccessToken()` after
Razorpay / Stripe writes so the newly-promoted user sees
admin access immediately.

## Manual promotion

Superuser's Users-tab edit modal has a Role select dropdown
(`general | pro | superuser`). Backed by existing
`PATCH /users/{id}` + `UserUpdateRequest.role:
Literal["general", "pro", "superuser"]` validation.

## Audit event vocabulary

- `ROLE_PROMOTED`
- `ROLE_DEMOTED`
- Existing `USER_UPDATED` — `fields_changed=["role"]` when
  role changed via admin.

## Extending this pattern

- Three tabs is a soft cap — add more scoped views if product
  wants (e.g. `my_transactions`) by declaring
  `roles: ["pro"]` on the TabDef.
- Future paid tiers fold into `pro` unless the product chooses
  to introduce a fourth role — the enum is frozen.
