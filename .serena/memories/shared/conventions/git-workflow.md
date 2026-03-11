# Git Workflow & PR Conventions

## Golden Rule

> Changes flow UP, sync flows DOWN. After every merge UP,
> immediately merge DOWN.

```
feature/* -> dev -> qa -> release -> main    (UP via PR)
               ^         ^          ^
         merge back merge back merge back    (DOWN immediately)
```

## Session Start (ALWAYS — NO EXCEPTIONS)

```bash
git fetch origin && git checkout dev && git pull origin dev
git checkout -b feature/<short-description>
git branch --show-current   # confirm before touching files
```

- NEVER commit directly to `dev`, `qa`, `release`, or `main`.
- ALWAYS branch off `dev`. If `feature/*` exists, check it out.

## Conventions

| Item | Format |
|------|--------|
| PR title | `[TYPE] Short description` — feat/fix/chore/refactor/hotfix/docs |
| Commit | `type: description` — feat/fix/refactor/docs/chore |
| Hotfix | Branch off `main`, PR to `main`, sync DOWN |
| Tags | `git tag -a v1.0.0 -m "Release v1.0.0"` |

## Promoting UP (e.g. dev -> qa)

```bash
git fetch origin
git checkout -b chore/promote-dev-to-qa origin/qa
git merge origin/dev
black backend/ auth/ stocks/ scripts/ dashboard/
isort backend/ auth/ stocks/ scripts/ dashboard/ --profile black
git push -u origin chore/promote-dev-to-qa && gh pr create --base qa
```

## Syncing DOWN

```bash
git fetch origin && git checkout dev && git pull origin dev
git merge origin/qa && git push origin dev
```

## Branch Protection

| Branch | Rules |
|--------|-------|
| `main` | No direct push, 2 approvals, CI pass |
| `release` | PR from `qa` only, 1 approval + QA lead |
| `qa` | PR from `dev` only, 1 approval |
| `dev` | PR from `feature/*`, 1 approval, tests + lint |

## Hard Rules

- NEVER push directly to protected branches.
- ALWAYS resolve conflicts locally before pushing.
- ALWAYS re-run lint after every merge.
- Long-lived feature branches (> 1 day) MUST merge dev daily.

## PR Review

### Focus Hierarchy

1. **Security** — auth bypass, injection, secret exposure.
2. **Correctness** — logic errors, edge cases, data loss.
3. **Breaking changes** — API contracts, schema, config.
4. **Performance** — N+1 queries, memory leaks, blocking calls.
5. **Maintainability** — readability, naming, dead code.

### What to Review

- ONLY files changed in the PR.
- Focus on the "why" — does the change achieve its goal?
- Verify test coverage for new logic paths.
- Check error messages don't leak sensitive info.

### PR Checklist (for authors)

- [ ] All lint checks pass (black, isort, flake8, ESLint).
- [ ] All tests pass (`python -m pytest tests/ -v`).
- [ ] New code has tests (happy path + 1 error path).
- [ ] No hardcoded secrets, no `print()` statements.
- [ ] PR title follows `[TYPE] Description` format.
- [ ] PROGRESS.md updated with dated entry.

### Feedback Severity

- **CRITICAL**: MUST fix before merge.
- **WARNING**: SHOULD fix.
- **SUGGESTION**: COULD improve.

### Tone

- Be direct, not harsh. State what needs to change and why.
- Correctness over style. Only flag style issues linters miss.
- Explain the "why". One fix per comment.
- Use RFC 2119 keywords: MUST, SHOULD, MAY.

## Documentation Triggers

| Trigger | Update |
|---------|--------|
| Every session | `PROGRESS.md` — dated entry |
| New/changed API endpoint | `docs/` — relevant API page |
| Architecture change | Serena shared memory |
| New config/env var | `README.md` — env vars table |
| New Iceberg table | `stocks/create_tables.py` + `docs/` |
