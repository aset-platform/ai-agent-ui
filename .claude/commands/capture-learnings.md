---
description: Route this session's new rules/patterns/gotchas into the right doc tier (CLAUDE.md hard rule · .claude/rules/*.md path-scoped · Serena memory) to keep CLAUDE.md slim
allowed-tools: [Read, Edit, Write, Bash, Glob, Grep, mcp__serena__list_memories, mcp__serena__read_memory]
version: 1.0.0
rollback: git checkout -- CLAUDE.md .claude/rules/ (uncommitted) or revert the commit
observe: log each candidate, chosen tier, target file, and net lines added to CLAUDE.md
feedback: user approves/edits the routing table before any file is written
---

# /capture-learnings — Route session learnings into the right doc tier

Capture the rules, patterns, and gotchas decided this session and file
each into the correct tier so `CLAUDE.md` stays slim. This is a **router**,
not an auditor: it places NEW learnings; it does not refactor existing
CLAUDE.md bloat.

The 3-tier system (established commit `1d66720`):

1. **`CLAUDE.md`** — always loaded (~11k tok). Hard rules (§4) + one-line
   pattern pointers (§5). Every byte here costs every session.
2. **`.claude/rules/*.md`** — lazy-loaded via `paths:` frontmatter. Loads
   only when matching files are touched.
3. **Serena memories** — deep detail, on-demand via `→ memory-name`.

## Process

### Step 1: Gather candidates

Invoked with no arguments. Build a candidate list from two sources:

1. **This conversation** — rules, conventions, gotchas, or decisions the
   user confirmed or that emerged from debugging. Look for: "always/never
   X", "X breaks when Y", config decisions, regression fixes.
2. **Recent git diff** — run the commands below to see what actually
   changed; a code change that prevents a class of bug is a candidate rule.

```bash
git log --oneline dev..HEAD           # commits on this branch
git diff --stat dev...HEAD            # files touched
git log --since="6am" --oneline       # today's work if not branch-based
```

Skip anything already covered by CLAUDE.md, an existing rules file, or a
memory (grep before proposing — see Step 3). If no genuine candidates,
say so and stop. Do NOT invent rules to fill a table.

### Step 2: Classify each candidate (routing rubric)

Apply in order; first match wins:

| If the learning is… | Tier → target |
|---|---|
| Non-negotiable, cross-cutting, regression-preventing, applies regardless of which file you are in | **CLAUDE.md §4** hard rule — terse, numbered |
| Broadly applicable but carries domain detail | **§5 one-line pointer** in CLAUDE.md **+** detail in a `.claude/rules/*.md` |
| Path-scoped — only matters when touching specific dirs/files | **`.claude/rules/<domain>.md`** — existing file if `paths:` matches; else NEW file w/ `paths:` frontmatter **+** a §5.x pointer line in CLAUDE.md |
| Deep narrative / debugging story / reference detail | **Serena memory** → hand off to `/promote-memory`; wire `→ name` pointer |

### Step 3: Dedup check (before proposing)

For each candidate, confirm it is not already documented:

```bash
grep -rn "<keyword>" CLAUDE.md .claude/rules/
```

Use `mcp__serena__list_memories` (and `read_memory` on near-matches) to
check the memory tier. If a candidate already exists, drop it or propose
an **update** to the existing location instead of a new entry.

### Step 4: Present the routing table — WAIT for approval

Print one row per candidate and stop for the user to approve/edit:

```
# | Candidate (one line)        | Tier            | Target file                  | Δ CLAUDE.md
1 | Scoped Iceberg deletes ...  | rules (exists)  | .claude/rules/algo.md        | +0 lines
2 | NullPool for sync→async ... | §4 hard rule    | CLAUDE.md §4.1               | +1 line
3 | Live square-off GTT race    | memory          | → /promote-memory            | +1 line (pointer)
```

Report the **net lines added to CLAUDE.md** as a total. Do not write any
file until the user approves. The user may re-tier, reword, or drop rows.

### Step 5: Anti-bloat guardrails (enforce while writing)

- **≤1 line added to CLAUDE.md per item.** If detail needs >1–2 lines, it
  is forbidden from the CLAUDE.md body — push it to a rules file or memory
  and leave only a pointer.
- **No duplication** — if detail lives in a rules file/memory, CLAUDE.md
  gets the pointer only.
- **Prefer an existing rules file** over creating a new one.
- Match the terse, code-id-dense voice of the surrounding section.

### Step 6: Write the approved placements

- **CLAUDE.md** — `Edit` to insert the hard rule (next number in §4.x) or
  the §5 pointer line. Keep line length ≤79 chars.
- **Existing rules file** — `Edit` to append under the right heading.
- **New rules file** — `Write` `.claude/rules/<domain>.md` with `paths:`
  frontmatter (glob the dirs/files it applies to), the mirror heading, and
  the detail. Then add the §5.x pointer line in CLAUDE.md.
- **Memory tier** — draft the cleaned content, wire the `→ name` pointer in
  CLAUDE.md/rules, then tell the user to run `/promote-memory <name>` to
  write the Serena memory (this command does not write memories itself).

### Step 7: Report

Summarize: per candidate — tier, file, action (added/updated/handed off).
Print the final net CLAUDE.md line delta and remind the user to commit
(`git add CLAUDE.md .claude/rules/` — do NOT auto-commit).

## Out of scope

- **Not an auditor** — does not scan existing CLAUDE.md for legacy bloat.
- **Does not write Serena memories** — hands off to `/promote-memory`.
- **Does not touch** PROGRESS.md, `.serena/` staging, or README (those are
  separate §25 doc triggers).
- **Does not route to AutoMem** (`~/.claude-futurepath/…`) — auto-managed,
  separate from this project's tier system.
