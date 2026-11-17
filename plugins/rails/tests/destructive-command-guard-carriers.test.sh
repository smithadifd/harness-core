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
#   H  shell comments: the segmenter reads # as text, so a quote or a
#      trailing backslash in a comment can swallow a separator, which rule G
#      denies beside a removal word; R1 and R2 also read a comment-skipping
#      copy, so a comment hides no removal word and no carrier from them
#   L  line ends: a newline after an even run of backslashes ends the command
#      (every backslash in the run is escaped); after an odd run it is a line
#      continuation, which rule G denies beside a removal word
#   S  rule G: a removal word beside a ; & | or newline the segmenter consumed
#      other than as a delimiter (inside quotes, after a backslash, as a line
#      continuation) denies as hidden_separator; redirection operators are not
#      separators
#   U  R1 and R2 read both segmenters: text the comment-skipping copy drops
#      hides no command word and no top-level reading
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

printf '%s\n' '== H: shell comments =='
# H0a and H0b are the top-level codes. The segmenter reads # as text, so a
# comment row keeps one only where the comment hides no separator.
deny H0a unsafe_rm 'rm -rf /etc/hosts'
deny H0b unsafe_worktree_remove $'cd build\ngit worktree remove --force /etc'
# A quote or a trailing backslash inside a comment is read as text: the
# newline it swallows is a hidden separator (rule G), or a quote that never
# closes leaves words that cannot be read (unsupported_syntax).
deny H1 hidden_separator $'# Clean the stale cache (it\'s regenerated)\nrm -rf /etc/hosts\n# Don\'t forget to rebuild'
deny H2 hidden_separator $'# a "quoted\nrm -rf /etc/hosts\n# word"'
deny H3 hidden_separator $'# see C:\\dir\\\nrm -rf /etc/hosts'
deny H4 hidden_separator $'cd build # it\'s here\ngit worktree remove --force /etc\n# that\'s it'
deny H5 hidden_separator $'echo start # it\'s time\nrm -rf /etc/hosts\necho done # that\'s all'
deny H6 hidden_separator $'true; # it\'s\nrm -rf /etc/hosts; # ok\''
deny H7 unsupported_syntax $'echo hi |# it\'s\nrm -rf /etc/hosts'
deny H36 unsupported_syntax $'true\n# it\'s regenerated\nrm -rf /etc/hosts'
deny H37 unsupported_syntax $'true;# it\'s\nrm -rf /etc/hosts'
# A comment with no quote is a word list no validator accepts. An unbalanced
# quote in one denies beside a removal word; H19 holds none, but its
# unbalanced $( stops the segmenter, which denies.
silent H8 '# rm -rf /etc/hosts is not needed here'
deny H9 unsupported_syntax $'# don\'t rm the cache'
silent H15 "x=1 # it's fine"
deny H17 unsupported_syntax $'# it\'s stale\nrm -rf build'
deny H19 unsupported_syntax $'echo x # $(\nls'
deny H18 unsupported_syntax "rm -rf build # it's stale"
# A # inside a word, after an escape or inside quotes is text.
deny H10 unsafe_rm 'echo a#b; rm -rf /etc/hosts'
deny H11 unsafe_rm "echo '#'; rm -rf /etc/hosts"
deny H12 unsafe_rm 'echo \#; rm -rf /etc/hosts'
deny H13 unsafe_rm 'echo ${#x}; rm -rf /etc/hosts'
deny H14 unsafe_rm 'echo $#; rm -rf /etc/hosts'
# R2 reads the raw text, comments included: a carrier in a comment counts.
deny H16 removal_beside_substitution $'# see $(date) for timing\nrm -rf /etc/hosts'
# Bash reads a # after a blank as text inside these constructs, and so does
# the segmenter. H24's accepted rm is silent, as it is alone (H24t).
deny H20 path_repointing_subshell '(( 1 # x )); rm -rf /etc/hosts'
deny H21 unsafe_rm 'for (( i=0; i<0; # x )); do :; done; rm -rf /etc/hosts'
deny H22 path_repointing_subshell $'shopt -s extglob\necho @(a|(b) # x); rm -rf /etc/hosts'
deny H23 unsafe_rm 'echo ${HOME// #/Z}; rm -rf /etc/hosts'
silent H24 'echo ${HOME// #/Z}; rm -rf build'
deny H25 unsafe_rm '[[ a =~ ( #) ]]; rm -rf /etc/hosts'
deny H26 unsafe_rm 'echo $[ 1 # x ]; rm -rf /etc/hosts'
deny H38 unsafe_rm $'shopt -s extglob\necho @(a)#x; rm -rf /etc/hosts'
deny H39 path_repointing_assignment 'x=(a)#b; rm -rf /etc/hosts'
# A comment that ends in a backslash joins no line, at the top level, in a
# quoted string for another shell and in a heredoc body. The segmenter reads
# a line continuation there, so H31 denies hidden_separator.
deny H27 removal_beside_eval $'# x\\\neval rm -rf /etc/hosts'
deny H28 removal_beside_shell $'# x\\\nsh -c \'rm -rf /etc/hosts\''
deny H29 removal_beside_shell $'sh -c \'true # x\\\nrm -rf /etc/hosts\''
deny H30 removal_beside_heredoc $'bash <<\'EOF\'\ntrue # x\\\nrm -rf /etc/hosts\nEOF'
deny H31 hidden_separator $'# c\\\nr\\\nm -rf /etc/hosts'
# A quote or $' inside a comment pairs with nothing after it.
deny H32 removal_beside_env_split $'# it\'s\nenv -S \'rm -rf /etc/hosts\''
deny H33 removal_beside_env_split $'# it\'s\nenv -S\'rm\\_-rf\\_/etc/hosts\''
deny H34 removal_beside_variable_command $'# $\'\n$\'\\x72\\x6d\' -rf /etc/hosts'
deny H35 removal_beside_shell $'bash -c "# $\'\n$\'\\x72\\x6d\' -rf /etc/hosts"'
# A # glued to a closer or an operator: bash starts a comment there ((( 1 ))#
# and f()# do; @(a)#x is one word). The segmenter reads the # as text, so the
# quote after it swallows the next line: a hidden separator (rule G), or a
# quote that never closes (unsupported_syntax). Each row's next line runs
# the removal under bash.
deny H40 hidden_separator $'(( n++ ))# don\'t care\nrm -rf /etc/hosts\n# that\'s it'
deny H41 hidden_separator $'cleanup()# it\'s here\n{ :; }\nrm -rf /etc/hosts\n# that\'s it'
deny H42 hidden_separator $'(( 1 ))#\'\ngit worktree remove --force /etc\necho \''
deny H43 hidden_separator $'for ((i=0;i<1;i++))#\'\ndo rm -rf /etc/hosts; done\necho \''
deny H44 hidden_separator $'(( 1 ))#"\nrm -rf /etc/hosts\necho "'
deny H45 hidden_separator $'(( 1 ))#\\\nrm -rf /etc/hosts'
# The spaced form's quote never closes (unsupported_syntax). @(a)#x and
# x=(a)#b keep their verdicts with no removal word; beside an accepted rm,
# H49 is silent, as the rm is alone (H49t), and H50 keeps its code.
deny H46 unsupported_syntax $'(( n++ )) # don\'t care\nrm -rf /etc/hosts'
silent H47 $'shopt -s extglob\necho @(a)#x'
silent H48 'x=(a)#b'
silent H49 $'shopt -s extglob\necho @(a)#x; rm -rf build'
deny H50 path_repointing_assignment 'x=(a)#b; rm -rf build'
# One row per closer kind and per operator family, the next line a plain rm.
deny H51 hidden_separator $'(true)#\'\nrm -rf /etc/hosts\necho \''
deny H52 hidden_separator $'(( 1 ))#\'\nrm -rf /etc/hosts\necho \''
deny H53 hidden_separator $'f()#\'\nrm -rf /etc/hosts\necho \''
deny H54 hidden_separator $'f(){ :; }#\'\nrm -rf /etc/hosts\necho \''
deny H55 hidden_separator $'echo ${HOME}#\'\nrm -rf /etc/hosts\necho \''
deny H56 hidden_separator $'[[ 1 ]]#\'\nrm -rf /etc/hosts\necho \''
deny H57 hidden_separator $'echo \'a\'#\'\nrm -rf /etc/hosts\necho \''
deny H58 hidden_separator $'echo "a"#\'\nrm -rf /etc/hosts\necho \''
deny H59 hidden_separator $'true;#\'\nrm -rf /etc/hosts\necho \''
deny H60 hidden_separator $'true&#\'\nrm -rf /etc/hosts\necho \''
deny H61 hidden_separator $'true&&#\'\nrm -rf /etc/hosts\necho \''
deny H62 hidden_separator $'true|#\'\nrm -rf /etc/hosts\necho \''
deny H63 hidden_separator $'true||#\'\nrm -rf /etc/hosts\necho \''
deny H64 hidden_separator $'echo >#\'\nrm -rf /etc/hosts\necho \''
deny H65 hidden_separator $'cat <#\'\nrm -rf /etc/hosts\necho \''
deny H66 hidden_separator $'(#\'\nrm -rf /etc/hosts\necho \')'
# A line continuation between the closer and the #.
deny H67 hidden_separator $'(( 1 ))\\\n#\'\nrm -rf /etc/hosts\necho \''
# A glued # beside an accepted rm: silent, as the rm is alone (H68t, H69t).
silent H68 'rm -rf build;# done'
silent H69 $'echo \'a\'#b; rm -rf build'
# With no removal word the verdicts stay. With one, a quote after a # that
# swallows a newline is a hidden separator (H72, H73), though bash runs no
# rm there.
silent H70 $'(( n++ ))# don\'t care\necho ok\n# that\'s it'
silent H71 $'true;# it\'s fine\necho ok'
deny H72 hidden_separator $'echo a#\'\nrm -rf /etc/hosts\necho \''
deny H73 hidden_separator $'echo \\;#\'\nrm -rf /etc/hosts\necho \''
# The four relaxations (H24, H49, H68, H69) with /etc/hosts as the target
# still deny.
deny H24t unsafe_rm 'echo ${HOME// #/Z}; rm -rf /etc/hosts'
deny H49t unsafe_rm $'shopt -s extglob\necho @(a)#x; rm -rf /etc/hosts'
deny H68t unsafe_rm 'rm -rf /etc/hosts;# done'
deny H69t unsafe_rm $'echo \'a\'#b; rm -rf /etc/hosts'
# R1 (f) and R2 read their command words from the comment-skipping copy too,
# so a quote or a backslash in a comment hides no pattern command word.
deny H74 removal_beside_variable_command $'# it\'s\n/bin/s? -c \'f=r; f=${f}m; $f -rf /etc/hosts\''
deny H75 removal_beside_variable_command $'# it\'s\n/bin/s? -c x'
deny H76 removal_beside_variable_command $'# x\\\n/bin/s? -c x'
deny H77 removal_beside_variable_command $'true # x\\\n/bin/s? -c x'
deny H78 removal_beside_variable_command $'echo hi # it\'s\n/bin/s? -c x'

