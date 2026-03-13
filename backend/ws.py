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
) -> None:
    """Run the agent streaming loop over WebSocket.

    Uses the same ``Thread`` + ``queue.Queue`` bridge as the
    HTTP ``/chat/stream`` endpoint in ``routes.py``.

    Args:
        ws: The WebSocket connection.
        msg: The parsed chat message dict.
        user_ctx: Authenticated user context.
        agent_registry: The agent registry.
        executor: Thread pool executor.
        settings: App settings.
    """
    agent_id = msg.get("agent_id", "general")
    agent = agent_registry.get(agent_id)
    if agent is None:
        await ws.send_json(
            {
                "type": "error",
                "message": f"Agent '{agent_id}' not found",
            }
        )
        return

    message = msg.get("message", "")
    history = msg.get("history", [])
    user_id = msg.get(
        "user_id",
        user_ctx.get("user_id"),
    )
    timeout = settings.agent_timeout_seconds

    event_queue: queue.Queue = queue.Queue()

    def run() -> None:
        """Execute agent.stream() in a worker thread."""
        set_current_user(user_id)
        try:
            for event in agent.stream(message, history):
                event_queue.put(event)
        except Exception:
            pass
        finally:
            event_queue.put(None)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()

    start = time.time()
    while True:
        elapsed = time.time() - start
        if elapsed >= timeout:
            await ws.send_json(
                {
                    "type": "timeout",
                    "message": (f"Agent timed out after {timeout}s"),
                }
            )
            break

        remaining = timeout - elapsed
        try:
            item = await asyncio.get_event_loop().run_in_executor(
                executor,
                lambda: event_queue.get(
                    timeout=min(remaining, 0.5),
                ),
            )
        except queue.Empty:
            continue

        if item is None:
            break

        # item is an NDJSON string ending with \n — parse
        # and send as JSON over WS.
        line = item.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
            await ws.send_json(event)
        except (json.JSONDecodeError, TypeError):
            await ws.send_text(line)

    worker.join(timeout=2)


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
