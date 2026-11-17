#!/bin/bash
# Static checks on the destructive-command guard's reason codes, hints and time
# budgets. Reads the guard and the plugin's hooks.json; runs nothing but text
# tools and jq.
set -u
LC_ALL=C

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENGINE="$HERE/../hooks/destructive-command-guard.sh"
HOOKS="$HERE/../hooks/hooks.json"
JQ="$(command -v jq)" || { printf 'FAIL: jq is not on PATH\n'; exit 1; }

FAILURES=0
pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; FAILURES=$((FAILURES + 1)); }

for f in "$ENGINE" "$HOOKS"; do
  [ -f "$f" ] || { printf 'FAIL: missing %s\n' "$f"; exit 1; }
done

# Code lines only: comments never count as calls.
CODE="$(grep -v '^[[:space:]]*#' "$ENGINE")"

printf '%s\n' '== C5 engine reason arguments are literals =='
DENY_ARGS="$(printf '%s\n' "$CODE" | grep -oE 'policy_deny [^ ;)|&]+' | sed 's/^policy_deny //')"
[ -n "$DENY_ARGS" ] || fail "C5 no policy_deny call found"
bad_args=""
while IFS= read -r arg; do
  [ -n "$arg" ] || continue
  case "$arg" in
    '"path_repointing_$REPOINT_CLASS"'|'"removal_beside_$CARRIER_CLASS"'|'"${LANE_REGISTRY_FAILURE:-unsafe_worktree_remove}"'|'"${CLEANUP_REGISTRY_FAILURE:-unsafe_worktree_remove}"'|'"$DEPENDENCY_CODE"') ;;
    *) [[ "$arg" =~ ^[a-z_]+$ ]] || bad_args="$bad_args $arg" ;;
  esac
done <<EOF
$DENY_ARGS
EOF
if [ -z "$bad_args" ]; then
  pass "C5 every policy_deny argument is a literal code or a variable whose every assignment is literal"
else
  fail "C5 policy_deny takes a non-literal argument:$bad_args"
fi
for var in REPOINT_CLASS CARRIER_CLASS LANE_REGISTRY_FAILURE CLEANUP_REGISTRY_FAILURE DEPENDENCY_CODE; do
  values="$(printf '%s\n' "$CODE" | grep -oE "(^|[^A-Za-z_])$var=[^ ;}]*" | sed "s/^.*$var=//")"
  bad=""
  while IFS= read -r v; do
    [[ "$v" =~ ^(\"\"|\"[a-z_]+\"|[a-z_]+)$ ]] || bad="$bad $v"
  done <<EOF
$values
EOF
  if [ -n "$values" ] && [ -z "$bad" ]; then
    pass "C5 every $var assignment is a literal"
  else
    fail "C5 $var has a non-literal assignment:${bad:- none found}"
  fi
done
EMIT_CALLS="$(printf '%s\n' "$CODE" | grep -E '(^|[^A-Za-z_])emit_policy ' | sed 's/^[[:space:]]*//')"
EXPECTED_EMIT="$(printf '%s\n' 'emit_policy deny "$1"' 'emit_policy no_opinion not_explicit_operation' 'emit_policy no_opinion accepted')"
if [ "$EMIT_CALLS" = "$EXPECTED_EMIT" ]; then
  pass "C5 emit_policy is called only from policy_deny, policy_no_opinion and policy_accepted"
else
  fail "C5 emit_policy has an unexpected call site: $EMIT_CALLS"
fi

printf '%s\n' '== C6 every engine and adapter code has a hint =='
CLASSES="$(printf '%s\n' "$CODE" | grep -oE 'REPOINT_CLASS="?[a-z_]+"?' | sed 's/^REPOINT_CLASS=//; s/"//g' | sort -u)"
CARRIERS="$(printf '%s\n' "$CODE" | grep -oE 'CARRIER_CLASS="?[a-z_]+"?' | sed 's/^CARRIER_CLASS=//; s/"//g' | sort -u)"
ENGINE_CODES="$({
  printf '%s\n' "$DENY_ARGS" | grep -E '^[a-z_]+$'
  printf '%s\n' "$CLASSES" | sed 's/^/path_repointing_/'
  printf '%s\n' "$CARRIERS" | sed 's/^/removal_beside_/'
  for var in LANE_REGISTRY_FAILURE CLEANUP_REGISTRY_FAILURE DEPENDENCY_CODE; do
    printf '%s\n' "$CODE" | grep -oE "(^|[^A-Za-z_])$var=[a-z_]+" | sed "s/^.*$var=//"
  done
} | sort -u)"
ADAPTER_CODES="$({
  printf '%s\n' "$CODE" | grep -oE 'emit_claude_deny [a-z_]+' | sed 's/^emit_claude_deny //'
  printf '%s\n' "$CODE" | grep -oE 'reason=(adapter_failed|adapter_interrupted)\)' | sed 's/^reason=//; s/)$//'
} | sort -u)"
# The adapter's hint table: the case labels inside deny_hint().
BASH_KEYS="$(awk '
  /^deny_hint\(\) \{/ { on = 1; next }
  on && /^}/ { on = 0 }
  on && /^    [a-z_|]+\) / { label = $1; sub(/\)$/, "", label); n = split(label, parts, "|"); for (i = 1; i <= n; i++) print parts[i] }
