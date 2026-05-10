# Algo Trading v2 — Slice OBS-1: WS Health Endpoint + Status Dot

> **STATUS:** SKELETON — expand into a full TDD task-by-task plan via `superpowers:writing-plans` when this session starts.

> **For agentic workers:** REQUIRED SUB-SKILL: After expansion, use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans`.

**Goal:** Add `GET /v1/algo/live/ws-health` exposing in-memory `KiteWsMultiplexer` state, plus a green/amber/red traffic-light dot in the Trading tab Live segment header so Mon 09:15 IST smoke is a glance check, not a log-grep session.

**Architecture:** Pure read-only snapshot endpoint over the existing per-user multiplexer singleton via a new `get_multiplexer_if_exists(user_id)` registry helper that doesn't auto-create. Frontend SWR polls 10s; dot color from `tick_age_seconds` thresholds (30s green, 120s amber, else red).

**Tech Stack:** Python 3.12 / FastAPI / Pydantic v2 / SWR (frontend).

**Spec:** `docs/superpowers/specs/2026-05-10-algo-v2-observability-postback-design.md` — §3.4 (endpoint), §3.5 (frontend dot).

**Branch:** `feature/algo-v2-obs-1-ws-health` off `feature/algo-trading-v2-integration`.

**Depends on:** none — fully decoupled. Multiplexer already exists from V2-1.

**Estimated SP:** 3.

---

## File Structure

**Backend (modified):**
- `backend/algo/broker/ws_multiplexer.py` — add `last_tick_at: datetime | None`, `tick_count_today: int`, `health_snapshot() -> dict` method.
- `backend/algo/broker/ws_registry.py` — add `get_multiplexer_if_exists(user_id) -> KiteWsMultiplexer | None`.
- `backend/algo/routes/live.py` — add `GET /ws-health` endpoint + `WsHealth` Pydantic response model.
- `backend/algo/jobs/__init__.py` (or wherever `@register_job` lives) — register `reset_tick_count_daily` at IST midnight.

**Backend (new tests):**
- `backend/algo/tests/test_ws_health_endpoint.py` — `test_ws_health_no_mux` (returns connected=false), `test_ws_health_snapshot` (with mocked mux), `test_ws_health_age_seconds_boundary` (exact boundaries 30s, 120s).
- `backend/algo/tests/test_ws_registry_if_exists.py` — `test_get_if_exists_returns_none`, `test_get_if_exists_returns_existing`.
- `backend/algo/tests/test_multiplexer_health_snapshot.py` — `test_health_snapshot_initial`, `test_health_snapshot_after_tick`, `test_tick_count_resets_at_midnight`.

**Frontend (new):**
- `frontend/components/algo-trading/LiveWsHealthDot.tsx` — 8px dot + tooltip.
- `frontend/hooks/useWsHealth.ts` — SWR @ 10s, `revalidateOnFocus: false`.
- `e2e/utils/selectors.ts` — register `live-ws-health-dot` testid.

**Frontend (modified):**
- `frontend/components/algo-trading/PaperTab.tsx` — mount `<LiveWsHealthDot />` in Live segment header.

**E2E:**
- `e2e/tests/frontend/algo-trading-ws-health.spec.ts` — dot renders, color matches mocked tick age.

---

## High-level task list (expand at session start)

1. **Multiplexer health properties** — add `last_tick_at` (set in tick callback), `tick_count_today` (incremented in tick callback). Test: `test_health_snapshot_initial` returns nulls; after a synthetic tick, returns updated values.
2. **`health_snapshot()` method** — returns dict matching the `WsHealth` Pydantic shape. Read-only, atomic. Test in isolation.
3. **`get_multiplexer_if_exists`** — new helper in registry. Mirrors `get_or_create_multiplexer` minus the create. Test both branches.
4. **`reset_tick_count_daily` job** — `@register_job` at IST midnight; resets `tick_count_today` for all live multiplexers in the registry.
5. **Endpoint** — `WsHealth` Pydantic model + route handler that calls `get_multiplexer_if_exists` and either returns the snapshot or the disconnected default.
6. **Endpoint test** — full FastAPI test client; mocked registry; both branches.
7. **`useWsHealth` hook** — SWR @ 10s, returns `{ data, isLoading, error }`. Falls back to disconnected on error.
8. **`LiveWsHealthDot` component** — 8px round dot, color from age; hover tooltip via existing `<Tooltip>`. Compute color via `statusFromAge` pure function (unit-testable).
9. **Mount in `PaperTab`** — Live segment header, right of "Live mode" label.
10. **E2E** — seed a multiplexer with a recent tick; assert green dot; advance time 31s; assert amber.
11. **Documentation** — `docs/algo-trading/observability.md` adds "WS health dot" section explaining the color thresholds.

---

## Acceptance

- [ ] `GET /v1/algo/live/ws-health` with no active mux → `{connected: false, subscriber_count: 0, ...}` 200.
- [ ] With active mux + recent tick → `connected: true, tick_age_seconds <= 5`.
- [ ] Endpoint NEVER instantiates a multiplexer (regression test).
- [ ] Status dot color: green (<30s), amber (30-120s), red (>120s OR disconnected).
- [ ] Tooltip text on hover matches "Connected. Last tick Ns ago. M strategies subscribed (T tokens). C ticks today."
- [ ] No regression on existing live runtime ticks (multiplexer changes are pure read-only adds).
- [ ] `tick_count_today` resets at IST midnight via scheduled job.

---

## Out of scope for OBS-1

- Postback URL backend (OBS-2).
- ngrok service in docker-compose (OBS-3).
- Postback panel UI (OBS-4).
- Per-second tick rate windows (deferred to v3 per spec §10).
- WS health alert email (deferred per spec §10).
