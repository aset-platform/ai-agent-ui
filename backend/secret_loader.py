"""Single-source-of-truth secret reader.

Mirrors a production CSI Secrets-Store driver: the container
reads the secret from a mounted file path. Locally on macOS we
materialize the file from the Keychain (see
``scripts/secrets/materialize.sh``); in CI we fall back to an
environment variable; in prod we'd point the same path at a real
CSI mount (Vault, AWS Secrets Manager, etc.) without changing
any application code.

Resolution order:

1. ``/run/secrets/<name>`` — the docker-compose / k8s standard
   mount path. Wins if present.
2. ``<NAME_UPPER_SNAKE>`` env var — fallback for environments
   without the file mount (CI, ad-hoc local).
3. ``default`` argument — final fallback (None by default).

Reads are cached after first hit so repeated callers don't
re-read disk.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

_logger = logging.getLogger(__name__)

_SECRETS_DIR = Path("/run/secrets")


def _env_name(secret_name: str) -> str:
    """Convert ``algo_kite_api_secret`` → ``ALGO_KITE_API_SECRET``."""
    return secret_name.upper().replace("-", "_")


@lru_cache(maxsize=64)
def load_secret(
    name: str,
    *,
    default: str | None = None,
) -> str | None:
    """Return the secret value or *default*.

    Args:
        name: lowercase-snake key (e.g. ``"algo_kite_api_secret"``).
            Must match the docker-compose secret name AND the
            slug used by ``scripts/secrets/keychain.sh``.
        default: returned when the secret cannot be located.
            Pass ``""`` if you want empty-string instead of None.

    Resolution: file → env → default.
    """
    file_path = _SECRETS_DIR / name
    if file_path.is_file():
        try:
            value = file_path.read_text(encoding="utf-8").strip()
            if value:
                _logger.debug(
                    "secret %s loaded from %s", name, file_path,
                )
                return value
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "secret %s file read failed: %s", name, exc,
            )

    env = os.environ.get(_env_name(name), "").strip()
    if env:
        _logger.debug("secret %s loaded from env", name)
        return env

    return default


def reset_cache() -> None:
    """Clear the in-process cache (use in tests)."""
    load_secret.cache_clear()
