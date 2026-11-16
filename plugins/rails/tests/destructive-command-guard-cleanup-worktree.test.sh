#!/bin/bash
# Seat-bound cleanup worktree admission and its required mutation audit.
# Guard inputs are inert JSON; every repository and registry is under mktemp.
set -u
LC_ALL=C

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
GUARD="$HERE/../hooks/destructive-command-guard.sh"
JQ="$(command -v jq)" || { printf 'FAIL: jq is not on PATH\n'; exit 1; }
TMP_BASE="$(mktemp -d)" || exit 1
TMP="$(cd "$TMP_BASE" && pwd -P)"
PRIMARY="$TMP/primary"
NONREPO="$TMP/nonrepo"
GITHOME="$TMP/home"
REG_DIR="$TMP/seat/scratch"
REG="$REG_DIR/cleanup-items.tsv"
MARKER="$TMP/fsmonitor-ran"
NO_TEMP_GUARD="$TMP/guard-no-temp-roots.sh"
sed 's|^TEMP_ROOTS=.*|TEMP_ROOTS=()|' "$GUARD" >"$NO_TEMP_GUARD" || exit 1
grep -q '^TEMP_ROOTS=()$' "$NO_TEMP_GUARD" || exit 1
FAILURES=0

cleanup_fixture() {
  chmod -R u+rwx "$TMP_BASE" 2>/dev/null || :
  rm -rf "$TMP_BASE"
}
trap cleanup_fixture EXIT

pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; FAILURES=$((FAILURES + 1)); }
git_q() {
  HOME="$GITHOME" GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
    git -c user.name=t -c user.email=t@example.invalid "$@"
}

mkdir -p "$PRIMARY" "$NONREPO" "$GITHOME" "$REG_DIR"
chmod 700 "$REG_DIR"
git_q -C "$PRIMARY" init -q --initial-branch=main || exit 1
cat >"$PRIMARY/.gitignore" <<'EOF'
__pycache__/
.pytest_cache/
*.pyc
.env
EOF
mkdir -p "$PRIMARY/sub"
printf 'base\n' >"$PRIMARY/f.txt"
printf 'tracked outer data\n' >"$PRIMARY/sub/keep.txt"
git_q -C "$PRIMARY" add .gitignore f.txt sub/keep.txt || exit 1
git_q -C "$PRIMARY" commit -qm base || exit 1

WT_CLEAN="$TMP/trees/clean"
WT_REMOVE="$TMP/trees/remove-end-to-end"
WT_SWAP="$TMP/trees/swapped"
WT_OTHER="$TMP/trees/other-admin"
WT_PYCACHE="$TMP/trees/pycache"
WT_PYC_DIR_DATA="$TMP/trees/pyc-dir-data"
WT_PYC_DIR_PLAIN="$TMP/trees/pyc-dir-plain"
WT_PYC_SYMLINK="$TMP/trees/pyc-symlink"
WT_BARE_CACHE="$TMP/trees/bare-cache"
WT_DOTGIT_CACHE="$TMP/trees/dotgit-cache"
WT_HEADLESS_CACHE="$TMP/trees/headless-cache"
WT_ENV="$TMP/trees/env"
WT_NESTED_REPO="$TMP/trees/nested-repo"
WT_DOTGIT_DATA="$TMP/trees/dotgit-data"
WT_DOTGIT_CASE="$TMP/trees/dotgit-case"
WT_IGNORED_REPO="$TMP/trees/ignored-repo"
WT_BRANCH="$TMP/trees/local-branch"
WT_FILTER="$TMP/trees/filter-driver"
WT_MODE="$TMP/trees/mode-change"
WT_REMOTE="$TMP/trees/remote-detached"
WT_UNPUSHED="$TMP/trees/unpushed-detached"
WT_BISECT="$TMP/trees/bisect"
WT_LOCKED="$TMP/trees/locked"
WT_UNREGISTERED="$TMP/trees/unregistered"
WT_CWD="$TMP/trees/b"
WT_LINK_CLEAN="$TMP/trees/symlink-clean"
WT_LINK_RETARGET="$TMP/trees/symlink-retarget"
WT_LINK_FILE="$TMP/trees/symlink-file"
mkdir -p "$TMP/trees"
for wt in "$WT_CLEAN" "$WT_REMOVE" "$WT_SWAP" "$WT_OTHER" "$WT_PYCACHE" \
  "$WT_PYC_DIR_DATA" "$WT_PYC_DIR_PLAIN" "$WT_PYC_SYMLINK" "$WT_BARE_CACHE" \
  "$WT_DOTGIT_CACHE" "$WT_HEADLESS_CACHE" "$WT_NESTED_REPO" \
  "$WT_DOTGIT_DATA" "$WT_DOTGIT_CASE" "$WT_IGNORED_REPO" "$WT_ENV" \
  "$WT_MODE" "$WT_REMOTE" "$WT_UNPUSHED" "$WT_BISECT" "$WT_LOCKED" "$WT_UNREGISTERED" \
  "$WT_CWD"; do
  git_q -C "$PRIMARY" worktree add -q --detach "$wt" HEAD || exit 1
