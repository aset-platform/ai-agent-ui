"""Tracing utilities: LangFuse callbacks, sampling, PII redaction.

Phase 2 & 3 of ASETPLTFRM-194.  Provides:

* :func:`get_callbacks` — per-call callback list for LLM invocations
* :func:`should_trace` — probabilistic sampling with error override
* :func:`redact_pii` — regex scrubber for email/phone/PAN/card
* :func:`setup_anonymizer` — LangSmith PII anonymizer (startup)
* :func:`get_langfuse_handler` — fresh LangChain callback handler
"""

from __future__ import annotations

import logging
import os
import random
import re
import threading
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from langchain_core.callbacks import BaseCallbackHandler

_logger = logging.getLogger(__name__)

# ── PII patterns (compiled once) ─────────────────────────────
# Word-boundary anchors minimise false positives on tickers.

_PII_PATTERNS: list[re.Pattern[str]] = [
    # Email
    re.compile(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
    ),
    # Phone: Indian (+91 / 0-prefixed 10-digit)
    re.compile(
        r"\b(?:\+91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}\b",
    ),
    # Phone: international E.164
    re.compile(
        r"\b\+[1-9]\d{6,14}\b",
    ),
    # Indian PAN (ABCDE1234F)
    re.compile(
        r"\b[A-Z]{5}\d{4}[A-Z]\b",
    ),
    # Indian Aadhaar (1234 5678 9012)
    re.compile(
        r"\b\d{4}\s\d{4}\s\d{4}\b",
    ),
    # Credit/debit card (1234-5678-9012-3456)
    re.compile(
        r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b",
    ),
]

# Secret / API key patterns — ALWAYS redacted, even in dev.
_SECRET_PATTERNS: list[re.Pattern[str]] = [
    # Groq API key (gsk_...)
    re.compile(r"\bgsk_[A-Za-z0-9]{20,}\b"),
    # Anthropic API key (sk-ant-...)
    re.compile(r"\bsk-ant-[A-Za-z0-9\-]{20,}\b"),
    # OpenAI API key (sk-...)
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    # LangSmith API key (lsv2_pt_...)
    re.compile(r"\blsv2_pt_[A-Za-z0-9_]{20,}\b"),
    # LangFuse keys (pk-lf-... / sk-lf-...)
    re.compile(r"\b[ps]k-lf-[A-Za-z0-9]{5,}\b"),
    # Razorpay key/secret (rzp_test_... / rzp_live_...)
    re.compile(r"\brzp_(?:test|live)_[A-Za-z0-9]{10,}\b"),
    # Stripe key (sk_test_... / sk_live_... / pk_...)
    re.compile(
        r"\b[sp]k_(?:test|live)_[A-Za-z0-9]{10,}\b",
    ),
    # Generic long hex/base64 secrets (32+ chars after
    # common key= / secret= / password= / token= prefixes)
    re.compile(
        r"(?i)(?:api[_-]?key|secret[_-]?key|password"
        r"|token|authorization)\s*[=:]\s*['\"]?"
        r"([A-Za-z0-9/+=_\-]{32,})",
    ),
    # JWT tokens (eyJ...)
    re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\b"),
]

_REDACTED = "[REDACTED]"


def redact_secrets(text: str) -> str:
    """Strip API keys, tokens, and secrets from *text*.

    Always applied — even when ``hide_trace_io`` is off.
    """
    for pat in _SECRET_PATTERNS:
        text = pat.sub(_REDACTED, text)
    return text


def redact_pii(text: str) -> str:
    """Replace PII tokens in *text* with ``[REDACTED]``.

    Handles email addresses, Indian/international phone numbers,
    PAN card numbers, Aadhaar numbers, and credit-card patterns.
    """
    for pat in _PII_PATTERNS:
        text = pat.sub(_REDACTED, text)
    return text


def redact_all(text: str) -> str:
    """Redact both secrets and PII from *text*."""
    return redact_pii(redact_secrets(text))


# ── Sampling ──────────────────────────────────────────────────


def should_trace(*, is_error: bool = False) -> bool:
    """Return ``True`` if this call should be traced.

    Errors are *always* traced regardless of sample rate.
    """
    if is_error:
        return True
    from config import get_settings

    rate = get_settings().trace_sample_rate
    if rate >= 1.0:
        return True
    if rate <= 0.0:
        return False
    return random.random() < rate


# ── LangFuse v4 integration ──────────────────────────────────
# LangFuse v4 uses OpenTelemetry under the hood and reads
# LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_BASE_URL
# from the environment.  We initialise the singleton once and
# create lightweight CallbackHandler instances per-call.

_langfuse_init_lock = threading.Lock()
_langfuse_initialised = False


def _make_langfuse_mask(
    scrub_fn: Callable[[str], str],
) -> Callable:
    """Wrap a string scrubber for LangFuse's ``mask`` param.

    LangFuse v4 passes arbitrary types (dict, list, str,
    BaseMessage, etc.) to the mask function.  This wrapper
    recursively walks the structure and applies *scrub_fn*
    to every string leaf.
    """

    def _mask(data: object) -> object:
        if isinstance(data, str):
            return scrub_fn(data)
        if isinstance(data, dict):
            return {k: _mask(v) for k, v in data.items()}
        if isinstance(data, (list, tuple)):
            return type(data)(_mask(v) for v in data)
        # For LangChain BaseMessage and other objects,
        # convert to string and scrub.
        if hasattr(data, "content"):
            try:
                data.content = scrub_fn(
                    str(data.content),
                )
            except (AttributeError, TypeError):
                pass
            return data
        return data

    return _mask


