# Tool-Result Truncation → LLM Hallucination

## Problem

`backend/message_compressor.py::MessageCompressor` truncates
`ToolMessage.content` to fit the Groq free-tier token budget.
It appends a literal marker:

```
... [truncated N chars]
```

When the truncation clips structured data mid-row (e.g. a
portfolio holdings table of 8 rows at 1100 chars, clipped
at 800), the LLM sees partial content + the marker and
**invents** the missing rows in its synthesis. Observed
output verbatim:

> "You have 8 stocks in your portfolio. The holdings include:
> DLF.NS, HDBFS.NS, … RELIANCE.NS.
> [Truncated in display, but confirmed in memory context]"

That closing phrase exists nowhere in our code — it's pure
fabrication. The LLM had no way to know whether the 8th row
existed; it guessed and dressed the guess in a plausible
"system" framing.

## Three-layer defense

### 1. Raise the truncation threshold

`MessageCompressor.max_tool_result_chars` default 800 is too
tight for modern Groq context windows (the 70% TPM ceiling
is ~8400 tokens = ~30K chars for llama-3.3-70b). Current
defaults:

| Pass | Chars |
|---|---|
| Normal | 4000 |
| Progressive pass 2 | 2500 |
| Progressive pass 3 | 1500 |

These hold a typical portfolio / screener table (~15 rows
intact) without needing to clip.

### 2. Synthesis prompt guardrail

`_SYNTHESIS_PROMPT` in `backend/agents/nodes/synthesis.py`
includes:

> NO HALLUCINATION ON TRUNCATION: If any tool output or prior
> message ends with a literal marker like `[truncated N
> chars]`, you MUST NOT invent, enumerate, or claim items
> beyond what is visible. Do not write phrases like
> "truncated in display" or "confirmed in memory context".
> Instead, list only what is shown and say "some rows were
> trimmed to fit token limits — ask me to narrow the query
> to see the rest."

### 3. Sub-agent prompt guardrail

Sub-agents that surface tool tables directly (Portfolio
agent is the main one) have the same NO HALLUCINATION ON
TRUNCATION clause in their system prompt, so the rule fires
even when synthesis is skipped (large-response passthrough).

## Test approach

Hard to unit-test end-to-end (LLM behaviour is stochastic)
but assertions can catch the upstream conditions:

- `max_tool_result_chars` sanity check in compressor tests
  — don't let it regress below a size that fits a typical
  portfolio response.
- Integration test: send a portfolio-holdings chat as a
  seeded user with N>10 holdings; assert the response
  mentions all N tickers OR acknowledges truncation in
  the approved phrasing.

## Signs of recurrence

If a user reports the LLM "making up" stocks, tickers, or
rows that don't exist:
1. Grep the response for a `[truncated` marker — confirms
   source.
2. Grep for invented phrases like "memory context",
   "confirmed elsewhere", "as noted earlier".
3. Check the actual tool output via backend logs to
   verify the data was intact pre-compression.
