# Algo Trading v2 — Slice V2-1: Live Kite WebSocket Multiplexer

> **STATUS:** SKELETON — expand into a full TDD task-by-task plan via `superpowers:writing-plans` when this session is about to start. Use V2-0's plan as a reference for task granularity.

> **For agentic workers:** REQUIRED SUB-SKILL: After expansion, use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement.

**Goal:** Replace today's replay-fixture-only `LiveTickSource` with a per-user persistent Kite Connect WebSocket multiplexer that fans out to many strategies, survives reconnects via gap-fill, and drives the existing paper runtime in `source="live-ws"` mode.

**Architecture:** Single `KiteWsMultiplexer` instance per user, started lazily on first paper-mode-Live launch and torn down when the user has zero active strategies. Subscribes to instrument tokens via `kite_ticker.subscribe()`; per-strategy queues fan out from a single in-process pubsub. On disconnect, exponential-backoff reconnect; on reconnect, pull missing window from Kite historical 1m and replay through each queue's resampler.

**Tech Stack:** Python 3.12 / `kiteconnect` SDK / `asyncio` / `pytest-asyncio`. Mocked WS server for tests. No new tables; new event types under `algo.events`.

**Spec:** `docs/superpowers/specs/2026-05-09-algo-trading-v2-design.md` — Slice V2-1 (§9.1, §7.2).

**Branch:** `feature/algo-trading-v2-slice-1-live-ws` off `feature/algo-trading-v2-integration`.

**Depends on:** V2-0 merged (Keychain BYO_SECRET_KEY in place; otherwise Kite credentials may not decrypt cleanly under the new master key).

---

## File Structure

**Backend (new):**
- `backend/algo/broker/ws_multiplexer.py` — `KiteWsMultiplexer` class.
- `backend/algo/broker/ws_registry.py` — process-local registry mapping `user_id → KiteWsMultiplexer`.
- `backend/algo/broker/ws_gap_fill.py` — Kite historical-1m gap-fill helper.
- `backend/algo/tests/test_ws_multiplexer.py` — happy path, subscribe/unsubscribe, fan-out.
- `backend/algo/tests/test_ws_reconnect.py` — disconnect → backoff → reconnect → gap-fill.
- `backend/algo/tests/test_ws_backpressure.py` — bounded queue, drop-oldest, warning log.
- `backend/algo/tests/fixtures/mock_kite_ws_server.py` — async fake server.

**Backend (modified):**
- `backend/algo/paper/runtime.py` — accept `source: Literal["replay", "live-ws"]`; when `"live-ws"`, subscribe via the registry instead of opening a fixture.
- `backend/algo/routes/paper.py` — `POST /v1/algo/paper/runs` accepts `source` body field.
- `backend/algo/event_writer.py` — register new event types: `ws_connected`, `ws_disconnected`, `ws_gap_filled`.
- `backend/main.py` — graceful shutdown closes all `KiteWsMultiplexer` instances in the registry.

**Frontend (modified):**
- `frontend/components/algo-trading/ActiveRunsPanel.tsx` — start-run form gets a "Source" radio: replay-fixture (default) | live-WS (gated on Kite connected).
- `frontend/hooks/usePaperRuns.ts` — extend `StartPaperRunRequest` with `source`.

**E2E:**
- `e2e/tests/frontend/algo-trading-paper-live-ws.spec.ts` — mocked Kite WS server (fixture); paper run with `source=live-ws`; assert `ws_connected` event lands; teardown.

---

## High-level task list (expand into TDD steps before session start)

1. **Mock Kite WS server fixture** (CI-safe, no real network).
2. **`KiteWsMultiplexer` skeleton** — connect, authenticate via `load_secret("algo_kite_api_secret")` + the user's stored access_token, subscribe to a static set of tokens.
3. **Fan-out queues** — `asyncio.Queue` per (user, strategy); single WS message dispatched to all subscribers of that token.
4. **Subscribe/unsubscribe lifecycle** — strategy start/stop drives `kite_ticker.subscribe()` / `unsubscribe()`; reference-count tokens to avoid mid-strategy unsubscribes.
5. **Reconnect with exponential backoff** — 1s, 2s, 4s, ..., cap 60s; emit `ws_disconnected` + reason on each loss; emit `ws_connected` on each gain.
6. **Gap-fill on reconnect** — track last tick timestamp per token; on reconnect, call `kite.historical_data(token, from=last_ts, to=now, interval="minute")`; replay through per-strategy resampler; emit `ws_gap_filled` with `missing_seconds` + `ticks_replayed`.
7. **Backpressure** — bounded queues; drop oldest on overflow; emit a WARNING log + bump a Prometheus counter (existing `observability` collector).
8. **Wire into `paper.runtime`** — `source="live-ws"` branch; subscribe via registry; on shutdown, decrement subscription ref-count.
9. **Frontend source toggle** — radio in start-run form; gate `live-ws` option on Kite-connected status (existing `useBrokerStatus` hook).
10. **Process shutdown closes all WS** — register a cleanup hook on FastAPI lifespan exit.
11. **Documentation** — update `docs/algo-trading/paper-trading.md` "Available sources" section.

---

## Acceptance

- [ ] Paper run with `source=live-ws` survives a 30-second simulated WS outage with zero data loss (gap-filled).
- [ ] Two simultaneous strategies on the same user share a single WS connection.
- [ ] Tearing down all strategies for a user closes their WS multiplexer cleanly.
- [ ] Backpressure drop emits a WARNING log + observable counter increment; never silently drops.
- [ ] CI runs without real Kite credentials (mock fixture only).
- [ ] Frontend toggle disabled with tooltip if Kite not connected.
- [ ] No regressions in existing replay-fixture paper runs.

---

## Risks (slice-specific)

- **Real Kite WS rate-limiting** during dev — use the mock server for iteration; touch real Kite only for the manual smoke at end of session.
- **Gap-fill Kite historical limits** — Kite historical-1m allows max 60 days lookback. We're filling seconds-to-minutes worth, so well inside limits, but log + truncate if `missing_seconds > 3600` (an hour-long outage means we abandon gap-fill and emit a `ws_gap_too_large` warning instead).
- **Async reentrancy in fan-out** — strict use of `asyncio.Queue` (not raw lists); test with stress fixture sending 1000 ticks/sec.

---

## Out of scope for V2-1

- Live order placement (V2-5).
- Reconciliation against broker positions (V2-3).
- Walk-forward harness (V2-2).
- F&O instrument tokens (v3).
- Multi-user WS pooling beyond per-user singletons (v3).
