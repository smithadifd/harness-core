#!/bin/bash
# The destructive-command guard's removal-word rule, through the real hook
# form. A command is judged only when its text holds a removal word (rm, or
# worktree and remove together); a removal word beside any code-carrying
# construct denies as removal_beside_<construct> without the guard reading
# inside it; a removal word with no carrier is judged as plain top-level
# commands. Row ids follow the round-5 design matrix:
#   W  the removal-word test (spellings, patterns, escapes, env -S)
#   C  the carriers, one row per removal_beside_ member, and their bounds
#   N  carriers with no removal word stay silent
#   X  commit and PR messages written by a heredoc
#   P  plain top-level judging after control openers and prefixes
#   K  known limits, silent by design
#   E  kept findings (repointing, prefixes, redirections before the command)
#   G  >&WORD read as a file target
#   B3 commands that crashed bash 3.2 on an empty array (run with /bin/bash)
# Each row runs the hook exactly as hooks.json does (env -u BASH_ENV -u ENV
# /bin/bash --noprofile --norc -p GUARD) with a PreToolUse JSON on stdin, from
# a worktree with a build folder. X is an absolute path outside the worktree.
# A result line names its row by id; the command is the row's in this file.
# Command strings are inert JSON data and never run.
set -u
LC_ALL=C

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUARD="${CARRIERS_TEST_GUARD:-$HERE/../hooks/destructive-command-guard.sh}"
JQ="$(command -v jq)" || { printf 'FAIL: jq is not on PATH\n'; exit 1; }
TEST_ROOT="$(mktemp -d "$(cd /tmp && pwd -P)/destructive-guard-carriers.XXXXXX")" || exit 1
WORKTREE="$TEST_ROOT/worktree"
X="$TEST_ROOT/outside/x"

FAILURES=0
pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; FAILURES=$((FAILURES + 1)); }

mkdir -p "$WORKTREE/build" "$TEST_ROOT/githome" "$TEST_ROOT/outside"
git_q() { HOME="$TEST_ROOT/githome" git -c user.name=t -c user.email=t@example.invalid "$@"; }
git_q init -q "$WORKTREE" || exit 1
printf 'x\n' >"$WORKTREE/f.txt"
printf 'x\n' >"$WORKTREE/build/x"
git_q -C "$WORKTREE" add f.txt && git_q -C "$WORKTREE" commit -qm seed || exit 1
printf 'existing\n' >"$TEST_ROOT/existing.txt"
# G2: a symlink to an existing file; G3: a symlink whose name is a descriptor.
ln -s "$TEST_ROOT/existing.txt" "$WORKTREE/lnk" || exit 1
ln -s "$TEST_ROOT/existing.txt" "$WORKTREE/2" || exit 1

hook() { # COMMAND: sets OUT and RC
  OUT="$("$JQ" -cn --arg cwd "$WORKTREE" --arg command "$1" \
    '{hook_event_name:"PreToolUse",tool_name:"Bash",tool_input:{command:$command},cwd:$cwd}' \
    | (cd "$WORKTREE" && HOME="$TEST_ROOT/githome" /usr/bin/env -u BASH_ENV -u ENV /bin/bash --noprofile --norc -p "$GUARD" 2>/dev/null))"
  RC=$?
}
REASON=""
deny() { # ID CODE COMMAND
  local decision
  hook "$3"
  decision="$(printf '%s' "$OUT" | "$JQ" -er '.hookSpecificOutput.permissionDecision' 2>/dev/null || printf none)"
  REASON="$(printf '%s' "$OUT" | "$JQ" -er '.hookSpecificOutput.permissionDecisionReason' 2>/dev/null || :)"
  case "$RC:$decision:$REASON" in
    "0:deny:Destructive command guard denied the command (reason=$2)"*) pass "$1 -> deny/$2" ;;
    *) fail "$1: expected deny/$2, got exit $RC, decision $decision, reason ${REASON:0:90}" ;;
  esac
}
silent() { # ID COMMAND
  local why
  hook "$2"
  if [ "$RC" -eq 0 ] && [ -z "$OUT" ]; then
    pass "$1 -> silent"
  else
    why="$(printf '%s' "$OUT" | "$JQ" -er '.hookSpecificOutput.permissionDecisionReason' 2>/dev/null || printf 'output %s' "${OUT:0:100}")"
    fail "$1: expected silence, got exit $RC and ${why:0:120}"
  fi
}
hint_names() { # ID TEXT: the last deny's text holds TEXT
  case "$REASON" in
    *"$2"*) pass "$1 the hint names: $2" ;;
    *) fail "$1 the hint does not name '$2': ${REASON:0:300}" ;;
  esac
}

