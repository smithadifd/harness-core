#!/bin/bash
# The git guard fails closed, by name (design § 6, § 7.1, § 12.4). Every case
# runs the guard as the hook runs it, or a copy of it whose one constant is
# rewritten (the guard reads no variable, option or file for its trusted
# roots, its tier lists or its budget):
#   D1   each program of each tier, missing, denies with its own code; a push
#        that needs no repository read never starts git.
#   P    jq, wc, tr and git planted at the front of PATH, and a ./jq in the
#        starting folder, never run.
#   J0   the shipped roots line and hint.
#   D2   the crash trap; D3 the signal traps, SIGKILL and a missing guard
#        file under the hook argv; H1 the hook entry.
#   D4   interpreter startup state changes no verdict.
#   D5   engine stand-ins: stderr beside silence, stderr beside an ask, and
#        the runner's budget.
#   E    git's environment: inherited selectors, a scrubbed read, a planted
#        settings file, the removed scope grant and the interactive signal.
# Command strings are inert JSON data and are never executed.
set -u
LC_ALL=C
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUITE=deps
. "$HERE/fixtures/git-guard/harness.sh"
MISSING="$T/missing"
mkdir -p "$MISSING" "$T/copies" "$T/roots"

NAMED_TOOLS="jq wc tr git"
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
# roots_copy DEST DIR...: a copy of the guard whose TRUSTED_ROOTS line names DIR...
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
expect_hint() { # LABEL TEXT
  case "$OUT" in *"$2"*) pass "$1: the hint names it" ;; *) fail "$1: the hint does not hold '$2': ${OUT:0:240}" ;; esac
}
ROOTS_TEXT="/usr/bin, /bin, /usr/local/bin, /opt/homebrew/bin or /home""/linuxbrew/.linuxbrew/bin"
T1='git push'
M1='git push origin main'

printf '%s\n' '== D1 adapter tier =='
EMPTY_ROOT="$(root_dir empty)"
roots_copy "$T/copies/no-roots.sh" "$EMPTY_ROOT"
GG_GUARD="$T/copies/no-roots.sh"
gg_hook "$REPO_FEATURE" "$M1"
gg_expect "D1 adapter jq: no trusted root holds jq" deny:dependency_missing_jq
expect_hint "D1 adapter jq" "jq was not found in $ROOTS_TEXT; install jq in one of them"
GG_GUARD=""
gg_hook_args "$REPO_FEATURE" "$M1" --push-to-main ask --force-push ask --commit-style off --timeout-runner "$MISSING/perl"
gg_expect "D1 adapter perl: the runner is missing" deny:dependency_missing_perl
expect_hint "D1 adapter perl" "/usr/bin/perl is missing"
gg_hook_args "$REPO_FEATURE" "$M1" --push-to-main ask --force-push ask --commit-style off --adapter-policy "$MISSING/engine.sh"
gg_expect "D1 adapter engine: the policy file is missing" deny:dependency_missing_engine
expect_hint "D1 adapter engine" "the guard's own policy file is missing; reinstall the rails plugin"

printf '%s\n' '== D1 engine tier =='
roots_copy "$T/copies/no-jq.sh" "$(root_dir no-jq wc tr git)"
GG_GUARD="$T/copies/no-jq.sh"
gg_policy "$REPO_FEATURE" "$M1" --push-to-main ask --force-push ask --commit-style off
if [ "$OUT" = '{"decision":"deny","reason":"dependency_missing_jq"}' ]; then pass "D1 engine jq: --policy with no jq in any root"; else fail "D1 engine jq: $OUT"; fi
roots_copy "$T/copies/no-wc.sh" "$(root_dir no-wc jq tr git)"
GG_GUARD="$T/copies/no-wc.sh"
gg_hook "$REPO_FEATURE" "$M1"
gg_expect "D1 engine wc" deny:dependency_missing_wc
expect_hint "D1 engine wc" "wc was not found in $ROOTS_TEXT"
roots_copy "$T/copies/no-tr.sh" "$(root_dir no-tr jq wc git)"
GG_GUARD="$T/copies/no-tr.sh"
gg_hook "$REPO_FEATURE" "$M1"
gg_expect "D1 engine tr" deny:dependency_missing_tr
expect_hint "D1 engine tr" "tr was not found in $ROOTS_TEXT"

