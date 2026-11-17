#!/bin/bash
# Focused regression for fail-closed protocol output without writable temp storage.
set -u
LC_ALL=C

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUARD="$HERE/../hooks/destructive-command-guard.sh"
JQ="$(command -v jq)" || { printf 'FAIL: jq is not on PATH\n'; exit 1; }
TEST_ROOT="$(mktemp -d "$(cd /tmp && pwd -P)/destructive-guard-unavailable-temp.XXXXXX")" || exit 1
WORKTREE="$TEST_ROOT/worktree"
UNAVAILABLE_TMP="$TEST_ROOT/not-a-directory"
POLICY_STDOUT="$TEST_ROOT/policy.stdout"
POLICY_STDERR="$TEST_ROOT/policy.stderr"
ADAPTER_STDOUT="$TEST_ROOT/adapter.stdout"
ADAPTER_STDERR="$TEST_ROOT/adapter.stderr"
INERT_COMMAND="rm -rf ."
AUDIT_PATTERN='^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z event=destructive-command-deny reason=unsafe_rm$'

mkdir -p "$WORKTREE"
git init -q "$WORKTREE"
printf '%s\n' 'temporary-path sentinel' >"$UNAVAILABLE_TMP"

POLICY_INPUT="$($JQ -cn --arg cwd "$WORKTREE" --arg command "$INERT_COMMAND" \
  '{cwd:$cwd,command:$command}')"
ADAPTER_INPUT="$($JQ -cn --arg cwd "$WORKTREE" --arg command "$INERT_COMMAND" \
  '{hook_event_name:"PreToolUse",tool_name:"Bash",tool_input:{command:$command},cwd:$cwd}')"

printf '%s' "$POLICY_INPUT" \
  | HOME=$(mktemp -d "$(cd /tmp && pwd -P)/destructive-guard-policy-home.XXXXXX") TMPDIR="$UNAVAILABLE_TMP" \
    /bin/bash --noprofile --norc -p "$GUARD" --policy >"$POLICY_STDOUT" 2>"$POLICY_STDERR"
printf '%s' "$ADAPTER_INPUT" \
  | HOME=$(mktemp -d "$(cd /tmp && pwd -P)/destructive-guard-adapter-home.XXXXXX") TMPDIR="$UNAVAILABLE_TMP" \
    /bin/bash --noprofile --norc -p "$GUARD" >"$ADAPTER_STDOUT" 2>"$ADAPTER_STDERR"

assert_denial() {
  local mode="$1" stdout_path="$2" stderr_path="$3" jq_filter="$4"
  "$JQ" -e -s "length == 1 and (.[0] | $jq_filter)" "$stdout_path" >/dev/null \
    || { printf 'FAIL: %s stdout is not one strict denial JSON record\n' "$mode"; return 1; }
  [ "$(wc -l <"$stderr_path" | tr -d ' ')" -eq 1 ] \
    && grep -Eq "$AUDIT_PATTERN" "$stderr_path" \
    || { printf 'FAIL: %s stderr is not one fixed audit record\n' "$mode"; return 1; }
  ! grep -qF "$GUARD" "$stderr_path" \
    && ! grep -qF "$WORKTREE" "$stderr_path" \
    && ! grep -qF "$INERT_COMMAND" "$stderr_path" \
    || { printf 'FAIL: %s stderr disclosed input or paths\n' "$mode"; return 1; }
  printf 'PASS: %s denies with strict JSON and one redacted stderr record\n' "$mode"
}

assert_denial policy "$POLICY_STDOUT" "$POLICY_STDERR" \
  'type == "object" and keys == ["decision","reason"] and .decision == "deny" and (.reason | type == "string")' \
  || exit 1
assert_denial adapter "$ADAPTER_STDOUT" "$ADAPTER_STDERR" \
  'type == "object" and keys == ["hookSpecificOutput"] and .hookSpecificOutput.permissionDecision == "deny"' \
  || exit 1

if [ "$(sed -n '1p' "$UNAVAILABLE_TMP")" != 'temporary-path sentinel' ] || [ "$(wc -l <"$UNAVAILABLE_TMP" | tr -d ' ')" -ne 1 ]; then
  printf 'FAIL: unavailable temporary path was modified\n'
  exit 1
fi

printf 'ALL UNAVAILABLE-TEMP REGRESSIONS PASSED\n'
printf 'Fixtures retained at %s (test performs no deletion)\n' "$TEST_ROOT"
