#!/bin/bash
# Fail closed, by name. Every case runs the guard as the hook runs it, or a
# copy of it whose one constant is rewritten (the guard reads no variable,
# option or file for its trusted roots or its tier lists):
#   D1   each required program of each tier, missing, denies with its own code;
#        a worktree-tier program is needed only by a worktree removal (B2).
#   P    a bin folder at the front of PATH holding jq, wc, tr, git and grep
#        that each write a marker: none of them ever runs (all five resolve
#        only from the trusted roots). J1 and the planted ./jq row start the
#        hook process in the folder that holds the plant. J2: a jq only
#        outside the roots is not used. J0: the shipped roots line and hint.
#   D2   the crash trap; D3 the signal traps; H1 the hook entry.
#   G1   inherited git repository selectors deny; C1 and C2 inherited git
#        configuration is cleared from the guard's own git calls.
#   D4   interpreter startup state (BASH_ENV, exported functions, Perl startup
#        variables, $HOME/.jq) changes no verdict.
#   D-S1 every program the guard runs is in a tier list or on the exemptions.
#   U1   USERS_ROOT cannot be moved from the environment.
# Command strings are inert JSON data and are never executed.
set -u
LC_ALL=C

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUARD="$HERE/../hooks/destructive-command-guard.sh"
HOOKS="$HERE/../hooks/hooks.json"
PLUGIN_ROOT="$(cd "$HERE/.." && pwd)"
JQ="$(command -v jq)" || { printf 'FAIL: jq is not on PATH\n'; exit 1; }
T="$(mktemp -d "$(cd /tmp && pwd -P)/destructive-guard-deps.XXXXXX")" || exit 1
WT="$T/worktree"
LINKED="$T/linked"
MISSING="$T/missing"
GITHOME="$T/githome"

FAILURES=0
pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; FAILURES=$((FAILURES + 1)); }

git_q() { HOME="$GITHOME" git -c user.name=t -c user.email=t@example.invalid "$@"; }
mkdir -p "$GITHOME" "$MISSING" "$T/copies" "$T/roots"
git_q init -q "$WT" || exit 1
printf 'x\n' >"$WT/f.txt"
git_q -C "$WT" add f.txt && git_q -C "$WT" commit -qm seed || exit 1
mkdir -p "$WT/build"
git_q -C "$WT" worktree add -q --detach "$LINKED" HEAD || exit 1

# The five programs the guard runs by name, and where this host keeps them.
NAMED_TOOLS="jq wc tr git grep"
for tool in $NAMED_TOOLS; do
  command -v "$tool" >/dev/null 2>&1 || { fail "fixture: $tool is not on PATH"; exit 1; }
done

# root_dir NAME TOOL...: a folder holding symlinks to the given tools.
root_dir() {
  local dir="$T/roots/$1" tool
  shift
  mkdir -p "$dir"
  for tool in "$@"; do ln -s "$(command -v "$tool")" "$dir/$tool" || exit 1; done
  printf '%s' "$dir"
}
# roots_copy DEST DIR...: a copy of the guard whose TRUSTED_ROOTS line names
# DIR...; fails the suite when the rewrite changes nothing.
roots_copy() {
  local dst="$1" line
  shift
  line="TRUSTED_ROOTS=($*)"
  sed "s|^TRUSTED_ROOTS=.*|$line|" "$GUARD" >"$dst" || exit 1
  if ! grep -qxF "$line" "$dst" || cmp -s "$GUARD" "$dst"; then
    fail "fixture: $dst has no TRUSTED_ROOTS line to rewrite"
    exit 1
  fi
}
# replace_copy DEST OLD NEW [SOURCE]: a copy of SOURCE (the guard) with OLD,
# which must occur exactly once, replaced by NEW. When OLD is not there the
# copy is not written, a FAIL is printed and the row that runs it fails too.
replace_copy() {
  python3 -I -c 'import sys
dst, old, new, src = sys.argv[1:5]
text = open(src, encoding="utf-8").read()
n = text.count(old)
if n != 1:
    sys.exit("replace_copy: %d matches for %r" % (n, old[:80]))
open(dst, "w", encoding="utf-8").write(text.replace(old, new))' "$1" "$2" "$3" "${4:-$GUARD}" || fail "fixture: $1 could not be written"
}

