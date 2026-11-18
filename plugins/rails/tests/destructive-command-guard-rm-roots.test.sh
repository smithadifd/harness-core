#!/bin/bash
# The account's rm-roots file (.claude/rails/rm-roots in the home folder the
# password database names). With the file trusted and well formed, an rm from
# a working folder in no git repository may remove a regular, non-symlink file
# strictly below a folder the file lists, outside every git repository, with
# no recursive flag; nothing else changes. Rows:
#   A   accepted: such a file, from a no-repo cwd (silent at the adapter);
#   D   denied: a target outside every root, the root itself, a prefix sibling;
#   R   denied: -r in any spelling, a directory, a symlink, a target in a repo;
#   W   denied: the same target from a worktree cwd (the worktree rule never
#       reads the roots);
#   U   untrusted file: a 0666 file, a 0777 folder, a symlinked file, a
#       symlinked folder, a folder in place of the file; each denies every
#       judged rm as rm_roots_untrusted;
#   M   malformed file: each grammar near miss denies every judged rm as
#       rm_roots_malformed;
#   Z   absent file: dormant, every verdict as without the layer;
#   F   a fake HOME holding a planted file changes nothing (the shipped guard);
#   P   the pin on the line that finds the account's home folder.
# The guard reads the account's real home folder, so the rows that need a
# file run copies of the guard whose rm_roots_account_home function prints a
# fixture folder instead. Every command string is inert JSON data and is
# never executed.
set -u
LC_ALL=C

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUARD="${RM_ROOTS_TEST_GUARD:-$HERE/../hooks/destructive-command-guard.sh}"
# The interpreter the rows run the guard under: the hook's own /bin/bash.
GUARD_BASH="${RM_ROOTS_TEST_BASH:-/bin/bash}"
JQ="$(command -v jq)" || { printf 'FAIL: jq is not on PATH\n'; exit 1; }

TMP_BASE="$(mktemp -d "$(cd /tmp && pwd -P)/guard-rm-roots.XXXXXX")" || exit 1
TMP="$(cd "$TMP_BASE" && pwd -P)"
ACCT="$TMP/acct-home"
RAILS="$ACCT/.claude/rails"
FILE="$RAILS/rm-roots"
ROOT="$TMP/roots/r1"
ROOT2="$TMP/roots/r2"
SIBLING="$TMP/roots/r1x"
OUTSIDE="$TMP/outside"
WORKSPACE="$TMP/workspace"
REPO="$TMP/repo"
INNER_REPO="$ROOT/inner-repo"
NOFILE_HOME="$TMP/nofile-home"
NORAILS_HOME="$TMP/norails-home"
FAKEHOME="$TMP/fake-home"

FAILURES=0
pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; FAILURES=$((FAILURES + 1)); }

git_q() { HOME="$TMP/githome" git -c user.name=t -c user.email=t@example.invalid "$@"; }
mkdir -p "$TMP/githome" "$RAILS" "$ROOT/sub/dir" "$ROOT2" "$SIBLING" "$OUTSIDE" "$WORKSPACE" \
  "$NOFILE_HOME/.claude/rails" "$NORAILS_HOME" "$FAKEHOME/.claude/rails" "$REPO/build"
chmod 700 "$RAILS" "$FAKEHOME/.claude/rails"
for repo in "$REPO" "$INNER_REPO"; do
  git_q init -q --initial-branch=main "$repo" || exit 1
  printf 'x\n' >"$repo/f.txt"
  git_q -C "$repo" add f.txt && git_q -C "$repo" commit -qm seed || exit 1
done
printf 'x\n' >"$ROOT/file.md"
printf 'x\n' >"$ROOT/second.md"
printf 'x\n' >"$ROOT/sub/deep.md"
printf 'x\n' >"$ROOT2/other.md"
printf 'x\n' >"$SIBLING/file.md"
printf 'x\n' >"$OUTSIDE/file.md"
printf 'x\n' >"$ACCT/file.md"
printf 'x\n' >"$TMP/top.md"
ln -s "$OUTSIDE/file.md" "$ROOT/link-out.md"
ln -s "$ROOT/file.md" "$ROOT/link-in.md"
ln -s "$ROOT" "$TMP/alias-root"

