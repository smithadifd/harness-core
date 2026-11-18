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
#   T12-T23 a project's settings env reaches both git guard hooks of
#      hooks.json (the floor and the stored-options hook, run by gg_pair): an
#      option variable changes nothing, and the loop variables only turn an ask
#      into a deny or write a record (WALKFIX-HC design § 4.4, § 4.6).
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

printf '%s\n' '== T12-T17 a project option variable changes no verdict =='
M1='git push origin main'
F1='git push -f origin feature'
K2='git commit -m nope'
gg_pair - - - "$M1" CLAUDE_PLUGIN_OPTION_PUSH_TO_MAIN=off
gg_expect "T12 none stored, CLAUDE_PLUGIN_OPTION_PUSH_TO_MAIN=off, a push to main" ask:push_main_branch
gg_pair - - - "$F1" CLAUDE_PLUGIN_OPTION_FORCE_PUSH=off
gg_expect "T13 none stored, CLAUDE_PLUGIN_OPTION_FORCE_PUSH=off, a force-push" ask:push_force
gg_pair ask ask deny "$K2" CLAUDE_PLUGIN_OPTION_COMMIT_STYLE=off
gg_expect "T14 commit_style=deny stored, CLAUDE_PLUGIN_OPTION_COMMIT_STYLE=off, a commit" deny:commit_nonconventional
gg_pair deny ask off "$M1" CLAUDE_PLUGIN_OPTION_PUSH_TO_MAIN=ask
gg_expect "T15 push_to_main=deny stored, CLAUDE_PLUGIN_OPTION_PUSH_TO_MAIN=ask, a push to main" deny:push_main_branch
for value in ask off; do
  gg_pair ask deny off "$F1" "CLAUDE_PLUGIN_OPTION_FORCE_PUSH=$value"
  gg_expect "T15b force_push=deny stored, CLAUDE_PLUGIN_OPTION_FORCE_PUSH=$value, a force-push" deny:push_force
done
gg_pair - - - "$M1" CLAUDE_PLUGIN_OPTION_PUSH_TO_MAIN=deny
gg_expect "T16 none stored, CLAUDE_PLUGIN_OPTION_PUSH_TO_MAIN=deny is ignored" ask:push_main_branch
gg_pair - - - "$F1" CLAUDE_PLUGIN_OPTION_FORCE_PUSH=deny
gg_expect "T16b none stored, CLAUDE_PLUGIN_OPTION_FORCE_PUSH=deny is ignored" ask:push_force
gg_pair - - - "$K2" CLAUDE_PLUGIN_OPTION_COMMIT_STYLE=deny
gg_expect "T17 none stored, CLAUDE_PLUGIN_OPTION_COMMIT_STYLE=deny is ignored" silent

printf '%s\n' '== T18-T23 the loop variables from a project only tighten =='
gg_pair - - - "$M1" CLAUDE_SEAT_UNATTENDED=1
gg_expect "T18 none stored, CLAUDE_SEAT_UNATTENDED=1, a push to main" deny:push_main_branch
case "${PAIR_OUTS[0]:-}" in
  *'the session is unattended, so a confirmation is a denial'*) pass "T18 the deny carries the unattended clause" ;;
  *) fail "T18 the unattended clause: ${PAIR_OUTS[0]:-}" ;;
esac
for value in 0 true ''; do
  gg_pair - - - "$M1" "CLAUDE_SEAT_UNATTENDED=$value"
  gg_expect "T19 none stored, CLAUDE_SEAT_UNATTENDED='$value', a push to main" ask:push_main_branch
done
T20_W="$T/t20-writable"
T20_RO="$T/t20-unwritable"
mkdir -p "$T20_W" "$T20_RO"
chmod 0500 "$T20_RO"
gg_pair - - - "$M1" CLAUDE_SEAT_UNATTENDED=1 "SEAT_SCRATCH=$T20_W"
gg_expect "T20 unattended, a writable SEAT_SCRATCH" deny:push_main_branch
if [ -f "$T20_W/owed.log" ] && [ "$(wc -l <"$T20_W/owed.log" | tr -d ' ')" = 1 ]; then pass "T20 one owed record in the writable folder"; else fail "T20 the writable folder's owed.log: $(cat "$T20_W/owed.log" 2>&1)"; fi
gg_pair - - - "$M1" CLAUDE_SEAT_UNATTENDED=1 "SEAT_SCRATCH=$T20_RO"
gg_expect "T20 unattended, an unwritable SEAT_SCRATCH" deny:push_main_branch
if printf '%s\n' "${PAIR_ERRS[0]:-}" | grep -qxF "rails git-guard: unattended denial not recorded: could not append to $T20_RO/owed.log"; then pass "T20 the could-not-append line on stderr"; else fail "T20 stderr: ${PAIR_ERRS[0]:-}"; fi
if [ ! -e "$T20_RO/owed.log" ]; then pass "T20 no record in the unwritable folder"; else fail "T20 a record in the unwritable folder"; fi
chmod 0700 "$T20_RO"
T21="$T/t21-project"
mkdir -p "$T21"
gg_pair - - - "$M1" "SEAT_SCRATCH=$T21"
gg_expect "T21 attended, SEAT_SCRATCH a project folder" ask:push_main_branch
if [ -f "$T21/guard.log" ] && [ "$(wc -l <"$T21/guard.log" | tr -d ' ')" = 1 ]; then pass "T21 one guard.log line"; else fail "T21 guard.log: $(cat "$T21/guard.log" 2>&1)"; fi
if grep -q 'git push' "$T21/guard.log" 2>/dev/null; then fail "T21 guard.log holds command text"; else pass "T21 guard.log holds no command text"; fi
mkdir -p "$T/t22rel"
gg_pair - - - "$M1" SEAT_SCRATCH=t22rel
gg_expect "T22 SEAT_SCRATCH relative" ask:push_main_branch
if [ -z "$(find "$T/t22rel" -mindepth 1 -print)" ]; then pass "T22 nothing written for a relative SEAT_SCRATCH"; else fail "T22 a relative SEAT_SCRATCH was written"; fi
gg_pair - - - "$M1" "SEAT_SCRATCH=$T/t22-no-such"
gg_expect "T22 SEAT_SCRATCH names no folder" ask:push_main_branch
if [ ! -e "$T/t22-no-such" ]; then pass "T22 nothing made for a missing SEAT_SCRATCH"; else fail "T22 a missing SEAT_SCRATCH was made"; fi
T22_SYM="$T/t22-sym"
mkdir -p "$T22_SYM"
ln -s "$T/t22-target" "$T22_SYM/guard.log"
gg_pair - - - "$M1" "SEAT_SCRATCH=$T22_SYM"
gg_expect "T22 guard.log a symlink" ask:push_main_branch
if [ ! -e "$T/t22-target" ]; then pass "T22 the symlink's target was not written"; else fail "T22 the symlink was followed"; fi
T23="$T/t23-seat"
mkdir -p "$T23"
gg_pair deny ask off "$M1" CLAUDE_SEAT_UNATTENDED=1 "SEAT_SCRATCH=$T23"
gg_expect "T23 push_to_main=deny stored, unattended" deny:push_main_branch
gg_pair_hook 1 deny:push_main_branch "T23 the stored-options hook denies"
if [ -f "$T23/owed.log" ] && [ "$(wc -l <"$T23/owed.log" | tr -d ' ')" = 1 ]; then pass "T23 exactly one owed record (the floor's converted ask)"; else fail "T23 owed.log: $(cat "$T23/owed.log" 2>&1)"; fi

gg_finish UNATTENDED
