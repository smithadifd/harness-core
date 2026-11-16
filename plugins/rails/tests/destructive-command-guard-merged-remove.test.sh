#!/bin/bash
# Regressions of the removed merged-or-pushed route. The source guard accepted
# a clean linked tree with no registry row when its HEAD was on a remote
# branch `git ls-remote` confirmed, or under a merged PR's final head, and an
# `rm <lane>/worktree` of a symlink to such a tree. That evidence is read from
# the repository, and the repository can forge it (a `git replace --graft`
# made an unrelated commit look merged), so the plugin removes the route:
# every removal only it accepted is now denied (`git worktree remove` as
# unsafe_worktree_remove, the link rm as unsafe_rm), and the person runs it by
# hand. The registered cleanup path keeps only its Python-cache allowance.
#
# The rig stays so the removal can be observed if the route ever comes back:
# temp repositories, a bare "remote" that the test's own global git config
# maps https://github.com/acme/widget.git onto (so nothing touches the
# network), and a fake gh on the hook process's PATH that answers from canned
# JSON and logs every call. The guard must never call it. GR1 is the graft
# case itself. Command strings are inert JSON data and are never executed.
set -u
LC_ALL=C

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUARD="${MERGED_REMOVE_TEST_GUARD:-$HERE/../hooks/destructive-command-guard.sh}"
JQ="$(command -v jq)" || { printf 'FAIL: jq is not on PATH\n'; exit 1; }

TMP_BASE="$(mktemp -d)" || exit 1
TMP="$(cd "$TMP_BASE" && pwd -P)"
FAILURES=0
pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; FAILURES=$((FAILURES + 1)); }

# Every tree here lives under the temp roots, which the guard accepts as
# throwaway trees on their own. Judge a copy with the temp roots emptied so no
# other rule accepts them (the lane-remove suite does the same).
sed 's|^TEMP_ROOTS=.*|TEMP_ROOTS=()|' "$GUARD" >"$TMP/guard-no-temp-roots.sh" || exit 1
grep -q '^TEMP_ROOTS=()$' "$TMP/guard-no-temp-roots.sh" || { printf 'FAIL: guard has no TEMP_ROOTS constant to rewrite\n'; exit 1; }
chmod 700 "$TMP/guard-no-temp-roots.sh"
ENGINE="$TMP/guard-no-temp-roots.sh"

GITHOME="$TMP/githome"
REMOTE="$TMP/remote.git"
URL="https://github.com/acme/widget.git"
PRIMARY="$TMP/primary"
OTHER="$TMP/other"
TREES="$TMP/trees"
LANES="$TMP/lanes"
BIN="$TMP/bin"
GHJSON="$TMP/gh-json"
GHLOG="$TMP/gh.log"
mkdir -p "$GITHOME" "$TREES" "$LANES" "$BIN" "$GHJSON" "$TMP/no-registry"
{
  printf '[user]\n\tname = t\n\temail = t@example.invalid\n'
  printf '[init]\n\tdefaultBranch = main\n'
  printf '[url "%s"]\n\tinsteadOf = %s\n' "$REMOTE" "$URL"
} >"$GITHOME/.gitconfig"
git_q() { HOME="$GITHOME" GIT_CONFIG_NOSYSTEM=1 git "$@"; }
die() { printf 'FAIL: setup: %s\n' "$1"; exit 1; }

git_q init -q --bare "$REMOTE" || die "bare remote"
git_q init -q "$PRIMARY" || die "primary"
printf 'x\n' >"$PRIMARY/f.txt"
git_q -C "$PRIMARY" add f.txt && git_q -C "$PRIMARY" commit -qm seed || die "seed"
git_q -C "$PRIMARY" remote add origin "$URL" || die "origin"
git_q -C "$PRIMARY" push -q origin main || die "push main (the URL mapping onto the bare remote failed)"
git_q -C "$PRIMARY" fetch -q origin || die "fetch"
git_q init -q "$OTHER" || die "other"
printf 'y\n' >"$OTHER/g.txt"
git_q -C "$OTHER" add g.txt && git_q -C "$OTHER" commit -qm seed || die "other seed"

commit_in() { # DIR FILE
  printf '%s\n' "$2" >"$1/$2" && git_q -C "$1" add "$2" && git_q -C "$1" commit -qm "$2"
}

# (1) The measured merged-PR case. The tree's HEAD (A) was the PR's head
# before GitHub's update-branch merged main into it (M); the PR was then
# squash-merged (S on main) and its branch deleted. HEAD is an ancestor of the
# PR's final head M, not equal to it, and is on no remote ref.
MERGED="$TREES/merged"
git_q -C "$PRIMARY" worktree add -q --no-track -b feat-merged "$MERGED" origin/main || die "merged tree"
commit_in "$MERGED" a.txt || die "commit A"
A="$(git_q -C "$MERGED" rev-parse HEAD)"
git_q -C "$MERGED" push -q origin feat-merged || die "push feat-merged"
commit_in "$PRIMARY" b.txt || die "commit B"
git_q -C "$PRIMARY" push -q origin main || die "push B"
UPDATER="$TREES/updater"
git_q -C "$PRIMARY" worktree add -q --detach "$UPDATER" "$A" || die "updater"
git_q -C "$UPDATER" merge -q --no-edit main || die "update-branch merge"
M="$(git_q -C "$UPDATER" rev-parse HEAD)"
git_q -C "$UPDATER" push -q origin "$M:refs/heads/feat-merged" || die "push M"
git_q -C "$UPDATER" checkout -q --detach main || die "updater to main"
git_q -C "$UPDATER" merge -q --squash "$M" || die "squash"
git_q -C "$UPDATER" commit -qm "squash feat-merged" || die "squash commit"
git_q -C "$UPDATER" push -q origin HEAD:main || die "push squash"
git_q -C "$UPDATER" checkout -q --detach "$A" || die "updater off main"
git_q -C "$PRIMARY" push -q origin --delete feat-merged || die "delete feat-merged"
git_q -C "$PRIMARY" fetch -q --prune origin || die "prune"
git_q -C "$PRIMARY" merge -q --ff-only origin/main || die "primary ff"
[ "$A" != "$M" ] || die "A equals M"
git_q -C "$PRIMARY" merge-base --is-ancestor "$A" "$M" || die "A is not an ancestor of M"
[ -z "$(git_q -C "$PRIMARY" for-each-ref --contains "$A" refs/remotes)" ] || die "A is on a remote ref"
printf '[{"headRefOid":"%s"}]\n' "$M" >"$GHJSON/feat-merged.json"
# A merged PR whose final head is the tree's HEAD itself (no update-branch).
MERGED_EQ="$TREES/merged-equal"
git_q -C "$PRIMARY" worktree add -q --no-track -b feat-equal "$MERGED_EQ" origin/main || die "merged-equal tree"
commit_in "$MERGED_EQ" e.txt || die "commit E"
printf '[{"headRefOid":"%s"},{"headRefOid":"%s"}]\n' "0123456789abcdef0123456789abcdef01234567" \
  "$(git_q -C "$MERGED_EQ" rev-parse HEAD)" >"$GHJSON/feat-equal.json"