# home_copy DEST HOME: a copy of the guard whose rm_roots_account_home prints
# HOME; fails the suite when the function is not found.
home_copy() {
  local dst="$1" home="$2"
  awk -v home="$home" '
    /^rm_roots_account_home\(\) \{$/ { print; printf "  printf %c%%s%c %c%s%c\n", 39, 39, 39, home, 39; on = 1; done++; next }
    on && /^}$/ { on = 0; print; next }
    on { next }
    { print }
    END { if (done != 1) exit 1 }
  ' "$GUARD" >"$dst" || { fail "fixture: $dst has no rm_roots_account_home function to rewrite"; exit 1; }
  if cmp -s "$GUARD" "$dst"; then fail "fixture: $dst is unchanged"; exit 1; fi
}
COPY="$TMP/guard-home.sh"
COPY_NOFILE="$TMP/guard-nofile.sh"
COPY_NORAILS="$TMP/guard-norails.sh"
home_copy "$COPY" "$ACCT"
home_copy "$COPY_NOFILE" "$NOFILE_HOME"
home_copy "$COPY_NORAILS" "$NORAILS_HOME"

# roots_file CONTENT [MODE]: writes the account fixture's rm-roots file.
roots_file() {
  rm -f "$FILE"
  printf '%s' "$1" >"$FILE"
  chmod "${2:-600}" "$FILE"
}

# run_case LABEL EXPECT(deny|no_opinion) CWD COMMAND [REASON] [-- ENV=VAL ...]
# The guard file is RUN_GUARD.
RUN_GUARD="$COPY"
run_case() {
  local label="$1" expect="$2" cwd="$3" command_data="$4" want_reason=""
  shift 4
  if [ "$#" -gt 0 ] && [ "$1" != "--" ]; then want_reason="$1"; shift; fi
  if [ "$#" -gt 0 ]; then shift; fi
  local input output decision reason
  input="$("$JQ" -cn --arg cwd "$cwd" --arg command "$command_data" '{cwd:$cwd,command:$command}')"
  output="$(cd "$TMP" && printf '%s' "$input" | env HOME="$TMP/githome" ${1+"$@"} \
    "$GUARD_BASH" --noprofile --norc -p "$RUN_GUARD" --policy 2>/dev/null)"
  decision="$(printf '%s' "$output" | "$JQ" -er '.decision' 2>/dev/null || printf invalid)"
  reason="$(printf '%s' "$output" | "$JQ" -er '.reason' 2>/dev/null || printf invalid)"
  if [ "$decision" = "$expect" ] && { [ -z "$want_reason" ] || [ "$reason" = "$want_reason" ]; }; then
    pass "$label -> $decision/$reason"
  else
    fail "$label: expected $expect${want_reason:+/$want_reason}, got $decision/$reason"
  fi
}

printf '%s\n' '== fixture: the workspace cwd and the roots are in no repository =='
if git -C "$WORKSPACE" rev-parse --git-dir >/dev/null 2>&1 || git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then
  fail "fixture workspace or root is inside a git repository; the rows below would not test a no-repo cwd"
else
  pass "fixture workspace and root are in no git repository"
fi

printf '%s\n' '== P the account home folder comes from a fresh shell with an empty environment =='
HOME_LINE="  home=\"\$(/usr/bin/env -i /bin/bash --noprofile --norc -p -c 'printf \"%s\" ~' 2>/dev/null)\" || return 1"
count="$(grep -cxF "$HOME_LINE" "$GUARD")"
if [ "$count" = 1 ]; then
  pass "P1 the guard finds the home folder with one env -i /bin/bash tilde line"
else
  fail "P1 the env -i /bin/bash tilde line occurs $count times in the guard"
fi

