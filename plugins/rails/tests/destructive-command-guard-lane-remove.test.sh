#!/bin/bash
# Regression for registry-backed `git worktree remove`: a non-force removal of
# a linked worktree is accepted (the engine's no_opinion/accepted verdict,
# silent at the adapter; the guard never allows) only when the worktree is
# registered for the
# repository AND listed in the trusted lane-root registry named by
# LANE_ROOTS_REGISTRY AND its identity row, an unexpired prune authorization
# and the tree's live identity all agree. Everything is built under a fresh
# temp dir; command strings are inert JSON data and are never executed, except
# that one identity case runs the accepted removal on its own fixture tree to
# show the same token lets git remove it.
set -u
LC_ALL=C

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUARD="${LANE_REMOVE_TEST_GUARD:-$HERE/../hooks/destructive-command-guard.sh}"
JQ="$(command -v jq)" || { printf 'FAIL: jq is not on PATH\n'; exit 1; }

TMP_BASE="$(mktemp -d)" || exit 1
TMP="$(cd "$TMP_BASE" && pwd -P)"
PRIMARY="$TMP/primary"
OTHER="$TMP/other"
WT="$TMP/scratch/wt-a"
WT_B="$TMP/scratch/wt-b"
WT_STAR="$TMP/scratch/wt*star"
WT_UNLISTED="$TMP/scratch/wt-unlisted"
WT_FOREIGN="$TMP/scratch/wt-foreign"
PLAIN="$TMP/scratch/plain-dir"
SEAT="$TMP/seat"
REG_DIR="$TMP/seat/scratch"
REG="$REG_DIR/lane-roots.tsv"

# The registry / authorization rules below are exercised on worktrees that live
# under the temp roots, which the guard would otherwise accept as throwaway
# trees. Run this suite against a copy with the temp roots emptied so those
# rules stay observable; the temp-root rule has its own suite.
sed 's|^TEMP_ROOTS=.*|TEMP_ROOTS=()|' "$GUARD" >"$TMP/guard-no-temp-roots.sh" || exit 1
grep -q '^TEMP_ROOTS=()$' "$TMP/guard-no-temp-roots.sh" || { printf 'FAIL: guard has no TEMP_ROOTS constant to rewrite\n'; exit 1; }
GUARD_WITH_TEMP_ROOTS="$GUARD"
GUARD="$TMP/guard-no-temp-roots.sh"

git_q() { HOME="$TMP/githome" git -c user.name=t -c user.email=t@example.invalid "$@"; }
mkdir -p "$TMP/githome" "$TMP/scratch" "$REG_DIR"
for repo in "$PRIMARY" "$OTHER"; do
  git_q init -q --initial-branch=main "$repo" || exit 1
  printf 'x\n' >"$repo/f.txt"
  git_q -C "$repo" add f.txt && git_q -C "$repo" commit -qm seed || exit 1
done
for w in "$WT" "$WT_B" "$WT_STAR" "$WT_UNLISTED"; do
  git_q -C "$PRIMARY" worktree add -q --detach "$w" HEAD || exit 1
done
git_q -C "$OTHER" worktree add -q --detach "$WT_FOREIGN" HEAD || exit 1
mkdir -p "$PLAIN"
ln -s "$WT" "$TMP/alias-wt"
ln -s "$PRIMARY" "$TMP/alias-primary"

HEADER=$'path\trepo\tbranch\tcreated_at'
IDS_HEADER=$'lane-roots-ids/v1\tpath\tgeneration\tidentity\tseat\tregistered_at'
PENDING_HEADER=$'lane-prune-pending/v1\tpath\trepo\tgeneration\tidentity\texpires_at'
row() { printf '%s\t%s\t%s\t%s\n' "$1" "$2" "(detached)" "2026-09-30T00:00:00Z"; }

# Plant a 0600 32-lowercase-hex + LF nonce in the worktree's admin dir.
# Production writes are only the delegation loop's launcher; fixtures plant
# their own.
plant_nonce() { # WORKTREE [HEX]
  local admin f hex
  admin="$(HOME="$TMP/githome" git -C "$1" rev-parse --path-format=absolute --git-dir 2>/dev/null)" || return 1
  f="$admin/lane-roots-nonce"
  if [ "$#" -ge 2 ] && [ -n "$2" ]; then
    hex="$2"
  else
    hex="$(python3 -I -c 'import secrets; print(secrets.token_hex(16))')" || return 1
  fi
  printf '%s\n' "$hex" >"$f" || return 1
  chmod 0600 "$f" || return 1
  printf '%s' "$hex"
}

# The identity token, computed independently of the guard (and of the
# launcher's code): canonical admin dir, then st_dev and st_ino, then the
# birth time of the admin dir's gitdir file to the nanosecond,
# `<secs>.<nanos>`, then the planted nonce. Birth is read with the host's
# /usr/bin/stat in its own flavour and checked against os.stat's st_birthtime
# where Python has it (a float, so to within a microsecond).
token() {
  HOME="$TMP/githome" python3 -I -c 'import os, subprocess, sys
a = subprocess.check_output(["git", "-C", sys.argv[1], "rev-parse", "--path-format=absolute", "--git-dir"], text=True).strip()
g = os.path.join(a, "gitdir")
s = os.stat(g)
# The host stat in its own flavour, detected the way the guard does it: GNU
# stat answers --version, BSD stat does not. Python can cross-check the birth
# time only where os.stat reports st_birthtime (macOS).
if subprocess.run(["/usr/bin/stat", "--version"], capture_output=True).returncode == 0:
    b = subprocess.check_output(["/usr/bin/stat", "-c", "%.9W", "--", g], text=True).strip()
else:
    b = subprocess.check_output(["/usr/bin/stat", "-f", "%.9FB", "--", g], text=True).strip()
if hasattr(s, "st_birthtime"):
    assert abs(float(b) - s.st_birthtime) < 1e-6, (b, s.st_birthtime)
nf = os.path.join(os.path.realpath(a), "lane-roots-nonce")
raw = open(nf, "rb").read()
if len(raw) != 33 or raw[32:] != b"\n":
    raise SystemExit("token: nonce file is not 32 hex + LF")
hx = raw[:32].decode("ascii")
if any(c not in "0123456789abcdef" for c in hx):
    raise SystemExit("token: nonce is not lowercase hex")
print("%s:%d:%d:%s:%s" % (os.path.realpath(a), s.st_dev, s.st_ino, b, hx))' "$1"
}
NOW="$(/bin/date +%s)"
FRESH=$((NOW + 3600))
STALE=$((NOW - 1))
ids_row() { printf '%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "seat-test" "2026-10-21T00:00:00Z"; }
pend_row() { printf '%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" "$5"; }
for w in "$WT" "$WT_B" "$WT_STAR" "$WT_UNLISTED" "$WT_FOREIGN"; do
  plant_nonce "$w" >/dev/null || exit 1
done
TOKEN_WT="$(token "$WT")" || exit 1
TOKEN_WT_B="$(token "$WT_B")" || exit 1
TOKEN_WT_STAR="$(token "$WT_STAR")" || exit 1
TOKEN_WT_FOREIGN="$(token "$WT_FOREIGN")" || exit 1
write_registry() {
  {
    printf '%s\n' "$HEADER"
    row "$WT" "$PRIMARY"; row "$WT_B" "$PRIMARY"; row "$WT_STAR" "$PRIMARY"
    row "$PRIMARY" "$PRIMARY"; row "$PLAIN" "$PRIMARY"; row "$WT_FOREIGN" "$PRIMARY"
  } >"$REG"
  # Every listed worktree also has its recorded identity and a live prune
  # authorization, so the cases below exercise the registry rules alone.
  {
    printf '%s\n' "$IDS_HEADER"
    ids_row "$WT" 1 "$TOKEN_WT"; ids_row "$WT_B" 2 "$TOKEN_WT_B"
    ids_row "$WT_STAR" 3 "$TOKEN_WT_STAR"; ids_row "$WT_FOREIGN" 4 "$TOKEN_WT_FOREIGN"
  } >"$REG_DIR/lane-roots.ids.tsv"
  {
    printf '%s\n' "$PENDING_HEADER"
    pend_row "$WT" "$PRIMARY" 1 "$TOKEN_WT" "$FRESH"
    pend_row "$WT_B" "$PRIMARY" 2 "$TOKEN_WT_B" "$FRESH"
    pend_row "$WT_STAR" "$PRIMARY" 3 "$TOKEN_WT_STAR" "$FRESH"
    pend_row "$WT_FOREIGN" "$PRIMARY" 4 "$TOKEN_WT_FOREIGN" "$FRESH"
  } >"$REG_DIR/lane-prune-pending.tsv"
  chmod 600 "$REG" "$REG_DIR/lane-roots.ids.tsv" "$REG_DIR/lane-prune-pending.tsv"; chmod 700 "$REG_DIR"
}
write_registry

FAILURES=0
pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; FAILURES=$((FAILURES + 1)); }

RUN_DIR="$TMP"
# run_case LABEL EXPECT(accepted|notaccepted|deny) CWD COMMAND [REGISTRY_ENV]
# accepted is the engine's no_opinion/accepted verdict.
run_case() {
  local label="$1" expect="$2" cwd="$3" command_data="$4" regenv="${5-$REG}" input output decision reason
  input="$($JQ -cn --arg cwd "$cwd" --arg command "$command_data" '{cwd:$cwd,command:$command}')"
  if [ "$regenv" = "__UNSET__" ]; then
    output="$(cd "$RUN_DIR" && printf '%s' "$input" | env -u LANE_ROOTS_REGISTRY HOME="$TMP/githome" /bin/bash --noprofile --norc -p "$GUARD" --policy 2>/dev/null)"
  else
    output="$(cd "$RUN_DIR" && printf '%s' "$input" | env LANE_ROOTS_REGISTRY="$regenv" HOME="$TMP/githome" /bin/bash --noprofile --norc -p "$GUARD" --policy 2>/dev/null)"
  fi
  decision="$(printf '%s' "$output" | "$JQ" -er '.decision' 2>/dev/null || printf invalid)"
  reason="$(printf '%s' "$output" | "$JQ" -er '.reason' 2>/dev/null || printf invalid)"
  [ "$decision/$reason" != no_opinion/accepted ] || decision=accepted
  case "$expect:$decision" in
    accepted:accepted|deny:deny) pass "$label -> $decision" ;;
    notaccepted:accepted|notaccepted:invalid|accepted:*|deny:*) fail "$label: expected $expect, got $decision/$reason" ;;
    notaccepted:*) pass "$label -> $decision (not accepted)" ;;
  esac
}

