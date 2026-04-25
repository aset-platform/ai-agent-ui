# Bring-Your-Own-Model (BYOM) — User-Keyed Cascade Override

Lets non-superuser users supply their own Groq and/or Anthropic API
keys. After a per-user free allowance is exhausted, the chat
cascade swaps in user-keyed LangChain clients for the remainder
of the turn. Non-chat flows (recommendations, sentiment, forecast
batches) and superusers always use platform keys.

## Design principles

- **Chat-only.** Scheduled / batch flows never route BYO — the
  shared platform keys amortize those costs.
- **Free allowance first.** 10 lifetime free chat turns per user
  (`users.chat_request_count` INT, clamped at 10 for display).
- **Hard block after exhaustion with no keys.** No soft fallback
  to a less-featured tier — we raise `HTTPException(429)` and let
  the UI instruct the user to configure a key.
- **Superuser-sticky.** `chat_request_count` never bumps for
  superusers, and BYO is never resolved for them.
- **User-settable monthly cap.** `users.byo_monthly_limit` (default
  100 chat turns). Backed by a Redis counter keyed
  `byo:month_counter:{user_id}:{yyyy-mm}` (IST month, 40-day TTL
  for safety). Counter is incremented once per **chat turn** at
  resolve time, not per LLM invocation within a turn.

## Storage

- `user_llm_keys` table — unique `(user_id, provider)`,
  `encrypted_key BYTEA`, `label`, `last_used_at`,
  `request_count_30d`.
- Fernet encryption via `backend/crypto/byo_secrets.py`. Master
  key in `BYO_SECRET_KEY` env (32-byte URL-safe base64). Fail
  fast on missing/invalid master key.
- Plaintext keys never return from any endpoint — only masked
  previews from `mask_key()` (provider-aware: `gsk_****abcd`,
  `sk-ant-****wxyz`).

## Per-request routing — the ContextVar pattern

The cascade override is driven by a module-level `ContextVar` in
`backend/llm_byo.py`:

```python
_byo_ctx: ContextVar[BYOContext | None] = ContextVar(
    "llm_byo_ctx", default=None,
)

@contextmanager
def apply_byo_context(ctx: BYOContext | None):
    if ctx is None:
        yield; return
    token = _byo_ctx.set(ctx)
    try: yield
    finally: _byo_ctx.reset(token)
```

`FallbackLLM._try_model` (Groq path) and the Anthropic fallback
both read the ContextVar via `get_active_byo_context()`; when
the matching key is present, they build a fresh
`ChatGroq(api_key=user_key)` / `ChatAnthropic(api_key=user_key)`,
rebind tools (using `_bound_tools` stashed at `bind_tools()`
time), and invoke. Graceful fallback to platform on build
errors.

`ObservabilityCollector.record_request` accepts
`key_source="platform" | "user"` and stamps it on the Iceberg
`llm_usage` row so the UI can split usage by origin.

## Client cache

Per-user LangChain clients are cached on a
`(provider, model, sha256(key)[:12])` tuple so we don't rebuild
on every `_try_model` invocation within a turn.

## Entry-point wiring

Every chat surface resolves BYO at entry and applies the
ContextVar **inside** the worker thread (not outside — see
`shared/debugging/contextvar-run-in-executor`):

- HTTP `/chat`, `/chat/stream`, LangGraph variants.
- WebSocket `_run_graph` and `_run_legacy`.
- Post-chat `update_summary` LLM call must also run inside the
  same `apply_byo_context()` block — otherwise it leaks to
  platform.

## `chat_request_count` bump rule

Increment only when BYO was **not** active for the turn. Once
BYO kicks in, the free-allowance counter freezes. The
`/admin/metrics?scope=self` response clamps
`free_allowance_used = min(count, 10)` so any historical drift
past the cap isn't shown to the user.

## Raw ChatGroq leak points to audit

Any node that imports `ChatGroq` or `ChatAnthropic` directly
(bypassing `FallbackLLM`) must also consult
`get_active_byo_context()` and swap clients when BYO is active.
Known past offender: `backend/agents/nodes/llm_classifier.py`
(Tier-2 intent classifier) — fixed, but the pattern bears
auditing whenever new nodes are added.

## Audit event vocabulary

`BYO_KEY_ADDED`, `BYO_KEY_UPDATED`, `BYO_KEY_DELETED`.
Auth events also fire on `byo_monthly_limit` changes
(`USER_UPDATED` with `fields_changed=["byo_monthly_limit"]`).
