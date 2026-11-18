#!/bin/bash
# A session whose working folder is in no git repository at all (a workspace
# folder that deliberately is not a repo) is not refused at context setup.
# The guard judges such a command on its literal absolute rm targets alone:
#   - anything strictly below the session scratchpad, outside every git
#     repository and worktree, is accepted (silent at the adapter) with the
#     usual r/R/f flags;
#   - a file or directory below the home folder's work folder is refused: that
#     acceptance was one person's folder layout and is removed, so these rows
#     stay as regressions of its removal;
#   - a path inside a repository or a worktree (registered lane roots
#     included), a path outside the scratchpad, a variable-derived path and a
#     home-relative spelling keep their refusals; a home-relative spelling,
#     quoted or not, is refused as unsafe_rm;
#   - a relative target, and any worktree removal that needs the cwd's
#     worktree, are refused with reason cwd_outside_worktree;
#   - a relative, non-canonical or missing cwd, or one inside a repository
#     that is not a worktree, is still invalid_cwd.
# $HOME points at a fixture; the real home folder is never consulted. Every
# command string is inert JSON data and is never executed. git comes only
# from the guard's trusted roots, never from PATH, so the git-absent and
# git-declines rows run copies of the guard whose roots line names a fixture
# folder.
set -u
LC_ALL=C

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUARD="${WORKSPACE_CWD_TEST_GUARD:-$HERE/../hooks/destructive-command-guard.sh}"
JQ="$(command -v jq)" || { printf 'FAIL: jq is not on PATH\n'; exit 1; }

TMP_BASE="$(mktemp -d "$(cd /tmp && pwd -P)/guard-workspace-cwd.XXXXXX")" || exit 1
TMP="$(cd "$TMP_BASE" && pwd -P)"
FIXHOME="$TMP/home"
WORK="$FIXHOME/work"
WORKSPACE="$TMP/workspace"
SCRATCH="$TMP/session-scratch"
REPO="$TMP/repo"
WORK_REPO="$WORK/some-repo"
LANE="$WORK/lanes/wt-lane"
SCRATCH_LANE="$SCRATCH/wt-lane"
OUTSIDE="$TMP/outside"
REG_DIR="$TMP/seat"
REG="$REG_DIR/lane-roots.tsv"
BARE="$WORK/bare.git"
BARE_PHRASE="$WORK/not a git repository.git"
NOGIT_BIN="$TMP/nogit-bin"
FAKEGIT_BIN="$TMP/fakegit-bin"

FAILURES=0
pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; FAILURES=$((FAILURES + 1)); }

git_q() { HOME="$TMP/githome" git -c user.name=t -c user.email=t@example.invalid "$@"; }
mkdir -p "$TMP/githome" "$WORKSPACE/sub" "$WORK/batch/dir" "$SCRATCH/cache" "$OUTSIDE" "$REG_DIR" "$WORK/lanes"
for repo in "$REPO" "$WORK_REPO"; do
  git_q init -q --initial-branch=main "$repo" || exit 1
  printf 'x\n' >"$repo/f.txt"
  git_q -C "$repo" add f.txt && git_q -C "$repo" commit -qm seed || exit 1
done
git_q -C "$REPO" worktree add -q --detach "$LANE" HEAD || exit 1
git_q -C "$REPO" worktree add -q --detach "$SCRATCH_LANE" HEAD || exit 1
printf 'x\n' >"$WORK/batch/file.md"
printf 'x\n' >"$WORK/batch/dir/inner.md"
printf 'x\n' >"$WORK_REPO/notes.md"
printf 'x\n' >"$SCRATCH/note.md"
printf 'x\n' >"$OUTSIDE/file.md"
printf 'x\n' >"$WORKSPACE/file.md"
printf 'x\n' >"$FIXHOME/file.md"
ln -s "$OUTSIDE" "$WORK/escape"
ln -s "$WORKSPACE" "$TMP/alias-workspace"
ln -s "$FIXHOME" "$TMP/alias-home"
git_q init -q --bare "$BARE" || exit 1
git_q init -q --bare "$BARE_PHRASE" || exit 1
mkdir -p "$BARE/info"
printf 'x\n' >"$BARE/info/lb5.txt"

