# `.secrets/` — materialised secret files

This directory holds short-lived files materialised from
the macOS Keychain via `scripts/secrets/materialize.sh`.
Each file is the host-side source for a docker-compose
`secrets:` mount that lands at `/run/secrets/<name>` inside
the container.

The directory itself is checked into git (so docker-compose
references are stable), but every file inside it (except this
README and `.gitignore`) is gitignored.

**Never** commit the materialised secret files. **Never** read
them in application code — use
`backend/secret_loader.load_secret("<slug>")` which reads
`/run/secrets/<slug>` directly.
