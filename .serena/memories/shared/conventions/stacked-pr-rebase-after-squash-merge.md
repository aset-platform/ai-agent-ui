# Stacked PRs after a squash-merge

When stacking PR-Y on top of PR-X and X squash-merges to dev,
Y's PR auto-closes (its base branch is deleted by
`--delete-branch`). Reopening with a different base is blocked
("Cannot change the base branch of a closed pull request"). The
recovery dance:

## 1. Confirm Y is auto-closed against a deleted base

```bash
gh pr view <Y> --json state,baseRefName -q '.'
# → state=CLOSED, baseRefName=feature/<X-branch>
```

## 2. Rebase Y's branch onto dev — but use `--onto`

Y's branch carries X's individual commits in its history. Those
commits don't exist on `dev` (they got squash-collapsed into one
commit). A naive `git rebase dev` will try to replay every X
commit and conflict.

Use `--onto` to take only Y's commits (after X's tip) and
replay them onto dev:

```bash
git checkout feature/<Y-branch>
git rebase --onto origin/dev <X-branch-tip-SHA> HEAD
```

`<X-branch-tip-SHA>` = the last commit of Y's branch that ALSO
existed on X's branch before X merged. You can find it with
`git log --oneline origin/dev..HEAD` — the LAST commit in the
output (oldest) is from X; the X-tip is one before that on Y's
branch. Or read it from the original PR-Y's description.

## 3. Re-point the branch label

`--onto` leaves you on detached HEAD:

```bash
git branch -f feature/<Y-branch> HEAD
git checkout feature/<Y-branch>
```

## 4. Force-push and open a fresh PR

```bash
git push --force-with-lease origin feature/<Y-branch>
gh pr create --base dev --head feature/<Y-branch> \
  --title "..." --body "..."
```

## 5. Cross-link the auto-closed PR

```bash
gh pr comment <old-Y> \
  --body "Auto-closed when <X-branch> deleted on merge. Continued at #<new-Y> (rebased onto new dev tip)."
```

## Why "Use stacked-PR base intentionally"

If you stack Y on X and want them reviewed separately, ALWAYS
cut Y's branch BEFORE Y's first commit, NOT after. Otherwise
Y's commits land on X's branch and X's PR diff grows. This
pattern, plus the rebase-after-squash dance above, is the
clean stacking path.

## Real example

2026-05-24 A→B→C arc. Epic C was stacked on Epic B's
`feature/algo-portfolio-tab`. When Epic B merged as PR #243,
Epic C's PR #244 auto-closed. Recovered via `git rebase --onto
origin/dev 7e3af8c HEAD` (7e3af8c was Epic B's tip pre-merge),
opened fresh PR #245. See `mem:project_abc_arc_shipped` in
auto-memory.

## Related

- `shared/conventions/git-push-workflow` — base hygiene.
- `shared/conventions/git-workflow` — branch-off-dev rule.