printf '%s\n' '== D1 target tier: only a push that needs the current branch needs git =='
roots_copy "$T/copies/no-git.sh" "$(root_dir no-git jq wc tr)"
GG_GUARD="$T/copies/no-git.sh"
gg_hook "$REPO_FEATURE" "$T1"
gg_expect "D1-git-b a bare push with no git in any root" deny:dependency_missing_git
expect_hint "D1-git-b" "git was not found in $ROOTS_TEXT; install git in one of them"
gg_hook "$REPO_FEATURE" "$M1"
gg_expect "D1-git-a a named push to main needs no read: it still asks" ask:push_main_branch
gg_hook "$REPO_FEATURE" 'git push -f origin feature'
gg_expect "D1-git-a a force-push needs no read" ask:push_force
replace_copy "$T/copies/no-env.sh" 'TARGET_TOOLS=(git env:/usr/bin/env)' "TARGET_TOOLS=(git env:$MISSING/env)"
GG_GUARD="$T/copies/no-env.sh"
gg_hook "$REPO_FEATURE" "$T1"
gg_expect "D1 target env: /usr/bin/env missing with a bare push" deny:dependency_missing_env
expect_hint "D1 target env" "/usr/bin/env is missing"
gg_hook "$REPO_FEATURE" "$M1"
gg_expect "D1 target env: a named push needs no read" ask:push_main_branch
GG_GUARD=""

printf '%s\n' '== P: programs planted at the front of PATH never run =='
PLANT="$T/plant/bin"
MARKS="$T/plant/marks"
mkdir -p "$PLANT" "$MARKS"
for tool in $NAMED_TOOLS; do
  printf '#!/bin/bash\n: >%s\nexit 0\n' "$MARKS/$tool" >"$PLANT/$tool"
  chmod 755 "$PLANT/$tool"
done
gg_hook "$REPO_FEATURE" "$T1" "PATH=$PLANT:$GG_PATH"
gg_expect "P a bare push (jq, wc, tr and git all run) keeps its verdict" silent
gg_hook "$REPO_MAIN" "$T1" "PATH=$PLANT:$GG_PATH"
gg_expect "P a bare push from main keeps its verdict" ask:push_main_branch
for tool in $NAMED_TOOLS; do
  if [ ! -e "$MARKS/$tool" ]; then pass "P planted $tool on PATH never ran"; else fail "P planted $tool on PATH ran"; fi
done
DOTDIR="$T/dotjq"
mkdir -p "$DOTDIR"
printf '#!/bin/bash\n: >%s\nexit 0\n' "$MARKS/dot-jq" >"$DOTDIR/jq"
chmod 755 "$DOTDIR/jq"
OUT="$(cd "$DOTDIR" && gg_payload "$REPO_MAIN" "$T1" | /usr/bin/env -i HOME="$GG_HOME" PATH=":.:$GG_PATH" \
  /usr/bin/env -u BASH_ENV -u ENV /bin/bash --noprofile --norc -p "$GUARD" --push-to-main ask --force-push ask --commit-style off 2>/dev/null)"
RC=$?
gg_classify
gg_expect "P a ./jq in the hook's starting folder: the trusted root's jq decides" ask:push_main_branch
if [ ! -e "$MARKS/dot-jq" ]; then pass "P the planted ./jq never ran"; else fail "P the planted ./jq ran"; fi

printf '%s\n' '== J0: the shipped roots, hint and resolver =='
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
if ! grep -v '^[[:space:]]*#' "$GUARD" | grep -qE 'command -v|type -P|hash -t|(^|[;&|({`]|\$\()[[:space:]]*which[[:space:]]'; then
  pass "J0 no PATH lookup anywhere in the guard"
else
  fail "J0 the guard has a PATH lookup"
fi

printf '%s\n' '== D2 the crash trap =='
FAIL_LINE='{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Git guard denied the command (reason=adapter_failed): the guard'"'"'s adapter failed; retry once and report the code if it repeats."}}'
INT_LINE='{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Git guard denied the command (reason=adapter_interrupted): the guard'"'"'s adapter was stopped by a signal; retry once."}}'
replace_copy "$T/copies/d2-exit.sh" '  GUARD_LOG_INPUT="$normalized"' '  GUARD_LOG_INPUT="$normalized"
  exit 7'
