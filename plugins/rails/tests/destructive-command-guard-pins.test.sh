#!/bin/bash
# Pins for guard behaviour that no other suite holds on its own, all run
# against the guard exactly as shipped (nothing here rewrites its constants):
#   1. the shipped TEMP_ROOTS value, in text and in behaviour;
#   2. the strict-descendant rules: the removal target, the scratch root, the
#      working directory of a worktree removal, and the working-directory check;
#   3. a removal word (rm, or git worktree remove) beside sh -c, bash -c,
#      eval, xargs, find -exec, a substitution, a here-string or env -S denies
#      as removal_beside_<construct>, behind any harmless prefix and whatever
#      its target, while the same wrappers around harmless work keep their
#      no_opinion verdict;
#   4. control-flow keywords around a forced removal are judged too.
# Every command string is inert JSON data and is never executed.
set -u
LC_ALL=C

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUARD="${PINS_TEST_GUARD:-$HERE/../hooks/destructive-command-guard.sh}"
JQ="$(command -v jq)" || { printf 'FAIL: jq is not on PATH\n'; exit 1; }

TMP_BASE="$(mktemp -d)" || exit 1
TMP="$(cd "$TMP_BASE" && pwd -P)"
PRIMARY="$TMP/primary"
OTHER="$TMP/other"
SCRATCH_BASE="$(mktemp -d "$(cd /tmp && pwd -P)/guard-pins-scratch.XXXXXX")" || exit 1
SCRATCH="$(cd "$SCRATCH_BASE" && pwd -P)/session-scratch"
FAILURES=0
pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; FAILURES=$((FAILURES + 1)); }

git_q() { HOME="$TMP/githome" git -c user.name=t -c user.email=t@example.invalid "$@"; }
mkdir -p "$TMP/githome" "$SCRATCH/cache"
for repo in "$PRIMARY" "$OTHER"; do
  git_q init -q --initial-branch=main "$repo" || exit 1
  printf 'x\n' >"$repo/f.txt"
  git_q -C "$repo" add f.txt && git_q -C "$repo" commit -qm seed || exit 1
done
mkdir -p "$PRIMARY/build" "$PRIMARY/sub/deeper" "$TMP/primary-evil/build" "$SCRATCH-evil/x"

new_wt() { git_q -C "$PRIMARY" worktree add -q --detach "$1" HEAD || { printf 'FAIL: cannot create %s\n' "$1"; exit 1; }; }

# run_case LABEL EXPECT(accepted|deny|no_opinion) CWD COMMAND [REASON] [-- ENV=VAL ...]
# EXPECT accepted means the engine's no_opinion/accepted verdict: the guard
# judged the command and accepted it (silent at the adapter; it never allows).
run_case() {
  local label="$1" expect="$2" cwd="$3" command_data="$4" want_reason=""
  shift 4
  if [ "$#" -gt 0 ] && [ "$1" != "--" ]; then want_reason="$1"; shift; fi
  if [ "$#" -gt 0 ]; then shift; fi
  local input output decision reason
  input="$($JQ -cn --arg cwd "$cwd" --arg command "$command_data" '{cwd:$cwd,command:$command}')"
  output="$(cd "$TMP" && printf '%s' "$input" | env -u LANE_ROOTS_REGISTRY HOME="$TMP/githome" ${1+"$@"} \
    /bin/bash --noprofile --norc -p "$GUARD" --policy --scratchpad "$SCRATCH" 2>/dev/null)"
  decision="$(printf '%s' "$output" | "$JQ" -er '.decision' 2>/dev/null || printf invalid)"
  reason="$(printf '%s' "$output" | "$JQ" -er '.reason' 2>/dev/null || printf invalid)"
  if [ "$expect" = accepted ]; then expect=no_opinion; want_reason=accepted; fi
  if [ "$decision" = "$expect" ] && { [ -z "$want_reason" ] || [ "$reason" = "$want_reason" ]; }; then
    pass "$label -> $decision/$reason"
  else
    fail "$label: expected $expect${want_reason:+/$want_reason}, got $decision/$reason"
  fi
}

