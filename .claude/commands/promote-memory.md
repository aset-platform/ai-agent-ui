---
description: Promote a session/personal Serena memory to shared (team) memory with AI cleanup
allowed-tools: [Read, Write, Edit, Bash, Glob, Grep, mcp__serena__list_memories, mcp__serena__read_memory, mcp__serena__write_memory, mcp__serena__delete_memory]
---

# /promote-memory — Promote Session Memory to Shared

Promote a personal or session Serena memory to the shared team
knowledge base with intelligent cleanup.

## Process

### Step 1: List available memories

Use `mcp__serena__list_memories` to list all memories. Present
the session/ and personal/ memories to the user. Ask which one
to promote.

If $ARGUMENTS is provided, use it as the memory name directly.

### Step 2: Read the source memory

Use `mcp__serena__read_memory` to read the selected memory content.

### Step 3: Ask target category

Present these categories and ask the user to pick one:

- `shared/architecture/` — system design decisions
- `shared/conventions/` — coding standards, workflow rules
- `shared/debugging/` — gotchas, workarounds, common issues
- `shared/onboarding/` — setup steps, env config
- `shared/api/` — endpoint contracts, data flows

Ask the user to provide a short name for the memory file
(e.g., `new-caching-pattern`).

### Step 4: Clean the content

Transform the session memory into team-quality documentation:

1. **Remove session-specific context**: dates ("today", "this
   session"), references to uncommitted work, personal progress
   notes, "I did X" language.
2. **Generalize findings**: Replace specific debugging sessions
   with reusable patterns. Turn "I found that X breaks when Y"
   into "X breaks when Y — fix by doing Z."
3. **Structure consistently**: Use headers, bullet points, code
   blocks. Match the style of existing shared memories.
4. **Keep it focused**: One topic per memory. If the source
   covers multiple topics, ask the user which to extract.

### Step 5: Write the shared memory

Use `mcp__serena__write_memory` with the name
`shared/<category>/<name>` and the cleaned content.

### Step 6: Create branch and commit

```bash
git checkout -b docs/promote-memory-<name>
git add .serena/memories/shared/<category>/<name>.md
git commit -m "docs: add shared memory — <category>/<name>"
```

### Step 7: Instruct the user

Tell the user:

> Memory promoted to `shared/<category>/<name>`.
> Branch: `docs/promote-memory-<name>`.
>
> Next steps:
> 1. `git push -u origin docs/promote-memory-<name>`
> 2. Create PR to `dev` with title `[docs] Add shared memory: <name>`
> 3. Get 1 approval, then merge.

### Step 8: Optionally delete the source

Ask the user if they want to delete the original session/personal
memory. If yes, use `mcp__serena__delete_memory`.
