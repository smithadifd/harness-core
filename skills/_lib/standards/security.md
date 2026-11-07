# Security standard

## Secrets rules

1. **Never in committed files** — including the coding assistant's committed settings file, `.mcp.json`, config YAML, compose files. Env-var references only.
2. **Never in the assistant's local settings override file either.** It is gitignored but plaintext, may be synced between machines, and is pasted into permission dialogs. Allowlist *commands*, not credentialed commands: put tokens in the environment or in a secrets helper the command reads at runtime.
3. Secret *values* live in a secrets manager: headless scheduled jobs authenticate with a service-account token and resolve their secrets at wrapper start. Interim state, until a job is migrated: a per-job env file with mode 600, kept in one secrets directory outside every repository and excluded from every backup or sync that leaves the machine.
4. Secret *locations* (never values) may be documented in an untracked local context file.
5. A new secret is named for its owner (`<job-or-project>.env` style) and registered in that job's row of the automations inventory.

## Live-automation retrofit rules (mandatory)

Live scheduled jobs (see the automations inventory's risk column) are production. When retrofitting a repo that owns one:

1. **Time it**: start right after a scheduled run completes; finish before the next window.
2. **Never rename or move a path referenced by a plist or wrapper** in the same change that edits content. Paths move only in a dedicated migration step, one job at a time, with the plist updated atomically.
3. **Smoke-test with the job's force override** (`*_FORCE=1` pattern) after every change; watch the job's notification arrive.
4. **Context files first, code later**: AGENTS.md/README/LICENSE changes are zero-risk and ship alone; code restructuring is a separate, tested change.
5. Roll back = `git revert` + forced re-run; keep the pre-change commit hash in the PR description.

## Backup posture

A file-sync tool between machines is *sync*, not versioning: it propagates deletions and secrets alike. Durable config belongs in git; secrets belong in the secrets manager; neither should rely on sync.
