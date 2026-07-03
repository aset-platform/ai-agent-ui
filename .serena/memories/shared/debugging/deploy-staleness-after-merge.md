# StatReload doesn't reliably fire for a git-merge-introduced module

## Symptom

A scheduled job (or any code path) that imports a module added by a
recently-merged PR fails with `ModuleNotFoundError`, even though the
file is present on disk and `git log` confirms the merge landed
before the job ran.

## Root cause

`uvicorn --reload`'s `StatReload` watches the bind-mounted working
directory and reloads the process when it detects a file change.
This is reliable for direct `Edit`/`Write`-style changes to existing
files, but is NOT guaranteed to fire for a `git merge`/`git pull`
that lands a burst of new/changed files at once — the mechanism can
miss some or all of a multi-file merge under conditions not yet
fully characterized (this is a distinct case from the one covered in
`shared/debugging/uvicorn-reload-routes-models-gotcha`, where reload
DOES fire but doesn't replay app-startup-time registration; here,
reload simply never fires at all for the merge).

A long-running Python process has already imported everything it's
going to import at its own startup. A new module appearing on disk
mid-run does not make it importable by that process without a
reload — StatReload is supposed to provide that reload automatically,
but this incident shows it cannot be relied on for merge-introduced
changes specifically.

**Confirmed instance:** PR #292 merged a new module
(`backend/algo/attribution/fifo_matcher.py`) into `dev` at
10:36:47 UTC. The scheduled job `algo_closed_trades_rollup`,
running on a process that had started at 09:54:56 UTC (before the
merge), hit `ModuleNotFoundError` at 11:00:06 UTC. Cross-referencing
the job's run history (`scheduler_runs` table) against
`docker compose logs backend -t | grep StatReload` showed no reload
log line anywhere in that 24-minute window — only the next explicit
restart (11:45:52 UTC) picked up the new module, after which the job
succeeded on retry.

## Fix

After merging a PR that adds a file some other code (a scheduled
job, a route, a background task) imports, restart the backend
explicitly. Do not assume the running process will notice on its
own.

## General rule

Don't reason about or trust StatReload's behavior for merge-driven
changes — verify explicitly:

```bash
docker compose logs backend -t --since "<merge-time>" | grep -i statreload
```

If you need certainty the new code is live (not just "probably
fine"), restart and confirm via a direct in-process check (e.g.
importing the module in a `docker compose exec` shell, or hitting an
endpoint that exercises the new code path) rather than relying on
"the merge happened, so the code should be there."

## Related

- `shared/debugging/uvicorn-reload-routes-models-gotcha` — the
  complementary case where StatReload DOES fire (log line appears)
  but the change still isn't live, because app-startup-time work
  (route registration, `response_model` binding) isn't replayed.
- `shared/conventions/backend-restart-triggers` — the general
  restart-trigger decision matrix; this finding corrects that
  table's "New module added → usually nothing" row.
