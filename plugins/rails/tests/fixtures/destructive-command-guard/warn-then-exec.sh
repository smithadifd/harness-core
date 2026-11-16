#!/bin/bash
# Test stand-in for the destructive-command guard's engine. It writes one line
# to stderr, by default the warning bash itself prints when fork() hits EAGAIN
# and the retry succeeds, and then runs the real engine named by
# DCG_WARN_ENGINE with the same arguments. Verdicts are therefore real while
# stderr is noisy. DCG_WARN_TEXT replaces the warning line.
printf '%s\n' "${DCG_WARN_TEXT:-bash: fork: retry: Resource temporarily unavailable}" >&2
exec /bin/bash --noprofile --norc -p "${DCG_WARN_ENGINE:?DCG_WARN_ENGINE names the engine to run}" "$@"
