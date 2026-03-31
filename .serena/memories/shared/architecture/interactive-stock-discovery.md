# Interactive Stock Discovery

## Overview
End-to-end pipeline for suggesting stocks by sector and enabling one-click analysis via action buttons in the chat UI.

## Backend Components
- **`suggest_sector_stocks` tool**: Iceberg scan + popular ticker fallback + freshness check
- **DISCOVERY PIPELINE**: Added to `stock_analyst` and `portfolio` agent prompts
- **Actions extraction**: `synthesis.py` parses `<!--actions:[]-->` HTML comments from LLM output
- **Graph state**: `response_actions` field carries extracted actions through the graph
- **WebSocket**: `final` event includes `actions` payload for frontend rendering

## Frontend Components
- **`ActionButtons` component**: Renders clickable action buttons below chat messages
- **`sendDirect` hook**: Sends action payload directly as a new user message (bypasses input box)

## Design Spec
- `docs/superpowers/specs/2026-03-31-interactive-stock-discovery-design.md`

## Added
2026-03-31