def _ensure_langfuse() -> bool:
    """Lazily initialise the LangFuse singleton.

    Sets LANGFUSE env vars from Settings, then creates the
    singleton client via ``Langfuse()``.  Returns ``True`` if
    the client was successfully initialised.
    """
    global _langfuse_initialised  # noqa: PLW0603
    if _langfuse_initialised:
        return True

    with _langfuse_init_lock:
        if _langfuse_initialised:
            return True
        try:
            from config import get_settings

            s = get_settings()
            if not s.langfuse_enabled:
                return False
            if not s.langfuse_public_key:
                _logger.warning(
                    "langfuse_enabled=True but "
                    "LANGFUSE_PUBLIC_KEY is empty",
                )
                return False

            # LangFuse v4 reads these env vars internally.
            os.environ.setdefault(
                "LANGFUSE_PUBLIC_KEY",
                s.langfuse_public_key,
            )
            os.environ.setdefault(
                "LANGFUSE_SECRET_KEY",
                s.langfuse_secret_key,
            )
            os.environ.setdefault(
                "LANGFUSE_BASE_URL",
                s.langfuse_host,
            )
            os.environ.setdefault(
                "LANGFUSE_SAMPLE_RATE",
                str(s.trace_sample_rate),
            )

            from langfuse import Langfuse

            # Trigger singleton creation.
            # Always scrub secrets; add PII in prod.
            _str_scrub = redact_all if s.hide_trace_io else redact_secrets
            _mask = _make_langfuse_mask(_str_scrub)
            Langfuse(
                public_key=s.langfuse_public_key,
                secret_key=s.langfuse_secret_key,
                base_url=s.langfuse_host,
                sample_rate=s.trace_sample_rate,
                mask=_mask,
            )
            _langfuse_initialised = True
            _logger.info(
                "LangFuse v4 initialised " "(host=%s, sample_rate=%.2f)",
                s.langfuse_host,
                s.trace_sample_rate,
            )
            return True
        except Exception:
            _logger.exception("Failed to initialise LangFuse")
            return False


def get_langfuse_handler(
    trace_name: str,
    user_id: str | None = None,
) -> BaseCallbackHandler | None:
    """Return a fresh LangFuse LangChain callback handler.

    Returns ``None`` when LangFuse is disabled or
    initialisation failed.
    """
    if not _ensure_langfuse():
        return None
    try:
        from langfuse.langchain import CallbackHandler

        ctx: dict[str, str] = {"trace_id": trace_name}
        handler = CallbackHandler(trace_context=ctx)
        return handler
    except Exception:
        _logger.debug(
            "LangFuse handler creation failed",
            exc_info=True,
        )
        return None


def get_callbacks(
    trace_name: str,
    user_id: str | None = None,
    *,
    is_error: bool = False,
) -> list[BaseCallbackHandler]:
    """Return callback handlers for the current LLM call.

    Checks sampling, feature flags, and returns a list of
    LangChain-compatible callback handlers.  Empty list means
    "don't trace this call".
    """
    if not should_trace(is_error=is_error):
        return []

    callbacks: list[BaseCallbackHandler] = []
    handler = get_langfuse_handler(trace_name, user_id)
    if handler is not None:
        callbacks.append(handler)
    return callbacks


# ── LangSmith PII anonymizer ─────────────────────────────────


def setup_anonymizer() -> None:
    """Configure LangSmith's trace anonymizer at startup.

    **Secrets are ALWAYS redacted** (API keys, tokens) even
    in dev.  Full PII redaction + I/O hiding only activates
    when ``hide_trace_io=True`` (production).
    """
    try:
        from config import get_settings

        s = get_settings()
        if not s.langsmith_enabled:
            return

        from langsmith.anonymizer import (
            create_anonymizer,
        )

        # Always redact secrets; add PII only in prod.
        if s.hide_trace_io:
            _scrub = lambda text: redact_all(str(text))  # noqa: E731
        else:
            _scrub = lambda text: redact_secrets(str(text))  # noqa: E731

        anonymizer = create_anonymizer(_scrub)

        # Hide full I/O in production only.
        if s.hide_trace_io:
            os.environ.setdefault(
                "LANGSMITH_HIDE_INPUTS",
                "true",
            )
            os.environ.setdefault(
                "LANGSMITH_HIDE_OUTPUTS",
                "true",
            )
        _logger.info(
            "LangSmith anonymizer configured " "(hide_io=%s)",
            s.hide_trace_io,
        )
        # Store for reference (not used at runtime).
        setup_anonymizer._instance = anonymizer  # type: ignore[attr-defined]
    except ImportError:
        _logger.debug(
            "langsmith.anonymizer not available",
        )
    except Exception:
        _logger.exception(
            "Failed to configure LangSmith anonymizer",
        )