# A folder with every system tool except git; a copy of the guard whose only
# trusted root it is runs with git absent.
mkdir -p "$NOGIT_BIN" "$FAKEGIT_BIN"
for bindir in /usr/bin /bin /usr/sbin /sbin; do
  for tool in "$bindir"/*; do
    name="${tool##*/}"
    [ "$name" != git ] || continue
    [ -e "$NOGIT_BIN/$name" ] || ln -s "$tool" "$NOGIT_BIN/$name"
  done
done
# A git that runs the real one, except that inside either bare repository it
# fails the way git does when it declines a repository (dubious ownership,
# with the path quoted in the message): the guard must not read that failure
# as "not a repository", even when the quoted path contains that phrase.
REAL_GIT="$(command -v git)"
{
  printf '%s\n' '#!/bin/bash'
  printf 'case "$2" in %q|%q/*|%q|%q/*) printf "fatal: detected dubious ownership in repository at %%s\\n" "$2" >&2; exit 128 ;; esac\n' "$BARE" "$BARE" "$BARE_PHRASE" "$BARE_PHRASE"
  printf 'exec %s "$@"\n' "$REAL_GIT"
} >"$FAKEGIT_BIN/git"
chmod +x "$FAKEGIT_BIN/git"
for tool in jq wc tr grep; do ln -s "$(command -v "$tool")" "$FAKEGIT_BIN/$tool" || exit 1; done
# roots_copy DEST DIR...: a copy of the guard whose TRUSTED_ROOTS line names
# DIR...; fails the suite when the rewrite changes nothing.
roots_copy() {
  local dst="$1" line
  shift
  line="TRUSTED_ROOTS=($*)"
  sed "s|^TRUSTED_ROOTS=.*|$line|" "$GUARD" >"$dst" || exit 1
  if ! grep -qxF "$line" "$dst" || cmp -s "$GUARD" "$dst"; then
    fail "roots fixture: $dst has no TRUSTED_ROOTS line to rewrite"
    exit 1
  fi
}
roots_copy "$TMP/guard-no-git.sh" "$NOGIT_BIN"
roots_copy "$TMP/guard-fake-git.sh" "$FAKEGIT_BIN"

# A trusted lane-root registry naming both linked worktrees, the shape the
# launcher writes (header, private file in a private directory).
{
  printf '%s\n' $'path\trepo\tbranch\tcreated_at'
  printf '%s\t%s\t%s\t%s\n' "$LANE" "$REPO" "(detached)" "2026-10-27T00:00:00Z"
  printf '%s\t%s\t%s\t%s\n' "$SCRATCH_LANE" "$REPO" "(detached)" "2026-10-27T00:00:00Z"
} >"$REG"
chmod 600 "$REG"; chmod 700 "$REG_DIR"

# run_case LABEL EXPECT(deny|no_opinion) CWD COMMAND [REASON] [-- ENV=VAL ...]
# The guard file is RUN_GUARD (the shipped guard unless a row sets it).
RUN_GUARD="$GUARD"
run_case() {
  local label="$1" expect="$2" cwd="$3" command_data="$4" want_reason=""
  shift 4
  if [ "$#" -gt 0 ] && [ "$1" != "--" ]; then want_reason="$1"; shift; fi
  if [ "$#" -gt 0 ]; then shift; fi
  local input output decision reason
  input="$($JQ -cn --arg cwd "$cwd" --arg command "$command_data" '{cwd:$cwd,command:$command}')"
  output="$(cd "$TMP" && printf '%s' "$input" | env HOME="$FIXHOME" LANE_ROOTS_REGISTRY="$REG" ${1+"$@"} \
    /bin/bash --noprofile --norc -p "$RUN_GUARD" --policy --scratchpad "$SCRATCH" 2>/dev/null)"
  decision="$(printf '%s' "$output" | "$JQ" -er '.decision' 2>/dev/null || printf invalid)"
  reason="$(printf '%s' "$output" | "$JQ" -er '.reason' 2>/dev/null || printf invalid)"
  if [ "$decision" = "$expect" ] && { [ -z "$want_reason" ] || [ "$reason" = "$want_reason" ]; }; then
    pass "$label -> $decision/$reason"
  else
    fail "$label: expected $expect${want_reason:+/$want_reason}, got $decision/$reason"
  fi
}

