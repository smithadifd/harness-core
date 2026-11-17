#!/bin/bash
# The destructive-command guard through the real hook form, for commands
# whose command word is not the segment's first word:
#   R  a redirection before (or glued to) the command word, in every place a
#      check finds that word: the rm scan, the worktree-removal patterns, the
#      repointing classes and the command splitter (`>&N` and `&>FILE` are not
#      separators);
#   G  an rm, recursive or not, after a control word or grouping opener, in a
#      case arm or a function body, judged the way a top-level rm is; an rm
#      beside a shell, a substitution, a process substitution, a here-string
#      or xargs denies as removal_beside_<construct> without the guard
#      reading inside it;
#   O  a command word glued with no blank to a control opener: a case arm's
#      ")" (x), (x), a|x), x ), a multi-line arm, an arm after ;&) and the
#      "))" of an arithmetic for or while (followed by do or {), read as the
#      command word it is, for rm, git worktree remove and the repointing
#      and source rows; the glued "(" forms (then(, do(, else(, {(,
#      if((...))then) are pinned too: they deny through the pattern
#      presumption for a command word that holds a "(";
#   S  controls: ordinary commands with redirections and compound commands
#      stay silent, and an accepted rm in a compound stays silent; an rm
#      inside bash -c or eval denies even when its target is safe.
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
deny R13 removal_beside_shell 'bash -c "2>/dev/null rm -f /etc/hosts"'
deny R14 removal_beside_shell '2>/dev/null bash -c "rm -f /etc/hosts"'
deny R15 unsafe_redirect 'echo x &> lnk'
deny R16 unsafe_redirect 'echo x >| lnk'

printf '%s\n' '== G: every rm the grammar reaches, recursive or not =='
deny G1 unsafe_rm '{ rm -f /etc/hosts; }'
deny G2 unsafe_rm '(rm -f /etc/hosts)'
deny G3 unsafe_rm 'if true; then rm -f /etc/hosts; fi'
deny G4 unsafe_rm 'for f in a; do rm -f /etc/hosts; done'
deny G5 removal_beside_shell 'bash -c "rm -f /etc/hosts"'
deny G6 unsafe_rm 'if rm -f /etc/hosts; then :; fi'
deny G7 unsafe_rm 'while rm -f /etc/hosts; do break; done'
deny G8 unsafe_rm 'until rm -f /etc/hosts; do break; done'
deny G9 unsafe_rm 'if false; then :; else rm -f /etc/hosts; fi'
deny G10 unsafe_rm 'case x in a) :;; b) rm -f /etc/hosts;; esac'
deny G11 unsafe_rm 'f() { rm -f /etc/hosts; }; f'
deny G12 removal_beside_process_substitution 'cat <(rm -f /etc/hosts)'
deny G13 unsafe_rm 'time -p rm -f /etc/hosts'
deny G14 unsafe_rm 'setsid rm -f /etc/hosts'
deny G15 removal_beside_substitution 'echo "$(rm -f /etc/hosts)"'
deny G16 path_repointing_ln 'ln -s / l && rm -f l/etc/hosts'
deny G17 removal_beside_xargs 'echo /etc/hosts | xargs rm -f'
deny G18 unsupported_rm_prefix 'sudo -X rm -f /etc/hosts'
deny G19 removal_beside_shell 'bash -c "cd / && rm -f etc/hosts"'
deny G21 removal_beside_heredoc 'bash <<< "rm -f /etc/hosts"'
deny G22 removal_beside_shell 'bash -c "mkdir -p l; rm -f l/x"'
deny G20 removal_beside_shell 'bash -c "ln -s / l; rm -f l/etc/hosts"'
# A removal beside a shell or eval denies whatever its target: the guard does
# not read inside them.
deny S11 removal_beside_shell 'bash -c "rm -f ./build/x"'
deny S14 removal_beside_eval 'eval "rm -f ./build/x"'

