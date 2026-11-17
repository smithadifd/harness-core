#!/bin/bash
# Regression for forced `git worktree remove` spelled through git global
# options or command prefixes, and for the registry owner / stat-flavour
# checks. Everything is built under a fresh temp dir; command strings are
# inert JSON data and are never executed. The guard resolves `stat` from fixed
# absolute paths only, so the BSD and the GNU syntax are exercised on any host
# by running a copy of the guard whose absolute-path constant is rewritten to
# point at a stub; PATH is never consulted for `stat`.
#
# Out of scope by design: `bash -c '...'`, `xargs`, `find -exec`, shell loops
# that build a removal at run time, and shell control keywords (`if`, `then`,
# `while`). The exec/time/builtin/nice/sudo prefixes and the subshell, brace
# and substitution wrappers are covered by destructive-command-guard-wrappers.
set -u
LC_ALL=C

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUARD="${GIT_FORMS_TEST_GUARD:-$HERE/../hooks/destructive-command-guard.sh}"
JQ="$(command -v jq)" || { printf 'FAIL: jq is not on PATH\n'; exit 1; }

TMP_BASE="$(mktemp -d)" || exit 1
TMP="$(cd "$TMP_BASE" && pwd -P)"
PRIMARY="$TMP/primary"
OTHER="$TMP/other"
WT="$TMP/scratch/wt-a"
WT_B="$TMP/scratch/wt-b"
WT_C="$TMP/scratch/wt-c"
REG_DIR="$TMP/seat/scratch"
REG="$REG_DIR/lane-roots.tsv"
STUBS="$TMP/stubs"
GUARD_COPIES="$TMP/guards"

git_q() { HOME="$TMP/githome" git -c user.name=t -c user.email=t@example.invalid "$@"; }
mkdir -p "$TMP/githome" "$TMP/scratch" "$REG_DIR" "$STUBS/bsd" "$STUBS/gnu" "$STUBS/uutils" "$STUBS/plant" "$GUARD_COPIES"
for repo in "$PRIMARY" "$OTHER"; do
  git_q init -q --initial-branch=main "$repo" || exit 1
  printf 'x\n' >"$repo/f.txt"
  git_q -C "$repo" add f.txt && git_q -C "$repo" commit -qm seed || exit 1
done
for w in "$WT" "$WT_B" "$WT_C"; do
  git_q -C "$PRIMARY" worktree add -q --detach "$w" HEAD || exit 1
done
{
  printf 'path\trepo\tbranch\tcreated_at\n'
  for w in "$WT" "$WT_B" "$WT_C"; do
    printf '%s\t%s\t%s\t%s\n' "$w" "$PRIMARY" "(detached)" "2026-09-30T00:00:00Z"
  done
} >"$REG"
chmod 600 "$REG"; chmod 700 "$REG_DIR"
# Each registered tree also gets its recorded identity and a live prune
# authorization beside the registry. The token is computed here, independently
# of the guard: canonical admin dir, st_dev, st_ino and the birth time of the
# admin dir's gitdir file to the nanosecond (`<secs>.<nanos>`, read with
# the host's /usr/bin/stat in its own flavour and checked against os.stat's
# st_birthtime where Python has it).
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
write_identity_files() { # DIR REPO TREE...
  local dir="$1" repo="$2" gen=0 w t exp
  shift 2
  exp=$(($(/bin/date +%s) + 3600))
  printf 'lane-roots-ids/v1\tpath\tgeneration\tidentity\tseat\tregistered_at\n' >"$dir/lane-roots.ids.tsv"
  printf 'lane-prune-pending/v1\tpath\trepo\tgeneration\tidentity\texpires_at\n' >"$dir/lane-prune-pending.tsv"
  for w in "$@"; do
    gen=$((gen + 1))
    plant_nonce "$w" >/dev/null || return 1
    t="$(token "$w")" || return 1
    printf '%s\t%s\t%s\t%s\t%s\n' "$w" "$gen" "$t" "seat-test" "2026-10-21T00:00:00Z" >>"$dir/lane-roots.ids.tsv"
    printf '%s\t%s\t%s\t%s\t%s\n' "$w" "$repo" "$gen" "$t" "$exp" >>"$dir/lane-prune-pending.tsv"
  done
  chmod 600 "$dir/lane-roots.ids.tsv" "$dir/lane-prune-pending.tsv"
}
write_identity_files "$REG_DIR" "$PRIMARY" "$WT" "$WT_B" "$WT_C" || exit 1

# Stub stat binaries. Each one only understands its own flavour and fails on
# the other, so a hook that picks the wrong syntax cannot pass by accident.
# STUB_STAT_UID overrides the reported owner; STUB_STAT_LOG records calls.
# The identity format (device, inode, birth time to the nanosecond) and the
# size are answered from the real file by the host's own stat, whatever its
# flavour; each stub accepts only its own flavour's syntax.
REAL_UID="$(/usr/bin/id -u)"
cat >"$STUBS/bsd/stat" <<'STUB'
#!/bin/bash
[ -z "${STUB_STAT_LOG:-}" ] || printf 'bsd %s\n' "$*" >>"$STUB_STAT_LOG"
if [ "${1:-}" = "-f" ] && [ "${2:-}" = "%d %i %.9FB" ]; then
  /usr/bin/stat --version >/dev/null 2>&1 && exec /usr/bin/stat -c '%d %i %.9W' -- "$4"
  exec /usr/bin/stat -f '%d %i %.9FB' -- "$4"
fi
if [ "${1:-}" = "-f" ] && [ "${2:-}" = "%z" ]; then
  /usr/bin/stat --version >/dev/null 2>&1 && exec /usr/bin/stat -c '%s' -- "$4"
  exec /usr/bin/stat -f '%z' -- "$4"
fi
# `%u %Mp%Lp` is the nonce's whole-mode read; BSD prints it with the
# special-bits digit first (0600).
case "${1:-} ${2:-}" in
  "-f %u %Lp") special="" ;;
  "-f %u %Mp%Lp") special=0 ;;
  *) exit 1 ;;
