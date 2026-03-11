"""Tests for Pydantic model input constraints (security)."""

import sys
from pathlib import Path

import pytest

# Ensure backend/ and auth/ are on sys.path
_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_root / "backend"))
sys.path.insert(0, str(_root))

from pydantic import ValidationError  # noqa: E402


# ── ChatRequest ──────────────────────────────────────────


class TestChatRequestConstraints:
    """Verify ChatRequest field limits."""

    def test_message_max_length(self):
        """Message over 10k chars is rejected."""
        from models import ChatRequest

        with pytest.raises(ValidationError):
            ChatRequest(message="x" * 10_001)

    def test_message_empty_rejected(self):
        """Empty message is rejected (min_length=1)."""
        from models import ChatRequest

        with pytest.raises(ValidationError):
            ChatRequest(message="")

    def test_message_within_limit(self):
        """Message at 10k chars is accepted."""
        from models import ChatRequest

        req = ChatRequest(message="x" * 10_000)
        assert len(req.message) == 10_000

    def test_agent_id_pattern(self):
        """Agent ID with invalid chars is rejected."""
        from models import ChatRequest

        with pytest.raises(ValidationError):
            ChatRequest(
                message="hi",
                agent_id="'; DROP TABLE--",
            )

    def test_agent_id_valid(self):
        """Agent ID with lowercase + underscore passes."""
        from models import ChatRequest

        req = ChatRequest(
            message="hi", agent_id="stock_agent"
        )
        assert req.agent_id == "stock_agent"


# ── UserCreateRequest ────────────────────────────────────


class TestUserCreateRequestConstraints:
    """Verify UserCreateRequest field limits."""

    def test_role_invalid_value_rejected(self):
        """Role other than general/superuser is rejected."""
        from auth.models.request import UserCreateRequest

        with pytest.raises(ValidationError):
            UserCreateRequest(
                email="a@b.com",
                password="Str0ng!Pass",
                full_name="Test",
                role="admin",
            )

    def test_role_valid_values(self):
        """Both general and superuser are accepted."""
        from auth.models.request import UserCreateRequest

        for role in ("general", "superuser"):
            req = UserCreateRequest(
                email="a@b.com",
                password="Str0ng!Pass",
                full_name="Test",
                role=role,
            )
            assert req.role == role

    def test_full_name_max_length(self):
        """Full name over 200 chars is rejected."""
        from auth.models.request import UserCreateRequest

        with pytest.raises(ValidationError):
            UserCreateRequest(
                email="a@b.com",
                password="Str0ng!Pass",
                full_name="x" * 201,
            )

    def test_password_max_length(self):
        """Password over 128 chars is rejected."""
        from auth.models.request import LoginRequest

        with pytest.raises(ValidationError):
            LoginRequest(
                email="a@b.com",
                password="x" * 129,
            )
