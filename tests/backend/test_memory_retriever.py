"""Tests for memory_retriever module."""

from unittest.mock import MagicMock, patch

import pytest

from memory_retriever import (
    format_memories_for_prompt,
    retrieve_memories,
)


class TestFormatMemories:
    """Verify prompt formatting and token budget."""

    def test_empty_returns_empty(self):
        assert format_memories_for_prompt([]) == ""

    def test_formats_with_header(self):
        memories = [
            {
                "content": "User tracks RELIANCE.NS",
                "memory_type": "fact",
                "session_id": "s1",
                "score": 0.95,
            },
        ]
        result = format_memories_for_prompt(memories)
        assert result.startswith("[Memory context]")
        assert "RELIANCE.NS" in result

    def test_respects_token_budget(self):
        """Long memories are truncated at budget."""
        memories = [
            {
                "content": "A" * 500,
                "memory_type": "fact",
                "session_id": "s1",
                "score": 0.9,
            },
            {
                "content": "B" * 500,
                "memory_type": "fact",
                "session_id": "s2",
                "score": 0.8,
            },
        ]
        # Budget of 50 tokens = ~200 chars
        result = format_memories_for_prompt(
            memories, token_budget=50,
        )
        # Should only include first memory (502 chars
        # for "- " + 500 "A"s fits budget of 200)
        # Actually 502 > 200, so even first is cut
        assert result == "" or "B" not in result

    def test_multiple_memories(self):
        memories = [
            {
                "content": "Fact one",
                "memory_type": "fact",
                "session_id": "s1",
                "score": 0.9,
            },
            {
                "content": "Fact two",
                "memory_type": "fact",
                "session_id": "s2",
                "score": 0.8,
            },
        ]
        result = format_memories_for_prompt(
            memories, token_budget=200,
        )
        assert "Fact one" in result
        assert "Fact two" in result


class TestRetrieveMemories:
    """Verify retrieval with mocked dependencies."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_disabled(self):
        with patch(
            "memory_retriever.get_settings",
        ) as mock_s:
            mock_s.return_value = MagicMock(
                memory_enabled=False,
            )
            result = await retrieve_memories(
                "u1", "hello",
            )
            assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_embed_failure(
        self,
    ):
        with (
            patch(
                "memory_retriever.get_settings",
            ) as mock_s,
            patch(
                "embedding_service"
                ".get_embedding_service",
            ) as mock_emb,
        ):
            mock_s.return_value = MagicMock(
                memory_enabled=True,
                memory_top_k=5,
            )
            mock_emb.return_value = MagicMock(
                embed=MagicMock(return_value=None),
            )
            result = await retrieve_memories(
                "u1", "hello",
            )
            assert result == []
