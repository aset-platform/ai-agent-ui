"""Tests for Story 3.2 — MessageCompressor optimizations."""

import time

import pytest
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from message_compressor import MessageCompressor


@pytest.fixture()
def compressor():
    """Return a default MessageCompressor."""
    return MessageCompressor()


def _make_messages(n: int):
    """Generate n user/assistant turn pairs + tool messages."""
    msgs = [SystemMessage(content="System prompt " * 50)]
    for i in range(n):
        msgs.append(HumanMessage(content=f"Q{i} " * 20))
        msgs.append(
            AIMessage(
                content=f"A{i} " * 20,
                tool_calls=(
                    [
                        {
                            "name": "tool",
                            "args": {"x": i},
                            "id": f"tc_{i}",
                        }
                    ]
                    if i % 3 == 0
                    else []
                ),
            )
        )
        if i % 3 == 0:
            msgs.append(
                ToolMessage(
                    content=f"Result {i} " * 30,
                    tool_call_id=f"tc_{i}",
                )
            )
    # Final user message (current request).
    msgs.append(HumanMessage(content="Current question"))
    return msgs


def test_early_exit_skips_compression(compressor):
    """When tokens < 50% of budget, compress returns early."""
    msgs = [
        SystemMessage(content="Hello"),
        HumanMessage(content="Hi"),
    ]
    # Large budget — should skip compression.
    result = compressor.compress(
        msgs,
        iteration=1,
        target_tokens=100_000,
    )
    assert len(result) == 2
    assert result[0].content == "Hello"


def test_compress_100_messages_correct(compressor):
    """Compression of 100 messages produces valid output."""
    msgs = _make_messages(100)
    result = compressor.compress(msgs, iteration=2)
    # Must start with SystemMessage.
    assert isinstance(result[0], SystemMessage)
    # Must end with the current HumanMessage.
    assert isinstance(result[-1], HumanMessage)
    assert result[-1].content == "Current question"
    # Should be shorter than original.
    assert len(result) <= len(msgs)


def test_compress_100_under_50ms(compressor):
    """Compression of 100 messages should be fast."""
    msgs = _make_messages(100)
    start = time.perf_counter()
    for _ in range(10):
        compressor.compress(msgs, iteration=2)
    elapsed = (time.perf_counter() - start) / 10
    # Allow generous 50ms per call.
    assert elapsed < 0.05, f"Compression took {elapsed:.3f}s"


def test_find_loop_boundary_single_pass():
    """_find_loop_boundary uses index-based lookup."""
    msgs = [
        SystemMessage(content="sys"),
        HumanMessage(content="old q"),
        AIMessage(content="old a"),
        HumanMessage(content="new q"),
        AIMessage(
            content="",
            tool_calls=[
                {"name": "t", "args": {}, "id": "1"},
            ],
        ),
        ToolMessage(content="result", tool_call_id="1"),
    ]
    boundary = MessageCompressor._find_loop_boundary(msgs)
    assert boundary == 3  # index of "new q"
