---
name: modal-stacking-pattern
description: z-index ladder, PortfolioActionsProvider single-mount pattern, never-route-redirect-to-open-modal rule
type: convention
---

# Modal stacking pattern

Three rules, hardened across Sprint 6 (PortfolioActionsProvider) +
Sprint 7 (transactions modal stacked over slideover).

## Rule 1 — z-index ladder

| Layer | z-index | Examples |
|---|---|---|
| App chrome (header, sidebar) | `z-50` | AppHeader, Sidebar |
| Slideovers | `z-[60]` | RecommendationSlideOver, NewsSlideOver |
| Modals (incl. modals opened from inside slideovers) | `z-[70]` | AddStockModal, EditStockModal, ConfirmModal, PortfolioTransactionsModal |
| Tooltips / popovers (KpiTooltip, ColumnSelector) | `z-[80]` | KpiTooltip |
| Toasts | `z-[90]` | Toast container |

Any new modal opened from a slideover MUST be `z-[70]` — otherwise
it renders BEHIND the slideover (the slideover steals click events).

## Rule 2 — Single-mount via Provider for cross-page modals

Modals that can be triggered from multiple pages (Add/Edit/Delete/
Transactions for portfolio holdings) MUST be mounted ONCE in
`frontend/app/(authenticated)/layout.tsx` via
`PortfolioActionsProvider`.

Pages dispatch via `usePortfolioActions()`:

```tsx
const { openAdd, openEdit, openDelete, openTransactions } =
  usePortfolioActions();
openTransactions("RELIANCE.NS");
```

Provider holds modal state (`open: bool`, `ticker: string | null`,
`mode: "add" | "edit" | "delete" | "tx"`).

### Anti-pattern (do NOT do this)

```tsx
// ❌ Route-redirect to open a modal
router.push(`/dashboard?add=${ticker}`);
// Stacks behind any open slideover, doesn't restore page state,
// breaks back-button.
```

## Rule 3 — View-first, edit-from-within UX

For tabular per-entity rows (portfolio holdings, recommendations):

- Eye icon on the row → opens the **view** modal (read-only details +
  date-sorted history, e.g. `PortfolioTransactionsModal`).
- Inline edit pencil on the row is REMOVED.
- Edit pencil lives INSIDE the view modal, per-row.

Reasoning: prevents accidental edits, gives users context before
modifying, and reduces row clutter.

## Modal lifecycle

```tsx
function MyModal({ isOpen, onClose }: Props) {
  if (!isOpen) return null;     // unmount when closed; preserves
                                 // form-reset semantics on reopen
  return (
    <div className="fixed inset-0 z-[70] ...">
      <div className="absolute inset-0 bg-black/50"
           onClick={onClose} />
      <div className="relative ...">
        {/* content */}
      </div>
    </div>
  );
}
```

`if (!isOpen) return null` is mandatory — `display: none` keeps the
state mounted, leaks listeners, and breaks form reset on reopen.

## Confirm-modal pattern

`ConfirmModal` (in `frontend/components/common/`) takes
`{ title, body, confirmLabel, onConfirm, danger?: boolean }`.

`danger=true` styles the confirm button red and adds an extra
tap-target distance. Use for delete and any irreversible action.

## Idempotent DELETE handlers

Confirm-modal DELETE handlers MUST treat 404 as success
(already-removed) alongside 204. See `useAdminData::deleteKey`.

## Testing modals

Playwright: prefer `getByTestId` over `getByRole("dialog")` because
multiple z-stacked modals are valid `dialog` roles. All modals
expose `data-testid` on the root.

## Related

- `shared/architecture/portfolio-management` — provider + tab routing
- `shared/conventions/tabular-page-pattern` — eye-icon convention
