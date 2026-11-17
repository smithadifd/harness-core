#!/bin/bash
# Regression for the destructive-command guard's handling of `git worktree
# remove`: throwaway worktrees under the temp roots, execution prefixes
# (exec, time, builtin, nice, sudo), `env -u NAME /bin/git`, removals wrapped in
# subshells, brace groups and substitutions, unparseable removals without a
# force flag, and the command splitter's quoting, heredoc and operator
# branches. Everything is built under a fresh temp dir; command strings are
# inert JSON data and are never executed.
set -u
LC_ALL=C

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUARD="${WRAPPERS_TEST_GUARD:-$HERE/../hooks/destructive-command-guard.sh}"
JQ="$(command -v jq)" || { printf 'FAIL: jq is not on PATH\n'; exit 1; }

TMP_BASE="$(mktemp -d)" || exit 1
TMP="$(cd "$TMP_BASE" && pwd -P)"
PRIMARY="$TMP/primary"
OTHER="$TMP/other"
WT="$TMP/scratch/wt-a"
REG_DIR="$TMP/seat/scratch"
REG="$REG_DIR/lane-roots.tsv"
ONLY="$TMP/only"
EVIL="$TMP/only-evil"
GUARD_COPIES="$TMP/guards"

git_q() { HOME="$TMP/githome" git -c user.name=t -c user.email=t@example.invalid "$@"; }
mkdir -p "$TMP/githome" "$TMP/scratch" "$REG_DIR" "$ONLY" "$EVIL" "$GUARD_COPIES" "$TMP/outside"
for repo in "$PRIMARY" "$OTHER"; do
  git_q init -q --initial-branch=main "$repo" || exit 1
  printf 'x\n' >"$repo/f.txt"
  git_q -C "$repo" add f.txt && git_q -C "$repo" commit -qm seed || exit 1
done
git_q -C "$PRIMARY" worktree add -q --detach "$WT" HEAD || exit 1
printf 'path\trepo\tbranch\tcreated_at\n%s\t%s\t%s\t%s\n' "$WT" "$PRIMARY" "(detached)" "2026-09-30T00:00:00Z" >"$REG"
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
write_identity_files "$REG_DIR" "$PRIMARY" "$WT" || exit 1

FAILURES=0
pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; FAILURES=$((FAILURES + 1)); }

# A copy of the guard whose temp roots are exactly the given list.
guard_with_roots() { # NAME ROOT...
  local dest="$GUARD_COPIES/guard-$1.sh" list="" r
  shift
  for r in "$@"; do list="$list $r"; done
  sed "s|^TEMP_ROOTS=.*|TEMP_ROOTS=($list)|" "$GUARD" >"$dest"
  grep -q "^TEMP_ROOTS=($list)\$" "$dest" || { printf 'FAIL: guard has no TEMP_ROOTS constant to rewrite\n'; exit 1; }
  printf '%s' "$dest"
}
GUARD_NOROOTS="$(guard_with_roots noroots)" || exit 1
GUARD_ONLY="$(guard_with_roots only "$ONLY")" || exit 1
GUARD_DEFAULT="$GUARD"