done
git_q -C "$PRIMARY" worktree add -q -b cleanup-local "$WT_BRANCH" HEAD || exit 1
printf 'survives only on the local branch\n' >"$WT_BRANCH/branch-only.txt"
git_q -C "$WT_BRANCH" add branch-only.txt || exit 1
git_q -C "$WT_BRANCH" commit -qm branch-only || exit 1
git_q -C "$PRIMARY" worktree add -q -b cleanup-filter "$WT_FILTER" HEAD || exit 1
printf 'victim filter=evil\n' >"$WT_FILTER/.gitattributes"
printf 'base\n' >"$WT_FILTER/victim"
git_q -C "$WT_FILTER" add .gitattributes victim || exit 1
git_q -C "$WT_FILTER" commit -qm filter-fixture || exit 1
printf 'evil\n' >"$WT_FILTER/victim"
mkdir -p "$WT_FILTER/.pytest_cache"
cat >"$WT_FILTER/.pytest_cache/filter.sh" <<EOF
#!/bin/bash
printf ran >"$TMP/filter-ran"
/bin/cat >/dev/null
printf 'base\\n'
EOF
chmod 700 "$WT_FILTER/.pytest_cache/filter.sh"
chmod 755 "$WT_MODE/f.txt"
git_q -C "$PRIMARY" worktree add -q -b cleanup-symlink "$WT_LINK_CLEAN" HEAD || exit 1
ln -s f.txt "$WT_LINK_CLEAN/link"
git_q -C "$WT_LINK_CLEAN" add link || exit 1
git_q -C "$WT_LINK_CLEAN" commit -qm tracked-symlink || exit 1
git_q -C "$PRIMARY" worktree add -q --detach "$WT_LINK_RETARGET" cleanup-symlink || exit 1
git_q -C "$PRIMARY" worktree add -q --detach "$WT_LINK_FILE" cleanup-symlink || exit 1
rm -f "$WT_LINK_RETARGET/link" "$WT_LINK_FILE/link"
ln -s sub/keep.txt "$WT_LINK_RETARGET/link"
# The regular file holds the exact link text, so only the file-type check can
# tell it from the committed symlink.
printf '%s' f.txt >"$WT_LINK_FILE/link"

printf 'cache bytecode\n' >"$WT_PYCACHE/x.pyc"
mkdir -p "$WT_PYCACHE/pkg/__pycache__"
printf 'cache bytecode\n' >"$WT_PYCACHE/pkg/__pycache__/module.cpython-313.pyc"
mkdir -p "$WT_PYC_DIR_DATA/stash.pyc" "$WT_PYC_DIR_PLAIN/plain.pyc"
printf 'ignored data\n' >"$WT_PYC_DIR_DATA/stash.pyc/notes.txt"
printf 'plain one\n' >"$WT_PYC_DIR_PLAIN/plain.pyc/one.txt"
printf 'plain two\n' >"$WT_PYC_DIR_PLAIN/plain.pyc/two.txt"
printf 'symlink target\n' >"$TMP/pyc-target"
ln -s "$TMP/pyc-target" "$WT_PYC_SYMLINK/x.pyc"

