# asyncpg `AmbiguousParameterError` on NULL parameters

When a SQLAlchemy `text()` query passes a Python `None` for a parameter that PG would otherwise need to type-infer from context, asyncpg can't determine the type at prepare time and raises:

```
asyncpg.exceptions.AmbiguousParameterError:
  could not determine data type of parameter $4
```

## Trigger pattern

The query has a NULL-tolerant predicate like:

```sql
WHERE (:scope IS NULL OR run.scope = :scope)
```

When `scope=None`, PG sees:
- `:scope IS NULL` — clearly boolean
- `run.scope = :scope` — the right-hand side type can't be inferred (asyncpg sends NULL untyped)

If this is the only place `:scope` appears, asyncpg has no way to resolve the type. The query plan fails before execution.

## Fix — explicit CAST

Cast the parameter to its expected type at every use site:

```sql
WHERE (
    CAST(:scope AS VARCHAR) IS NULL
    OR run.scope = CAST(:scope AS VARCHAR)
)
```

Same for booleans:

```sql
AND (
    NOT CAST(:acted_on_only AS BOOLEAN)
    OR r.acted_on_date IS NOT NULL
)
```

The cast is essentially free at runtime; it just gives asyncpg's prepare phase the type info it needs.

## When this bites

- Helper functions that accept optional filter params (`scope`, `granularity`, `tag`, etc.)
- Default-NULL parameters used to express "match all" semantics
- Boolean flags expressed as `NOT :flag OR ...` patterns

Caller-side workarounds (passing a sentinel like `''` instead of `None`) work but make the SQL uglier and shift the special-case handling into the WHERE clause anyway. The CAST approach keeps the SQL idiomatic.

## SQLAlchemy core ORM avoids this

If you're using SQLAlchemy core (e.g. `select().where(Model.col == val)`), the binding layer always emits typed parameters. The bug is specific to raw `text()` queries with optional NULLs.

## Real incident

`backend/db/pg_stocks.py::get_recommendation_performance_buckets()` — CTE-based raw SQL with optional `:scope` (None | "india" | "us") and `:acted_on_only` (bool). First implementation hit `AmbiguousParameterError: $4` on every call when `scope=None`. Fixed by adding `CAST(:scope AS VARCHAR)` at all uses. Both occurrences in the file (the bucket query + the summary roll-up query) needed the fix.

## See also

- `shared/debugging/sync-async-migration-patterns`
- `shared/debugging/pg-nullpool-sync-async-bridge`