printf '%s\n' '== allowed forms =='
run_case "plain form, registered + listed" accepted "$PRIMARY" "git worktree remove $WT"
run_case "-C form from an unrelated cwd" accepted "$OTHER" "git -C $PRIMARY worktree remove $WT_B"
run_case "symlinked alias of a listed worktree" accepted "$PRIMARY" "git worktree remove $TMP/alias-wt"

printf '%s\n' '== force and extra words =='
run_case "--force before path" deny "$PRIMARY" "git worktree remove --force $WT"
run_case "-f before path" deny "$PRIMARY" "git worktree remove -f $WT"
run_case "--force after path" deny "$PRIMARY" "git worktree remove $WT --force"
run_case "-C form with --force" deny "$OTHER" "git -C $PRIMARY worktree remove --force $WT"
run_case "-C form with -f after path" deny "$OTHER" "git -C $PRIMARY worktree remove $WT -f"
run_case "lone flag as path" deny "$PRIMARY" "git worktree remove --force"
run_case "second path (plain)" deny "$PRIMARY" "git worktree remove $WT $WT_B"
run_case "second path (-C form)" deny "$OTHER" "git -C $PRIMARY worktree remove $WT $WT_B"

printf '%s\n' '== glob, relative and metacharacter paths =='
run_case "quoted literal star in a listed path" deny "$PRIMARY" "git worktree remove '$WT_STAR'"
run_case "unquoted glob" deny "$PRIMARY" "git worktree remove $TMP/scratch/wt-*"
run_case "relative path" deny "$PRIMARY" "git worktree remove ../scratch/wt-a"
RUN_DIR="$TMP/scratch"
run_case "relative path that resolves from the process directory" deny "$PRIMARY" "git worktree remove wt-a"
RUN_DIR="$TMP"
run_case "path with command substitution" deny "$PRIMARY" "git worktree remove \$(echo $WT)"

printf '%s\n' '== primary, unregistered and unlisted =='
run_case "primary checkout, listed in registry" deny "$OTHER" "git -C $PRIMARY worktree remove $PRIMARY"
run_case "primary checkout via symlink alias" deny "$OTHER" "git -C $PRIMARY worktree remove $TMP/alias-primary"
run_case "primary checkout, plain form" deny "$PRIMARY" "git worktree remove $PRIMARY"
run_case "plain directory, listed but not a worktree" deny "$PRIMARY" "git worktree remove $PLAIN"
run_case "worktree of another repo, listed with this repo" deny "$PRIMARY" "git worktree remove $WT_FOREIGN"
run_case "registered worktree absent from registry" deny "$PRIMARY" "git worktree remove $WT_UNLISTED"
run_case "registry row names a different repo" deny "$OTHER" "git -C $OTHER worktree remove $WT_FOREIGN"
run_case "cwd is the target" deny "$WT" "git worktree remove $WT"
run_case "cwd is the target (-C form)" deny "$WT" "git -C $PRIMARY worktree remove $WT"
mkdir -p "$WT/nested" && git_q init -q --initial-branch=main "$WT/nested" || exit 1
run_case "cwd strictly inside the target, in a nested repository" deny "$WT/nested" "git -C $PRIMARY worktree remove $WT"

printf '%s\n' '== -C form: every denial =='
run_case "-C form, unquoted glob" deny "$OTHER" "git -C $PRIMARY worktree remove $TMP/scratch/wt-*"
run_case "-C form, listed path containing a star" deny "$OTHER" "git -C $PRIMARY worktree remove '$WT_STAR'"
run_case "-C form, command substitution" deny "$OTHER" "git -C $PRIMARY worktree remove \$(echo $WT)"
run_case "-C form, relative target" deny "$OTHER" "git -C $PRIMARY worktree remove ../scratch/wt-a"
run_case "-C form, relative repository" deny "$OTHER" "git -C ../primary worktree remove $WT"
run_case "-C form, relative repository that resolves from the process directory" deny "$OTHER" "git -C primary worktree remove $WT_B"
run_case "-C form, plain directory listed but not a worktree" deny "$OTHER" "git -C $PRIMARY worktree remove $PLAIN"
run_case "-C form, worktree of another repo" deny "$OTHER" "git -C $PRIMARY worktree remove $WT_FOREIGN"
run_case "-C form, registered worktree absent from registry" deny "$OTHER" "git -C $PRIMARY worktree remove $WT_UNLISTED"
run_case "-C form, env var unset" deny "$OTHER" "git -C $PRIMARY worktree remove $WT_B" __UNSET__
run_case "-C form, env var empty" deny "$OTHER" "git -C $PRIMARY worktree remove $WT_B" ""
run_case "-C form, env var relative" deny "$OTHER" "git -C $PRIMARY worktree remove $WT_B" "seat/scratch/lane-roots.tsv"
run_case "-C form, extra word before the operation" deny "$OTHER" "git -C $PRIMARY -C $PRIMARY worktree remove $WT_B"
run_case "-C form, trailing extra word" deny "$OTHER" "git -C $PRIMARY worktree remove $WT_B extra"
run_case "-C form, --force twice" deny "$OTHER" "git -C $PRIMARY worktree remove --force --force $WT_B"

printf '%s\n' '== compound commands: any failing removal denies the whole command =='
for sep in ';' '&&'; do
  run_case "plain valid $sep plain forced" deny "$PRIMARY" "git worktree remove $WT $sep git worktree remove --force $WT_B"
  run_case "plain forced $sep plain valid" deny "$PRIMARY" "git worktree remove --force $WT_B $sep git worktree remove $WT"
  run_case "-C valid $sep -C forced" deny "$OTHER" "git -C $PRIMARY worktree remove $WT_B $sep git -C $PRIMARY worktree remove --force $WT"
  run_case "-C forced $sep -C valid" deny "$OTHER" "git -C $PRIMARY worktree remove --force $WT $sep git -C $PRIMARY worktree remove $WT_B"
  run_case "-C valid $sep plain forced" deny "$PRIMARY" "git -C $PRIMARY worktree remove $WT_B $sep git worktree remove --force $WT"
  run_case "plain valid $sep -C unlisted" deny "$PRIMARY" "git worktree remove $WT $sep git -C $PRIMARY worktree remove $WT_UNLISTED"
  run_case "-C unlisted $sep plain valid" deny "$PRIMARY" "git -C $PRIMARY worktree remove $WT_UNLISTED $sep git worktree remove $WT"
  run_case "-C valid $sep -C valid still allowed" accepted "$OTHER" "git -C $PRIMARY worktree remove $WT_B $sep git -C $PRIMARY worktree remove $WT"
done

printf '%s\n' '== registry location =='
run_case "env var unset" deny "$PRIMARY" "git worktree remove $WT" __UNSET__
run_case "env var empty" deny "$PRIMARY" "git worktree remove $WT" ""
run_case "env var relative" deny "$PRIMARY" "git worktree remove $WT" "seat/scratch/lane-roots.tsv"
run_case "env var relative, file present from the process directory" deny "$PRIMARY" "git worktree remove $WT" "seat/scratch/lane-roots.tsv"
run_case "env var names a missing file" deny "$PRIMARY" "git worktree remove $WT" "$REG_DIR/absent/lane-roots.tsv"
run_case "inline assignment in the command is not honored" notaccepted "$PRIMARY" "LANE_ROOTS_REGISTRY=$REG git worktree remove $WT" "$TMP/none/lane-roots.tsv"
run_case "registry path with dot-dot" deny "$PRIMARY" "git worktree remove $WT" "$REG_DIR/../scratch/lane-roots.tsv"