# run_case LABEL EXPECT CWD COMMAND [GUARD_PATH] [AUTHORIZED_WORKTREE]
run_case() {
  local label="$1" expect="$2" cwd="$3" command_data="$4" guard="${5:-$GUARD_DEFAULT}"
  local input output decision reason
  local policy_args=(--policy)
  [ -z "${6:-}" ] || policy_args+=(--authorized-worktree "$6")
  input="$($JQ -cn --arg cwd "$cwd" --arg command "$command_data" '{cwd:$cwd,command:$command}')"
  output="$(cd "$TMP" && printf '%s' "$input" | env LANE_ROOTS_REGISTRY="$REG" HOME="$TMP/githome" /bin/bash --noprofile --norc -p "$guard" "${policy_args[@]}" 2>/dev/null)"
  decision="$(printf '%s' "$output" | "$JQ" -er '.decision' 2>/dev/null || printf invalid)"
  reason="$(printf '%s' "$output" | "$JQ" -er '.reason' 2>/dev/null || printf invalid)"
  # EXPECT accepted is the engine's no_opinion/accepted verdict (the guard
  # never allows); nonallow is any valid verdict.
  if [ "$expect" = accepted ]; then
    [ "$reason" = accepted ] || decision="$decision (reason $reason)"
    expect=no_opinion
  fi
  if [[ "$expect" == */* ]]; then
    [ "$decision/$reason" != "$expect" ] || decision="$expect"
  fi
  if [ "$decision" = "$expect" ] || { [ "$expect" = nonallow ] && [ "$decision" != allow ] && [ "$decision" != invalid ]; }; then
    pass "$label -> $decision/$reason"
  else
    fail "$label: expected $expect, got $decision/$reason"
  fi
}
new_wt() { git_q -C "$PRIMARY" worktree add -q --detach "$1" HEAD || { printf 'FAIL: cannot create %s\n' "$1"; exit 1; }; }

printf '%s\n' '== throwaway worktrees under a temp root =='
new_wt "$ONLY/rev-1"; new_wt "$ONLY/rev-2"; new_wt "$ONLY/rev-3"; new_wt "$ONLY/rev-4"
new_wt "$ONLY/rev-5"; new_wt "$ONLY/rev-6"; new_wt "$EVIL/rev"; new_wt "$TMP/outside/rev"; new_wt "$TMP/rootwt"
mkdir -p "$ONLY/plain-dir"
run_case "unregistered worktree under a temp root, no force" accepted "$PRIMARY" "git worktree remove $ONLY/rev-1" "$GUARD_ONLY"
run_case "two eligible temp worktrees" accepted "$PRIMARY" "git worktree remove $ONLY/rev-1 && git worktree remove $ONLY/rev-2" "$GUARD_ONLY"
run_case "temp removal then sh forced removal" deny "$PRIMARY" "git worktree remove $ONLY/rev-1; sh -c 'git worktree remove --force $ONLY/rev-2'" "$GUARD_ONLY"
run_case "temp removal then bash forced removal" deny "$PRIMARY" "git worktree remove $ONLY/rev-1; bash -c 'git worktree remove --force $ONLY/rev-2'" "$GUARD_ONLY"
run_case "temp removal then double-quoted bash forced removal" deny "$PRIMARY" "git worktree remove $ONLY/rev-1; bash -c \"git worktree remove --force $ONLY/rev-2\"" "$GUARD_ONLY"
run_case "temp removal then shell payload with a prior command" deny "$PRIMARY" "git worktree remove $ONLY/rev-1; sh -c 'echo ok; git worktree remove --force $ONLY/rev-2'" "$GUARD_ONLY"
run_case "temp removal then eval forced removal" deny "$PRIMARY" "git worktree remove $ONLY/rev-1; eval 'git worktree remove --force $ONLY/rev-2'" "$GUARD_ONLY"
run_case "temp removal then xargs shell forced removal" deny "$PRIMARY" "git worktree remove $ONLY/rev-1; xargs -I{} sh -c 'git worktree remove --force $ONLY/rev-2'" "$GUARD_ONLY"
run_case "temp removal && sh forced removal" deny "$PRIMARY" "git worktree remove $ONLY/rev-1 && sh -c 'git worktree remove --force $ONLY/rev-2'" "$GUARD_ONLY"
run_case "temp removal newline sh forced removal" deny "$PRIMARY" "git worktree remove $ONLY/rev-1"$'\n'"sh -c 'git worktree remove --force $ONLY/rev-2'" "$GUARD_ONLY"
run_case "temp removal || sh forced removal" deny "$PRIMARY" "git worktree remove $ONLY/rev-1 || sh -c 'git worktree remove --force $ONLY/rev-2'" "$GUARD_ONLY"
run_case "temp removal pipe sh forced removal" deny "$PRIMARY" "git worktree remove $ONLY/rev-1 | sh -c 'git worktree remove --force $ONLY/rev-2'" "$GUARD_ONLY"
run_case "temp removal background sh forced removal" deny "$PRIMARY" "git worktree remove $ONLY/rev-1 & sh -c 'git worktree remove --force $ONLY/rev-2'" "$GUARD_ONLY"
run_case "temp removal then bash recursive rm" deny "$PRIMARY" "git worktree remove $ONLY/rev-1; bash -c 'rm -rf /'" "$GUARD_ONLY"
run_case "temp removal && bash recursive rm" deny "$PRIMARY" "git worktree remove $ONLY/rev-1 && bash -c 'rm -rf /'" "$GUARD_ONLY"
run_case "temp removal then echo" deny "$PRIMARY" "git worktree remove $ONLY/rev-1; echo hi" "$GUARD_ONLY"
run_case "temp removal && echo" deny "$PRIMARY" "git worktree remove $ONLY/rev-1 && echo hi" "$GUARD_ONLY"
run_case "temp removal then true" deny "$PRIMARY" "git worktree remove $ONLY/rev-1; true" "$GUARD_ONLY"
run_case "temp removal && true" deny "$PRIMARY" "git worktree remove $ONLY/rev-1 && true" "$GUARD_ONLY"
run_case "temp removal newline echo" deny "$PRIMARY" "git worktree remove $ONLY/rev-1"$'\n'"echo hi" "$GUARD_ONLY"
run_case "accepted rm then temp removal" deny "$PRIMARY" "rm -rf ./safe; git worktree remove $ONLY/rev-1" "$GUARD_ONLY"
run_case "accepted rm && temp removal" deny "$PRIMARY" "rm -rf ./safe && git worktree remove $ONLY/rev-1" "$GUARD_ONLY"
run_case "echo then temp removal" deny "$PRIMARY" "echo hi; git worktree remove $ONLY/rev-1" "$GUARD_ONLY"
run_case "echo && temp removal" deny "$PRIMARY" "echo hi && git worktree remove $ONLY/rev-1" "$GUARD_ONLY"
run_case "temp removal then accepted rm" deny "$PRIMARY" "git worktree remove $ONLY/rev-1; rm -rf ./build" "$GUARD_ONLY"
run_case "accepted rm then shell forced removal" deny "$PRIMARY" "rm -rf ./safe; sh -c 'git worktree remove --force $ONLY/rev-2'" "$GUARD_ONLY"
run_case "authorized removal then sh forced removal" deny "$PRIMARY" "git worktree remove $TMP/outside/rev; sh -c 'git worktree remove --force $ONLY/rev-2'" "$GUARD_NOROOTS" "$TMP/outside/rev"
run_case "authorized removal then bash forced removal" deny "$PRIMARY" "git worktree remove $TMP/outside/rev; bash -c 'git worktree remove --force $ONLY/rev-2'" "$GUARD_NOROOTS" "$TMP/outside/rev"
run_case "same worktree with the temp roots emptied" deny "$PRIMARY" "git worktree remove $ONLY/rev-2" "$GUARD_NOROOTS"
run_case "-C form, unregistered temp worktree" accepted "$OTHER" "git -C $PRIMARY worktree remove $ONLY/rev-3" "$GUARD_ONLY"
run_case "--force on a temp worktree" deny "$PRIMARY" "git worktree remove --force $ONLY/rev-4" "$GUARD_ONLY"
run_case "-f on a temp worktree" deny "$PRIMARY" "git worktree remove -f $ONLY/rev-4" "$GUARD_ONLY"
run_case "-C form, --force on a temp worktree" deny "$OTHER" "git -C $PRIMARY worktree remove --force $ONLY/rev-4" "$GUARD_ONLY"
run_case "sibling directory sharing the root's name prefix" deny "$PRIMARY" "git worktree remove $EVIL/rev" "$GUARD_ONLY"
run_case "worktree outside the temp roots" deny "$PRIMARY" "git worktree remove $TMP/outside/rev" "$GUARD_ONLY"
run_case ".. out of the temp root" deny "$PRIMARY" "git worktree remove $ONLY/../outside/rev" "$GUARD_ONLY"
run_case "the temp root itself is not eligible" deny "$PRIMARY" "git worktree remove $TMP/rootwt" "$(guard_with_roots rootwt "$TMP/rootwt")"
run_case "plain directory under a temp root" deny "$PRIMARY" "git worktree remove $ONLY/plain-dir" "$GUARD_ONLY"
run_case "temp root path that is not a worktree at all" deny "$PRIMARY" "git worktree remove $ONLY/missing" "$GUARD_ONLY"
run_case "the primary tree is not removable from a linked one" deny "$ONLY/rev-5" "git worktree remove $PRIMARY" "$GUARD_ONLY"
run_case "the worktree being removed is the cwd" deny "$ONLY/rev-6" "git worktree remove $ONLY/rev-6" "$GUARD_ONLY"
run_case "temp worktree of another repository" deny "$OTHER" "git worktree remove $ONLY/rev-5" "$GUARD_ONLY"
run_case "registered worktree stays removable without temp roots" accepted "$PRIMARY" "git worktree remove $WT" "$GUARD_NOROOTS"

printf '%s\n' '== execution prefixes: exec, time, builtin, nice, sudo =='
for w in 'exec' 'time' 'time -p' 'builtin' 'nice' 'nice -n 5' 'nice --adjustment=5' 'nice -n 5 time' 'sudo' 'sudo -n' 'sudo -u root' 'sudo -E' \
         '/usr/bin/time' '/usr/bin/nice' '/usr/bin/sudo' '\exec' '"exec"' "'sudo'" 'exec -a name' 'env exec' 'command exec' 'sudo env' 'FOO=1 exec' 'env FOO=1 nice -n 3'; do
  run_case "$w git, --force" deny "$PRIMARY" "$w git worktree remove --force $WT"
  run_case "$w git, -f after path" deny "$PRIMARY" "$w git worktree remove $WT -f"
  run_case "$w git -C P, --force" deny "$OTHER" "$w git -C $PRIMARY worktree remove --force $WT"
  run_case "$w /bin/git, --force" deny "$PRIMARY" "$w /bin/git worktree remove --force $WT"
  run_case "$w git, unlisted path" deny "$PRIMARY" "$w git worktree remove $TMP/scratch/nope" "$GUARD_NOROOTS"
  run_case "$w git, glob target" deny "$PRIMARY" "$w git worktree remove $TMP/scratch/wt-*"
  run_case "$w git, quoted wor''ktree, --force" deny "$PRIMARY" "$w git wor''ktree remove --force $WT"
  run_case "$w git, quoted wor''ktree, no force" deny "$PRIMARY" "$w git wor''ktree remove $WT"
done
for w in exec time builtin nice '/usr/bin/time' '\exec' 'env exec'; do
  run_case "$w git, registered worktree, no force (same as plain)" accepted "$PRIMARY" "$w git worktree remove $WT" "$GUARD_NOROOTS"
done
for w in 'sudo' 'nice -n 5' 'exec -a name'; do
  run_case "$w git, registered worktree, no force (options and sudo are refused)" deny "$PRIMARY" "$w git worktree remove $WT" "$GUARD_NOROOTS"
done
# The reserved word time and its -p are stripped with the control openers, so
# the removal is judged as the plain command and the registry accepts it.
run_case "time -p git, registered worktree, no force (time -p is a control opener)" accepted "$PRIMARY" "time -p git worktree remove $WT" "$GUARD_NOROOTS"
for w in exec time builtin nice sudo; do
  run_case "$w echo of a forced removal is a mention" no_opinion "$PRIMARY" "$w echo git worktree remove --force $WT"
  run_case "echo $w git is a mention" no_opinion "$PRIMARY" "echo $w git worktree remove --force $WT"
done
for w in execx timex /bin/nice-foo mysudo nicer; do
  run_case "$w git is not a prefix, --force" no_opinion "$PRIMARY" "$w git worktree remove --force $WT"
done

printf '%s\n' '== env -u NAME /bin/git =='
run_case "env -u NAME /bin/git, no force" deny "$PRIMARY" "env -u HOME /bin/git worktree remove $WT"
run_case "env -u NAME /bin/git, --force" deny "$PRIMARY" "env -u HOME /bin/git worktree remove --force $WT"
run_case "env -u NAME /usr/local/bin/git, -f" deny "$PRIMARY" "env -u HOME /usr/local/bin/git worktree remove -f $WT"
run_case "env -u NAME /bin/git -C P, --force" deny "$OTHER" "env -u HOME /bin/git -C $PRIMARY worktree remove --force $WT"
run_case "env --unset NAME ~/bin/git, --force" deny "$PRIMARY" "env --unset HOME ~/bin/git worktree remove --force $WT"
run_case "env -u NAME /bin/git, glob target" deny "$PRIMARY" "env -u HOME /bin/git worktree remove $TMP/scratch/wt-*"
run_case "/bin/git alone, registered worktree, no force" accepted "$PRIMARY" "/bin/git worktree remove $WT" "$GUARD_NOROOTS"
run_case "env -u NAME /bin/git-foo is not git" no_opinion "$PRIMARY" "env -u HOME /bin/git-foo worktree remove --force $WT"
run_case "env -u NAME echo of the words is a mention" no_opinion "$PRIMARY" "env -u HOME echo /bin/git worktree remove --force $WT"
run_case "ANSI-C hex escape in worktree word, forced" deny "$PRIMARY" "git \$'work\\x74ree' remove --force $WT"
run_case "ANSI-C Unicode escape in worktree word, forced" deny "$PRIMARY" "git \$'\\u0077orktree' remove --force $WT"
run_case "ANSI-C long Unicode escape in worktree word, forced" deny "$PRIMARY" "git \$'work\\U00000074ree' remove --force $WT"

printf '%s\n' '== removals wrapped in subshells, groups and substitutions =='
run_case "subshell" deny "$PRIMARY" "(git worktree remove --force $WT)"
run_case "subshell, linked temp worktree, no force" deny "$PRIMARY" "(git worktree remove $ONLY/rev-1)" "$GUARD_ONLY"
run_case "subshell followed by sh -c forced removal" deny "$PRIMARY" "(git worktree remove $WT); sh -c 'git worktree remove --force $ONLY/rev-2'" "$GUARD_ONLY"
run_case "subshell followed by bash -c recursive rm" deny "$PRIMARY" "(git worktree remove $WT); bash -c 'rm -rf /'"
run_case "subshell with inner spaces" deny "$PRIMARY" "( git worktree remove --force $WT )"
run_case "nested subshell" deny "$PRIMARY" "((git worktree remove --force $WT))"
run_case "subshell after another command" deny "$PRIMARY" "true && (git worktree remove --force $WT)"
run_case "subshell with a leading cd" deny "$PRIMARY" "(cd /; git worktree remove --force $WT)"
run_case "subshell, -f after path" deny "$PRIMARY" "(git worktree remove $WT -f)"
run_case "subshell, prefixed git" deny "$PRIMARY" "(env git worktree remove --force $WT)"
run_case "subshell, quoted wor''ktree" deny "$PRIMARY" "(git wor''ktree remove --force $WT)"
run_case "subshell, unlisted path, no force" deny "$PRIMARY" "(git worktree remove $TMP/scratch/nope)" "$GUARD_NOROOTS"
run_case "brace group" deny "$PRIMARY" "{ git worktree remove --force $WT; }"
run_case "brace group on several lines" deny "$PRIMARY" $'{\n git worktree remove --force '"$WT"$'\n}'
run_case "negated removal" deny "$PRIMARY" "! git worktree remove --force $WT"
run_case "\$(...) argument" deny "$PRIMARY" "echo \$(git worktree remove --force $WT)"
run_case "\$(...) as the command word" deny "$PRIMARY" "\$(git worktree remove --force $WT)"
run_case "backtick argument" deny "$PRIMARY" "echo \`git worktree remove --force $WT\`"
run_case "\$(...) inside double quotes" deny "$PRIMARY" "echo \"\$(git worktree remove --force $WT)\""
run_case "\$(...) inside double quotes after an apostrophe" deny "$PRIMARY" "echo \"it's \$(git worktree remove --force $WT)\""
run_case "\$(...) in an assignment" deny "$PRIMARY" "x=\$(git worktree remove --force $WT)"
run_case "process substitution" deny "$PRIMARY" "cat <(git worktree remove --force $WT)"
run_case "nested substitutions" deny "$PRIMARY" "echo \$(echo \$(git worktree remove --force $WT))"
run_case "substitution holding a pipeline" deny "$PRIMARY" "echo \$(true | git worktree remove --force $WT)"
run_case "substitution with an inner semicolon before a forced removal" deny "$PRIMARY" "echo \$(git worktree remove --force $WT; true)"
run_case "substitution holding env -S" deny "$PRIMARY" "echo \$(env -S 'git worktree remove --force $WT')"
run_case "exec env -S carrying a forced removal" deny "$PRIMARY" "exec env -S \"git worktree remove --force $WT\""
run_case "time env -S carrying a forced removal" deny "$PRIMARY" "time env -S \"git worktree remove --force $WT\""
run_case "nice env -S carrying a forced removal" deny "$PRIMARY" "nice env -S \"git worktree remove --force $WT\""
run_case "builtin env -S carrying a forced removal" deny "$PRIMARY" "builtin env -S \"git worktree remove --force $WT\""
run_case "exec -a x env -S carrying a forced removal" deny "$PRIMARY" "exec -a x env -S \"git worktree remove --force $WT\""
run_case "substitution, unlisted path, no force" deny "$PRIMARY" "echo \$(git worktree remove $TMP/scratch/nope)" "$GUARD_NOROOTS"
run_case "substitution, registered worktree, no force, is not auto-allowed" deny/removal_beside_substitution "$PRIMARY" "echo \$(git worktree remove $WT)" "$GUARD_NOROOTS"
NESTED=""; CLOSERS=""
for _ in $(seq 1 20); do NESTED="$NESTED\$("; CLOSERS="$CLOSERS)"; done
# No removal word: no opinion, however deep the nesting (there is no depth bound).
run_case "runaway substitution nesting has no removal word" no_opinion "$PRIMARY" "echo ${NESTED}true${CLOSERS}"
run_case "repeated identical substitutions are not a runaway" no_opinion "$PRIMARY" "echo \$(date) \$(date) \$(date) \$(date) \$(date) \$(date) \$(date) \$(date) \$(date) \$(date) \$(date) \$(date) \$(date) \$(date) \$(date) \$(date) \$(date) \$(date)"
run_case "duplicate after sixteen distinct fragments stays within cap" no_opinion "$PRIMARY" 'echo $(printf 1) $(printf 2) $(printf 3) $(printf 4) $(printf 5) $(printf 6) $(printf 7) $(printf 8) $(printf 9) $(printf 10) $(printf 11) $(printf 12) $(printf 13) $(printf 14) $(printf 15) $(printf 16) $(printf 1)'
run_case "single-quoted substitution beside the words" deny/removal_beside_substitution "$PRIMARY" "echo '\$(git worktree remove --force $WT)'"
run_case "single-quoted subshell is a mention" no_opinion "$PRIMARY" "echo '(git worktree remove --force $WT)'"
run_case "escaped substitution beside the words" deny/removal_beside_substitution "$PRIMARY" "echo \\\$(git worktree remove --force $WT)"
run_case "substitution holding only a mention" deny/removal_beside_substitution "$PRIMARY" 'git commit -m "$(echo worktree remove --force)"'
run_case "subshell holding only an echo" no_opinion "$PRIMARY" "(echo git worktree remove --force $WT)"
run_case "substitution holding only an echo" deny/removal_beside_substitution "$PRIMARY" "echo \$(echo git worktree remove --force $WT)"

printf '%s\n' '== unparseable removal without a force flag =='
run_case "git wor''ktree" deny "$PRIMARY" "git wor''ktree remove $WT" "$GUARD_NOROOTS"
run_case "git worktree re''move" deny "$PRIMARY" "git worktree re''move $WT" "$GUARD_NOROOTS"
run_case "git worktree re\\move" deny "$PRIMARY" "git worktree re\\move $WT" "$GUARD_NOROOTS"
run_case "git \$'worktree'" deny "$PRIMARY" "git \$'worktree' remove $WT" "$GUARD_NOROOTS"
run_case "git -c quoted-space value, wor''ktree" deny "$PRIMARY" "git -c \"k=v w\" wor''ktree remove $WT" "$GUARD_NOROOTS"
run_case "env git wor''ktree" deny "$PRIMARY" "env git wor''ktree remove $WT" "$GUARD_NOROOTS"
run_case "unbalanced quote" deny "$PRIMARY" "git wor''ktree remove $WT \"x" "$GUARD_NOROOTS"
run_case "echo of an unparseable removal" no_opinion "$PRIMARY" "echo git wor''ktree remove $WT"
run_case "git wor''ktree list" no_opinion "$PRIMARY" "git wor''ktree list"
run_case "legit wor''ktree remove is not git" no_opinion "$PRIMARY" "legit wor''ktree remove $WT"
run_case "git commit mentioning the words, unparseable" deny/removal_beside_substitution "$PRIMARY" 'git commit -m "docs: worktree remove" $(date)'

printf '%s\n' '== command splitter: operators, quoting, continuation, heredocs =='
# A heredoc is a carrier: a body that holds the removal words denies
# removal_beside_heredoc, data or not.
F="git worktree remove --force $WT"
run_case "after ;" deny "$PRIMARY" "true; $F"
run_case "after &&" deny "$PRIMARY" "true && $F"
run_case "after ||" deny "$PRIMARY" "false || $F"
run_case "after a single &" deny "$PRIMARY" "sleep 1 & $F"
run_case "after a pipe" deny "$PRIMARY" "echo x | $F"
run_case "after a newline" deny "$PRIMARY" $'true\n'"$F"
run_case "after a continued line" deny "$PRIMARY" $'true \\\n&& '"$F"
run_case "before ;" deny "$PRIMARY" "$F; true"
run_case "inside a continued word, plain" deny "$PRIMARY" $'git worktree remove \\\n--force '"$WT"
run_case "inside a continued word, double quotes" deny "$PRIMARY" $'git worktree remove "--for\\\nce" '"$WT"
run_case "continuation joins the halves of a word, plain" deny "$PRIMARY" $'git wor\\\nktree remove --force '"$WT"
run_case "continuation joins the halves of a word, double quotes" deny "$PRIMARY" $'git "wor\\\nktree" remove --force '"$WT"
# Beside a removal word, a line continuation or an operator inside quotes
# denies (rule G): the segmenter does not split there.
run_case "continuation joins the halves of a mention" deny/hidden_separator "$PRIMARY" $'echo git wor\\\nktree remove --force '"$WT"
run_case "; inside single quotes does not split" no_opinion "$PRIMARY" "echo 'a;b'; true"
run_case "operators inside single quotes do not split" deny/hidden_separator "$PRIMARY" "echo 'a && $F | b'"
run_case "operators inside double quotes do not split" deny/hidden_separator "$PRIMARY" "echo \"a && $F | b\""
run_case "escaped quote does not open a string" deny "$PRIMARY" "echo \\\"; $F"
run_case "removal after a closed quote pair" deny "$PRIMARY" "echo 'a' \"b\"; $F"
run_case "escaped quote inside double quotes before forced removal" deny "$PRIMARY" "echo \"a\\\"b\"; $F"
run_case "escaped quote inside ANSI-C quotes before forced removal" deny "$PRIMARY" "echo \$'\\''; $F"
run_case "heredoc body holds the words" deny/removal_beside_heredoc "$PRIMARY" $'cat <<EOF\n'"$F"$'\nEOF'
run_case "heredoc, quoted delimiter" deny/removal_beside_heredoc "$PRIMARY" $'cat <<\'EOF\'\n'"$F"$'\nEOF'
run_case "heredoc, double-quoted delimiter" deny/removal_beside_heredoc "$PRIMARY" $'cat <<"EOF"\n'"$F"$'\nEOF'
run_case "heredoc, backslash delimiter" deny/removal_beside_heredoc "$PRIMARY" $'cat <<\\EOF\n'"$F"$'\nEOF'
run_case "heredoc, spaced delimiter" deny/removal_beside_heredoc "$PRIMARY" $'cat << EOF\n'"$F"$'\nEOF'
run_case "heredoc, <<- with a tab-indented terminator" deny/removal_beside_heredoc "$PRIMARY" $'cat <<-EOF\n'"$F"$'\n\tEOF'
run_case "removal after a <<- heredoc with a tab-indented terminator" deny "$PRIMARY" $'cat <<-EOF\ndata\n\tEOF\n'"$F"
run_case "heredoc, plain << needs an exact terminator" deny/removal_beside_heredoc "$PRIMARY" $'cat <<EOF\n'"$F"$'\n\tEOF\n'"$F"$'\nEOF'
run_case "two heredocs on one line" deny/removal_beside_heredoc "$PRIMARY" $'cat <<A <<B\n'"$F"$'\nA\n'"$F"$'\nB'
run_case "removal after a heredoc" deny "$PRIMARY" $'cat <<EOF\ndata\nEOF\n'"$F"
run_case "removal on the heredoc header line" deny "$PRIMARY" $'cat <<EOF; '"$F"$'\ndata\nEOF'
run_case "removal after two heredocs" deny "$PRIMARY" $'cat <<A <<B\na\nA\nb\nB\n'"$F"
run_case "unterminated heredoc swallows the rest" deny/removal_beside_heredoc "$PRIMARY" $'cat <<EOF\n'"$F"
run_case "here-string is not a heredoc" deny "$PRIMARY" $'cat <<< x\n'"$F"
run_case "heredoc delimiter in a pipeline header" deny "$PRIMARY" $'cat <<EOF | cat\ndata\nEOF\n'"$F"

if [ "$FAILURES" -eq 0 ]; then
  printf '%s\n' 'ALL WRAPPER TESTS PASSED'
  printf 'Fixtures retained at %s (test performs no deletion)\n' "$TMP"
  exit 0
fi
printf '%s TEST(S) FAILED\n' "$FAILURES"
exit 1
