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
    user_id = user_ctx["user_id"]
    timeout = settings.agent_timeout_seconds

    event_queue: queue.Queue = queue.Queue()

    # ── Choose execution path ─────────────────────
    use_graph = graph is not None and getattr(settings, "use_langgraph", False)

    # Check quota before dispatching
    try:
        from usage_tracker import is_quota_exceeded

        if is_quota_exceeded(user_id):
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
                )
            else:
                _run_legacy(
                    message,
                    history,
                    agent_registry,
                    event_queue,
                    user_id,
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

    # Emit final (tool events already sent in real-time)
    event_queue.put(
        {
            "type": "final",
            "response": result.get("final_response", ""),
            "agent": result.get("current_agent", ""),
        }
    )

    # Track usage
    if user_id:
        try:
            from usage_tracker import increment_usage

            increment_usage(user_id)
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
):
    """Run legacy agent in worker thread."""
    from agents.router import route as _route

    agent_id = _route(message)
    agent = agent_registry.get(agent_id)
    if agent is None:
        return
    for event in agent.stream(message, history):
        event_queue.put(event)

    if user_id:
        try:
            from usage_tracker import increment_usage

            increment_usage(user_id)
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