printf '%s\n' '== W: the removal-word test =='
deny W1 unsafe_rm "/bin/rm -rf $X"
deny W2 unsafe_rm "\"rm\" -rf $X"
deny W3 unsupported_syntax "r''m -rf $X"
deny W4 unsupported_syntax "\\rm -rf $X"
deny W5 unsafe_rm "RM -rf $X"
deny W6 unsafe_rm $'r\\\nm -rf '"$X"
silent W7 'docker run --rm $(echo alpine) true'
silent W8 'rmdir "$(mktemp -d)"'
silent W9 'echo "$(date) form"'
silent W10 'git worktree list | xargs echo'
deny W11 unsafe_worktree_remove "git worktree remove --force $X"
deny W12 removal_beside_variable_command "a=rm; \$a -rf $X"
deny W13 removal_beside_variable_command "\$'\\x72m' -rf $X"
deny W14 removal_beside_variable_command "/bin/r? -rf $X"
deny W15 removal_beside_variable_command "r{m,} -rf $X"
silent W16 '"$PYTHON" scripts/rm_helper.py'
deny W17 removal_beside_variable_command "/bin/r[[:lower:]] -f $X"
deny W18 removal_beside_variable_command "/bin/r@(m) -f $X"
deny W19 removal_beside_variable_command './scripts/run?.sh'
deny W20 removal_beside_shell "bash -c '/bin/r[[:lower:]] -f $X'"
deny W21 removal_beside_shell "bash -c 'r{m,} -rf $X'"
deny W22 removal_beside_variable_command "\$'rm' -f $X"
deny W23 removal_beside_shell "sh -c 'git WORKTREE REMOVE --force $X'"
silent W24 '((n*2))'
deny W25 removal_beside_env_split "env -S'rm -rf $X'"
deny W26 removal_beside_env_split "env -S'rm -f build/x'"
deny W27 removal_beside_env_split "env --split-string='rm -f build/x'"
deny W28 removal_beside_variable_command "\${RM:-rm} -rf $X"
deny W29 removal_beside_variable_command "\${X:=rm} -rf $X"
deny W30 removal_beside_shell "bash -O extglob -c '/bin/r@(m|x) -f $X'"
deny W31 removal_beside_shell "zsh -c '/bin/r(m|x) -f $X'"
deny W35 removal_beside_shell "bash -O extglob -c '/bin/r@(m| x) -f $X'"
deny W36 removal_beside_shell "zsh -c '/bin/r(m| x) -f $X'"
deny W37 removal_beside_shell "zsh -c \"/bin/r(m|' ') -f $X\""
deny W38 removal_beside_shell "bash -c \"/bin/r[' 'm] -f $X\""
deny W39 removal_beside_shell "bash -c \"r{m,' '} -rf $X\""
deny W40 removal_beside_shell "bash -c '/bin/r[m -f $X'"
silent W41 "bash -c './go[ ]fast.sh'"
deny W42 removal_beside_env_split "env -S'rm\\_-rf\\_$X'"
deny W43 removal_beside_env_split "env -S 'rm\\_-rf\\_$X'"
deny W44 removal_beside_env_split "env -S 'rm\\t-rf $X'"
silent W45 "bash -c 'rpm* -qa'"
silent W46 "bash -c 'if [ -f form ]; then ls; fi'"
silent W47 "bash -c '{a,b c}x'"
silent W48 "git ls-files '*.py' | xargs grep -nE 'import[ ]+requests'"
silent W49 "sed -i 's/[ ]*from/ from/' \$(git ls-files '*.py')"
silent W50 'env CI=1 npm test --silent && echo "$(date)" a\ b'
deny W51 removal_beside_env_split "/usr/bin/env -S 'rm\\_-rf\\_$X'"
silent W52 "git ls-files '*.py' | xargs grep -c 'import[ ]+requests'"

