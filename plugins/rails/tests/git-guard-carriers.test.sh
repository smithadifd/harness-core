#!/bin/bash
# The git guard's presumptions: a push word beside a code-carrying construct
# the guard does not read inside (design § 2.2 P2), rule G outside plain text
# (P3), the data-consumer heredoc exemption (§ 2.4) and configuration on the
# command line (§ 7.2), with the source suite's rows moved here. A presumed
# push takes the stricter of push_to_main and force_push: an ask at the
# defaults, a deny unattended.
# Command strings are inert JSON data and are never executed.
set -u
LC_ALL=C
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUITE=carriers
. "$HERE/fixtures/git-guard/harness.sh"

printf '%s\n' '== Carriers, one row per class (C rows) =='
gg_row "C-1 substitution" ask:push_beside_substitution 'git push $(echo origin) feature'
gg_row "C-2 backtick" ask:push_beside_backtick 'git push `echo origin` feature'
gg_row "C-3 process substitution" ask:push_beside_process_substitution 'tee >(git push origin feature)'
gg_row "C-4 here-string to a shell" ask:push_beside_heredoc "bash <<< 'git push origin feature'"
gg_row "C-5 eval" ask:push_beside_eval 'eval git push origin feature'
gg_row "C-6 alias" ask:push_beside_alias "alias gp='git push'; gp"
gg_row "C-7 env -S" ask:push_beside_env_split "env -S 'git push origin feature'"
gg_row "C-8 a variable command word" ask:push_beside_variable_command '$G push origin feature'
gg_row "C-9 git submodule foreach" ask:push_beside_git_foreach "git submodule foreach 'git push origin feature'"
gg_row "C-10 git rebase -x" ask:push_beside_git_rebase_exec "git rebase -x 'git push -f' main"
gg_row "C-11 git bisect run" ask:push_beside_git_bisect_run 'git bisect run git push'
gg_row "C-12 source of a process substitution" ask:push_beside_process_substitution 'source <(echo git push)'
gg_row "C-13 trap" ask:push_beside_trap "trap 'git push -f' EXIT"
gg_row "C-14 xargs" ask:push_beside_xargs 'printf x | xargs git push origin'
gg_row "C-15 find -exec" ask:push_beside_find_exec 'find . -exec git push origin feature \;'
gg_row "C-16 sh -c" ask:push_beside_shell "sh -c 'git push origin feature'"
gg_row "C-17 flock" ask:push_beside_flock 'flock /tmp/l git push origin feature'
gg_row "C-18 node -e" ask:push_beside_interpreter "node -e 'require(\"child_process\").execSync(\"git push\")'"
gg_row "C-19 a shell whose pipeline holds a \$" ask:push_beside_shell 'CMD="git push -f"; bash -c "$CMD"'
gg_row "C-20 source in the push's pipeline" ask:push_beside_source 'echo git push | source /dev/stdin'
gg_row "C-21 dot as a command word in the push's pipeline" ask:push_beside_source "printf 'git push -f' | . /dev/stdin"
gg_row "C-21b source in another pipeline, before a feature push" silent 'source ./env.sh && git push origin feature'
gg_row "C-22 python3.12 -c" ask:push_beside_interpreter "python3.12 -c 'import os; os.system(\"git push\")'"
gg_row "C-23 a pattern command word" ask:push_beside_variable_command 'gi? push origin feature'
gg_row "C-24 push as the last element of a python list" ask:push_beside_interpreter "python3 -c \"import subprocess; subprocess.run(['git','push'])\""
gg_row "C-25 a pipeline into a group that runs sh" ask:push_beside_shell "printf 'git push --force origin main\\n' | { cat; sh; }"
gg_row "C-26 a pipeline into a subshell that runs sh" ask:push_beside_shell "printf 'git push --force origin main\\n' | (cat; sh)"
gg_row "C-27 a group, then a pipe into sh" ask:push_beside_shell "{ printf 'git push -f'; } | sh"
gg_row "C-28 a glob suffix on the push word, beside a substitution" ask:push_beside_substitution 'echo "row $(date +%s) | git push"*'

printf '%s\n' '== Pipeline scope (P-S rows): silent =='
gg_row "P-S1 a shell script before a feature push" silent 'bash scripts/test.sh && git push origin feature'
gg_row "P-S2 inline python before a feature push" silent 'python3 -c "import json" && git push origin feature'
gg_row "P-S3 xargs in another pipeline" silent 'git ls-files | xargs grep x && git push origin feature'
gg_row "P-S4 echo of a push" silent 'echo never git push --force'
gg_row "P-S5 grep for push" silent 'grep -n push notes.md'
gg_row "P-S6 |& joins the pipeline" ask:push_beside_shell "printf 'git push -f\\n' |& sh"
gg_row "P-S7 a newline after | joins the pipeline" ask:push_beside_shell $'printf \'git push -f\\n\' |\nsh'

