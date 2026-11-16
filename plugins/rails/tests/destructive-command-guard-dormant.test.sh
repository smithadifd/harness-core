#!/bin/bash
# The lane-registry layer is dormant unless the loop's variables are set, and
# an armed registry that is absent, untrusted or malformed denies by name.
#   L1 unarmed, no registry function runs: a copy whose registry functions
#      each begin with `exit 97` gives the real guard's verdict on every
#      worktree-removal case of the temp-root and unregistered rules.
#   L2 the guard log: unarmed, nothing is written below HOME; armed with
#      SEAT_SCRATCH, one record with exactly timestamp, event, code, class and
#      cwd; a FIFO at the log path is never waited on or written; an
#      unattended session writes nothing.
#   L3 armed and broken: each broken registry denies by its code for every
#      judged removal; a command that is not a removal stays silent.
# Command strings are inert JSON data and are never executed.
set -u
LC_ALL=C

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUARD="$HERE/../hooks/destructive-command-guard.sh"
JQ="$(command -v jq)" || { printf 'FAIL: jq is not on PATH\n'; exit 1; }
T="$(mktemp -d "$(cd /tmp && pwd -P)/destructive-guard-dormant.XXXXXX")" || exit 1
PRIMARY="$T/primary"
GITHOME="$T/githome"

FAILURES=0
pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; FAILURES=$((FAILURES + 1)); }

git_q() { HOME="$GITHOME" git -c user.name=t -c user.email=t@example.invalid "$@"; }
mkdir -p "$GITHOME" "$T/copies"
git_q init -q "$PRIMARY" || exit 1
printf 'x\n' >"$PRIMARY/f.txt"
git_q -C "$PRIMARY" add f.txt && git_q -C "$PRIMARY" commit -qm seed || exit 1
mkdir -p "$PRIMARY/build"
for w in a b c d; do git_q -C "$PRIMARY" worktree add -q --detach "$T/wt-$w" HEAD || exit 1; done

UNSET=(-u LANE_ROOTS_REGISTRY -u CLEANUP_ITEMS_REGISTRY -u CLEANUP_SEAT -u SEAT_SCRATCH -u CLAUDE_SEAT_UNATTENDED)
# policy GUARD CWD COMMAND [ENV=VAL...]: prints "decision/reason" or "exit N"
policy() {
  local guard="$1" cwd="$2" command_data="$3" input out rc
  shift 3
  input="$("$JQ" -cn --arg cwd "$cwd" --arg command "$command_data" '{cwd:$cwd,command:$command}')"
  out="$(cd "$cwd" && printf '%s' "$input" | /usr/bin/env "${UNSET[@]}" HOME="$GITHOME" "$@" /bin/bash --noprofile --norc -p "$guard" --policy 2>/dev/null)"
  rc=$?
  [ "$rc" -eq 0 ] || { printf 'exit %s' "$rc"; return; }
  printf '%s' "$out" | "$JQ" -er '.decision + "/" + .reason' 2>/dev/null || printf 'invalid'
}
# hook CWD COMMAND [ENV=VAL...]: the adapter; sets OUT, RC, ERR.
hook() {
  local cwd="$1" command_data="$2" input
  shift 2
  input="$("$JQ" -cn --arg cwd "$cwd" --arg command "$command_data" '{hook_event_name:"PreToolUse",tool_name:"Bash",tool_input:{command:$command},cwd:$cwd}')"
  OUT="$(cd "$cwd" && printf '%s' "$input" | /usr/bin/env "${UNSET[@]}" HOME="$GITHOME" "$@" /usr/bin/perl -e 'alarm 30; exec @ARGV' /bin/bash --noprofile --norc -p "$GUARD" 2>"$T/hook.err")"
  RC=$?
  ERR="$(cat "$T/hook.err")"
}
reason_of() {
  if [ "$RC" -eq 0 ] && [ -z "$OUT" ]; then printf silent; return; fi
  printf '%s' "$OUT" | "$JQ" -er '.hookSpecificOutput.permissionDecisionReason' 2>/dev/null \
    | sed -n 's/^Destructive command guard denied the command (reason=\([a-z_]*\)).*/\1/p'
}
expect_hook() { # LABEL WANT
  local got
  got="$(reason_of)"
  if [ "$RC" -eq 0 ] && [ "$got" = "$2" ]; then pass "$1 -> $2"; else fail "$1: expected $2, got exit $RC, ${got:-output ${OUT:0:120}}"; fi
}

printf '%s\n' '== L1 unarmed: no registry function runs =='
python3 -I - "$GUARD" "$T/copies/stubbed.sh" <<'PY' || { fail "L1 fixture: the stub copy could not be written"; exit 1; }
import sys
text = open(sys.argv[1], encoding="utf-8").read()
for name in ("path_in_lane_registry", "trusted_cleanup_registry", "cleanup_registry_mentions_worktree", "cleanup_registry_worktree_row"):
    head = "\n%s() {" % name
    if text.count(head) != 1:
        sys.exit("no single definition of %s" % name)
    i = text.index(head) + len(head)
    eol = text.index("\n", i)
    text = text[:eol + 1] + "  exit 97\n" + text[eol + 1:]