mkdir -p "$WT_BARE_CACHE/.pytest_cache/repo.git" "$TMP/bare-refs" "$TMP/bare-objects"
printf 'ref: refs/heads/main\n' >"$WT_BARE_CACHE/.pytest_cache/repo.git/HEAD"
ln -s "$TMP/bare-refs" "$WT_BARE_CACHE/.pytest_cache/repo.git/refs"
ln -s "$TMP/bare-objects" "$WT_BARE_CACHE/.pytest_cache/repo.git/objects"

mkdir -p "$WT_DOTGIT_CACHE/__pycache__/.GIT"
printf 'marker\n' >"$WT_DOTGIT_CACHE/__pycache__/.GIT/config"
mkdir -p "$WT_HEADLESS_CACHE/.pytest_cache/store/objects" "$WT_HEADLESS_CACHE/.pytest_cache/store/refs"
git_q -C "$WT_NESTED_REPO/sub" init -q --initial-branch=main || exit 1
git_q -C "$WT_NESTED_REPO/sub" add keep.txt || exit 1
git_q -C "$WT_NESTED_REPO/sub" commit -qm nested-unpushed || exit 1
mkdir -p "$WT_DOTGIT_DATA/sub/.git" "$WT_DOTGIT_CASE/sub/.GIT"
printf 'plain data\n' >"$WT_DOTGIT_DATA/sub/.git/notes.txt"
printf 'case-folded data\n' >"$WT_DOTGIT_CASE/sub/.GIT/notes.txt"
git_q init --bare -q "$WT_IGNORED_REPO/stash.pyc" || exit 1
printf 'ignored secret\n' >"$WT_ENV/.env"

git_q -C "$PRIMARY" update-ref refs/remotes/origin/x HEAD || exit 1
printf 'unpushed\n' >>"$WT_UNPUSHED/f.txt"
git_q -C "$WT_UNPUSHED" add f.txt || exit 1
git_q -C "$WT_UNPUSHED" commit -qm unpushed || exit 1
git_q -C "$WT_BISECT" update-ref refs/bisect/test HEAD || exit 1
git_q -C "$PRIMARY" worktree lock --reason fixture "$WT_LOCKED" || exit 1

HEADER=$'repo\tpath\tkind\tseat\trow\tcreated_at'
row() { printf '%s\t%s\tworktree\t%s\tCLN1\t2026-10-28T00:00:00Z\n' "$PRIMARY" "$1" "${2:-seat-test}"; }
write_registry() {
  {
    printf '%s\n' "$HEADER"
    row "$WT_CLEAN"
    row "$WT_REMOVE"
    row "$WT_SWAP"
    row "$WT_OTHER"
    row "$WT_PYCACHE"
    row "$WT_PYC_DIR_DATA"
    row "$WT_PYC_DIR_PLAIN"
    row "$WT_PYC_SYMLINK"
    row "$WT_BARE_CACHE"
    row "$WT_DOTGIT_CACHE"
    row "$WT_HEADLESS_CACHE"
    row "$WT_ENV"
    row "$WT_BRANCH"
    row "$WT_NESTED_REPO"
    row "$WT_DOTGIT_DATA"
    row "$WT_DOTGIT_CASE"
    row "$WT_IGNORED_REPO"
    row "$WT_FILTER"
    row "$WT_MODE"
    row "$WT_REMOTE"
    row "$WT_UNPUSHED"
    row "$WT_BISECT"
    row "$WT_LOCKED"
    row "$WT_CWD"
    row "$WT_LINK_CLEAN"
    row "$WT_LINK_RETARGET"
    row "$WT_LINK_FILE"
  } >"$REG"
  chmod 600 "$REG"
}
write_registry

