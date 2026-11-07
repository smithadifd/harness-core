# Prompting standard: briefs and reviewers

How to write the prompts a delegation loop runs on: sub-agent briefs (builders and reviewers) and review asks. Distilled from the published best-practice guidance of the major agent vendors for sub-agents and long-running work, plus the loop's own autopsies. The skills cite this file rather than restating it. Session kickoff blocks are loop-specific, so the instance keeps their opening preamble.

## The brief checklist (every sub-agent brief)

1. **One goal, stated as an outcome**: what is true when done, not the activity.
2. **Named files and interfaces, plus a pattern to imitate**: point at an existing example in the repository; agents match surrounding idiom far better than abstract style rules.
3. **Explicit out-of-scope**: the two or three adjacent things it must NOT touch.
4. **Machine-checkable success criteria**: a test, a grep, an HTTP probe; never "should work".
5. **The exact verify command**: the brief names the command whose exit code settles it.
6. **Evidence required in the report**: test output, a command and its result, or a screenshot. Assertions count for nothing (`trust-ladder.md` § 1: self-reported success counts for nothing).
7. **Tool and permission scope**: a reviewer with no write tools cannot "fix" its way to a pass.
8. **Escalate-versus-decide rule**: which decisions the agent logs and proceeds on, and which park the item.
9. **Where to write state on finish**: a progress file, ledger row or PR body, so a killed run resumes.
10. **Fresh-context reviewer, never the builder**: generator and evaluator separation is the single most effective guard against self-praise ("looks done" is the only signal a builder has).

A brief that fails 1, 4, or 5 is not delegable. It is an unresolved contract wearing a task costume; send it back through elicitation.

## Reviewer scoping

Tell reviewers what NOT to report: a reviewer prompted to find gaps will find some in sound work. Scope the review to correctness, requirement gaps, and the item's declared risk class; style-only findings are out of scope unless asked. Reviewers verify the merge-class label as part of review (`trust-ladder.md` § 4).

## Anti-patterns (observed, not theoretical)

- **Option menus without a recommendation**: state the recommendation and why; alternatives only where genuinely torn.
- **Mid-stream questions**: they drown. Batch them at bookends; blocking items ping asynchronously.
- **Briefs that inherit context implicitly**: sub-agents do not see the session's system prompt or conversation; the brief is the whole world.
- **"Per your standing rule" citations**: cite the actual document or decision record, or drop the claim; rules cited from memory get re-litigated forever.
- **SSH clone URLs in kickoffs and briefs**: they fail for agents without SSH keys configured. Use an explicit HTTPS clone URL, never the SSH form. A hosting CLI's clone command is not itself a fix: it follows the CLI's configured protocol and clones over SSH if that is set, so trust it only after setting the protocol to HTTPS.
- **Assuming a post-merge pull unlocks a skill mid-session**: the skill registry is enumerated at session start; merging and pulling a new skill mid-session does not make it invokable until the next session.