esac
target="$4"
mode=600; [ ! -d "$target" ] || mode=700
printf '%s %s%s\n' "${STUB_STAT_UID:-$(/usr/bin/id -u)}" "$special" "$mode"
STUB
cat >"$STUBS/gnu/stat" <<'STUB'
#!/bin/bash
[ -z "${STUB_STAT_LOG:-}" ] || printf 'gnu %s\n' "$*" >>"$STUB_STAT_LOG"
if [ "${1:-}" = "--version" ]; then printf 'stat (GNU coreutils) 9.4\n'; exit 0; fi
if [ "${1:-}" = "-c" ] && [ "${2:-}" = "%d %i %.9W" ]; then
  /usr/bin/stat --version >/dev/null 2>&1 && exec /usr/bin/stat -c '%d %i %.9W' -- "$4"
  exec /usr/bin/stat -f '%d %i %.9FB' -- "$4"
fi
if [ "${1:-}" = "-c" ] && [ "${2:-}" = "%s" ]; then
  /usr/bin/stat --version >/dev/null 2>&1 && exec /usr/bin/stat -c '%s' -- "$4"
  exec /usr/bin/stat -f '%z' -- "$4"
fi
[ "${1:-}" = "-c" ] && [ "${2:-}" = "%u %a" ] || exit 1
target="$4"
mode=600; [ ! -d "$target" ] || mode=700
printf '%s %s\n' "${STUB_STAT_UID:-$(/usr/bin/id -u)}" "$mode"
STUB
# A GNU-syntax stat whose banner does not say GNU, as the uutils rewrite's
# does not. It takes only the -c syntax and answers every field from the real
# file (the owner, the whole mode, the identity and the size), so a registry
# the host reports as group- or world-writable stays that way. STUB_STAT_UID
# overrides only the reported owner. STUB_STAT_WHOLE_SECONDS=1 answers the
# birth time as uutils coreutils 0.10.0 does, in whole seconds with no
# fraction.
cat >"$STUBS/uutils/stat" <<'STUB'
#!/bin/bash
[ -z "${STUB_STAT_LOG:-}" ] || printf 'uutils %s\n' "$*" >>"$STUB_STAT_LOG"
if [ "${1:-}" = "--version" ]; then printf 'stat (uutils coreutils) 0.0.24\n'; exit 0; fi
[ "${1:-}" = "-c" ] && [ "${3:-}" = "--" ] && [ "$#" -eq 4 ] || exit 1
host_gnu=0; /usr/bin/stat --version >/dev/null 2>&1 && host_gnu=1
case "$2" in
  "%u %a")
    if [ "$host_gnu" = 1 ]; then out="$(/usr/bin/stat -c '%u %a' -- "$4")" || exit 1
    else out="$(/usr/bin/stat -f '%u %Mp%Lp' -- "$4")" || exit 1; fi
    owner="${out%% *}"; mode="${out#* }"
    while [ "${mode#0}" != "$mode" ]; do mode="${mode#0}"; done
    printf '%s %s\n' "${STUB_STAT_UID:-$owner}" "$mode" ;;
  "%d %i %.9W")
    if [ "$host_gnu" = 1 ]; then out="$(/usr/bin/stat -c '%d %i %.9W' -- "$4")" || exit 1
    else out="$(/usr/bin/stat -f '%d %i %.9FB' -- "$4")" || exit 1; fi
    [ "${STUB_STAT_WHOLE_SECONDS:-}" != 1 ] || out="${out%.*}"
    printf '%s\n' "$out" ;;
  "%s")
    [ "$host_gnu" = 1 ] && exec /usr/bin/stat -c '%s' -- "$4"
    exec /usr/bin/stat -f '%z' -- "$4" ;;
  *) exit 1 ;;
esac
STUB
chmod 755 "$STUBS/bsd/stat" "$STUBS/gnu/stat" "$STUBS/uutils/stat"

# Planted stat: sits first on PATH and claims every file is owned by the
# caller with mode 600, whatever the real owner or mode is.
cat >"$STUBS/plant/stat" <<'STUB'
#!/bin/bash
[ "${1:-}" != "--version" ] || exit 1
printf '%s 600\n' "$(/usr/bin/id -u)"
STUB
chmod 755 "$STUBS/plant/stat"
# A registry the real stat reports as world-writable.
REG_BAD_DIR="$TMP/seat-bad/scratch"
REG_BAD="$REG_BAD_DIR/lane-roots.tsv"
mkdir -p "$REG_BAD_DIR"
cp "$REG" "$REG_BAD"
chmod 666 "$REG_BAD"; chmod 700 "$REG_BAD_DIR"
# A private registry file in a folder the real stat reports as group-writable.
REG_GW_DIR="$TMP/seat-gw/scratch"
REG_GW="$REG_GW_DIR/lane-roots.tsv"
mkdir -p "$REG_GW_DIR"
cp "$REG" "$REG_GW"
chmod 600 "$REG_GW"; chmod 770 "$REG_GW_DIR"

# The registry / authorization rules below are exercised on worktrees that live
# under the temp roots, which the guard would otherwise accept as throwaway
# trees. Run this suite against a copy with the temp roots emptied so those
# rules stay observable; the temp-root rule has its own suite.
sed 's|^TEMP_ROOTS=.*|TEMP_ROOTS=()|' "$GUARD" >"$GUARD_COPIES/guard-no-temp-roots.sh" || exit 1
grep -q '^TEMP_ROOTS=()$' "$GUARD_COPIES/guard-no-temp-roots.sh" || { printf 'FAIL: guard has no TEMP_ROOTS constant to rewrite\n'; exit 1; }
GUARD="$GUARD_COPIES/guard-no-temp-roots.sh"