printf '%s\n' '== A accepted: a regular file strictly below a listed root, from a no-repo cwd =='
roots_file "$ROOT"$'\n'"$ROOT2"$'\n'
run_case "A1 rm -f of a file below the first root" no_opinion "$WORKSPACE" "rm -f $ROOT/file.md" accepted
run_case "A2 rm -f of a file in a subfolder of the root" no_opinion "$WORKSPACE" "rm -f $ROOT/sub/deep.md" accepted
run_case "A3 rm -f of two files under two roots" no_opinion "$WORKSPACE" "rm -f $ROOT/file.md $ROOT2/other.md" accepted
run_case "A4 rm -f -- of a file below the root" no_opinion "$WORKSPACE" "rm -f -- $ROOT/second.md" accepted
run_case "A6 two rm -f commands, each judged on its own flags" no_opinion "$WORKSPACE" "rm -f $ROOT/file.md; rm -f $ROOT/second.md" accepted
roots_file "$ROOT"
run_case "A5 a last row with no newline" no_opinion "$WORKSPACE" "rm -f $ROOT/file.md" accepted

printf '%s\n' '== D denied: a target outside every root =='
roots_file "$ROOT"$'\n'
run_case "D1 file outside every root" deny "$WORKSPACE" "rm -f $OUTSIDE/file.md" unsafe_rm
run_case "D2 a sibling folder whose name extends the root's" deny "$WORKSPACE" "rm -f $SIBLING/file.md" unsafe_rm
run_case "D3 the root itself" deny "$WORKSPACE" "rm -f $ROOT" unsafe_rm
run_case "D4 a root file beside a file outside" deny "$WORKSPACE" "rm -f $ROOT/file.md $OUTSIDE/file.md" unsafe_rm
run_case "D5 a file above the root" deny "$WORKSPACE" "rm -f $TMP/top.md" unsafe_rm
run_case "D6 a dot-dot spelling into the root" deny "$WORKSPACE" "rm -f $ROOT/../r1/file.md" unsafe_rm
run_case "D7 a relative target" deny "$WORKSPACE" "rm -f file.md" cwd_outside_worktree
run_case "D8 a missing file below the root" deny "$WORKSPACE" "rm -f $ROOT/missing.md" unsafe_rm
run_case "D9 bare rm with no flag" deny "$WORKSPACE" "rm $ROOT/file.md" unsafe_rm

printf '%s\n' '== R denied: recursive, a directory, a symlink, a target in a repository =='
run_case "R1 -rf of a regular file below the root" deny "$WORKSPACE" "rm -rf $ROOT/file.md" unsafe_rm
run_case "R2 -fr" deny "$WORKSPACE" "rm -fr $ROOT/file.md" unsafe_rm
run_case "R3 -R -f" deny "$WORKSPACE" "rm -R -f $ROOT/file.md" unsafe_rm
run_case "R4 --recursive -f" deny "$WORKSPACE" "rm --recursive -f $ROOT/file.md" unsafe_rm
run_case "R5 -r after the target" deny "$WORKSPACE" "rm -f $ROOT/file.md -r" unsafe_rm
run_case "R6 -r alone" deny "$WORKSPACE" "rm -r $ROOT/file.md" unsafe_rm
run_case "R7 a directory below the root, -f" deny "$WORKSPACE" "rm -f $ROOT/sub/dir" unsafe_rm
run_case "R8 a directory below the root, -rf" deny "$WORKSPACE" "rm -rf $ROOT/sub" unsafe_rm
run_case "R9 a symlink below the root to a file outside" deny "$WORKSPACE" "rm -f $ROOT/link-out.md" unsafe_rm
run_case "R10 a symlink below the root to a file inside" deny "$WORKSPACE" "rm -f $ROOT/link-in.md" unsafe_rm
run_case "R11 a file reached through a symlinked root" deny "$WORKSPACE" "rm -f $TMP/alias-root/file.md" unsafe_rm
run_case "R12 a file in a git repository below the root" deny "$WORKSPACE" "rm -f $INNER_REPO/f.txt" unsafe_rm
run_case "R13 a .git path below the root" deny "$WORKSPACE" "rm -f $INNER_REPO/.git/HEAD" unsafe_rm
run_case "R14 a root file rm, then a recursive one" deny "$WORKSPACE" "rm -f $ROOT/file.md; rm -rf $ROOT/second.md" unsafe_rm

printf '%s\n' '== W a worktree cwd never reads the roots =='
run_case "W1 worktree cwd, absolute root file" deny "$REPO" "rm -f $ROOT/file.md" unsafe_rm
run_case "W2 worktree cwd, a subfolder, absolute root file" deny "$REPO/build" "rm -f $ROOT/file.md" unsafe_rm
run_case "W3 worktree cwd, a worktree target still accepted" no_opinion "$REPO" "rm -rf ./build" accepted

