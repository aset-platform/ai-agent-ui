# Intent-Aware Routing

## Overview
Guardrail runs a keyword-based intent router (best_intent) BEFORE the LLM topic classifier, enabling fast deterministic routing for most queries.

## Three Routing Branches
1. **Same-intent follow-up**: best_intent matches last_agent intent → reuse agent (skip router entirely)
2. **Intent switch**: best_intent returns a different intent than last_agent → route through full router
3. **Ambiguous**: tied scores across intents → fall through to LLM topic classifier for disambiguation

## Key Functions
- `score_intents()` in `router_node.py` — scores query against all intent keyword sets
- `best_intent()` in `router_node.py` — returns top intent or None if tied/ambiguous
- `classify_followup()` in `topic_classifier.py` — LLM-based fallback for ambiguous cases

## Clarification Flow
When `best_intent()` returns tied scores, the guardrail defers to the LLM classifier. If the classifier is also uncertain, the system can ask a clarifying question rather than routing incorrectly.

## Files
- `backend/agents/nodes/guardrail.py` — orchestrates the routing decision
- `backend/agents/nodes/router_node.py` — `score_intents()`, `best_intent()`
- `backend/agents/nodes/topic_classifier.py` — `classify_followup()` LLM fallback

## Added
2026-03-31
