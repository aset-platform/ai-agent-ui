"""Fernet-backed encryption for user-supplied LLM provider keys.

The master key is loaded via ``load_secret("byo_secret_key")``, which
follows the Keychain → docker-compose secrets mount → env-var fallback
resolution order (see ``docs/algo-trading/secrets.md``).

Generate a new key with::

    python -c "from cryptography.fernet import Fernet; \
        print(Fernet.generate_key().decode())"

The helper is used by ``auth.repo.byo_repo`` to encrypt user-entered
Groq / Anthropic API keys before persisting them in
``user_llm_keys.encrypted_key``.
"""
from __future__ import annotations

import logging

from cryptography.fernet import Fernet, InvalidToken

from backend.secret_loader import load_secret

_logger = logging.getLogger(__name__)

_MIN_MASK_LEN: int = 6
_fernet: Fernet | None = None


def get_fernet() -> Fernet:
    """Lazy-initialise the process-wide Fernet instance.

    Raises:
        RuntimeError: If ``byo_secret_key`` is missing or invalid.
    """
    global _fernet
    if _fernet is not None:
        return _fernet

    raw = load_secret("byo_secret_key") or ""
    if not raw:
        raise RuntimeError(
            "BYO_SECRET_KEY not configured. Set via Keychain "
            "(preferred) or .env (legacy). "
            "See docs/algo-trading/secrets.md."
        )
    try:
        _fernet = Fernet(raw.encode())
    except (ValueError, TypeError) as exc:
        raise RuntimeError(
            "BYO_SECRET_KEY is not a valid 32-byte URL-safe base64 "
            "Fernet key — regenerate and restart."
        ) from exc
    return _fernet


def encrypt_key(plaintext: str) -> bytes:
    """Encrypt a provider API key for at-rest storage."""
    if not plaintext:
        raise ValueError("plaintext key is empty")
    return get_fernet().encrypt(plaintext.encode("utf-8"))


def decrypt_key(ciphertext: bytes) -> str:
    """Decrypt a stored provider key. Used by the cascade override
    (Phase B). Raises on tampering / master-key rotation.
    """
    try:
        return get_fernet().decrypt(ciphertext).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError(
            "Stored BYO key cannot be decrypted — master key may have "
            "rotated or ciphertext is corrupt."
        ) from exc


def mask_key(plaintext: str) -> str:
    """Return a display-safe preview like ``gsk_****3xyz``.

    Preserves everything up to the last ``_`` or ``-`` separator in
    the provider prefix so users can tell keys apart by provider
    (``gsk_****``, ``sk-ant-****``, etc.) and by the trailing 4 chars.
    """
    if not plaintext:
        return ""
    if len(plaintext) < _MIN_MASK_LEN:
        return "*" * len(plaintext)
    # Prefer a prefix of the form "<letters>_" or "<letters>-<letters>-".
    # Anthropic uses "sk-ant-…" so we scan for the dash after "ant".
    last_sep = -1
    for i, ch in enumerate(plaintext):
        if ch in "_-":
            last_sep = i
    tail = plaintext[-4:]
    if last_sep > 0 and last_sep < len(plaintext) - 4:
        prefix = plaintext[: last_sep + 1]
        return f"{prefix}****{tail}"
    return f"****{tail}"
