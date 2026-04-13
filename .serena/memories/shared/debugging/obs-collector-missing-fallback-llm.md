# obs_collector Missing from FallbackLLM Instances

## Problem
7 out of 11 FallbackLLM instantiations were missing `obs_collector=get_obs_collector()`. This caused:
- gpt-oss-120b/20b showing 0 usage in dashboard (synthesis requests not tracked)
- Observability events not flushed to Iceberg llm_usage table
- `seed_daily_from_iceberg()` finding 0 rows for those models on restart

## Root Cause
Only `agents/base.py` and `jobs/recommendation_engine.py` passed obs_collector. All other creation sites defaulted to `obs_collector=None`.

## Fixed Files (Apr 13, 2026)
1. `backend/agents/sub_agents.py` — synthesis LLM (`_build_synthesis_llm`)
2. `backend/agents/nodes/synthesis.py` — graph synthesis node
3. `backend/agents/nodes/topic_classifier.py` — classifier LLM
4. `backend/agents/conversation_context.py` — summary LLM
5. `backend/memory_extractor.py` — fact_extractor LLM
6. `backend/tools/sentiment_agent.py` — sentiment LLM
7. `backend/jobs/gap_filler.py` — sentiment_batch LLM

## Fix Pattern
```python
from observability import get_obs_collector
return FallbackLLM(
    ...,
    obs_collector=get_obs_collector(),
    ...
)
```

## Related: Synthesis Hallucination Fix
Changed `[Tool result for X]:` prefix to `Data from X:` in `_strip_tool_metadata()`.
The tool-like prefix triggered gpt-oss models to hallucinate tool calls during synthesis (error: "Tool choice is none, but model called a tool").
