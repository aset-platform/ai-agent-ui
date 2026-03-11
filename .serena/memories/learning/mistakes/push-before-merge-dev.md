# Mistake: Pushed Feature Branch Without Merging Dev First

## Date: 2026-03-06

## What Happened
Pushed `feature/sortable-column-headers` to origin and created PR #64 without
first merging `origin/dev` into the feature branch. This caused 5 file conflicts
visible on GitHub, requiring a second push to resolve.

## Root Cause
Skipped the pre-push merge step. The branch had diverged from dev (dev had the
same files from a previous PR merge) and I didn't check for divergence.

## Correct Workflow (MUST FOLLOW EVERY TIME)
```bash
# Before EVERY push of a feature branch:
git fetch origin
git merge origin/dev          # resolve conflicts LOCALLY
black + isort + flake8        # re-run lint after merge
python -m pytest tests/ -v    # re-run full tests after merge
# ONLY THEN:
git push -u origin feature/branch-name
```

## CLAUDE.md Rules (already documented)
- Line 211: "ALWAYS resolve conflicts locally before pushing."
- Line 212: "ALWAYS re-run lint after every merge."
- Line 139: "NEVER push with lint errors."

## Prevention
- ALWAYS `git fetch origin && git merge origin/dev` before `git push`
- NEVER push then create PR — merge dev first, verify clean, then push+PR
- Treat push as a quality gate: fetch → merge → lint → test → push
