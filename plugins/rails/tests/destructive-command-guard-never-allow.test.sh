#!/bin/bash
# The destructive-command guard never allows and never asks: it denies, or it
# stays silent (exit 0, nothing on stdout) and leaves the command to Claude
# Code's own permission flow.
#   N1 static: the guard file has no allow or ask word in code and no
#      permissionDecision other than deny; its hooks.json entry sets none.
#   N2 dynamic: every command the source suite accepted through the adapter
#      is silent here, and the engine still says it judged and accepted it.
#   N3: an engine that prints an allow verdict is denied as invalid output.
#   N4: with a trusted rm-roots file set (a copy of the guard whose account
#      home folder is a fixture), every N2 command is still silent and still
#      judged, an rm of a file below a listed root is silent too, and an
#      engine allow verdict still denies.
# Command strings are inert JSON data and are never executed.
set -u
LC_ALL=C

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUARD="$HERE/../hooks/destructive-command-guard.sh"
HOOKS="$HERE/../hooks/hooks.json"
JQ="$(command -v jq)" || { printf 'FAIL: jq is not on PATH\n'; exit 1; }
TEST_ROOT="$(mktemp -d "$(cd /tmp && pwd -P)/destructive-guard-never-allow.XXXXXX")" || exit 1
WORKTREE="$TEST_ROOT/worktree"
SCRATCHPAD="$TEST_ROOT/session-scratchpad"
DISPOSABLE="$TEST_ROOT/disposable-worktree"

FAILURES=0
pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; FAILURES=$((FAILURES + 1)); }

mkdir -p "$WORKTREE/build/cache" "$SCRATCHPAD/cache" "$TEST_ROOT/githome"
git_q() { HOME="$TEST_ROOT/githome" git -c user.name=t -c user.email=t@example.invalid "$@"; }
git_q init -q "$WORKTREE" || exit 1
printf 'x\n' >"$WORKTREE/f.txt"
git_q -C "$WORKTREE" add f.txt && git_q -C "$WORKTREE" commit -qm seed || exit 1
git_q -C "$WORKTREE" worktree add -q --detach "$DISPOSABLE" HEAD || exit 1

printf '%s\n' '== N1 static: no allow or ask in the guard or its hook entry =='
N1_HITS="$(awk '
  /^[[:space:]]*#/ { next }
  { line = $0; low = " " tolower(line) " "
    if (match(low, /[^a-z0-9_](allow|ask)[^a-z0-9_]/)) print FILENAME ":" NR ": " line }
' "$GUARD")"
if [ -z "$N1_HITS" ]; then
  pass "N1 no code line of the guard holds the word allow or ask"
else
  fail "N1 code lines hold allow or ask: $N1_HITS"
fi
N1_DECISIONS="$(grep -oE 'permissionDecision"?:"?[A-Za-z_]*' "$GUARD" | sort -u)"
N1_DECISION_COUNT="$(grep -cE 'permissionDecision"?:' "$GUARD")"
if [ "$N1_DECISIONS" = 'permissionDecision":"deny' ] && [ "$N1_DECISION_COUNT" -ge 1 ]; then
  pass "N1 every permissionDecision in the guard is deny ($N1_DECISION_COUNT lines)"
else
  fail "N1 a permissionDecision other than deny: $N1_DECISIONS"
fi
N1_HOOK="$("$JQ" -r '[.. | objects | select(has("args") and ((.args | map(tostring) | join(" ")) | contains("destructive-command-guard.sh")))] | if length == 1 then (.[0] | [paths(type == "string") as $p | getpath($p)] | map(select(test("permissionDecision"))) | length | tostring) else "entries=\(length)" end' "$HOOKS" 2>/dev/null)"
if [ "$N1_HOOK" = 0 ]; then
  pass "N1 the guard's hook entry in hooks.json sets no permissionDecision"
else
  fail "N1 the guard's hook entry in hooks.json: $N1_HOOK"
fi