# (2) The measured pushed case: HEAD is on origin's default branch and the
# branch has no upstream configured.
PUSHED="$TREES/pushed"
git_q -C "$PRIMARY" worktree add -q --no-track -b topic "$PUSHED" origin/main || die "pushed tree"
commit_in "$PUSHED" c.txt || die "commit C"
git_q -C "$PUSHED" push -q origin HEAD:main || die "push C to main"
git_q -C "$PRIMARY" fetch -q origin || die "fetch after C"
[ -z "$(git_q -C "$PUSHED" config --get branch.topic.remote)" ] || die "topic has an upstream"
[ "$(git_q -C "$PRIMARY" rev-parse origin/main)" = "$(git_q -C "$PUSHED" rev-parse HEAD)" ] || die "C is not origin/main"

# Trees that must be refused.
DIRTY_MOD="$TREES/dirty-mod"
DIRTY_NEW="$TREES/dirty-untracked"
LOCAL_ONLY="$TREES/local-only"
FORGED="$TREES/forged"
STALE="$TREES/stale"
git_q -C "$PRIMARY" worktree add -q --detach "$DIRTY_MOD" origin/main || die "dirty-mod"
printf 'changed\n' >"$DIRTY_MOD/f.txt"
git_q -C "$PRIMARY" worktree add -q --detach "$DIRTY_NEW" origin/main || die "dirty-untracked"
printf 'new\n' >"$DIRTY_NEW/untracked.txt"
git_q -C "$PRIMARY" worktree add -q --no-track -b local-only "$LOCAL_ONLY" origin/main || die "local-only"
commit_in "$LOCAL_ONLY" l.txt || die "commit L"
git_q -C "$PRIMARY" worktree add -q --no-track -b forged "$FORGED" origin/main || die "forged"
commit_in "$FORGED" forged.txt || die "commit F"
# A remote-tracking ref written locally; the remote has no such branch.
git_q -C "$PRIMARY" update-ref refs/remotes/origin/forged "$(git_q -C "$FORGED" rev-parse HEAD)" || die "forge ref"
# A tree on a pushed branch whose local tracking ref is stale: the remote
# branch has since moved on, so ls-remote no longer reports this tip.
git_q -C "$PRIMARY" worktree add -q --no-track -b stale "$STALE" origin/main || die "stale"
commit_in "$STALE" s1.txt || die "commit s1"
git_q -C "$STALE" push -q origin stale || die "push stale"
git_q -C "$PRIMARY" fetch -q origin || die "fetch stale"
git_q -C "$PRIMARY" worktree add -q --detach "$TREES/stale-mover" "$(git_q -C "$STALE" rev-parse HEAD)" || die "stale mover"
commit_in "$TREES/stale-mover" s2.txt || die "commit s2"
git_q -C "$TREES/stale-mover" push -q origin HEAD:refs/heads/stale || die "move stale"
git_q -C "$PRIMARY" update-ref refs/remotes/origin/stale "$(git_q -C "$STALE" rev-parse HEAD)" || die "keep stale ref"
# A tree whose repository's origin is not a github.com URL, on its remote ref.
NONGH_REMOTE="$TMP/nongh.git"
NONGH="$TREES/nongh"
git_q init -q --bare "$NONGH_REMOTE" || die "nongh bare"
git_q -C "$OTHER" remote add origin "$NONGH_REMOTE" || die "nongh origin"
git_q -C "$OTHER" push -q origin main || die "nongh push"
git_q -C "$OTHER" fetch -q origin || die "nongh fetch"
git_q -C "$OTHER" worktree add -q --detach "$NONGH" origin/main || die "nongh tree"

# Trees the porcelain check passes but the registered cleanup path's own
# checks refuse; each sits on the confirmed origin/main.
IGNORED="$TREES/ignored-env"
SKIPWT="$TREES/skip-worktree"
KEEP="$TREES/worktree-ref"
mkdir -p "$PRIMARY/.git/info" || die "info dir"
printf '.env\n' >>"$PRIMARY/.git/info/exclude" || die "exclude .env"
git_q -C "$PRIMARY" worktree add -q --detach "$IGNORED" origin/main || die "ignored tree"
printf 'SECRET=1\n' >"$IGNORED/.env"
[ -z "$(git_q -C "$IGNORED" status --porcelain)" ] || die "the ignored .env shows in porcelain"
git_q -C "$PRIMARY" worktree add -q --detach "$SKIPWT" origin/main || die "skip-worktree tree"
git_q -C "$SKIPWT" update-index --skip-worktree f.txt || die "skip-worktree bit"
printf 'edited\n' >"$SKIPWT/f.txt"
[ -z "$(git_q -C "$SKIPWT" status --porcelain)" ] || die "the skip-worktree edit shows in porcelain"
git_q -C "$PRIMARY" worktree add -q --detach "$KEEP" origin/main || die "worktree-ref tree"
commit_in "$KEEP" k.txt || die "commit K"
git_q -C "$KEEP" update-ref refs/worktree/keep HEAD || die "refs/worktree/keep"
git_q -C "$KEEP" checkout -q --detach origin/main || die "keep back to main"
[ -z "$(git_q -C "$KEEP" status --porcelain)" ] || die "the worktree-ref tree is not clean"
# A merged PR whose final head is present locally (origin/main's tip) but does
# not contain the tree's HEAD, a local-only commit.
NOCONTAIN="$TREES/no-contain"
git_q -C "$PRIMARY" worktree add -q --no-track -b no-contain "$NOCONTAIN" origin/main || die "no-contain tree"
commit_in "$NOCONTAIN" n.txt || die "commit N"
MAIN_TIP="$(git_q -C "$PRIMARY" rev-parse origin/main)"
printf '[{"headRefOid":"%s"}]\n' "$MAIN_TIP" >"$GHJSON/no-contain.json"
git_q -C "$PRIMARY" merge-base --is-ancestor "$(git_q -C "$NOCONTAIN" rev-parse HEAD)" "$MAIN_TIP" \
  && die "no-contain HEAD is an ancestor of origin/main"