LAST_OUTPUT=""
run_policy() { # GUARD CWD COMMAND [SEAT] [REGISTRY]
  local guard="$1" cwd="$2" command_data="$3" seat="${4-seat-test}" registry="${5-$REG}" input
  input="$($JQ -cn --arg cwd "$cwd" --arg command "$command_data" '{cwd:$cwd,command:$command}')"
  if [ "$seat" = __UNSET__ ]; then
    LAST_OUTPUT="$(printf '%s' "$input" | env -u CLEANUP_SEAT -u LANE_ROOTS_REGISTRY \
      CLEANUP_ITEMS_REGISTRY="$registry" HOME="$GITHOME" /bin/bash --noprofile --norc -p "$guard" --policy 2>/dev/null)"
  else
    LAST_OUTPUT="$(printf '%s' "$input" | env -u LANE_ROOTS_REGISTRY \
      CLEANUP_ITEMS_REGISTRY="$registry" CLEANUP_SEAT="$seat" HOME="$GITHOME" \
      /bin/bash --noprofile --norc -p "$guard" --policy 2>/dev/null)"
  fi
}

expect_policy() { # LABEL DECISION REASON GUARD CWD COMMAND [SEAT] [REGISTRY]
  local label="$1" decision="$2" reason="$3" guard="$4" cwd="$5" command_data="$6"
  local seat="${7-seat-test}" registry="${8-$REG}" want
  run_policy "$guard" "$cwd" "$command_data" "$seat" "$registry"
  want="{\"decision\":\"$decision\",\"reason\":\"$reason\"}"
  if [ "$LAST_OUTPUT" = "$want" ]; then pass "$label -> $decision/$reason"; else fail "$label: got '$LAST_OUTPUT', want '$want'"; fi
}

cmd() { printf 'git -C %s worktree remove %s' "$PRIMARY" "$1"; }

printf '%s\n' '== exact shape, seat binding, and force refusal =='
expect_policy "registered worktree for this seat" no_opinion accepted "$GUARD" "$NONREPO" "$(cmd "$WT_CLEAN")"
expect_policy "end-to-end removal fixture" no_opinion accepted "$GUARD" "$NONREPO" "$(cmd "$WT_REMOVE")"
if git_q -C "$PRIMARY" worktree remove "$WT_REMOVE" && [ ! -e "$WT_REMOVE" ]; then
  pass "allowed command removes its clean fixture worktree"
else
  fail "allowed command did not remove its clean fixture worktree"
fi
expect_policy "row for another seat" deny unsafe_worktree_remove "$GUARD" "$NONREPO" "$(cmd "$WT_CLEAN")" seat-other
# An armed cleanup registry with no seat, or one that is not private, now
# denies by name for every judged removal (it only failed to authorize before).
expect_policy "CLEANUP_SEAT unset" deny cleanup_seat_missing "$GUARD" "$NONREPO" "$(cmd "$WT_CLEAN")" __UNSET__
expect_policy "unregistered worktree" deny unsafe_worktree_remove "$NO_TEMP_GUARD" "$NONREPO" "$(cmd "$WT_UNREGISTERED")"
chmod 644 "$REG"
expect_policy "0644 cleanup registry is untrusted" deny cleanup_registry_untrusted "$GUARD" "$NONREPO" "$(cmd "$WT_CLEAN")"
chmod 600 "$REG"
chmod 755 "$REG_DIR"
expect_policy "0755 cleanup registry directory is untrusted" deny cleanup_registry_untrusted "$GUARD" "$NONREPO" "$(cmd "$WT_CLEAN")"
chmod 700 "$REG_DIR"
ln -s "$PRIMARY" "$TMP/primary-alias"
expect_policy "symlinked repository spelling cannot bypass cleanup checks" deny unsafe_worktree_remove "$GUARD" "$NONREPO" \
  "git -C $TMP/primary-alias worktree remove $WT_ENV"
expect_policy "trailing-slash target cannot bypass cleanup checks" deny unsafe_worktree_remove "$GUARD" "$NONREPO" \
  "git -C $PRIMARY worktree remove $WT_ENV/"
expect_policy "--force before target" deny unsafe_worktree_remove "$GUARD" "$NONREPO" \
  "git -C $PRIMARY worktree remove --force $WT_CLEAN"
expect_policy "--force after target" deny unsafe_worktree_remove "$GUARD" "$NONREPO" \
  "git -C $PRIMARY worktree remove $WT_CLEAN --force"
expect_policy "short force flag" deny unsafe_worktree_remove "$GUARD" "$NONREPO" \
  "git -C $PRIMARY worktree remove -f $WT_CLEAN"
