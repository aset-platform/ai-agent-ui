"""Pydantic request and response models for the backend chat API.

Models
------
- :class:`ChatRequest` — ``POST /chat`` and ``POST /chat/stream`` request body
- :class:`ChatResponse` — ``POST /chat`` response body
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """Request body for ``POST /chat`` and ``POST /chat/stream``.

    Attributes:
        message: The user's latest message text.
        history: Previous conversation turns, oldest first.
            Each element must be a dict with ``"role"``
            (``"user"`` or ``"assistant"``) and ``"content"``.
        agent_id: ID of the agent that should handle the
            request.
        user_id: Optional authenticated user UUID for
            auto-linking tickers analysed during the chat.
    """

    message: str = Field(..., min_length=1, max_length=10_000)
    history: list = Field(
        default=[],
        max_length=100,
    )
    agent_id: str = Field("general", max_length=50, pattern=r"^[a-z_]+$")
    user_id: str | None = Field(
        default=None,
        description=("Authenticated user's ID for ticker linking."),
    )


class ChatResponse(BaseModel):
    """Response body for the ``POST /chat`` endpoint.

    Attributes:
        response: The agent's natural-language reply.
        agent_id: The ID of the agent that produced the response.
    """

    response: str
    agent_id: str