printf '%s\n' '== fixture: the workspace cwd is in no repository =='
if git -C "$WORKSPACE" rev-parse --git-dir >/dev/null 2>&1; then
  fail "fixture workspace is inside a git repository; the cases below would not test a workspace cwd"
else
  pass "fixture workspace is in no git repository"
fi

printf '%s\n' '== accepted: literal absolute targets below the scratchpad; the work folder is refused =='
run_case "file below the home work folder (the removed work-folder rule)" deny "$WORKSPACE" "rm -f $WORK/batch/file.md" unsafe_rm
run_case "file below the home work folder, from a workspace subfolder (the removed rule)" deny "$WORKSPACE/sub" "rm -f $WORK/batch/file.md" unsafe_rm
run_case "file below the session scratchpad" no_opinion "$WORKSPACE" "rm -f $SCRATCH/note.md" accepted
run_case "directory below the session scratchpad (the scratchpad rule)" no_opinion "$WORKSPACE" "rm -rf $SCRATCH/cache" accepted
run_case "a work-folder file beside a scratchpad file (the removed rule)" deny "$WORKSPACE" "rm -f $WORK/batch/file.md $SCRATCH/note.md" unsafe_rm

printf '%s\n' '== refused: the rules an in-worktree cwd already applies =='
run_case "regular file below the work folder with -rf" deny "$WORKSPACE" "rm -rf $WORK/batch/file.md" unsafe_rm
run_case "regular file below the work folder with -fr" deny "$WORKSPACE" "rm -fr $WORK/batch/file.md" unsafe_rm
run_case "regular file below the work folder with -R -f" deny "$WORKSPACE" "rm -R -f $WORK/batch/file.md" unsafe_rm
run_case "regular file below the work folder with --recursive -f" deny "$WORKSPACE" "rm --recursive -f $WORK/batch/file.md" unsafe_rm
run_case "regular file below the work folder with -r after the target" deny "$WORKSPACE" "rm -f $WORK/batch/file.md -r" unsafe_rm
run_case "file inside a bare repository below the work folder" deny "$WORKSPACE" "rm -f $BARE/HEAD" unsafe_rm
run_case "bare rm with no r/R/f flag (same rule as from a worktree)" deny "$WORKSPACE" "rm $WORK/batch/file.md" unsafe_rm
run_case "directory below the work folder" deny "$WORKSPACE" "rm -rf $WORK/batch/dir" unsafe_rm
run_case "the work folder itself" deny "$WORKSPACE" "rm -rf $WORK" unsafe_rm
run_case "a missing file below the work folder" deny "$WORKSPACE" "rm -f $WORK/batch/missing.md" unsafe_rm
run_case "file reached through a symlink below the work folder" deny "$WORKSPACE" "rm -f $WORK/escape/file.md" unsafe_rm
run_case "file in the home folder outside work" deny "$WORKSPACE" "rm -f $FIXHOME/file.md" unsafe_rm
run_case "file outside both roots" deny "$WORKSPACE" "rm -f $OUTSIDE/file.md" unsafe_rm
run_case "the scratchpad root" deny "$WORKSPACE" "rm -rf $SCRATCH" unsafe_rm
run_case "registered lane root below the work folder" deny "$WORKSPACE" "rm -rf $LANE" unsafe_rm
run_case "registered lane root below the scratchpad" deny "$WORKSPACE" "rm -rf $SCRATCH_LANE" unsafe_rm
run_case "file inside a registered lane root below the scratchpad" deny "$WORKSPACE" "rm -f $SCRATCH_LANE/f.txt" unsafe_rm
run_case "file inside a git repository below the work folder" deny "$WORKSPACE" "rm -f $WORK_REPO/notes.md" unsafe_rm
run_case "file inside a git repository elsewhere" deny "$WORKSPACE" "rm -f $REPO/f.txt" unsafe_rm
run_case "a .git path" deny "$WORKSPACE" "rm -rf $WORK_REPO/.git" unsafe_rm
run_case "a dot-dot spelling" deny "$WORKSPACE" "rm -f $WORK/batch/../batch/file.md" unsafe_rm
run_case "variable-derived path" deny "$WORKSPACE" 'rm -f "$X/file"' unsupported_syntax
run_case "variable-derived path with no flag" deny "$WORKSPACE" 'rm "$X/file"' unsupported_syntax
# A home spelling the guard does not expand denies as unsafe_rm, as its quoted
# spelling on the next line does.
run_case "home-relative path" deny "$WORKSPACE" 'rm -f ~/notes/batch/file.md' unsafe_rm
run_case "quoted home-relative path" deny "$WORKSPACE" "rm -f '~/notes/batch/file.md'" unsafe_rm
run_case "quoted \$HOME path" deny "$WORKSPACE" "rm -f '\$HOME/notes/batch/file.md'" unsafe_rm
run_case "the root folder" deny "$WORKSPACE" "rm -rf /" unsafe_rm