printf '%s\n' '== C: a removal word beside a carrier =='
deny C1 removal_beside_substitution "\$(echo rm) -rf $X"
deny C2 removal_beside_substitution 'docker rm $(docker ps -aq)'
deny C3 removal_beside_backtick "\`echo rm\` -rf $X"
deny C4 removal_beside_process_substitution "source <(echo rm -rf $X)"
deny C5 removal_beside_heredoc $'bash <<EOF\nrm -rf '"$X"$'\nEOF'
deny C6 removal_beside_heredoc $'cat > clean.sh <<\'EOF\'\n#!/bin/sh\nrm -rf build\nEOF'
deny C7 removal_beside_heredoc $'python3 - <<\'EOF\'\nprint("rm -rf /")\nEOF'
deny C8 removal_beside_heredoc "bash 0<<<\"rm -r $X\""
deny C9 removal_beside_eval "eval \"rm -rf $X\""
deny C10 removal_beside_source '. ./env.sh && rm -rf build'
silent C11 'ls . && rm -rf build'
deny C12 removal_beside_trap "trap 'rm -rf $X' EXIT"
deny C13 removal_beside_trap "trap 'rm -f build/x' EXIT"
deny C14 removal_beside_alias "alias x='rm -rf $X'; x"
deny C15 removal_beside_xargs 'xargs rm -f < list.txt'
deny C16 removal_beside_find_exec "find . -name '*.pyc' -exec rm -f {} +"
deny C17 removal_beside_env_split "env -S 'rm -rf $X'"
deny C18 removal_beside_shell "bash -c 'rm -rf build'"
deny C19 removal_beside_shell "dash -c \"true &>/dev/null rm -r $X\""
deny C20 removal_beside_shell "fish -c 'rm -rf $X'"
deny C21 removal_beside_shell "watch rm -rf $X"
deny C22 removal_beside_shell "bash -c 'git worktree remove --force $X'"
deny C23 removal_beside_variable_command "\$SUDO rm -rf $X"
deny C24 removal_beside_variable_command '$SUDO rm -rf build'
deny C25 removal_beside_substitution $'git commit -m "$(cat <<EOF\nfix: rm cache\nEOF\n)"'
deny C26 removal_beside_shell "su -c 'rm -rf $X'"
deny C27 removal_beside_shell "script -q /dev/null rm -rf $X"
deny C28 removal_beside_shell "parallel rm -rf ::: $X"
deny C29 removal_beside_shell "BASH -c 'rm -rf build'"
deny C30 removal_beside_substitution "echo \$(git worktree remove --force $X)"
deny C31 removal_beside_eval "eval 'rm -f build/x'"
deny C32 removal_beside_env_split "env --spl='rm -rf $X'"
silent C33 'rm -rf build && env NODE_ENV=production npm run build --silent'
silent C34 "env | grep -E '^NODE_\\w+' && npm run build --silent"
deny C35 removal_beside_env_split "FOO=1 env -S 'rm -f /etc/hosts'"
deny C36 removal_beside_env_split "2>/dev/null env -S 'rm -f /etc/hosts'"
deny C37 removal_beside_env_split "(env -S 'rm -f /etc/hosts')"
deny C38 removal_beside_env_split "cd /tmp&&env -S 'rm -f /etc/hosts'"
deny C39 removal_beside_env_split "sudo -u root env -S 'rm -f /etc/hosts'"
deny C40 removal_beside_env_split "nice -n 5 env -S 'rm -f /etc/hosts'"
deny C41 removal_beside_env_split "sudo FOO=1 env -S 'rm -f /etc/hosts'"
deny C42 removal_beside_env_split "FOO=1 env --split-string='rm -f /etc/hosts'"
deny C43 removal_beside_env_split "timeout 30 env -S 'rm -f /etc/hosts'"
deny C44 removal_beside_git_foreach "git submodule foreach 'rm -rf node_modules'"
silent C45 "git submodule foreach 'git pull'"
deny C46 removal_beside_git_rebase_exec "git rebase -x 'rm -f /etc/hosts' HEAD~1"
silent C47 "git rebase -x 'npm test' HEAD~3"
deny C48 removal_beside_git_bisect_run 'git bisect run rm -f /etc/hosts'
silent C49 'git bisect run npm test'
deny C50 removal_beside_flock "flock /tmp/l -c 'rm -f /etc/hosts'"
silent C51 "flock /tmp/l -c 'make build'"