printf '%s\n' '== registry the seat did not create =='
reset_reg() { chmod 700 "$REG_DIR"; [ ! -d "$REG" ] || rmdir "$REG"; rm -f "$REG" "$REG_DIR/real.tsv"; write_registry; }
try_reg() { # LABEL SETUP-FUNC
  local label="$1"; shift
  reset_reg; "$@"
  run_case "$label" deny "$PRIMARY" "git worktree remove $WT"
  reset_reg
}
mk_symlink() { mv "$REG" "$REG_DIR/real.tsv"; ln -s "$REG_DIR/real.tsv" "$REG"; }
try_reg "registry is a symlink" mk_symlink
mk_dir() { rm -f "$REG"; mkdir "$REG"; }
try_reg "registry is a directory" mk_dir
mk_gw() { chmod 620 "$REG"; }
try_reg "registry is group-writable" mk_gw
mk_ow() { chmod 602 "$REG"; }
try_reg "registry is world-writable" mk_ow
mk_dgw() { chmod 770 "$REG_DIR"; }
try_reg "registry directory is group-writable" mk_dgw
mk_dow() { chmod 707 "$REG_DIR"; }
try_reg "registry directory is world-writable" mk_dow
mk_badhead() { printf 'nope\n' >"$REG"; row "$WT" "$PRIMARY" >>"$REG"; }
try_reg "registry lacks the expected header" mk_badhead
mkdir -p "$TMP/other-name"
cp "$REG" "$TMP/other-name/registry.tsv"; chmod 600 "$TMP/other-name/registry.tsv"; chmod 700 "$TMP/other-name"
run_case "registry has the wrong file name" deny "$PRIMARY" "git worktree remove $WT" "$TMP/other-name/registry.tsv"
run_case "registry restored still allows" accepted "$PRIMARY" "git worktree remove $WT"

printf '%s\n' '== identity binding: recorded identity, prune authorization, live identity =='
# The launcher refuses to authorize a prune unless the installed guard carries
# this exact marker line, so the line itself is part of the contract.
if grep -qx '# lane-roots-identity: v1' "$GUARD_WITH_TEMP_ROOTS"; then
  pass "guard carries the lane-roots-identity: v1 marker line"
else
  fail "guard lacks the exact '# lane-roots-identity: v1' marker line"
fi
ID_DIR="$TMP/idseat/scratch"
ID_REG="$ID_DIR/lane-roots.tsv"
ID_IDS="$ID_DIR/lane-roots.ids.tsv"
ID_PEND="$ID_DIR/lane-prune-pending.tsv"
ID_GOOD="$TMP/idtrees/good"
ID_RECREATE="$TMP/idtrees/recreate"
ID_BIRTH0="$TMP/idtrees/birth0"
ID_NOGITDIR="$TMP/idtrees/nogitdir"
ID_REMOVE="$TMP/idtrees/remove"
ID_TEMPONLY="$TMP/idtrees/temp-only"
mkdir -p "$ID_DIR" "$TMP/idtrees"
chmod 700 "$ID_DIR"
for w in "$ID_GOOD" "$ID_RECREATE" "$ID_BIRTH0" "$ID_NOGITDIR" "$ID_REMOVE" "$ID_TEMPONLY"; do
  git_q -C "$PRIMARY" worktree add -q --detach "$w" HEAD || exit 1
done
{
  printf '%s\n' "$HEADER"
  for w in "$ID_GOOD" "$ID_RECREATE" "$ID_BIRTH0" "$ID_NOGITDIR" "$ID_REMOVE"; do row "$w" "$PRIMARY"; done
} >"$ID_REG"
chmod 600 "$ID_REG"
for w in "$ID_GOOD" "$ID_RECREATE" "$ID_BIRTH0" "$ID_NOGITDIR" "$ID_REMOVE" "$ID_TEMPONLY"; do
  plant_nonce "$w" >/dev/null || exit 1
done
T_GOOD="$(token "$ID_GOOD")" || exit 1

put_ids() { { printf '%s\n' "$IDS_HEADER"; [ "$#" -eq 0 ] || printf '%s' "$@"; } >"$ID_IDS"; chmod 600 "$ID_IDS"; }
put_pending() { { printf '%s\n' "$PENDING_HEADER"; [ "$#" -eq 0 ] || printf '%s' "$@"; } >"$ID_PEND"; chmod 600 "$ID_PEND"; }
put_good() { # TARGET TOKEN [GEN]: matching identity row and live authorization
  put_ids "$(ids_row "$1" "${3:-7}" "$2")"$'\n'
  put_pending "$(pend_row "$1" "$PRIMARY" "${3:-7}" "$2" "$FRESH")"$'\n'
}
idcase() { # LABEL EXPECT TARGET
  run_case "$1" "$2" "$PRIMARY" "git worktree remove $3" "$ID_REG"
}

put_good "$ID_GOOD" "$T_GOOD"
idcase "identity: recorded, authorized and live identity agree" accepted "$ID_GOOD"
run_case "identity: -C form, all three agree" accepted "$OTHER" "git -C $PRIMARY worktree remove $ID_GOOD" "$ID_REG"

# G1: a registry row with no recorded identity (a legacy row) is denied.
put_good "$ID_GOOD" "$T_GOOD"
rm -f "$ID_IDS"
idcase "G1 legacy row: no identity file at all" deny "$ID_GOOD"
put_good "$ID_GOOD" "$T_GOOD"
put_ids "$(ids_row "$ID_REMOVE" 7 "$T_GOOD")"$'\n'
idcase "G1 legacy row: identity file has no row for the path" deny "$ID_GOOD"
put_good "$ID_GOOD" "$T_GOOD"
put_ids
idcase "G1 legacy row: identity file holds only the header" deny "$ID_GOOD"

# G2: a tree removed and re-created at the same path gets a new identity, so
# the identity recorded (and authorized) for the old tree no longer matches.
T_OLD="$(token "$ID_RECREATE")" || exit 1
git_q -C "$PRIMARY" worktree remove "$ID_RECREATE" || exit 1
git_q -C "$PRIMARY" worktree add -q --detach "$ID_RECREATE" HEAD || exit 1
plant_nonce "$ID_RECREATE" >/dev/null || exit 1
T_NEW="$(token "$ID_RECREATE")" || exit 1
if [ "$T_OLD" = "$T_NEW" ]; then fail "G2 fixture: re-created tree kept its identity token ($T_OLD)"; fi
put_good "$ID_RECREATE" "$T_OLD"
idcase "G2 re-created tree: recorded and authorized identity is the old tree's" deny "$ID_RECREATE"
put_good "$ID_RECREATE" "$T_NEW"
idcase "G2 re-created tree: its own new identity is accepted" accepted "$ID_RECREATE"

# G3: no authorization, or an expired one, is denied.
put_good "$ID_GOOD" "$T_GOOD"
rm -f "$ID_PEND"
idcase "G3 no pending file" deny "$ID_GOOD"
put_good "$ID_GOOD" "$T_GOOD"
put_pending
idcase "G3 pending file holds only the header" deny "$ID_GOOD"
put_good "$ID_GOOD" "$T_GOOD"
put_pending "$(pend_row "$ID_REMOVE" "$PRIMARY" 7 "$T_GOOD" "$FRESH")"$'\n'
idcase "G3 pending entry names another path" deny "$ID_GOOD"
put_good "$ID_GOOD" "$T_GOOD"
put_pending "$(pend_row "$ID_GOOD" "$PRIMARY" 7 "$T_GOOD" "$STALE")"$'\n'
idcase "G3 pending entry expired" deny "$ID_GOOD"
put_good "$ID_GOOD" "$T_GOOD"
put_pending "$(pend_row "$ID_GOOD" "$PRIMARY" 7 "$T_GOOD" "$NOW")"$'\n'
idcase "G3 pending entry expiring at the time it was read" deny "$ID_GOOD"
put_good "$ID_GOOD" "$T_GOOD"
put_pending "$(pend_row "$ID_GOOD" "$PRIMARY" 7 "$T_GOOD" "$FRESH")"$'\n' \
  "$(pend_row "$ID_GOOD" "$PRIMARY" 7 "$T_GOOD" "$STALE")"$'\n'
idcase "G3 newest pending entry for the path wins (expired after a live one)" deny "$ID_GOOD"
put_good "$ID_GOOD" "$T_GOOD"
put_pending "$(pend_row "$ID_GOOD" "$PRIMARY" 7 "$T_GOOD" "$STALE")"$'\n' \
  "$(pend_row "$ID_GOOD" "$PRIMARY" 7 "$T_GOOD" "$FRESH")"$'\n'
idcase "G3 newest pending entry for the path wins (live after an expired one)" accepted "$ID_GOOD"
put_good "$ID_GOOD" "$T_GOOD"
put_pending "$(pend_row "$ID_GOOD" "$OTHER" 7 "$T_GOOD" "$FRESH")"$'\n'
idcase "G3 pending entry names another repository" deny "$ID_GOOD"

# G4: the authorized generation must be the path's current recorded one.
put_good "$ID_GOOD" "$T_GOOD"
put_pending "$(pend_row "$ID_GOOD" "$PRIMARY" 8 "$T_GOOD" "$FRESH")"$'\n'
idcase "G4 pending generation differs from the recorded one" deny "$ID_GOOD"
put_good "$ID_GOOD" "$T_GOOD"
put_ids "$(ids_row "$ID_GOOD" 7 "$T_GOOD")"$'\n' "$(ids_row "$ID_GOOD" 9 "$T_GOOD")"$'\n'
idcase "G4 pending names an older generation than the path's current one" deny "$ID_GOOD"
put_good "$ID_GOOD" "$T_GOOD"
put_ids "$(ids_row "$ID_GOOD" 7 "${T_GOOD}x")"$'\n'
idcase "G4 recorded identity differs from the authorized one" deny "$ID_GOOD"

