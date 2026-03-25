# LangSmith Observability — Phase 1 Live (2026-03-25)

## Status
Phase 1 complete and live. Traces flowing to LangSmith EU.

## Config
- `LANGCHAIN_TRACING_V2=true` in `~/.ai-agent-ui/backend.env`
- `LANGCHAIN_ENDPOINT=https://eu.api.smith.langchain.com` (EU region!)
- `LANGCHAIN_PROJECT=ai-agent-ui`
- Dashboard: https://eu.smith.langchain.com

## What's traced
- All `ChatGroq` / `ChatAnthropic` calls (auto-traced by LangChain)
- LangGraph StateGraph node transitions (auto-traced)
- `agents/loop.py:run()` — `@traceable` (agentic loop)
- `agents/stream.py:stream()` — `@traceable` (streaming loop)
- `agents/graph.py:build_supervisor_graph()` — `@traceable`
- `message_compressor.py:compress()` — `@traceable`
- `token_budget.py:reserve()` — `@traceable`
- HTTP requests via `TracingMiddleware` in `routes.py`

## IMPORTANT: Do NOT add @traceable to FallbackLLM.invoke()
Reverted on 2026-03-25 — broke LangChain tool call parsing.
The inner ChatGroq/ChatAnthropic calls are auto-traced anyway.

## Pending (ASETPLTFRM-194)
- Phase 2: LangFuse dual-platform integration
- Phase 3: Sampling, PII redaction, cost dashboard