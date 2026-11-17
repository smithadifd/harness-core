#!/bin/bash
# The git guard's dormant loop layer (design § 9), inert unless the loop's own
# variables are set:
#   U1 with CLAUDE_SEAT_UNATTENDED exactly 1, an ask is printed as a deny with
#      the unattended clause, and one owed line is appended to
#      $SEAT_SCRATCH/owed.log: the UTC time, the original ask reason and the
#      first 200 bytes of the command, tabs and newlines flattened.
#   U2 a missing SEAT_SCRATCH or a failed append still denies, with one fixed
#      stderr line; U-SYM a symlink planted at owed.log is never followed.
#   U3 any other value of CLAUDE_SEAT_UNATTENDED leaves the ask as it is; a
#      silent or deny verdict writes no owed line.
#   U-LOG the guard log: armed only by an existing absolute SEAT_SCRATCH in an
#      attended session, one JSON line per ask or deny, no command text.
#   U-DORM unarmed, nothing is written anywhere under HOME.
# Command strings are inert JSON data and are never executed.
set -u
LC_ALL=C
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUITE=unattended
. "$HERE/fixtures/git-guard/harness.sh"

SEAT="$T/seat"
mkdir -p "$SEAT"
FORCE_HINT="this is a force-push (--force, --force-with-lease, --force-if-includes, -f or a + refspec); confirm it, or set the rails plugin's force_push option"
MAIN_HINT="this push updates main or master; confirm it, or set the rails plugin's push_to_main option"

printf '%s\n' '== U1 an ask becomes a deny and owes one line =='
: >"$SEAT/owed.log"
gg_hook "$REPO_FEATURE" 'git push --force origin main' CLAUDE_SEAT_UNATTENDED=1 "SEAT_SCRATCH=$SEAT"
WANT='{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Git guard denied the command (reason=push_force): the session is unattended, so a confirmation is a denial; '"$FORCE_HINT"'."}}'
if [ "$OUT" = "$WANT" ]; then pass "U1 the exact unattended deny line"; else fail "U1 the deny line: $OUT"; fi
LINE="$(cat "$SEAT/owed.log")"
IFS=$'\t' read -r STAMP REASON COMMAND_TEXT <<<"$LINE"
if [ "$(wc -l <"$SEAT/owed.log" | tr -d ' ')" = 1 ] && [ "$(printf '%s\n' "$LINE" | awk -F '\t' '{print NF}')" = 3 ]; then
  pass "U1 one owed line with three tab-separated fields"
else
  fail "U1 owed.log: $(cat "$SEAT/owed.log")"
fi
if [[ "$STAMP" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]]; then pass "U1 the time is UTC ISO-8601"; else fail "U1 the time: $STAMP"; fi
if [ "$REASON" = "Git guard asks before this command (reason=push_force): $FORCE_HINT." ]; then pass "U1 the original ask reason"; else fail "U1 the reason: $REASON"; fi
if [ "$COMMAND_TEXT" = 'git push --force origin main' ]; then pass "U1 the command"; else fail "U1 the command: $COMMAND_TEXT"; fi
if [ -z "$ERR" ] || ! printf '%s' "$ERR" | grep -q 'rails git-guard'; then pass "U1 no failure line on stderr"; else fail "U1 stderr: $ERR"; fi

: >"$SEAT/owed.log"
LONG_TAIL="$(python3 -I -c 'print("x" * 300, end="")')"
LONG=$'git push origin main\t\n'"$LONG_TAIL"
gg_hook "$REPO_FEATURE" "$LONG" CLAUDE_SEAT_UNATTENDED=1 "SEAT_SCRATCH=$SEAT"
gg_expect "U1b a long command with a tab and a newline" deny:push_main_branch
LINE="$(cat "$SEAT/owed.log")"
IFS=$'\t' read -r STAMP REASON COMMAND_TEXT <<<"$LINE"
WANT_TEXT="$(python3 -I -c 'import sys; s = sys.argv[1][:200]; print(s.replace("\t", " ").replace("\n", " "), end="")' "$LONG")"
if [ "$COMMAND_TEXT" = "$WANT_TEXT" ] && [ "${#COMMAND_TEXT}" -eq 200 ]; then pass "U1b the first 200 bytes, flattened"; else fail "U1b the command field (${#COMMAND_TEXT} bytes): ${COMMAND_TEXT:0:60}"; fi
if [ "$REASON" = "Git guard asks before this command (reason=push_main_branch): $MAIN_HINT." ]; then pass "U1b the reason"; else fail "U1b the reason: $REASON"; fi