printf '%s\n' '== U an untrusted file denies every judged rm =='
roots_file "$ROOT"$'\n' 666
run_case "U1 a 0666 file, root target" deny "$WORKSPACE" "rm -f $ROOT/file.md" rm_roots_untrusted
run_case "U2 a 0666 file, worktree target" deny "$REPO" "rm -rf ./build" rm_roots_untrusted
run_case "U3 a 0666 file, a command with no rm keeps no opinion" no_opinion "$WORKSPACE" "ls $ROOT" not_explicit_operation
roots_file "$ROOT"$'\n' 620
run_case "U4 a group-writable file" deny "$WORKSPACE" "rm -f $ROOT/file.md" rm_roots_untrusted
roots_file "$ROOT"$'\n'
chmod 777 "$RAILS"
run_case "U5 a 0777 folder" deny "$WORKSPACE" "rm -f $ROOT/file.md" rm_roots_untrusted
chmod 700 "$RAILS"
rm -f "$FILE"
printf '%s\n' "$ROOT" >"$TMP/real-roots"
chmod 600 "$TMP/real-roots"
ln -s "$TMP/real-roots" "$FILE"
run_case "U6 a symlinked file naming a private file" deny "$WORKSPACE" "rm -f $ROOT/file.md" rm_roots_untrusted
rm -f "$FILE"
mkdir "$FILE"
run_case "U7 a folder in place of the file" deny "$WORKSPACE" "rm -f $ROOT/file.md" rm_roots_untrusted
rmdir "$FILE"
mkdir -p "$TMP/real-rails"
chmod 700 "$TMP/real-rails"
printf '%s\n' "$ROOT" >"$TMP/real-rails/rm-roots"
chmod 600 "$TMP/real-rails/rm-roots"
mv "$RAILS" "$TMP/rails-aside"
ln -s "$TMP/real-rails" "$RAILS"
run_case "U8 a symlinked folder" deny "$WORKSPACE" "rm -f $ROOT/file.md" rm_roots_untrusted
rm -f "$RAILS"
mv "$TMP/rails-aside" "$RAILS"