expect_policy "Git config prefix is not the exact shape" deny unsafe_worktree_remove "$GUARD" "$NONREPO" \
  "git -c core.fsmonitor=false -C $PRIMARY worktree remove $WT_CLEAN"
expect_policy "command wrapper is not the exact shape" deny unsafe_worktree_remove "$GUARD" "$NONREPO" \
  "command git -C $PRIMARY worktree remove $WT_CLEAN"
expect_policy "absolute Git executable is not the exact shape" deny unsafe_worktree_remove "$GUARD" "$NONREPO" \
  "/usr/bin/git -C $PRIMARY worktree remove $WT_CLEAN"
expect_policy "compound command is not the exact whole command" deny unsafe_worktree_remove "$GUARD" "$NONREPO" \
  "$(cmd "$WT_CLEAN") && echo done"

printf '%s\n' '== A1 gitdir binding and isolated Git =='
SWAP_ORIGINAL="$(cat "$WT_SWAP/.git")"
OTHER_ADMIN="$(git_q -C "$WT_OTHER" rev-parse --path-format=absolute --git-dir)"
printf '#!/bin/bash\nprintf marker >%q\nexit 0\n' "$MARKER" >"$TMP/fsmonitor.sh"
chmod 700 "$TMP/fsmonitor.sh"
git_q -C "$PRIMARY" config core.fsmonitor "$TMP/fsmonitor.sh" || exit 1
printf 'gitdir: %s\n' "$OTHER_ADMIN" >"$WT_SWAP/.git"
expect_policy "swapped gitfile with planted fsmonitor" deny unsafe_worktree_remove "$GUARD" "$NONREPO" "$(cmd "$WT_SWAP")"
if [ ! -e "$MARKER" ]; then pass "swapped gitfile did not execute fsmonitor"; else fail "swapped gitfile executed fsmonitor"; fi
expect_policy "gitfile pointing at another worktree admin" deny unsafe_worktree_remove "$GUARD" "$NONREPO" "$(cmd "$WT_SWAP")"
printf '%s\n' "$SWAP_ORIGINAL" >"$WT_SWAP/.git"
expect_policy "isolated Git ignores configured fsmonitor" no_opinion accepted "$GUARD" "$NONREPO" "$(cmd "$WT_CLEAN")"
if [ ! -e "$MARKER" ]; then pass "bound Git disabled fsmonitor"; else fail "bound Git executed fsmonitor"; fi
git_q -C "$PRIMARY" config --unset core.fsmonitor || exit 1

printf '%s\n' '== A2 narrow cache allowance =='
expect_policy "regular ignored x.pyc and __pycache__ files" no_opinion accepted "$GUARD" "$NONREPO" "$(cmd "$WT_PYCACHE")"
expect_policy "ignored stash.pyc/notes.txt data" deny unsafe_worktree_remove "$GUARD" "$NONREPO" "$(cmd "$WT_PYC_DIR_DATA")"
expect_policy "ignored pyc directory holding only plain files" deny unsafe_worktree_remove "$GUARD" "$NONREPO" "$(cmd "$WT_PYC_DIR_PLAIN")"
expect_policy "ignored x.pyc symlink" deny unsafe_worktree_remove "$GUARD" "$NONREPO" "$(cmd "$WT_PYC_SYMLINK")"
expect_policy "bare store in .pytest_cache with symlinked refs and objects" deny unsafe_worktree_remove "$GUARD" "$NONREPO" "$(cmd "$WT_BARE_CACHE")"
expect_policy "case-insensitive .GIT marker in cache" deny unsafe_worktree_remove "$GUARD" "$NONREPO" "$(cmd "$WT_DOTGIT_CACHE")"
expect_policy "HEAD-less object and refs store in cache" deny unsafe_worktree_remove "$GUARD" "$NONREPO" "$(cmd "$WT_HEADLESS_CACHE")"
expect_policy "ignored .env outside cache allowance" deny unsafe_worktree_remove "$GUARD" "$NONREPO" "$(cmd "$WT_ENV")"
expect_policy "nested repository with an unpushed commit" deny unsafe_worktree_remove "$GUARD" "$NONREPO" "$(cmd "$WT_NESTED_REPO")"
expect_policy "plain nested .git data directory" deny unsafe_worktree_remove "$GUARD" "$NONREPO" "$(cmd "$WT_DOTGIT_DATA")"
expect_policy "case-folded nested .GIT directory" deny unsafe_worktree_remove "$GUARD" "$NONREPO" "$(cmd "$WT_DOTGIT_CASE")"
expect_policy "ignored stash.pyc bare repository" deny unsafe_worktree_remove "$GUARD" "$NONREPO" "$(cmd "$WT_IGNORED_REPO")"

