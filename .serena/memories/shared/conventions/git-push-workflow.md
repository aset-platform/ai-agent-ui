# Git Push Workflow

## Rule: ALWAYS merge dev before pushing

```bash
git fetch origin
git merge origin/dev          # resolve conflicts LOCALLY
black + isort + flake8        # re-run lint after merge
python -m pytest tests/ -v    # re-run full tests after merge
# ONLY THEN:
git push -u origin feature/branch-name
```

## What Goes Wrong
Pushing without merging dev first causes file conflicts visible on GitHub, requiring a second push to resolve. The branch diverges from dev (dev has files from previous PR merges).

## Prevention
- ALWAYS `git fetch origin && git merge origin/dev` before `git push`
- NEVER push then create PR — merge dev first, verify clean, then push+PR
- Treat push as a quality gate: fetch → merge → lint → test → push