payload() { # CWD COMMAND
  "$JQ" -cn --arg cwd "$1" --arg command "$2" \
    '{hook_event_name:"PreToolUse",tool_name:"Bash",tool_input:{command:$command},cwd:$cwd}'
}
# Every run starts with the loop's variables unset (a row arms them itself)
# and then unsets the names in HOOK_UNSET.
BASE_UNSET=(-u LANE_ROOTS_REGISTRY -u CLEANUP_ITEMS_REGISTRY -u CLEANUP_SEAT -u SEAT_SCRATCH -u CLAUDE_SEAT_UNATTENDED)
HOOK_UNSET=()
# hook GUARD_FILE PROCESS_CWD JSON_CWD COMMAND [ENV=VAL...] [-- ARG...]: runs
# the adapter, with -p, in PROCESS_CWD; sets OUT, RC.
OUT=""
RC=0
hook() {
  local guard="$1" pcwd="$2" jcwd="$3" command_data="$4" input
  local -a envs=() args=()
  shift 4
  while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do envs+=("$1"); shift; done
  [ "$#" -eq 0 ] || { shift; args=("$@"); }
  input="$(payload "$jcwd" "$command_data")"
  OUT="$(cd "$pcwd" && printf '%s' "$input" | /usr/bin/env "${BASE_UNSET[@]}" ${HOOK_UNSET[@]+"${HOOK_UNSET[@]}"} \
    HOME="$GITHOME" ${envs[@]+"${envs[@]}"} \
    /bin/bash --noprofile --norc -p "$guard" ${args[@]+"${args[@]}"} 2>/dev/null)"
  RC=$?
}
# policy GUARD_FILE CWD COMMAND [ENV=VAL...]: the engine alone; sets OUT, RC.
policy() {
  local guard="$1" cwd="$2" command_data="$3" input
  shift 3
  input="$("$JQ" -cn --arg cwd "$cwd" --arg command "$command_data" '{cwd:$cwd,command:$command}')"
  OUT="$(cd "$cwd" && printf '%s' "$input" | /usr/bin/env "${BASE_UNSET[@]}" ${HOOK_UNSET[@]+"${HOOK_UNSET[@]}"} \
    HOME="$GITHOME" "$@" /bin/bash --noprofile --norc -p "$guard" --policy 2>/dev/null)"
  RC=$?
}
reason_of() { # -> the reason code the last adapter deny names, or "silent"
  if [ "$RC" -eq 0 ] && [ -z "$OUT" ]; then printf silent; return; fi
  printf '%s' "$OUT" | "$JQ" -er '.hookSpecificOutput.permissionDecisionReason' 2>/dev/null \
    | sed -n 's/^Destructive command guard denied the command (reason=\([a-z_]*\)).*/\1/p'
}
expect_hook() { # LABEL WANT(silent|CODE)
  local got
  got="$(reason_of)"
  if [ "$RC" -eq 0 ] && [ "$got" = "$2" ]; then
    pass "$1 -> ${2}"
  else
    fail "$1: expected $2, got exit $RC, ${got:-output ${OUT:0:160}}"
  fi
}
expect_policy() { # LABEL DECISION/REASON
  local got
  got="$(printf '%s' "$OUT" | "$JQ" -er '.decision + "/" + .reason' 2>/dev/null || printf 'invalid (%s)' "${OUT:0:80}")"
  if [ "$got" = "$2" ]; then pass "$1 -> $got"; else fail "$1: expected $2, got $got"; fi
}
expect_hint() { # LABEL TEXT
  case "$OUT" in *"$2"*) pass "$1: the hint names it" ;; *) fail "$1: the hint does not hold '$2': ${OUT:0:240}" ;; esac
}

# A trusted, empty lane registry to arm the armed tier with.
ARMED_DIR="$T/seat/scratch"
mkdir -p "$ARMED_DIR"
printf 'path\trepo\tbranch\tcreated_at\n' >"$ARMED_DIR/lane-roots.tsv"
chmod 600 "$ARMED_DIR/lane-roots.tsv"
chmod 700 "$ARMED_DIR" "$T/seat"
ARMED="LANE_ROOTS_REGISTRY=$ARMED_DIR/lane-roots.tsv"

ROOTS_TEXT="/usr/bin, /bin, /usr/local/bin, /opt/homebrew/bin or /home""/linuxbrew/.linuxbrew/bin"

printf '%s\n' '== D1 adapter tier =='
EMPTY_ROOT="$(root_dir empty)"
roots_copy "$T/copies/no-roots.sh" "$EMPTY_ROOT"
hook "$T/copies/no-roots.sh" "$WT" "$WT" 'rm -rf build'
expect_hook "D1 adapter jq: no trusted root holds jq" dependency_missing_jq
expect_hint "D1 adapter jq" "jq was not found in $ROOTS_TEXT; install jq in one of them"
hook "$GUARD" "$WT" "$WT" 'rm -rf build' -- --timeout-runner "$MISSING/perl"
expect_hook "D1 adapter perl: the runner is missing" dependency_missing_perl
expect_hint "D1 adapter perl" "/usr/bin/perl is missing"
hook "$GUARD" "$WT" "$WT" 'rm -rf build' -- --adapter-policy "$MISSING/engine.sh"
expect_hook "D1 adapter engine: the policy file is missing" dependency_missing_engine

