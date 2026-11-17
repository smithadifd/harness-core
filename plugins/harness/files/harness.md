# Harness rules

Working agreements for every Claude Code session on this machine and every agent it starts. A repository's own `AGENTS.md` or `CLAUDE.md` adds to them, and wins where it is stricter.

**The override file.** `harness-local.md`, in this same rules folder, adds what belongs to one person or one machine: grants, accounts, model choices, a second review family, exceptions to these rules, and stricter local rules. Where it is silent, these rules stand. A second-model-family review applies only when the override names one. With no override, or one that names no second family, the fresh-context review is the whole gate, no code goes to an outside model, and a person merges.

## Review

- Every change bound for a merge gets a fresh-context review, by a reviewer that did not write it, before the merge. Review in two passes, in order: does the change do what was asked, then does it meet the standards. Report correctness and requirement gaps.
- Three review rounds at most. Round 2 and later review the delta since the last round. At the cap the change goes back to planning unless only nits remain, and nothing merges with unreviewed commits after the last round.
- An open bug finding from any reviewer blocks the merge. Clearing one takes a waiver in the pull request that says why the path cannot be reached, written or explicitly approved by the human who merges (or whoever harness-local.md names as the merge step), never by the change's author or its builder.

## Evidence

- Evidence must be falsifiable: a command a non-author can rerun, in the environment that gates the change.
- A test counts once it has been seen red under a targeted mutation (production made wrong at the line the test guards) and green after the restore. Running the suite is not enough. Docs-only changes are exempt.
- Reproduce a CI-only failure locally before reasoning about it.
- A handed-down diagnosis is a claim: re-read the file and line before it drives a decision.

## Merge

- A human merges. What counts as the human step is the override's to say; with none, the session stops with the pull request ready and its review attached, and a person merges.
- CI is green on the final head, on a run created after the pull request was marked ready. Read each job's conclusion (`gh run view --json jobs`): a skipped job shows green. Every gating job's conclusion must be success; a skipped, cancelled or neutral gating job does not count as green.
- Pin the merge to the reviewed head (`gh pr merge --match-head-commit <sha>`), so a commit pushed after the review cannot land with it.
- Ask before anything with live reach, and wait for the answer: deploys, live hooks and wrappers, scheduled jobs, secrets, settings files, and any edit that changes who may approve or merge. This holds even where merging is otherwise granted.
- Never bypass a safety check (`--no-verify`, skipped hooks). Ask before a destructive operation: a force-push, a history rewrite, a hard reset, a dropped table.

## Delegated work

- Subagents and background jobs run sandboxed by default. A sandbox block is a signal to the session that delegated the work: it widens the sandbox for that one task, with a one-line reason it records, or takes the step itself. It is never a reason to drop the task.

## Command shapes

- A command that prompts for approval stalls a session left to run alone. Write a multi-step command or probe to a script at a literal absolute path, run it once with its output sent to a file, and read the file.
- Run `git push`, `gh pr merge`, `rm` and `git worktree` one per call, with literal paths.
- Never `rm -rf` a path built from a variable. Never `export HOME`; set a sandbox `HOME` inline on the one command that needs it.

## Clean up

- Remove what you made, in the same session: the worktrees, clones and copies made for review or mutation testing. Remove a worktree with `git worktree remove`, never `--force`, once its work is merged or its commits are on a remote branch.
- Keep something only for a stated reason (uncommitted changes, commits nowhere else, an open pull request still building there, another session still using it), and give that reason in the closing report.

## Where people read

- In pull requests, commit messages, code comments and docs, write decisions and plain reasons. Leave out session ids, paths into a home folder, and private personal context.
- A grant, exception or waiver line states what was decided, when, for what scope, and who decided it.
- Follow-up work goes into the project's own tracker in the same session, never into a note.
- Record words as a person's own only if that person typed them in this session.

## Tool gotchas

- Read the `owner/repo` slug from `git remote get-url origin`; never type it from memory.
- `gh pr view --json files` stops at 100 files; page `gh api --paginate repos/<owner>/<repo>/pulls/<n>/files` for the rest.
- Under squash merges, decide "merged" from the pull request's state (`gh pr view --json state`), never from `git merge-base --is-ancestor`.
- Retarget stacked pull requests before deleting their base branch; deleting it closes them.
- A test that reads machine-local files needs a guard for when they are absent, and an environment override the reviewer can set.
