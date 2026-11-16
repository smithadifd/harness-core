# Known limits

Limits that are known and judged out of scope for the current stage, one per
line: where they are in the code, what they allow, why they are left and the
pull request that raised them.

- scripts/probe-default-tier-load.sh:955 — the top-level .claude folder and .claude.json files of the target home are exempt from load C's no-write check, so a hook could write there unseen — out of scope for alpha: documented in the README and the script header, and only the pinned ledger commit runs under it (harness-core#3 r1)
- scripts/probe-default-tier-load.sh:616 — the subagent reader trusts the order of events in Claude Code's own stream, so a result placed before its tool_use, or after the final result event, is not refused — out of scope for alpha: the probe trusts the stream the session wrote (harness-core#3 r3)
- scripts/probe-default-tier-load.sh:616 — a forged event that is fully consistent (the right tool-use id and the right parent) reads as a real one, so a hand-built stream could still give B2 PASS — out of scope for alpha: the probe trusts the stream the session wrote and does not sign it (harness-core#3 r3)
- scripts/probe-default-tier-load.sh:627 — a subagent that starts its own subagent is not followed: that depth-2 subagent's Bash carries its own Agent call's id, is not credited to the first Agent call, and so reads as no nested Bash call (UNVERIFIED) — out of scope for alpha: the probe asks for one general-purpose subagent with one Bash call (harness-core#3 r3)
