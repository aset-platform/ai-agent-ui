# ContextVar Propagation Through `run_in_executor`

## Problem

`asyncio.loop.run_in_executor(executor, fn)` does **not** copy
the calling task's `ContextVar` values into the worker thread
by default. If you set a ContextVar in an async route handler
and then dispatch work to a `ThreadPoolExecutor`, the worker
sees the ContextVar as empty.

This bites any feature that uses ContextVars for per-request
state that must reach code running in a thread pool — common
for LangChain/LangGraph pipelines where `graph.invoke()`
ultimately lands in a thread via `run_in_executor`.

## Symptom

The feature appears to work *some* of the time. E.g. BYOM's
cascade override landed `key_source="user"` on direct-path
calls but kept stamping `"platform"` on anything that ran
deeper in the graph — ContextVar was unset by the time the
deeper call fired.

## Fix

Set the ContextVar **inside** the worker thread, not outside.
A scoped context manager handles this cleanly:

```python
@contextmanager
def apply_byo_context(ctx):
    if ctx is None: yield; return
    token = _byo_ctx.set(ctx)
    try: yield
    finally: _byo_ctx.reset(token)
```

At the entry point:

```python
byo_ctx = await resolve_byo_for_chat(...)  # async, outside worker

def _worker():
    with apply_byo_context(byo_ctx):   # set INSIDE worker thread
        return graph.invoke(state)

await loop.run_in_executor(executor, _worker)
```

## Post-chat side-effect trap

Any post-chat LLM call that also needs the ContextVar (summary
generation, context persistence, secondary tool runs) must live
**inside** the same `with` block. BYOM had a bug where
`update_summary()` ran after the block exited and silently
leaked to platform keys. Always scope the entire chain:

```python
with apply_byo_context(ctx):
    result = graph.invoke(state)
    update_summary(session, result)   # must be inside, not after
```

## Alternative: copy_context()

`contextvars.copy_context().run(fn)` captures the current
context and re-applies it in the target. Can be used in
place of the explicit `with` block when the worker is a
simple callable:

```python
ctx = contextvars.copy_context()
loop.run_in_executor(executor, ctx.run, _worker)
```

The explicit `apply_*_context()` form is preferred for
readability when multiple scopes need orchestrating.

## Where this applies

- Chat HTTP handlers (`/chat`, `/chat/stream`, LangGraph
  variants) in `backend/routes.py`.
- WebSocket chat worker in `backend/ws.py` (`_run_graph`,
  `_run_legacy`).
- Any new async→thread bridge introduced for agent pipelines.

## Testing

Unit tests should cover:
- ContextVar is `None` when no context applied.
- `apply_X_context(ctx)` exposes it.
- Exiting the block resets it (so subsequent requests start
  clean).

See `tests/backend/test_byo_routing.py::TestBYOContextVar`
for the reference pattern.