printf '%s\n' '== the shipped TEMP_ROOTS value =='
declared="$(sed -n 's/^TEMP_ROOTS=(\(.*\))$/\1/p' "$GUARD")"
if [ "$declared" = "/tmp /private/tmp /var/folders /private/var/folders" ]; then
  pass "TEMP_ROOTS is exactly the four temp roots"
else
  fail "TEMP_ROOTS is '$declared', expected '/tmp /private/tmp /var/folders /private/var/folders'"
fi
if [ "$(grep -c '^TEMP_ROOTS=' "$GUARD")" -eq 1 ]; then
  pass "TEMP_ROOTS is declared exactly once"
else
  fail "TEMP_ROOTS is not declared exactly once"
fi

# A throwaway tree made by plain mktemp -d sits under the system temp root
# (/private/var/folders on macOS); one made under /private/tmp is the other
# canonical root. Both are accepted by the shipped value.
DEFAULT_ROOT_BASE="$(mktemp -d)" && DEFAULT_ROOT_BASE="$(cd "$DEFAULT_ROOT_BASE" && pwd -P)"
DEFAULT_ROOT_TREE="$DEFAULT_ROOT_BASE/wt"
new_wt "$DEFAULT_ROOT_TREE"
case "$DEFAULT_ROOT_TREE" in
  /tmp/*|/private/tmp/*|/var/folders/*|/private/var/folders/*) pass "the default mktemp root is one of the shipped roots" ;;
  *) fail "the default mktemp root ($DEFAULT_ROOT_TREE) is not under any shipped temp root" ;;
esac
run_case "worktree under the default mktemp root" accepted "$PRIMARY" "git worktree remove $DEFAULT_ROOT_TREE"
PRIV_TMP="$(mktemp -d "$(cd /tmp && pwd -P)/guard-pins.XXXXXX")" && PRIV_TMP="$(cd "$PRIV_TMP" && pwd -P)"
new_wt "$PRIV_TMP/wt"
run_case "worktree under /private/tmp" accepted "$PRIMARY" "git worktree remove $PRIV_TMP/wt"
run_case "worktree under /private/tmp, forced" deny "$PRIMARY" "git worktree remove --force $PRIV_TMP/wt" unsafe_worktree_remove
new_wt "$PRIV_TMP/wt-by-symlink"
ln -s "$PRIV_TMP/wt-by-symlink" "$PRIV_TMP/link-to-wt"
run_case "symlinked spelling of an eligible temp worktree is judged by its canonical path" accepted "$PRIMARY" "git worktree remove $PRIV_TMP/link-to-wt"

printf '%s\n' '== strict-descendant: rm targets and the scratch root =='
run_case "rm of a path strictly inside the worktree" accepted "$PRIMARY" "rm -rf $PRIMARY/build"
run_case "rm of the worktree root by absolute path" deny "$PRIMARY" "rm -rf $PRIMARY" unsafe_rm
run_case "rm of the worktree root with a trailing slash" deny "$PRIMARY" "rm -rf $PRIMARY/" unsafe_rm
run_case "rm of the worktree root from a subdirectory" deny "$PRIMARY/sub" "rm -rf $PRIMARY" unsafe_rm
run_case "rm of a sibling sharing the worktree's name prefix" deny "$PRIMARY" "rm -rf $TMP/primary-evil/build" unsafe_rm
run_case "rm of a path strictly inside the scratchpad" accepted "$PRIMARY" "rm -rf $SCRATCH/cache"
run_case "rm of the scratchpad root" deny "$PRIMARY" "rm -rf $SCRATCH" unsafe_rm
run_case "rm of the scratchpad root with a trailing slash" deny "$PRIMARY" "rm -rf $SCRATCH/" unsafe_rm
run_case "rm of a sibling sharing the scratchpad's name prefix" deny "$PRIMARY" "rm -rf $SCRATCH-evil/x" unsafe_rm

printf '%s\n' '== strict-descendant: the working directory of a worktree removal =='
new_wt "$TMP/rm-a"; new_wt "$TMP/rm-b"; new_wt "$TMP/rm-c"
mkdir -p "$TMP/rm-a/sub" "$TMP/rm-b/nested"
git_q init -q --initial-branch=main "$TMP/rm-b/nested"
run_case "removal from an unrelated directory" accepted "$OTHER" "git -C $PRIMARY worktree remove $TMP/rm-c"
run_case "cwd is the target" deny "$TMP/rm-a" "git worktree remove $TMP/rm-a" unsafe_worktree_remove
# An inherited repository selector denies before git runs.
new_wt "$PRIMARY/wt"
run_case "an inherited GIT_DIR and GIT_WORK_TREE deny a worktree removal" deny "$PRIMARY/wt" \
  "git worktree remove $PRIMARY/wt" inherited_git_environment -- GIT_DIR="$PRIMARY/.git" GIT_WORK_TREE="$PRIMARY"
# With no inherited selector, repository configuration can still make git
# report PRIMARY as the toplevel while the command runs in its linked
# worktree: the worktree's own core.worktree (extensions.worktreeConfig).
# Both cwd and toplevel matter: from the linked tree's own cwd the removal
# is denied though the toplevel differs; from PRIMARY it is accepted (the
# tree sits below a temp root and no registry names it).
git_q -C "$PRIMARY" config extensions.worktreeConfig true || exit 1
git_q -C "$PRIMARY/wt" config --worktree core.worktree "$PRIMARY" || exit 1
toplevel="$(HOME="$TMP/githome" git -C "$PRIMARY/wt" rev-parse --show-toplevel 2>/dev/null)"
if [ "$toplevel" = "$PRIMARY" ]; then
  pass "fixture: git reports PRIMARY as the toplevel from the linked tree"
else
  fail "fixture: git reports '$toplevel' as the toplevel from the linked tree, not PRIMARY"
fi
run_case "cwd equals target while git reports a different toplevel" deny "$PRIMARY/wt" \
  "git worktree remove $PRIMARY/wt" unsafe_worktree_remove
run_case "control: the same tree removed from PRIMARY keeps its verdict" accepted "$PRIMARY" \
  "git worktree remove $PRIMARY/wt"
git_q -C "$PRIMARY/wt" config --worktree --unset core.worktree || exit 1
git_q -C "$PRIMARY" config --unset extensions.worktreeConfig || exit 1
run_case "cwd is a plain subdirectory of the target" deny "$TMP/rm-a/sub" "git -C $PRIMARY worktree remove $TMP/rm-a" unsafe_worktree_remove
run_case "cwd is a nested repository inside the target" deny "$TMP/rm-b/nested" "git -C $PRIMARY worktree remove $TMP/rm-b" unsafe_worktree_remove
run_case "the primary tree is not removable from a linked one" deny "$TMP/rm-c" "git worktree remove $PRIMARY" unsafe_worktree_remove

printf '%s\n' '== strict-descendant: the working-directory check =='
mkdir -p "$TMP/not-a-repo"
ln -s "$PRIMARY" "$TMP/alias-primary"
run_case "relative cwd" deny "primary" "rm -rf ./build" invalid_cwd
run_case "cwd that is not canonical (symlinked)" deny "$TMP/alias-primary" "rm -rf ./build" invalid_cwd
run_case "cwd outside any repository, relative target" deny "$TMP/not-a-repo" "rm -rf ./build" cwd_outside_worktree
run_case "cwd inside a git directory" deny "$PRIMARY/.git" "rm -rf ./build" invalid_cwd
run_case "an inherited selector denies from a folder in no repository" deny "$TMP/not-a-repo" "rm -rf ./build" inherited_git_environment -- GIT_DIR="$PRIMARY/.git" GIT_WORK_TREE="$PRIMARY"
run_case "an inherited selector denies inside the tree it names" deny "$PRIMARY/sub" "rm -rf ./deeper" inherited_git_environment -- GIT_DIR="$PRIMARY/.git" GIT_WORK_TREE="$PRIMARY"

printf '%s\n' '== forced removal behind a shell wrapper and a harmless prefix is denied =='
WT_FORCED="$TMP/rm-c"
for prefix in 'echo hi; ' 'true && ' 'echo hi | '; do
  for payload in "git worktree remove --force $WT_FORCED" "git worktree remove -f $WT_FORCED" "rm -rf /"; do
    run_case "${prefix}sh -c '$payload'" deny "$PRIMARY" "${prefix}sh -c '$payload'" removal_beside_shell
    run_case "${prefix}bash -c '$payload'" deny "$PRIMARY" "${prefix}bash -c '$payload'" removal_beside_shell
    run_case "${prefix}eval '$payload'" deny "$PRIMARY" "${prefix}eval '$payload'" removal_beside_eval
    run_case "${prefix}xargs -I{} sh -c '$payload'" deny "$PRIMARY" "${prefix}xargs -I{} sh -c '$payload'" removal_beside_xargs
  done
done
run_case "double-quoted bash -c rm -rf behind a prefix" deny "$PRIMARY" "echo hi; bash -c \"rm -rf $SCRATCH/cache\"" removal_beside_shell
run_case "double-quoted eval rm -fr behind a prefix" deny "$PRIMARY" "true && eval \"rm -fr $SCRATCH/cache\"" removal_beside_eval
run_case "double-quoted bash -c dangerous rm -rf" deny "$PRIMARY" "echo hi; bash -c \"rm -rf /\"" removal_beside_shell
run_case "double-quoted eval dangerous rm -fr" deny "$PRIMARY" "true && eval \"rm -fr /\"" removal_beside_eval
run_case "forced removal after another command inside the payload" deny "$PRIMARY" "echo hi; sh -c 'echo ok; rm -rf $SCRATCH/cache'" removal_beside_shell
run_case "xargs sh -c without a replacement option" deny "$PRIMARY" "echo hi | xargs sh -c 'rm -rf $SCRATCH/cache'" removal_beside_xargs
run_case "xargs -n 1 bash -c" deny "$PRIMARY" "echo hi | xargs -n 1 bash -c 'git worktree remove --force $WT_FORCED'" removal_beside_xargs
run_case "xargs -0 sh -c" deny "$PRIMARY" "printf x | xargs -0 sh -c 'rm -rf $SCRATCH/cache'" removal_beside_xargs
run_case "xargs -0 sh -c dangerous rm -rf" deny "$PRIMARY" "printf x | xargs -0 sh -c 'rm -rf /'" removal_beside_xargs
run_case "sh -c rm -rf with no prefix" deny "$PRIMARY" "sh -c 'rm -rf $SCRATCH/cache'" removal_beside_shell
run_case "eval git worktree remove --force with no prefix" deny "$PRIMARY" "eval 'git worktree remove --force $WT_FORCED'" removal_beside_eval
run_case "env split string after harmless command" deny "$PRIMARY" "echo hi; env -S 'sh -c \"rm -rf /\"'" removal_beside_env_split
run_case "top-level cd reaches delegated relative target" deny "$PRIMARY" "cd /; sh -c 'rm -rf ./build'" removal_beside_shell

printf '%s\n' '== command-position inspection inside delegated shells =='
for runner in 'env' 'env FOO=1' 'nohup' 'command' 'timeout 5' 'time' 'nice -n 5' 'sudo' '/usr/bin/env'; do
  run_case "prefix $runner" deny "$PRIMARY" "$runner sh -c 'rm -rf /x/wt'" removal_beside_shell
  run_case "prefix after harmless command $runner" deny "$PRIMARY" "echo hi; $runner bash -c 'rm -rf /x/wt'" removal_beside_shell
done
for shell in zsh dash; do
  run_case "$shell -c" deny "$PRIMARY" "echo hi; $shell -c 'rm -rf /x/wt'" removal_beside_shell
done
for invocation in "sh -ec 'rm -rf /x/wt'" "bash -lc 'rm -rf /x/wt'" "sh -c -- 'rm -rf /x/wt'"; do
  run_case "shell flags $invocation" deny "$PRIMARY" "echo hi; $invocation" removal_beside_shell
done
for invocation in "sh -e -c 'rm -rf /'" "sh -x -c 'rm -rf /'" "bash -o errexit -c 'rm -rf /'" "zsh -f -c 'rm -rf /'" "bash --norc -c 'rm -rf /'" "bash --init-file /dev/null -c 'rm -rf /'"; do
  run_case "separate shell flags $invocation" deny "$PRIMARY" "echo hi; $invocation" removal_beside_shell
done
for invocation in "timeout --preserve-status 5" "timeout -k 1 5"; do
  run_case "timeout flags carrying worktree removal $invocation" deny "$PRIMARY" \
    "echo hi; $invocation sh -c 'git worktree remove --force $WT_FORCED'" removal_beside_shell
done
run_case "find exec shell" deny "$PRIMARY" "find . -exec sh -c 'rm -rf {}' \\;" removal_beside_find_exec
run_case "find exec bash" deny "$PRIMARY" "find . -name x -exec bash -c 'rm -rf \"\$1\"' _ {} \\;" removal_beside_find_exec
run_case "pipe echo into sh" deny "$PRIMARY" "echo 'rm -rf /x/wt' | sh" removal_beside_shell
run_case "pipe echo into bash" deny "$PRIMARY" "echo 'rm -rf /x/wt' | bash" removal_beside_shell
run_case "pipe printf into sh" deny "$PRIMARY" "printf 'git worktree remove --force /x/wt' | sh" removal_beside_shell
run_case "pipe several echo words into sh" deny "$PRIMARY" "echo rm -rf / | sh" removal_beside_shell
run_case "pipe printf arguments into sh" deny "$PRIMARY" "printf %s rm -rf / | sh" removal_beside_shell
# The removal word and the shell are read from the whole command text, so a
# shell anywhere in it denies beside an rm word anywhere in it.
run_case "echo and shell in separate commands" deny "$PRIMARY" "echo rm -rf /; sh | cat" removal_beside_shell
run_case "echo and sh -c in separate commands" deny "$PRIMARY" "echo rm -rf /; sh -c ls" removal_beside_shell
run_case "echo and shell in separate pipelines" no_opinion "$PRIMARY" "echo hi && sh | cat" not_explicit_operation
run_case "echo and shell split by or" deny "$PRIMARY" "echo rm -rf / || sh | cat" removal_beside_shell
run_case "echo and shell split by background" deny "$PRIMARY" "echo rm -rf / & sh | cat" removal_beside_shell
run_case "echo and shell split by newline" deny "$PRIMARY" $'echo rm -rf /\nsh | cat' removal_beside_shell
run_case "here string into bash" deny "$PRIMARY" "bash <<< 'rm -rf /x/wt'" removal_beside_heredoc
run_case "shell in dollar substitution" deny "$PRIMARY" "echo hi \$(sh -c 'rm -rf /x/wt')" removal_beside_substitution
run_case "shell in backtick substitution" deny "$PRIMARY" "echo hi \`sh -c 'rm -rf /x/wt'\`" removal_beside_backtick
run_case "direct removal in substitution" deny "$PRIMARY" 'echo $(rm -rf /x/wt)' removal_beside_substitution
run_case "substitution supplies shell code" deny "$PRIMARY" 'echo hi; sh -c "$(echo rm -rf /x/wt)"' removal_beside_substitution
run_case "nested shell" deny "$PRIMARY" "echo hi; sh -c \"sh -c 'rm -rf /x/wt'\"" removal_beside_shell
run_case "nested eval" deny "$PRIMARY" "echo hi; eval \"eval 'rm -rf /x/wt'\"" removal_beside_eval
run_case "changed cwd before relative rm" deny "$PRIMARY" "echo hi; sh -c 'cd /x && rm -rf wt'" removal_beside_shell
run_case "harmless command before rm" deny "$PRIMARY" "echo hi; sh -c 'echo ok && rm -rf /x/wt'" removal_beside_shell
run_case "harmless command before git remove" deny "$PRIMARY" "echo hi; sh -c 'echo ok; git worktree remove --force /x/wt'" removal_beside_shell
for invocation in "setsid sh -c 'rm -rf /'" "stdbuf -o0 sh -c 'rm -rf /'" "busybox sh -c 'rm -rf /'" "ksh -c 'rm -rf /'"; do
  run_case "runner $invocation" deny "$PRIMARY" "$invocation" removal_beside_shell
done
for invocation in "find . -execdir sh -c 'rm -rf /' \\;" "find . -ok sh -c 'rm -rf /' \\;"; do
  run_case "find runner $invocation" deny "$PRIMARY" "$invocation" removal_beside_find_exec
done
run_case "find execdir direct removal" deny "$PRIMARY" "find . -execdir rm -rf {} \\;" removal_beside_find_exec
run_case "find ok direct removal" deny "$PRIMARY" "find . -ok rm -rf {} \\;" removal_beside_find_exec
# There is no inspection depth any more: the first shell is the carrier.
deep='rm -rf /'
for ((nest=0; nest<7; nest++)); do deep="sh -c $(printf '%q' "$deep")"; done
run_case "seven nested shells hit the inspection bound" deny "$PRIMARY" "$deep" removal_beside_shell
deep='rm -rf /'
for ((nest=0; nest<6; nest++)); do deep="sh -c $(printf '%q' "$deep")"; done
run_case "backtick substitution consumes an inspection level" deny "$PRIMARY" "echo \`$deep\`" removal_beside_backtick
for invocation in "xargs -P4 -I{} sh -c 'rm -rf {}'" "xargs --max-args=1 sh -c 'rm -rf'" "xargs -I {} sh -c 'rm -rf {}'" "xargs -0 -n1 -- sh -c 'rm -rf'" "xargs env sh -c 'rm -rf'"; do
  run_case "xargs shell $invocation" deny "$PRIMARY" "echo hi; $invocation"
done
# A shell's command string is not read: an rm word in it denies, data or not.
run_case "rm words passed to echo are data" deny "$PRIMARY" "echo hi; sh -c 'echo rm -rf is dangerous'" removal_beside_shell
run_case "rm words passed to grep are data" deny "$PRIMARY" "bash -c 'grep -r rm foo'" removal_beside_shell
run_case "safe recursive removal in delegated shell" deny "$PRIMARY" "sh -c 'rm -rf $SCRATCH/cache'" removal_beside_shell
run_case "nonrecursive rm in delegated shell is judged like a top-level rm" deny "$PRIMARY" "echo hi; sh -c 'rm file.txt'" removal_beside_shell
run_case "nonforced worktree remove in delegated shell" deny "$PRIMARY" "echo hi; sh -c 'git worktree remove /x/wt'" removal_beside_shell
run_case "find exec harmless shell" no_opinion "$PRIMARY" "find . -exec sh -c 'echo {}' \\;" not_explicit_operation
run_case "empty pipe to shell" no_opinion "$PRIMARY" "echo hi | sh" not_explicit_operation
run_case "xargs harmless shell" no_opinion "$PRIMARY" "ls | xargs -n1 sh -c 'cat \$0'" not_explicit_operation
run_case "plain harmless command" no_opinion "$PRIMARY" "echo hi" not_explicit_operation

printf '%s\n' '== the same wrappers around harmless work keep their verdict =='
for prefix in '' 'echo hi; ' 'true && '; do
  run_case "${prefix}sh -c 'ls'" no_opinion "$PRIMARY" "${prefix}sh -c 'ls'" not_explicit_operation
  run_case "${prefix}bash -c 'echo done'" no_opinion "$PRIMARY" "${prefix}bash -c 'echo done'" not_explicit_operation
  run_case "${prefix}eval 'git status'" no_opinion "$PRIMARY" "${prefix}eval 'git status'" not_explicit_operation
  run_case "${prefix}xargs echo" no_opinion "$PRIMARY" "${prefix}xargs echo" not_explicit_operation
  run_case "${prefix}xargs -I{} sh -c 'echo {}'" no_opinion "$PRIMARY" "${prefix}xargs -I{} sh -c 'echo {}'" not_explicit_operation
done
run_case "sh -c with a non-forced worktree remove" deny "$PRIMARY" "echo hi; sh -c 'git worktree remove $WT_FORCED'" removal_beside_shell
run_case "sh -c with a non-recursive rm" deny "$PRIMARY" "echo hi; sh -c 'rm -f $SCRATCH/cache/x'" removal_beside_shell
for harmless in "env -S 'sh -c \"echo ok\"'" "setsid sh -c 'echo ok'" "stdbuf -o0 sh -c 'echo ok'" "busybox sh -c 'echo ok'" "ksh -c 'echo ok'" "find . -execdir echo ok \\;" "find . -ok echo ok \\;" "if true; then echo ok; fi" "time echo ok"; do
  run_case "harmless $harmless" no_opinion "$PRIMARY" "$harmless" not_explicit_operation
done
# The reserved word time is stripped with the control openers, so the rm
# after it is judged like a top-level rm, and this one is accepted.
run_case "time wrapper around eligible scratch removal" accepted "$PRIMARY" \
  "time rm -rf $SCRATCH/cache"

printf '%s\n' '== control words and grouping expose forced removals =='
REG_DIR="$TMP/registry"
REG="$REG_DIR/lane-roots.tsv"
mkdir -p "$REG_DIR"
printf 'path\trepo\tbranch\tcreated_at\n%s\t%s\t(detached)\t2026-09-30T00:00:00Z\n' \
  "$TMP/rm-a" "$PRIMARY" >"$REG"
chmod 700 "$REG_DIR"
chmod 600 "$REG"
run_case "forced payload after a registered removal is denied" deny "$PRIMARY" \
  "git worktree remove $TMP/rm-a; sh -c 'git worktree remove --force $WT_FORCED'" \
  removal_beside_shell -- LANE_ROOTS_REGISTRY="$REG"
for kw in 'if true; then ' 'while true; do ' 'until false; do ' 'for w in a; do ' 'if false; then :; else ' 'case x in x) '; do
  for forced in "git worktree remove --force $WT_FORCED" "git worktree remove -f $WT_FORCED"; do
    case "$kw" in
      'if true; then '|'if false; then :; else ') tail='; fi' ;;
      'case x in x) ') tail=';; esac' ;;
      *) tail='; done' ;;
    esac
    run_case "${kw}${forced}${tail}" deny "$PRIMARY" "${kw}${forced}${tail}" unsafe_worktree_remove
  done
done
for wrapped in "if true; then rm -rf /; fi" "while true; do rm -rf /; done" "(rm -rf /)" "{ rm -rf /; }" "time rm -rf /"; do
  run_case "control direct $wrapped" deny "$PRIMARY" "$wrapped" unsafe_rm
done
# Each item denies with the first carrier the guard finds in it.
for item in "shell|if true; then sh -c 'rm -rf /'; fi" "shell|(sh -c 'rm -rf /')" "shell|{ sh -c 'rm -rf /'; }" "shell|! sh -c 'rm -rf /'" "shell|time sh -c 'rm -rf /'" "shell|sh -c 'if true; then rm -rf /; fi'" "eval|eval 'if true; then rm -rf /; fi'" "xargs|xargs sh -c 'if true; then rm -rf /; fi'"; do
  wrapped="${item#*|}"
  run_case "control delegated $wrapped" deny "$PRIMARY" "$wrapped" "removal_beside_${item%%|*}"
done
run_case "eligible direct worktree removal stays accepted" accepted "$PRIMARY" "git worktree remove $TMP/rm-c"
run_case "forced direct worktree removal stays denied" deny "$PRIMARY" "git worktree remove --force $TMP/rm-c" unsafe_worktree_remove

if [ "$FAILURES" -ne 0 ]; then
  printf 'FAIL: %s pin(s) failed\n' "$FAILURES"
  exit 1
fi
printf 'ALL GUARD PINS PASSED\n'