# Build output. These trees sit on the confirmed origin/main with no registry
# row, so only their ignored files decide. An ignored path is allowed when a
# directory above it is named node_modules, .next, dist, build, .venv, venv,
# .ruff_cache, .mypy_cache, .tox, .cache, coverage, .nyc_output or target, or
# ends in .egg-info; anything else ignored still refuses.
{
  printf 'node_modules/\n.next/\n*.egg-info/\n.venv/\n__pycache__/\n'
  printf 'ledger/.claude-plugin/types/\n/build\nnode_modules.bak/\n'
} >>"$PRIMARY/.git/info/exclude" || die "exclude build output"
BUILD_OK="$TREES/build-ok"
BUILD_TYPES="$TREES/build-types"
BUILD_ENV="$TREES/build-env"
BUILD_NESTED_GIT="$TREES/build-nested-git"
BUILD_BARE="$TREES/build-bare-store"
BUILD_FILE="$TREES/build-file"
BUILD_NEAR="$TREES/build-near-miss"
for wt in "$BUILD_OK" "$BUILD_TYPES" "$BUILD_ENV" "$BUILD_NESTED_GIT" "$BUILD_BARE" "$BUILD_FILE" "$BUILD_NEAR"; do
  git_q -C "$PRIMARY" worktree add -q --detach "$wt" origin/main || die "build tree $wt"
done
put() { mkdir -p "$(dirname "$1")" && printf 'generated\n' >"$1"; }
put "$BUILD_OK/node_modules/pkg/index.js"
put "$BUILD_OK/node_modules/pkg/lib/deep.js"
put "$BUILD_OK/.next/cache/x"
put "$BUILD_OK/src/pkg.egg-info/PKG-INFO"
put "$BUILD_OK/sub/.venv/lib/x.py"
put "$BUILD_TYPES/ledger/.claude-plugin/types/x.d.ts"
put "$BUILD_ENV/node_modules/pkg/index.js"
printf 'SECRET=1\n' >"$BUILD_ENV/.env"
put "$BUILD_NESTED_GIT/node_modules/dep/index.js"
git_q init -q "$BUILD_NESTED_GIT/node_modules/dep" || die "nested repo in node_modules"
put "$BUILD_BARE/node_modules/pkg/index.js"
git_q init -q --bare "$BUILD_BARE/node_modules/store" || die "bare store in node_modules"
printf 'not a directory\n' >"$BUILD_FILE/build"
put "$BUILD_NEAR/node_modules.bak/pkg/index.js"
for wt in "$BUILD_OK" "$BUILD_TYPES" "$BUILD_ENV" "$BUILD_BARE" "$BUILD_FILE" "$BUILD_NEAR"; do
  [ -z "$(git_q -C "$wt" status --porcelain)" ] || die "an ignored build fixture shows in porcelain: $wt"
done

# Registered trees: a seat-bound cleanup-registry row names each. With the row,
# any ignored file is allowed once HEAD is merged or pushed; untracked files and
# nested repositories still refuse, and a tree neither merged nor pushed keeps
# the Python-cache-only rule.
REG_PUSHED="$TREES/reg-pushed"
REG_PR="$TREES/reg-merged-pr"
REG_LOCAL="$TREES/reg-local-env"
REG_LOCAL_BUILD="$TREES/reg-local-build"
REG_LOCAL_CACHE="$TREES/reg-local-cache"
REG_UNTRACKED="$TREES/reg-untracked"
REG_NESTED="$TREES/reg-nested-git"
git_q -C "$PRIMARY" worktree add -q --detach "$REG_PUSHED" origin/main || die "reg-pushed"
git_q -C "$PRIMARY" worktree add -q --detach "$REG_UNTRACKED" origin/main || die "reg-untracked"
git_q -C "$PRIMARY" worktree add -q --detach "$REG_NESTED" origin/main || die "reg-nested"
git_q -C "$PRIMARY" worktree add -q --no-track -b reg-pr "$REG_PR" origin/main || die "reg-pr"
commit_in "$REG_PR" r.txt || die "commit R"
printf '[{"headRefOid":"%s"}]\n' "$(git_q -C "$REG_PR" rev-parse HEAD)" >"$GHJSON/reg-pr.json"
git_q -C "$PRIMARY" worktree add -q --no-track -b reg-local "$REG_LOCAL" origin/main || die "reg-local"
git_q -C "$PRIMARY" worktree add -q --no-track -b reg-local-build "$REG_LOCAL_BUILD" origin/main || die "reg-local-build"
git_q -C "$PRIMARY" worktree add -q --no-track -b reg-local-cache "$REG_LOCAL_CACHE" origin/main || die "reg-local-cache"
commit_in "$REG_LOCAL" l1.txt || die "commit reg-local"
commit_in "$REG_LOCAL_BUILD" l2.txt || die "commit reg-local-build"
commit_in "$REG_LOCAL_CACHE" l3.txt || die "commit reg-local-cache"
put "$REG_PUSHED/node_modules/pkg/index.js"
printf 'SECRET=1\n' >"$REG_PUSHED/.env"
put "$REG_PUSHED/ledger/.claude-plugin/types/x.d.ts"
printf 'SECRET=1\n' >"$REG_PR/.env"
printf 'SECRET=1\n' >"$REG_LOCAL/.env"
put "$REG_LOCAL_BUILD/node_modules/pkg/index.js"
put "$REG_LOCAL_CACHE/pkg/__pycache__/m.cpython-313.pyc"
put "$REG_UNTRACKED/node_modules/pkg/index.js"
printf 'a note\n' >"$REG_UNTRACKED/notes.txt"
put "$REG_NESTED/node_modules/dep/index.js"
git_q init -q "$REG_NESTED/node_modules/dep" || die "nested repo in a registered tree"
# Registered pushed trees whose ignored paths hold repository data. Each sits
# on the confirmed origin/main, so only a repository-marker scan of every
# ignored path keeps the merged-or-pushed test from admitting it.
printf '.store/\n' >>"$PRIMARY/.git/info/exclude" || die "exclude .store"
REG_BARE_NM="$TREES/reg-bare-node-modules"
REG_BARE_STORE="$TREES/reg-bare-store-dir"
REG_BARE_PYCACHE="$TREES/reg-bare-pycache"
REG_GITLINK="$TREES/reg-gitlink"
REG_OBJECTS="$TREES/reg-objects-only"
for wt in "$REG_BARE_NM" "$REG_BARE_STORE" "$REG_BARE_PYCACHE" "$REG_GITLINK" "$REG_OBJECTS"; do
  git_q -C "$PRIMARY" worktree add -q --detach "$wt" origin/main || die "registered marker tree $wt"