printf '%s\n' '== U2 the record can fail; the deny cannot =='
gg_hook "$REPO_FEATURE" 'git push --force origin main' CLAUDE_SEAT_UNATTENDED=1
gg_expect "U2 SEAT_SCRATCH unset" deny:push_force
if printf '%s\n' "$ERR" | grep -qxF 'rails git-guard: unattended denial not recorded: SEAT_SCRATCH is unset or empty'; then pass "U2 the unset line on stderr"; else fail "U2 stderr: $ERR"; fi
APPEND_FAIL="$T/append-fail"
mkdir -p "$APPEND_FAIL/owed.log"
gg_hook "$REPO_FEATURE" 'git push --force origin main' CLAUDE_SEAT_UNATTENDED=1 "SEAT_SCRATCH=$APPEND_FAIL"
gg_expect "U2 owed.log is a directory" deny:push_force
if printf '%s\n' "$ERR" | grep -qxF "rails git-guard: unattended denial not recorded: could not append to $APPEND_FAIL/owed.log"; then pass "U2 the append line on stderr"; else fail "U2 stderr: $ERR"; fi
gg_hook "$REPO_FEATURE" 'git push --force origin main' CLAUDE_SEAT_UNATTENDED=1 "SEAT_SCRATCH=$T/no-such-folder"
gg_expect "U2 SEAT_SCRATCH names no folder" deny:push_force
if printf '%s\n' "$ERR" | grep -qxF "rails git-guard: unattended denial not recorded: SEAT_SCRATCH is not an existing absolute directory: $T/no-such-folder"; then pass "U2 the missing-folder line on stderr"; else fail "U2 stderr: $ERR"; fi
SYM_SEAT="$T/sym-seat"
mkdir -p "$SYM_SEAT"
printf 'outside\n' >"$T/outside.txt"
ln -s "$T/outside.txt" "$SYM_SEAT/owed.log"
gg_hook "$REPO_FEATURE" 'git push --force origin main' CLAUDE_SEAT_UNATTENDED=1 "SEAT_SCRATCH=$SYM_SEAT"
gg_expect "U-SYM a symlink at owed.log" deny:push_force
if [ "$(cat "$T/outside.txt")" = outside ]; then pass "U-SYM the file outside is unchanged"; else fail "U-SYM the file outside was written"; fi

printf '%s\n' '== U3 only exactly 1 converts; only an ask owes =='
: >"$SEAT/owed.log"
gg_hook "$REPO_FEATURE" 'git push --force origin main' CLAUDE_SEAT_UNATTENDED=true "SEAT_SCRATCH=$SEAT"
WANT='{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"Git guard asks before this command (reason=push_force): '"$FORCE_HINT"'."}}'
if [ "$OUT" = "$WANT" ]; then pass "U3 CLAUDE_SEAT_UNATTENDED=true: the exact ask line"; else fail "U3 true: $OUT"; fi
gg_hook "$REPO_FEATURE" 'git push --force origin main'
if [ "$OUT" = "$WANT" ]; then pass "U3 unset: the exact ask line"; else fail "U3 unset: $OUT"; fi
GG_MAIN=off
gg_hook "$REPO_FEATURE" 'git push origin main' CLAUDE_SEAT_UNATTENDED=1 "SEAT_SCRATCH=$SEAT"
gg_expect "U3 push_to_main=off, unattended: a push to main" silent
GG_MAIN=ask
GG_COMMIT=deny
gg_hook "$REPO_FEATURE" 'git commit -m nope' CLAUDE_SEAT_UNATTENDED=1 "SEAT_SCRATCH=$SEAT"
gg_expect "U3 a deny stays a deny" deny:commit_nonconventional
GG_COMMIT=off
if [ ! -s "$SEAT/owed.log" ]; then pass "U3 no owed line for a silent, an attended or a deny verdict"; else fail "U3 owed.log: $(cat "$SEAT/owed.log")"; fi