printf '%s\n' '== Rule G and the plain text (C-G rows) =='
INCIDENT_A='node /opt/agent/companion.mjs task --cwd /srv/lanes/lane-1 --prompt "You are a builder working ONLY inside your worktree. Never rm -rf; never export HOME; never git push or --force; never launchctl; never write the agent config folders. Exactly one commit." --write'
gg_row "C-G1 incident A: a quoted builder prompt" silent "$INCIDENT_A"
gg_row "C-G2 a quoted semicolon in a commit message, then a feature push" silent 'git commit -m "docs: a; b" && git push origin feature'
gg_row "C-G3 read past a trusted quote" ask:push_main_branch "git log --format='a;b' && git push origin main"
gg_row "C-G4 an apostrophe in a comment hides a push from the segmenter" ask:push_hidden_separator $'echo a # it\'s\ngit push -f origin main\necho b\''
gg_row "C-G5 margin: \$' in the text" ask:push_hidden_separator "echo \$'a\\';' git push -f origin feature"
gg_row "C-G5b margin: \$' with no backslash" ask:push_hidden_separator "echo \$'a;b' git push -f origin feature"
gg_row "C-G6 margin: a backslash in the text" ask:push_hidden_separator 'echo "a\"; git push -f origin main"'
gg_row "C-G7 margin: \${ in the text" ask:push_hidden_separator "printf '%s' \"a;b\" \${X:-}; git push origin feature"
gg_row "C-G8 margin: \$\" in the text" ask:push_hidden_separator "echo \$\"a;b\" git push -f origin feature"
gg_row "C-G9 a continuation in plain text beside a push word" ask:push_hidden_separator $'git push \\\n origin feature'

printf '%s\n' '== Heredocs and the data-consumer exemption (C-H rows) =='
INCIDENT_B=$'cat <<\'EOF\' >> "$SCRATCH/lane-preamble.md"\nRules for this lane: never git push or --force; never launchctl; never write outside the worktree.\nEOF'
gg_row "C-H1 incident B: a heredoc brief appended to a file" silent "$INCIDENT_B"
gg_row "C-H2 gh --body-file - with a push in the body" silent $'gh pr create --body-file - <<\'EOF\'\nNever push to main.\nEOF'
gg_row "C-H3 git commit -F - with push in the body" silent $'git commit -F - <<EOF\ndocs: never git push from here\nEOF'
gg_row "C-H4 a heredoc piped to sh" ask:push_beside_heredoc $'cat <<EOF | sh\ngit push -f origin feature\nEOF'
gg_row "C-H5 a pipe carried over a newline to sh" ask:push_beside_heredoc $'cat <<EOF |\ngit push -f origin feature\nEOF\nsh'
gg_row "C-H6 a data heredoc, then a real push to main" ask:push_main_branch $'cat <<EOF > x\ngit push\nEOF\ngit push origin main'
gg_row "C-H7 an arithmetic shift is not a consumer" ask:push_beside_heredoc $'((y=1<<E))\ngit push -f origin main\nE'
gg_row "C-H7b an arithmetic shift with a clean delimiter and its line" ask:push_beside_heredoc $'(( y = 1 << E ))\ngit push -f origin main\nE'
gg_row "C-H8 a backslash in the delimiter" ask:push_beside_heredoc $'cat <<E\\OF\ngit push -f origin main\nEOF'
gg_row "C-H9 python reads a heredoc" ask:push_beside_heredoc $'python3 - <<EOF\nimport os; os.system("git push -f")\nEOF'
gg_row "C-H10 a delimiter the copied reader stops past the )" ask:push_beside_heredoc $'(cat <<EOF)\nhello\nEOF\ngit push -f origin main'
gg_row "C-H11 no closing line" ask:push_beside_heredoc $'cat <<EOF > notes\ngit push -f origin main'
gg_row "C-H12 a clean delimiter and its line" silent $'cat <<\'END-1\' > notes\ngit push -f origin main\nEND-1'
gg_row "C-H13 a heredoc marker inside quotes" silent $'echo "<<EOF"\necho never git push --force'
gg_row "C-H14 tee is a consumer" silent $'tee file <<EOF\ngit push -f\nEOF'

printf '%s\n' '== Configuration on the command line (C-K rows) =='
gg_row "C-K1 an alias defined with -c, no push word" ask:git_config_injection 'git -c alias.p=log p'
gg_row "C-K2 --config-env" ask:git_config_injection 'git --config-env=alias.p=VAR p'
gg_row "C-K3 GIT_CONFIG_COUNT and a key before git" ask:git_config_injection 'GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=alias.p GIT_CONFIG_VALUE_0=log git p'
gg_row "C-K4 git config alias." ask:git_config_injection "git config --global alias.p 'log -1'"
gg_row "C-K5 an echo argument does not reach" silent 'echo GIT_CONFIG_COUNT=1 && git push origin feature'
gg_row "C-K6 GIT_DIR before a push" ask:push_git_environment 'GIT_DIR=/x git push origin feature'
gg_row "C-K7 an exported GIT_WORK_TREE" ask:push_git_environment 'export GIT_WORK_TREE=/x; git push origin feature'
gg_row "C-K8 assignment-only segments, then bare names exported" ask:push_git_environment 'GIT_CONFIG_COUNT=1; GIT_CONFIG_KEY_0=remote.origin.push; GIT_CONFIG_VALUE_0=feature:main; export GIT_CONFIG_COUNT GIT_CONFIG_KEY_0 GIT_CONFIG_VALUE_0; git push origin'
GG_MAIN=off
GG_FORCE=off
gg_row "C-K8b the same, both push options off: a presumption is silent too" silent 'GIT_CONFIG_COUNT=1; GIT_CONFIG_KEY_0=remote.origin.push; GIT_CONFIG_VALUE_0=feature:main; export GIT_CONFIG_COUNT GIT_CONFIG_KEY_0 GIT_CONFIG_VALUE_0; git push origin'
GG_MAIN=ask
GG_FORCE=ask
gg_row "C-K9 an assignment-only segment reaches" ask:push_git_environment 'GIT_DIR=/x; git push origin feature'
gg_row "C-K10 a bare name to declare, no push word" ask:git_config_injection 'declare -x GIT_CONFIG_PARAMETERS; git status'
gg_row "C-K11 an argument of echo does not reach" silent 'echo GIT_DIR=/x; git push origin feature'
gg_row "C-K12 env with a GIT_ assignment" ask:push_git_environment 'env GIT_WORK_TREE=y git push origin feature'
gg_row "C-K13 a push mapping with -c" ask:git_config_injection 'git -c remote.origin.push=+HEAD:feature push origin'
gg_row "C-K14 an unrelated GIT_ name" silent 'GIT_TERMINAL_PROMPT=0 git push origin feature'
gg_row "C-K15 an alias defined with -c through a backslashed git word" ask:git_config_injection 'g\it -c alias.p=log p'