printf '%s\n' '== refused with cwd_outside_worktree: rules that need a worktree =='
run_case "relative file" deny "$WORKSPACE" "rm file.md" cwd_outside_worktree
run_case "relative file with -f" deny "$WORKSPACE" "rm -f file.md" cwd_outside_worktree
run_case "relative directory with -rf" deny "$WORKSPACE" "rm -rf sub" cwd_outside_worktree
run_case "relative file from a cwd inside the work folder" deny "$WORK/batch" "rm -f file.md" cwd_outside_worktree
run_case "dot-slash relative path" deny "$WORKSPACE" "rm -f ./file.md" cwd_outside_worktree
run_case "relative path after an eligible absolute one" deny "$WORKSPACE" "rm -f $SCRATCH/note.md file.md" cwd_outside_worktree
run_case "worktree removal relative to the cwd's repository" deny "$WORKSPACE" "git worktree remove $LANE" cwd_outside_worktree

printf '%s\n' '== the registry rule still judges a removal that names its repository =='
run_case "git -C <repo> worktree remove of a registered lane with no identity rows" deny "$WORKSPACE" "git -C $REPO worktree remove $LANE" unsafe_worktree_remove

printf '%s\n' '== invalid_cwd is unchanged for a bad cwd =='
run_case "relative cwd" deny "workspace" "rm -f $WORK/batch/file.md" invalid_cwd
run_case "non-canonical cwd (symlinked)" deny "$TMP/alias-workspace" "rm -f $WORK/batch/file.md" invalid_cwd
run_case "missing cwd" deny "$TMP/no-such-folder" "rm -f $WORK/batch/file.md" invalid_cwd
run_case "cwd inside a git directory" deny "$REPO/.git" "rm -f $WORK/batch/file.md" invalid_cwd
run_case "cwd inside a git directory's subfolder" deny "$REPO/.git/refs" "rm -f $WORK/batch/file.md" invalid_cwd
run_case "cwd is a bare repository" deny "$BARE" "rm -f $WORK/batch/file.md" invalid_cwd

