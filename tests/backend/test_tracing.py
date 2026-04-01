"""Unit tests for backend/tracing.py.

Covers:
- should_trace() sampling logic
- redact_pii() pattern matching
- get_callbacks() feature-flag + sampling integration
- get_langfuse_handler() disabled path
"""

from unittest.mock import MagicMock, patch

# ── redact_pii ────────────────────────────────────────────────


class TestRedactPii:
    """PII scrubbing via regex patterns."""

    def test_email_redacted(self):
        from tracing import redact_pii

        assert "[REDACTED]" in redact_pii(
            "contact user@example.com for details"
        )

    def test_phone_indian_redacted(self):
        from tracing import redact_pii

        result = redact_pii("call +91 98765 43210 now")
        assert "[REDACTED]" in result
        assert "98765" not in result

    def test_pan_redacted(self):
        from tracing import redact_pii

        result = redact_pii("PAN: ABCDE1234F")
        assert "[REDACTED]" in result
        assert "ABCDE1234F" not in result

    def test_credit_card_redacted(self):
        from tracing import redact_pii

        result = redact_pii("card 4242-4242-4242-4242")
        assert "[REDACTED]" in result
        assert "4242" not in result

    def test_aadhaar_redacted(self):
        from tracing import redact_pii

        result = redact_pii("aadhaar 1234 5678 9012")
        assert "[REDACTED]" in result
        assert "5678" not in result

    def test_no_pii_passthrough(self):
        from tracing import redact_pii

        text = "RELIANCE stock is up 2.5% today"
        assert redact_pii(text) == text


# ── redact_secrets ────────────────────────────────────────────


class TestRedactSecrets:
    """API key and token scrubbing."""

    def test_groq_key_redacted(self):
        from tracing import redact_secrets

        text = "GROQ_API_KEY=gsk_abc123def456ghi789jkl012mno"
        result = redact_secrets(text)
        assert "gsk_" not in result
        assert "[REDACTED]" in result

    def test_langsmith_key_redacted(self):
        from tracing import redact_secrets

        text = "key=lsv2_pt_" + "a" * 32 + "_" + "b" * 10
        result = redact_secrets(text)
        assert "lsv2_pt_" not in result

    def test_stripe_key_redacted(self):
        from tracing import redact_secrets

        text = "sk_test_" + "x" * 24
        result = redact_secrets(text)
        assert "sk_test_" not in result

    def test_razorpay_key_redacted(self):
        from tracing import redact_secrets

        text = "rzp_test_abc123def456ghi789"
        result = redact_secrets(text)
        assert "rzp_test_" not in result

    def test_jwt_redacted(self):
        from tracing import redact_secrets

        text = "token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        result = redact_secrets(text)
        assert "eyJ" not in result

    def test_generic_secret_redacted(self):
        from tracing import redact_secrets

        text = "secret_key = " "abcdef1234567890abcdef1234567890ab"
        result = redact_secrets(text)
        assert "[REDACTED]" in result

    def test_clean_text_passthrough(self):
        from tracing import redact_secrets

        text = "RELIANCE is trading at 2450.75"
        assert redact_secrets(text) == text


# ── should_trace ──────────────────────────────────────────────


class TestShouldTrace:
    """Probabilistic sampling with error override."""

    def test_full_rate_always_traces(self):
        from tracing import should_trace

        with patch(
            "config.get_settings",
        ) as mock_settings:
            mock_settings.return_value = MagicMock(
                trace_sample_rate=1.0,
            )
            assert should_trace() is True

    def test_zero_rate_skips(self):
        from tracing import should_trace

        with patch(
            "config.get_settings",
        ) as mock_settings:
            mock_settings.return_value = MagicMock(
                trace_sample_rate=0.0,
            )
            assert should_trace() is False

    def test_error_always_traced(self):
        from tracing import should_trace

        # Errors bypass sampling entirely.
        assert should_trace(is_error=True) is True


# ── get_langfuse_handler ──────────────────────────────────────


class TestGetLangfuseHandler:
    """LangFuse handler creation."""

    def test_returns_none_when_disabled(self):
        from tracing import get_langfuse_handler

        with patch(
            "tracing._ensure_langfuse",
            return_value=False,
        ):
            assert get_langfuse_handler("test") is None


# ── get_callbacks ─────────────────────────────────────────────


class TestGetCallbacks:
    """Callback list assembly."""

    def test_empty_when_sampling_skips(self):
        from tracing import get_callbacks

        with patch(
            "tracing.should_trace",
            return_value=False,
        ):
            assert get_callbacks("test") == []

    def test_returns_handler_when_enabled(self):
        from tracing import get_callbacks

        mock_handler = MagicMock()
        with (
            patch(
                "tracing.should_trace",
                return_value=True,
            ),
            patch(
                "tracing.get_langfuse_handler",
                return_value=mock_handler,
            ),
        ):
            cbs = get_callbacks("test")
            assert len(cbs) == 1
            assert cbs[0] is mock_handler

    def test_empty_when_langfuse_disabled(self):
        from tracing import get_callbacks

        with (
            patch(
                "tracing.should_trace",
                return_value=True,
            ),
            patch(
                "tracing.get_langfuse_handler",
                return_value=None,
            ),
        ):
            assert get_callbacks("test") == []