GG_GUARD="$T/copies/d2-exit.sh"
gg_hook "$REPO_FEATURE" "$M1"
if [ "$RC" -eq 0 ] && [ "$OUT" = "$FAIL_LINE" ]; then pass "D2 an exit 7 after normalising: one adapter_failed line, exit 0"; else fail "D2 exit 7: exit $RC and: $OUT"; fi
replace_copy "$T/copies/d2-unbound.sh" '  GUARD_LOG_INPUT="$normalized"' '  GUARD_LOG_INPUT="$normalized"
  : "$D2_UNBOUND_VARIABLE"'
GG_GUARD="$T/copies/d2-unbound.sh"
gg_hook "$REPO_FEATURE" "$M1"
if [ "$RC" -eq 0 ] && [ "$OUT" = "$FAIL_LINE" ]; then pass "D2 a set -u fault: one adapter_failed line, exit 0"; else fail "D2 set -u: exit $RC and: $OUT"; fi
replace_copy "$T/copies/d2-between.sh" '  if [ "$decision" = ask ]; then
    printf' '  if [ "$decision" = ask ]; then
    kill -TERM $$
    printf'
GG_GUARD="$T/copies/d2-between.sh"
gg_hook "$REPO_FEATURE" "$M1"
if [ "$RC" -ne 0 ] && [ -z "$OUT" ]; then pass "D2 a signal between the two flags: exit $RC (a block under onFailure), no output"; else fail "D2 between flags: exit $RC and: $OUT"; fi
GG_GUARD=""

printf '%s\n' '== D3 the signal traps =='
for sig in HUP INT QUIT ILL TRAP ABRT BUS FPE USR1 SEGV USR2 PIPE ALRM TERM XCPU XFSZ VTALRM PROF SYS; do
  if ! kill -l "$sig" >/dev/null 2>&1; then
    fail "D3 $sig is not defined on this platform, so its trap cannot be tested here"
    continue
  fi
  replace_copy "$T/copies/d3-$sig.sh" '  GUARD_LOG_INPUT="$normalized"' "  GUARD_LOG_INPUT=\"\$normalized\"
  kill -$sig \$\$"
  GG_GUARD="$T/copies/d3-$sig.sh"
  gg_hook "$REPO_FEATURE" "$M1"
  if [ "$RC" -eq 0 ] && [ "$OUT" = "$INT_LINE" ]; then
    pass "D3-$sig before the verdict: one adapter_interrupted line, exit 0"
  else
    fail "D3-$sig expected one adapter_interrupted line and exit 0, got exit $RC and: $OUT"
  fi
done
GG_GUARD=""
# The hook argv of hooks.json's first git guard entry (the floor, which
# references no option), with the plugin root substituted, and the guard path
# replaceable.
GIT_ENTRY_ARGS="$("$JQ" -c --arg root "$PLUGIN_ROOT" '[.hooks.PreToolUse[].hooks[] | select(.type == "command") | select((.args // []) | any(endswith("/hooks/git-guard.sh")))] | .[0] | [.command] + (.args | map(gsub("\\$\\{CLAUDE_PLUGIN_ROOT\\}"; $root) | gsub("\\$\\{user_config\\.[a-z_]+\\}"; "")))' "$HOOKS")" || { fail "D3 fixture: no git guard entry"; exit 1; }
run_argv() { # GUARD_FILE CWD COMMAND: the hook argv; sets OUT, RC
  local guard="$1" cwd="$2" command_data="$3" word
  local -a argv=()
  while IFS= read -r word; do
    case "$word" in */hooks/git-guard.sh) word="$guard" ;; esac
    argv+=("$word")
  done < <("$JQ" -r '.[]' <<<"$GIT_ENTRY_ARGS")
  OUT="$(cd "$T" && gg_payload "$cwd" "$command_data" | /usr/bin/env -i HOME="$GG_HOME" PATH="$GG_PATH" "${argv[@]}" 2>/dev/null)"
  RC=$?
}
run_argv "$GUARD" "$REPO_FEATURE" "$M1"
gg_classify
gg_expect "D3 control: the floor's hook argv asks" ask:push_main_branch
replace_copy "$T/copies/d3-kill.sh" '  GUARD_LOG_INPUT="$normalized"' '  GUARD_LOG_INPUT="$normalized"
  kill -KILL $$'