printf '%s\n' '== U-LOG the guard log, armed only =='
LOG_SEAT="$T/log-seat"
mkdir -p "$LOG_SEAT"
gg_hook "$REPO_FEATURE" 'git push --force origin ZZMARKERZZ' "SEAT_SCRATCH=$LOG_SEAT"
gg_expect "U-LOG an armed ask" ask:push_force
GG_COMMIT=deny
gg_hook "$REPO_FEATURE" 'git commit -m ZZMARKERZZ' "SEAT_SCRATCH=$LOG_SEAT"
gg_expect "U-LOG an armed deny" deny:commit_nonconventional
GG_COMMIT=off
gg_hook "$REPO_FEATURE" 'git status' "SEAT_SCRATCH=$LOG_SEAT"
gg_expect "U-LOG an armed silence" silent
LOG_CHECK="$("$JQ" -sr --arg cwd "$REPO_FEATURE" '
  if length != 2 then "lines \(length)"
  elif (map(keys == ["class","code","cwd","event","timestamp"]) | all | not) then "keys"
  elif .[0].event != "ask" or .[0].code != "push_force" or .[0].class != "git-push" then "first \(.[0] | tostring)"
  elif .[1].event != "deny" or .[1].code != "commit_nonconventional" or .[1].class != "text-match:conventional-commit" then "second \(.[1] | tostring)"
  elif (map(.cwd == $cwd) | all | not) then "cwd"
  else "ok" end' "$LOG_SEAT/guard.log" 2>&1)"
if [ "$LOG_CHECK" = ok ]; then pass "U-LOG two lines: event, code, class and cwd"; else fail "U-LOG guard.log: $LOG_CHECK"; fi
if grep -q ZZMARKERZZ "$LOG_SEAT/guard.log"; then fail "U-LOG the log holds command text"; else pass "U-LOG the log holds no command text"; fi
UNATT_SEAT="$T/unattended-log-seat"
mkdir -p "$UNATT_SEAT"
gg_hook "$REPO_FEATURE" 'git push --force origin main' CLAUDE_SEAT_UNATTENDED=1 "SEAT_SCRATCH=$UNATT_SEAT"
if [ ! -e "$UNATT_SEAT/guard.log" ]; then pass "U-LOG an unattended session writes no guard log"; else fail "U-LOG an unattended session wrote guard.log"; fi

printf '%s\n' '== U-DORM unarmed, nothing anywhere =='
DORM_HOME="$T/dorm-home"
mkdir -p "$DORM_HOME"
GG_HOME_SAVED="$GG_HOME"
GG_HOME="$DORM_HOME"
gg_hook "$REPO_FEATURE" 'git push --force origin main'
gg_expect "U-DORM an unarmed ask" ask:push_force
GG_COMMIT=deny
gg_hook "$REPO_FEATURE" 'git commit -m nope'
gg_expect "U-DORM an unarmed deny" deny:commit_nonconventional
GG_COMMIT=off
GG_HOME="$GG_HOME_SAVED"
if [ -z "$(find "$DORM_HOME" -mindepth 1 -print)" ]; then pass "U-DORM no file anywhere under HOME"; else fail "U-DORM files under HOME: $(find "$DORM_HOME" -mindepth 1 -print | tr '\n' ' ')"; fi

gg_finish UNATTENDED