printf '%s\n' '== N2 dynamic: what the source accepted is silent here =='
# The guard and working folder the N2 rows run against.
N2_GUARD="$GUARD"
N2_CWD="$WORKTREE"
payload() {
  "$JQ" -cn --arg cwd "$N2_CWD" --arg command "$1" \
    '{hook_event_name:"PreToolUse",tool_name:"Bash",tool_input:{command:$command},cwd:$cwd}'
}
n2() { # LABEL COMMAND
  local out rc policy
  out="$(payload "$2" | /bin/bash --noprofile --norc -p "$N2_GUARD" --scratchpad "$SCRATCHPAD" --authorized-worktree "$DISPOSABLE" 2>/dev/null)"
  rc=$?
  if [ "$rc" -eq 0 ] && [ -z "$out" ]; then
    pass "N2 $1: silent (exit 0, no output)"
  else
    fail "N2 $1: expected silence, got exit $rc and output ${out:0:160}"
  fi
  policy="$("$JQ" -cn --arg cwd "$N2_CWD" --arg command "$2" '{cwd:$cwd,command:$command}' \
    | /bin/bash --noprofile --norc -p "$N2_GUARD" --policy --scratchpad "$SCRATCHPAD" --authorized-worktree "$DISPOSABLE" 2>/dev/null)"
  if [ "$policy" = '{"decision":"no_opinion","reason":"accepted"}' ]; then
    pass "N2 $1: the engine judged and accepted it"
  else
    fail "N2 $1: the engine said $policy"
  fi
}
n2_rows() {
  local rm_spelling
  n2 "literal build descendant" 'rm -rf ./build'
  n2 "two literal relative descendants" 'rm -rf ./build ./build/cache'
  n2 "explicitly authorized disposable worktree" "git worktree remove $DISPOSABLE"
  n2 "literal nested build descendant" 'rm -f ./build/cache/file'
  n2 "named scratchpad descendant" "rm -rf $SCRATCHPAD/cache"
  for rm_spelling in /bin/rm /usr/bin/rm ./rm ../bin/rm 'command rm' 'exec rm' 'env rm' 'sudo rm' 'nohup rm' 'nice rm'; do
    n2 "$rm_spelling literal descendant" "$rm_spelling -rf ./build"
  done
  n2 "tab-separated literal rm" $'rm\t-rf\t./build'
  n2 "leading negation operator with literal descendant" '! rm -rf ./build'
  n2 "assignment prefix with literal descendant" 'FOO=1 rm -rf ./build'
  n2 "sudo option with literal descendant" 'sudo -n rm -rf ./build'
  n2 "env option with literal descendant" 'env -i rm -rf ./build'
  n2 "nested supported prefixes with literal descendant" 'sudo -n env NAME=value command -p rm -rf ./build'
  n2 "relative recursive rm" 'rm -r build'
}
n2_rows
# The source accepted a line continuation inside a literal rm; rule G now
# denies it, since a continuation beside a removal word can hide a separator.
out="$(payload $'rm -rf \\\n./build' | /bin/bash --noprofile --norc -p "$GUARD" --scratchpad "$SCRATCHPAD" --authorized-worktree "$DISPOSABLE" 2>/dev/null)"
case "$out" in
  *'"permissionDecision":"deny"'*'(reason=hidden_separator)'*) pass "N2 continued literal rm: deny/hidden_separator (rule G)" ;;
  *) fail "N2 continued literal rm: expected deny/hidden_separator, got ${out:0:160}" ;;
esac

printf '%s\n' '== N3: an allow from the engine is never passed through =='
STANDIN="$TEST_ROOT/allow-policy.sh"
printf '%s\n' '#!/bin/bash' "printf '%s\\n' '{\"decision\":\"allow\",\"reason\":\"accepted\"}'" >"$STANDIN"
out="$(payload 'printf safe' | /bin/bash --noprofile --norc -p "$GUARD" --adapter-policy "$STANDIN" 2>/dev/null)"
rc=$?
decision="$(printf '%s' "$out" | "$JQ" -er '.hookSpecificOutput.permissionDecision' 2>/dev/null || printf none)"
reason="$(printf '%s' "$out" | "$JQ" -er '.hookSpecificOutput.permissionDecisionReason' 2>/dev/null || :)"
case "$rc:$decision:$reason" in
  "0:deny:Destructive command guard denied the command (reason=engine_output_invalid)"*) pass "N3 an engine allow verdict denies as engine_output_invalid" ;;
  *) fail "N3 an engine allow verdict gave exit $rc, decision $decision, reason ${reason:0:120}" ;;
esac

printf '%s\n' '== N4: with an rm-roots file set, nothing allows either =='
ROOTS_HOME="$TEST_ROOT/acct-home"
ROOTS_DIR="$TEST_ROOT/rm-root"
ROOTS_WORKSPACE="$TEST_ROOT/workspace"
ROOTS_GUARD="$TEST_ROOT/guard-rm-roots.sh"
mkdir -p "$ROOTS_HOME/.claude/rails" "$ROOTS_DIR" "$ROOTS_WORKSPACE"
chmod 700 "$ROOTS_HOME/.claude/rails"
printf '%s\n' "$ROOTS_DIR" >"$ROOTS_HOME/.claude/rails/rm-roots"
chmod 600 "$ROOTS_HOME/.claude/rails/rm-roots"
printf 'x\n' >"$ROOTS_DIR/file.md"
# A copy of the guard whose rm_roots_account_home prints the fixture home.
awk -v home="$ROOTS_HOME" '
  /^rm_roots_account_home\(\) \{$/ { print; printf "  printf %c%%s%c %c%s%c\n", 39, 39, 39, home, 39; on = 1; done++; next }
  on && /^}$/ { on = 0; print; next }
  on { next }
  { print }
  END { if (done != 1) exit 1 }
' "$GUARD" >"$ROOTS_GUARD" || { fail "N4 fixture: the guard has no rm_roots_account_home function to rewrite"; exit 1; }
N2_GUARD="$ROOTS_GUARD"
n2_rows
N2_CWD="$ROOTS_WORKSPACE"
n2 "rm-roots file below a listed root, from a folder in no repository" "rm -f $ROOTS_DIR/file.md"
out="$(payload 'printf safe' | /bin/bash --noprofile --norc -p "$ROOTS_GUARD" --adapter-policy "$STANDIN" 2>/dev/null)"
rc=$?
decision="$(printf '%s' "$out" | "$JQ" -er '.hookSpecificOutput.permissionDecision' 2>/dev/null || printf none)"
reason="$(printf '%s' "$out" | "$JQ" -er '.hookSpecificOutput.permissionDecisionReason' 2>/dev/null || :)"
case "$rc:$decision:$reason" in
  "0:deny:Destructive command guard denied the command (reason=engine_output_invalid)"*) pass "N4 with rm-roots set, an engine allow verdict denies as engine_output_invalid" ;;
  *) fail "N4 with rm-roots set, an engine allow verdict gave exit $rc, decision $decision, reason ${reason:0:120}" ;;
esac

if [ "$FAILURES" -eq 0 ]; then
  printf 'ALL NEVER-ALLOW TESTS PASSED\n'
  exit 0
fi
printf '%s TEST(S) FAILED\n' "$FAILURES"
exit 1
