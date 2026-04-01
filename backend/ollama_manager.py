"""Ollama local-LLM lifecycle manager.

Owns health probing with TTL-based caching, model
load/unload via the Ollama HTTP API, and status queries.

Thread-safe -- cache mutations use :class:`threading.Lock`.

Typical usage::

    from ollama_manager import get_ollama_manager

    mgr = get_ollama_manager()
    if mgr.is_available():
        mgr.load_profile("reasoning")
"""

from __future__ import annotations

import logging
import threading
import time
from functools import lru_cache
from typing import Any

import requests

from config import get_settings

_logger = logging.getLogger(__name__)

# Profile → (model, keep_alive, num_ctx).
_PROFILES: dict[str, tuple[str, str, int]] = {
    "coding": ("qwen2.5-coder:14b", "2h", 16384),
    "reasoning": ("gpt-oss:20b", "1h", 8192),
}


class OllamaManager:
    """Cached Ollama server probe + model lifecycle.

    Health probes are cached for ``ollama_health_cache_ttl``
    seconds (default 30).  At most one HTTP round-trip per
    TTL window per process.
    """

    def __init__(self) -> None:
        s = get_settings()
        self._base_url: str = s.ollama_base_url
        self._ttl: float = float(s.ollama_health_cache_ttl)

        self._lock = threading.Lock()
        self._valid_until: float = 0.0
        self._available: bool = False
        self._models: list[str] = []

    # ── Cache probe ─────────────────────────────────

    def _probe(self) -> None:
        """Refresh cached state if TTL expired."""
        with self._lock:
            if time.monotonic() < self._valid_until:
                return
            # Mark cache as refreshing (hold lock).
            self._valid_until = (
                time.monotonic() + self._ttl
            )

        # HTTP calls outside lock (blocking, 2s timeout).
        avail = False
        models: list[str] = []
        try:
            r = requests.get(
                f"{self._base_url}/",
                timeout=2,
            )
            if not r.ok:
                return
            avail = True
            r2 = requests.get(
                f"{self._base_url}/api/ps",
                timeout=2,
            )
            for m in r2.json().get("models", []):
                name = m.get("name", "")
                if name:
                    models.append(name)
        except Exception:
            avail = False
            models = []
        finally:
            with self._lock:
                self._available = avail
                self._models = models

    # ── Public read-only queries ────────────────────

    def is_available(self) -> bool:
        """Return *True* if Ollama server is reachable."""
        self._probe()
        return self._available

    def is_model_loaded(self, model: str) -> bool:
        """Return *True* if *model* is in memory."""
        self._probe()
        return model in self._models

    def get_status(self) -> dict[str, Any]:
        """Loaded models with RAM usage (for admin API)."""
        if not self.is_available():
            return {"available": False, "models": []}
        try:
            r = requests.get(
                f"{self._base_url}/api/ps",
                timeout=2,
            )
            out: list[dict[str, Any]] = []
            for m in r.json().get("models", []):
                vram = m.get("size_vram", 0)
                out.append({
                    "name": m.get("name", ""),
                    "ram_gb": round(
                        vram / 1_073_741_824, 2,
                    ),
                })
            return {"available": True, "models": out}
        except Exception:
            return {"available": False, "models": []}

    # ── Model lifecycle ─────────────────────────────

    def load_profile(
        self,
        profile: str,
    ) -> dict[str, Any]:
        """Unload current models, then load *profile*.

        Raises:
            ValueError: Unknown profile name.
            RuntimeError: Ollama API error.
        """
        if profile not in _PROFILES:
            raise ValueError(
                f"Unknown profile: {profile!r}. "
                f"Valid: {list(_PROFILES)}"
            )
        model, keep_alive, num_ctx = _PROFILES[profile]

        self.unload_all()

        _logger.info(
            "Loading Ollama profile %r → %s "
            "(keep_alive=%s, ctx=%d)",
            profile,
            model,
            keep_alive,
            num_ctx,
        )
        try:
            r = requests.post(
                f"{self._base_url}/api/generate",
                json={
                    "model": model,
                    "prompt": "",
                    "keep_alive": keep_alive,
                    "options": {"num_ctx": num_ctx},
                },
                timeout=120,
            )
            r.raise_for_status()
        except Exception as exc:
            raise RuntimeError(
                f"Failed to load {model}: {exc}"
            ) from exc

        self.invalidate_cache()
        return {
            "loaded": model,
            "keep_alive": keep_alive,
        }

    def unload_all(self) -> dict[str, Any]:
        """Unload every loaded model (free RAM).

        Raises:
            RuntimeError: Ollama API error.
        """
        try:
            r = requests.get(
                f"{self._base_url}/api/ps",
                timeout=2,
            )
            names = [
                m["name"]
                for m in r.json().get("models", [])
            ]
        except Exception:
            names = []

        if not names:
            self.invalidate_cache()
            return {"unloaded": []}

        for name in names:
            _logger.info("Unloading Ollama model %s", name)
            try:
                requests.post(
                    f"{self._base_url}/api/generate",
                    json={
                        "model": name,
                        "keep_alive": 0,
                    },
                    timeout=10,
                )
            except Exception:
                _logger.warning(
                    "Failed to unload %s",
                    name,
                    exc_info=True,
                )
        time.sleep(2)
        self.invalidate_cache()
        return {"unloaded": names}

    def invalidate_cache(self) -> None:
        """Force next ``_probe()`` to hit the server."""
        with self._lock:
            self._valid_until = 0.0


@lru_cache
def get_ollama_manager() -> OllamaManager:
    """Return the process-wide :class:`OllamaManager`."""
    return OllamaManager()
