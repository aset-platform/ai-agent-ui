# Jira MCP Usage Conventions

## Board & Sprint IDs
- Board: ASETPLTFRM board (id=34, scrum)
- Sprint 1: ASET Sprint 1 (id=35, active, ends 2026-03-18)
- Sprint 2: ASET Sprint 2 (id=36, 2026-03-19 to 2026-03-25)
- Sprint 3: ASET Sprint 3 (id=37, 2026-03-26 to 2026-04-01)

## Custom Field IDs
- Story points: `customfield_10016` (jsw-story-points, the one the board uses)
- Story Points (legacy): `customfield_10036` — NOT on Bug screens, may error
- Start date: `customfield_10015` (datepicker)
- Sprint: `customfield_10020`

## Important Patterns
- When updating story points, use `customfield_10016`. Also set `customfield_10036` but catch errors (not available on all issue types).
- When updating fields via REST API, update fields **individually** — batch updates silently fail if one field errors.
- Transition IDs: To Do=11, In Progress=21, Done=31
- MCP env var substitution (`${VAR}`) does NOT work in `.claude.json` — MCP servers need raw values or env vars inherited from the launching shell.

## Backlog Query
```
sprint is EMPTY AND project = ASETPLTFRM ORDER BY priority DESC, created ASC
```