done
put "$REG_BARE_NM/node_modules/pkg/index.js"
git_q init -q --bare "$REG_BARE_NM/node_modules/store" || die "bare store in registered node_modules"
git_q init -q --bare "$REG_BARE_STORE/.store/mirror.git" || die "bare mirror in an ignored .store"
git_q init -q --bare "$REG_BARE_PYCACHE/__pycache__/repo" || die "bare repository in __pycache__"
put "$REG_GITLINK/.store/sub/data.txt"
printf 'gitdir: %s\n' "$TMP/elsewhere/admin" >"$REG_GITLINK/.store/sub/.git"
# Only an objects directory: no HEAD, refs or .git beside it.
put "$REG_OBJECTS/.store/cache/objects/pack/pack-1.pack"
for wt in "$REG_BARE_NM" "$REG_BARE_STORE" "$REG_BARE_PYCACHE" "$REG_GITLINK" "$REG_OBJECTS"; do
  [ -z "$(git_q -C "$wt" status --porcelain)" ] || die "a registered marker fixture shows in porcelain: $wt"
done
REG_CLEANUP_DIR="$TMP/cleanup-this-seat"
mkdir -p "$REG_CLEANUP_DIR"
chmod 700 "$REG_CLEANUP_DIR"
{
  printf 'repo\tpath\tkind\tseat\trow\tcreated_at\n'
  for wt in "$REG_PUSHED" "$REG_PR" "$REG_LOCAL" "$REG_LOCAL_BUILD" "$REG_LOCAL_CACHE" "$REG_UNTRACKED" "$REG_NESTED" \
    "$REG_BARE_NM" "$REG_BARE_STORE" "$REG_BARE_PYCACHE" "$REG_GITLINK" "$REG_OBJECTS"; do
    printf '%s\t%s\tworktree\tthis-seat\t1\t2026-11-06T00:00:00Z\n' "$PRIMARY" "$wt"
  done
} >"$REG_CLEANUP_DIR/cleanup-items.tsv"
chmod 600 "$REG_CLEANUP_DIR/cleanup-items.tsv"
REG_ENV="CLEANUP_ITEMS_REGISTRY=$REG_CLEANUP_DIR/cleanup-items.tsv"

# Lane registries (private, as the launcher writes them). REG_ROW has a row and
# an identity row for the pushed tree but no prune grant: another seat owns it.
# REG_OTHER names only another tree.
REG_ROW="$TMP/reg-row"
REG_OTHER="$TMP/reg-other"
mkdir -p "$REG_ROW" "$REG_OTHER"
chmod 700 "$REG_ROW" "$REG_OTHER"
printf 'path\trepo\tbranch\tcreated_at\n%s\t%s\ttopic\t2026-11-02T00:00:00Z\n' "$PUSHED" "$PRIMARY" >"$REG_ROW/lane-roots.tsv"
printf 'lane-roots-ids/v1\tpath\tgeneration\tidentity\tseat\tregistered_at\n%s\t1\tidentity-of-another-seat\tother-seat\t2026-11-02T00:00:00Z\n' \
  "$PUSHED" >"$REG_ROW/lane-roots.ids.tsv"
