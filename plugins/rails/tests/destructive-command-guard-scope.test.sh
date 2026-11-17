#!/bin/bash
# Interface-level scope checks. Command strings are inert JSON data only.
set -u
LC_ALL=C

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUARD="$HERE/../hooks/destructive-command-guard.sh"
JQ="$(command -v jq)" || { printf 'FAIL: jq is not on PATH\n'; exit 1; }
TEST_ROOT="$(mktemp -d "$(cd /tmp && pwd -P)/destructive-guard-scope.XXXXXX")" || exit 1
WORKTREE="$TEST_ROOT/worktree"

mkdir -p "$WORKTREE/build"
git init -q "$WORKTREE"

payload() {
  "$JQ" -cn --arg cwd "$WORKTREE" --arg command "$1" \
    '{hook_event_name:"PreToolUse",tool_name:"Bash",tool_input:{command:$command},cwd:$cwd}'
}

# The guard never allows: a command it judged and accepted is silent (exit 0,
# nothing on stdout), and the person's own permission flow decides.
output="$(payload 'rm -rf ./build' | /bin/bash --noprofile --norc -p "$GUARD")"
accepted_exit=$?
if [ "$accepted_exit" -ne 0 ] || [ -n "$output" ]; then
  printf 'FAIL: explicit literal rm expected silence (exit=%s, output=%s)\n' "$accepted_exit" "$output" >&2
  exit 1
fi

printf 'PASS: explicit literal rm -> silence, never allow\n'

set +e
ordinary_output="$(payload 'npm test' | /bin/bash --noprofile --norc -p "$GUARD")"
ordinary_exit=$?
set -e
if [ "$ordinary_exit" -ne 0 ] || [ -n "$ordinary_output" ]; then
  printf 'FAIL: ordinary command expected exit 0 with no decision (exit=%s, output=%s)\n' \
    "$ordinary_exit" "$ordinary_output" >&2
  exit 1
fi

printf 'PASS: ordinary command -> no opinion\n'
