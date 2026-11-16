#!/bin/bash
# The destructive-command guard through the real hook form, for commands
# whose command word is not the segment's first word:
#   R  a redirection before (or glued to) the command word, in every place a
#      check finds that word: the rm scan, the worktree-removal patterns, the
#      repointing classes, the delegated-text walk, and the command splitter
#      (`>&N` and `&>FILE` are not separators);
#   G  an rm, recursive or not, after a control word or grouping opener, in a
#      case arm, a function body, a process substitution or a delegated
#      shell, judged the way a top-level rm is;
#   S  controls: ordinary commands with redirections and compound commands
#      stay silent, and an accepted rm in a compound stays silent.
# Each row runs the hook exactly as hooks.json does (env -u BASH_ENV -u ENV
# /bin/bash --noprofile --norc -p GUARD) with a PreToolUse JSON on stdin,
# from the worktree. Command strings are inert JSON data and never run.
set -u
LC_ALL=C

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUARD="${COMPOUND_TEST_GUARD:-$HERE/../hooks/destructive-command-guard.sh}"
JQ="$(command -v jq)" || { printf 'FAIL: jq is not on PATH\n'; exit 1; }
TEST_ROOT="$(mktemp -d "$(cd /tmp && pwd -P)/destructive-guard-compound.XXXXXX")" || exit 1
WORKTREE="$TEST_ROOT/worktree"

FAILURES=0
pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; FAILURES=$((FAILURES + 1)); }

mkdir -p "$WORKTREE/build" "$TEST_ROOT/githome"
git_q() { HOME="$TEST_ROOT/githome" git -c user.name=t -c user.email=t@example.invalid "$@"; }
git_q init -q "$WORKTREE" || exit 1
printf 'x\n' >"$WORKTREE/f.txt"
printf 'x\n' >"$WORKTREE/build/x"
git_q -C "$WORKTREE" add f.txt && git_q -C "$WORKTREE" commit -qm seed || exit 1
ln -s "$TEST_ROOT/elsewhere" "$WORKTREE/lnk" || exit 1

hook() { # COMMAND: sets OUT and RC
  OUT="$("$JQ" -cn --arg cwd "$WORKTREE" --arg command "$1" \
    '{hook_event_name:"PreToolUse",tool_name:"Bash",tool_input:{command:$command},cwd:$cwd}' \
    | (cd "$WORKTREE" && HOME="$TEST_ROOT/githome" /usr/bin/env -u BASH_ENV -u ENV /bin/bash --noprofile --norc -p "$GUARD" 2>/dev/null))"
  RC=$?
}
deny() { # ID REASON COMMAND
  local decision reason
  hook "$3"
  decision="$(printf '%s' "$OUT" | "$JQ" -er '.hookSpecificOutput.permissionDecision' 2>/dev/null || printf none)"
  reason="$(printf '%s' "$OUT" | "$JQ" -er '.hookSpecificOutput.permissionDecisionReason' 2>/dev/null || :)"
  case "$RC:$decision:$reason" in
    "0:deny:Destructive command guard denied the command (reason=$2)"*) pass "$1 $3 -> deny/$2" ;;
    *) fail "$1 $3: expected deny/$2, got exit $RC, decision $decision, reason ${reason:0:90}" ;;
  esac
}
silent() { # ID COMMAND
  hook "$2"
  if [ "$RC" -eq 0 ] && [ -z "$OUT" ]; then
    pass "$1 $2 -> silent"
  else
    fail "$1 $2: expected silence, got exit $RC and output ${OUT:0:160}"
  fi
}