printf '%s\n' '== N: carriers with no removal word stay silent =='
silent N1 'echo "$(date)"'
silent N2 'echo `date`'
silent N3 'diff <(ls) <(ls -a)'
silent N4 $'cat > notes.md <<\'EOF\'\nhello\nEOF'
silent N5 "bash <<< 'echo hi'"
silent N6 'eval "$(ssh-agent -s)"'
silent N7 'source ./env.sh'
silent N8 "trap 'echo done' EXIT"
silent N9 "alias ll='ls -l'"
silent N10 "find . -name '*.o' | xargs wc -l"
silent N11 'find . -name x -exec grep -l foo {} +'
silent N12 "env -S 'echo hi'"
silent N13 "bash -c 'echo hi'"
silent N14 '"$PYTHON" x.py'
silent N15 '$(which python3) x.py'
silent N16 'echo $((1<<2))'

printf '%s\n' '== X: commit and PR messages written by a heredoc =='
deny X1 removal_beside_substitution $'git commit -m "$(cat <<\'EOF\'\nfix: rm the stale cache\nEOF\n)"'
hint_names X1 'write the message to a file and use git commit -F or gh --body-file'
deny X2 removal_beside_substitution $'gh pr create --title t --body "$(cat <<\'EOF\'\nreplaces rm -rf with a helper\nEOF\n)"'
deny X4 removal_beside_substitution $'git add -A && git commit -m "$(cat <<\'EOF\'\nfix: rm the stale cache\nEOF\n)"'
deny X5 removal_beside_substitution $'git add -A && git commit -m "$(cat <<\'EOF\'\nfix: rm cache\nEOF\n)"; rm -rf '"$X"
deny X6 removal_beside_substitution $'git add -A && git commit -m "$(cat <<\'EOF\'\nfix: rm cache\nEOF\n)"; rm -rf build'
deny X7 removal_beside_substitution $'git commit -m "$(cat <<\'EOF\'\nfix: rm cache\nEOF\n)" --author "$(id -un)"'
deny X13 removal_beside_substitution $'git commit -m "$(cat <<\'EOF\'\nfix; rm -rf '"$X"$'\nEOF\n)" | sh'
deny X14 removal_beside_substitution $'git commit -m "$(cat <<\'EOF\'\nrm -rf '"$X"$'\nEOF\n)" && git log -1 --format=%B | sh'
deny X16 removal_beside_substitution $'git commit -m "$(cat <<\'EOF\'\nrm -rf '"$X"$'\nEOF\n)" > >(sh)'
deny X17 removal_beside_substitution $'git commit -m "$(cat <<\'EOF\'\nfix: rm cache\nEOF\n)" | tee log'
silent X21 $'git commit -m "$(cat <<\'EOF\'\nfix: tidy the cache\nEOF\n)"'
deny X22 removal_beside_substitution $'git commit -m "$(cat <<\'EOF\'\nfix)"; rm -f '"$X"$'; echo "\nEOF\n)"'
deny X23 removal_beside_heredoc $'git commit -F - <<\'EOF\'\nfix: rm cache\nEOF'
hint_names X23 'write the message to a file and use git commit -F or gh --body-file'
hint_names X23 'write files that mention rm with the Write tool'