# G5: the token is byte-equal to the independent computation above (the
# allowed cases prove it); a birth time of 0, a missing gitdir file, a token
# with microseconds, or a whole-second token (the old form) is refused; the
# allowed removal really removes.
# (The birth-time-0 case runs after G8 below, with G8's stub stat, so it
# runs where no tool can set a file's birth time.)
T_NG="$(token "$ID_NOGITDIR")" || exit 1
NG_ADMIN="$(git_q -C "$ID_NOGITDIR" rev-parse --path-format=absolute --git-dir)" || exit 1
rm -f "$NG_ADMIN/gitdir"
put_good "$ID_NOGITDIR" "$T_NG"
idcase "G5 missing gitdir file is refused" deny "$ID_NOGITDIR"
# The microsecond form, built from the nanosecond token: the same admin dir,
# device and inode, and the birth time as whole microseconds.
T_US="$(python3 -I -c 'import sys
admin, dev, ino, birth, _nonce = sys.argv[1].rsplit(":", 4)
secs, nanos = birth.split(".")
print("%s:%s:%s:%d" % (admin, dev, ino, int(secs) * 1000000 + int(nanos) // 1000))' "$T_GOOD")" || exit 1
put_good "$ID_GOOD" "$T_US"
idcase "G5 a microsecond-precision token is not the identity" deny "$ID_GOOD"
T_SEC="${T_GOOD%.*}"
case "$T_SEC" in *:*:*:[1-9]*) ;; *) fail "G5 fixture: no whole-second form of $T_GOOD" ;; esac
put_good "$ID_GOOD" "$T_SEC"
idcase "G5 a whole-second token (the old form) is not the identity" deny "$ID_GOOD"
T_REMOVE="$(token "$ID_REMOVE")" || exit 1
put_good "$ID_REMOVE" "$T_REMOVE"
idcase "G5 good identity is allowed" accepted "$ID_REMOVE"
if git_q -C "$PRIMARY" worktree remove "$ID_REMOVE" && [ ! -e "$ID_REMOVE" ]; then
  pass "G5 the allowed removal removes the tree"
else
  fail "G5 the allowed removal did not remove $ID_REMOVE"
fi

# G6: a malformed header or row in either file, or a file the seat did not
# write privately, denies.
g6() { # LABEL SETUP-FUNC
  local label="$1"; shift
  put_good "$ID_GOOD" "$T_GOOD"; "$@"
  idcase "$label" deny "$ID_GOOD"
  rm -f "$ID_IDS" "$ID_PEND" "$ID_DIR/real-ids.tsv"
}
bad_ids_header() { { printf 'lane-roots-ids/v2\tpath\tgeneration\tidentity\tseat\tregistered_at\n'; ids_row "$ID_GOOD" 7 "$T_GOOD"; } >"$ID_IDS"; }
g6 "G6 identity file header differs" bad_ids_header
bad_pend_header() { { printf 'path\trepo\tgeneration\tidentity\texpires_at\n'; pend_row "$ID_GOOD" "$PRIMARY" 7 "$T_GOOD" "$FRESH"; } >"$ID_PEND"; }
g6 "G6 pending file header differs" bad_pend_header
ids_gw() { chmod 620 "$ID_IDS"; }
g6 "G6 identity file is group-writable" ids_gw
ids_ow() { chmod 602 "$ID_IDS"; }
g6 "G6 identity file is world-writable" ids_ow
pend_gw() { chmod 620 "$ID_PEND"; }
g6 "G6 pending file is group-writable" pend_gw
pend_ow() { chmod 602 "$ID_PEND"; }
g6 "G6 pending file is world-writable" pend_ow
ids_symlink() { mv "$ID_IDS" "$ID_DIR/real-ids.tsv"; ln -s "$ID_DIR/real-ids.tsv" "$ID_IDS"; }
g6 "G6 identity file is a symlink" ids_symlink
ids_bad_gen() { printf '%s\t%s\t%s\t%s\t%s\n' "$ID_REMOVE" "two" "$T_GOOD" "s" "t" >>"$ID_IDS"; }
g6 "G6 identity file has a non-numeric generation on another path's row" ids_bad_gen
ids_empty_gen() { printf '%s\t%s\t%s\t%s\t%s\n' "$ID_REMOVE" "" "$T_GOOD" "s" "t" >>"$ID_IDS"; }
g6 "G6 identity file has an empty generation on another path's row" ids_empty_gen
ids_empty_ident() { printf '%s\t%s\t%s\t%s\t%s\n' "$ID_REMOVE" "3" "" "s" "t" >>"$ID_IDS"; }
g6 "G6 identity file has an empty identity on another path's row" ids_empty_ident
ids_short_row() { printf '%s\t%s\t%s\n' "$ID_REMOVE" "3" "$T_GOOD" >>"$ID_IDS"; }
g6 "G6 identity file has a row with too few columns" ids_short_row
pend_bad_exp() { printf '%s\t%s\t%s\t%s\t%s\n' "$ID_REMOVE" "$PRIMARY" 3 "$T_GOOD" "soon" >>"$ID_PEND"; }
g6 "G6 pending file has a non-numeric expiry on another path's row" pend_bad_exp
pend_extra_col() { printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$ID_REMOVE" "$PRIMARY" 3 "$T_GOOD" "$FRESH" extra >>"$ID_PEND"; }
g6 "G6 pending file has a row with too many columns" pend_extra_col
put_good "$ID_GOOD" "$T_GOOD"
idcase "G6 files restored still allow" accepted "$ID_GOOD"

# The guard's identity lines: the STAT_CANDIDATES line, then stat_fields,
# stat_owner_mode, owned_and_private, canonical_dir, is_small_decimal,
# lane_nonce and live_worktree_identity, each from its `name() {` line
# through the first line that is `}`. Printed as text, or as the SHA-256
# of that text.
IDENTITY_LINES_PY='import hashlib, sys
lines = open(sys.argv[1], encoding="utf-8").read().split("\n")
picked = [l for l in lines if l.startswith("STAT_CANDIDATES=")]
assert len(picked) == 1, "STAT_CANDIDATES lines: %d" % len(picked)
for name in ("stat_fields", "stat_owner_mode", "owned_and_private", "canonical_dir", "is_small_decimal", "lane_nonce", "live_worktree_identity"):
    starts = [i for i, l in enumerate(lines) if l.startswith(name + "() {")]
    assert len(starts) == 1, "%s definitions: %d" % (name, len(starts))
    picked += lines[starts[0]:lines.index("}", starts[0]) + 1]
text = "\n".join(picked)
print(hashlib.sha256(text.encode()).hexdigest() if sys.argv[2] == "digest" else text)'

# G8: a tree removed and re-created at the same path, with the same device,
# the same inode and the same birth second, still gets a new identity; so does
# one re-created across a second boundary. The device, inode and birth time
# are set by a stub stat on a fixed path (11, 22 and the birth written to
# $BIRTH_CTL); a whole-second request is answered in whole seconds, as a real
# stat answers it. The guard's own identity lines run against the stub, and a
# copy of the whole guard whose STAT_CANDIDATES names the stub decides.
G8_BIN="$TMP/g8-bin"
BIRTH_CTL="$TMP/g8-birth"
mkdir -p "$G8_BIN"
cat >"$G8_BIN/stat" <<STUB
#!/bin/bash
if [ "\$#" = 4 ] && [ "\$3" = "--" ]; then
  case "\$1 \$2" in
    "-f %d %i %.9FB"|"-c %d %i %.9W") printf '11 22 %s\n' "\$(cat "$BIRTH_CTL")"; exit 0 ;;
    "-f %d %i %B"|"-c %d %i %W") b="\$(cat "$BIRTH_CTL")"; printf '11 22 %s\n' "\${b%.*}"; exit 0 ;;
  esac
