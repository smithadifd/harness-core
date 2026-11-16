# Known limits

Limits that are known and judged out of scope for the current stage, one per
line: where they are in the code, what they allow, why they are left and the
pull request that raised them.

- scripts/probe-default-tier-load.sh:955 — the top-level .claude folder and .claude.json files of the target home are exempt from load C's no-write check, so a hook could write there unseen — out of scope for alpha: documented in the README and the script header, and only the pinned ledger commit runs under it (harness-core#3 r1)
- scripts/probe-default-tier-load.sh:616 — the subagent reader trusts the order of events in Claude Code's own stream, so a result placed before its tool_use, or after the final result event, is not refused — out of scope for alpha: the probe trusts the stream the session wrote (harness-core#3 r3)
- scripts/probe-default-tier-load.sh:616 — a forged event that is fully consistent (the right tool-use id and the right parent) reads as a real one, so a hand-built stream could still give B2 PASS — out of scope for alpha: the probe trusts the stream the session wrote and does not sign it (harness-core#3 r3)
- scripts/probe-default-tier-load.sh:627 — a subagent that starts its own subagent is not followed: that depth-2 subagent's Bash carries its own Agent call's id, is not credited to the first Agent call, and so reads as no nested Bash call (UNVERIFIED) — out of scope for alpha: the probe asks for one general-purpose subagent with one Bash call (harness-core#3 r3)
- scripts/probe-default-tier-load.sh:651 — B2 accepts only the exact command its prompt hands the subagent, so an honest subagent that rewrites it (a wrapper, a prefix, a suffix) fails B2 closed: UNVERIFIED or FAIL, never a false PASS — out of scope for alpha: the prompt asks for the command verbatim, and a live sample ran exactly that command (harness-core#3 r4)
- .github/workflows/ci.yml:199 — the tag check (check-marketplace.py --source) attests only for an unmodified workflow and script: it runs on the pull_request event from the pull request's own checkout, so a pull request that edits ci.yml or the script can delete the step, drop --expect-owner, or set a proxy and CA store that answers for api.github.com, and ambient same-user state (the process environment, the CA store, PATH, the interpreter) is not checked — out of scope for alpha: workflow and script edits are part of the reviewed diff, and running the attestation from main's trusted code is a separate follow-up (replaces harness-core#4)
- scripts/check-marketplace.py:525 — a self-hosted runner behind a proxy cannot pass the --source step: in CI the tripwire refuses any proxy or CA override (https_proxy, all_proxy, ssl_cert_file, ssl_cert_dir, in any letter case) before any request, and the refusal names the variable — out of scope for alpha: the default runner is GitHub-hosted and sets none of them (replaces harness-core#4)

## Harness setup (plugins/harness/setup.py)

What `setup.py apply` and `setup.py remove` promise, and what they leave to
you. Paths are under your home folder (`<home>`).

### The promise

This is the whole contract.

- **A run that completes** leaves exactly the documented result: apply adds the rules file, the override if it was absent, and the approved settings items, and records them; remove takes out exactly what a complete record lists (I3).
- **An apply that fails in-process** (any `Exception`, including OSError, a short write and a full disk) rolls back all of setup's own changes from memory, all-or-nothing, under I2. Rollback never needs to read or write the record, so a failing record write cannot stop it.
- **A remove that fails in-process** stops where it failed, refuses by name, and leaves the complete record in place. Running remove again finishes the job (I3). Remove does not roll back.
- **An apply that hard-stops** (Ctrl-C, a closed terminal, SIGTERM, SIGKILL, a crash, power loss) after it has changed anything outside its own folder (`.claude/plugins/harness-core/harness`), and before line 2 of the record is written, guarantees exactly three things, and nothing more:
  1. settings.json is whole: it is only ever changed by one `os.replace` of a fully written, fsynced temp file that already has settings.json's mode;
  2. if settings.json existed, a 0600 copy of it as it was before apply's first change exists at the path the record names;
  3. the next apply or remove refuses by name, naming the record and the backup path, and saying whether the backup is present.

  These three guarantees end once an in-process rollback has restored the home: a hard stop after that point leaves the home as it was before apply, and the next run may proceed, or refuse naming only the leftover backup.
- **An apply that hard-stops before it changed anything outside its own folder** has changed nothing the person uses; setup's own folder may hold a temp file, a record and a backup, and the next run either proceeds or refuses by name (`leftover settings backup` or the hard-stop refusal). **An apply that hard-stops after line 2 is written** has completed: the next apply refuses `already applied`, and a leftover backup is deleted by remove.
- **A remove that hard-stops** guarantees that settings.json is whole and the complete record is still in place until remove's last step, so running remove again finishes the job (I3). Remove has completed once the record is deleted; after that, only empty folders can remain, and a failure while pruning them is named as a warning and does not fail the run.
- **Setup never acts on an unfinished record.** It never deletes, restores or rewrites anything on the strength of one. Recovery after a hard stop is by hand, and the steps under "After a hard stop" below are advice, not a guarantee.

I2 and I3, as the promise uses them:

- **I2. Never knowingly overwrite or delete bytes setup did not write.** Before rollback or remove deletes a file setup created, it compares the file's sha256 with the one setup wrote; on a difference it keeps the file and names it. Before apply or remove replaces settings.json, it compares the current bytes with the bytes it based its change on, and refuses unwritten on a difference. Rollback restores the pre-apply settings bytes only if the current bytes equal the bytes apply wrote; otherwise it takes apply's items out one by one, as remove does, and names the file.
- **I3. Exact, re-runnable remove.** Remove acts only on a complete record. Settings items come out one by one: an item comes out only if it is still at its recorded place with its recorded neighbours. Items the person moved or duplicated are kept and named, and nothing else in settings.json is touched. A listed file that is already gone is skipped and named. Remove writes nothing to the record and deletes it last, and each of its steps is decided from the current files, so a remove stopped at any point before the record is deleted can be run again and finishes.

### After a hard stop

Advice, not a guarantee. The next apply or remove refuses, naming the record and the backup. To run setup again: if the refusal says the backup is present, restore `<home>/.claude/settings.json` from it, unless you have changed settings since; if it says absent, settings.json was not changed; if none, settings.json did not exist before apply, so delete it unless you have added to it since; then delete `<home>/.claude/rules/harness.md` only if it is byte-identical to the plugin's `files/harness.md`, and `<home>/.claude/rules/harness-local.md` only if you did not have one before and it is byte-identical to the plugin's `files/harness-local.md`. When unsure, keep the file. Delete any `.harness.md.harness-*`, `.harness-local.md.harness-*` and `.settings.json.harness-*` temp files beside those targets. Then delete the record, and the backup once you no longer need it. A stop during remove needs only another run of remove.

### Other limits

- **The hard-stop window.** Anything a person or another writer does between a hard stop and the hand steps is outside the promise. Setup never acts on an unfinished record, so it cannot make that window worse.
- **A stuck lock.** If setup refuses as locked while no setup is running, a `claude plugin configure` started by a killed setup, or a process it started, still holds it. The lock clears when that process exits.
- **Outside writers can lose an edit, silently.** Each compare-then-act (the settings compare then `os.replace` in apply, rollback and remove; the sha256 compare then unlink in rollback and remove) is a window of a few system calls in which another writer's edit can be lost undetected. Closing them would need every writer to take the same lock.
- **Leftovers after a hard stop:** temp files (at most one per target), the backup, and empty folders, all at 0600 or empty. `.claude` and the lock file always stay. A settings.json temp file has settings.json's own mode, set before any bytes are written to it.
- **Configure is not undone.** Its changes stay when apply fails after it, and the refusal says so.
- **CI runs on Linux only.** No harness-core CI job runs on macOS. Setup uses only portable calls (`flock`, `mkstemp`, `fchmod`, `fsync`, `link`, `os.replace`, `O_NOFOLLOW`, `SIG_DFL`, and `F_FULLFSYNC` only where defined), but it is not tested on macOS in CI.
- **Settings are re-serialized.** When apply adds an item, or remove takes one out, settings.json is written back as JSON with the indent it had (two spaces when none is found) and a final newline; key order and every value are kept, but other spacing is not.