printf '%s\n' '== L: line ends after a run of backslashes =='
# An even run: every backslash is escaped, so the newline ends the command and
# the next line runs.
deny L1 unsafe_rm $'echo a\\\\\nrm -rf /etc/hosts'
deny L2 unsafe_rm $'echo a\\\\\\\\\nrm -rf /etc/hosts'
deny L3 unsafe_rm $'echo a\\\\\\\\\\\\\nrm -rf /etc/hosts'
deny L4 unsafe_worktree_remove $'echo a\\\\\ngit worktree remove --force /etc'
deny L5 unsafe_worktree_remove $'echo a\\\\\\\\\ngit worktree remove --force /etc'
deny L6 unsafe_worktree_remove $'echo a\\\\\\\\\\\\\ngit worktree remove --force /etc'
deny L7 unsafe_rm $'printf "%s" a\\\\\nrm -rf /etc/hosts'
# An odd run is a line continuation: echo arm -rf /etc/hosts runs echo.
# Beside a removal word a continuation denies (rule G).
deny L8 hidden_separator $'echo a\\\nrm -rf /etc/hosts'
deny L9 hidden_separator $'echo a\\\\\\\nrm -rf /etc/hosts'
deny L10 hidden_separator $'echo a\\\ngit worktree remove --force /etc'
# An even run inside single quotes is text for echo (a newline inside quotes
# beside a removal word denies, rule G), and code for sh -c; in a heredoc
# body it is code for bash.
deny L11 hidden_separator $'echo \'a\\\\\nrm -rf /etc/hosts\''
deny L12 removal_beside_shell $'sh -c \'echo a\\\\\nrm -rf /etc/hosts\''
deny L13 removal_beside_heredoc $'bash <<\'EOF\'\necho a\\\\\nrm -rf /etc/hosts\nEOF'
# CRLF line ends are refused before any reading.
deny L14 adapter_input_invalid $'echo a\\\\\r\nrm -rf /etc/hosts'
# A comment line ending in an odd run joins no line in bash; the segmenter
# reads a line continuation there (rule G).
deny L15 hidden_separator $'# x\\\\\\\nrm -rf /etc/hosts'
# A heredoc's terminator line that ends in a backslash ends the heredoc (bash
# joins no line there) and the next line runs; R1's join reads that backslash
# as a continuation, so the top-level reading is what finds the rm.
deny L16 removal_beside_heredoc $'cat <<\'E\\\'\nhello\nE\\\nrm -rf /etc/hosts'
deny L17 removal_beside_heredoc $'cat <<E\\\\\nhello\nE\\\nrm -rf /etc/hosts'
# An even run, then a carrier word split by a real continuation: bash ends
# echo at the newline and runs the carrier. Only R1's even-run test keeps
# that newline; a join that deleted it would glue the carrier word to echo's
# word, and the command would deny hidden_separator instead.
deny L18 removal_beside_eval $'echo a\\\\\nev\\\nal rm -rf /etc/hosts'
deny L19 removal_beside_source $'echo a\\\\\nsou\\\nrce ./x.sh; rm -rf build'
deny L20 removal_beside_trap $'echo a\\\\\ntr\\\nap \'rm -rf /etc/hosts\' EXIT'