printf '%s\n' '== R: a redirection before the command word =='
deny R1 unsupported_syntax '2>/dev/null rm -rf /'
deny R2 unsupported_syntax '</dev/null rm -rf /'
deny R3 unsupported_syntax 'FOO=1 2>/dev/null rm -rf /'
deny R4 unsupported_syntax 'sudo 2>/dev/null rm -rf /'
deny R5 unsupported_syntax 'echo hi && 2>/dev/null rm -rf ~'
deny R6 unsupported_syntax '2> /dev/null rm -f /etc/hosts'
deny R7 unsupported_syntax 'rm>/dev/null -rf /'
deny R8 unsafe_worktree_remove '2>/dev/null git worktree remove --force /tmp/x'
deny R9 unsafe_worktree_remove '2>/dev/null git worktree remove /tmp/x'
deny R10 unsupported_syntax '>&2 rm -rf /'
deny R11 unsupported_syntax '&>/dev/null rm -rf /'
deny R12 path_repointing_cd '2>/dev/null cd / && rm -rf etc'
deny R13 unsafe_rm 'bash -c "2>/dev/null rm -f /etc/hosts"'
deny R14 unsafe_rm '2>/dev/null bash -c "rm -f /etc/hosts"'
deny R15 unsafe_redirect 'echo x &> lnk'
deny R16 unsafe_redirect 'echo x >| lnk'

printf '%s\n' '== G: every rm the grammar reaches, recursive or not =='
deny G1 unsafe_rm '{ rm -f /etc/hosts; }'
deny G2 unsafe_rm '(rm -f /etc/hosts)'
deny G3 unsafe_rm 'if true; then rm -f /etc/hosts; fi'
deny G4 unsafe_rm 'for f in a; do rm -f /etc/hosts; done'
deny G5 unsafe_rm 'bash -c "rm -f /etc/hosts"'
deny G6 unsafe_rm 'if rm -f /etc/hosts; then :; fi'
deny G7 unsafe_rm 'while rm -f /etc/hosts; do break; done'
deny G8 unsafe_rm 'until rm -f /etc/hosts; do break; done'
deny G9 unsafe_rm 'if false; then :; else rm -f /etc/hosts; fi'
deny G10 unsafe_rm 'case x in a) :;; b) rm -f /etc/hosts;; esac'
deny G11 unsafe_rm 'f() { rm -f /etc/hosts; }; f'
deny G12 unsafe_rm 'cat <(rm -f /etc/hosts)'
deny G13 unsafe_rm 'time -p rm -f /etc/hosts'
deny G14 unsafe_rm 'setsid rm -f /etc/hosts'
deny G15 unsafe_rm 'echo "$(rm -f /etc/hosts)"'
deny G16 path_repointing_ln 'ln -s / l && rm -f l/etc/hosts'
deny G17 non_literal_rm_targets 'echo /etc/hosts | xargs rm -f'
deny G18 unsupported_rm_prefix 'sudo -X rm -f /etc/hosts'
deny G19 unsafe_rm 'bash -c "cd / && rm -f etc/hosts"'
deny G21 unsafe_rm 'bash <<< "rm -f /etc/hosts"'
deny G22 path_repointing_mkdir 'bash -c "mkdir -p l; rm -f l/x"'
deny G20 path_repointing_ln 'bash -c "ln -s / l; rm -f l/etc/hosts"'

printf '%s\n' '== S: controls stay silent =='
silent S1 '2>/dev/null ls'
silent S2 'ls 2>/dev/null'
silent S3 'echo hi >&2'
silent S4 'echo hi &> /dev/null'
silent S5 'make 2>&1 | tee log'
silent S6 '{ ls; }'
silent S7 'if true; then echo ok; fi'
silent S8 'diff <(ls) <(ls -a)'
silent S9 'rm -f ./build/x'
silent S10 'if [ -d build ]; then rm -rf ./build; fi'
silent S11 'bash -c "rm -f ./build/x"'
silent S12 '2>/dev/null git status'
silent S13 'echo x >| fresh-name.txt'
silent S14 'eval "rm -f ./build/x"'

if [ "$FAILURES" -eq 0 ]; then
  printf 'ALL COMPOUND TESTS PASSED\n'
  exit 0
fi
printf '%s TEST(S) FAILED\n' "$FAILURES"
exit 1