printf '%s\n' '== git missing or failing is not read as "outside a repository" =='
RUN_GUARD="$TMP/guard-no-git.sh"
run_case "git in no trusted root: workspace cwd is refused, naming git" deny "$WORKSPACE" "rm -f $SCRATCH/note.md" dependency_missing_git
run_case "git in no trusted root: target inside a bare repository is refused, naming git" deny "$WORKSPACE" "rm -f $BARE/HEAD" dependency_missing_git
RUN_GUARD="$TMP/guard-fake-git.sh"
run_case "git declines the bare repository: target inside it is refused" deny "$WORKSPACE" "rm -f $BARE/HEAD" unsafe_rm
run_case "git declines the bare repository: an eligible scratchpad file still passes" no_opinion "$WORKSPACE" "rm -f $SCRATCH/note.md" accepted
run_case "git declines a bare repository whose path holds the phrase 'not a git repository': target refused" deny "$WORKSPACE" "rm -f '$BARE_PHRASE/HEAD'" unsafe_rm
RUN_GUARD="$GUARD"

printf '%s\n' '== inherited git repository selectors deny every judged removal =='
run_case "GIT_DIR unset: target inside a bare repository is refused" deny "$WORKSPACE" "rm -f $BARE/HEAD" unsafe_rm
run_case "GIT_DIR=/missing.git: workspace cwd is refused" deny "$WORKSPACE" "rm -f $SCRATCH/note.md" inherited_git_environment -- GIT_DIR=/missing.git
run_case "GIT_DIR=/missing.git: target inside a bare repository is refused" deny "$WORKSPACE" "rm -f $BARE/HEAD" inherited_git_environment -- GIT_DIR=/missing.git
run_case "GIT_WORK_TREE set: workspace cwd is refused" deny "$WORKSPACE" "rm -f $SCRATCH/note.md" inherited_git_environment -- GIT_WORK_TREE="$OUTSIDE"
run_case "GIT_OBJECT_DIRECTORY=/missing: target inside a bare repository is refused" deny "$WORKSPACE" "rm -f $BARE/HEAD" inherited_git_environment -- GIT_OBJECT_DIRECTORY=/missing
run_case "GIT_COMMON_DIR=/missing: target inside a bare repository is refused" deny "$WORKSPACE" "rm -f $BARE/HEAD" inherited_git_environment -- GIT_COMMON_DIR=/missing
run_case "GIT_CEILING_DIRECTORIES at the bare repository: file in its subfolder is refused" deny "$WORKSPACE" "rm -f $BARE/info/lb5.txt" inherited_git_environment -- GIT_CEILING_DIRECTORIES="$BARE"
run_case "GIT_OBJECT_DIRECTORY=/missing: an eligible scratchpad file is refused too" deny "$WORKSPACE" "rm -f $SCRATCH/note.md" inherited_git_environment -- GIT_OBJECT_DIRECTORY=/missing

printf '%s\n' '== the recursive flag is judged per rm command =='
run_case "recursive scratchpad rm, then a -f work-folder file rm (the removed rule)" deny "$WORKSPACE" "rm -rf $SCRATCH/cache; rm -f $WORK/batch/file.md" unsafe_rm

printf '%s\n' '== the work-folder rule is not offered to an in-worktree cwd =='
run_case "in-worktree cwd, absolute home work file" deny "$REPO" "rm -f $WORK/batch/file.md" unsafe_rm

printf '%s\n' '== HOME that cannot anchor the work folder =='
run_case "relative HOME" deny "$WORKSPACE" "rm -f $WORK/batch/file.md" unsafe_rm -- HOME=home
run_case "HOME with a dot-dot component" deny "$WORKSPACE" "rm -f $WORK/batch/file.md" unsafe_rm -- HOME="$FIXHOME/../home"
run_case "HOME reached through a symlink" deny "$WORKSPACE" "rm -f $WORK/batch/file.md" unsafe_rm -- HOME="$TMP/alias-home"
run_case "HOME is the root folder" deny "$WORKSPACE" "rm -f $WORK/batch/file.md" unsafe_rm -- HOME=/

if [ "$FAILURES" -eq 0 ]; then
  printf 'ALL DESTRUCTIVE GUARD WORKSPACE-CWD TESTS PASSED\n'
  exit 0
fi
printf '%s TEST(S) FAILED\n' "$FAILURES"
exit 1