printf '%s\n' '== M a malformed file denies every judged rm =='
roots_file "$ROOT"$'\n\n'
run_case "M1 a blank row after a good one" deny "$WORKSPACE" "rm -f $ROOT/file.md" rm_roots_malformed
run_case "M2 a blank row, worktree target" deny "$REPO" "rm -rf ./build" rm_roots_malformed
roots_file $'\n'"$ROOT"$'\n'
run_case "M3 a blank first row" deny "$WORKSPACE" "rm -f $ROOT/file.md" rm_roots_malformed
roots_file ""
run_case "M4 an empty file" deny "$WORKSPACE" "rm -f $ROOT/file.md" rm_roots_malformed
roots_file "roots/r1"$'\n'"$ROOT"$'\n'
run_case "M5 a relative row" deny "$WORKSPACE" "rm -f $ROOT/file.md" rm_roots_malformed
roots_file "$ROOT/"$'\n'"$ROOT"$'\n'
run_case "M6 a trailing slash" deny "$WORKSPACE" "rm -f $ROOT/file.md" rm_roots_malformed
roots_file "$TMP/roots//r1"$'\n'"$ROOT"$'\n'
run_case "M7 a repeated slash" deny "$WORKSPACE" "rm -f $ROOT/file.md" rm_roots_malformed
roots_file "$TMP/roots/./r1"$'\n'"$ROOT"$'\n'
run_case "M8 a dot component" deny "$WORKSPACE" "rm -f $ROOT/file.md" rm_roots_malformed
roots_file "$ROOT/../r1"$'\n'"$ROOT"$'\n'
run_case "M9 a dot-dot component" deny "$WORKSPACE" "rm -f $ROOT/file.md" rm_roots_malformed
roots_file "$TMP/alias-root"$'\n'"$ROOT"$'\n'
run_case "M10 a symlinked folder row" deny "$WORKSPACE" "rm -f $ROOT/file.md" rm_roots_malformed
roots_file "$TMP/roots/missing"$'\n'"$ROOT"$'\n'
run_case "M11 a missing folder" deny "$WORKSPACE" "rm -f $ROOT/file.md" rm_roots_malformed
roots_file "$ROOT/file.md"$'\n'"$ROOT"$'\n'
run_case "M12 a file, not a folder" deny "$WORKSPACE" "rm -f $ROOT/file.md" rm_roots_malformed
roots_file $' '"$ROOT"$'\n'"$ROOT"$'\n'
run_case "M13 a leading space" deny "$WORKSPACE" "rm -f $ROOT/file.md" rm_roots_malformed
roots_file "$ROOT"$'\r\n'
run_case "M14 a carriage return" deny "$WORKSPACE" "rm -f $ROOT/file.md" rm_roots_malformed
roots_file "/"$'\n'
run_case "M15 the root folder" deny "$WORKSPACE" "rm -f $ROOT/file.md" rm_roots_malformed
roots_file "/Users"$'\n'
run_case "M16 /Users" deny "$WORKSPACE" "rm -f $ROOT/file.md" rm_roots_malformed
roots_file "$ACCT"$'\n'
run_case "M17 the home folder" deny "$WORKSPACE" "rm -f $ACCT/file.md" rm_roots_malformed
roots_file "$TMP"$'\n'
run_case "M18 a folder above the home folder" deny "$WORKSPACE" "rm -f $TMP/top.md" rm_roots_malformed
printf '%s\0\n' "$ROOT" >"$FILE"
chmod 600 "$FILE"
run_case "M19 a NUL byte" deny "$WORKSPACE" "rm -f $ROOT/file.md" rm_roots_malformed
rows=""
for _ in $(seq 1 65); do rows="$rows$ROOT"$'\n'; done
roots_file "$rows"
run_case "M20 65 rows" deny "$WORKSPACE" "rm -f $ROOT/file.md" rm_roots_malformed
rows=""
for _ in $(seq 1 64); do rows="$rows$ROOT"$'\n'; done
roots_file "$rows"
run_case "M21 64 rows are the limit" no_opinion "$WORKSPACE" "rm -f $ROOT/file.md" accepted
head -c 70000 /dev/zero | tr '\0' 'a' >"$FILE"
chmod 600 "$FILE"
run_case "M22 a file over 65536 bytes" deny "$WORKSPACE" "rm -f $ROOT/file.md" rm_roots_malformed

printf '%s\n' '== Z an absent file is dormant =='
RUN_GUARD="$COPY_NOFILE"
run_case "Z1 no file, root file" deny "$WORKSPACE" "rm -f $ROOT/file.md" unsafe_rm
run_case "Z2 no file, worktree target" no_opinion "$REPO" "rm -rf ./build" accepted
run_case "Z3 no file, outside target" deny "$WORKSPACE" "rm -f $OUTSIDE/file.md" unsafe_rm
RUN_GUARD="$COPY_NORAILS"
run_case "Z4 no rails folder, root file" deny "$WORKSPACE" "rm -f $ROOT/file.md" unsafe_rm
run_case "Z5 no rails folder, worktree target" no_opinion "$REPO" "rm -rf ./build" accepted
chmod 777 "$NOFILE_HOME/.claude/rails"
RUN_GUARD="$COPY_NOFILE"
run_case "Z6 no file in a 0777 folder is still dormant" deny "$WORKSPACE" "rm -f $ROOT/file.md" unsafe_rm
chmod 700 "$NOFILE_HOME/.claude/rails"

printf '%s\n' '== F a fake HOME holding a planted file changes nothing (the shipped guard) =='
printf '%s\n' "$ROOT" >"$FAKEHOME/.claude/rails/rm-roots"
chmod 600 "$FAKEHOME/.claude/rails/rm-roots"
RUN_GUARD="$GUARD"
run_case "F1 HOME names a folder whose rm-roots lists the root" deny "$WORKSPACE" "rm -f $ROOT/file.md" unsafe_rm -- HOME="$FAKEHOME"
RUN_GUARD="$COPY"

if [ "$FAILURES" -eq 0 ]; then
  printf 'ALL DESTRUCTIVE GUARD RM-ROOTS TESTS PASSED\n'
  exit 0
fi
printf '%s TEST(S) FAILED\n' "$FAILURES"
exit 1
