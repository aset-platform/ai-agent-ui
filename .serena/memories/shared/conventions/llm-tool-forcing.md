# LLM Tool-Use Forcing — Preventing Hallucination

## Problem
Groq Llama 3.3 and similar models hallucinate data ("$1.2M", "ABC Corp") instead of calling tools, especially for portfolio queries where the model has no real data.

## Solution: Forceful System Prompts
Standard instructions like "use tools when needed" are insufficient. The system prompt must be extremely directive:

```
YOUR FIRST RESPONSE MUST ONLY be a tool call.
DO NOT write any text before calling a tool.
NEVER guess, estimate, or make up numbers.
If you don't have data, call the appropriate tool FIRST.
```

## Dynamic Context Injection
`_build_context_block()` in `agents/sub_agents.py` detects the user's currency/market mix from portfolio holdings and appends context to the system prompt:

```
User's portfolio contains: 7 INR stocks, 1 USD stock.
Always use ₹ for INR stocks and $ for USD stocks.
Show per-currency totals when mixing currencies.
```

This is injected at request time via the `user_context` field in `AgentState`.

## Key Files
- `agents/configs/portfolio.py` — system prompt with mandatory tool-use rules
- `agents/sub_agents.py` — `_build_context_block()` for currency detection
- `agents/graph_state.py` — `user_context` field in AgentState