open(sys.argv[2], "w", encoding="utf-8").write(text)
PY
sed 's|^TEMP_ROOTS=.*|TEMP_ROOTS=()|' "$GUARD" >"$T/copies/real-no-temp.sh"
sed 's|^TEMP_ROOTS=.*|TEMP_ROOTS=()|' "$T/copies/stubbed.sh" >"$T/copies/stubbed-no-temp.sh"
grep -q '^TEMP_ROOTS=()$' "$T/copies/real-no-temp.sh" && grep -q '^TEMP_ROOTS=()$' "$T/copies/stubbed-no-temp.sh" \
  || { fail "L1 fixture: no TEMP_ROOTS line to rewrite"; exit 1; }
l1() { # LABEL REAL STUBBED CWD COMMAND WANT
  local real stub
  real="$(policy "$2" "$4" "$5")"
  stub="$(policy "$3" "$4" "$5")"
  if [ "$stub" = "$real" ] && [ "$real" = "$6" ]; then
    pass "L1 $1: the stubbed copy gives the real verdict ($real)"
  else
    fail "L1 $1: real $real, stubbed $stub, want $6"
  fi
}
l1 "temp-root rule, plain form" "$GUARD" "$T/copies/stubbed.sh" "$PRIMARY" "git worktree remove $T/wt-a" no_opinion/accepted
l1 "temp-root rule, -C form" "$GUARD" "$T/copies/stubbed.sh" "$T" "git -C $PRIMARY worktree remove $T/wt-b" no_opinion/accepted
l1 "temp-root rule, forced" "$GUARD" "$T/copies/stubbed.sh" "$PRIMARY" "git worktree remove --force $T/wt-a" deny/unsafe_worktree_remove
l1 "unregistered, no temp roots, plain form" "$T/copies/real-no-temp.sh" "$T/copies/stubbed-no-temp.sh" "$PRIMARY" "git worktree remove $T/wt-c" deny/unsafe_worktree_remove
l1 "unregistered, no temp roots, -C form" "$T/copies/real-no-temp.sh" "$T/copies/stubbed-no-temp.sh" "$T" "git -C $PRIMARY worktree remove $T/wt-d" deny/unsafe_worktree_remove
l1 "an ordinary rm" "$GUARD" "$T/copies/stubbed.sh" "$PRIMARY" "rm -rf build" no_opinion/accepted

printf '%s\n' '== L2 the guard log is armed only =='
L2_HOME="$T/l2-home"
mkdir -p "$L2_HOME"
L2_BEFORE="$(cd "$L2_HOME" && find . | sort)"
hook "$PRIMARY" "rm -rf /" HOME="$L2_HOME"
expect_hook "L2 unarmed deny" unsafe_rm
L2_AFTER="$(cd "$L2_HOME" && find . | sort)"
if [ "$L2_BEFORE" = "$L2_AFTER" ]; then pass "L2 unarmed: no file is written below HOME"; else fail "L2 unarmed: files appeared below HOME: $L2_AFTER"; fi
SEAT="$T/seat-scratch"
mkdir -p "$SEAT"
hook "$PRIMARY" "rm -rf $T/outside-ZZMARKERZZ" "SEAT_SCRATCH=$SEAT"
expect_hook "L2 armed deny" unsafe_rm
if [ -f "$SEAT/guard.log" ] && [ "$(wc -l <"$SEAT/guard.log" | tr -d ' ')" = 1 ]; then
  pass "L2 armed: one record in SEAT_SCRATCH/guard.log"
else
  fail "L2 armed: expected one record in $SEAT/guard.log"
fi
L2_KEYS="$("$JQ" -c 'keys' "$SEAT/guard.log" 2>/dev/null)"
if [ "$L2_KEYS" = '["class","code","cwd","event","timestamp"]' ]; then pass "L2 the record has exactly timestamp, event, code, class and cwd"; else fail "L2 the record keys are $L2_KEYS"; fi
L2_REC="$("$JQ" -r '[.event, .code, .class, .cwd] | join(" ")' "$SEAT/guard.log" 2>/dev/null)"
if [ "$L2_REC" = "deny unsafe_rm rm $PRIMARY" ]; then pass "L2 the record is deny, unsafe_rm, rm and the cwd"; else fail "L2 the record is: $L2_REC"; fi
if grep -q 'ZZMARKERZZ\|rm -rf' "$SEAT/guard.log"; then fail "L2 the record holds command text"; else pass "L2 the record holds no command text"; fi
FIFO_SEAT="$T/fifo-seat"
mkdir -p "$FIFO_SEAT"
mkfifo "$FIFO_SEAT/guard.log"
hook "$PRIMARY" "rm -rf /" "SEAT_SCRATCH=$FIFO_SEAT"
expect_hook "L2 a FIFO at the log path: the deny still comes, unblocked" unsafe_rm
if [ -p "$FIFO_SEAT/guard.log" ]; then pass "L2 the FIFO is left as it was"; else fail "L2 the FIFO was replaced"; fi
UNATT_SEAT="$T/unattended-seat"
mkdir -p "$UNATT_SEAT"
hook "$PRIMARY" "rm -rf /" "SEAT_SCRATCH=$UNATT_SEAT" CLAUDE_SEAT_UNATTENDED=1
expect_hook "L2 unattended deny" unsafe_rm
if [ -z "$(ls -A "$UNATT_SEAT")" ]; then pass "L2 unattended: nothing is written"; else fail "L2 unattended: a file was written"; fi

