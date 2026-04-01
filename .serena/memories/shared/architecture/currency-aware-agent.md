# Currency-Aware Portfolio Agent

## Problem
AI chat showed $ for Indian stocks (₹) and hallucinated data instead of calling tools.

## Solution

### System Prompt Enforcement
Groq Llama 3.3 requires extremely forceful prompts to use tools:
- "YOUR FIRST RESPONSE MUST ONLY be a tool call"
- Explicit currency rules: "Use ₹ for .NS/.BO tickers, $ for US tickers"
- Anti-hallucination: "NEVER invent prices, percentages, or company names"

### Dynamic Context Injection
`_build_context_block()` in sub-agent configs detects currency/market mix from user's portfolio holdings and appends to system prompt:
```
Portfolio context: 3 INR stocks (₹), 2 USD stocks ($)
Markets: india (3), us (2)
```

### User Context Flow
1. `build_user_context(user_id)` reads portfolio holdings
2. Extracts `{currencies: {INR: 3, USD: 2}, markets: {india: 3, us: 2}}`
3. Injected into `AgentState.user_context` for both HTTP and WS paths
4. Sub-agent configs read context and format currency-specific prompts

### Tool Output Format
`get_portfolio_holdings/summary/performance` show ₹/$ per row + per-currency totals.

## Key Files
- `backend/agents/configs/portfolio.py` — system prompt + `_build_context_block()`
- `backend/agents/sub_agents.py` — context injection
- `backend/agents/graph_state.py` — `user_context` field
- `backend/user_context.py` — shared `build_user_context()` function
- `backend/tools/portfolio_tools.py` — currency-formatted outputs
