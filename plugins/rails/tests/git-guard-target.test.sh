#!/bin/bash
# The git guard's target reads (design § 2.2 P7, § 7.3): a push that names no
# destination branch (no refspec, or HEAD or @) updates the current branch,
# which the guard asks a scrubbed git for, against real fixture repositories.
# Every way that read can fail is presumed main (push_main_unresolved), and
# configuration that can re-point the push is the main gate too.
# Command strings are inert JSON data and are never executed.
set -u
LC_ALL=C
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUITE=target
. "$HERE/fixtures/git-guard/harness.sh"

# make_repo NAME BRANCH: a fresh repository beside the fixtures.
make_repo() {
  git_q init -q --initial-branch="$2" "$T/repos/$1" || exit 1
  git_q -C "$T/repos/$1" remote add origin https://example.invalid/acme/widget.git || exit 1
  printf '%s' "$T/repos/$1"
}

DETACHED="$(make_repo detached feature)"
git_q -C "$DETACHED" commit -q --allow-empty -m seed || exit 1
git_q -C "$DETACHED" checkout -q --detach || exit 1
MATCHING="$(make_repo matching feature)"
git_q -C "$MATCHING" config push.default matching || exit 1
SIMPLE="$(make_repo simple feature)"
git_q -C "$SIMPLE" config push.default simple || exit 1
CURRENT="$(make_repo current feature)"
git_q -C "$CURRENT" config push.default current || exit 1
UPSTREAM="$(make_repo upstream feature)"
git_q -C "$UPSTREAM" config push.default upstream || exit 1
MAPPED="$(make_repo mapped feature)"
git_q -C "$MAPPED" config remote.origin.push feature:main || exit 1
MAPPED_FULL="$(make_repo mapped-full feature)"
git_q -C "$MAPPED_FULL" config remote.origin.push refs/heads/feature:refs/heads/main || exit 1
MIRRORED="$(make_repo mirrored feature)"
git_q -C "$MIRRORED" config remote.origin.mirror true || exit 1
BARE_TOO="$(make_repo bare-too feature)"
mkdir -p "$BARE_TOO/objects" "$BARE_TOO/refs/heads" || exit 1
printf 'ref: refs/heads/main\n' >"$BARE_TOO/HEAD" || exit 1
NOT_A_REPO="$T/not-a-repo"
mkdir -p "$NOT_A_REPO"