printf '%s\n' '== S: rule G, a separator the segmenter did not split on =='
# Bypass rows: a quote read differently from bash (a " inside a double-quoted
# ${...}) swallowed the separator before a removal, and bash runs it. The
# [[ ]] rows are judged now that # is text, as bash reads it there.
deny S1 hidden_separator $'echo "${x//\'"\'/}"; rm -rf '"$X"
deny S2 hidden_separator $'name="${raw//\'"\'/}"\nrm -rf '"$X"$'\necho \'done\''
deny S3 hidden_separator $'echo "${x//\'"\'/}" && rm -rf '"$X"
deny S4 hidden_separator $'echo "${x//\'"\'/}"\ngit worktree remove --force ../sibling\necho \'ok\''
deny S5 hidden_separator $'echo "${x//\'"\'/}"; rm -rf '"$X"$'; echo "${y//\'"\'/}"'
deny S6 unsafe_rm "[[ a]] =~ ( #) ]]; rm -rf $X"
deny S7 unsafe_worktree_remove '[[ a]] =~ ( #) ]]; git worktree remove --force ../sibling'
# A hidden cd: bash runs the cd, so the relative rm names another folder.
deny S8 path_repointing_cd $'[[ a]] =~ ( #) ]]; cd /\nrm -rf build'
deny S9 hidden_separator $'echo "${x//\'"\'/}"; cd /; echo "${y//\'"\'/}"\nrm -rf build'
# Earlier findings: a comment's quote or trailing backslash, an even run of
# backslashes, a # glued to a closer.
deny S10 unsupported_syntax $'echo hi # it\'s\nrm -rf '"$X"$'\necho \'done\''
deny S11 hidden_separator $'echo hi # note \\\nrm -rf '"$X"
deny S12 unsafe_rm $'echo a\\\\\nrm -rf '"$X"
deny S13 unsupported_syntax $'(( 1 ))# it\'s\nrm -rf '"$X"
# One row per state that flags a separator, each before an accepted rm.
deny S14 hidden_separator $'echo \'a; b\'\nrm -rf build'
hint_names S14 'run the removal as its own one-line command'
deny S15 hidden_separator $'echo "a; b"\nrm -rf build'
deny S16 hidden_separator $'echo $\'a; b\'\nrm -rf build'
deny S17 hidden_separator $'echo a\\; b\nrm -rf build'
deny S18 hidden_separator $'rm -rf \\\nbuild'
deny S19 hidden_separator $'echo "a\\;b"\nrm -rf build'
deny S20 hidden_separator $'echo $\'a\\;b\'\nrm -rf build'
# Silent as before: no separator is hidden, and the redirection operators
# >&, &>, >| and <& are not separators.
silent S21 'rm -rf build && npm run build 2>&1 | tail -20'
silent S22 'for d in a b; do rm -rf build; done'
silent S23 'git rm f.txt'
silent S24 'docker rm c1'
silent S25 'rm -rf build && git commit -m "chore: clean"'
silent S26 'git commit -m "fix: rm stale cache"'
silent S27 "grep -rn 'rm -rf' scripts/"
silent S28 'cat <&3 f; rm -rf build'
silent S29 'rm -rf build; echo x >&2'
silent S30 'rm -rf build; echo x &>/dev/null'
silent S31 'rm -rf build; echo x >|out.log'
# <| is not bash; the command denies as before.
deny S32 unsupported_syntax 'cat <| f; rm -rf build'
# Intended denials (KNOWN-LIMITS): a removal word beside a quoted or escaped
# separator, a multi-line quoted string, or a comment holding a quote.
deny S33 hidden_separator 'rm -rf build; echo "done; ok"'
deny S34 hidden_separator "jq '.a | .b' f.json > out.json && rm -rf build"
deny S35 hidden_separator 'curl -s "https://example.invalid/x?a=1&b=2" -o out && rm -rf build'
deny S36 hidden_separator 'git commit -m "one" -m "rm the cache; add tests"'
deny S37 hidden_separator "grep -nE 'unlink|rm' src/x.sh"
deny S38 hidden_separator 'git log --oneline | grep -iE "worktree|remove"'
deny S39 hidden_separator 'ssh host "cd x; rm -rf y"'
deny S40 hidden_separator $'git commit -m "fix: tidy\n\nrm the old file"'
deny S41 unsupported_syntax $'# don\'t keep it\nrm -rf build'
deny S42 hidden_separator $'git rm -r --cached dist && git commit -m "chore: untrack dist\n\nBody text"'
deny S43 hidden_separator $'npm rm lodash && git commit -m "chore: drop lodash\n\nBody text"'
deny S44 hidden_separator 'docker rm c1 && echo "a;b"'
# R2 also takes its tokens from the text with every backslash deleted, so a
# carrier after an even run of backslashes is found without the line join.
deny S45 removal_beside_eval $'echo a\\\\\neval rm -rf build'

