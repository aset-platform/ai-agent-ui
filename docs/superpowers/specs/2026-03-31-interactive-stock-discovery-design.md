# Interactive Stock Discovery & Analysis — Design Spec

**Date:** 2026-03-31
**Branch:** `feature/interactive-stock-discovery`
**Status:** Draft

---

## Problem

When a user asks "pick stocks from financial services to rebalance my
portfolio", the stock_analyst agent attempts to batch-analyse all
matching stocks at once. This triggers a sequence of tool calls per
stock (fetch_stock_data, get_stock_info, analyse_stock_price,
forecast_stock, get_ticker_news, get_analyst_recommendations) that
exceeds the MAX_ITERATIONS limit defined in `agents/config.py`.

The result is an incomplete analysis: the agent hits the iteration
cap, emits a `warning` event, and produces a truncated response
covering only the first 1-2 stocks. The remaining stocks are silently
dropped. Users have no visibility into which stocks were analysed and
no way to continue.

**Root cause:** The stock_analyst system prompt's COMPARISON PIPELINE
(lines 60-66 of `backend/agents/configs/stock_analyst.py`) instructs
the agent to call `fetch_multiple_stocks` then loop through all
tickers calling analyse + forecast. For a sector with 10+ stocks,
this easily requires 50+ tool calls — well beyond the iteration limit.

---

## Solution Overview

Replace the batch-all-at-once approach with an interactive,
user-controlled sequential flow. The agent presents a list of
candidate stocks with freshness metadata and renders clickable
action buttons. The user selects one stock at a time, triggering
the full single-stock pipeline (Steps 1-4). After each analysis
completes, the agent offers the next stock or a comparison option.

**Key design principles:**

1. **User controls the loop** — the agent never batch-analyses
   without explicit user action. Each stock is a separate chat turn.
2. **Backward compatible** — the `actions` field in the WebSocket
   protocol is optional; existing clients ignore it gracefully.
3. **Progressive data reuse** — stocks already analysed today
   (data in Iceberg + Redis cache) are flagged as fresh, skipping
   redundant API calls.
4. **Minimal agent changes** — the new `suggest_sector_stocks`
   tool is added to the stock_analyst tool list; the system prompt
   gains a DISCOVERY PIPELINE section. No changes to the existing
   STANDARD or COMPARISON pipelines.

---

## Backend Changes

### 1. New Tool: `suggest_sector_stocks`

**New file:** `backend/tools/sector_discovery_tool.py`

```python
@tool
def suggest_sector_stocks(sector: str) -> str:
    """Suggest stocks in a sector with data freshness status.

    Queries Iceberg company_info for stocks matching the
    sector. Checks each stock's analysis recency. Falls
    back to a hardcoded popular-stocks list when Iceberg
    has no data for the sector.

    Args:
        sector: Sector name, e.g. "Financial Services",
            "Technology", "Healthcare".

    Returns:
        JSON string with structure:
        {
            "sector": "Financial Services",
            "stocks": [
                {
                    "ticker": "SBIN.NS",
                    "company_name": "State Bank of India",
                    "status": "fresh",
                    "last_analysed": "2026-03-31"
                },
                {
                    "ticker": "HDFCBANK.NS",
                    "company_name": "HDFC Bank Ltd",
                    "status": "stale",
                    "last_analysed": "2026-03-25"
                },
                {
                    "ticker": "ICICIBANK.NS",
                    "company_name": "ICICI Bank Ltd",
                    "status": "no_data",
                    "last_analysed": null
                }
            ],
            "source": "iceberg"
        }
    """
```

**Implementation approach:**

1. Scan Iceberg `stocks.company_info` table with an `EqualTo("sector", sector)` predicate (new method `_scan_sector` on `StockRepository`, modeled after `_scan_ticker` at line 264 of `stocks/repository.py`).
2. For each matched ticker, check analysis freshness by calling `get_latest_company_info_if_fresh(ticker, date.today())` (existing method at line 898 of `stocks/repository.py`).
3. Also check Redis cache key `cache:dash:analysis:{ticker}` via `CacheService.get()` for recent analysis data.
4. Classify each stock as `fresh` (analysed today), `stale` (analysed but older than 1 day), or `no_data` (never analysed).
5. If Iceberg returns zero results for the sector, fall back to `_POPULAR_SECTOR_STOCKS` dict.
6. Return a JSON string consumed by the agent.

**Freshness logic:**
- `fresh`: `company_info.fetched_at` matches today AND `analysis_summary` exists for ticker with `analysed_at` today
- `stale`: data exists but `analysed_at` older than today
- `no_data`: no company_info record at all