printf '%s\n' '== O: a command word glued to a control opener =='
deny O1 unsafe_rm 'case x in x)rm -rf /etc/hosts;; esac'
deny O2 unsafe_worktree_remove 'case x in x)git worktree remove --force /etc;; esac'
deny O3 unsafe_rm 'case x in (x)rm -rf /etc/hosts;; esac'
deny O4 unsafe_worktree_remove 'case x in (x)git worktree remove --force /etc;; esac'
deny O5 unsafe_rm 'case x in a|x)rm -rf /etc/hosts;; esac'
deny O6 unsafe_worktree_remove 'case x in a|x)git worktree remove --force /etc;; esac'
deny O7 unsafe_rm $'case x in\n  x)rm -rf /etc/hosts ;;\nesac'
deny O8 unsafe_worktree_remove $'case x in\n  x)git worktree remove --force /etc ;;\nesac'
deny O9 unsafe_rm 'case x in x )rm -rf /etc/hosts;; esac'
deny O10 unsafe_worktree_remove 'case x in x )git worktree remove --force /etc;; esac'
# ;& is bash 4 and later; bash 3.2 refuses it, so it runs only there.
deny O11 unsafe_rm 'case x in x) :;&y)rm -rf /etc/hosts;; esac'
deny O12 unsafe_worktree_remove 'case x in x) :;&y)git worktree remove --force /etc;; esac'
deny O13 unsafe_rm 'for ((i=0;i<1;i++))do rm -rf /etc/hosts; done'
deny O14 unsafe_worktree_remove 'for ((i=0;i<1;i++))do git worktree remove --force /etc; done'
# while ((...))do parses in bash 5; bash 3.2 refuses the glued do.
deny O15 unsafe_rm 'while ((i++<1))do rm -rf /etc/hosts; done'
deny O16 unsafe_worktree_remove 'while ((i++<1))do git worktree remove --force /etc; done'
deny O17 unsafe_rm 'for ((i=0;i<1;i++)){ rm -rf /etc/hosts; }'
deny O18 unsafe_worktree_remove 'for ((i=0;i<1;i++)){ git worktree remove --force /etc; }'
deny O19 unsafe_rm $'case x in x)\trm -rf /etc/hosts;; esac'
deny O20 unsafe_rm 'case "$1" in clean)rm -rf /etc/hosts;; esac'
deny O21 unsafe_rm 'case x in x)rm -rf "$HOME";; esac'
deny O22 unsafe_rm 'case x in x)RM -rf /etc/hosts;; esac'
deny O23 unsafe_worktree_remove 'case x in x)git worktree remove --force ../other;; esac'
deny O24 path_repointing_cd 'case x in x)cd /;; esac; rm -rf build'
deny O25 path_repointing_pushd 'case x in x)pushd /;; esac; rm -rf build'
deny O26 path_repointing_ln 'case x in x)ln -s / build/l;; esac; rm -rf build/l/etc'
deny O27 removal_beside_source 'case x in x). ./scripts/setup.sh;; esac; rm -rf build'
# Glued "(" forms: bash runs each; the command word holds a "(", so the
# pattern presumption denies it.
deny O28 removal_beside_variable_command 'if true; then(rm -rf /etc/hosts); fi'
deny O29 removal_beside_variable_command 'if true; then(git worktree remove --force /etc); fi'
deny O30 removal_beside_variable_command 'for i in 1; do(rm -rf /etc/hosts); done'
deny O31 removal_beside_variable_command 'for i in 1; do(git worktree remove --force /etc); done'
deny O32 removal_beside_variable_command 'if false; then :; else(rm -rf /etc/hosts); fi'
deny O33 removal_beside_variable_command 'if false; then :; else(git worktree remove --force /etc); fi'
deny O34 removal_beside_variable_command '{(rm -rf /etc/hosts); }'
deny O35 removal_beside_variable_command '{(git worktree remove --force /etc); }'
# if((...))then parses in bash 5; bash 3.2 refuses the glued then.
deny O36 removal_beside_variable_command 'if((1))then rm -rf /etc/hosts; fi'
deny O37 removal_beside_variable_command 'if((1))then git worktree remove --force /etc; fi'
# The same glued forms with no removal word and no repointing stay silent,
# and an accepted rm in a glued arm is silent as it is after a blank.
silent O38 'case x in x)echo hi;; esac'
silent O39 'case x in (x)echo hi;; esac'
silent O40 'case x in a|x)echo hi;; esac'
silent O41 $'case x in\n  x)echo hi ;;\nesac'
silent O42 'case x in x )echo hi;; esac'
silent O43 'case x in x) :;&y)echo hi;; esac'
silent O44 'for ((i=0;i<1;i++))do echo hi; done'
silent O45 'while ((i++<1))do echo hi; done'
silent O46 'for ((i=0;i<1;i++)){ echo hi; }'
silent O47 'case x in x)rm -rf build;; esac'

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
silent S12 '2>/dev/null git status'
silent S13 'echo x >| fresh-name.txt'

if [ "$FAILURES" -eq 0 ]; then
  printf 'ALL COMPOUND TESTS PASSED\n'
  exit 0
fi
printf '%s TEST(S) FAILED\n' "$FAILURES"
exit 1