printf '%s\n' '== U: R1 and R2 read both segmenters =='
# Every reader that decides a verdict reads the segmenter's segments (where #
# is text) beside the comment-skipping copy's, so text the copy drops as a
# comment, here a # that bash reads as text inside [[ ]], hides nothing bash
# runs. The rule G rows for a hidden cd are S8 and S9.
# collect_command_words: the variable command word and the source carrier's .
deny U1 removal_beside_variable_command 'f=rm; [[ a]] =~ ( #) ]]; $f -rf /etc/hosts'
deny U2 removal_beside_source '[[ a]] =~ ( #) ]]; . ./x.sh; rm -rf build'
# top_level_text, read by R1: the heredoc ends at its E\ line and the next
# two lines join into rm -rf /etc/hosts, which only the segmenter's top-level
# reading sees.
deny U3 removal_beside_heredoc $'[[ a]] =~ ( #) ]]; cat <<\'E\\\'\nhello\nE\\\nr\\\nm -rf /etc/hosts'
# top_level_text, read by R1 and by R2's env split: the line continuation
# joins e and nv into env -S, which only the segmenter's top-level reading
# joins.
deny U4 removal_beside_env_split $'[[ a]] =~ ( #) ]]; echo \'it\'; e\\\nnv -S\'rm\\_-rf\\_/etc/hosts\''

if [ -d "$WORKTREE/build" ] && [ -f "$WORKTREE/build/x" ]; then pass "fixtures remained present"; else fail "a fixture was executed"; fi

if [ "$FAILURES" -eq 0 ]; then
  printf 'ALL CARRIER TESTS PASSED\n'
  exit 0
fi
printf '%s TEST(S) FAILED\n' "$FAILURES"
exit 1
