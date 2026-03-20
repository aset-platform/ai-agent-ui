# LLM Cascade — Tool/Synthesis/Test Profiles

## Overview
The LLM cascade uses different model configurations (profiles) depending on the task type. This reduces cost and improves reliability.

## Profiles

### Tool-Call Profile (default)
- Used for: agent tool-calling loops (search, stock analysis, forecasting)
- Tiers: 4-tier Groq cascade → Anthropic Claude fallback
- Models: llama-3.3-70b → gemma2-9b → llama-3.1-8b → kimi-k2 → claude-sonnet
- Optimized for: function calling reliability, low latency

### Synthesis Profile
- Used for: final response generation (report building, verdict synthesis)
- Model: Claude Sonnet only (no Groq cascade)
- Optimized for: output quality, coherent long-form text
- Called once per analysis (not in loop)

### Test Profile (`AI_AGENT_UI_ENV=test`)
- Used for: pytest runs, CI
- Tiers: free-tier Groq models only (no Anthropic)
- Prevents: accidental paid API calls during testing

## Configuration
- `backend/config.py`: `Settings.groq_model_tiers` CSV string
- `backend/llm_fallback.py`: `FallbackLLM` class with tier cascade
- Profile selection: `agents/base.py` checks env and task type

## Key Metrics (from Mar 15 optimization)
- API calls per analysis: 10 → 5 (50% reduction)
- Token usage: ~28K → ~14.6K per analysis (48% reduction)
- Report consistency: 100% deterministic via `report_builder.py`
