"""Pydantic request and response models for the backend chat API.

Models
------
- :class:`ChatRequest` — ``POST /chat`` and ``POST /chat/stream`` request body
- :class:`ChatResponse` — ``POST /chat`` response body
"""

from pydantic import BaseModel


class ChatRequest(BaseModel):
    """Request body for the ``POST /chat`` and ``POST /chat/stream`` endpoints.

    Attributes:
        message: The user's latest message text.
        history: Previous conversation turns, oldest first.  Each element
            must be a dict with ``"role"`` (``"user"`` or ``"assistant"``)
            and ``"content"`` keys.
        agent_id: ID of the agent that should handle the request.
    """

    message: str
    history: list = []
    agent_id: str = "general"


class ChatResponse(BaseModel):
    """Response body for the ``POST /chat`` endpoint.

    Attributes:
        response: The agent's natural-language reply.
        agent_id: The ID of the agent that produced the response.
    """

    response: str
    agent_id: str
