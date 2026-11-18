#!/bin/bash
# The destructive-command guard through the real hook form, for an explicit
# rm whose words hold a home-folder spelling the guard does not expand:
#   H  ~, ~/x, ~user/x, $HOME, $HOME/x, ${HOME} and ${HOME}/x, quoted or not,
#      deny as unsafe_rm with unsafe_rm's hint (an rm segment that does not
#      parse because of such a word is a home path the guard will not judge,
#      not an over-long or unbalanced command);
#   K  controls: a parse failure with no home spelling keeps
#      unsupported_syntax and its hint ($X, $HOMEDIR, ${HOME:-/}, a
#      backslash, a redirection before rm); a quoted '~x' in a worktree is a
#      literal folder name and stays silent; the compound suite's R5 now
#      denies as unsafe_rm.
# Every row but K6 and H16 runs from two working folders: N, a folder in no
# git repository (the throwaway HOME itself, as a session started in a home
# folder), and W, a git worktree. HOME is a throwaway folder; the real home
# folder is never consulted. Each row runs the hook exactly as hooks.json does
# (env -u BASH_ENV -u ENV /bin/bash --noprofile --norc -p GUARD) with a
# PreToolUse JSON on stdin. Command strings are inert JSON data and never run.
set -u
LC_ALL=C

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUARD="$HERE/../hooks/destructive-command-guard.sh"
JQ="$(command -v jq)" || { printf 'FAIL: jq is not on PATH\n'; exit 1; }
TEST_ROOT="$(mktemp -d "$(cd /tmp && pwd -P)/destructive-guard-home-spelling.XXXXXX")" || exit 1
FIXHOME="$TEST_ROOT/home"
WORKTREE="$TEST_ROOT/worktree"

HINT_UNSAFE_RM='an rm target is not a literal path strictly below the current worktree or the named session scratchpad; spell a worktree target relative to the current folder, because an absolute home path is refused by spelling'
HINT_UNSUPPORTED_SYNTAX='the command is over 6000 bytes or has an unbalanced substitution or quote; split or shorten it'

FAILURES=0
pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; FAILURES=$((FAILURES + 1)); }

mkdir -p "$FIXHOME/guard-target" "$TEST_ROOT/githome" "$WORKTREE/~x" || exit 1
printf 'keep\n' >"$FIXHOME/guard-target/keep.txt"
printf 'keep\n' >"$WORKTREE/~x/keep.txt"
git_q() { HOME="$TEST_ROOT/githome" git -c user.name=t -c user.email=t@example.invalid "$@"; }
git_q init -q "$WORKTREE" || exit 1
printf 'x\n' >"$WORKTREE/f.txt"
git_q -C "$WORKTREE" add f.txt && git_q -C "$WORKTREE" commit -qm seed || exit 1

printf '%s\n' '== fixture: N is in no repository, W is a worktree =='
if git -C "$FIXHOME" rev-parse --git-dir >/dev/null 2>&1; then
  fail "fixture: the N folder is inside a git repository; the N rows would not test it"
else
  pass "fixture: the N folder is in no git repository"
fi

hook() { # CWD COMMAND: sets OUT and RC
  OUT="$("$JQ" -cn --arg cwd "$1" --arg command "$2" \
    '{hook_event_name:"PreToolUse",tool_name:"Bash",tool_input:{command:$command},cwd:$cwd}' \
    | (cd "$1" && HOME="$FIXHOME" /usr/bin/env -u BASH_ENV -u ENV /bin/bash --noprofile --norc -p "$GUARD" 2>/dev/null))"
  RC=$?
}
deny_at() { # ID STATE CWD REASON HINT COMMAND
  local decision reason want
  hook "$3" "$6"
  decision="$(printf '%s' "$OUT" | "$JQ" -er '.hookSpecificOutput.permissionDecision' 2>/dev/null || printf none)"
  reason="$(printf '%s' "$OUT" | "$JQ" -er '.hookSpecificOutput.permissionDecisionReason' 2>/dev/null || :)"
  want="Destructive command guard denied the command (reason=$4): $5."
  if [ "$RC" -eq 0 ] && [ "$decision" = deny ] && [ "$reason" = "$want" ]; then
    pass "$1 $2 $6 -> deny/$4 with its hint"
  else
    fail "$1 $2 $6: expected deny/$4 with its hint, got exit $RC, decision $decision, reason ${reason:0:120}"
  fi
}
deny() { # ID REASON COMMAND: at N and at W
  local hint
  case "$2" in
    unsafe_rm) hint="$HINT_UNSAFE_RM" ;;
    unsupported_syntax) hint="$HINT_UNSUPPORTED_SYNTAX" ;;
    *) fail "$1: no hint known for $2"; return ;;
  esac
  deny_at "$1" N "$FIXHOME" "$2" "$hint" "$3"
  deny_at "$1" W "$WORKTREE" "$2" "$hint" "$3"
}
silent_at() { # ID STATE CWD COMMAND
  hook "$3" "$4"
  if [ "$RC" -eq 0 ] && [ -z "$OUT" ]; then
    pass "$1 $2 $4 -> silent"
  else
    fail "$1 $2 $4: expected silence, got exit $RC and output ${OUT:0:160}"
  fi
}