run_argv "$T/copies/d3-kill.sh" "$REPO_FEATURE" "$M1"
if [ "$RC" -ne 0 ] && [ -z "$OUT" ]; then pass "D3-KILL: exit $RC, no output (a block under onFailure)"; else fail "D3-KILL: exit $RC and: $OUT"; fi
run_argv "$T/copies/not-there.sh" "$REPO_FEATURE" "$M1"
if [ "$RC" -ne 0 ] && [ -z "$OUT" ]; then pass "D3 a missing guard file: exit $RC, no output (a block under onFailure)"; else fail "D3 missing guard file: exit $RC and: $OUT"; fi

printf '%s\n' '== H1 the hook entry =='
H1="$("$JQ" -r '
  [.hooks | to_entries[] | .key as $event | .value[] | .matcher as $m | .hooks[] | {event: $event, matcher: $m} + .] as $all
  | ($all | map(select((.args // []) | map(tostring) | any(endswith("/hooks/git-guard.sh"))))) as $g
  | if ($g | length) != 2 then "guard entries: \($g | length)"
    elif ($g | map(.event) | unique) != ["PreToolUse"] then "event \($g | map(.event))"
    elif ($g | map(.matcher) | unique) != ["Bash"] then "matcher \($g | map(.matcher))"
    elif ($g | map(.type) | unique) != ["command"] then "type \($g | map(.type))"
    elif ($g | map(.command) | unique) != ["/usr/bin/env"] then "command \($g | map(.command))"
    elif ($g | map(.timeout) | unique) != [10] then "timeout \($g | map(.timeout))"
    elif $g[0].args != ["-u","BASH_ENV","-u","ENV","/bin/bash","--noprofile","--norc","-p","${CLAUDE_PLUGIN_ROOT}/hooks/git-guard.sh","--push-to-main","ask","--force-push","ask","--commit-style","off"] then "floor args \($g[0].args | tostring)"
    elif $g[0].onFailure != "block" then "floor onFailure \($g[0].onFailure)"
    elif $g[0].statusMessage != "Checking git guardrails..." then "floor statusMessage \($g[0].statusMessage)"
    elif $g[1].args != ["-u","BASH_ENV","-u","ENV","/bin/bash","--noprofile","--norc","-p","${CLAUDE_PLUGIN_ROOT}/hooks/git-guard.sh","--tighten-only","--push-to-main","${user_config.push_to_main}","--force-push","${user_config.force_push}","--commit-style","${user_config.commit_style}"] then "stored-options args \($g[1].args | tostring)"
    elif ($g[1] | has("onFailure")) then "stored-options onFailure \($g[1].onFailure)"
    elif $g[1].statusMessage != "Checking stored git guard options..." then "stored-options statusMessage \($g[1].statusMessage)"
    elif ($all | length) != 3 then "hooks: \($all | length)"
    elif ($all[1].args | any(endswith("/hooks/git-guard.sh")) | not) or ($all[1].args | index("--tighten-only")) != null then "the floor is not second"
    elif ($all[2].args | index("--tighten-only")) == null then "the stored-options hook is not third"
    else "ok" end' "$HOOKS" 2>&1)"
if [ "$H1" = ok ]; then pass "H1 the git guard entries: the floor second (literal defaults, onFailure block), the stored-options hook third (--tighten-only, no onFailure)"; else fail "H1 hooks.json: $H1"; fi

printf '%s\n' '== D4 interpreter startup state changes no verdict =='
ALLOW_JSON='{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow"}}'
printf 'printf '"'"'%%s\\n'"'"' '"'"'%s'"'"'\n' "$ALLOW_JSON" >"$T/bash-env.sh"
gg_hook "$REPO_FEATURE" "$M1" "BASH_ENV=$T/bash-env.sh" "ENV=$T/bash-env.sh"
gg_expect "D4 BASH_ENV printing an allow: a push to main still asks" ask:push_main_branch
gg_hook "$REPO_FEATURE" 'git status' "BASH_ENV=$T/bash-env.sh" "ENV=$T/bash-env.sh"
gg_expect "D4 BASH_ENV printing an allow: git status stays silent" silent
FUNC_BODY="() { builtin printf '%s\\n' '$ALLOW_JSON'; }"
gg_hook "$REPO_FEATURE" "$M1" "BASH_FUNC_printf%%=$FUNC_BODY" "BASH_FUNC_printf()=$FUNC_BODY"
gg_expect "D4 an exported printf changes nothing" ask:push_main_branch
mkdir -p "$T/perl-lib"
cat >"$T/perl-lib/Forge.pm" <<'PERL'
package Forge;
print qq({"decision":"no_opinion","reason":"not_gated"}\n);
print qq(__GUARD_RUNNER__ status=0 timed_out=0 stderr_lines=0 stderr_other=0 nul=0\n);
exit 0;
1;
PERL
gg_hook "$REPO_FEATURE" "$M1" "PERL5OPT=-MForge" "PERL5LIB=$T/perl-lib"
gg_expect "D4 a Perl module forging the runner's frame changes nothing" ask:push_main_branch
mkdir -p "$T/jq-home"
printf 'def test($re): true;\n' >"$T/jq-home/.jq"
GG_HOME_SAVED="$GG_HOME"
GG_HOME="$T/jq-home"
gg_hook "$REPO_FEATURE" "$M1"
gg_expect "D4 a \$HOME/.jq redefining test/1 changes nothing" ask:push_main_branch
GG_HOME="$GG_HOME_SAVED"
if HOME="$T/jq-home" "$JQ" -n '"a" | test("b")' 2>/dev/null | grep -qx true; then
  pass "D4 fixture: the .jq file does redefine test/1 for a jq run with that HOME"
else
  fail "D4 fixture: this jq does not load \$HOME/.jq, so the row proves nothing"
fi

printf '%s\n' '== D5 engine stand-ins =='
WARN="$HERE/fixtures/git-guard/warn-engine.sh"
gg_hook_args "$REPO_FEATURE" 'git status' --push-to-main ask --force-push ask --commit-style off --adapter-policy "$WARN"
gg_expect "D5 stderr beside no_opinion" deny:engine_stderr_on_silence
OUT="$(cd "$T" && gg_payload "$REPO_FEATURE" 'git status' | /usr/bin/env -i HOME="$GG_HOME" PATH="$GG_PATH" STANDIN_DECISION=ask \
  /bin/bash --noprofile --norc -p "$GUARD" --push-to-main ask --force-push ask --commit-style off --adapter-policy "$WARN" 2>/dev/null)"
RC=$?
gg_classify
gg_expect "D5 stderr beside an ask: the ask stands" ask:push_force
replace_copy "$T/copies/d5-budget.sh" 'TIMEOUT_SECONDS=8' 'TIMEOUT_SECONDS=1'
OUT="$(cd "$T" && gg_payload "$REPO_FEATURE" 'git status' | /usr/bin/env -i HOME="$GG_HOME" PATH="$GG_PATH" STANDIN_DECISION=ask STANDIN_SLEEP=3 \
  /bin/bash --noprofile --norc -p "$T/copies/d5-budget.sh" --push-to-main ask --force-push ask --commit-style off --adapter-policy "$WARN" 2>/dev/null)"
RC=$?
gg_classify
gg_expect "D5 an engine past a 1-second budget" deny:engine_timeout
STANDIN_EXIT="$T/exit-engine.sh"
printf '#!/bin/bash\nexit 4\n' >"$STANDIN_EXIT"
gg_hook_args "$REPO_FEATURE" 'git status' --push-to-main ask --force-push ask --commit-style off --adapter-policy "$STANDIN_EXIT"
gg_expect "D5 an engine that exits non-zero" deny:engine_failed

printf '%s\n' '== D6 malformed input =='
OUT="$(cd "$T" && "$JQ" -cn --arg cwd "$REPO_FEATURE" '{hook_event_name:"PreToolUse",tool_name:"Read",tool_input:{command:"git push origin main"},cwd:$cwd}' \
  | /usr/bin/env -i HOME="$GG_HOME" PATH="$GG_PATH" /bin/bash --noprofile --norc -p "$GUARD" --push-to-main ask --force-push ask --commit-style off 2>/dev/null)"
RC=$?
gg_classify
gg_expect "D6 a hook input that is not a Bash event" deny:adapter_input_invalid
OUT="$(cd "$T" && "$JQ" -cn '{hook_event_name:"PreToolUse",tool_name:"Bash",tool_input:{command:"git push origin main"},cwd:"relative"}' \
  | /usr/bin/env -i HOME="$GG_HOME" PATH="$GG_PATH" /bin/bash --noprofile --norc -p "$GUARD" --push-to-main ask --force-push ask --commit-style off 2>/dev/null)"
RC=$?
gg_classify
gg_expect "D6 a relative cwd" deny:adapter_input_invalid
OUT="$(cd "$T" && printf '%s' '{"cwd":"/tmp"}' | /usr/bin/env -i HOME="$GG_HOME" PATH="$GG_PATH" \
  /bin/bash --noprofile --norc -p "$GUARD" --policy --push-to-main ask --force-push ask --commit-style off 2>/dev/null)"
if [ "$OUT" = '{"decision":"deny","reason":"malformed_normalized_input"}' ]; then pass "D6 the engine refuses input with no command"; else fail "D6 the engine: $OUT"; fi
OUT="$(cd "$T" && "$JQ" -cn --arg cwd "$REPO_FEATURE" '{cwd:$cwd,command:"git push origin main",extra:1}' | /usr/bin/env -i HOME="$GG_HOME" PATH="$GG_PATH" \
  /bin/bash --noprofile --norc -p "$GUARD" --policy --push-to-main ask --force-push ask --commit-style off 2>/dev/null)"
if [ "$OUT" = '{"decision":"deny","reason":"malformed_normalized_input"}' ]; then pass "D6 the engine refuses input with a key beyond command and cwd"; else fail "D6 the engine took an extra key: $OUT"; fi
# A value of newlines only passes both strict filters and is empty once the
# command substitution strips it: the engine's empty check denies by name.
gg_row "D6 a command of one newline" deny:extraction_failure $'\n'
gg_policy "$REPO_FEATURE" $'\n' --push-to-main ask --force-push ask --commit-style off
if [ "$OUT" = '{"decision":"deny","reason":"extraction_failure"}' ]; then pass "D6 the engine: a command of one newline"; else fail "D6 the engine, a command of one newline: $OUT"; fi
gg_policy $'\n' 'git status' --push-to-main ask --force-push ask --commit-style off
if [ "$OUT" = '{"decision":"deny","reason":"extraction_failure"}' ]; then pass "D6 the engine: a cwd of one newline"; else fail "D6 the engine, a cwd of one newline: $OUT"; fi

printf '%s\n' "== E git's environment =="
for name in GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR GIT_INDEX_FILE GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_NAMESPACE GIT_CEILING_DIRECTORIES GIT_DISCOVERY_ACROSS_FILESYSTEM GIT_CONFIG GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM GIT_CONFIG_NOSYSTEM GIT_CONFIG_PARAMETERS GIT_CONFIG_COUNT GIT_CONFIG_KEY_0 GIT_CONFIG_VALUE_0; do
  gg_row "E-G1 $name in the hook environment, a feature push" ask:inherited_git_environment 'git push origin feature' "$name=$T/x"
  gg_row "E-G2 $name in the hook environment, git status" silent 'git status' "$name=$T/x"
done
gg_hook "$REPO_FEATURE" "$T1" "GIT_TRACE=$T/trace" "GIT_TRACE2=$T/trace2" "GIT_TRACE_PACKET=$T/trace3"
gg_expect "E-SCRUB a bare push with GIT_TRACE in the hook environment" silent
if [ ! -e "$T/trace" ] && [ ! -e "$T/trace2" ] && [ ! -e "$T/trace3" ]; then pass "E-SCRUB no trace file was written"; else fail "E-SCRUB a trace file was written"; fi
gg_hook "$REPO_MAIN" "$T1" "GIT_TRACE=$T/trace"
gg_expect "E-SCRUB a bare push from main with GIT_TRACE set" ask:push_main_branch
mkdir -p "$GG_HOME/.claude/hooks"
printf 'PUSH_MAIN=allow\nFORCE_PUSH=allow\nCOMMIT_STYLE=off\n' >"$GG_HOME/.claude/hooks/git-guard.conf"
gg_row "E-CONF a planted git-guard.conf: the guard reads no file" ask:push_main_branch "$M1"
gg_row "E-CONF a planted git-guard.conf, force" ask:push_force 'git push -f origin feature'
gg_row "E-S1 the removed scope grant" ask:push_main_branch "$M1" GIT_GUARD_ALLOW_PUSH=1 GIT_GUARD_ALLOW_PUSH_REPOS=example.invalid/acme/widget
gg_row "E-TP an interactive terminal signal" ask:push_main_branch "$M1" TERM_PROGRAM=x

gg_finish DEPENDENCY