printf '%s\n' '== L3 armed and broken: deny by name =='
L3_DIR="$T/l3"
mkdir -p "$L3_DIR"
chmod 700 "$L3_DIR"
ABSENT_LANE="LANE_ROOTS_REGISTRY=$L3_DIR/lane-roots.tsv"
# The round 1 probe: a clean linked tree on a pushed HEAD, outside the temp
# roots (a copy with them emptied), with the lane registry armed to an absent
# file in an existing folder.
REMOTE="$T/remote.git"
git_q init -q --bare "$REMOTE" || exit 1
git_q -C "$PRIMARY" push -q "$REMOTE" HEAD:refs/heads/main || exit 1
git_q -C "$PRIMARY" fetch -q "$REMOTE" main:refs/remotes/origin/main || exit 1
got="$(policy "$T/copies/real-no-temp.sh" "$T" "git -C $PRIMARY worktree remove $T/wt-c" "$ABSENT_LANE")"
if [ "$got" = deny/lane_registry_absent_armed ]; then pass "L3 armed, absent lane registry: a pushed-worktree removal -> $got"; else fail "L3 armed, absent lane registry: a pushed-worktree removal gave $got"; fi
got="$(policy "$GUARD" "$PRIMARY" "git worktree remove $T/wt-a" "$ABSENT_LANE")"
if [ "$got" = deny/lane_registry_absent_armed ]; then pass "L3 armed, absent lane registry: a temp-root removal -> $got"; else fail "L3 armed, absent lane registry: a temp-root removal gave $got"; fi
hook "$PRIMARY" "rm -rf build" "$ABSENT_LANE"
expect_hook "L3 armed, absent lane registry: rm -rf build" lane_registry_absent_armed
hook "$PRIMARY" "ls" "$ABSENT_LANE"
expect_hook "L3 armed, absent lane registry: ls" silent

CLEAN_DIR="$T/cleanup"
mkdir -p "$CLEAN_DIR"
chmod 700 "$CLEAN_DIR"
CREG="$CLEAN_DIR/cleanup-items.tsv"
CHEADER=$'repo\tpath\tkind\tseat\trow\tcreated_at'
l3_cleanup() { # LABEL CODE [ENV...]
  local label="$1" code="$2"
  shift 2
  hook "$PRIMARY" "rm -rf build" "CLEANUP_ITEMS_REGISTRY=$CREG" "$@"
  expect_hook "L3 $label: rm -rf build" "$code"
  hook "$PRIMARY" "ls" "CLEANUP_ITEMS_REGISTRY=$CREG" "$@"
  expect_hook "L3 $label: ls" silent
}
l3_cleanup "cleanup registry absent" cleanup_registry_absent_armed CLEANUP_SEAT=this-seat
printf '%s\n' "$CHEADER" >"$CREG"
chmod 644 "$CREG"
l3_cleanup "cleanup registry untrusted (0644)" cleanup_registry_untrusted CLEANUP_SEAT=this-seat
chmod 600 "$CREG"
printf 'repo\tpath\n' >"$CREG"
l3_cleanup "cleanup registry with a bad header" cleanup_registry_malformed CLEANUP_SEAT=this-seat
printf '%s\n%s\t%s\tworktree\n' "$CHEADER" "$PRIMARY" "$T/wt-a" >"$CREG"
l3_cleanup "cleanup registry with a short row" cleanup_registry_malformed CLEANUP_SEAT=this-seat
printf '%s\n' "$CHEADER" >"$CREG"
l3_cleanup "cleanup registry armed with CLEANUP_SEAT empty" cleanup_seat_missing CLEANUP_SEAT=
l3_cleanup "a valid empty cleanup registry (control)" silent CLEANUP_SEAT=this-seat

if [ "$FAILURES" -eq 0 ]; then
  printf 'ALL DORMANT-LAYER TESTS PASSED\n'
  exit 0
fi
printf '%s TEST(S) FAILED\n' "$FAILURES"
exit 1