printf '%s\n' '== H: a home-folder spelling denies as unsafe_rm with its hint =='
deny H1 unsafe_rm 'rm -rf ~/guard-target'
deny H2 unsafe_rm 'rm -rf "$HOME/guard-target"'
deny H3 unsafe_rm 'rm -rf $HOME/guard-target'
deny H4 unsafe_rm 'rm -rf ${HOME}/guard-target'
deny H5 unsafe_rm 'rm -rf "${HOME}"/guard-target'
deny H6 unsafe_rm 'rm -rf ~'
deny H6 unsafe_rm 'rm -rf ~/'
deny H7 unsafe_rm 'rm -rf $HOME'
deny H7 unsafe_rm 'rm -rf "$HOME"'
deny H8 unsafe_rm 'rm -rf ${HOME}'
deny H9 unsafe_rm 'rm -rf ~someone/x'
deny H10 unsafe_rm 'rm ~/guard-target'
deny H11 unsafe_rm 'rm -r -f ~/guard-target'
deny H11 unsafe_rm 'rm -rf -- ~/guard-target'
deny H12 unsafe_rm 'sudo rm -rf ~/guard-target'
deny H13 unsafe_rm 'echo hi; rm -rf ~/guard-target'
deny H14 unsafe_rm 'rm -rf build ~/guard-target'
# The expanded path parses and is refused by the target rules, as before.
deny H15 unsafe_rm "rm -rf $FIXHOME/guard-target"
# A quoted '~/x' parses; from a folder in no repository the target rule for
# such a folder refuses its spelling.
deny_at H16 N "$FIXHOME" unsafe_rm "$HINT_UNSAFE_RM" "rm -rf '~/guard-target'"

printf '%s\n' '== K: every other parse failure keeps unsupported_syntax =='
deny K1 unsupported_syntax 'rm -rf "$X"'
deny K2 unsupported_syntax 'rm -rf $X/y'
deny K3 unsupported_syntax 'rm -rf $HOMEDIR/x'
deny K4 unsupported_syntax 'rm -rf ${HOME:-/}/x'
deny K5 unsupported_syntax 'rm -rf a\ b'
# A quoted tilde word parses: bash does not expand it, so it names a folder
# literally called ~x below the worktree, and the rm is judged as any other
# relative literal.
silent_at K6 W "$WORKTREE" "rm -rf '~x'"
# The compound suite's redirection rows with no home spelling.
deny K7 unsupported_syntax '2>/dev/null rm -rf /'
deny K7 unsupported_syntax '</dev/null rm -rf /'
deny K7 unsupported_syntax 'FOO=1 2>/dev/null rm -rf /'
deny K7 unsupported_syntax 'sudo 2>/dev/null rm -rf /'
deny K7 unsupported_syntax '2> /dev/null rm -f /etc/hosts'
deny K7 unsupported_syntax 'rm>/dev/null -rf /'
deny K7 unsupported_syntax '>&2 rm -rf /'
deny K7 unsupported_syntax '&>/dev/null rm -rf /'
# The compound suite's R5: a home spelling after a redirection.
deny K8 unsafe_rm 'echo hi && 2>/dev/null rm -rf ~'

printf '%s\n' '== the guard-target folder is untouched =='
if [ -f "$FIXHOME/guard-target/keep.txt" ] && [ -f "$WORKTREE/~x/keep.txt" ]; then
  pass "fixture folders are intact (no command string ran)"
else
  fail "a fixture folder changed"
fi

if [ "$FAILURES" -eq 0 ]; then
  printf 'ALL DESTRUCTIVE GUARD HOME-SPELLING TESTS PASSED\n'
  exit 0
fi
printf '%s TEST(S) FAILED\n' "$FAILURES"
exit 1
