"""Tests for memory_extractor module."""

import json
from unittest.mock import (
    AsyncMock,
    MagicMock,
    patch,
)

import pytest

from memory_extractor import _parse_facts


class TestParseFacts:
    """Verify JSON fact parsing from LLM output."""

    def test_valid_json_array(self):
        raw = json.dumps([
            {
                "type": "fact",
                "content": "User tracks RELIANCE.NS",
                "tickers": ["RELIANCE.NS"],
                "metrics": [],
                "agent": "stock_analyst",
            },
        ])
        facts = _parse_facts(raw)
        assert len(facts) == 1
        assert facts[0]["content"] == (
            "User tracks RELIANCE.NS"
        )

    def test_empty_array(self):
        assert _parse_facts("[]") == []

    def test_markdown_fenced(self):
        raw = "```json\n[{\"type\":\"fact\"," \
              "\"content\":\"test\"}]\n```"
        facts = _parse_facts(raw)
        assert len(facts) == 1

    def test_invalid_json(self):
        assert _parse_facts("not json") == []

    def test_skips_empty_content(self):
        raw = json.dumps([
            {"type": "fact", "content": ""},
            {"type": "fact", "content": "valid"},
        ])
        facts = _parse_facts(raw)
        assert len(facts) == 1
        assert facts[0]["content"] == "valid"

    def test_non_list_returns_empty(self):
        raw = json.dumps({"type": "fact"})
        assert _parse_facts(raw) == []


class TestExtractAndStoreMemories:
    """Verify the async extraction pipeline."""

    @pytest.mark.asyncio
    async def test_skips_when_disabled(self):
        """memory_enabled=False skips everything."""
        from memory_extractor import (
            extract_and_store_memories,
        )

        with patch(
            "memory_extractor.get_settings",
        ) as mock_s:
            mock_s.return_value = MagicMock(
                memory_enabled=False,
            )
            # Should return without error
            await extract_and_store_memories(
                "u1", "s1", "hi", "hello",
                "summary", 1,
            )

    @pytest.mark.asyncio
    async def test_upsert_summary_called(self):
        """With memory enabled, summary upsert fires."""
        from memory_extractor import (
            extract_and_store_memories,
        )

        with (
            patch(
                "memory_extractor.get_settings",
            ) as mock_s,
            patch(
                "memory_extractor"
                "._upsert_summary_memory",
                new_callable=AsyncMock,
            ) as mock_upsert,
            patch(
                "memory_extractor"
                "._extract_and_store_facts",
                new_callable=AsyncMock,
            ),
            patch(
                "embedding_service"
                ".get_embedding_service",
            ),
        ):
            mock_s.return_value = MagicMock(
                memory_enabled=True,
            )
            await extract_and_store_memories(
                "u1", "s1",
                "what is my portfolio?",
                "Your portfolio has 4 stocks worth "
                "INR 147,703. TCS is down 18.7%.",
                "discussed portfolio health", 1,
            )
            mock_upsert.assert_awaited_once()
