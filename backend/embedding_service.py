"""Ollama-backed embedding service for memory vectors.

Wraps the Ollama ``/api/embed`` endpoint with
TTL-cached availability checks and graceful fallback
(returns ``None`` when unavailable).

Thread-safe — singleton via :func:`get_embedding_service`.

Typical usage::

    from embedding_service import get_embedding_service

    svc = get_embedding_service()
    vec = svc.embed("User prefers Indian stocks")
    if vec is not None:
        # store in pgvector
"""

from __future__ import annotations

import logging
import threading
import time
from functools import lru_cache

import requests

from config import get_settings

_logger = logging.getLogger(__name__)

# Embedding request timeout (seconds).
_TIMEOUT = 5


class EmbeddingService:
    """Ollama embedding wrapper with health caching.

    Args:
        base_url: Ollama HTTP base URL.
        model: Embedding model name.
        dim: Expected embedding dimension.
        ttl: Health-check cache TTL in seconds.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        dim: int,
        ttl: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._dim = dim
        self._ttl = ttl
        self._available: bool | None = None
        self._checked_at: float = 0.0
        self._lock = threading.Lock()

    @property
    def model(self) -> str:
        """Return the configured embedding model."""
        return self._model

    @property
    def dim(self) -> int:
        """Return the expected embedding dimension."""
        return self._dim

    def is_available(self) -> bool:
        """Check if the embedding model is loaded.

        Caches the result for ``ttl`` seconds.
        """
        now = time.monotonic()
        with self._lock:
            if (
                self._available is not None
                and now - self._checked_at < self._ttl
            ):
                return self._available

        try:
            r = requests.get(
                f"{self._base_url}/api/tags",
                timeout=2,
            )
            r.raise_for_status()
            models = [
                m.get("name", "")
                for m in r.json().get("models", [])
            ]
            # Match by prefix (e.g. "nomic-embed-text"
            # matches "nomic-embed-text:latest").
            available = any(
                m.startswith(self._model)
                for m in models
            )
        except Exception:
            available = False

        with self._lock:
            self._available = available
            self._checked_at = time.monotonic()
        return available

    def embed(self, text: str) -> list[float] | None:
        """Embed a single text string.

        Returns a list of floats (length = ``dim``),
        or ``None`` on any failure.
        """
        if not text or not text.strip():
            return None
        try:
            r = requests.post(
                f"{self._base_url}/api/embed",
                json={
                    "model": self._model,
                    "input": text[:8000],
                },
                timeout=_TIMEOUT,
            )
            r.raise_for_status()
            data = r.json()
            # Ollama /api/embed returns
            # {"embeddings": [[0.1, 0.2, ...]]}
            embeddings = data.get("embeddings")
            if (
                embeddings
                and isinstance(embeddings, list)
                and len(embeddings) > 0
            ):
                vec = embeddings[0]
                if len(vec) == self._dim:
                    return vec
                _logger.warning(
                    "Embedding dim mismatch: "
                    "got %d, expected %d",
                    len(vec),
                    self._dim,
                )
            return None
        except Exception as exc:
            _logger.debug(
                "Embedding failed: %s", exc,
            )
            return None

    def embed_batch(
        self, texts: list[str],
    ) -> list[list[float] | None]:
        """Embed multiple texts sequentially.

        Returns a list aligned with input — each
        entry is a vector or ``None`` on failure.
        """
        return [self.embed(t) for t in texts]


@lru_cache(maxsize=1)
def get_embedding_service() -> EmbeddingService:
    """Return the process-wide EmbeddingService."""
    s = get_settings()
    return EmbeddingService(
        base_url=s.ollama_base_url,
        model=getattr(
            s, "embedding_model", "nomic-embed-text",
        ),
        dim=getattr(s, "embedding_dim", 768),
    )