printf '%s\n' '== P: plain top-level judging =='
deny P1 unsafe_rm 'if true; then rm -f /etc/hosts; fi'
deny P2 unsafe_rm '{ rm -f /etc/hosts; }'
deny P3 unsafe_rm '(rm -f /etc/hosts)'
silent P4 'if [ -d build ]; then rm -rf ./build; fi'
deny P5 unsafe_rm 'setsid rm -f /etc/hosts'
deny P13 unsafe_rm 'stdbuf -o 0 rm -f /etc/hosts'
deny P14 unsafe_rm "ENV RM -f $X"
deny P15 unsafe_worktree_remove "GIT worktree remove --force $X"
deny P16 unsafe_rm "TIME rm -f $X"
deny P17 unsafe_rm "/usr/bin/env rm -rf $X"
deny P18 unsafe_rm "/usr/bin/nice rm -f $X"
deny P19 unsafe_rm "/usr/bin/time rm -rf $X"
deny P20 unsafe_rm "/usr/bin/sudo rm -rf $X"
deny P21 unsafe_rm "noglob rm -rf $X"
deny P22 unsafe_rm "nocorrect rm -rf $X"
deny P6 unsafe_rm "builtin command rm -rf $X"
deny P7 path_repointing_popd 'popd; rm -rf build'
silent P8 'git commit -m "rm the old file"'
silent P9 'ls | grep -v rm'
deny P10 unsafe_worktree_remove "(git worktree remove $X)"
deny P11 unsafe_worktree_remove "{ git worktree remove $X; }"
deny P12 unsafe_worktree_remove "! git worktree remove $X"

printf '%s\n' '== K: known limits stay silent =='
silent K1 '$CMD -rf /'
silent K2 '"$CMD" x'
silent K3 "chronic rm -rf $X"
silent K4 "r\$()m -rf $X"
silent K5 "perl -e 'system(\"rm -rf $X\")'"
silent K6 "git -c alias.x='!rm -rf $X' x"

printf '%s\n' '== E: kept findings =='
deny E1 path_repointing_cd 'if true; then cd /; fi; rm -f etc/hosts'
deny E2 unsupported_rm_prefix '{ sudo -X rm -f ./build/x; }'
deny E3 unsupported_rm_prefix '2>/dev/null sudo -X rm -f ./build/x'
deny E4 path_repointing_ln 'ln -s / l && 2>/dev/null rm -f l/etc/hosts'

printf '%s\n' '== G: >&WORD names a file unless WORD is a descriptor =='
deny G2 unsafe_redirect 'echo x >&lnk'
silent G3 'echo x >&2'

printf '%s\n' '== B3: no empty-array expansion aborts the engine (bash 3.2) =='
silent B3 '> out.txt'
silent B3 '>> log.txt'
silent B3 'echo hi; > out.txt'
silent B3 '2>/dev/null'
silent B3 'bash -c "> out.txt"'
silent B3 'echo $((1<<2))'
silent B3 'echo $((2>1))'
silent B3 'echo "$((1<<2))"'
silent B3 '()'

if [ -d "$WORKTREE/build" ] && [ -f "$WORKTREE/build/x" ]; then pass "fixtures remained present"; else fail "a fixture was executed"; fi

if [ "$FAILURES" -eq 0 ]; then
  printf 'ALL CARRIER TESTS PASSED\n'
  exit 0
fi
printf '%s TEST(S) FAILED\n' "$FAILURES"
exit 1
