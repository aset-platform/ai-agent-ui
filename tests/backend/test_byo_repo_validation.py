"""Validation-layer unit tests for ``auth.repo.byo_repo``."""

from __future__ import annotations

import pytest

from auth.repo import byo_repo


class TestValidateProvider:
    def test_accepts_groq(self):
        assert byo_repo.validate_provider("groq") == "groq"

    def test_accepts_anthropic(self):
        assert byo_repo.validate_provider("anthropic") == "anthropic"

    def test_case_insensitive(self):
        assert byo_repo.validate_provider("Groq") == "groq"

    def test_whitespace_trimmed(self):
        assert byo_repo.validate_provider(" groq ") == "groq"

    def test_rejects_unknown(self):
        with pytest.raises(ValueError):
            byo_repo.validate_provider("openai")

    def test_rejects_empty(self):
        with pytest.raises(ValueError):
            byo_repo.validate_provider("")


class TestValidateKeyFormat:
    def test_groq_prefix(self):
        byo_repo.validate_key_format(
            "groq", "gsk_abcdefghijklmn",
        )

    def test_anthropic_prefix(self):
        byo_repo.validate_key_format(
            "anthropic", "sk-ant-xxxxxxxxxxxx",
        )

    def test_rejects_groq_without_prefix(self):
        with pytest.raises(
            ValueError, match="gsk_",
        ):
            byo_repo.validate_key_format(
                "groq", "whatever_xxxxxxxxx",
            )

    def test_rejects_anthropic_without_prefix(self):
        with pytest.raises(
            ValueError, match="sk-ant-",
        ):
            byo_repo.validate_key_format(
                "anthropic", "sk-other-xxxxxx",
            )

    def test_rejects_empty(self):
        with pytest.raises(ValueError, match="empty"):
            byo_repo.validate_key_format("groq", "")

    def test_strips_whitespace(self):
        byo_repo.validate_key_format(
            "groq", "  gsk_realkey  ",
        )