fi
exec /usr/bin/stat "\$@"
STUB
chmod 755 "$G8_BIN/stat"
python3 -I -c "$IDENTITY_LINES_PY" "$GUARD" lines >"$TMP/g8-identity.sh" || exit 1
sed -i.bak "s|^STAT_CANDIDATES=.*|STAT_CANDIDATES=($G8_BIN/stat)|" "$TMP/g8-identity.sh" || exit 1
grep -qx "STAT_CANDIDATES=($G8_BIN/stat)" "$TMP/g8-identity.sh" || { fail "G8 fixture: identity lines have no STAT_CANDIDATES to rewrite"; exit 1; }
sed "s|^STAT_CANDIDATES=.*|STAT_CANDIDATES=($G8_BIN/stat)|" "$GUARD" >"$TMP/guard-g8-stat.sh" || exit 1
grep -qx "STAT_CANDIDATES=($G8_BIN/stat)" "$TMP/guard-g8-stat.sh" || { fail "G8 fixture: guard has no STAT_CANDIDATES to rewrite"; exit 1; }
stub_identity() { HOME="$TMP/githome" /bin/bash -c '. "$1"; live_worktree_identity "$2"' g8 "$TMP/g8-identity.sh" "$1"; }
stubcase() { # LABEL EXPECT TARGET: decided by the guard copy that uses the stub stat
  local saved="$GUARD"
  GUARD="$TMP/guard-g8-stat.sh"
  idcase "$@"
  GUARD="$saved"
}
ID_SAME="$TMP/idtrees/same"
git_q -C "$PRIMARY" worktree add -q --detach "$ID_SAME" HEAD || exit 1
row "$ID_SAME" "$PRIMARY" >>"$ID_REG"
recreate_case() { # LABEL OLD_BIRTH NEW_BIRTH SAME_SECOND(yes|no)
  local label="$1" t_old t_new
  printf '%s' "$2" >"$BIRTH_CTL"
  plant_nonce "$ID_SAME" >/dev/null || exit 1
  t_old="$(stub_identity "$ID_SAME")" || { fail "$label: the old tree has no identity"; return; }
  git_q -C "$PRIMARY" worktree remove "$ID_SAME" || exit 1
  git_q -C "$PRIMARY" worktree add -q --detach "$ID_SAME" HEAD || exit 1
  plant_nonce "$ID_SAME" >/dev/null || exit 1
  printf '%s' "$3" >"$BIRTH_CTL"
  t_new="$(stub_identity "$ID_SAME")" || { fail "$label: the re-created tree has no identity"; return; }
  if [ "$t_old" != "$t_new" ]; then
    pass "$label: the re-created tree has a new identity"
  else
    fail "$label: the re-created tree kept the old identity ($t_old)"
  fi
  if [ "$4" = yes ]; then
    case "$t_old" in *:11:22:*) ;; *) fail "$label fixture: old token has no stub device and inode ($t_old)" ;; esac
    if [ "${t_old%.*}" = "${t_new%.*}" ]; then
      pass "$label: the old and new tree share admin dir, device, inode and birth second"
    else
      fail "$label fixture: the trees do not share the birth second ($t_old, $t_new)"
    fi
  fi
  put_good "$ID_SAME" "$t_old"
  stubcase "$label: the old tree's recorded and authorized identity is denied" deny "$ID_SAME"
  put_good "$ID_SAME" "$t_new"
  stubcase "$label: the new tree's own identity is allowed" accepted "$ID_SAME"
}
recreate_case "G8 same second, same device and inode" 1792670984.123456789 1792670984.987654321 yes
recreate_case "G8 across a second boundary" 1792670984.999999999 1792670985.000000000 no
recreate_case "G8 the next second, same nanoseconds" 1792670984.500000000 1792670985.500000000 no

# G5, birth time 0: the stub stat reports birth 0 for the tree's gitdir. The
# recorded and authorized token carries that same birth, so the only reason
# left to refuse is the birth time itself; the control row, with a real birth
# time and its own token, is accepted.
ID_B0_ADMIN="$(git_q -C "$ID_BIRTH0" rev-parse --path-format=absolute --git-dir)" || exit 1
ID_B0_ADMIN="$(cd "$ID_B0_ADMIN" && pwd -P)" || exit 1
B0_NONCE="$(plant_nonce "$ID_BIRTH0")" || exit 1
put_good "$ID_BIRTH0" "$ID_B0_ADMIN:11:22:0.000000000:$B0_NONCE"
printf '%s' "0.000000000" >"$BIRTH_CTL"
stubcase "G5 birth time 0 is refused" deny "$ID_BIRTH0"
printf '%s' "1792670984.123456789" >"$BIRTH_CTL"
t_b0_control="$(stub_identity "$ID_BIRTH0")" || { fail "G5 control: the tree has no identity"; t_b0_control=""; }
if [ "$t_b0_control" = "$ID_B0_ADMIN:11:22:1792670984.123456789:$B0_NONCE" ]; then
  pass "G5 control: the stub identity has the expected shape"
else
  fail "G5 control: the stub identity is '$t_b0_control'"
fi
put_good "$ID_BIRTH0" "$t_b0_control"
stubcase "G5 control: the same tree with a real birth time is accepted" accepted "$ID_BIRTH0"

# G9: the delegation loop's launcher prints the same token, and its tests run
# a verbatim copy of these identity lines. Both pin the SHA-256 of the same
# lines; changing them here fails this check until the pin, and the
# launcher's copy and pin, are refreshed.
PAIRED_IDENTITY_SHA256="4c144231d2a11f66c4e532b1bc565848d60e0b93ec0b470196f63f60d67d7658"
got_digest="$(python3 -I -c "$IDENTITY_LINES_PY" "$GUARD_WITH_TEMP_ROOTS" digest)" || got_digest="(no digest)"
if [ "$got_digest" = "$PAIRED_IDENTITY_SHA256" ]; then
  pass "G9 the guard's identity lines match the digest the launcher's copy pins"
else
  fail "G9 the guard's identity lines drifted from the launcher's pinned copy (got $got_digest, pinned $PAIRED_IDENTITY_SHA256)"
fi

# G10: per-creation nonce. Fixtures plant the file (0600, 32 hex + LF).
# Any unreadable nonce makes live_worktree_identity return 1, so the
# removal denies with the existing unsafe_worktree_remove JSON.
python3 -I -c "$IDENTITY_LINES_PY" "$GUARD_WITH_TEMP_ROOTS" lines >"$TMP/ident-only.sh" || exit 1
ident_probe() { # WORKTREE
  HOME="$TMP/githome" /bin/bash -c '. "$1"; live_worktree_identity "$2"' ident "$TMP/ident-only.sh" "$1"
}
restore_good_nonce() {
  local admin f
  admin="$(HOME="$TMP/githome" git -C "$ID_GOOD" rev-parse --path-format=absolute --git-dir 2>/dev/null)" || exit 1
  f="$admin/lane-roots-nonce"
  rm -f "$f"
  rm -f "$admin/lane-roots-nonce.real"
  plant_nonce "$ID_GOOD" "${T_GOOD##*:}" >/dev/null || exit 1
}
write_nonce_kind() { # WORKTREE KIND
  local admin
  admin="$(HOME="$TMP/githome" git -C "$1" rev-parse --path-format=absolute --git-dir 2>/dev/null)" || return 1
  HOME="$TMP/githome" python3 -I -c 'import os, sys
kind, admin = sys.argv[1], sys.argv[2]
p = os.path.join(admin, "lane-roots-nonce")
hx = b"0123456789abcdef0123456789abcdef"
body = {
  "short": b"abcd\n",
  "uppercase": b"0123456789ABCDEF0123456789ABCDEF\n",
  "crlf": hx + b"\r\n",
  "extra": hx + b"\nX",
  "nul": hx[:16] + b"\x00" + hx[17:] + b"\n",
}[kind]
open(p, "wb").write(body)
os.chmod(p, 0o600)
' "$2" "$admin"
}
nonce_ident_denied() { # LABEL SETUP-FUNC
  local label="$1" rc out
  restore_good_nonce
  put_good "$ID_GOOD" "$T_GOOD"
  "$2"
  rc=0
  out="$(ident_probe "$ID_GOOD" 2>/dev/null)" || rc=$?
  if [ "$rc" -ne 0 ]; then
    pass "$label: live_worktree_identity rc $rc"
  else
    fail "$label: live_worktree_identity succeeded ($out)"
  fi
  idcase "$label: remove is denied" deny "$ID_GOOD"
  restore_good_nonce
  put_good "$ID_GOOD" "$T_GOOD"
}

restore_good_nonce
put_good "$ID_GOOD" "$T_GOOD"
idcase "nonce: permit allows when the planted nonce matches" accepted "$ID_GOOD"