### 2. Popular Sector Stocks Fallback

**In:** `backend/tools/sector_discovery_tool.py`

A hardcoded dictionary of top stocks per sector for Indian (NSE)
and US markets. Used when Iceberg has no company_info data for a
sector (cold start scenario).

```python
_POPULAR_SECTOR_STOCKS: dict[str, list[dict]] = {
    "Financial Services": [
        {"ticker": "SBIN.NS", "company_name": "State Bank of India"},
        {"ticker": "HDFCBANK.NS", "company_name": "HDFC Bank"},
        {"ticker": "ICICIBANK.NS", "company_name": "ICICI Bank"},
        {"ticker": "KOTAKBANK.NS", "company_name": "Kotak Mahindra Bank"},
        {"ticker": "AXISBANK.NS", "company_name": "Axis Bank"},
        {"ticker": "JPM", "company_name": "JPMorgan Chase"},
        {"ticker": "BAC", "company_name": "Bank of America"},
        {"ticker": "GS", "company_name": "Goldman Sachs"},
    ],
    "Technology": [
        {"ticker": "TCS.NS", "company_name": "Tata Consultancy Services"},
        {"ticker": "INFY.NS", "company_name": "Infosys"},
        {"ticker": "AAPL", "company_name": "Apple"},
        {"ticker": "MSFT", "company_name": "Microsoft"},
        {"ticker": "GOOGL", "company_name": "Alphabet"},
    ],
    # ... additional sectors
}
```

**Sector name normalization:** Lowercase + strip whitespace for
matching. Accept common aliases (e.g., "banking" maps to
"Financial Services", "tech" maps to "Technology").

### 3. New Repository Method: `_scan_sector`

**Modified file:** `stocks/repository.py`

```python
def get_stocks_by_sector(
    self,
    sector: str,
    selected_fields: list[str] | None = None,
) -> pd.DataFrame:
    """Scan company_info for all stocks in a sector.

    Uses EqualTo("sector", sector) predicate push-down.
    Falls back to full-table scan with pandas filtering.
    Returns the latest snapshot per ticker (deduped by
    most recent fetched_at).
    """
```

This follows the same pattern as `_scan_ticker` (line 264) but
filters on the `sector` column instead of `ticker`. Returns only
the latest row per ticker (deduped by `fetched_at DESC`).

### 4. Stock Analyst System Prompt Update

**Modified file:** `backend/agents/configs/stock_analyst.py`

Add a DISCOVERY PIPELINE section to `_STOCK_SYSTEM_PROMPT`:

```
DISCOVERY PIPELINE — for sector/category stock requests:
When a user asks to pick, discover, or suggest stocks from
a sector or industry:
1. Call suggest_sector_stocks with the sector name.
2. Present the results as a numbered list showing each
   stock's ticker, company name, and freshness status
   (already analysed / needs fresh analysis / no data).
3. DO NOT batch-analyse. Instead, ask the user which
   stock to analyse first.
4. After analysing one stock, suggest the next unanalysed
   stock or offer "Compare all analysed stocks".
5. Include an actions array in your response for the
   frontend to render as clickable buttons.

ACTIONS FORMAT — when suggesting stocks, include a JSON
block at the end of your response:
<!--actions:[{"label":"Analyse SBIN.NS →","prompt":"analyse SBIN.NS"},...]-->
The frontend will parse this and render clickable buttons.
```

Add `suggest_sector_stocks` to the tool_names list in
`STOCK_ANALYST_CONFIG` (line 147).

### 5. Tool Registration

**Modified file:** `backend/bootstrap.py`

```python
from tools.sector_discovery_tool import suggest_sector_stocks
registry.register(suggest_sector_stocks)
```

### 6. Actions Extraction in Synthesis Node

**Modified file:** `backend/agents/nodes/synthesis.py`

The synthesis node (which generates the `final_response`)
parses the agent's response for an `<!--actions:[...]-->`
HTML comment block. If found, it extracts the JSON array
and attaches it to the graph state as `response_actions`.

```python
import re, json

_ACTIONS_RE = re.compile(
    r"<!--actions:(.*?)-->", re.DOTALL,
)

def _extract_actions(text: str) -> tuple[str, list[dict]]:
    """Strip actions block from text, return (clean_text, actions)."""
    m = _ACTIONS_RE.search(text)
    if not m:
        return text, []
    try:
        actions = json.loads(m.group(1))
    except (json.JSONDecodeError, TypeError):
        return text, []
    clean = text[:m.start()] + text[m.end():]
    return clean.strip(), actions
```