printf '%s\n' '== D1 engine tier =='
roots_copy "$T/copies/no-jq.sh" "$(root_dir no-jq wc tr git grep)"
policy "$T/copies/no-jq.sh" "$WT" 'rm -rf build'
expect_policy "D1 engine jq: --policy with no jq in any root" deny/dependency_missing_jq
roots_copy "$T/copies/no-wc.sh" "$(root_dir no-wc jq tr git grep)"
hook "$T/copies/no-wc.sh" "$WT" "$WT" 'rm -rf build'
expect_hook "D1 engine wc" dependency_missing_wc
expect_hint "D1 engine wc" "wc was not found in $ROOTS_TEXT"
roots_copy "$T/copies/no-tr.sh" "$(root_dir no-tr jq wc git grep)"
hook "$T/copies/no-tr.sh" "$WT" "$WT" 'rm -rf build'
expect_hook "D1 engine tr" dependency_missing_tr
expect_hint "D1 engine tr" "tr was not found in $ROOTS_TEXT"

printf '%s\n' '== D1 context tier =='
roots_copy "$T/copies/no-git.sh" "$(root_dir no-git jq wc tr grep)"
hook "$T/copies/no-git.sh" "$WT" "$WT" 'rm -rf build'
expect_hook "D1 context git" dependency_missing_git
expect_hint "D1 context git" "git was not found in $ROOTS_TEXT"
hook "$T/copies/no-git.sh" "$WT" "$WT" 'ls build'
expect_hook "D1 context git: a command that is not a removal does not need git" silent
replace_copy "$T/copies/no-env.sh" 'CONTEXT_TOOLS=(git env:/usr/bin/env)' "CONTEXT_TOOLS=(git env:$MISSING/env)"
hook "$T/copies/no-env.sh" "$WT" "$WT" 'rm -rf build'
expect_hook "D1 context env" dependency_missing_env

printf '%s\n' '== D1 worktree tier, and B2: only a worktree removal needs it =='
for entry in system_git:/usr/bin/git perl:/usr/bin/perl find:/usr/bin/find readlink:/usr/bin/readlink cat:/bin/cat; do
  code="${entry%%:*}"
  replace_copy "$T/copies/no-wt-$code.sh" "$entry" "$code:$MISSING/$code"
  hook "$T/copies/no-wt-$code.sh" "$WT" "$WT" "git worktree remove $LINKED"
  expect_hook "D1 worktree $code" "dependency_missing_$code"
  hook "$T/copies/no-wt-$code.sh" "$WT" "$WT" 'rm -rf build'
  expect_hook "B2 worktree $code missing: an ordinary rm -rf build keeps its verdict" silent
done

printf '%s\n' '== D1 armed tier (a registry variable is set) =='
replace_copy "$T/copies/no-stat.sh" 'STAT_CANDIDATES=(/usr/bin/stat /bin/stat)' "STAT_CANDIDATES=($MISSING/stat)"
hook "$T/copies/no-stat.sh" "$WT" "$WT" 'rm -rf build' "$ARMED"
expect_hook "D1 armed stat" dependency_missing_stat
hook "$T/copies/no-stat.sh" "$WT" "$WT" 'rm -rf build'
expect_hook "D1 armed stat: unarmed, no stat is needed" silent
roots_copy "$T/copies/no-grep.sh" "$(root_dir no-grep jq wc tr git)"
hook "$T/copies/no-grep.sh" "$WT" "$WT" 'rm -rf build' "$ARMED"
expect_hook "D1 armed grep" dependency_missing_grep
expect_hint "D1 armed grep" "grep was not found in $ROOTS_TEXT"
for entry in id:/usr/bin/id date:/bin/date; do
  code="${entry%%:*}"
  replace_copy "$T/copies/no-armed-$code.sh" "$entry" "$code:$MISSING/$code"
  hook "$T/copies/no-armed-$code.sh" "$WT" "$WT" 'rm -rf build' "$ARMED"
  expect_hook "D1 armed $code" "dependency_missing_$code"
done
hook "$GUARD" "$WT" "$WT" 'rm -rf build' "$ARMED"
expect_hook "D1 armed control: every armed program present" silent

printf '%s\n' '== P: programs planted at the front of PATH never run =='
PLANT="$T/plant/bin"
MARKS="$T/plant/marks"
mkdir -p "$PLANT" "$MARKS"
for tool in $NAMED_TOOLS; do
  printf '#!/bin/bash\n: >%s\nexit 0\n' "$MARKS/$tool" >"$PLANT/$tool"
  chmod 755 "$PLANT/$tool"
done
# rm -rf build in a worktree, armed: the adapter and engine run jq, the length
# read runs wc and tr, context discovery runs git, the registry check runs grep.
hook "$GUARD" "$WT" "$WT" 'rm -rf build' "$ARMED" "PATH=$PLANT:$PATH"
expect_hook "P the command keeps its verdict with every tool planted on PATH" silent
hook "$GUARD" "$WT" "$WT" "git worktree remove $LINKED" "$ARMED" "PATH=$PLANT:$PATH"
expect_hook "P a worktree removal keeps its verdict with every tool planted on PATH" silent
for tool in $NAMED_TOOLS; do
  if [ ! -e "$MARKS/$tool" ]; then
    pass "P planted $tool on PATH never ran"
  else
    fail "P planted $tool on PATH ran"
  fi
done

