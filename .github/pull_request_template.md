## PR Type

<!-- Mark the type that applies -->
- [ ] `feat` — new feature
- [ ] `fix` — bug fix
- [ ] `refactor` — code restructure (no behaviour change)
- [ ] `docs` — documentation only
- [ ] `chore` — build / config / dependency update
- [ ] `hotfix` — urgent production fix

## Summary

<!-- 1–3 sentences: what does this PR do and why? -->

## Related Issue / Ticket

<!-- Link to the issue or ticket: Closes #123 / Fixes #456 -->

## Checklist

### Code quality
- [ ] Follows the branching strategy: branched off `dev` (or `main` for hotfix)
- [ ] PR title follows `[TYPE] Short description` format
- [ ] No bare `print()` calls in backend Python — uses `logging.getLogger(__name__)` instead
- [ ] Google-style Sphinx docstrings on all new backend Python files (module + class + method)
- [ ] `Optional[X]` used (not `X | Y`) — Python 3.9 compat
- [ ] No secrets or API keys staged (`.env`, credentials, tokens)
- [ ] No debug leftovers (`breakpoint()`, `# TODO`, temp `print()`)

### Testing
- [ ] Tested locally — backend starts and responds correctly
- [ ] Pre-commit hook passes (`bash hooks/pre-commit`)
- [ ] Pre-push hook passes (`bash hooks/pre-push`)
- [ ] `mkdocs build` passes

### Documentation
- [ ] Relevant `docs/` page(s) updated (new endpoints → `api.md`, decisions → `decisions.md`)
- [ ] `CLAUDE.md` updated if project structure, architecture, or How to Run changed
- [ ] `PROGRESS.md` updated with a dated session entry

### For PRs into `qa` or higher
- [ ] `requirements.txt` updated if new packages installed (`pip freeze > backend/requirements.txt`)
- [ ] Migration script run if Iceberg schema changed (`python auth/migrate_users_table.py`)
- [ ] CI checks pass on the source branch before raising this PR
