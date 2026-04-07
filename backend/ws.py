"""WebSocket endpoint for real-time agent streaming.

Provides a persistent bidirectional connection at ``/ws/chat``.
Auth is performed via the first message (not query params) to
avoid token leakage in server logs.

Protocol
--------
1. Client connects to ``/ws/chat``.
2. Client sends ``{"type": "auth", "token": "<JWT>"}`` within
   ``ws_auth_timeout_seconds`` (default 10 s).
3. Server replies ``{"type": "auth_ok"}`` on success.
4. Client sends ``{"type": "chat", ...}`` to start streaming.
5. Server pushes ``thinking``, ``tool_start``, ``tool_done``,
   ``warning``, ``final``, ``error``, ``timeout`` events.
6. ``ping`` / ``pong`` keepalive supported at any time.
7. Re-auth via ``{"type": "auth", "token": "..."}`` mid-session.

Close codes
-----------
- 4001 — authentication failed
- 4002 — authentication timeout
- 4003 — invalid message format

Functions
---------
- :func:`register_ws_routes`
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
import time

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState
from tools._ticker_linker import set_current_user

_logger = logging.getLogger(__name__)


def register_ws_routes(
    app: FastAPI,
    agent_registry,
    executor,
    settings,
    graph=None,
) -> None:
    """Mount the ``/ws/chat`` WebSocket endpoint.

    Args:
        app: The FastAPI application instance.
        agent_registry: Populated
            :class:`~agents.registry.AgentRegistry`.
        executor: :class:`~concurrent.futures.ThreadPoolExecutor`
            for agent execution.
        settings: :class:`~config.Settings` instance.
    """

    @app.websocket("/ws/chat")
    async def ws_chat(ws: WebSocket) -> None:
        """Handle a single WebSocket connection."""
        await ws.accept()
        _logger.info("WS connection accepted")

        user_ctx = await _authenticate(ws, settings)
        if user_ctx is None:
            return  # already closed with error code

        streaming = False

        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    await _close(ws, 4003, "Invalid JSON")
                    return

                msg_type = msg.get("type")

                if msg_type == "ping":
                    await ws.send_json({"type": "pong"})

                elif msg_type == "auth":
                    # Re-auth mid-session.
                    user_ctx = _validate_token(
                        msg.get("token", ""),
                    )
                    if user_ctx is None:
                        await _close(
                            ws,
                            4001,
                            "Re-auth failed",
                        )
                        return
                    await ws.send_json({"type": "auth_ok"})

                elif msg_type == "chat":
                    if streaming:
                        await ws.send_json(
                            {
                                "type": "error",
                                "message": (
                                    "Already streaming — wait "
                                    "for the current response"
                                ),
                            }
                        )
                        continue

                    streaming = True
                    try:
                        await _handle_chat(
                            ws,
                            msg,
                            user_ctx,
                            agent_registry,
                            executor,
                            settings,
                            graph=graph,
                        )
                    finally:
                        streaming = False

                else:
                    await _close(
                        ws,
                        4003,
                        f"Unknown type: {msg_type}",
                    )
                    return

        except WebSocketDisconnect:
            _logger.info("WS client disconnected")


async def _authenticate(ws, settings):
    """Wait for an auth message and validate the JWT.

    Args:
        ws: The WebSocket connection.
        settings: App settings (for timeout).

    Returns:
        A dict with ``user_id``, ``email``, ``role`` on
        success, or ``None`` if auth failed (connection
        already closed).
    """
    timeout = settings.ws_auth_timeout_seconds
    try:
        raw = await asyncio.wait_for(
            ws.receive_text(),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        await _close(ws, 4002, "Auth timeout")
        return None
    except WebSocketDisconnect:
        return None

    try:
        msg = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        await _close(ws, 4003, "Invalid JSON")
        return None

    if msg.get("type") != "auth":
        await _close(
            ws,
            4003,
            "First message must be auth",
        )
        return None

    token = msg.get("token", "")
    user_ctx = _validate_token(token)
    if user_ctx is None:
        await _close(ws, 4001, "Invalid token")
        return None

    await ws.send_json({"type": "auth_ok"})
    _logger.info(
        "WS authenticated user_id=%s",
        user_ctx["user_id"],
    )
    return user_ctx


def _validate_token(token: str) -> dict | None:
    """Decode a JWT and return user context or ``None``.

    Args:
        token: Raw JWT string.

    Returns:
        Dict with ``user_id``, ``email``, ``role`` or ``None``.
    """
    from auth.dependencies import get_auth_service

    try:
        svc = get_auth_service()
        payload = svc.decode_token(
            token,
            expected_type="access",
        )
    except Exception:
        return None

    user_id = payload.get("sub")
    role = payload.get("role")
    if not user_id or not role:
        return None

    return {
        "user_id": user_id,
        "email": payload.get("email", ""),
        "role": role,
    }


async def _handle_chat(
    ws,
    msg,
    user_ctx,
    agent_registry,
    executor,
    settings,
    graph=None,
) -> None:
    """Run the agent streaming loop over WebSocket.

    Uses the same ``Thread`` + ``queue.Queue`` bridge as
    the HTTP ``/chat/stream`` endpoint in ``routes.py``.

    When ``graph`` is provided and ``use_langgraph`` is
    enabled, uses the LangGraph supervisor graph instead
    of the legacy agent dispatch.
    """
    message = msg.get("message", "")
    history = msg.get("history", [])
    session_id = msg.get("session_id", "")
    user_id = user_ctx["user_id"]
    timeout = settings.agent_timeout_seconds

    event_queue: queue.Queue = queue.Queue()

    # Capture the running event loop for async
    # fire-and-forget tasks from worker threads.
    import asyncio

    _main_loop = asyncio.get_running_loop()

    # ── Choose execution path ─────────────────────
    use_graph = graph is not None and getattr(settings, "use_langgraph", False)

    # Check quota before dispatching
    try:
        from usage_tracker import is_quota_exceeded

        if await is_quota_exceeded(user_id):
            event_queue.put(
                {
                    "type": "error",
                    "message": (
                        "Monthly analysis quota exceeded."
                        " Upgrade your plan for more."
                    ),
                }
            )
            event_queue.put(None)
            return event_queue
    except Exception:
        _logger.debug(
            "Quota check failed for WS user",
            exc_info=True,
        )

    def run() -> None:
        """Execute in a worker thread."""
        set_current_user(user_id)
        try:
            if use_graph:
                _run_graph(
                    graph,
                    message,
                    history,
                    user_id,
                    event_queue,
                    session_id=session_id,
                    main_loop=_main_loop,
                )
            else:
                _run_legacy(
                    message,
                    history,
                    agent_registry,
                    event_queue,
                    user_id,
                    main_loop=_main_loop,
                )
        except Exception as exc:
            _logger.warning(
                "WS worker error: %s",
                exc,
                exc_info=True,
            )
            event_queue.put(
                {
                    "type": "error",
                    "message": str(exc),
                }
            )
        finally:
            event_queue.put(None)

    worker = threading.Thread(
        target=run,
        daemon=True,
    )
    worker.start()

    start = time.time()
    while True:
        elapsed = time.time() - start
        if elapsed >= timeout:
            await ws.send_json(
                {
                    "type": "timeout",
                    "message": ("Agent timed out" f" after {timeout}s"),
                }
            )
            break

        remaining = timeout - elapsed
        try:
            item = await asyncio.get_running_loop().run_in_executor(
                executor,
                lambda: event_queue.get(
                    timeout=min(remaining, 0.5),
                ),
            )
        except queue.Empty:
            continue

        if item is None:
            break

        # Parse NDJSON or dict → send as JSON
        if isinstance(item, dict):
            await ws.send_json(item)
        else:
            line = item.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                await ws.send_json(event)
            except (json.JSONDecodeError, TypeError):
                await ws.send_text(line)

    worker.join(timeout=2)


def _run_graph(
    graph,
    message,
    history,
    user_id,
    event_queue,
    session_id: str = "",
    main_loop=None,
):
    """Run LangGraph supervisor in worker thread."""
    from langchain_core.messages import (
        AIMessage,
        HumanMessage,
    )

    msgs = []
    for h in history or []:
        role = h.get("role", "user")
        content = h.get("content", "")
        if role == "assistant":
            msgs.append(AIMessage(content=content))
        else:
            msgs.append(
                HumanMessage(content=content),
            )
    msgs.append(HumanMessage(content=message))

    # Build user context (currency/market mix)
    from user_context import build_user_context

    user_ctx = build_user_context(user_id or "")

    # Retrieve memories from pgvector (async → sync bridge)
    _memories: list[dict] = []
    if main_loop and user_id:
        try:
            import asyncio

            from memory_retriever import (
                retrieve_memories,
            )

            fut = asyncio.run_coroutine_threadsafe(
                retrieve_memories(
                    user_id, message, top_k=5,
                ),
                main_loop,
            )
            _memories = fut.result(timeout=3)
        except Exception:
            _logger.debug(
                "Memory retrieval skipped",
                exc_info=True,
            )

    input_state = {
        "messages": msgs,
        "user_input": message,
        "user_id": user_id or "",
        "history": history or [],
        "user_context": user_ctx,
        "intent": "",
        "next_agent": "",
        "current_agent": "",
        "tickers": [],
        "data_sources_used": [],
        "was_local_sufficient": True,
        "tool_events": [],
        "session_id": session_id,
        "retrieved_memories": _memories,
        "final_response": "",
        "error": None,
        "start_time_ns": 0,
    }

    # Wire real-time event sink so sub-agent tool
    # events push to the WS event queue immediately.
    from agents.sub_agents import set_event_sink

    set_event_sink(event_queue.put)

    try:
        result = graph.invoke(input_state)
    finally:
        set_event_sink(None)

    # Update conversation context for follow-ups.
    if session_id:
        try:
            from agents.conversation_context import (
                ConversationContext,
                context_store,
                update_summary,
            )

            ctx = context_store.get(session_id)
            if ctx is None:
                ctx = ConversationContext(
                    session_id=session_id,
                )
            ctx.last_agent = result.get(
                "current_agent", "",
            )
            ctx.last_intent = result.get("intent", "")
            tickers = result.get("tickers", [])
            ctx.current_topic = (
                f"{', '.join(tickers)} "
                f"{ctx.last_intent}"
                if tickers else ctx.last_intent
            )
            for t in tickers:
                if t not in ctx.tickers_mentioned:
                    ctx.tickers_mentioned.append(t)
            try:
                update_summary(
                    ctx, message,
                    result.get("final_response", ""),
                )
            except Exception:
                ctx.turn_count += 1
            context_store.upsert(session_id, ctx)
            _logger.debug(
                "Context updated for session %s"
                " (turn %d, agent=%s)",
                session_id,
                ctx.turn_count,
                ctx.last_agent,
            )
        except Exception:
            _logger.debug(
                "WS context update failed",
                exc_info=True,
            )

    # ── Post-response async tasks (fire-and-forget) ──
    _final_resp = result.get("final_response", "")
    _cur_agent = result.get("current_agent", "")

    if main_loop and session_id:
        import asyncio

        # Memory extraction (summary + facts → pgvector)
        try:
            from memory_extractor import (
                extract_and_store_memories,
            )

            _summary = ""
            try:
                _ctx_r = context_store.get(session_id)
                if _ctx_r:
                    _summary = _ctx_r.summary
            except Exception:
                pass

            asyncio.run_coroutine_threadsafe(
                extract_and_store_memories(
                    user_id=user_id,
                    session_id=session_id,
                    user_input=message,
                    response=_final_resp,
                    summary=_summary,
                    turn_number=(
                        _ctx_r.turn_count
                        if _ctx_r
                        else 1
                    ),
                    agent_id=_cur_agent,
                ),
                main_loop,
            )
        except Exception:
            _logger.debug(
                "Memory extraction dispatch failed",
                exc_info=True,
            )

        # Per-answer Iceberg persistence
        try:
            from audit_persistence import (
                persist_chat_turn,
            )

            asyncio.run_coroutine_threadsafe(
                persist_chat_turn(
                    user_id=user_id,
                    session_id=session_id,
                    user_input=message,
                    response=_final_resp,
                    agent_id=_cur_agent,
                ),
                main_loop,
            )
        except Exception:
            _logger.debug(
                "Chat turn persistence failed",
                exc_info=True,
            )

    # Emit final (tool events already sent in real-time)
    final_event = {
        "type": "final",
        "response": _final_resp,
        "agent": _cur_agent,
        "memory_used": len(_memories) > 0,
    }
    actions = result.get("response_actions", [])
    if actions:
        final_event["actions"] = actions
    event_queue.put(final_event)

    # Track usage
    if user_id and main_loop:
        try:
            from usage_tracker import increment_usage

            asyncio.run_coroutine_threadsafe(
                increment_usage(user_id),
                main_loop,
            )
        except Exception:
            _logger.debug(
                "Usage tracking failed for %s",
                user_id,
                exc_info=True,
            )


def _run_legacy(
    message,
    history,
    agent_registry,
    event_queue,
    user_id=None,
    main_loop=None,
):
    """Run legacy agent in worker thread."""
    from agents.router import route as _route

    agent_id = _route(message)
    agent = agent_registry.get(agent_id)
    if agent is None:
        return
    for event in agent.stream(message, history):
        event_queue.put(event)

    if user_id and main_loop:
        try:
            import asyncio

            from usage_tracker import increment_usage

            asyncio.run_coroutine_threadsafe(
                increment_usage(user_id),
                main_loop,
            )
        except Exception:
            _logger.debug(
                "Usage tracking failed for %s",
                user_id,
                exc_info=True,
            )


async def _close(ws, code: int, reason: str) -> None:
    """Close the WebSocket if still connected.

    Args:
        ws: The WebSocket connection.
        code: WebSocket close code.
        reason: Human-readable close reason.
    """
    _logger.info("WS close code=%d reason=%s", code, reason)
    if ws.client_state == WebSocketState.CONNECTED:
        await ws.close(code=code, reason=reason)
