# Team Knowledge Sharing Ecosystem — Design Document

**Date:** 2026-03-09
**Status:** Approved
**Scope:** ai-agent-ui — 4-5 developers, Claude Code + Serena tooling

---

## 1. Problem

All project context (Serena memories, indexed docs, conventions) is
local to one developer. Onboarding new devs means they start with
zero AI context. The current `CLAUDE.md` (~400 lines, ~3,500 tokens)
is loaded into every message, wasting context window on content that
could be served on-demand.

## 2. Goals

- Share architectural decisions, conventions, and debugging knowledge
  across the team via git-committed Serena memories.
- Reduce per-message token usage by slimming `CLAUDE.md` to
  ~80-100 lines (~800-1,000 tokens).
- Keep session progress and personal preferences local/gitignored.
- Automate memory promotion from personal to shared with AI cleanup.
- Detect stale and conflicting memories before they mislead the team.
- Onboard new developers in ~5 minutes with a single script.

## 3. Design

### 3.1 Slim `CLAUDE.md` (~80-100 lines)

Retains only:

- Project overview table (services, ports, stacks).
- Quick-start commands (`run.sh`, venv activation).
- Key directories and config files (one-liner each).
- **Hard rules block** — 10-12 non-negotiable rules:
  - Line length 79 chars; no bare `print()`; `X | None` not
    `Optional[X]`.
  - Branch off `dev`; never push to protected branches.
  - `apiFetch` not `fetch`; `<Image />` not `<img>`.
  - All state in class instances; no module-level mutable globals.
  - Patch at SOURCE module, not importing module.
  - Update `PROGRESS.md` after every session.
- Pointer to Serena: "For detailed architecture, conventions, and
  debugging — use Serena shared memories (`list_memories`)."

**Token savings:** ~2,500 tokens per message.

### 3.2 Shared Serena Memories (git-committed, PR-reviewed)

```
.serena/memories/shared/
├── architecture/
│   ├── system-overview.md
│   ├── agent-init-pattern.md
│   ├── groq-chunking-strategy.md
│   ├── iceberg-data-layer.md
│   └── auth-jwt-flow.md
├── conventions/
│   ├── python-style.md
│   ├── typescript-style.md
│   ├── git-workflow.md
│   ├── testing-patterns.md
│   ├── performance.md
│   └── error-handling.md
├── debugging/
│   ├── common-issues.md
│   ├── mock-patching-gotchas.md
│   └── (future entries as discovered)
├── onboarding/
│   └── setup-guide.md
└── api/
    └── streaming-protocol.md
```

Total: ~15 files at launch. Each file covers one focused topic
(reduces merge conflicts, easy to discover by name).

### 3.3 Local Memories (gitignored)

```
.serena/memories/session/    # Daily progress (e.g. 2026-03-09-*)
.serena/memories/personal/   # Individual prefs, workflow notes
```

Written freely by AI sessions via `/sc:save`. Never committed.

### 3.4 `.gitignore` Changes

**Project `.gitignore`** — replace blanket `.serena/` with:

```gitignore
# Serena — selective ignore
.serena/cache/
.serena/project.local.yml
.serena/memories/session/
.serena/memories/personal/
```

**`.serena/.gitignore`** — update to match:

```gitignore
/cache
/project.local.yml
/memories/session/
/memories/personal/
```

This tracks `project.yml` and `memories/shared/` in git.

### 3.5 Content Migration Map

| Current CLAUDE.md Section | Target Serena Memory |
|---|---|
| S2: Architecture (core patterns, Iceberg, filesystem, auth) | `shared/architecture/system-overview.md`, `iceberg-data-layer.md`, `auth-jwt-flow.md` |
| S3.2: Python rules, line wrapping, black gotchas | `shared/conventions/python-style.md` |
| S3.3: TypeScript rules | `shared/conventions/typescript-style.md` |
| S3.4-3.5: OOP conventions, lint commands | Merged into respective convention files |
| S3.6: Testing (mock patching, DataFrame, E2E) | `shared/conventions/testing-patterns.md` + `shared/debugging/mock-patching-gotchas.md` |
| S4: Git branching + S5: PR review rules + S9: Tone | `shared/conventions/git-workflow.md` |
| S6: Anti-patterns | Split across relevant convention files |
| S8: Performance review | `shared/conventions/performance.md` |
| S10: Error handling & logging | `shared/conventions/error-handling.md` |
| S11: Dependency management | `shared/onboarding/setup-guide.md` |
| S12: Documentation standards | `shared/conventions/git-workflow.md` (appended) |
| S13: Debugging & troubleshooting | `shared/debugging/common-issues.md` |
| Appendix: Deployment, hooks, known limitations | `shared/onboarding/setup-guide.md` |

### 3.6 `/promote-memory` Claude Code Skill

Interactive skill that promotes a session/personal memory to shared:

1. Lists available session/personal memories.
2. Asks which memory to promote and which shared category.
3. Uses Claude to clean content: strips session-specific context
   (dates, "today I did", uncommitted references), generalizes
   findings, fits team voice.
4. Writes cleaned content to `shared/<category>/<name>.md` via
   Serena `write_memory`.
5. Creates `docs/promote-memory-<name>` branch and commits.
6. Instructs dev to push and create PR.

**Why AI-powered, not regex:** Claude understands context, can
distinguish reusable insight from session noise, and produces
clean team-quality documentation.

### 3.7 Memory Conflict Resolution

**Prevention:**
- Each shared memory covers one focused topic (small files).
- Narrow scope reduces likelihood of concurrent edits to the same
  file.

**Detection:**
- PR checks flag conflicts in `memories/shared/` files.
- Standard git merge surfaces conflicts like any other file.

**Resolution convention:**
- When conflicts occur, the resolving dev uses `/promote-memory` to
  re-clean the merged content rather than hand-editing conflict
  markers.
- This ensures AI-quality output and consistent formatting.

### 3.8 Stale Memory Detection

Memories referencing renamed/deleted code become misleading. Two-layer
detection:

**Layer 1: CI script (`scripts/check-stale-memories.sh`)**
- Runs on PRs that touch `backend/`, `auth/`, `stocks/`,
  `dashboard/`, or `frontend/` — skips docs-only PRs.
- Scans each `memories/shared/*.md` file for references to specific
  files, functions, or class names.
- Cross-references against the current codebase (do those
  files/symbols still exist?).
- Reports memories with broken references as "potentially stale."
- Outputs a list for review — no auto-deletion.
- Exit code 0 (non-blocking warning, not a gate).

**Layer 2: `/check-stale-memories` Claude Code skill**
- Manual trigger for deeper review.
- Uses Serena's `find_symbol` and `search_for_pattern` for
  semantic validation (not just filename matching).
- Can detect conceptual staleness (e.g., memory describes a pattern
  that was refactored away, even if the file still exists).
- Suggests updates or flags for removal.

### 3.9 Developer Setup Script (`scripts/dev-setup.sh`)

Single-command onboarding for new developers:

```bash
./scripts/dev-setup.sh
```

Steps (in order):

1. **Verify prerequisites** — Python 3.12, Node.js, npm, git.
2. **Create virtualenv** — `~/.ai-agent-ui/venv` if not exists,
   install dependencies.
3. **Setup env files** — copy templates, create symlinks
   (`backend/.env`, `frontend/.env.local`).
4. **Verify Claude Code** — check `claude` CLI is available.
5. **Verify Serena** — check MCP server is configured.
6. **Update `.gitignore`** — ensure selective Serena ignoring is
   in place (not blanket `.serena/`).
7. **Run Serena onboarding** — index the codebase.
8. **Verify shared memories** — confirm `memories/shared/` exists
   and has expected files.
9. **Create local directories** — `memories/session/`,
   `memories/personal/` with `.gitkeep`.
10. **Install git hooks** — copy `hooks/pre-commit`,
    `hooks/pre-push`.
11. **Print summary** — what's ready, what needs manual steps
    (API keys, `gh auth login`).

**Does NOT:** install Claude Code/Serena (prereqs), set API keys
(security), or run database migrations (separate concern).

**Estimated onboarding time:** ~5 minutes.

## 4. Daily Workflow

```
Developer session
    |
    +-- AI reads slim CLAUDE.md (800 tokens, always loaded)
    +-- AI loads relevant shared memories on-demand via Serena
    +-- /sc:save writes to session/ (personal, gitignored)
    |
    +-- Found a reusable insight?
    |    |
    |    +-- /promote-memory
    |         +-- Cleans content (AI-powered)
    |         +-- Writes to shared/<category>/
    |         +-- Commits on docs/ branch
    |         +-- Dev creates PR -> team reviews -> merged
    |
    +-- Pull from origin?
    |    +-- Shared memories update automatically (just .md files)
    |
    +-- PR touches code?
         +-- CI runs check-stale-memories.sh
         +-- Warns if any shared memories reference missing symbols
```

## 5. Out of Scope

- Cross-repo memory sharing (single repo for now).
- Real-time sync (git pull is sufficient for 4-5 devs).
- Memory versioning beyond git history.

## 6. Deliverables

| # | Deliverable | Type |
|---|---|---|
| 1 | Slim `CLAUDE.md` (~80-100 lines) | File edit |
| 2 | 15 shared Serena memory files | New files |
| 3 | `.gitignore` updates (project + `.serena/`) | File edits |
| 4 | `/promote-memory` Claude Code skill | New skill |
| 5 | `/check-stale-memories` Claude Code skill | New skill |
| 6 | `scripts/check-stale-memories.sh` CI script | New file |
| 7 | `scripts/dev-setup.sh` onboarding script | New file |
| 8 | Update existing Serena memories to shared taxonomy | File moves |