FAILURES=0
make_guard_copy() { # FLAVOUR STUB_BINARY -> path of a guard copy that uses only that stat
  local dest="$GUARD_COPIES/guard-$1.sh"
  sed "s|^STAT_CANDIDATES=.*|STAT_CANDIDATES=($2)|" "$GUARD" >"$dest"
  chmod 755 "$dest"
  grep -q "^STAT_CANDIDATES=($2)\$" "$dest" || { printf 'FAIL: guard has no STAT_CANDIDATES constant to rewrite\n'; exit 1; }
  printf '%s' "$dest"
}
GUARD_BSD="$(make_guard_copy bsd "$STUBS/bsd/stat")" || exit 1
GUARD_GNU="$(make_guard_copy gnu "$STUBS/gnu/stat")" || exit 1
GUARD_UUTILS="$(make_guard_copy uutils "$STUBS/uutils/stat")" || exit 1
GUARD_NOSTAT="$(make_guard_copy nostat "$TMP/no-such-dir/stat")" || exit 1
pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; FAILURES=$((FAILURES + 1)); }

# run_case LABEL EXPECT(accepted|deny|no_opinion|nonallow) CWD COMMAND [bsd|gnu|nostat|-] [STUB_UID]
# EXPECT accepted is the engine's no_opinion/accepted verdict (the guard never
# allows); nonallow is any valid verdict.
run_case() {
  local label="$1" expect="$2" cwd="$3" command_data="$4" variant="${5:--}" stubuid="${6:-}"
  local input output decision reason path="$PATH" guard="$GUARD"
  case "$variant" in bsd) guard="$GUARD_BSD" ;; gnu) guard="$GUARD_GNU" ;; uutils) guard="$GUARD_UUTILS" ;; nostat) guard="$GUARD_NOSTAT" ;; esac
  input="$($JQ -cn --arg cwd "$cwd" --arg command "$command_data" '{cwd:$cwd,command:$command}')"
  output="$(cd "$TMP" && printf '%s' "$input" | env PATH="$path" STUB_STAT_UID="$stubuid" \
    LANE_ROOTS_REGISTRY="$REG" HOME="$TMP/githome" /bin/bash --noprofile --norc -p "$guard" --policy 2>/dev/null)"
  decision="$(printf '%s' "$output" | "$JQ" -er '.decision' 2>/dev/null || printf invalid)"
  reason="$(printf '%s' "$output" | "$JQ" -er '.reason' 2>/dev/null || printf invalid)"
  if [ "$expect" = "nonallow" ] && [ "$decision" != "allow" ] && [ "$decision" != "invalid" ]; then
    pass "$label -> $decision/$reason (not allowed)"
  elif [[ "$expect" == */* ]] && [ "$decision/$reason" = "$expect" ]; then
    pass "$label -> $decision/$reason"
  elif [ "$expect" = accepted ] && [ "$decision/$reason" = no_opinion/accepted ]; then
    pass "$label -> $decision/$reason"
  elif [ "$expect" != accepted ] && [ "$decision" = "$expect" ]; then
    pass "$label -> $decision/$reason"
  else
    fail "$label: expected $expect, got $decision/$reason"
  fi
}

printf '%s\n' '== forced removal through git global options =='
run_case "git -c k=v, --force" deny "$PRIMARY" "git -c k=v worktree remove --force $WT"
run_case "git --git-dir=X, --force" deny "$PRIMARY" "git --git-dir=$PRIMARY/.git worktree remove --force $WT"
run_case "git --git-dir X (separate word), --force" deny "$PRIMARY" "git --git-dir $PRIMARY/.git worktree remove --force $WT"
run_case "git -C P -c a=b, --force" deny "$OTHER" "git -C $PRIMARY -c a=b worktree remove --force $WT"
run_case "git -c a=b -C P, --force" deny "$OTHER" "git -c a=b -C $PRIMARY worktree remove --force $WT"
run_case "git --work-tree=X, --force" deny "$PRIMARY" "git --work-tree=$PRIMARY worktree remove --force $WT"
run_case "git --no-pager, --force" deny "$PRIMARY" "git --no-pager worktree remove --force $WT"
run_case "git -c k=v, -f after path" deny "$PRIMARY" "git -c k=v worktree remove $WT -f"
run_case "git -c k=v, registered lane, no force (same as plain)" accepted "$PRIMARY" "git -c k=v worktree remove $WT"
run_case "git --git-dir=X, registered lane, no force (same as plain)" accepted "$PRIMARY" "git --git-dir=$PRIMARY/.git worktree remove $WT"
run_case "git -c k=v, unlisted path, no force" deny "$PRIMARY" "git -c k=v worktree remove $TMP/scratch/nope"

printf '%s\n' '== forced removal through command prefixes =='
run_case "env git, --force" deny "$PRIMARY" "env git worktree remove --force $WT"
run_case "command git, --force" deny "$PRIMARY" "command git worktree remove --force $WT"
run_case "/usr/bin/git, --force" deny "$PRIMARY" "/usr/bin/git worktree remove --force $WT"
run_case "/usr/bin/env git, --force" deny "$PRIMARY" "/usr/bin/env git worktree remove --force $WT"
run_case "env command git, --force" deny "$PRIMARY" "env command git worktree remove --force $WT"
run_case "env git -C P, --force" deny "$OTHER" "env git -C $PRIMARY worktree remove --force $WT"
run_case "command git -C P, --force" deny "$OTHER" "command git -C $PRIMARY worktree remove --force $WT"
run_case "/usr/bin/git -C P, -f" deny "$OTHER" "/usr/bin/git -C $PRIMARY worktree remove -f $WT"
run_case "env with an assignment, --force" deny "$PRIMARY" "env GIT_DIR=$PRIMARY/.git git worktree remove --force $WT"
run_case "bare assignment prefix, --force" deny "$PRIMARY" "GIT_DIR=$PRIMARY/.git git worktree remove --force $WT"
run_case "env with an option, --force" deny "$PRIMARY" "env -i git worktree remove --force $WT"
run_case "prefix plus -c, --force" deny "$PRIMARY" "env git -c k=v worktree remove --force $WT"
run_case "prefix, second path" deny "$PRIMARY" "env git worktree remove $WT $WT_B"
run_case "prefix, lone flag" deny "$PRIMARY" "env git worktree remove --force"
run_case "prefix, unlisted path" deny "$PRIMARY" "env git worktree remove $TMP/scratch/nope"

printf '%s\n' '== other options and prefixes are refused even without --force =='
run_case "git --no-pager, no force" deny "$PRIMARY" "git --no-pager worktree remove $WT"
run_case "git --work-tree=X, no force" deny "$PRIMARY" "git --work-tree=$PRIMARY worktree remove $WT"
run_case "git -C P -c a=b, registered lane, no force (same as plain)" accepted "$OTHER" "git -C $PRIMARY -c a=b worktree remove $WT"
run_case "env with an assignment, no force" deny "$PRIMARY" "env GIT_DIR=$PRIMARY/.git git worktree remove $WT"
run_case "bare assignment prefix, no force" deny "$PRIMARY" "GIT_DIR=$PRIMARY/.git git worktree remove $WT"
run_case "env with an option, no force" deny "$PRIMARY" "env -i git worktree remove $WT"
run_case "env -u NAME, no force" deny "$PRIMARY" "env -u HOME git worktree remove $WT"
run_case "prefixed, repeated -C" deny "$OTHER" "env git -C $PRIMARY -C $PRIMARY worktree remove $WT"

printf '%s\n' '== spellings that do not parse as a simple command =='
run_case "env git, unquoted glob" deny "$PRIMARY" "env git worktree remove $TMP/scratch/wt-*"
run_case "command git, command substitution" deny "$PRIMARY" "command git worktree remove \$(echo $WT)"
run_case "/usr/bin/git, glob" deny "$PRIMARY" "/usr/bin/git worktree remove $TMP/scratch/wt-*"
run_case "git -c k=v, command substitution" deny "$PRIMARY" "git -c k=v worktree remove \$(echo $WT)"
run_case "git --git-dir=X, glob, --force" deny "$PRIMARY" "git --git-dir=$PRIMARY/.git worktree remove --force $TMP/scratch/wt-*"
run_case "assignment prefix, glob" deny "$PRIMARY" "GIT_DIR=$PRIMARY/.git git worktree remove $TMP/scratch/wt-*"
run_case "env -i, glob" deny "$PRIMARY" "env -i git worktree remove $TMP/scratch/wt-*"

printf '%s\n' '== prefix words respelled like git =='
for p in '/bin/env' '/usr/local/bin/env' './env' '\env' '"env"' "'/usr/bin/env'" '/bin/command' '\command' './command' '"command"'; do
  run_case "$p git, --force" deny "$PRIMARY" "$p git worktree remove --force $WT"
  run_case "$p git, -f after path" deny "$PRIMARY" "$p git worktree remove $WT -f"
  run_case "$p git -C P, --force" deny "$OTHER" "$p git -C $PRIMARY worktree remove --force $WT"
  run_case "$p git, registered lane, no force" accepted "$PRIMARY" "$p git worktree remove $WT"
  run_case "$p git, unlisted path" deny "$PRIMARY" "$p git worktree remove $TMP/scratch/nope"
done
for p in envx /bin/env-foo myenv /bin/commandx; do
  run_case "$p git is not a prefix, --force" no_opinion "$PRIMARY" "$p git worktree remove --force $WT"
done

printf '%s\n' '== forced removal in spellings that do not parse =='
run_case "git -c quoted-space value, --force" deny "$PRIMARY" "git -c k=v\\ w worktree remove --force $WT"
run_case "env git -c escaped-space value, --force" deny "$PRIMARY" "env git -c k=v\\ w worktree remove --force $WT"
run_case "git -C escaped-space path, --force" deny "$PRIMARY" "git -C $PRIMARY\\ x worktree remove --force $WT"
run_case "git wor''ktree, --force" deny "$PRIMARY" "git wor''ktree remove --force $WT"
run_case "git worktree re\\move, --force" deny "$PRIMARY" "git worktree re\\move --force $WT"
run_case "git \$'worktree', --force" deny "$PRIMARY" "git \$'worktree' remove --force $WT"
run_case "git wor''ktree, -f" deny "$PRIMARY" "git wor''ktree remove -f $WT"
run_case "git wor''ktree, combined -rf" deny "$PRIMARY" "git wor''ktree remove -rf $WT"
run_case "/bin/env git wor''ktree, --force" deny "$PRIMARY" "/bin/env git wor''ktree remove --force $WT"
run_case "\\env git -c escaped-space, --force" deny "$PRIMARY" "\\env git -c k=v\\ w worktree remove --force $WT"
run_case "unparseable, not forced, is denied like a forced one" deny "$PRIMARY" "git wor''ktree remove $WT"
run_case "echo of an unparseable forced removal" no_opinion "$PRIMARY" "echo git wor''ktree remove --force $WT"
run_case "echo with escapes" no_opinion "$PRIMARY" "echo git worktree re\\move --force $WT"
run_case "legit wor''ktree is not git" no_opinion "$PRIMARY" "legit wor''ktree remove --force $WT"
run_case "gitx wor''ktree is not git" no_opinion "$PRIMARY" "gitx wor''ktree remove --force $WT"
run_case "/bin/git-foo wor''ktree is not git" no_opinion "$PRIMARY" "/bin/git-foo wor''ktree remove --force $WT"

printf '%s\n' '== option values with quoted spaces or substitutions keep their words whole =='
run_case "git -c 'user.name=Alice Bob', wor''ktree" deny "$PRIMARY" "git -c 'user.name=Alice Bob' wor''ktree remove --force $WT"
run_case "env -u NAME /usr/bin/git, wor''ktree" deny "$PRIMARY" "env -u NAME /usr/bin/git wor''ktree remove --force $WT"
run_case "git -c double-quoted space, wor''ktree" deny "$PRIMARY" "git -c \"k=v w\" wor''ktree remove --force $WT"
run_case "git -c single-quoted space, wor''ktree" deny "$PRIMARY" "git -c 'k=v w' wor''ktree remove --force $WT"
run_case "git -c \$(...) value, wor''ktree" deny "$PRIMARY" "git -c k=\$(echo v) wor''ktree remove --force $WT"
run_case "git -c backtick value, wor''ktree" deny "$PRIMARY" "git -c k=\`echo v\` wor''ktree remove --force $WT"
run_case "git --exec-path=\"a b\", wor''ktree" deny "$PRIMARY" "git --exec-path=\"a b\" wor''ktree remove --force $WT"
run_case "git -c quoted space, re''move" deny "$PRIMARY" "git -c \"k=v w\" worktree re''move --force $WT"
run_case "git -c k=\"\$(echo v w)\", forced" deny "$PRIMARY" "git -c k=\"\$(echo v w)\" worktree remove --force $WT"
run_case "git -c \$'..' value with a space, wor''ktree" deny "$PRIMARY" "git -c \$'k=v w' wor''ktree remove --force $WT"
run_case "git -c nested parens in value, wor''ktree" deny "$PRIMARY" "git -c k=\$(echo \$(echo \"a b\")) wor''ktree remove --force $WT"
run_case "env git -c quoted space, wor''ktree, -f" deny "$PRIMARY" "env git -c \"k=v w\" wor''ktree remove -f $WT"
run_case "git -c quoted space, mention only (commit)" deny/removal_beside_substitution "$PRIMARY" "git -c \"k=v w\" commit -m \"worktree remove --force\" \$(date)"
run_case "commit -m value holds the words with a space token" no_opinion "$PRIMARY" 'git commit -m "k=v w worktree remove --force"'
run_case "commit -m value, single quotes, space token" no_opinion "$PRIMARY" "git commit -m 'k=v w worktree remove --force'"
run_case "log --grep with a substitution" deny/removal_beside_substitution "$PRIMARY" 'git log --grep "$(echo worktree remove --force)"'
run_case "log --grep with a backtick substitution" deny/removal_beside_backtick "$PRIMARY" 'git log --grep "`echo worktree remove --force`"'
run_case "commit -m with a substitution holding the words" deny/removal_beside_substitution "$PRIMARY" 'git commit -m "x" -m "$(echo a; worktree remove --force)"'
run_case "echo of a quoted-space forced removal" no_opinion "$PRIMARY" "echo git -c \"k=v w\" wor''ktree remove --force $WT"
run_case "unbalanced trailing quote, wor''ktree, falls back to stripped words" deny "$PRIMARY" "git wor''ktree remove --force $WT \"x"
run_case "unbalanced quote falls back to the stripped words" deny "$PRIMARY" "git -c \"unbalanced worktree remove --force $WT"

printf '%s\n' '== env -S carries a command string =='
# env -S is a carrier: every row here denies removal_beside_env_split, a
# registered tree included; env's own command word ends its option list.
run_case "env -S double quotes, --force" deny "$PRIMARY" "env -S \"git worktree remove --force $WT\""
run_case "env -S single quotes, --force" deny "$PRIMARY" "env -S 'git worktree remove --force $WT'"
run_case "env -C P -S, --force" deny "$PRIMARY" "env -C $PRIMARY -S \"git worktree remove --force $WT\""
run_case "env --split-string=, --force" deny "$PRIMARY" "env --split-string=\"git worktree remove --force $WT\""
run_case "env --split-string VALUE, --force" deny "$PRIMARY" "env --split-string 'git worktree remove --force $WT'"
run_case "env --split-string alone, --force" deny "$PRIMARY" "env --split-string \"git worktree remove --force $WT\""
run_case "env -S attached value, --force" deny "$PRIMARY" "env -S\"git worktree remove --force $WT\""
run_case "env -iS bundle, --force" deny "$PRIMARY" "env -iS \"git worktree remove --force $WT\""
run_case "assignment, env -u X -S, -f" deny "$OTHER" "FOO=1 env -u X -S \"git -C $PRIMARY worktree remove -f $WT\""
run_case "env -S, force given as a trailing word" deny "$PRIMARY" "env -S \"git worktree remove $WT\" --force"
run_case "env -S inside env -S" deny "$PRIMARY" "env -S \"env -S 'git worktree remove --force $WT'\""
run_case "env -S with an unbalanced inner quote, forced" deny "$PRIMARY" "env -S \"git worktree remove --force '$WT\""
run_case "env -S, unlisted path, no force" deny "$PRIMARY" "env -S \"git worktree remove $TMP/scratch/nope\""
run_case "env -S, registered lane, no force, is a carrier" deny/removal_beside_env_split "$PRIMARY" "env -S \"git worktree remove $WT\""
run_case "env -S of an unrelated command" no_opinion "$PRIMARY" "env -S \"echo hi\""
run_case "env echo -S of the words is a mention" no_opinion "$PRIMARY" "env echo -S \"git worktree remove --force $WT\""
run_case "echo env -S of the words: an env word anywhere carries its option list" deny/removal_beside_env_split "$PRIMARY" "echo env -S \"git worktree remove --force $WT\""

printf '%s\n' '== commands that only mention a forced removal stay out of scope =='
run_case "commit message, double quotes" no_opinion "$PRIMARY" 'git commit -m "docs: guard worktree remove --force"'
run_case "commit message, single quotes" no_opinion "$PRIMARY" "git commit -m 'single quotes worktree remove --force'"
run_case "commit message, -f spelling" no_opinion "$PRIMARY" 'git commit -m "fix: worktree remove -f now denied"'
run_case "commit, two messages, -rf" no_opinion "$PRIMARY" 'git commit -m "a" -m "worktree remove -rf"'
run_case "commit message with an apostrophe" no_opinion "$PRIMARY" "git commit -m \"it's worktree remove --force\""
run_case "commit message holding a whole command" no_opinion "$PRIMARY" 'git commit -m "git worktree remove --force x"'
run_case "git -C P commit message" no_opinion "$PRIMARY" "git -C $PRIMARY commit -m \"docs: worktree remove --force\""
run_case "env git commit message" no_opinion "$PRIMARY" 'env git commit -m "worktree remove --force"'
run_case "tag message" no_opinion "$PRIMARY" 'git tag -a v1 -m "worktree remove --force"'
run_case "notes message" no_opinion "$PRIMARY" 'git notes add -m "worktree remove --force"'
run_case "stash message" no_opinion "$PRIMARY" 'git stash push -m "worktree remove -f"'
run_case "log --grep" no_opinion "$PRIMARY" 'git log --grep "worktree remove --force"'
run_case "git grep, double quotes" no_opinion "$PRIMARY" 'git grep "worktree remove --force"'
run_case "git grep, single quotes, pathspec" no_opinion "$PRIMARY" "git grep -n 'worktree remove -f' -- docs"
run_case "git show with the words as operands" no_opinion "$PRIMARY" 'git show worktree remove --force'
run_case "commit with an unquoted message" no_opinion "$PRIMARY" 'git commit -m worktree remove --force'
# A substitution beside the words denies removal_beside_substitution; a glob
# does not parse as a simple command, so it reaches the stripped-words check,
# where `worktree` is not the first word after git.
run_case "unparseable commit message mention" deny/removal_beside_substitution "$PRIMARY" 'git commit -m "docs: worktree remove --force" $(date)'
run_case "unparseable commit, -C P, mention" deny/removal_beside_substitution "$PRIMARY" "git -C $PRIMARY commit -m \"worktree remove -f\" \$(date)"
run_case "unparseable git grep mention" no_opinion "$PRIMARY" "git grep -n 'worktree remove -f' -- docs/*"
run_case "unparseable log --grep mention" no_opinion "$PRIMARY" 'git log --grep "worktree remove --force" *.md'
run_case "unparseable unquoted commit message mention" deny/removal_beside_substitution "$PRIMARY" 'git commit -m worktree remove --force $(date)'
run_case "unparseable git show operands mention" no_opinion "$PRIMARY" 'git show worktree remove -f *.md'
run_case "unparseable log --grep with loose words" no_opinion "$PRIMARY" 'git log --grep x worktree remove --force *.md'
run_case "env echo of a plain removal" no_opinion "$PRIMARY" "env echo git worktree remove $WT"
run_case "env echo of a forced removal" no_opinion "$PRIMARY" "env echo git worktree remove --force $WT"
run_case "command echo of a removal" no_opinion "$PRIMARY" "command echo git worktree remove $WT"
run_case "bare echo of a plain removal" no_opinion "$PRIMARY" "echo git worktree remove $WT"

printf '%s\n' '== git spelled with a backslash, a path or quotes =='
GIT_SPELLINGS=('\git' '/bin/git' '/usr/bin/git' '/opt/homebrew/bin/git' '/opt/local/bin/git' '~/bin/git' '$HOME/bin/git' '${HOME}/bin/git' '"/usr/bin/git"' "'/usr/bin/git'" '"$HOME/bin/git"' '\/bin/git')
# A git word that holds $ is a command word built from a variable, a carrier:
# beside the removal words it denies removal_beside_variable_command.
for g in "${GIT_SPELLINGS[@]}"; do
  case "$g" in *'$'*) plain_verdict=deny/removal_beside_variable_command ;; *) plain_verdict=accepted ;; esac
  run_case "$g, --force" deny "$PRIMARY" "$g worktree remove --force $WT"
  run_case "$g, -f after path" deny "$PRIMARY" "$g worktree remove $WT -f"
  run_case "$g -C P, --force" deny "$OTHER" "$g -C $PRIMARY worktree remove --force $WT"
  run_case "$g -c k=v, --force" deny "$PRIMARY" "$g -c k=v worktree remove --force $WT"
  run_case "env $g, --force" deny "$PRIMARY" "env $g worktree remove --force $WT"
  run_case "command $g, --force" deny "$PRIMARY" "command $g worktree remove --force $WT"
  run_case "$g, registered lane, no force" "$plain_verdict" "$PRIMARY" "$g worktree remove $WT"
  run_case "$g, unlisted path" deny "$PRIMARY" "$g worktree remove $TMP/scratch/nope"
done

printf '%s\n' '== words that only look like git are not git =='
for g in gitx /bin/git-foo legit /bin/gitx 'git-foo' '/bin/git/x'; do
  run_case "$g is not git, --force" no_opinion "$PRIMARY" "$g worktree remove --force $WT"
  run_case "env $g is not git, --force" no_opinion "$PRIMARY" "env $g worktree remove --force $WT"
done
# $GIT is not git either, but a command word built from a variable is a
# carrier: beside the removal words it denies.
run_case '$GIT is not git, --force' deny/removal_beside_variable_command "$PRIMARY" "\$GIT worktree remove --force $WT"
run_case 'env $GIT is not git, --force' deny/removal_beside_variable_command "$PRIMARY" "env \$GIT worktree remove --force $WT"
run_case "rm operand spelled like git is still judged as rm" deny "$PRIMARY" "env rm -rf /bin/git"

printf '%s\n' '== wrapped spellings get the verdict of the canonical command =='
# verdict CWD COMMAND -> "decision/reason" from the guard, PATH untouched.
verdict() {
  local out
  out="$(cd "$TMP" && printf '%s' "$($JQ -cn --arg cwd "$1" --arg command "$2" '{cwd:$cwd,command:$command}')" \
    | env LANE_ROOTS_REGISTRY="$REG" HOME="$TMP/githome" /bin/bash --noprofile --norc -p "$GUARD" --policy 2>/dev/null)"
  printf '%s/%s' "$(printf '%s' "$out" | "$JQ" -er '.decision' 2>/dev/null || printf invalid)" \
    "$(printf '%s' "$out" | "$JQ" -er '.reason' 2>/dev/null || printf invalid)"
}
# Each row: CWD | wrapped head | canonical head (the words before `worktree`).
EQUIV_ROWS=(
  "$PRIMARY|git -c k=v|git"
  "$PRIMARY|git --git-dir=$PRIMARY/.git|git"
  "$PRIMARY|git --git-dir $PRIMARY/.git|git"
  "$OTHER|git -C $PRIMARY -c a=b|git -C $PRIMARY"
  "$OTHER|git -c a=b -C $PRIMARY|git -C $PRIMARY"
  "$PRIMARY|env git|git"
  "$PRIMARY|command git|git"
  "$PRIMARY|env command git|git"
  "$PRIMARY|/usr/bin/env git|git"
  "$OTHER|env git -C $PRIMARY|git -C $PRIMARY"
  "$PRIMARY|env git -c k=v|git"
)
for g in "${GIT_SPELLINGS[@]}"; do
  EQUIV_ROWS+=("$PRIMARY|$g|git" "$OTHER|$g -C $PRIMARY|git -C $PRIMARY" "$PRIMARY|env $g|git")
done
for row in "${EQUIV_ROWS[@]}"; do
  IFS='|' read -r r_cwd r_wrapped r_canon <<<"$row"
  for tail in "worktree remove --force $WT" "worktree remove -f $WT" "worktree remove $WT" "worktree remove $WT_B" \
              "worktree remove $TMP/scratch/nope" "worktree remove $WT $WT_B" "worktree remove --force" "worktree remove $PRIMARY"; do
    want="$(verdict "$r_cwd" "$r_canon $tail")"
    # A git word built from a variable is a carrier, whatever the canonical
    # command's verdict.
    case "$r_wrapped" in *'$'*) want=deny/removal_beside_variable_command ;; esac
    got="$(verdict "$r_cwd" "$r_wrapped $tail")"
    if [ "$got" = "$want" ]; then
      pass "equivalence [$r_wrapped] $tail -> $got"
    else
      fail "equivalence [$r_wrapped] $tail: canonical [$r_canon] gives $want, wrapped gives $got"
    fi
  done
done

printf '%s\n' '== not a git invocation =='
run_case "env git worktree list stays out of scope" no_opinion "$PRIMARY" "env git worktree list"
run_case "git -c k=v worktree add stays out of scope" no_opinion "$PRIMARY" "git -c k=v worktree add $TMP/scratch/new"
run_case "echo of the words is not a removal" no_opinion "$PRIMARY" "echo git worktree remove --force $WT"

printf '%s\n' '== compound commands =='
run_case "valid ; env git forced" deny "$PRIMARY" "git worktree remove $WT ; env git worktree remove --force $WT_B"
run_case "command git forced && valid" deny "$PRIMARY" "command git worktree remove --force $WT_B && git worktree remove $WT"
run_case "valid ; git -c forced" deny "$PRIMARY" "git worktree remove $WT ; git -c k=v worktree remove --force $WT_B"

printf '%s\n' '== plain prefixes still reach the same validator =='
run_case "env git, registered + listed" accepted "$PRIMARY" "env git worktree remove $WT_C"
run_case "command git -C P, registered + listed" accepted "$OTHER" "command git -C $PRIMARY worktree remove $WT_B"
run_case "/usr/bin/git, registered + listed" accepted "$PRIMARY" "/usr/bin/git worktree remove $WT"
run_case "/usr/bin/env git, registered + listed" accepted "$PRIMARY" "/usr/bin/env git worktree remove $WT_B"
run_case "/usr/bin/git -C P, registered + listed" accepted "$OTHER" "/usr/bin/git -C $PRIMARY worktree remove $WT_C"

printf '%s\n' '== registry owner (uid) check, BSD and GNU stat =='
for flavour in bsd gnu; do
  run_case "$flavour stat, owner is this user" accepted "$PRIMARY" "git worktree remove $WT" "$flavour"
  run_case "$flavour stat, owner is another uid" deny "$PRIMARY" "git worktree remove $WT" "$flavour" "$((REAL_UID + 1))"
  run_case "$flavour stat, owner is root" deny "$PRIMARY" "git worktree remove $WT" "$flavour" "0"
done

run_case "no usable stat binary fails closed" deny "$PRIMARY" "git worktree remove $WT" nostat

printf '%s\n' '== a GNU-syntax stat whose banner does not say GNU (uutils) =='
# The flavour is the syntax the binary takes: a banner that does not name GNU
# still gets the -c form, and the owner and mode rules still deny.
run_uutils_registry() { # LABEL REGISTRY WANT_REASON [STUB_UID]
  local out reason
  out="$(cd "$TMP" && printf '%s' "$($JQ -cn --arg cwd "$PRIMARY" --arg command "git worktree remove $WT" '{cwd:$cwd,command:$command}')" \
    | env STUB_STAT_UID="${4:-}" LANE_ROOTS_REGISTRY="$2" HOME="$TMP/githome" /bin/bash --noprofile --norc -p "$GUARD_UUTILS" --policy 2>/dev/null)"
  reason="$(printf '%s' "$out" | "$JQ" -er 'select(.decision == "deny") | .reason' 2>/dev/null || printf invalid)"
  if [ "$reason" = "$3" ]; then pass "$1 -> deny/$reason"; else fail "$1: expected deny/$3, got ${out:-no output}"; fi
}
run_case "uutils stat, trusted registry, owner is this user" accepted "$PRIMARY" "git worktree remove $WT" uutils
run_uutils_registry "uutils stat, world-writable registry file" "$REG_BAD" lane_registry_untrusted
run_uutils_registry "uutils stat, group-writable registry folder" "$REG_GW" lane_registry_untrusted
run_uutils_registry "uutils stat, registry owned by another uid" "$REG" lane_registry_untrusted "$((REAL_UID + 1))"

# A uutils stat that prints the birth time in whole seconds (0.10.0 does)
# fails closed: a registered, unchanged tree whose recorded identity holds the
# same whole-second birth time still denies, because the guard needs the
# birth time to the nanosecond and has no whole-second fallback. The tree has
# its own registry, so the identity rows above keep theirs.
whole_second_token() { # WORKTREE -> its token, the birth time cut to whole seconds
  local t rest birth
  t="$(token "$1")" || return 1
  rest="${t%:*}"; birth="${rest##*:}"
  printf '%s:%s:%s' "${rest%:*}" "${birth%%.*}" "${t##*:}"
}
WT_D="$TMP/scratch/wt-d"
REG_WS_DIR="$TMP/seat-ws/scratch"
REG_WS="$REG_WS_DIR/lane-roots.tsv"
git_q -C "$PRIMARY" worktree add -q --detach "$WT_D" HEAD || exit 1
mkdir -p "$REG_WS_DIR"
printf 'path\trepo\tbranch\tcreated_at\n%s\t%s\t%s\t%s\n' "$WT_D" "$PRIMARY" "(detached)" "2026-09-30T00:00:00Z" >"$REG_WS"
plant_nonce "$WT_D" >/dev/null || exit 1
WS_TOKEN="$(whole_second_token "$WT_D")" || exit 1
case "$WS_TOKEN" in *:*:*:*.*:*) printf 'FAIL: the whole-second token kept a fraction: %s\n' "$WS_TOKEN"; exit 1 ;; esac
printf 'lane-roots-ids/v1\tpath\tgeneration\tidentity\tseat\tregistered_at\n%s\t1\t%s\tseat-test\t2026-10-21T00:00:00Z\n' \
  "$WT_D" "$WS_TOKEN" >"$REG_WS_DIR/lane-roots.ids.tsv"
printf 'lane-prune-pending/v1\tpath\trepo\tgeneration\tidentity\texpires_at\n%s\t%s\t1\t%s\t%s\n' \
  "$WT_D" "$PRIMARY" "$WS_TOKEN" "$(($(/bin/date +%s) + 3600))" >"$REG_WS_DIR/lane-prune-pending.tsv"
chmod 600 "$REG_WS" "$REG_WS_DIR/lane-roots.ids.tsv" "$REG_WS_DIR/lane-prune-pending.tsv"; chmod 700 "$REG_WS_DIR"
ws_out="$(cd "$TMP" && printf '%s' "$($JQ -cn --arg cwd "$PRIMARY" --arg command "git worktree remove $WT_D" '{cwd:$cwd,command:$command}')" \
  | env STUB_STAT_WHOLE_SECONDS=1 LANE_ROOTS_REGISTRY="$REG_WS" HOME="$TMP/githome" /bin/bash --noprofile --norc -p "$GUARD_UUTILS" --policy 2>/dev/null)"
ws_reason="$(printf '%s' "$ws_out" | "$JQ" -er 'select(.decision == "deny") | .reason' 2>/dev/null || printf invalid)"
if [ "$ws_reason" = unsafe_worktree_remove ]; then
  pass "uutils stat, whole-second birth time, registered unchanged tree -> deny/$ws_reason (fails closed)"
else
  fail "uutils stat, whole-second birth time, registered unchanged tree: expected deny/unsafe_worktree_remove, got ${ws_out:-no output}"
fi

printf '%s\n' '== stat is never resolved through PATH =='
run_plant() { # LABEL EXPECT REGISTRY [plant] -> runs with the planted stat first on PATH when asked
  local label="$1" expect="$2" reg="$3" out decision reason path="$PATH"
  [ "${4:-}" != "plant" ] || path="$STUBS/plant:$PATH"
  out="$(cd "$TMP" && printf '%s' "$($JQ -cn --arg cwd "$PRIMARY" --arg command "git worktree remove $WT" '{cwd:$cwd,command:$command}')" \
    | env PATH="$path" LANE_ROOTS_REGISTRY="$reg" HOME="$TMP/githome" /bin/bash --noprofile --norc -p "$GUARD" --policy 2>/dev/null)"
  decision="$(printf '%s' "$out" | "$JQ" -er '.decision' 2>/dev/null || printf invalid)"
  reason="$(printf '%s' "$out" | "$JQ" -er '.reason' 2>/dev/null || printf invalid)"
  [ "$expect" != accepted ] || { expect=no_opinion; [ "$reason" = accepted ] || decision="$decision (reason $reason)"; }
  if [ "$decision" = "$expect" ]; then pass "$label -> $decision/$reason"; else fail "$label: expected $expect, got $decision/$reason"; fi
}
run_plant "world-writable registry, no planted stat on PATH" deny "$REG_BAD"
run_plant "world-writable registry, planted stat first on PATH" deny "$REG_BAD" plant
run_plant "trusted registry, planted stat first on PATH" accepted "$REG" plant

printf '%s\n' '== stat flavour is detected, not guessed =='
stat_calls() { # FLAVOUR -> calls recorded by the stub for one allowed removal
  local log="$TMP/stat-$1.log"
  : >"$log"
  printf '%s' "$($JQ -cn --arg cwd "$PRIMARY" --arg command "git worktree remove $WT" '{cwd:$cwd,command:$command}')" \
    | env STUB_STAT_LOG="$log" LANE_ROOTS_REGISTRY="$REG" HOME="$TMP/githome" \
        /bin/bash --noprofile --norc -p "$GUARD_COPIES/guard-$1.sh" --policy >/dev/null 2>&1
  cat "$log"
}
bsd_calls="$(stat_calls bsd)"
gnu_calls="$(stat_calls gnu)"
case "$bsd_calls" in *"bsd -f %u %Lp"*) pass "BSD stat receives the -f form" ;; *) fail "BSD stat never received -f (saw: $bsd_calls)" ;; esac
case "$bsd_calls" in *"-c %u %a"*) fail "BSD stat received the GNU -c form" ;; *) pass "BSD stat never receives -c" ;; esac
case "$gnu_calls" in *"gnu -c %u %a"*) pass "GNU stat receives the -c form" ;; *) fail "GNU stat never received -c (saw: $gnu_calls)" ;; esac
case "$gnu_calls" in *"gnu -f"*) fail "GNU stat received the BSD -f form (filesystem mode on GNU)" ;; *) pass "GNU stat never receives -f" ;; esac
case "$bsd_calls" in *"bsd -f %d %i %.9FB"*) pass "BSD stat receives the -f nanosecond identity form" ;; *) fail "BSD stat never received the -f nanosecond identity form (saw: $bsd_calls)" ;; esac
case "$gnu_calls" in *"gnu -c %d %i %.9W"*) pass "GNU stat receives the -c nanosecond identity form" ;; *) fail "GNU stat never received the -c nanosecond identity form (saw: $gnu_calls)" ;; esac
uutils_calls="$(stat_calls uutils)"
case "$uutils_calls" in *"uutils -c %u %a"*) pass "uutils stat receives the -c form" ;; *) fail "uutils stat never received -c (saw: $uutils_calls)" ;; esac
case "$uutils_calls" in *"uutils -f"*) fail "uutils stat received the BSD -f form (filesystem mode there)" ;; *) pass "uutils stat never receives -f" ;; esac

if [ "$FAILURES" -eq 0 ]; then
  printf '%s\n' 'ALL GIT-FORM TESTS PASSED'
  printf 'Fixtures retained at %s (test performs no deletion)\n' "$TMP"
  exit 0
fi
printf '%s TEST(S) FAILED\n' "$FAILURES"
exit 1