### 7. Graph State Extension

**Modified file:** `backend/agents/graph_state.py`

Add `response_actions: list[dict]` to `AgentState` with
default `[]`. This carries the parsed actions through to
the WebSocket emission layer.

### 8. WebSocket Emission Update

**Modified file:** `backend/ws.py`

In `_run_graph()` at line 472, include `actions` in the
final event when present:

```python
final_event = {
    "type": "final",
    "response": result.get("final_response", ""),
    "agent": result.get("current_agent", ""),
}
actions = result.get("response_actions", [])
if actions:
    final_event["actions"] = actions
event_queue.put(final_event)
```

This is backward compatible — the `actions` key is only
present when the agent explicitly includes action buttons.

---

## Frontend Changes

### 1. Message Type Extension

**Modified file:** `frontend/lib/constants.tsx`

```typescript
export interface Message {
  role: "user" | "assistant";
  content: string;
  timestamp: Date;
  actions?: ActionButton[];  // NEW — optional
}

export interface ActionButton {
  label: string;
  prompt: string;
}
```

### 2. Actions Parsing in useSendMessage

**Modified file:** `frontend/hooks/useSendMessage.ts`

In the `handleEvent` callback (line 73), when processing a
`final` event, extract the `actions` array and attach it to
the assistant message:

```typescript
} else if (event.type === "final") {
  // ... existing tool line prepend ...
  const actions = (event.actions as ActionButton[]) || [];
  setMessages([
    ...updatedMessages,
    {
      role: "assistant",
      content: response,
      timestamp: new Date(),
      ...(actions.length > 0 ? { actions } : {}),
    },
  ]);
  // ...
}
```

The `actions` field from the WebSocket event is optional.
If absent or empty, no `actions` property is set on the
message — fully backward compatible.

### 3. ActionButtons Component

**New file:** `frontend/components/ActionButtons.tsx`

```typescript
interface ActionButtonsProps {
  actions: ActionButton[];
  onAction: (prompt: string) => void;
  disabled?: boolean;
}

export function ActionButtons({
  actions, onAction, disabled,
}: ActionButtonsProps) {
  return (
    <div className="flex flex-wrap gap-2 mt-2">
      {actions.map((action, i) => (
        <button
          key={i}
          onClick={() => onAction(action.prompt)}
          disabled={disabled}
          className="inline-flex items-center gap-1.5
            px-3 py-1.5 rounded-full text-xs font-medium
            bg-indigo-50 dark:bg-indigo-900/30
            text-indigo-700 dark:text-indigo-300
            border border-indigo-200 dark:border-indigo-700
            hover:bg-indigo-100 dark:hover:bg-indigo-900/50
            transition-colors cursor-pointer
            disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {action.label}
          <svg className="w-3 h-3" viewBox="0 0 24 24"
            fill="none" stroke="currentColor"
            strokeWidth="2.5">
            <path d="M5 12h14M12 5l7 7-7 7" />
          </svg>
        </button>
      ))}
    </div>
  );
}
```

**Visual design:** Pill-shaped buttons (rounded-full) in the
indigo color family matching the existing chat theme. Each
button shows the stock ticker label and a right-arrow icon.
Disabled state during loading prevents double-clicks.

### 4. MessageBubble Integration

**Modified file:** `frontend/components/MessageBubble.tsx`

Render `ActionButtons` below the assistant message content
when `msg.actions` is present:

```typescript
import { ActionButtons } from "./ActionButtons";

// Inside the assistant message branch, after MarkdownContent:
{msg.role === "assistant" && msg.actions?.length > 0 && (
  <ActionButtons
    actions={msg.actions}
    onAction={onActionClick}
    disabled={false}
  />
)}
```

The `onActionClick` callback is passed down from `ChatPanel`.

### 5. Action Click Handler in ChatPanel

**Modified file:** `frontend/components/ChatPanel.tsx`

When a user clicks an action button, inject the prompt into
the input and auto-submit:

```typescript
const handleActionClick = useCallback(
  (prompt: string) => {
    setInput(prompt);
    // Use requestAnimationFrame to ensure state update
    // propagates before sendMessage reads the input.
    requestAnimationFrame(() => {
      sendMessage();
    });
  },
  [setInput, sendMessage],
);
```

**Alternative approach (more reliable):** Instead of setting
input then calling sendMessage, create a `sendDirect(prompt)`
method in `useSendMessage` that bypasses the input state:

```typescript
const sendDirect = useCallback(
  async (prompt: string) => {
    // Same logic as sendMessage but uses prompt directly
    // instead of reading from input state
    const userMessage: Message = {
      role: "user",
      content: prompt,
      timestamp: new Date(),
    };
    // ... rest of send flow
  },
  [/* deps */],
);
```

This avoids the React state timing issue where `sendMessage`
reads the stale `input` value before `setInput` propagates.

### 6. MessageBubble Props Extension

**Modified file:** `frontend/components/MessageBubble.tsx`

Add `onActionClick` to `MessageBubbleProps`:

```typescript
interface MessageBubbleProps {
  message: Message;
  onInternalLink: (href: string) => void;
  onActionClick?: (prompt: string) => void;  // NEW
}
```

---

## WebSocket Protocol

### Current Protocol (no change to existing events)

```json
{"type": "thinking", "iteration": 1}
{"type": "tool_start", "tool": "fetch_stock_data", "args": {...}}
{"type": "tool_done", "tool": "fetch_stock_data"}
{"type": "final", "response": "...markdown...", "agent": "stock_analyst"}
```

### Extended Protocol (additive)

```json
{
  "type": "final",
  "response": "Here are Financial Services stocks I found...",
  "agent": "stock_analyst",
  "actions": [
    {"label": "Analyse SBIN.NS →", "prompt": "analyse SBIN.NS"},
    {"label": "Analyse HDFCBANK.NS →", "prompt": "analyse HDFCBANK.NS"},
    {"label": "Analyse ICICIBANK.NS →", "prompt": "analyse ICICIBANK.NS"}
  ]
}
```

**Post-analysis event** (after single stock completes):

```json
{
  "type": "final",
  "response": "## SBIN.NS Analysis\n\n...",
  "agent": "stock_analyst",
  "actions": [
    {"label": "Next: Analyse HDFCBANK.NS →", "prompt": "analyse HDFCBANK.NS"},
    {"label": "Compare all analysed →", "prompt": "compare SBIN.NS"}
  ]
}
```

**Backward compatibility:** The `actions` field is:
- Optional in the WebSocket event (only present when agent
  includes action suggestions)
- Optional in the `Message` TypeScript interface (the `?`
  makes it undefined by default)
- Ignored by the existing `handleEvent` logic (unknown
  fields on the event object are simply not read)

No migration or version negotiation is needed.

---

## Data Flow

```
User: "pick stocks from financial services to rebalance my portfolio"

  → LLM classifier: intent = "stock_analysis"
  → Supervisor: next_agent = "stock_analyst"
  → Stock analyst detects sector discovery request
  → Calls suggest_sector_stocks("Financial Services")
    → Iceberg scan: company_info WHERE sector = "Financial Services"
    → Found 6 stocks, check freshness for each
    → Returns JSON with tickers + status
  → Agent formats numbered list + actions block
  → Synthesis node extracts <!--actions:[...]--> → response_actions
  → WS emits: {type: "final", response: "...", actions: [...]}

Frontend:
  → useSendMessage.handleEvent receives final event
  → Creates Message with actions array
  → MessageBubble renders markdown content
  → ActionButtons renders 6 pill buttons below message

User clicks "Analyse SBIN.NS →"

  → ChatPanel.handleActionClick("analyse SBIN.NS")
  → sendDirect("analyse SBIN.NS") — creates user message, sends via WS
  → Backend receives "analyse SBIN.NS"
  → LLM classifier: intent = "stock_analysis"
  → Stock analyst: STANDARD PIPELINE
    → Step 1: fetch_stock_data("SBIN.NS") + get_stock_info("SBIN.NS")
    → Step 2: analyse_stock_price("SBIN.NS") + forecast_stock("SBIN.NS")
    → Step 3: get_ticker_news("SBIN.NS") + get_analyst_recommendations("SBIN.NS")
    → Step 4: verdict with Buy/Hold/Sell
  → format_response prepends data template
  → Agent appends next-stock actions:
    <!--actions:[{"label":"Next: Analyse HDFCBANK.NS →", ...}, {"label":"Compare all analysed →", ...}]-->
  → WS emits final with next-stock actions

User clicks "Compare all analysed →"

  → Sends "compare SBIN.NS, HDFCBANK.NS" (tickers tracked in
    ConversationContext.tickers_mentioned)
  → Stock analyst: COMPARISON PIPELINE (existing, works for 2-3 stocks)
```

---

## Files Changed