' "$ENGINE" | sort -u)"
[ -n "$BASH_KEYS" ] || fail "C6 no hint keys found in deny_hint"
missing=""
for code in $ENGINE_CODES $ADAPTER_CODES; do
  case "$code" in lane_registry_*|lane_ids_*) continue ;; esac
  printf '%s\n' "$BASH_KEYS" | grep -qx "$code" || missing="$missing $code"
done
if [ -n "$ENGINE_CODES" ] && [ -n "$ADAPTER_CODES" ] && [ -z "$missing" ]; then
  pass "C6 every engine and adapter code ($(printf '%s\n' $ENGINE_CODES $ADAPTER_CODES | sort -u | wc -l | tr -d ' ') codes) has a hint (the lane registry's own codes keep the default hint)"
else
  fail "C6 codes without a hint:$missing"
fi
# The carrier family: the seventeen members, each with its own hint, and no
# hint for a retired code.
WANT_CARRIERS="alias backtick env_split eval find_exec flock git_bisect_run git_foreach git_rebase_exec heredoc process_substitution shell source substitution trap variable_command xargs"
if [ "$(printf '%s\n' $CARRIERS | tr '\n' ' ' | sed 's/ $//')" = "$WANT_CARRIERS" ]; then
  pass "C6 CARRIER_CLASS takes exactly the seventeen carrier classes"
else
  fail "C6 CARRIER_CLASS classes differ: $(printf '%s\n' $CARRIERS | tr '\n' ' ')"
fi
for code in non_literal_rm_targets path_repointing_substitution path_repointing_eval; do
  if printf '%s\n' "$BASH_KEYS" | grep -qx "$code"; then
    fail "C6 the retired code $code still has a hint label"
  else
    pass "C6 the retired code $code has no hint label"
  fi
done
for code in $(printf '%s\n' $CARRIERS | sed 's/^/removal_beside_/') path_repointing_popd hidden_separator lane_registry_absent_armed cleanup_registry_absent_armed cleanup_registry_untrusted cleanup_registry_malformed cleanup_seat_missing inherited_git_environment; do
  if printf '%s\n' "$BASH_KEYS" | grep -qx "$code"; then
    pass "C6 the new code $code has its own hint"
  else
    fail "C6 the new code $code has no hint"
  fi
done

printf '%s\n' '== D budgets =='
SECONDS_LINES="$(grep -E '^TIMEOUT_SECONDS=' "$ENGINE")"
TIMEOUT_VALUE="${SECONDS_LINES#TIMEOUT_SECONDS=}"
if [[ "$SECONDS_LINES" =~ ^TIMEOUT_SECONDS=[0-9]+$ ]] && [ "$TIMEOUT_VALUE" -eq 8 ]; then
  pass "D1 engine budget is 8 seconds"
else
  fail "D1 engine budget is not one TIMEOUT_SECONDS=8 line (got: $SECONDS_LINES)"
  TIMEOUT_VALUE=8
fi
# The one command hook of hooks.json that runs the guard.
HOOK_TIMEOUT="$("$JQ" -r '[.hooks.PreToolUse[].hooks[] | select(.type == "command" and ((.args // []) | map(tostring) | any(endswith("/hooks/destructive-command-guard.sh"))))] | if length == 1 then (.[0].timeout // "none" | tostring) else "count=\(length)" end' "$HOOKS" 2>/dev/null)"
if [[ "$HOOK_TIMEOUT" =~ ^[0-9]+$ ]] && [ "$HOOK_TIMEOUT" -ge $((TIMEOUT_VALUE + 2)) ]; then
  pass "D2 hook timeout ${HOOK_TIMEOUT}s is at least the budget plus 2 s"
else
  fail "D2 hook timeout '${HOOK_TIMEOUT}' is not at least TIMEOUT_SECONDS + 2 ($((TIMEOUT_VALUE + 2)))"
fi

if [ "$FAILURES" -eq 0 ]; then
  printf 'ALL DESTRUCTIVE GUARD REASON TESTS PASSED\n'
  exit 0
fi
printf '%s TEST(S) FAILED\n' "$FAILURES"
exit 1