printf '%s\n' '== A3 surviving refs =='
# The remote ref points at the seed commit. The branch fixture has no tracking
# requirement; the detached remote fixture is retained by origin/x.
expect_policy "local branch tip survives after tracking ref prune" no_opinion accepted "$GUARD" "$NONREPO" "$(cmd "$WT_BRANCH")"
expect_policy "detached HEAD contained by origin/x" no_opinion accepted "$GUARD" "$NONREPO" "$(cmd "$WT_REMOTE")"
expect_policy "detached unpushed HEAD" deny unsafe_worktree_remove "$GUARD" "$NONREPO" "$(cmd "$WT_UNPUSHED")"
expect_policy "refs/bisect state" deny unsafe_worktree_remove "$GUARD" "$NONREPO" "$(cmd "$WT_BISECT")"

printf '%s\n' '== raw cleanliness ignores filter drivers =='
git_q -C "$PRIMARY" config filter.evil.clean "$WT_FILTER/.pytest_cache/filter.sh" || exit 1
rm -f "$TMP/filter-ran"
expect_policy "modified tracked file hidden by a clean filter" deny unsafe_worktree_remove "$GUARD" "$NONREPO" "$(cmd "$WT_FILTER")"
if [ ! -e "$TMP/filter-ran" ]; then pass "cleanliness check did not execute the filter driver"; else fail "cleanliness check executed the filter driver"; fi
expect_policy "tracked executable-bit change" deny unsafe_worktree_remove "$GUARD" "$NONREPO" "$(cmd "$WT_MODE")"
git_q -C "$PRIMARY" config --unset filter.evil.clean || exit 1

printf '%s\n' '== A4 lock and A5 workspace interplay =='
expect_policy "locked linked worktree" deny unsafe_worktree_remove "$GUARD" "$NONREPO" "$(cmd "$WT_LOCKED")"
expect_policy "registered clean tree from non-repository cwd" no_opinion accepted "$GUARD" "$NONREPO" "$(cmd "$WT_CLEAN")"
expect_policy "four-word form from non-repository cwd" deny cwd_outside_worktree "$GUARD" "$NONREPO" \
  "git worktree remove $WT_CLEAN"

printf '%s\n' '== the caller cannot remove its own tree =='
expect_policy "registered clean tree removed from its own root" deny unsafe_worktree_remove "$GUARD" "$WT_CWD" "$(cmd "$WT_CWD")"
expect_policy "registered clean tree removed from a subfolder of it" deny unsafe_worktree_remove "$GUARD" "$WT_CWD/sub" "$(cmd "$WT_CWD")"
# An inherited GIT_DIR is a repository selector: it denies before git runs.
# (The ancestor check it used to reach is covered by the row above.)
CWD_ADMIN="$(git_q -C "$WT_CWD" rev-parse --path-format=absolute --git-dir)"
SUB_INPUT="$($JQ -cn --arg cwd "$WT_CWD/sub" --arg command "$(cmd "$WT_CWD")" '{cwd:$cwd,command:$command}')"
LAST_OUTPUT="$(printf '%s' "$SUB_INPUT" | env -u LANE_ROOTS_REGISTRY GIT_DIR="$CWD_ADMIN" \
  CLEANUP_ITEMS_REGISTRY="$REG" CLEANUP_SEAT=seat-test HOME="$GITHOME" \
  /bin/bash --noprofile --norc -p "$GUARD" --policy 2>/dev/null)"
