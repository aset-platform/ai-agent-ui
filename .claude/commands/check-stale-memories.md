---
description: Check shared Serena memories for stale references to renamed/deleted code
allowed-tools: [Read, Glob, Grep, Bash, mcp__serena__list_memories, mcp__serena__read_memory, mcp__serena__find_symbol, mcp__serena__search_for_pattern, mcp__serena__write_memory]
---

# /check-stale-memories — Detect Stale Shared Memories

Scan shared Serena memories for references to code that no longer
exists or has been significantly refactored.

## Process

### Step 1: List shared memories

Use `mcp__serena__list_memories` filtered to `shared/` topic.

### Step 2: For each shared memory

Use `mcp__serena__read_memory` to read the content. Extract:

1. **File paths** — any path like `backend/foo.py`, `auth/bar.py`
2. **Symbol names** — class names, function names, variable names
   referenced in code blocks or backtick references
3. **Config references** — env var names, config keys

### Step 3: Validate references

For each extracted reference:

- **File paths**: Use `Glob` to check if the file exists.
- **Symbol names**: Use `mcp__serena__find_symbol` to check if the
  symbol exists in the codebase.
- **Config references**: Use `Grep` to search for the config key.

### Step 4: Assess conceptual staleness

Beyond just missing references, check if the memory's description
of behavior still matches reality. For example:

- Memory says "function X does Y" — read function X and verify.
- Memory says "pattern A is used in module B" — verify the pattern.

Use `mcp__serena__search_for_pattern` for flexible matching.

### Step 5: Report findings

Present a table:

| Memory | Status | Issues |
|--------|--------|--------|
| shared/architecture/system-overview | OK | — |
| shared/conventions/python-style | STALE | `_ticker_linker.py` renamed |
| ... | ... | ... |

### Step 6: Suggest fixes

For each stale memory, suggest one of:

- **Update**: Provide the corrected content.
- **Remove**: If the memory is entirely obsolete.
- **Merge**: If the memory should be combined with another.

Ask the user which action to take for each stale memory.
If updating, use `mcp__serena__write_memory` to write the fix,
then commit on a `docs/fix-stale-memory-<name>` branch.
