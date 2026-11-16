# Known limits

Limits that are known and judged out of scope for the current stage, one per
line: where they are in the code, what they allow, why they are left and the
pull request that raised them.

- scripts/probe-default-tier-load.sh:955 — the top-level .claude folder and .claude.json files of the target home are exempt from load C's no-write check, so a hook could write there unseen — out of scope for alpha: documented in the README and the script header, and only the pinned ledger commit runs under it (smithadifd/harness-core#3 r1)