if [ "$LAST_OUTPUT" = '{"decision":"deny","reason":"inherited_git_environment"}' ]; then
  pass "subfolder of the target with an inherited GIT_DIR -> deny/inherited_git_environment"
else
  fail "subfolder of the target with an inherited GIT_DIR: got '$LAST_OUTPUT'"
fi
expect_policy "same registered tree from non-repository cwd" no_opinion accepted "$GUARD" "$NONREPO" "$(cmd "$WT_CWD")"

printf '%s\n' '== tracked symlinks compare their link text =='
expect_policy "clean tree with a tracked symlink" no_opinion accepted "$GUARD" "$NONREPO" "$(cmd "$WT_LINK_CLEAN")"
expect_policy "tracked symlink retargeted on disk" deny unsafe_worktree_remove "$GUARD" "$NONREPO" "$(cmd "$WT_LINK_RETARGET")"
expect_policy "tracked symlink replaced by a regular file" deny unsafe_worktree_remove "$GUARD" "$NONREPO" "$(cmd "$WT_LINK_FILE")"

printf '%s\n' '== required mutation audit =='
MUT_DIR="$TMP/mutations"
mkdir -p "$MUT_DIR"
python3 -I - "$GUARD" "$MUT_DIR" <<'PY'
import pathlib
import re
import sys

source = pathlib.Path(sys.argv[1]).read_text()
out = pathlib.Path(sys.argv[2])

def emit(name, text):
    if text == source:
        raise SystemExit(f"mutation {name} changed nothing")
    path = out / f"{name}.sh"
    path.write_text(text)
    path.chmod(0o700)

flags = re.sub(
    r'''    /usr/bin/env -u GIT_DIR -u GIT_WORK_TREE -u GIT_COMMON_DIR \\\n      -u GIT_INDEX_FILE -u GIT_OBJECT_DIRECTORY -u GIT_ALTERNATE_OBJECT_DIRECTORIES \\\n      -u GIT_NAMESPACE -u GIT_CEILING_DIRECTORIES -u GIT_CONFIG_PARAMETERS \\\n      -u GIT_CONFIG_COUNT -u GIT_CONFIG_SYSTEM -u GIT_CONFIG_GLOBAL \\\n      GIT_DIR="\$CLEANUP_GIT_DIR" GIT_WORK_TREE="\$CLEANUP_GIT_WORK_TREE" \\\n      GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null LC_ALL=C \\\n''',
    '''    /usr/bin/env GIT_DIR="$CLEANUP_GIT_DIR" GIT_WORK_TREE="$CLEANUP_GIT_WORK_TREE" LC_ALL=C \\\n''',
    source,
    count=1,
)
flags = flags.replace('        -c core.fsmonitor=false -c core.hooksPath=/dev/null "$@" 2>/dev/null', '        "$@" 2>/dev/null', 1)
emit('a1-drop-git-isolation', flags)
emit('a1-drop-gitdir-equality', source.replace('  [ "$candidate" = "$target/.git" ] || return 1\n', '', 1))
restore_ancestor_loop = source.replace(
    '  return 1\n}\n\ncleanup_nested_git_absent() {',
    '''  local ancestor="$path"
  ancestor="$path"
  while [[ "$ancestor" = */* ]]; do
    ancestor="${ancestor%/*}"
    case "$ancestor" in *.pyc) return 0 ;; esac
  done
  return 1
}

cleanup_nested_git_absent() {''',
    1,
)
emit('a2-drop-nested-git-scan', source.replace('  cleanup_nested_git_absent "$CLEANUP_GIT_WORK_TREE" || return 1\n', '  : # mutation skips the nested .git scan\n', 1))
emit('a2-restore-pyc-ancestor-loop', restore_ancestor_loop)
emit('a2-skip-ignored-path-scan', source.replace('  cleanup_cache_markers_absent "$CLEANUP_GIT_WORK_TREE" || return 1\n', '  : # mutation skips the ignored-path marker scan\n', 1))
emit('a3-drop-branch-tip-equality', source.replace('    [ "$line" = "$head" ] && return 0\n', '    [ -n "$line" ] && return 0\n', 1))
emit('a4-drop-lock-check', source.replace('  { [ ! -e "$admin/locked" ] && [ ! -L "$admin/locked" ]; } || return 1\n', '  : # mutation ignores the worktree lock\n', 1))
workspace = '''  if [ "${#WORDS[@]}" -eq 4 ] && [ -z "$WORKTREE_ROOT" ]; then
    policy_deny cwd_outside_worktree
  fi
'''
wrong_order = '''  # mutation: cleanup fallback runs before the workspace-only branch
  validate_registered_cleanup_worktree_remove "$raw_segment"
  return
'''
emit('a5-swap-branch-order', source.replace(workspace, wrong_order, 1))
PY
if [ "$?" -ne 0 ]; then
  fail "mutation generation"
  exit 1