printf '%s\n' '== planted ./jq: PATH with an empty, a dot and a relative entry =='
DOTDIR="$T/dotjq"
mkdir -p "$DOTDIR/rel"
printf '#!/bin/bash\n: >%s\nexit 0\n' "$MARKS/dot-jq" >"$DOTDIR/jq"
cp "$DOTDIR/jq" "$DOTDIR/rel/jq"
chmod 755 "$DOTDIR/jq" "$DOTDIR/rel/jq"
hook "$GUARD" "$DOTDIR" "$WT" 'rm -rf build' "PATH=:.:rel:$PATH"
expect_hook "planted ./jq: the trusted root's jq decides" silent
hook "$T/copies/no-roots.sh" "$DOTDIR" "$WT" 'rm -rf build' "PATH=:.:rel:$PATH"
expect_hook "planted ./jq with no jq in any root" dependency_missing_jq
if [ ! -e "$MARKS/dot-jq" ]; then pass "planted ./jq never ran"; else fail "planted ./jq ran"; fi

printf '%s\n' "== J1: PATH starts with the worktree's own absolute bin =="
J1_WT="$T/j1-worktree"
git_q init -q "$J1_WT" || exit 1
mkdir -p "$J1_WT/bin" "$J1_WT/build"
printf '#!/bin/bash\n: >%s\nexit 0\n' "$MARKS/j1-jq" >"$J1_WT/bin/jq"
chmod 755 "$J1_WT/bin/jq"
hook "$GUARD" "$J1_WT" "$J1_WT" 'rm -rf build' "PATH=$J1_WT/bin:$PATH"
expect_hook "J1 the trusted root's jq decides" silent
hook "$T/copies/no-roots.sh" "$J1_WT" "$J1_WT" 'rm -rf build' "PATH=$J1_WT/bin:$PATH"
expect_hook "J1 with no jq in any root" dependency_missing_jq
if [ ! -e "$MARKS/j1-jq" ]; then pass "J1 the worktree's bin/jq never ran"; else fail "J1 the worktree's bin/jq ran"; fi

printf '%s\n' '== J2: a working jq only outside the roots =='
OUTSIDE_JQ="$(root_dir outside-jq jq)"
hook "$T/copies/no-roots.sh" "$WT" "$WT" 'rm -rf build' "PATH=$OUTSIDE_JQ:$PATH"
expect_hook "J2 a jq outside the trusted roots is not used" dependency_missing_jq
expect_hint "J2 the text names the five roots" "$ROOTS_TEXT"

printf '%s\n' '== J0: the shipped roots and hint =='
WANT_ROOTS='TRUSTED_ROOTS=(/usr/bin /bin /usr/local/bin /opt/homebrew/bin "/home"/linuxbrew/.linuxbrew/bin)'
if [ "$(grep -c '^TRUSTED_ROOTS=' "$GUARD")" -eq 1 ] && grep -qxF "$WANT_ROOTS" "$GUARD"; then
  pass "J0 the roots line is exactly the five roots, in order"
else
  fail "J0 the roots line is not exactly: $WANT_ROOTS"
fi
WANT_HINT='ROOTS_HINT="/usr/bin, /bin, /usr/local/bin, /opt/homebrew/bin or /home""/linuxbrew/.linuxbrew/bin"'
if [ "$(grep -c '^ROOTS_HINT=' "$GUARD")" -eq 1 ] && grep -qxF "$WANT_HINT" "$GUARD"; then
  pass "J0 the hint names exactly the five roots, in order"
else
  fail "J0 the hint line is not exactly: $WANT_HINT"
fi
# which counts only as a command word: the carrier hints say "which the guard
# does not read inside".
if [ "$(grep -c 'resolve_tool ' "$GUARD")" -eq 1 ] && ! grep -v '^[[:space:]]*#' "$GUARD" | grep -qE 'command -v|type -P|hash -t|(^|[;&|({`]|\$\()[[:space:]]*which[[:space:]]'; then
  pass "J0 one resolver, and no PATH lookup anywhere in the guard"
else
  fail "J0 the guard has a second resolver or a PATH lookup"
fi

printf '%s\n' '== D2 the crash trap =='
FAIL_LINE='{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Destructive command guard denied the command (reason=adapter_failed): the guard'"'"'s adapter failed; retry once and report the code if it repeats."}}'
INT_LINE='{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Destructive command guard denied the command (reason=adapter_interrupted): the guard'"'"'s adapter was stopped by a signal; retry once."}}'
RM_DENY_LINE='{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Destructive command guard denied the command (reason=unsafe_rm): an rm target is not a literal path strictly below the current worktree or the named session scratchpad; spell a worktree target relative to the current folder, because an absolute home path is refused by spelling."}}'
replace_copy "$T/copies/d2a.sh" '  GUARD_LOG_INPUT="$normalized"' '  GUARD_LOG_INPUT="$normalized"
  : "$D2_UNBOUND_VARIABLE"'
hook "$T/copies/d2a.sh" "$WT" "$WT" 'rm -rf build'
if [ "$RC" -eq 0 ] && [ "$OUT" = "$FAIL_LINE" ]; then
  pass "D2 (a) an unbound variable after normalisation: one adapter_failed line, exit 0"