| File | Change | Effort |
|------|--------|--------|
| `backend/tools/sector_discovery_tool.py` | NEW | Medium |
| `stocks/repository.py` | ADD `get_stocks_by_sector()` | Small |
| `backend/agents/configs/stock_analyst.py` | ADD discovery pipeline to prompt + tool list | Small |
| `backend/bootstrap.py` | REGISTER new tool | Trivial |
| `backend/agents/graph_state.py` | ADD `response_actions` field | Trivial |
| `backend/agents/nodes/synthesis.py` | ADD actions extraction | Small |
| `backend/ws.py` | ADD actions to final event | Trivial |
| `frontend/lib/constants.tsx` | ADD `actions` to Message, ADD ActionButton type | Trivial |
| `frontend/components/ActionButtons.tsx` | NEW | Small |
| `frontend/components/MessageBubble.tsx` | ADD action buttons rendering | Small |
| `frontend/components/ChatPanel.tsx` | ADD handleActionClick, pass to MessageBubble | Small |
| `frontend/hooks/useSendMessage.ts` | ADD sendDirect, parse actions from event | Small |
| `tests/backend/test_sector_discovery.py` | NEW | Medium |
| `tests/backend/test_actions_extraction.py` | NEW | Small |
| `tests/frontend/ActionButtons.test.tsx` | NEW | Small |

---

## Acceptance Criteria

1. **Discovery flow:** User sends "pick stocks from financial
   services" and receives a numbered list of stocks with freshness
   indicators — NOT a batch analysis.

2. **Action buttons rendered:** The assistant message shows
   clickable pill-shaped buttons below the stock list text.

3. **Click-to-analyse:** Clicking "Analyse SBIN.NS" injects
   the prompt into chat and auto-submits. The full standard
   pipeline runs for that single stock.

4. **Sequential flow:** After analysis completes, the response
   includes "Next: Analyse [ticker]" and "Compare all analysed"
   action buttons.

5. **Freshness indicators:** Stocks already analysed today show
   a "fresh" badge; stocks with stale data show "needs update";
   stocks with no data show "new".

6. **Fallback coverage:** When Iceberg has no company_info for a
   sector, the hardcoded popular stocks list is used.

7. **Backward compatibility:** Messages without `actions` render
   identically to current behavior. No regression in existing
   chat functionality.

8. **Iteration limit safe:** The discovery + single-stock flow
   never exceeds MAX_ITERATIONS. Discovery requires 1 tool call;
   single-stock analysis requires 6-8 tool calls.

9. **Mobile support:** Action buttons wrap correctly on mobile
   (flex-wrap) and are large enough for touch targets (min 44px
   height via py-1.5 + text size).

10. **Comparison works:** After analysing 2+ stocks sequentially,
    the "Compare all analysed" button triggers the existing
    COMPARISON PIPELINE with only the analysed tickers — not the
    full sector.

---

## Edge Cases

- **Empty sector:** Iceberg has no stocks for the requested sector
  AND no hardcoded fallback exists. Agent responds with: "I don't
  have data for that sector yet. Try one of these sectors: [list]."

- **Sector name mismatch:** User says "banking" but Iceberg stores
  "Financial Services". The tool applies fuzzy matching via a
  sector alias map before querying.

- **All stocks already fresh:** If all sector stocks were analysed
  today, the agent notes this and offers "Compare all" directly
  instead of suggesting re-analysis.

- **Mid-flow topic change:** User clicks "Analyse SBIN.NS" but
  then types a different question before it completes. The existing
  `streaming` guard in ws.py (line 106) blocks concurrent requests.
  The user must wait for the current analysis to finish.

- **Actions on HTTP fallback:** The HTTP streaming path in
  `useSendMessage.ts` also parses the `actions` field from the
  final NDJSON event. Same extraction logic applies.

- **Large sector (20+ stocks):** Paginate the suggestion list to
  show 8-10 stocks maximum, with a "Show more stocks..." action
  button that loads the next batch.

---

## Token Budget Impact

| Component | Tokens/turn | Who pays |
|-----------|------------|----------|
| suggest_sector_stocks call | ~100 (tool input/output) | Main LLM |
| Discovery prompt addition | ~150 (system prompt) | Main LLM |
| Actions block in response | ~80 (hidden comment) | Main LLM |
| **Discovery turn total** | **~330** | **One Groq call** |
| Single-stock analysis | Same as today (~6 tool calls) | Existing |

The discovery turn is lightweight — one tool call returning a
JSON list. The expensive work (Yahoo Finance API calls, Prophet
forecasting) only happens when the user explicitly clicks a stock.
This actually reduces overall token usage compared to the current
batch approach, which burns tokens attempting all stocks.