nk_missing() {
  local admin
  admin="$(HOME="$TMP/githome" git -C "$ID_GOOD" rev-parse --path-format=absolute --git-dir 2>/dev/null)" || exit 1
  rm -f "$admin/lane-roots-nonce"
}
nonce_ident_denied "nonce: missing file" nk_missing
nk_short() { write_nonce_kind "$ID_GOOD" short; }
nonce_ident_denied "nonce: short" nk_short
nk_upper() { write_nonce_kind "$ID_GOOD" uppercase; }
nonce_ident_denied "nonce: uppercase" nk_upper
nk_crlf() { write_nonce_kind "$ID_GOOD" crlf; }
nonce_ident_denied "nonce: CRLF" nk_crlf
nk_extra() { write_nonce_kind "$ID_GOOD" extra; }
nonce_ident_denied "nonce: extra byte" nk_extra
nk_nul() { write_nonce_kind "$ID_GOOD" nul; }
nonce_ident_denied "nonce: NUL" nk_nul
nk_symlink() {
  local admin f
  admin="$(HOME="$TMP/githome" git -C "$ID_GOOD" rev-parse --path-format=absolute --git-dir 2>/dev/null)" || exit 1
  f="$admin/lane-roots-nonce"
  mv "$f" "$admin/lane-roots-nonce.real"
  ln -s "$admin/lane-roots-nonce.real" "$f"
}
nonce_ident_denied "nonce: symlink" nk_symlink
nk_0666() {
  local admin
  admin="$(HOME="$TMP/githome" git -C "$ID_GOOD" rev-parse --path-format=absolute --git-dir 2>/dev/null)" || exit 1
  chmod 0666 "$admin/lane-roots-nonce"
}
nonce_ident_denied "nonce: mode 0666" nk_0666
nk_0644() {
  local admin
  admin="$(HOME="$TMP/githome" git -C "$ID_GOOD" rev-parse --path-format=absolute --git-dir 2>/dev/null)" || exit 1
  chmod 0644 "$admin/lane-roots-nonce"
}
nonce_ident_denied "nonce: mode 0644" nk_0644
nk_0640() {
  local admin
  admin="$(HOME="$TMP/githome" git -C "$ID_GOOD" rev-parse --path-format=absolute --git-dir 2>/dev/null)" || exit 1
  chmod 0640 "$admin/lane-roots-nonce"
}
nonce_ident_denied "nonce: mode 0640" nk_0640
nk_0400() {
  local admin
  admin="$(HOME="$TMP/githome" git -C "$ID_GOOD" rev-parse --path-format=absolute --git-dir 2>/dev/null)" || exit 1
  chmod 0400 "$admin/lane-roots-nonce"
}
nonce_ident_denied "nonce: mode 0400" nk_0400
nk_0700() {
  local admin
  admin="$(HOME="$TMP/githome" git -C "$ID_GOOD" rev-parse --path-format=absolute --git-dir 2>/dev/null)" || exit 1
  chmod 0700 "$admin/lane-roots-nonce"
}
nonce_ident_denied "nonce: mode 0700" nk_0700
nk_1600() {
  local admin
  admin="$(HOME="$TMP/githome" git -C "$ID_GOOD" rev-parse --path-format=absolute --git-dir 2>/dev/null)" || exit 1
  chmod 1600 "$admin/lane-roots-nonce" || fail "nonce: mode 1600 fixture: chmod refused"
  python3 -I -c 'import os, sys; sys.exit(0 if os.stat(sys.argv[1]).st_mode & 0o7777 == 0o1600 else 1)' "$admin/lane-roots-nonce" \
    || fail "nonce: mode 1600 fixture: the file is not mode 1600"
}
nonce_ident_denied "nonce: mode 1600 (sticky bit set)" nk_1600
restore_good_nonce
put_good "$ID_GOOD" "$T_GOOD"
idcase "nonce: mode 0600 allows" accepted "$ID_GOOD"

# The nonce mode as stat reports it. A stub stat on a fixed path answers the
# guard's whole-mode read of lane-roots-nonce with this user's uid and the
# mode written to $MODE_CTL; every other call goes to /usr/bin/stat. A copy of
# the identity lines and a copy of the whole guard whose STAT_CANDIDATES names
# the stub decide, so setuid and setgid (which a test cannot always set) and
# mode strings of an unexpected shape are covered with no seam in the guard.
MODE_BIN="$TMP/mode-bin"
MODE_CTL="$TMP/mode-ctl"
mkdir -p "$MODE_BIN"
cat >"$MODE_BIN/stat" <<STUB
#!/bin/bash
if [ "\$#" = 4 ] && [ "\$3" = "--" ]; then
  case "\$1 \$2:\${4##*/}" in
    "-f %u %Mp%Lp:lane-roots-nonce"|"-c %u %a:lane-roots-nonce") printf '%s %s\n' "\$(/usr/bin/id -u)" "\$(cat "$MODE_CTL")"; exit 0 ;;
  esac
fi
exec /usr/bin/stat "\$@"
STUB
chmod 755 "$MODE_BIN/stat"
python3 -I -c "$IDENTITY_LINES_PY" "$GUARD" lines >"$TMP/mode-identity.sh" || exit 1
sed -i.bak "s|^STAT_CANDIDATES=.*|STAT_CANDIDATES=($MODE_BIN/stat)|" "$TMP/mode-identity.sh" || exit 1
grep -qx "STAT_CANDIDATES=($MODE_BIN/stat)" "$TMP/mode-identity.sh" || { fail "mode fixture: identity lines have no STAT_CANDIDATES to rewrite"; exit 1; }
sed "s|^STAT_CANDIDATES=.*|STAT_CANDIDATES=($MODE_BIN/stat)|" "$GUARD" >"$TMP/guard-mode-stat.sh" || exit 1
grep -qx "STAT_CANDIDATES=($MODE_BIN/stat)" "$TMP/guard-mode-stat.sh" || { fail "mode fixture: guard has no STAT_CANDIDATES to rewrite"; exit 1; }
modecase() { # LABEL REPORTED-MODE EXPECT(accepted|deny)
  local label="$1" rc out saved
  printf '%s' "$2" >"$MODE_CTL"
  restore_good_nonce
  put_good "$ID_GOOD" "$T_GOOD"
  rc=0
  out="$(HOME="$TMP/githome" /bin/bash -c '. "$1"; live_worktree_identity "$2"' mode "$TMP/mode-identity.sh" "$ID_GOOD" 2>/dev/null)" || rc=$?
  if [ "$3" = accepted ] && [ "$rc" -eq 0 ] && [ "$out" = "$T_GOOD" ]; then
    pass "$label: live_worktree_identity is the tree's token"
  elif [ "$3" = deny ] && [ "$rc" -ne 0 ]; then
    pass "$label: live_worktree_identity rc $rc"
  else
    fail "$label: live_worktree_identity rc=$rc out=$out, want $3"
  fi
  saved="$GUARD"
  GUARD="$TMP/guard-mode-stat.sh"
  idcase "$label: remove" "$3" "$ID_GOOD"
  GUARD="$saved"
}
modecase "nonce: stat reports 0600" 0600 accepted
modecase "nonce: stat reports 600" 600 accepted
modecase "nonce: stat reports 1600" 1600 deny
modecase "nonce: stat reports 2600" 2600 deny
modecase "nonce: stat reports 4600" 4600 deny
modecase "nonce: stat reports 7600" 7600 deny
modecase "nonce: stat reports 0644" 0644 deny
modecase "nonce: stat reports 0400" 0400 deny
modecase "nonce: stat reports an empty mode" "" deny
modecase "nonce: stat reports a non-octal mode" 6x0 deny
modecase "nonce: stat reports five digits, 00600" 00600 deny
modecase "nonce: stat reports five digits, 10600" 10600 deny
restore_good_nonce
put_good "$ID_GOOD" "$T_GOOD"

T_LEGACY="${T_GOOD%:*}"
put_good "$ID_GOOD" "$T_LEGACY"
idcase "nonce: legacy ids/permit row without the nonce suffix is denied" deny "$ID_GOOD"
rc=0
out="$(ident_probe "$ID_GOOD" 2>/dev/null)" || rc=$?
if [ "$rc" -eq 0 ] && [ "$out" = "$T_GOOD" ]; then
  pass "nonce: live identity still readable on a legacy row"
else
  fail "nonce: live identity rc=$rc out=$out want=$T_GOOD"
fi
put_good "$ID_GOOD" "$T_GOOD"

# Same tree removed and re-added with the stub stat held at
# 11 22 1792670984.123456789: missing nonce denies; a different
# nonce denies; the new tree's own token allows.
printf '%s' "1792670984.123456789" >"$BIRTH_CTL"
plant_nonce "$ID_SAME" "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" >/dev/null || exit 1
t_held="$(stub_identity "$ID_SAME")" || { fail "held-stat: the old tree has no identity"; t_held=""; }
put_good "$ID_SAME" "$t_held"
stubcase "nonce: held-stat tree is allowed before re-add" accepted "$ID_SAME"
git_q -C "$PRIMARY" worktree remove "$ID_SAME" || exit 1
git_q -C "$PRIMARY" worktree add -q --detach "$ID_SAME" HEAD || exit 1
printf '%s' "1792670984.123456789" >"$BIRTH_CTL"
put_good "$ID_SAME" "$t_held"
stubcase "nonce: re-add with held stat 11 22 1792670984.123456789 and no nonce is denied" deny "$ID_SAME"
plant_nonce "$ID_SAME" "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" >/dev/null || exit 1
t_newheld="$(stub_identity "$ID_SAME")" || { fail "held-stat: the re-created tree has no identity"; t_newheld=""; }
if [ -n "$t_held" ] && [ -n "$t_newheld" ] && [ "$t_held" != "$t_newheld" ]; then
  pass "nonce: held-stat re-add with a different nonce has a new token"
else
  fail "nonce: held-stat re-add kept the token ($t_held)"
fi
put_good "$ID_SAME" "$t_held"
stubcase "nonce: re-add with held stat and a different nonce is denied" deny "$ID_SAME"
put_good "$ID_SAME" "$t_newheld"
stubcase "nonce: re-add with held stat and its own nonce is allowed" accepted "$ID_SAME"

# Closure extracted alone, valid nonce, rc 0.
python3 -I -c "$IDENTITY_LINES_PY" "$GUARD_WITH_TEMP_ROOTS" lines >"$TMP/closure-alone.sh" || exit 1
rc=0
out="$(HOME="$TMP/githome" /bin/bash -c '. "$1"; live_worktree_identity "$2"' cl "$TMP/closure-alone.sh" "$ID_GOOD")" || rc=$?
if [ "$rc" -eq 0 ] && [ "$out" = "$T_GOOD" ]; then
  pass "nonce: extracted closure runs alone with a valid nonce (rc 0)"
else
  fail "nonce: extracted closure rc=$rc out=$out want=$T_GOOD"