GG_A_4067="$(python3 -I -c 'import sys; sys.stdout.write("A" * 4067)')"
GG_A_4069="$(python3 -I -c 'import sys; sys.stdout.write("A" * 4069)')"
GG_A_30000="$(python3 -I -c 'import sys; sys.stdout.write("A" * 30000)')"
printf '%s\n' '== Rows moved from the source suite (GT:<line> is the source line) =='
GG_COMMIT=off
gg_row_at "$REPO_FEATURE" $'GT:335 force push piped to /bin/sh' ask $'printf \'git push --force origin main\\n\' | /bin/sh'
gg_row_at "$REPO_FEATURE" $'GT:336 force push piped to bash' ask $'printf \'git push --force origin main\\n\' | bash'
gg_row_at "$REPO_FEATURE" $'GT:337 force push piped through env sh' ask $'printf \'git push --force origin main\\n\' | /usr/bin/env sh'
gg_row_at "$REPO_FEATURE" $'GT:338 force push piped to grouped sh' ask $'printf \'git push --force origin main\\n\' | (sh)'
gg_row_at "$REPO_FEATURE" $'GT:339 force push piped to braced sh' ask $'printf \'git push --force origin main\\n\' | { sh; }'
gg_row_at "$REPO_FEATURE" $'GT:374 force push piped through brace group' ask $'printf \'git push --force origin main\\n\' | { cat; sh; }'
gg_row_at "$REPO_FEATURE" $'GT:376 force push piped through subshell' ask $'printf \'git push --force origin main\\n\' | (cat; sh)'
# an ANSI-C escape that can spell a letter presumes the push word (P1)
gg_row_at "$REPO_FEATURE" $'GT:605 escaped ANSI-C quoted shell word' ask $'x=$(y); cat f | $\'\\x62ash\''
# an ANSI-C escape that can spell a letter presumes the push word (P1)
gg_row_at "$REPO_FEATURE" $'GT:738 ANSI-C escapes can spell a shell name' ask $'x=$(y); S=$\'\\x62ash\'; cat f | ./launch $S'
# an ANSI-C escape that can spell a letter presumes the push word (P1)
gg_row_at "$REPO_FEATURE" $'GT:766 ANSI-C escaped shell name' ask $'x=$(y); S=$\'\\x62ash\'; cat f | env $S'
gg_row_at "$REPO_FEATURE" $'GT:1368 expansion in earlier command' silent $'cd "$S" && git push -q origin x'
gg_row_at "$REPO_FEATURE" $'GT:1369 single-quoted dollar' silent $'sed -n \'/x/,$p\' f && git push origin x'
gg_row_at "$REPO_FEATURE" $'GT:1370 quoted separator in git log' silent $'git log -1 --format=\'audit;v1\' && git push origin feature'
gg_row_at "$REPO_FEATURE" $'GT:1371 quoted commit punctuation' silent $'git commit -m "docs: a; b" && git push -q origin HEAD'
gg_row_at "$REPO_FEATURE" $'GT:1372 python payload before push' silent $'python3 -c "import json" && git push -q origin x'
gg_row_at "$REPO_FEATURE" $'GT:1373 shell file before push' silent $'bash -n s.sh && git push origin x'
gg_row_at "$REPO_FEATURE" $'GT:1374 shell script option after path' ask $'bash s.sh \'git push -f\' -c ignored; git push origin x'
gg_row_at "$REPO_FEATURE" $'GT:1375 conventional commit with punctuation' silent $'git commit -m "docs: a; b"'
gg_row_at "$REPO_FEATURE" $'GT:1376 assignment before push' silent $'FOO=bar git push origin feature'
gg_row_at "$REPO_FEATURE" $'GT:1377 non-git config flag' silent $'./deploy.sh --config-env=prod'
gg_row_at "$REPO_FEATURE" $'GT:1378 non-git config text' silent $'echo GIT_CONFIG_COUNT=1'
gg_row_at "$REPO_FEATURE" $'GT:1379 other config flag' silent $'kubectl --config-env=x get pods'
gg_row_at "$REPO_FEATURE" $'GT:1380 wrapper after push' silent $'git push origin x; timeout 5 echo done'
gg_row_at "$REPO_FEATURE" $'GT:1381 quoted push punctuation' ask $'git push -o=\'audit;v1\' --force origin feature'
gg_row_at "$REPO_FEATURE" $'GT:1386 configured push refspec' ask $'git -c remote.origin.push=+HEAD:feature push origin'
gg_row_at "$REPO_FEATURE" $'GT:1387 configured pushurl' ask $'git -c remote.origin.pushurl=elsewhere push origin feature'
gg_row_at "$REPO_FEATURE" $'GT:1388 configured mirror' ask $'git -c REMOTE.origin.MIRROR=true push origin feature'
gg_row_at "$REPO_FEATURE" $'GT:1389 attached config option' ask $'git -cremote.origin.push=+HEAD:feature push origin'
gg_row_at "$REPO_FEATURE" $'GT:1390 other push key' ask $'git -c custom.push=+HEAD:feature push origin'
gg_row_at "$REPO_FEATURE" $'GT:1391 other attached mirror key' ask $'git -ccustom.MIRROR=true push origin feature'
gg_row_at "$REPO_FEATURE" $'GT:1383 config text in earlier command' silent $'echo GIT_CONFIG_COUNT=1 && git push origin feature'
gg_row_at "$REPO_FEATURE" $'GT:1384 env chdir in earlier command' silent $'env -C /tmp pwd && git push origin feature'
gg_row_at "$REPO_FEATURE" $'GT:1393 loop reads push body' ask $'while read l; do $l; done <<EOF\ngit push -f origin feature\nEOF'
gg_row_at "$REPO_FEATURE" $'GT:1394 piped loop reads push body' ask $'cat <<EOF | while read l; do $l; done\ngit push -f origin feature\nEOF'
gg_row_at "$REPO_FEATURE" $'GT:1396 unknown input command ash' ask $'ash <<EOF\ngit push -f origin feature\nEOF'
gg_row_at "$REPO_FEATURE" $'GT:1396 unknown input command csh' ask $'csh <<EOF\ngit push -f origin feature\nEOF'
gg_row_at "$REPO_FEATURE" $'GT:1396 unknown input command tcsh' ask $'tcsh <<EOF\ngit push -f origin feature\nEOF'
gg_row_at "$REPO_FEATURE" $'GT:1396 unknown input command rbash' ask $'rbash <<EOF\ngit push -f origin feature\nEOF'
gg_row_at "$REPO_FEATURE" $'GT:1396 unknown input command mksh' ask $'mksh <<EOF\ngit push -f origin feature\nEOF'
gg_row_at "$REPO_FEATURE" $'GT:1396 unknown input command yash' ask $'yash <<EOF\ngit push -f origin feature\nEOF'
gg_row_at "$REPO_FEATURE" $'GT:1396 unknown input command busybox' ask $'busybox <<EOF\ngit push -f origin feature\nEOF'
gg_row_at "$REPO_FEATURE" $'GT:1396 unknown input command nodejs' ask $'nodejs <<EOF\ngit push -f origin feature\nEOF'
gg_row_at "$REPO_FEATURE" $'GT:1396 unknown input command bash5' ask $'bash5 <<EOF\ngit push -f origin feature\nEOF'
gg_row_at "$REPO_FEATURE" $'GT:1396 unknown input command bash-5.2' ask $'bash-5.2 <<EOF\ngit push -f origin feature\nEOF'
gg_row_at "$REPO_FEATURE" $'GT:1396 unknown input command at' ask $'at <<EOF\ngit push -f origin feature\nEOF'
gg_row_at "$REPO_FEATURE" $'GT:1396 unknown input command batch' ask $'batch <<EOF\ngit push -f origin feature\nEOF'
gg_row_at "$REPO_FEATURE" $'GT:1396 unknown input command tclsh' ask $'tclsh <<EOF\ngit push -f origin feature\nEOF'
gg_row_at "$REPO_FEATURE" $'GT:1396 unknown input command osascript' ask $'osascript <<EOF\ngit push -f origin feature\nEOF'
gg_row_at "$REPO_FEATURE" $'GT:1396 unknown input command expect' ask $'expect <<EOF\ngit push -f origin feature\nEOF'
gg_row_at "$REPO_FEATURE" $'GT:1396 unknown input command php' ask $'php <<EOF\ngit push -f origin feature\nEOF'
gg_row_at "$REPO_FEATURE" $'GT:1396 unknown input command lua' ask $'lua <<EOF\ngit push -f origin feature\nEOF'
gg_row_at "$REPO_FEATURE" $'GT:1396 unknown input command parallel' ask $'parallel <<EOF\ngit push -f origin feature\nEOF'
gg_row_at "$REPO_FEATURE" $'GT:1398 scheduled stdin' ask $'at now <<EOF\ngit push -f origin feature\nEOF'
gg_row_at "$REPO_FEATURE" $'GT:1399 escaped shell command' ask $'s\\h <<EOF\ngit push -f origin feature\nEOF'
gg_row_at "$REPO_FEATURE" $'GT:1400 split quoted shell command' ask $'"s"h <<EOF\ngit push -f origin feature\nEOF'
gg_row_at "$REPO_FEATURE" $'GT:1401 shell variable command' ask $'$SHELL <<EOF\ngit push -f origin feature\nEOF'
gg_row_at "$REPO_FEATURE" $'GT:1402 quoted shell variable command' ask $'"$BASH" <<EOF\ngit push -f origin feature\nEOF'
gg_row_at "$REPO_FEATURE" $'GT:1403 braced shell variable command' ask $'${SHELL} <<EOF\ngit push -f origin feature\nEOF'
gg_row_at "$REPO_FEATURE" $'GT:1404 deno stdin' ask $'deno run - <<EOF\ngit push -f origin feature\nEOF'
gg_row_at "$REPO_FEATURE" $'GT:1405 bun stdin' ask $'bun run - <<EOF\ngit push -f origin feature\nEOF'
gg_row_at "$REPO_FEATURE" $'GT:1406 loop in process substitution' ask $'cat <(while read l; do $l; done <<< \'git push -f origin feature\')'
gg_row_at "$REPO_FEATURE" $'GT:1407 data heredoc output' silent $'cat > f <<EOF\ngit push -f origin feature\nEOF'
gg_row_at "$REPO_FEATURE" $'GT:1408 data pipeline body file' silent $'cd d && cat <<EOF | gh pr create --body-file -\ngit push -f origin feature\nEOF'
gg_row_at "$REPO_FEATURE" $'GT:1409 data commit message' silent $'git commit -F - <<EOF\ngit push -f origin feature\nEOF'
gg_row_at "$REPO_FEATURE" $'GT:1410 plain feature push' silent $'git push origin feature'
gg_row_at "$REPO_FEATURE" $'GT:1412 option after shell path' ask $'bash script.sh -c \'git push -f origin feature\''
gg_row_at "$REPO_FEATURE" $'GT:1413 plus option after shell path' ask $'bash script.sh +x \'git push -f origin feature\''
gg_row_at "$REPO_FEATURE" $'GT:1415 exported pushurl' ask $'export GIT_CONFIG_PARAMETERS="\'remote.origin.pushurl=/tmp/x\'"; git push origin feature'
gg_row_at "$REPO_FEATURE" $'GT:1416 exported alias' ask $'export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=alias.p GIT_CONFIG_VALUE_0=\'push -f\'; git p'
gg_row_at "$REPO_FEATURE" $'GT:1417 declared pushurl' ask $'declare -x GIT_CONFIG_PARAMETERS="\'remote.origin.pushurl=/tmp/x\'" && git push origin feature'
gg_row_at "$REPO_FEATURE" $'GT:1418 assigned then exported pushurl' ask $'GIT_CONFIG_PARAMETERS="\'remote.origin.pushurl=/tmp/x\'"; export GIT_CONFIG_PARAMETERS\ngit push origin feature'
gg_row_at "$REPO_FEATURE" $'GT:1422 length expansion before push' ask $'echo ${#a}; git push --force origin main'
gg_row_at "$REPO_FEATURE" $'GT:1423 argument count before push' ask $'echo $#; git push --force origin main'
gg_row_at "$REPO_FEATURE" $'GT:1424 brace word before push' ask $'echo {#a}; git push --force origin main'
gg_row_at "$REPO_FEATURE" $'GT:1426 force push text via stdout and stderr pipe' ask $'printf \'git push -f\\n\' |& sh'
gg_row_at "$REPO_FEATURE" $'GT:1427 global-option push text via stdout and stderr pipe' ask $'printf \'git -C /tmp push -f\\n\' |& sh'
gg_row_at "$REPO_FEATURE" $'GT:1428 possible shell input via stdout and stderr pipe' silent $'echo x |& bash'
gg_row_at "$REPO_FEATURE" $'GT:1429 status through stdout and stderr pipe to cat' silent $'git status |& cat'
gg_row_at "$REPO_FEATURE" $'GT:1435 push-free R4L1' ask $'echo \''"$GG_A_30000"$'\' && git push --force origin main'
gg_row_at "$REPO_FEATURE" $'GT:1436 push-free R4L2' silent $'python3 -c \''"$GG_A_30000"$'\''
gg_row_at "$REPO_FEATURE" $'GT:1437 push-free R4L3' ask $'echo "git push $(date +%s) '"$GG_A_4067"$'"'
gg_row_at "$REPO_FEATURE" $'GT:1438 push-free R4L4' ask $'echo "git push $(date +%s) '"$GG_A_4069"$'"'
# no push word under P1's normal form; the live guard is silent too
gg_row_at "$REPO_FEATURE" $'GT:1439 push-free A1' silent $'echo "- $(date +%H:%M) | row | ff-push.sh pushed" >> /abs/handoff.txt'
gg_row_at "$REPO_FEATURE" $'GT:1440 push-free A2' ask $'echo "- $(date \'+%Y-%m-%d %H:%M %Z\') | row | git push of the branch landed" >> /abs/handoff.txt'
gg_row_at "$REPO_FEATURE" $'GT:1441 push-free A3' ask $'echo "- $(date "+%Y-%m-%d %H:%M %Z") | row | git push landed" >> /abs/handoff.txt'
# no push word under P1's normal form; the live guard is silent too
gg_row_at "$REPO_FEATURE" $'GT:1442 push-free A4' silent $'echo "- $(date +%H:%M) | row | git pushed" >> /abs/handoff.txt'
# no push word under P1's normal form; the live guard is silent too (push\n normalizes to pushn)
gg_row_at "$REPO_FEATURE" $'GT:1443 push-free A5' silent $'printf -- "- %s | LE1 | git push\\n" "$(date +%H:%M)" >> /abs/handoff.txt'
# no push word under P1's normal form; the live guard is silent too (push\n normalizes to pushn)
gg_row_at "$REPO_FEATURE" $'GT:1444 push-free A6' silent $'printf "%s git push\\n" "$(date +%H:%M)"'
# no push word under P1's normal form; the live guard is silent too (push.log)
gg_row_at "$REPO_FEATURE" $'GT:1445 push-free A7' silent $'cat "notes-$(date +%F).md" >> "git push.log"'
gg_row_at "$REPO_FEATURE" $'GT:1446 push-free A8' ask $'echo "- $(date +%s)" git push'
gg_row_at "$REPO_FEATURE" $'GT:1447 push-free A8b' ask $'echo $(date +%s) git push'
gg_row_at "$REPO_FEATURE" $'GT:1448 push-free A9' ask $'echo "- $(date +%H:%M) | row | git push" > /abs/out.txt'
gg_row_at "$REPO_FEATURE" $'GT:1449 push-free A10' ask $'echo "- $(date +%H:%M) | row | git push" >> "/abs/hand off.txt"'
gg_row_at "$REPO_FEATURE" $'GT:1450 push-free A11' ask $'echo "- $(date +%s)" "$(date +%F)" "git push"'
gg_row_at "$REPO_FEATURE" $'GT:1451 push-free A12' ask $'echo "- $(date +%H:%M) | row | git push" >> /abs/notes-$(date +%F).md'
gg_row_at "$REPO_FEATURE" $'GT:1452 push-free A13' ask $'echo "- $(date +%H:%M) | row | git push" >> "/abs/notes-$(date +%F).md"'
gg_row_at "$REPO_FEATURE" $'GT:1453 push-free A14' ask $'echo \'- $(date +%s) | git push\''
gg_row_at "$REPO_FEATURE" $'GT:1454 push-free R3T1' ask $'echo "- $(date +%H:%M) | row | git push" > $(date +%F)'
gg_row_at "$REPO_FEATURE" $'GT:1455 push-free R3T2' ask $'echo "- $(date +%H:%M) | row | git push" >> $(date +%F)'
gg_row_at "$REPO_FEATURE" $'GT:1456 push-free R3T3' ask $'echo "- $(date +%H:%M) | row | git push" > "$(date +%F)"*'
gg_row_at "$REPO_FEATURE" $'GT:1457 push-free R3T4' ask $'echo "- $(date +%H:%M) | row | git push" > *'
gg_row_at "$REPO_FEATURE" $'GT:1458 push-free R3D1' ask $'echo x"$(date +%s) | row | git push"*'
# UNCLASSED: no push word under P1 (pushx); the live guard asks; proposed class S12
gg_row_at "$REPO_FEATURE" $'GT:1459 push-free R3D2' silent $'echo "row $(date +%s) | git push"x'
gg_row_at "$REPO_FEATURE" $'GT:1460 push-free R3D3' ask $'echo "row $(date +%s) | git push"*'
gg_row_at "$REPO_FEATURE" $'GT:1461 push-free B1' ask $'echo "- `date +%H:%M` | git push" >> f'
gg_row_at "$REPO_FEATURE" $'GT:1462 push-free B1b' ask $'echo "- $(date +%H:%M) git push" \'`date`\''
gg_row_at "$REPO_FEATURE" $'GT:1463 push-free B2' ask $'echo "- $(date -u) | git push" >> f'
gg_row_at "$REPO_FEATURE" $'GT:1464 push-free B3' ask $'echo "- $(date) | git push" >> f'
gg_row_at "$REPO_FEATURE" $'GT:1465 push-free B4' ask $'echo "- $(date  +%s) | git push" >> f'
gg_row_at "$REPO_FEATURE" $'GT:1466 push-free B5' ask $'echo "- $(date; git push) | git push" >> f'
gg_row_at "$REPO_FEATURE" $'GT:1467 push-free B6' ask $'echo "- $(date $(git push)) | git push" >> f'
gg_row_at "$REPO_FEATURE" $'GT:1468 push-free B7' ask $'echo "- $(eval date) | git push" >> f'
gg_row_at "$REPO_FEATURE" $'GT:1469 push-free B8' ask $'echo "- $(date | sh) | git push" >> f'
gg_row_at "$REPO_FEATURE" $'GT:1470 push-free B9' ask $'echo "- $(date > /abs/x) | git push" >> f'
gg_row_at "$REPO_FEATURE" $'GT:1471 push-free B10' ask $'echo "- $(date +%H:%M; true) | git push" >> f'
gg_row_at "$REPO_FEATURE" $'GT:1472 push-free B11' ask $'echo "- $(sh +e) | git push" >> f'
gg_row_at "$REPO_FEATURE" $'GT:1473 push-free B12' ask $'echo "- $(date +x)$(git push) | git push" >> f'
gg_row_at "$REPO_FEATURE" $'GT:1474 push-free B13' ask $'echo "- $(date +x) $HOME git push"'
gg_row_at "$REPO_FEATURE" $'GT:1475 push-free B14' ask $'echo "- $(date +x) $$ git push"'
gg_row_at "$REPO_FEATURE" $'GT:1476 push-free B15' ask $'echo "- $(date +x) ${X} git push"'
gg_row_at "$REPO_FEATURE" $'GT:1477 push-free B16' ask $'echo "- $(date \'+%s)\' git push"'
gg_row_at "$REPO_FEATURE" $'GT:1478 push-free B17' ask $'echo "- $(date \'+%s\' git push)"'
# a carriage return fails the copied hook-input filter: adapter_input_invalid
gg_row_at "$REPO_FEATURE" $'GT:1479 push-free B18' deny $'echo "- $(date +%s) git push\r" >> f'
gg_row_at "$REPO_FEATURE" $'GT:1480 push-free B19' ask $'echo "- $(date\n+x) push"'
gg_row_at "$REPO_FEATURE" $'GT:1481 push-free B20' ask $'echo "- $(date +%s)\ngit push --force origin main"'
gg_row_at "$REPO_FEATURE" $'GT:1482 push-free B21' ask $'echo "- $(date "+$FMT") | git push" >> f'
gg_row_at "$REPO_FEATURE" $'GT:1483 push-free B22' ask $'echo "- $(date +%s)" "it\'s push'
gg_row_at "$REPO_FEATURE" $'GT:1484 push-free B23' ask $'echo$(date +x) "git push"'
gg_row_at "$REPO_FEATURE" $'GT:1485 push-free B24' ask $'$(date +git) push --force origin main'
gg_row_at "$REPO_FEATURE" $'GT:1486 push-free B25' ask $'echo "- $(date +%s)" \'$(git push --force origin main)\''
gg_row_at "$REPO_FEATURE" $'GT:1487 push-free C1' ask $'[[ \'a[$(git push --force origin main)]\' -eq "$(date +%s)" ]]'
gg_row_at "$REPO_FEATURE" $'GT:1488 push-free C2' ask $'test "- $(date +%s) git push"'
gg_row_at "$REPO_FEATURE" $'GT:1489 push-free C3' ask $'[ "- $(date +%s) git push"'
gg_row_at "$REPO_FEATURE" $'GT:1490 push-free C4' ask $'/bin/echo "- $(date +%s) git push"'
gg_row_at "$REPO_FEATURE" $'GT:1491 push-free C5' ask $'./echo "- $(date +%s) git push"'
gg_row_at "$REPO_FEATURE" $'GT:1492 push-free C6' ask $'"echo" "- $(date +%s) git push"'
gg_row_at "$REPO_FEATURE" $'GT:1493 push-free C7' ask $'ec\\ho "- $(date +%s) git push"'
gg_row_at "$REPO_FEATURE" $'GT:1494 push-free C8' ask $'FOO=1 echo "- $(date +%s) git push"'
gg_row_at "$REPO_FEATURE" $'GT:1495 push-free C9' ask $'command echo "- $(date +%s) git push"'
gg_row_at "$REPO_FEATURE" $'GT:1496 push-free C10' ask $'env echo "- $(date +%s) git push"'
gg_row_at "$REPO_FEATURE" $'GT:1497 push-free C11' ask $'sed "- $(date +%s) git push"'
gg_row_at "$REPO_FEATURE" $'GT:1498 push-free C12' ask $'sort "- $(date +%s) git push"'
gg_row_at "$REPO_FEATURE" $'GT:1499 push-free C13' ask $'python3 "- $(date +%s) git push"'
gg_row_at "$REPO_FEATURE" $'GT:1500 push-free C14' ask $'sh "- $(date +%s) git push"'
gg_row_at "$REPO_FEATURE" $'GT:1501 push-free C15' ask $'node "- $(date +%s) git push"'
gg_row_at "$REPO_FEATURE" $'GT:1502 push-free C16' ask $'eval "- $(date +%s) git push"'
gg_row_at "$REPO_FEATURE" $'GT:1503 push-free C17' ask $'[[ "- $(date +%s) git push"'
gg_row_at "$REPO_FEATURE" $'GT:1504 push-free D1' ask $'printf -v \'a[$(git push --force origin main)]\' "$(date +%s)"'
gg_row_at "$REPO_FEATURE" $'GT:1505 push-free D1b' ask $'printf "-v" "a[0]" "- $(date +%s) git push"'
gg_row_at "$REPO_FEATURE" $'GT:1506 push-free D2' ask $'printf -- "-v" "- $(date +%s) git push"'
gg_row_at "$REPO_FEATURE" $'GT:1507 push-free D3' ask $'printf -- >> "$(date +%F) push"'
gg_row_at "$REPO_FEATURE" $'GT:1508 push-free D4' ask $'echo -e "- $(date +%s) git push"'
gg_row_at "$REPO_FEATURE" $'GT:1509 push-free D5' ask $'echo "-e" "- $(date +%s) git push"'
gg_row_at "$REPO_FEATURE" $'GT:1510 push-free D6' ask $'echo "-" "- $(date +%s) git push"'
gg_row_at "$REPO_FEATURE" $'GT:1511 push-free D7' ask $'echo "- $(date +%H:%M) git push"'
gg_row_at "$REPO_FEATURE" $'GT:1512 push-free D8' ask $'cat "-v" "- $(date +%s) push"'
gg_row_at "$REPO_FEATURE" $'GT:1513 push-free D9' ask $'cat "notes-$(date +%F)" "-" "push"'
gg_row_at "$REPO_FEATURE" $'GT:1514 push-free D10' ask $'printf "$(date +-v)" "target" "git push --force origin main"'
gg_row_at "$REPO_FEATURE" $'GT:1515 push-free D11' ask $'echo "$(date +-n)" "git push"'
gg_row_at "$REPO_FEATURE" $'GT:1516 push-free D12' ask $'cat "notes" "$(date +-v)" "push"'
gg_row_at "$REPO_FEATURE" $'GT:1517 push-free D13' ask $'echo "$(date +%H:%M) | row | git push" >> f'
gg_row_at "$REPO_FEATURE" $'GT:1518 push-free D14' ask $'printf "-$(date +v)" "target" "git push --force origin main"'
gg_row_at "$REPO_FEATURE" $'GT:1519 push-free D15' ask $'cat "notes" "x"$(date \'+a -v\') "push"'
gg_row_at "$REPO_FEATURE" $'GT:1520 push-free D16' ask $'echo "- x" "y"$(date \'+%H %z\') "git push"'
gg_row_at "$REPO_FEATURE" $'GT:1521 push-free E1' ask $'echo "- $(date +%s)" && git push --force origin main'
gg_row_at "$REPO_FEATURE" $'GT:1522 push-free E2' ask $'echo "- $(date +%s)"; git push --force origin main'
# S1: no push word
gg_row_at "$REPO_FEATURE" $'GT:1523 push-free E3' silent $'echo "- $(date +%s)" | sh'
gg_row_at "$REPO_FEATURE" $'GT:1524 push-free E4' ask $'echo "- $(date +%s)" > >(git push --force origin main)'
gg_row_at "$REPO_FEATURE" $'GT:1525 push-free E5' ask $'echo "- $(date +%s)" <<< "git push"'
gg_row_at "$REPO_FEATURE" $'GT:1526 push-free E6' ask $'echo "- $(date +%s) git push" > f > g'
gg_row_at "$REPO_FEATURE" $'GT:1527 push-free E7' ask $'echo "- $(date +%s) git push" >'
gg_row_at "$REPO_FEATURE" $'GT:1528 push-free E8' ask $'echo "- $(date +%s) git push" &'
gg_row_at "$REPO_FEATURE" $'GT:1529 push-free E9' ask $'echo "- $(date +%s)" {a,b} push'
gg_row_at "$REPO_FEATURE" $'GT:1530 push-free E10' ask $'echo "- $(date +%s) git push" >&2'
gg_row_at "$REPO_FEATURE" $'GT:1531 push-free E11' ask $'echo "- $(date +%s)" 2> f "git push"'
gg_row_at "$REPO_FEATURE" $'GT:1532 push-free E12' ask $'cat <<EOF\n$(git push --force origin main) $(date +%s)\nEOF'
gg_row_at "$REPO_FEATURE" $'GT:1533 push-free E13' ask $'cat < "notes-$(date +%F) push"'
gg_row_at "$REPO_FEATURE" $'GT:1534 push-free E14' ask $'echo "- $(date +%s) git push" >|'
gg_row_at "$REPO_FEATURE" $'GT:1535 push-free E15' ask $'echo "- $(date +%s) git push" >> ;'
gg_row_at "$REPO_FEATURE" $'GT:1536 push-free C18' ask $'sed "1e git push --force origin main # $(date +%s)" /etc/hosts'
gg_row_at "$REPO_FEATURE" $'GT:1537 push-free C19' ask $'python3 -c "print(1) # git push $(date +%s)"'
gg_row_at "$REPO_FEATURE" $'GT:1538 push-free C20' ask $'sh -c "git push # $(date +%s)"'
gg_row_at "$REPO_FEATURE" $'GT:1539 push-free C21' ask $'node -e "1 // git push $(date +%s)"'
gg_row_at "$REPO_FEATURE" $'GT:1540 push-free F1' ask $'git push --receive-pack="x\ny" --force origin main'
gg_row_at "$REPO_FEATURE" $'GT:1541 push-free F2' ask $'git push origin "x\ny" --force'
gg_row_at "$REPO_FEATURE" $'GT:1542 push-free F3' ask $'git push "/tmp/a\nb" --force HEAD:main'
gg_row_at "$REPO_FEATURE" $'GT:1543 push-free F4' ask $'git push --exec="x\ny" origin main'
gg_row_at "$REPO_FEATURE" $'GT:1544 push-free F5' ask $'git push -o "x\ny" origin main'
gg_row_at "$REPO_FEATURE" $'GT:1545 push-free G1' ask $'python3 -c "import os as x; x.system(\'git push\')"'
gg_row_at "$REPO_FEATURE" $'GT:1546 push-free G2' ask $'python3 -c "import os; os.__dict__[\'sys\' \'tem\'](\'git push\')"'
gg_row_at "$REPO_FEATURE" $'GT:1547 push-free G3' ask $'python3 -X dev -c "import subprocess; subprocess.run([\'git\',\'push\'])"'
gg_row_at "$REPO_FEATURE" $'GT:1548 push-free G4' ask $'node --require fs --eval "require(\'child_process\').execSync(\'git push\')"'
gg_row_at "$REPO_FEATURE" $'GT:1549 push-free G5' ask $'perl -I /tmp -e \'system("git push")\''
gg_row_at "$REPO_FEATURE" $'GT:1550 push-free G6' ask $'python3 -c "exec(input())" <<< "import subprocess; subprocess.run([\'git\',\'push\'])"'
gg_row_at "$REPO_FEATURE" $'GT:1551 push-free G7' ask $'sh -c \'git push\''
gg_row_at "$REPO_FEATURE" $'GT:1552 push-free G8' ask $'echo git push | sh'
GG_COMMIT=deny
gg_row_at "$REPO_FEATURE" $'GT:1553 push-free H2' deny $'echo "- $(date +%s)"; git commit -m nope'
GG_COMMIT=off
gg_row_at "$REPO_FEATURE" $'GT:1554 push-free H3' silent $'echo "- $(date +%s) git commit -m nope" >> f'
# interpreter reads stdin with no inline-code flag; the live guard is silent too (§ 15 line 13)
gg_row_at "$REPO_FEATURE" $'GT:1555 push-free G9a' silent $'echo "import subprocess; subprocess.run([\'git\',\'push\'])" | python3 -X dev -'
# no push word; the live guard is silent too
gg_row_at "$REPO_FEATURE" $'GT:1556 push-free G9b' silent $'eval "$X"'
GG_COMMIT=off
GG_MAIN=ask
GG_FORCE=ask

gg_finish CARRIER
