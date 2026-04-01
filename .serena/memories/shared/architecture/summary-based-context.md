# Summary-Based Context for Sub-Agents

## Overview
Sub-agents receive a compressed summary instead of raw conversation history, reducing token usage and eliminating history pollution that caused hallucination on intent switches.

## Message Construction Rules
- **First message in session**: prompt + query (no history)
- **Same-intent follow-up**: prompt + summary (~100 tokens) + query
- **Intent switch**: prompt + query only (no summary — prevents cross-intent contamination)

## Token Savings
- `ConversationContext.summary`: ~100 tokens
- Raw message history it replaces: ~3K tokens
- Net saving per sub-agent call: ~2.9K tokens

## Why No History on Intent Switch
Passing stock analysis history to a portfolio agent caused hallucinated portfolio data. Dropping history on intent switches eliminated this class of hallucination entirely.

## File
- `backend/agents/sub_agents.py` lines 208-260 — message building logic with summary injection

## Added
2026-03-31