fi

# Deny JSON stays byte-identical: no new reason names.
nk_missing
put_good "$ID_GOOD" "$T_GOOD"
input="$($JQ -cn --arg cwd "$PRIMARY" --arg command "git worktree remove $ID_GOOD" '{cwd:$cwd,command:$command}')"
output="$(cd "$RUN_DIR" && printf '%s' "$input" | env LANE_ROOTS_REGISTRY="$ID_REG" HOME="$TMP/githome" /bin/bash --noprofile --norc -p "$GUARD" --policy 2>/dev/null)"
want='{"decision":"deny","reason":"unsafe_worktree_remove"}'
if [ "$output" = "$want" ]; then
  pass "nonce: unsafe_worktree_remove JSON is unchanged"
else
  fail "nonce: deny JSON was '$output', want '$want'"
fi
restore_good_nonce
put_good "$ID_GOOD" "$T_GOOD"

# G7: the temp-root rule for unregistered trees and the --force denial are
# unchanged by the identity binding (this uses the guard with its real temp
# roots; the fixtures live under mktemp -d).
case "$TMP" in
  /tmp/*|/private/tmp/*|/var/folders/*|/private/var/folders/*) ;;
  *) fail "G7 fixture: $TMP is not below a temp root" ;;
esac
run_case "G7 unregistered linked worktree, no temp roots: denied" deny "$PRIMARY" \
  "git worktree remove $ID_TEMPONLY" "$ID_REG"
put_good "$ID_GOOD" "$T_GOOD"
for forced in "--force $ID_GOOD" "-f $ID_GOOD" "$ID_GOOD --force"; do
  run_case "G7 fully authorized row, still no $forced" deny "$PRIMARY" "git worktree remove $forced" "$ID_REG"
done
SAVED_GUARD="$GUARD"
GUARD="$GUARD_WITH_TEMP_ROOTS"
# A registered tree under a configured temp root whose identity fails must
# deny. The temp-root fallback is only for an unregistered target.
restore_good_nonce
put_good "$ID_GOOD" "$T_GOOD"
nk_missing
idcase "registered under temp root, missing nonce: denied" deny "$ID_GOOD"
restore_good_nonce
put_good "$ID_GOOD" "$T_GOOD"
put_good "$ID_RECREATE" "$T_OLD"
idcase "registered under temp root, re-created with a different nonce: denied" deny "$ID_RECREATE"
put_good "$ID_RECREATE" "$T_NEW"
rm -f "$ID_IDS" "$ID_PEND"
idcase "registered under temp root, no ids/pending sidecars: denied" deny "$ID_GOOD"
put_good "$ID_GOOD" "$T_GOOD"
run_case "G7 temp-root guard: unregistered linked worktree allowed" accepted "$PRIMARY" "git worktree remove $ID_TEMPONLY" "$ID_REG"
run_case "unregistered linked worktree under the temp root still allows" accepted "$PRIMARY" "git worktree remove $ID_TEMPONLY" "$ID_REG"
run_case "G7 temp-root guard: --force still denied" deny "$PRIMARY" "git worktree remove --force $ID_TEMPONLY" "$ID_REG"

printf '%s\n' '== the temp-root fallback needs a fully validated registry =='
# Still with the real temp roots. A registry that cannot be read or validated
# denies by name, so a registered tree under a temp root can never reach the
# temp-root rule that way. Only a registry read in full with no row for the
# target, or no registry at all, falls through to that rule. Each case
# compares the whole policy JSON.
R4_DIR="$TMP/r4seat/scratch"
R4_REG="$R4_DIR/lane-roots.tsv"
mkdir -p "$R4_DIR"
chmod 700 "$R4_DIR"
{ printf '%s\n' "$IDS_HEADER"; ids_row "$ID_GOOD" 7 "$T_GOOD"; } >"$R4_DIR/lane-roots.ids.tsv"
{ printf '%s\n' "$PENDING_HEADER"; pend_row "$ID_GOOD" "$PRIMARY" 7 "$T_GOOD" "$FRESH"; } >"$R4_DIR/lane-prune-pending.tsv"
chmod 600 "$R4_DIR/lane-roots.ids.tsv" "$R4_DIR/lane-prune-pending.tsv"
r4_reg() { # LINE...: the registry is the header, then these lines
  rm -f "$R4_REG"
  { printf '%s\n' "$HEADER"; [ "$#" -eq 0 ] || printf '%s\n' "$@"; } >"$R4_REG"
  chmod 600 "$R4_REG"
}
r4case() { # LABEL DECISION REASON TARGET REGISTRY_ENV(__UNSET__ = unset)
  local input output want
  input="$($JQ -cn --arg cwd "$PRIMARY" --arg command "git worktree remove $4" '{cwd:$cwd,command:$command}')"
  if [ "$5" = "__UNSET__" ]; then
    output="$(cd "$RUN_DIR" && printf '%s' "$input" | env -u LANE_ROOTS_REGISTRY HOME="$TMP/githome" /bin/bash --noprofile --norc -p "$GUARD" --policy 2>/dev/null)"
  else
    output="$(cd "$RUN_DIR" && printf '%s' "$input" | env LANE_ROOTS_REGISTRY="$5" HOME="$TMP/githome" /bin/bash --noprofile --norc -p "$GUARD" --policy 2>/dev/null)"
  fi
  want="{\"decision\":\"$2\",\"reason\":\"$3\"}"
  if [ "$output" = "$want" ]; then
    pass "$1 -> $2/$3"
  else
    fail "$1: got '$output', want '$want'"
  fi
}
restore_good_nonce
put_good "$ID_GOOD" "$T_GOOD"
r4_reg "$(row "$ID_GOOD" "$PRIMARY")"
r4case "R4 registered and authorized under a temp root" no_opinion accepted "$ID_GOOD" "$R4_REG"
r4case "R4 validated registry, no row for the target: temp-root rule" no_opinion accepted "$ID_TEMPONLY" "$R4_REG"

# Untrusted registry.
r4_reg "$(row "$ID_GOOD" "$PRIMARY")"
chmod 620 "$R4_REG"
r4case "R4 group-writable registry: registered tree denied" deny lane_registry_untrusted "$ID_GOOD" "$R4_REG"
r4_reg "$(row "$ID_GOOD" "$PRIMARY")"
mv "$R4_REG" "$R4_DIR/real.tsv"
ln -s "$R4_DIR/real.tsv" "$R4_REG"
r4case "R4 symlinked registry: registered tree denied" deny lane_registry_untrusted "$ID_GOOD" "$R4_REG"
rm -f "$R4_REG" "$R4_DIR/real.tsv"
ln -s "$R4_DIR/nowhere.tsv" "$R4_REG"
r4case "R4 dangling symlink at the registry path is not an absent registry" deny lane_registry_untrusted "$ID_GOOD" "$R4_REG"
rm -f "$R4_REG"
r4case "R4 registry path in a directory that does not resolve" deny lane_registry_untrusted "$ID_GOOD" "$TMP/r4-no-dir/lane-roots.tsv"
r4case "R4 relative registry path" deny lane_registry_untrusted "$ID_GOOD" "r4seat/scratch/lane-roots.tsv"

# Malformed header.
r4_reg
{ printf 'nope\n'; row "$ID_GOOD" "$PRIMARY"; } >"$R4_REG"
r4case "R4 malformed header, the target's row kept: denied" deny lane_registry_bad_header "$ID_GOOD" "$R4_REG"
: >"$R4_REG"
r4case "R4 empty registry file: denied" deny lane_registry_bad_header "$ID_GOOD" "$R4_REG"

# Malformed rows.
r4_reg "$(row "$ID_GOOD" "$PRIMARY")" "$(printf '%s\t%s\t%s' "$ID_NOGITDIR" "$PRIMARY" x)"
r4case "R4 row with three fields: denied" deny lane_registry_bad_row "$ID_GOOD" "$R4_REG"
r4_reg "$(row "$ID_GOOD" "$PRIMARY")" "$(row "idtrees/nogitdir" "$PRIMARY")"
r4case "R4 row with a relative path: denied" deny lane_registry_bad_row "$ID_GOOD" "$R4_REG"
r4_reg "$(row "$ID_GOOD" "$PRIMARY")" "$(row "$ID_NOGITDIR" "primary")"
r4case "R4 row with a relative repository: denied" deny lane_registry_bad_row "$ID_GOOD" "$R4_REG"
r4_reg "$(row "$ID_GOOD" "$PRIMARY")" ""
r4case "R4 blank row: denied" deny lane_registry_bad_row "$ID_GOOD" "$R4_REG"
r4_reg "$(row "$ID_GOOD" "$TMP/r4-no-such-repo")"
r4case "R4 the target's row names a repository that does not resolve: denied" deny lane_registry_bad_row "$ID_GOOD" "$R4_REG"
# A row that names the target through a directory the guard cannot search
# does not resolve, but it is not provably gone: it may be the target.
R4_LOCKED="$TMP/r4-locked"
mkdir -p "$R4_LOCKED"
ln -s "$ID_GOOD" "$R4_LOCKED/alias"
chmod 000 "$R4_LOCKED"
r4_reg "$(row "$R4_LOCKED/alias" "$PRIMARY")"
r4case "R4 row naming the target through an unsearchable directory: denied" deny lane_registry_bad_row "$ID_GOOD" "$R4_REG"
chmod 700 "$R4_LOCKED"
# A stale row whose path names nothing (the tree, or its whole parent, is
# gone) cannot be the target, which exists; it is skipped.
r4_reg "$(row "$TMP/idtrees/never-created" "$PRIMARY")" "$(row "$TMP/r4-gone/deep/wt" "$PRIMARY")"
r4case "R4 stale rows for trees already gone are skipped: temp-root rule" no_opinion accepted "$ID_TEMPONLY" "$R4_REG"
r4_reg "$(row "$ID_GOOD" "$PRIMARY")" "$(printf '%s\t%s' "$ID_NOGITDIR" "$PRIMARY")"
r4case "R4 malformed row after the target's own row: denied" deny lane_registry_bad_row "$ID_GOOD" "$R4_REG"

# A row naming the target for another repository.
r4_reg "$(row "$ID_GOOD" "$OTHER")"
r4case "R4 the target's row names another repository: denied" deny lane_registry_repo_mismatch "$ID_GOOD" "$R4_REG"

# No registry at all: nothing is registered, so the temp-root rule applies
# (the throwaway-tree path the pins and differential suites rely on).
r4case "R4 LANE_ROOTS_REGISTRY unset: temp-root rule" no_opinion accepted "$ID_GOOD" __UNSET__
r4case "R4 LANE_ROOTS_REGISTRY empty: temp-root rule" no_opinion accepted "$ID_GOOD" ""
rm -f "$R4_REG"
# An armed registry variable that names no file denies by name; before, it
# meant "no registry" and the temp-root rule accepted.
r4case "R4 no file at the configured registry path: armed and absent, denied" deny lane_registry_absent_armed "$ID_GOOD" "$R4_REG"

printf '%s\n' '== identity rows must be what the launcher writes =='
# Still with the real temp roots. The launcher writes each path once,
# absolute and already canonical, with a generation of at least 1. Each case
# gives the registered target a live permit and an identity row that matches
# it, so without these rules every case below would be allowed. Each one
# compares the whole policy JSON.
R5_DIR="$TMP/r5seat/scratch"
R5_REG="$R5_DIR/lane-roots.tsv"
mkdir -p "$R5_DIR"
chmod 700 "$R5_DIR"
{ printf '%s\n' "$HEADER"; row "$ID_GOOD" "$PRIMARY"; } >"$R5_REG"
chmod 600 "$R5_REG"
ln -s "$ID_GOOD" "$TMP/r5-alias-good"
r5_ids() { # PERMIT_GENERATION ROW...: the target's permit, then these identity rows
  local gen="$1"; shift
  { printf '%s\n' "$PENDING_HEADER"; pend_row "$ID_GOOD" "$PRIMARY" "$gen" "$T_GOOD" "$FRESH"; } >"$R5_DIR/lane-prune-pending.tsv"
  { printf '%s\n' "$IDS_HEADER"; printf '%s\n' "$@"; } >"$R5_DIR/lane-roots.ids.tsv"
  chmod 600 "$R5_DIR/lane-prune-pending.tsv" "$R5_DIR/lane-roots.ids.tsv"
}
r5case() { r4case "$1" "$2" "$3" "$ID_GOOD" "$R5_REG"; }
T_OTHER_NONCE="${T_GOOD%:*}:ffffffffffffffffffffffffffffffff"
restore_good_nonce
r5_ids 7 "$(ids_row "$ID_GOOD" 7 "$T_GOOD")" "$(ids_row "$ID_RECREATE" 8 "$T_NEW")"
r5case "R5 well-formed identity rows still allow" no_opinion accepted
r5_ids 0 "$(ids_row "$ID_GOOD" 0 "$T_GOOD")"
r5case "R5 generation 0, permit for generation 0: denied" deny lane_ids_bad_generation
r5_ids 7 "$(ids_row "idtrees/good" 7 "$T_GOOD")"
r5case "R5 relative path that resolves to the target from the process directory: denied" deny lane_ids_relative_path
r5_ids 7 "$(ids_row "$TMP/r5-alias-good" 7 "$T_GOOD")"
r5case "R5 absolute path through a symlink to the target: denied" deny lane_ids_noncanonical_path
r5_ids 7 "$(ids_row "$ID_GOOD/" 7 "$T_GOOD")"
r5case "R5 absolute path with a trailing slash: denied" deny lane_ids_noncanonical_path
# A row for a tree that is gone stays until the launcher's next sweep. It
# is skipped, so it neither blocks nor stands in for the target's row, but
# its path must still be spelled canonically.
mkdir -p "$TMP/idtrees/r5-removed"
rmdir "$TMP/idtrees/r5-removed"
r5_ids 7 "$(ids_row "$TMP/idtrees/r5-removed" 3 "$T_GOOD")" "$(ids_row "$ID_GOOD" 7 "$T_GOOD")"
r5case "R5 a stale row for a removed tree is skipped: allowed" no_opinion accepted
r5_ids 7 "$(ids_row "$ID_GOOD" 7 "$T_GOOD")" "$(ids_row "$TMP/idtrees/never-created/../r5-removed" 3 "$T_GOOD")"
r5case "R5 a stale row with a .. component: denied" deny lane_ids_noncanonical_path
r5_ids 7 "$(ids_row "$ID_GOOD" 7 "$T_GOOD")" "$(ids_row "$TMP/idtrees/./r5-removed" 3 "$T_GOOD")"
r5case "R5 a stale row with a . component: denied" deny lane_ids_noncanonical_path
r5_ids 7 "$(ids_row "$ID_GOOD" 7 "$T_GOOD")" "$(ids_row "$TMP/idtrees//r5-removed" 3 "$T_GOOD")"
r5case "R5 a stale row with a // in its path: denied" deny lane_ids_noncanonical_path
r5_ids 7 "$(ids_row "$ID_GOOD" 7 "$T_GOOD")" "$(ids_row "$TMP/idtrees/r5-removed/" 3 "$T_GOOD")"
r5case "R5 a stale row with a trailing slash: denied" deny lane_ids_noncanonical_path
# The launcher stores the resolved path, so a stale row is checked against
# its nearest existing ancestor resolved, with the missing part appended.
mkdir -p "$TMP/r6-real"
ln -s "$TMP/r6-real" "$TMP/r6-link"
r5_ids 7 "$(ids_row "$TMP/r6-link/r6-removed" 3 "$T_GOOD")" "$(ids_row "$ID_GOOD" 7 "$T_GOOD")"
r5case "R6 a stale row whose parent is a symlink to a real directory: denied" deny lane_ids_noncanonical_path
r5_ids 7 "$(ids_row "$TMP/r6-link/gone/r6-removed" 3 "$T_GOOD")" "$(ids_row "$ID_GOOD" 7 "$T_GOOD")"
r5case "R6 a stale row two missing levels below a symlink: denied" deny lane_ids_noncanonical_path
r5_ids 7 "$(ids_row "$TMP/r6-real/r6-removed" 3 "$T_GOOD")" "$(ids_row "$ID_GOOD" 7 "$T_GOOD")"
r5case "R6 the same stale row under the resolved parent is skipped: allowed" no_opinion accepted
r5_ids 7 "$(ids_row "$TMP/r6-real/gone/r6-removed" 3 "$T_GOOD")" "$(ids_row "$ID_GOOD" 7 "$T_GOOD")"
r5case "R6 a stale row two missing levels below the resolved parent is skipped: allowed" no_opinion accepted
r5_ids 7 "$(ids_row "$ID_GOOD" 7 "$T_GOOD")" "$(ids_row "$TMP/idtrees/r5-removed" 3 "$T_GOOD")" "$(ids_row "$TMP/idtrees/r5-removed" 4 "$T_GOOD")"
r5case "R5 a stale tree's path twice: denied" deny lane_ids_duplicate_path
r5_ids 7 "$(ids_row "$ID_GOOD" 7 "$T_GOOD")" "$(ids_row "$ID_GOOD" 7 "$T_GOOD")"
r5case "R5 the target's path twice with the same identity: denied" deny lane_ids_duplicate_path
r5_ids 7 "$(ids_row "$ID_GOOD" 7 "$T_GOOD")" "$(ids_row "$ID_GOOD" 7 "$T_OTHER_NONCE")"
r5case "R5 the target's path twice, same generation, conflicting identities: denied" deny lane_ids_duplicate_path
r5_ids 7 "$(ids_row "$ID_GOOD" 7 "$T_GOOD")" "$(ids_row "$ID_RECREATE" 8 "$T_NEW")" "$(ids_row "$ID_RECREATE" 9 "$T_NEW")"
r5case "R5 another tree's path twice: denied" deny lane_ids_duplicate_path
r5_ids 7 "$(ids_row "$ID_GOOD" 7 "$T_GOOD")"
r5case "R5 rows restored still allow" no_opinion accepted
GUARD="$SAVED_GUARD"

if [ "$FAILURES" -eq 0 ]; then
  printf '%s\n' 'ALL LANE-REMOVE TESTS PASSED'
  printf 'Fixtures retained at %s (test performs no deletion)\n' "$TMP"
  exit 0
fi
printf '%s TEST(S) FAILED\n' "$FAILURES"
exit 1
