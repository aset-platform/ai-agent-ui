# LLM Hallucination Guardrail

## Overview
`synthesis.py` contains `_is_hallucinated()` which detects fabricated stock analysis responses by checking for characteristic patterns when no tool calls were made.

## Detection Logic
- Triggers when 3+ stock-analysis patterns are present AND zero `tool_done` events in the response
- Patterns matched: `CMP:`, `P/E Ratio`, `RSI(14)`, `SMA 200`, `MACD crossover`, and others
- Returns a fallback message ("I don't have enough data...") instead of fabricated data

## Cache Protection
- Query cache only stores responses that have at least one `tool_event`
- Prevents hallucinated responses from being cached and served to future queries

## False Positive Fix
- Initial patterns were too broad — matched legitimate portfolio sector discussions
- Tightened patterns to require stock-specific indicators (CMP, RSI, SMA) rather than generic financial terms

## File
- `backend/agents/nodes/synthesis.py` — `_is_hallucinated()` method

## Added
2026-03-31