else
  fail "D2 (a) expected one adapter_failed line and exit 0, got exit $RC and: $OUT"
fi
# Right after VERDICT_DONE=1, the line after the deny printf.
replace_copy "$T/copies/d2b.sh" '"$DENY_HINT" || exit 3
  VERDICT_DONE=1
' '"$DENY_HINT" || exit 3
  VERDICT_DONE=1
  kill -TERM $$
'
hook "$T/copies/d2b.sh" "$WT" "$WT" 'rm -rf /'
if [ "$RC" -eq 0 ] && [ "$OUT" = "$RM_DENY_LINE" ]; then
  pass "D2 (b) a signal after the deny line: the one original line, exit 0"
else
  fail "D2 (b) expected the one unsafe_rm line and exit 0, got exit $RC and: $OUT"
fi
# On the line just above the deny printf, with no other change.
replace_copy "$T/copies/d2c.sh" '  printf '"'"'{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Destructive command guard denied the command (reason=%s)' '  kill -TERM $$
  printf '"'"'{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Destructive command guard denied the command (reason=%s)'
hook "$T/copies/d2c.sh" "$WT" "$WT" 'rm -rf /'
if [ "$RC" -ne 0 ] && [ -z "$OUT" ]; then
  pass "D2 (c) a signal between the two flags: exit $RC (a block under onFailure), no output"
else
  fail "D2 (c) expected a non-zero exit and no output, got exit $RC and: $OUT"
fi

# (d) fork failure. ulimit -u 1 must bite first; then the guard runs with no
# fork left, from a PATH holding only an empty folder.
if /bin/bash -c 'ulimit -u 1; /usr/bin/true; :' >/dev/null 2>&1; then
  fail "D2 (d) fixture: ulimit -u 1 does not stop a fork here (is the suite running as root?)"
else
  pass "D2 (d) fixture: ulimit -u 1 stops a fork"
fi
forkless() { # GUARD_FILE: sets OUT, RC; a 60 s alarm bounds it
  OUT="$(payload "$WT" 'rm -rf build' | /usr/bin/env -i PATH="$EMPTY_ROOT" HOME="$GITHOME" \
    /usr/bin/perl -e 'alarm 60; exec @ARGV' /bin/bash -c 'ulimit -u 1; exec /bin/bash --noprofile --norc -p "$0"' "$1" 2>/dev/null)"
  RC=$?
}
JQ_MISSING_LINE='{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Destructive command guard denied the command (reason=dependency_missing_jq): jq was not found in '"$ROOTS_TEXT"'; install jq in one of them."}}'
forkless "$T/copies/no-roots.sh"
if [ "$RC" -eq 0 ] && [ "$OUT" = "$JQ_MISSING_LINE" ]; then
  pass "D2 (d1) no fork left, no jq in any root: one dependency_missing_jq line, exit 0"
else
  fail "D2 (d1) expected one dependency_missing_jq line and exit 0, got exit $RC and: $OUT"