printf '%s\n' '== Targets (T rows), defaults =='
gg_row_at "$REPO_FEATURE" "T-1 a bare push from feature" silent 'git push'
gg_row_at "$REPO_MAIN" "T-2 a bare push from main" ask:push_main_branch 'git push'
gg_row_at "$REPO_MAIN" "T-3 -u origin HEAD from main" ask:push_main_branch 'git push -u origin HEAD'
gg_row_at "$REPO_FEATURE" "T-4 origin HEAD from feature" silent 'git push origin HEAD'
gg_row_at "$REPO_MAIN" "T-5 @ on an unborn main" ask:push_main_branch 'git push origin @'
gg_row_at "$DETACHED" "T-6 a detached HEAD" ask:push_main_unresolved 'git push origin HEAD'
gg_row_at "$NOT_A_REPO" "T-7 not a repository" ask:push_main_unresolved 'git push'
gg_row_at "$MATCHING" "T-8 push.default=matching" ask:push_config_remap 'git push'
gg_row_at "$SIMPLE" "T-9 push.default=simple" silent 'git push'
gg_row_at "$MAPPED" "T-10 a remote push mapping, bare push" ask:push_config_remap 'git push'
gg_row_at "$MIRRORED" "T-11 remote mirror=true" ask:push_config_remap 'git push origin HEAD'
gg_row_at "$REPO_FEATURE" "T-12 cd to main-repo with &&" ask:push_main_branch 'cd ../main-repo && git push'
gg_row_at "$REPO_MAIN" "T-13 cd to feature-repo with &&" silent 'cd ../feature-repo && git push'
gg_row_at "$REPO_MAIN" "T-14 cd across ;" ask:push_main_unresolved 'cd ../feature-repo; git push'
gg_row_at "$REPO_MAIN" "T-15 cd to a variable" ask:push_main_unresolved 'cd "$D" && git push'
gg_row_at "$T" "T-16 -C names the feature repository" silent "git -C $REPO_FEATURE push"
gg_row_at "$REPO_FEATURE" "T-17 --git-dir" ask:push_main_unresolved 'git --git-dir=.git push'
gg_row_at "$REPO_MAIN" "T-18 checkout -b topic, then HEAD" silent 'git checkout -b topic && git push -u origin HEAD'
gg_row_at "$REPO_FEATURE" "T-19 checkout -b main, then HEAD" ask:push_main_branch 'git checkout -b main && git push origin HEAD'
gg_row_at "$REPO_FEATURE" "T-20 checkout main, then a bare push" ask:push_main_unresolved 'git checkout main && git push'
gg_row_at "$REPO_FEATURE" "T-21 git config, then a bare push" ask:push_main_unresolved 'git config push.default matching && git push'
gg_row_at "$REPO_FEATURE" "T-22 gh pr checkout, then a bare push" ask:push_main_unresolved 'gh pr checkout 1 && git push'
GG_MAIN=off
gg_row_at "$DETACHED" "T-24 push_to_main=off, detached" silent 'git push origin HEAD'
GG_MAIN=ask
gg_row_at "$MAPPED_FULL" "T-25 a full-ref mapping with a named branch (a stated limit)" silent 'git push origin feature'
gg_row_at "$MAPPED" "T-25b a short mapping with a named branch" silent 'git push origin feature'
gg_row_at "$MAPPED" "T-25c a short mapping with no refspec" ask:push_config_remap 'git push origin'
gg_row_at "$REPO_MAIN" "T-26 a bare push in a subshell" ask:push_unreadable_word '(git push)'
gg_row_at "$REPO_FEATURE" "T-27 cd to main-repo in a subshell" ask:push_unreadable_word '(cd ../main-repo && git push)'
gg_row_at "$BARE_TOO" "T-28 --bare in a checkout that holds bare files" ask:push_main_unresolved 'git --bare push origin HEAD'
gg_row_at "$REPO_FEATURE" "T-29 an earlier bash -c" ask:push_main_unresolved "bash -c 'git checkout main' && git push origin HEAD"
gg_row_at "$REPO_FEATURE" "T-29b an earlier interpreter with no flag" ask:push_main_unresolved 'python3 tools/switch.py && git push'
gg_row_at "$REPO_FEATURE" "T-29c an earlier npm test (a stated limit)" silent 'npm test && git push'
gg_row_at "$REPO_FEATURE" "T-29d a named branch after a shell script" silent 'bash scripts/test.sh && git push origin feature'
gg_row_at "$REPO_MAIN" "T-30 head in lower case on main" ask:push_main_branch 'git push origin head'
gg_row_at "$REPO_FEATURE" "T-30b head in lower case on feature" silent 'git push origin head'
gg_row_at "$REPO_FEATURE" "T-31 GIT_DIR assigned and exported" ask:push_git_environment 'GIT_DIR=/x; export GIT_DIR; git push'
gg_row_at "$CURRENT" "T-32 push.default=current" silent 'git push'
gg_row_at "$UPSTREAM" "T-33 push.default=upstream" ask:push_config_remap 'git push'
gg_row_at "$REPO_MASTER" "T-34 a bare push from master" ask:push_main_branch 'git push'
gg_row_at "$REPO_FEATURE" "T-35 --tags alone reads as no refspec" silent 'git push --tags'
gg_row_at "$REPO_MAIN" "T-36 --tags alone on main" ask:push_main_branch 'git push --tags origin'
gg_row_at "$REPO_FEATURE" "T-37 -C to main-repo" ask:push_main_branch 'git -C ../main-repo push'
gg_row_at "$REPO_FEATURE" "T-38 a non-literal -C" ask:push_main_unresolved 'git -C "$R" push'
gg_row_at "$REPO_FEATURE" "T-39 checkout -b with ; before the push" ask:push_main_unresolved 'git checkout -b topic; git push origin HEAD'
gg_row_at "$REPO_FEATURE" "T-40 an earlier safe git subcommand" silent 'git fetch && git push'
gg_row_at "$REPO_FEATURE" "T-41 an earlier switch" ask:push_main_unresolved 'git switch main && git push'
gg_row_at "$REPO_FEATURE" "T-42 popd" ask:push_main_unresolved 'popd && git push'
gg_row_at "$REPO_FEATURE" "T-43 an earlier eval" ask:push_beside_eval "eval 'git checkout main' && git push"
gg_row_at "$REPO_FEATURE" "T-44 an earlier xargs" ask:push_main_unresolved 'echo main | xargs git checkout && git push'
gg_row_at "$REPO_MAIN" "T-45 cd then checkout -b in the new folder" silent 'cd ../feature-repo && git checkout -b topic && git push origin HEAD'
gg_row_at "$REPO_FEATURE" "T-46 checkout -b, then -C elsewhere" ask:push_main_unresolved 'git checkout -b topic && git -C ../main-repo push origin HEAD'
gg_row_at "$REPO_FEATURE" "T-47 an earlier checkout through a backslashed git word" ask:push_main_unresolved 'g\it checkout main && git push'
gg_row_at "$REPO_MAIN" "T-48 cd behind an or-list (true ||)" ask:push_main_unresolved 'true || cd ../feature-repo && git push'
gg_row_at "$REPO_MAIN" "T-49 cd behind an or-list (a test ||)" ask:push_main_unresolved '[ -d x ] || cd ../feature-repo && git push'
gg_row_at "$REPO_MAIN" "T-50 checkout -b behind an or-list, then HEAD" ask:push_main_unresolved 'true || git checkout -b topic && git push origin HEAD'
gg_row_at "$REPO_MAIN" "T-51 checkout -b behind an or-list, then -u HEAD" ask:push_main_unresolved 'true || git checkout -b topic && git push -u origin HEAD'
gg_row_at "$REPO_MAIN" "T-52 cd behind an or-list, then -u HEAD" ask:push_main_unresolved 'true || cd ../feature-repo && git push -u origin HEAD'
gg_row_at "$REPO_MAIN" "T-53 pushd behind an or-list" ask:push_main_unresolved 'true || pushd ../feature-repo && git push'
gg_row_at "$REPO_MAIN" "T-54 switch -c behind an or-list, then HEAD" ask:push_main_unresolved 'true || git switch -c topic && git push origin HEAD'
gg_row_at "$REPO_MAIN" "T-55 a ; before an && chain with a cd (the whole text must be one && chain)" ask:push_main_unresolved 'echo hi; cd ../feature-repo && git push'
gg_row_at "$REPO_MAIN" "T-56 cd in a subshell that closes before the push, then HEAD" ask:push_main_unresolved '(cd ../feature-repo && true) && git push origin HEAD'
gg_row_at "$REPO_MAIN" "T-57 cd in a subshell that closes before a bare push" ask:push_main_unresolved '(cd ../feature-repo && true) && git push'
gg_row_at "$REPO_MAIN" "T-58 cd in a subshell with make, then a bare push" ask:push_main_unresolved '(cd ../feature-repo && make) && git push'
gg_row_at "$REPO_MAIN" "T-59 cd in a spaced subshell that closes before the push" ask:push_main_unresolved '( cd ../feature-repo && true ) && git push'
gg_row_at "$REPO_MAIN" "T-60 checkout -b in a subshell that closes before the push" ask:push_main_unresolved '(git checkout -b topic && true) && git push origin HEAD'
gg_row_at "$REPO_MAIN" "T-61 cd and the push in one brace group" silent '{ cd ../feature-repo && git push; }'

printf '%s\n' '== Rows moved from the source suite (GT:<line> is the source line) =='
GG_COMMIT=off
gg_row_at "$REPO_MASTER" $'GT:2019 ceiling: a bare tracking \'git push\' is not parsed by gate 2 — current hook (from REPO_MASTER)' ask $'git push'
gg_row_at "$REPO_FEATURE" $'GT:2019 ceiling: a bare tracking \'git push\' is not parsed by gate 2 — current hook (from REPO_FEATURE)' silent $'git push'
GG_COMMIT=off
GG_MAIN=ask
GG_FORCE=ask

gg_finish TARGET