printf 'lane-prune-pending/v1\tpath\trepo\tgeneration\tidentity\texpires_at\n' >"$REG_ROW/lane-prune-pending.tsv"
printf 'path\trepo\tbranch\tcreated_at\n%s\t%s\tfeat-equal\t2026-11-02T00:00:00Z\n' "$MERGED_EQ" "$PRIMARY" >"$REG_OTHER/lane-roots.tsv"
chmod 600 "$REG_ROW"/*.tsv "$REG_OTHER"/*.tsv
# A cleanup registry naming the pushed tree for another seat.
CLEANUP_DIR="$TMP/cleanup"
mkdir -p "$CLEANUP_DIR"
chmod 700 "$CLEANUP_DIR"
printf 'repo\tpath\tkind\tseat\trow\tcreated_at\n%s\t%s\tworktree\tother-seat\t1\t2026-11-02T00:00:00Z\n' \
  "$PRIMARY" "$PUSHED" >"$CLEANUP_DIR/cleanup-items.tsv"
chmod 600 "$CLEANUP_DIR/cleanup-items.tsv"

# Lane scratch symlinks. Links under $LANES sit in no root this session owns.
mkdir -p "$LANES/good" "$LANES/bad" "$LANES/named"
ln -s "$PUSHED" "$LANES/good/worktree"
ln -s "$LOCAL_ONLY" "$LANES/bad/worktree"
ln -s "$PUSHED" "$LANES/named/tree"
# Links directly in the current worktree (the caller's own repository).
CALLER="$TMP/caller"
CALLER_BAD="$TMP/caller-bad"
git_q init -q "$CALLER" || die "caller"
git_q init -q "$CALLER_BAD" || die "caller-bad"
ln -s "$PUSHED" "$CALLER/worktree"
ln -s "$PUSHED" "$CALLER/tree"
ln -s "$LOCAL_ONLY" "$CALLER_BAD/worktree"
# Links in a session scratchpad, which must sit below /private/tmp.
SCRATCH_BASE="$(mktemp -d "$(cd /tmp && pwd -P)/guard-merged-remove.XXXXXX")" || die "scratchpad"
SCRATCH="$(cd "$SCRATCH_BASE" && pwd -P)"
mkdir -p "$SCRATCH/lane"
ln -s "$PUSHED" "$SCRATCH/worktree"
ln -s "$PUSHED" "$SCRATCH/lane/worktree"

# The fake gh: answers `pr list --repo acme/widget --state merged --head <b>`
# from $FAKE_GH_JSON_DIR/<b>.json (an empty list when absent), logs its
# arguments, and can fail or hang on request. All of it is read from the hook
# process's environment.
cat >"$BIN/gh" <<'FAKE'
#!/bin/bash
printf '%s\n' "$*" >>"$FAKE_GH_LOG"
case "${FAKE_GH_MODE:-json}" in
  fail) exit 1 ;;
  hang) exec /bin/sleep 20 ;;
  garbage) printf 'not json\n'; exit 0 ;;
esac
head="" repo="" state=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --head) head="$2"; shift ;;
    --repo) repo="$2"; shift ;;
    --state) state="$2"; shift ;;
  esac
  shift
done
[ "$repo" = acme/widget ] && [ "$state" = merged ] && [ -n "$head" ] || exit 3
if [ -f "$FAKE_GH_JSON_DIR/$head.json" ]; then cat "$FAKE_GH_JSON_DIR/$head.json"; else printf '[]\n'; fi
FAKE
chmod 755 "$BIN/gh"
# jq beside the fake gh, so a guard that went back to a PATH lookup for the
# route would find both; the shipped guard reads neither from PATH.
ln -s "$JQ" "$BIN/jq" || die "jq beside the fake gh"
BASE_PATH="/usr/bin:/bin:/usr/sbin:/sbin"
# gh_never_asked LABEL: the fake gh's log is still empty.
gh_never_asked() {
  if [ ! -s "$GHLOG" ]; then pass "$1: gh was never asked"; else fail "$1: gh was asked: $(cat "$GHLOG")"; fi
}

LAST_ELAPSED=0
# run_case LABEL EXPECT(no_opinion|deny) CWD COMMAND [REASON] [-- ENV=VAL ...]
# The engine runs with PATH=<fake gh dir>:<system dirs> unless an ENV=VAL
# after -- overrides it.
run_case() {
  local label="$1" expect="$2" cwd="$3" command_data="$4" want_reason="" input output decision reason
  local start end
  shift 4
  if [ "$#" -gt 0 ] && [ "$1" != "--" ]; then want_reason="$1"; shift; fi
  if [ "$#" -gt 0 ]; then shift; fi
  input="$($JQ -cn --arg cwd "$cwd" --arg command "$command_data" '{cwd:$cwd,command:$command}')"
  start="$(/bin/date +%s)"
  output="$(cd "$TMP" && printf '%s' "$input" | env -i HOME="$GITHOME" PATH="$BIN:$BASE_PATH" \
    FAKE_GH_LOG="$GHLOG" FAKE_GH_JSON_DIR="$GHJSON" \
    ${1+"$@"} /bin/bash --noprofile --norc -p "$ENGINE" --policy 2>/dev/null)"
  end="$(/bin/date +%s)"
  LAST_ELAPSED=$((end - start))
  decision="$(printf '%s' "$output" | "$JQ" -er '.decision' 2>/dev/null || printf invalid)"
  reason="$(printf '%s' "$output" | "$JQ" -er '.reason' 2>/dev/null || printf invalid)"
  if [ "$decision" = "$expect" ] && { [ -z "$want_reason" ] || [ "$reason" = "$want_reason" ]; }; then
    pass "$label -> $decision/$reason"
  else
    fail "$label: expected $expect${want_reason:+/$want_reason}, got $decision/$reason"
  fi
}

# run_scratch_case LABEL EXPECT CWD COMMAND [REASON]: the engine with
# --scratchpad "$SCRATCH".
run_scratch_case() {
  local label="$1" expect="$2" cwd="$3" command_data="$4" want_reason="${5:-}" input output decision reason
  input="$($JQ -cn --arg cwd "$cwd" --arg command "$command_data" '{cwd:$cwd,command:$command}')"
  output="$(cd "$TMP" && printf '%s' "$input" | env -i HOME="$GITHOME" PATH="$BIN:$BASE_PATH" \
    FAKE_GH_LOG="$GHLOG" FAKE_GH_JSON_DIR="$GHJSON" \
    /bin/bash --noprofile --norc -p "$ENGINE" --policy --scratchpad "$SCRATCH" 2>/dev/null)"
  decision="$(printf '%s' "$output" | "$JQ" -er '.decision' 2>/dev/null || printf invalid)"
  reason="$(printf '%s' "$output" | "$JQ" -er '.reason' 2>/dev/null || printf invalid)"
  if [ "$decision" = "$expect" ] && { [ -z "$want_reason" ] || [ "$reason" = "$want_reason" ]; }; then
    pass "$label -> $decision/$reason"
  else
    fail "$label: expected $expect${want_reason:+/$want_reason}, got $decision/$reason"
  fi
}

printf '%s\n' '== (1) merged PR: the route is removed, so the removal is denied =='
: >"$GHLOG"
run_case "update-branched then squash-merged PR" deny "$OTHER" "git -C $PRIMARY worktree remove $MERGED" unsafe_worktree_remove
gh_never_asked "update-branched then squash-merged PR"
run_case "plain form from the primary checkout" deny "$PRIMARY" "git worktree remove $MERGED" unsafe_worktree_remove
run_case "merged PR whose final head equals HEAD (an absent head listed first)" deny "$OTHER" "git -C $PRIMARY worktree remove $MERGED_EQ" unsafe_worktree_remove

printf '%s\n' '== (2) pushed: the route is removed, so the removal is denied =='
: >"$GHLOG"
run_case "HEAD on the default branch" deny "$OTHER" "git -C $PRIMARY worktree remove $PUSHED" unsafe_worktree_remove
gh_never_asked "HEAD on the default branch"

printf '%s\n' '== (3) the lane worktree symlink: the ordinary rm rule refuses it =='
run_case "rm of a worktree link directly in the current worktree" deny "$CALLER" "rm worktree" unsafe_rm
run_case "rm of the same link spelled absolute" deny "$CALLER" "rm $CALLER/worktree" unsafe_rm
run_case "rm -f of the same link" deny "$CALLER" "rm -f $CALLER/worktree" unsafe_rm
run_case "rm of a qualifying worktree link outside the current worktree and scratchpad" deny "$PRIMARY" \
  "rm $LANES/good/worktree" unsafe_rm
run_case "rm -f of a qualifying worktree link outside the current worktree and scratchpad" deny "$PRIMARY" \
  "rm -f $LANES/good/worktree" unsafe_rm
run_case "rm of a worktree link in the current worktree to a local-only tree" deny "$CALLER_BAD" "rm worktree" unsafe_rm
run_case "rm of a lane's worktree link to a local-only tree" deny "$PRIMARY" "rm $LANES/bad/worktree" unsafe_rm
run_case "rm -r of the qualifying link" deny "$CALLER" "rm -r worktree" unsafe_rm
run_case "rm -rf of the qualifying link" deny "$CALLER" "rm -rf $CALLER/worktree" unsafe_rm
run_case "rm of a qualifying link not named worktree" deny "$CALLER" "rm tree" unsafe_rm
run_case "rm of the link and a second path" deny "$CALLER" "rm worktree tree" unsafe_rm
run_case "rm of a worktree link to a tree a lane-registry row names without a grant" deny "$CALLER" \
  "rm worktree" unsafe_rm -- LANE_ROOTS_REGISTRY="$REG_ROW/lane-roots.tsv"
run_scratch_case "rm of a worktree link directly in the session scratchpad" deny "$PRIMARY" "rm $SCRATCH/worktree" unsafe_rm
run_scratch_case "rm of a worktree link one level below the session scratchpad" deny "$PRIMARY" \
  "rm $SCRATCH/lane/worktree" unsafe_rm
if [ -L "$LANES/good/worktree" ] && [ -L "$CALLER/worktree" ] && [ -d "$PUSHED" ]; then
  pass "the links and their tree are untouched"
else
  fail "the fixture link or tree changed"
fi

printf '%s\n' '== a registry row for the tree is final =='
run_case "registered clean pushed tree whose row has no prune grant" deny "$OTHER" \
  "git -C $PRIMARY worktree remove $PUSHED" unsafe_worktree_remove -- LANE_ROOTS_REGISTRY="$REG_ROW/lane-roots.tsv"
run_case "the same tree, plain form" deny "$PRIMARY" \
  "git worktree remove $PUSHED" unsafe_worktree_remove -- LANE_ROOTS_REGISTRY="$REG_ROW/lane-roots.tsv"
run_case "a registry whose rows name only other trees" deny "$OTHER" \
  "git -C $PRIMARY worktree remove $PUSHED" unsafe_worktree_remove -- LANE_ROOTS_REGISTRY="$REG_OTHER/lane-roots.tsv"
run_case "a cleanup-registry row for another seat names the tree (plain form)" deny "$PRIMARY" \
  "git worktree remove $PUSHED" unsafe_worktree_remove -- CLEANUP_ITEMS_REGISTRY="$CLEANUP_DIR/cleanup-items.tsv" CLEANUP_SEAT=this-seat

printf '%s\n' '== refused as today =='
run_case "dirty tree: modified tracked file" deny "$OTHER" "git -C $PRIMARY worktree remove $DIRTY_MOD" unsafe_worktree_remove
run_case "dirty tree: untracked file" deny "$OTHER" "git -C $PRIMARY worktree remove $DIRTY_NEW" unsafe_worktree_remove
run_case "local-only commit, no merged PR" deny "$OTHER" "git -C $PRIMARY worktree remove $LOCAL_ONLY" unsafe_worktree_remove
run_case "ignored .env in a pushed tree" deny "$OTHER" "git -C $PRIMARY worktree remove $IGNORED" unsafe_worktree_remove
run_case "skip-worktree edit in a pushed tree" deny "$OTHER" "git -C $PRIMARY worktree remove $SKIPWT" unsafe_worktree_remove
run_case "local-only commit held only by refs/worktree/keep" deny "$OTHER" "git -C $PRIMARY worktree remove $KEEP" unsafe_worktree_remove
run_case "merged PR whose present final head does not contain HEAD" deny "$OTHER" \
  "git -C $PRIMARY worktree remove $NOCONTAIN" unsafe_worktree_remove
run_case "--force on a qualifying tree" deny "$OTHER" "git -C $PRIMARY worktree remove --force $PUSHED" unsafe_worktree_remove
run_case "-f on a qualifying tree" deny "$OTHER" "git -C $PRIMARY worktree remove -f $PUSHED" unsafe_worktree_remove
run_case "--force after the path" deny "$PRIMARY" "git worktree remove $PUSHED --force" unsafe_worktree_remove
run_case "forged local refs/remotes/origin/forged that ls-remote does not confirm" deny "$OTHER" \
  "git -C $PRIMARY worktree remove $FORGED" unsafe_worktree_remove
run_case "stale tracking ref whose tip ls-remote no longer reports" deny "$OTHER" \
  "git -C $PRIMARY worktree remove $STALE" unsafe_worktree_remove
run_case "the primary worktree" deny "$OTHER" "git -C $PRIMARY worktree remove $PRIMARY" unsafe_worktree_remove
run_case "the primary worktree, plain form" deny "$PRIMARY" "git worktree remove $PRIMARY" unsafe_worktree_remove
run_case "origin is not a github.com URL" deny "$PRIMARY" "git -C $OTHER worktree remove $NONGH" unsafe_worktree_remove
run_case "gh unavailable" deny "$OTHER" "git -C $PRIMARY worktree remove $MERGED" unsafe_worktree_remove -- PATH="$BASE_PATH"
run_case "gh exits non-zero" deny "$OTHER" "git -C $PRIMARY worktree remove $MERGED" unsafe_worktree_remove -- FAKE_GH_MODE=fail
run_case "gh prints something that is not a PR list" deny "$OTHER" "git -C $PRIMARY worktree remove $MERGED" unsafe_worktree_remove -- FAKE_GH_MODE=garbage
run_case "gh hangs past its timeout" deny "$OTHER" "git -C $PRIMARY worktree remove $MERGED" unsafe_worktree_remove -- FAKE_GH_MODE=hang
# The fake sleeps 20 s. The guard never calls gh, so the verdict comes at once.
if [ "$LAST_ELAPSED" -lt 7 ]; then
  pass "a hanging gh is never waited on (${LAST_ELAPSED} s)"
else
  fail "a hanging gh took ${LAST_ELAPSED} s; the guard must not call gh"
fi
printf '[{"headRefOid":"%s"}]\n' "0123456789abcdef0123456789abcdef01234567" >"$GHJSON/feat-merged.json"
run_case "merged PR whose headRefOid is not present locally" deny "$OTHER" "git -C $PRIMARY worktree remove $MERGED" unsafe_worktree_remove
printf '[{"headRefOid":"%s"}]\n' "$M" >"$GHJSON/feat-merged.json"
run_case "inline PATH in the command text does not reach the probe" deny "$OTHER" \
  "PATH=$BIN:$BASE_PATH git -C $PRIMARY worktree remove $MERGED" unsafe_worktree_remove -- PATH="$BASE_PATH"
run_case "two removals that each need gh: the second has no budget" deny "$OTHER" \
  "git -C $PRIMARY worktree remove $MERGED && git -C $PRIMARY worktree remove $MERGED_EQ" unsafe_worktree_remove
# The second removal runs against whatever the first one left, which the guard
# never saw, so the same tree named twice is no whole-command removal either.
run_case "the same qualifying tree named twice in one command" deny "$OTHER" \
  "git -C $PRIMARY worktree remove $MERGED; git -C $PRIMARY worktree remove $MERGED" unsafe_worktree_remove
run_case "a qualifying tree beside a refused one" deny "$OTHER" \
  "git -C $PRIMARY worktree remove $PUSHED && git -C $PRIMARY worktree remove $DIRTY_MOD" unsafe_worktree_remove

printf '%s\n' '== compound and wrapped forms of the removed route stay denied =='
# The source admitted a removal through the route only when it was the
# command's single segment. The route is gone, so each case below, and the
# single-command form, is denied.
run_case "remove, re-add at the same path, add an ignored file, remove again" deny "$OTHER" \
  "git -C $PRIMARY worktree remove $PUSHED; git -C $PRIMARY worktree add $PUSHED topic; touch $PUSHED/.env; git -C $PRIMARY worktree remove $PUSHED" \
  unsafe_worktree_remove
run_case "the same sequence joined by &&" deny "$OTHER" \
  "git -C $PRIMARY worktree remove $PUSHED && git -C $PRIMARY worktree add $PUSHED topic && touch $PUSHED/.env && git -C $PRIMARY worktree remove $PUSHED" \
  unsafe_worktree_remove
run_case "two qualifying trees joined by ;" deny "$OTHER" \
  "git -C $PRIMARY worktree remove $PUSHED; git -C $PRIMARY worktree remove $MERGED_EQ" unsafe_worktree_remove
run_case "two qualifying trees joined by &&" deny "$OTHER" \
  "git -C $PRIMARY worktree remove $PUSHED && git -C $PRIMARY worktree remove $MERGED_EQ" unsafe_worktree_remove
run_case "two qualifying trees on two lines" deny "$OTHER" \
  "git -C $PRIMARY worktree remove $PUSHED"$'\n'"git -C $PRIMARY worktree remove $MERGED_EQ" unsafe_worktree_remove
run_case "two qualifying trees, plain form, joined by ;" deny "$PRIMARY" \
  "git worktree remove $PUSHED; git worktree remove $MERGED_EQ" unsafe_worktree_remove
# A shell string is not judged as removals at all, so it is never allowed.
run_case "two qualifying trees inside bash -c" no_opinion "$OTHER" \
  "bash -c 'git -C $PRIMARY worktree remove $PUSHED; git -C $PRIMARY worktree remove $MERGED_EQ'"
run_case "one qualifying tree inside bash -c" no_opinion "$OTHER" \
  "bash -c 'git -C $PRIMARY worktree remove $PUSHED'"
run_case "a qualifying tree inside a subshell" deny "$OTHER" \
  "(git -C $PRIMARY worktree remove $PUSHED)" unsafe_worktree_remove
run_case "a qualifying tree inside a command substitution" deny "$OTHER" \
  "echo \$(git -C $PRIMARY worktree remove $PUSHED)" unsafe_worktree_remove
run_case "a qualifying tree carried by env -S" deny "$OTHER" \
  "env -S 'git -C $PRIMARY worktree remove $PUSHED'" unsafe_worktree_remove
run_case "a qualifying tree followed by a harmless read" deny "$OTHER" \
  "git -C $PRIMARY worktree remove $PUSHED && git status" unsafe_worktree_remove
run_case "a harmless read followed by a qualifying tree" deny "$OTHER" \
  "git status; git -C $PRIMARY worktree remove $PUSHED" unsafe_worktree_remove
run_case "a qualifying tree piped into a harmless read" deny "$OTHER" \
  "git -C $PRIMARY worktree remove $PUSHED | cat" unsafe_worktree_remove
run_case "a qualifying tree with a trailing separator" deny "$OTHER" \
  "git -C $PRIMARY worktree remove $PUSHED;" unsafe_worktree_remove
run_case "a qualifying tree run in the background" deny "$OTHER" \
  "git -C $PRIMARY worktree remove $PUSHED &" unsafe_worktree_remove
# The accepted words are exactly `git [-C REPO] worktree remove TARGET`; a
# prefix or a global option the normalizer would drop is not that form.
run_case "a qualifying tree through env" deny "$OTHER" \
  "env git -C $PRIMARY worktree remove $PUSHED" unsafe_worktree_remove
run_case "a qualifying tree through an absolute git path" deny "$OTHER" \
  "/usr/bin/git -C $PRIMARY worktree remove $PUSHED" unsafe_worktree_remove
run_case "a qualifying tree with a dropped -c option" deny "$OTHER" \
  "git -c core.x=y -C $PRIMARY worktree remove $PUSHED" unsafe_worktree_remove
run_case "a qualifying tree with leading blanks" deny "$OTHER" \
  "  git -C $PRIMARY worktree remove $PUSHED" unsafe_worktree_remove
run_case "a qualifying tree after an empty line" deny "$OTHER" \
  $'\n'"git -C $PRIMARY worktree remove $PUSHED" unsafe_worktree_remove
run_case "a qualifying tree with its output redirected" deny "$OTHER" \
  "git -C $PRIMARY worktree remove $PUSHED 2>/dev/null" unsafe_worktree_remove
run_case "a qualifying tree with the git word quoted" deny "$OTHER" \
  "'git' -C $PRIMARY worktree remove $PUSHED" unsafe_worktree_remove
run_case "a qualifying tree with quoted path words" deny "$OTHER" \
  "git -C '$PRIMARY' worktree remove \"$PUSHED\"" unsafe_worktree_remove

printf '%s\n' '== build output in an unregistered tree: denied =='
run_case "node_modules, .next, a nested pkg.egg-info and a nested .venv" deny "$OTHER" \
  "git -C $PRIMARY worktree remove $BUILD_OK" unsafe_worktree_remove
run_case "a repo-specific generated folder (ledger/.claude-plugin/types/)" deny "$OTHER" \
  "git -C $PRIMARY worktree remove $BUILD_TYPES" unsafe_worktree_remove
run_case "build output beside an ignored .env" deny "$OTHER" \
  "git -C $PRIMARY worktree remove $BUILD_ENV" unsafe_worktree_remove
run_case "a nested repository inside node_modules" deny "$OTHER" \
  "git -C $PRIMARY worktree remove $BUILD_NESTED_GIT" unsafe_worktree_remove
run_case "a bare repository store inside node_modules" deny "$OTHER" \
  "git -C $PRIMARY worktree remove $BUILD_BARE" unsafe_worktree_remove
run_case "an ignored file (not a directory) named build" deny "$OTHER" \
  "git -C $PRIMARY worktree remove $BUILD_FILE" unsafe_worktree_remove
run_case "a directory whose name only starts with node_modules" deny "$OTHER" \
  "git -C $PRIMARY worktree remove $BUILD_NEAR" unsafe_worktree_remove
run_case "a build-output tree in a compound command" deny "$OTHER" \
  "git -C $PRIMARY worktree remove $BUILD_OK && echo done" unsafe_worktree_remove

printf '%s\n' '== a registered tree: only Python caches may be ignored =='
# The merged-or-pushed leniency for other ignored files is removed with the
# route, so these registered trees are denied; the cache-only tree is still
# accepted.
run_case "registered pushed tree with node_modules, .env and ledger/.claude-plugin/types/" deny "$OTHER" \
  "git -C $PRIMARY worktree remove $REG_PUSHED" unsafe_worktree_remove -- "$REG_ENV" CLEANUP_SEAT=this-seat
run_case "registered tree with a merged PR and an ignored .env" deny "$OTHER" \
  "git -C $PRIMARY worktree remove $REG_PR" unsafe_worktree_remove -- "$REG_ENV" CLEANUP_SEAT=this-seat
run_case "the same pushed tree with no registry row" deny "$OTHER" \
  "git -C $PRIMARY worktree remove $REG_PUSHED" unsafe_worktree_remove
run_case "the same pushed tree, row for another seat" deny "$OTHER" \
  "git -C $PRIMARY worktree remove $REG_PUSHED" unsafe_worktree_remove -- "$REG_ENV" CLEANUP_SEAT=other-seat
run_case "registered local-only tree with an ignored .env" deny "$OTHER" \
  "git -C $PRIMARY worktree remove $REG_LOCAL" unsafe_worktree_remove -- "$REG_ENV" CLEANUP_SEAT=this-seat
run_case "registered local-only tree with node_modules" deny "$OTHER" \
  "git -C $PRIMARY worktree remove $REG_LOCAL_BUILD" unsafe_worktree_remove -- "$REG_ENV" CLEANUP_SEAT=this-seat
run_case "registered local-only tree with only a Python cache (as before)" no_opinion "$OTHER" \
  "git -C $PRIMARY worktree remove $REG_LOCAL_CACHE" accepted -- "$REG_ENV" CLEANUP_SEAT=this-seat
run_case "registered pushed tree with an untracked file" deny "$OTHER" \
  "git -C $PRIMARY worktree remove $REG_UNTRACKED" unsafe_worktree_remove -- "$REG_ENV" CLEANUP_SEAT=this-seat
run_case "registered pushed tree with a nested repository in node_modules" deny "$OTHER" \
  "git -C $PRIMARY worktree remove $REG_NESTED" unsafe_worktree_remove -- "$REG_ENV" CLEANUP_SEAT=this-seat
run_case "registered pushed tree in a compound command" deny "$OTHER" \
  "git -C $PRIMARY worktree remove $REG_PUSHED && echo done" unsafe_worktree_remove -- "$REG_ENV" CLEANUP_SEAT=this-seat

printf '%s\n' '== a registered tree: repository data in an ignored path refuses =='
run_case "registered pushed tree with a bare repository at node_modules/store" deny "$OTHER" \
  "git -C $PRIMARY worktree remove $REG_BARE_NM" unsafe_worktree_remove -- "$REG_ENV" CLEANUP_SEAT=this-seat
run_case "registered pushed tree with a bare .store/mirror.git in an ignored directory" deny "$OTHER" \
  "git -C $PRIMARY worktree remove $REG_BARE_STORE" unsafe_worktree_remove -- "$REG_ENV" CLEANUP_SEAT=this-seat
run_case "registered pushed tree with a bare repository under __pycache__" deny "$OTHER" \
  "git -C $PRIMARY worktree remove $REG_BARE_PYCACHE" unsafe_worktree_remove -- "$REG_ENV" CLEANUP_SEAT=this-seat
run_case "registered pushed tree with a .git file (gitlink) in an ignored directory" deny "$OTHER" \
  "git -C $PRIMARY worktree remove $REG_GITLINK" unsafe_worktree_remove -- "$REG_ENV" CLEANUP_SEAT=this-seat
run_case "registered pushed tree with only an objects directory in an ignored directory" deny "$OTHER" \
  "git -C $PRIMARY worktree remove $REG_OBJECTS" unsafe_worktree_remove -- "$REG_ENV" CLEANUP_SEAT=this-seat
run_case "registered pushed tree with node_modules, .env and types is denied beside them" deny "$OTHER" \
  "git -C $PRIMARY worktree remove $REG_PUSHED" unsafe_worktree_remove -- "$REG_ENV" CLEANUP_SEAT=this-seat

printf '%s\n' '== the lane worktree link is refused in every form =='
run_case "rm of the link followed by another command" deny "$CALLER" "rm worktree; echo done" unsafe_rm
run_case "rm of the link after another command" deny "$CALLER" "git status && rm worktree" unsafe_rm
run_case "rm of the link twice" deny "$CALLER" "rm worktree; rm worktree" unsafe_rm
run_case "rm of the link on two lines with a removal" deny "$CALLER" \
  "rm worktree"$'\n'"git -C $PRIMARY worktree remove $MERGED_EQ" unsafe_rm
run_case "rm of the link through command" deny "$CALLER" "command rm worktree" unsafe_rm
run_case "rm of the link through an absolute rm path" deny "$CALLER" "/bin/rm worktree" unsafe_rm
run_case "rm of the link after an empty line" deny "$CALLER" $'\n'"rm worktree" unsafe_rm
run_case "rm -- of the link alone" deny "$CALLER" "rm -- worktree" unsafe_rm
run_case "rm --force of the link alone" deny "$CALLER" "rm --force $CALLER/worktree" unsafe_rm
run_scratch_case "rm of a scratchpad worktree link followed by another command" deny "$PRIMARY" \
  "rm $SCRATCH/worktree; true" unsafe_rm

gh_never_asked "every row since section (2)"

printf '%s\n' '== GR1: a graft cannot make an unrelated commit look merged =='
# The round 4 probe of the removed route. A clean linked tree on branch
# graft-topic whose HEAD (U) is a local-only commit; the fake gh lists a merged
# PR for that branch whose final head (F) is a different commit, present
# locally; and `git replace --graft F U` in the primary repository makes F
# descend from U. The route honoured the graft and accepted the removal; the
# plugin has no route, so it is denied and gh is never asked.
GRAFT="$TREES/graft"
git_q -C "$PRIMARY" worktree add -q --no-track -b graft-topic "$GRAFT" origin/main || die "graft tree"
commit_in "$GRAFT" u.txt || die "commit U"
U="$(git_q -C "$GRAFT" rev-parse HEAD)"
F="$(git_q -C "$PRIMARY" commit-tree -p origin/main -m "final head" "origin/main^{tree}")" || die "commit F"
git_q -C "$PRIMARY" update-ref refs/heads/graft-final "$F" || die "keep F"
if git_q -C "$PRIMARY" merge-base --is-ancestor "$U" "$F"; then die "U is an ancestor of F before the graft"; fi
git_q -C "$PRIMARY" replace --graft "$F" "$U" || die "graft F onto U"
git_q -C "$PRIMARY" merge-base --is-ancestor "$U" "$F" || die "the graft does not make U an ancestor of F"
printf '[{"headRefOid":"%s"}]\n' "$F" >"$GHJSON/graft-topic.json"
[ -z "$(git_q -C "$GRAFT" status --porcelain)" ] || die "the graft tree is not clean"
: >"$GHLOG"
run_case "GR1 grafted final head, git -C form" deny "$OTHER" "git -C $PRIMARY worktree remove $GRAFT" unsafe_worktree_remove
gh_never_asked "GR1"

if [ "$FAILURES" -eq 0 ]; then
  printf '%s\n' 'ALL MERGED-REMOVE TESTS PASSED'
  printf 'Fixtures retained at %s and %s (test performs no deletion)\n' "$TMP" "$SCRATCH"
  exit 0
fi
printf '%s TEST(S) FAILED\n' "$FAILURES"
exit 1