fi
replace_copy "$T/copies/d2d2.sh" 'run_adapter() {
' 'run_adapter() {
  : "$D2_UNBOUND_VARIABLE"
'
forkless "$T/copies/d2d2.sh"
if [ "$RC" -eq 0 ] && [ "$OUT" = "$FAIL_LINE" ]; then
  pass "D2 (d2) no fork left, an unbound variable: one adapter_failed line, exit 0"
else
  fail "D2 (d2) expected one adapter_failed line and exit 0, got exit $RC and: $OUT"
fi

printf '%s\n' '== D3 the signal traps =='
for sig in HUP INT QUIT ILL TRAP ABRT BUS FPE USR1 SEGV USR2 PIPE ALRM TERM XCPU XFSZ VTALRM PROF SYS; do
  if ! kill -l "$sig" >/dev/null 2>&1; then
    pass "D3 $sig is not defined on this platform; the guard skips it"
    continue
  fi
  replace_copy "$T/copies/d3-$sig.sh" '  GUARD_LOG_INPUT="$normalized"' "  GUARD_LOG_INPUT=\"\$normalized\"
  kill -$sig \$\$"
  hook "$T/copies/d3-$sig.sh" "$WT" "$WT" 'rm -rf build'
  if [ "$RC" -eq 0 ] && [ "$OUT" = "$INT_LINE" ]; then
    pass "D3 $sig after normalisation: one adapter_interrupted line, exit 0"
  else
    fail "D3 $sig expected one adapter_interrupted line and exit 0, got exit $RC and: $OUT"
  fi
done
# The hook argv of hooks.json, with the plugin root substituted.
HOOK_ARGV_JSON="$("$JQ" -c --arg root "$PLUGIN_ROOT" '[.hooks.PreToolUse[].hooks[] | select(.type == "command")] | .[0] | [.command] + (.args | map(gsub("\\$\\{CLAUDE_PLUGIN_ROOT\\}"; $root)))' "$HOOKS")" || { fail "D3 fixture: hooks.json has no command hook"; exit 1; }
HOOK_ARGV=()
while IFS= read -r word; do HOOK_ARGV+=("$word"); done < <("$JQ" -r '.[]' <<<"$HOOK_ARGV_JSON")
argv_with_guard() { # GUARD_FILE: prints the hook argv with its last word replaced, one per line
  local i=0 n="${#HOOK_ARGV[@]}"
  while [ "$i" -lt $((n - 1)) ]; do printf '%s\n' "${HOOK_ARGV[$i]}"; i=$((i + 1)); done
  printf '%s\n' "$1"
}
run_argv() { # GUARD_FILE CWD COMMAND [ENV=VAL...]: the hook argv; sets OUT, RC
  local guard="$1" cwd="$2" command_data="$3" input word
  local -a argv=()
  shift 3
  while IFS= read -r word; do argv+=("$word"); done < <(argv_with_guard "$guard")
  input="$(payload "$cwd" "$command_data")"
  OUT="$(cd "$cwd" && printf '%s' "$input" | /usr/bin/env "${BASE_UNSET[@]}" HOME="$GITHOME" "$@" "${argv[@]}" 2>/dev/null)"
  RC=$?
}
replace_copy "$T/copies/d3-kill.sh" '  GUARD_LOG_INPUT="$normalized"' '  GUARD_LOG_INPUT="$normalized"
  kill -KILL $$'
run_argv "$T/copies/d3-kill.sh" "$WT" 'rm -rf build'
if [ "$RC" -ne 0 ] && [ -z "$OUT" ]; then pass "D3 KILL: exit $RC, no output (a block under onFailure)"; else fail "D3 KILL: exit $RC and: $OUT"; fi
run_argv "$T/copies/not-there.sh" "$WT" 'rm -rf build'
if [ "$RC" -ne 0 ] && [ -z "$OUT" ]; then pass "D3 a missing guard file: exit $RC, no output (a block under onFailure)"; else fail "D3 missing guard file: exit $RC and: $OUT"; fi

printf '%s\n' '== H1 the hook entry =='
H1="$("$JQ" -r '
  [.hooks | to_entries[] | .key as $event | .value[] | .matcher as $m | .hooks[] | {event: $event, matcher: $m} + .] as $all
  | ($all | map(select((.args // []) | map(tostring) | any(endswith("/hooks/destructive-command-guard.sh"))))) as $g
  | if ($g | length) != 1 then "guard entries: \($g | length)"
    elif $g[0].event != "PreToolUse" then "event \($g[0].event)"
    elif $g[0].matcher != "Bash" then "matcher \($g[0].matcher)"
    elif $g[0].type != "command" then "type \($g[0].type)"
    elif $g[0].command != "/usr/bin/env" then "command \($g[0].command)"
    elif $g[0].args != ["-u","BASH_ENV","-u","ENV","/bin/bash","--noprofile","--norc","-p","${CLAUDE_PLUGIN_ROOT}/hooks/destructive-command-guard.sh"] then "args \($g[0].args | tostring)"
    elif $g[0].timeout != 10 then "timeout \($g[0].timeout)"
    elif $g[0].onFailure != "block" then "onFailure \($g[0].onFailure)"
    elif ($all | length) != 1 then "hooks: \($all | length)"
    else "ok" end' "$HOOKS" 2>&1)"
if [ "$H1" = ok ]; then pass "H1 one PreToolUse Bash command hook: /usr/bin/env, the exact args, timeout 10, onFailure block"; else fail "H1 hooks.json: $H1"; fi

printf '%s\n' '== G1 inherited git repository selectors deny =='
TMPROOT="$(cd /tmp && pwd -P)"
G1_OTHER="$T/g1-other"
git_q init -q "$G1_OTHER" || exit 1
hook "$GUARD" "$TMPROOT" "$TMPROOT" "rm -rf $TMPROOT/unrelated-data" "GIT_DIR=$G1_OTHER/.git" "GIT_WORK_TREE=$TMPROOT"
expect_hook "G1 the round 2 probe: GIT_DIR names another repository, GIT_WORK_TREE the temp root" inherited_git_environment
expect_hint "G1" "a GIT_ variable that selects a repository is set in the session; unset it and retry"
for selector in GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR GIT_INDEX_FILE GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_NAMESPACE GIT_CEILING_DIRECTORIES GIT_DISCOVERY_ACROSS_FILESYSTEM; do
  hook "$GUARD" "$WT" "$WT" 'rm -rf build' "$selector=$T/g1-$selector"
  expect_hook "G1 $selector alone" inherited_git_environment
done
hook "$GUARD" "$WT" "$WT" 'ls' "GIT_DIR=$G1_OTHER/.git"
expect_hook "G1 ls with GIT_DIR set" silent

printf '%s\n' '== C1 and C2 inherited git configuration is cleared =='
# Every configuration variable (and GIT_CONFIG_NOSYSTEM) unset, then one set.
HOOK_UNSET=(-u GIT_CONFIG_PARAMETERS -u GIT_CONFIG_COUNT -u GIT_CONFIG -u GIT_CONFIG_GLOBAL -u GIT_CONFIG_SYSTEM -u GIT_CONFIG_NOSYSTEM -u GIT_CONFIG_KEY_0 -u GIT_CONFIG_VALUE_0)
C_WT="$T/c-worktree"
git_q init -q "$C_WT" || exit 1
mkdir -p "$C_WT/build"
C1_VALUE="'http.proxyAuthMethod=basic' 'credential.http://127.0.0.1:43210.helper='"
hook "$GUARD" "$C_WT" "$C_WT" 'rm -rf build' "GIT_CONFIG_PARAMETERS=$C1_VALUE"
expect_hook "C1 the sandbox's two-entry GIT_CONFIG_PARAMETERS: the adapter is silent" silent
policy "$GUARD" "$C_WT" 'rm -rf build' "GIT_CONFIG_PARAMETERS=$C1_VALUE"
expect_policy "C1 the sandbox's two-entry GIT_CONFIG_PARAMETERS: the engine accepts" no_opinion/accepted
BAD_CONFIG="$T/bad.gitconfig"
printf '[core]\n\tbare = notabool\n' >"$BAD_CONFIG"
policy "$GUARD" "$C_WT" 'rm -rf build' "GIT_CONFIG_PARAMETERS='core.bare=notabool'"
expect_policy "C2a GIT_CONFIG_PARAMETERS is cleared" no_opinion/accepted
policy "$GUARD" "$C_WT" 'rm -rf build' GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.bare GIT_CONFIG_VALUE_0=notabool
expect_policy "C2b GIT_CONFIG_COUNT with its key and value is cleared" no_opinion/accepted
policy "$GUARD" "$C_WT" 'rm -rf build' "GIT_CONFIG=$BAD_CONFIG"
expect_policy "C2c GIT_CONFIG (git reads it only in git config; cleared as defence)" no_opinion/accepted
policy "$GUARD" "$C_WT" 'rm -rf build' "GIT_CONFIG_GLOBAL=$BAD_CONFIG"
expect_policy "C2d GIT_CONFIG_GLOBAL is cleared" no_opinion/accepted
policy "$GUARD" "$C_WT" 'rm -rf build' "GIT_CONFIG_SYSTEM=$BAD_CONFIG"
expect_policy "C2e GIT_CONFIG_SYSTEM is cleared" no_opinion/accepted

HOOK_UNSET=()

printf '%s\n' '== D4 interpreter startup state changes no verdict =='
ALLOW_JSON='{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow"}}'
printf 'printf '"'"'%%s\\n'"'"' '"'"'%s'"'"'\n' "$ALLOW_JSON" >"$T/bash-env.sh"
run_argv "$GUARD" "$WT" 'rm -rf build' "BASH_ENV=$T/bash-env.sh" "ENV=$T/bash-env.sh"
if [ "$RC" -eq 0 ] && [ -z "$OUT" ]; then pass "D4 (a) BASH_ENV printing an allow: an accepted command stays silent"; else fail "D4 (a) BASH_ENV, accepted command: exit $RC and: $OUT"; fi
run_argv "$GUARD" "$WT" 'rm -rf /' "BASH_ENV=$T/bash-env.sh" "ENV=$T/bash-env.sh"
if [ "$RC" -eq 0 ] && [ "$OUT" = "$RM_DENY_LINE" ]; then pass "D4 (a) BASH_ENV printing an allow: a denied command prints exactly its deny line"; else fail "D4 (a) BASH_ENV, denied command: exit $RC and: $OUT"; fi
FUNC_BODY="() { builtin printf '%s\\n' '$ALLOW_JSON'; }"
run_argv "$GUARD" "$WT" 'rm -rf /' "BASH_FUNC_printf%%=$FUNC_BODY" "BASH_FUNC_printf()=$FUNC_BODY"
if [ "$RC" -eq 0 ] && [ "$OUT" = "$RM_DENY_LINE" ]; then pass "D4 (b) an exported printf changes nothing in the adapter"; else fail "D4 (b) adapter: exit $RC and: $OUT"; fi
run_argv "$GUARD" "$WT" 'rm -rf build' "BASH_FUNC_printf%%=$FUNC_BODY" "BASH_FUNC_printf()=$FUNC_BODY"
if [ "$RC" -eq 0 ] && [ -z "$OUT" ]; then pass "D4 (b) an exported printf changes nothing in the engine the adapter starts"; else fail "D4 (b) engine: exit $RC and: $OUT"; fi
mkdir -p "$T/perl-lib"
cat >"$T/perl-lib/Forge.pm" <<'PERL'
package Forge;
print qq({"decision":"no_opinion","reason":"accepted"}\n);
print qq(__GUARD_RUNNER__ status=0 timed_out=0 stderr_lines=0 stderr_other=0 nul=0\n);
exit 0;
1;
PERL
run_argv "$GUARD" "$WT" "rm -rf $T/outside-target" "PERL5OPT=-MForge" "PERL5LIB=$T/perl-lib"
if [ "$RC" -eq 0 ] && [ "$OUT" = "$RM_DENY_LINE" ]; then pass "D4 (c) a Perl module forging the runner's frame changes nothing: unsafe_rm"; else fail "D4 (c) PERL5OPT: exit $RC and: $OUT"; fi
D4_HOME="$T/jq-home"
mkdir -p "$D4_HOME"
printf 'def test($re): true;\n' >"$D4_HOME/.jq"
OUT="$(cd "$WT" && payload "$WT" 'rm -rf build' | /usr/bin/env "${BASE_UNSET[@]}" HOME="$D4_HOME" /bin/bash --noprofile --norc -p "$GUARD" 2>/dev/null)"
RC=$?
if [ "$RC" -eq 0 ] && [ -z "$OUT" ]; then pass "D4 (d) a \$HOME/.jq redefining test/1 changes nothing"; else fail "D4 (d) \$HOME/.jq: exit $RC and: $OUT"; fi
if HOME="$D4_HOME" "$JQ" -n '"a" | test("b")' 2>/dev/null | grep -qx true; then
  pass "D4 (d) fixture: the .jq file does redefine test/1 for a jq run with that HOME"
else
  fail "D4 (d) fixture: this jq does not load \$HOME/.jq, so the row proves nothing"
fi

printf '%s\n' '== D-S1 every program the guard runs is in a tier list or exempt =='
DS1="$(python3 -I - "$GUARD" <<'PY'
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
code = [l for l in text.split("\n") if not l.lstrip().startswith("#")]
body = "\n".join(code)
def array(name):
    m = re.search(r"^%s=\(([^)]*)\)$" % name, body, re.M)
    return m.group(1).split() if m else None
problems = []
tiers = {}
for name in ("ADAPTER_TOOLS", "ENGINE_TOOLS", "CONTEXT_TOOLS", "WORKTREE_TOOLS", "ARMED_TOOLS"):
    tiers[name] = array(name)
    if tiers[name] is None:
        problems.append("no %s line" % name)
listed_paths, listed_names = set(), set()
for entries in tiers.values():
    for entry in entries or []:
        if ":" in entry:
            listed_paths.add(entry.split(":", 1)[1])
        else:
            listed_names.add(entry)
stat = set(array("STAT_CANDIDATES") or [])
# The exemption list, each with its reason; nothing else is exempt.
EXEMPT = {
    "/bin/date": "the audit timestamp only, with a literal fallback; its decision use is the armed tier's date",
    "/usr/bin/perl": "the guard log only (armed, fails open) and the runner default the adapter tier checks",
    "/bin/bash": "the interpreter the hook command itself runs, and the engine start",
}
for number, line in enumerate(code, 1):
    for m in re.finditer(r"(?<![A-Za-z0-9_./~-])/(?:usr/)?s?bin/[A-Za-z0-9_.-]+", line):
        path = m.group(0)
        if path in listed_paths or path in stat or path in EXEMPT:
            continue
        problems.append("unlisted program %s: %s" % (path, line.strip()[:120]))
resolved = set(re.findall(r"^\s*(jq|wc|tr|git|grep)\) [A-Z]+_BIN=\"\$RESOLVED_TOOL\" ;;", body, re.M))
if resolved != {"jq", "wc", "tr", "git", "grep"}:
    problems.append("check_tools resolves %s, not jq, wc, tr, git and grep" % sorted(resolved))
for name in sorted(resolved - listed_names):
    problems.append("%s is resolved but no tier lists it" % name)
for name in sorted(listed_names - resolved):
    problems.append("%s is listed but never resolved" % name)
print("\n".join(problems) if problems else "ok")
PY
)"
if [ "$DS1" = ok ]; then pass "D-S1 every absolute program and named tool the guard runs is in a tier list or exempt"; else fail "D-S1 $DS1"; fi

printf '%s\n' '== U1 USERS_ROOT cannot be moved from the environment =='
USERS_ROOT_SPELLING=/Users
hook "$GUARD" "$WT" "$WT" "rm -rf $USERS_ROOT_SPELLING/example" "USERS_ROOT=$T/accounts"
expect_hook "U1 the account root is still refused by spelling" unsafe_rm
U1_PARENT="$T/u1"
git_q init -q "$U1_PARENT/repo" || exit 1
mkdir -p "$U1_PARENT/repo/build"
U1_REPO="$(cd "$U1_PARENT/repo" && pwd -P)"
hook "$GUARD" "$U1_REPO" "$U1_REPO" "rm -rf $U1_REPO/build" "USERS_ROOT=$(cd "$U1_PARENT" && pwd -P)"
expect_hook "U1 a USERS_ROOT in the environment does not refuse an absolute worktree path" silent

if [ "$FAILURES" -eq 0 ]; then
  printf 'ALL DEPENDENCY AND STARTUP TESTS PASSED\n'
  exit 0
fi
printf '%s TEST(S) FAILED\n' "$FAILURES"
exit 1