fi

mutation_red() { # LABEL
  printf 'RED: %s\n' "$1"
  pass "$1 was caught"
}

ALLOW_JSON='{"decision":"no_opinion","reason":"accepted"}'
# A mutant is caught only when it returns the exact decision the dropped check
# exists to prevent. A missing or crashing mutant prints nothing and fails.
expect_mutant() { # LABEL MUTANT CWD COMMAND WANT
  local label="$1" mutant="$2" cwd="$3" command_data="$4" want="$5"
  run_policy "$MUT_DIR/$mutant.sh" "$cwd" "$command_data"
  if [ "$LAST_OUTPUT" = "$want" ]; then
    mutation_red "$label"
  else
    fail "$label mutant did not return '$want' (got '$LAST_OUTPUT')"
  fi
}

# A1 isolation mutation: the verdict can still be accepted, but executable
# config running is itself the contract failure.
rm -f "$MARKER"
git_q -C "$PRIMARY" config core.fsmonitor "$TMP/fsmonitor.sh" || exit 1
run_policy "$MUT_DIR/a1-drop-git-isolation.sh" "$NONREPO" "$(cmd "$WT_CLEAN")"
if [ -e "$MARKER" ] && [ "$LAST_OUTPUT" = "$ALLOW_JSON" ]; then
  mutation_red "A1 drop env and -c isolation"
else
  fail "A1 isolation mutation did not create marker plus accept (got '$LAST_OUTPUT')"
fi
git_q -C "$PRIMARY" config --unset core.fsmonitor || exit 1

printf 'gitdir: %s\n' "$OTHER_ADMIN" >"$WT_SWAP/.git"
expect_mutant "A1 drop gitdir equality" a1-drop-gitdir-equality "$NONREPO" "$(cmd "$WT_SWAP")" "$ALLOW_JSON"
printf '%s\n' "$SWAP_ORIGINAL" >"$WT_SWAP/.git"

expect_mutant "A2 drop nested .git scan" a2-drop-nested-git-scan "$NONREPO" "$(cmd "$WT_DOTGIT_DATA")" "$ALLOW_JSON"
expect_mutant "A2 restore pyc ancestor loop" a2-restore-pyc-ancestor-loop "$NONREPO" "$(cmd "$WT_PYC_DIR_DATA")" "$ALLOW_JSON"
expect_mutant "A2 skip ignored-path marker scan" a2-skip-ignored-path-scan "$NONREPO" "$(cmd "$WT_BARE_CACHE")" "$ALLOW_JSON"
expect_mutant "A3 drop branch-tip equality" a3-drop-branch-tip-equality "$NONREPO" "$(cmd "$WT_UNPUSHED")" "$ALLOW_JSON"
expect_mutant "A4 drop lock check" a4-drop-lock-check "$NONREPO" "$(cmd "$WT_LOCKED")" "$ALLOW_JSON"
# When the cleanup validator runs first, it refuses the four-word form for its
# shape, so the mutant loses the workspace reason the unmutated guard reports.
expect_mutant "A5 swap cleanup and workspace branches" a5-swap-branch-order "$NONREPO" \
  "git worktree remove $WT_CLEAN" '{"decision":"deny","reason":"unsafe_worktree_remove"}'

if [ "$FAILURES" -eq 0 ]; then
  printf '%s\n' 'ALL CLEANUP-WORKTREE TESTS PASSED'
  exit 0
fi
printf '%s TEST(S) FAILED\n' "$FAILURES"
exit 1
