#!/bin/bash
# Focused fixture suite. Every command under review is inert JSON data;
# none is evaluated, sourced, or sent to a shell for execution.
# shellcheck disable=SC2016
set -u
LC_ALL=C

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUARD="$HERE/../hooks/destructive-command-guard.sh"
FIXTURES="$HERE/fixtures/destructive-command-guard"
JQ="$(command -v jq)" || { printf 'FAIL: jq is not on PATH\n'; exit 1; }
TEST_ROOT="$(mktemp -d "$(cd /tmp && pwd -P)/destructive-guard.XXXXXX")" || exit 1
WORKTREE="$TEST_ROOT/worktree"
SCRATCHPAD="$TEST_ROOT/session-scratchpad"
OUTSIDE="$TEST_ROOT/outside"
DISPOSABLE="$TEST_ROOT/disposable-worktree"
LOG="$TEST_ROOT/denials.log"
# The macOS account root the guard refuses by spelling.
USERS_ROOT=/Users

mkdir -p "$WORKTREE/build/cache" "$SCRATCHPAD/cache" "$OUTSIDE"
git init -q "$WORKTREE"
git -C "$WORKTREE" config user.name "Destructive Guard Fixture"
git -C "$WORKTREE" config user.email "destructive-guard@example.invalid"
printf '%s\n' harmless >"$WORKTREE/fixture.txt"
git -C "$WORKTREE" add fixture.txt
git -C "$WORKTREE" commit -qm "test: seed inert fixture"
git -C "$WORKTREE" worktree add -q --detach "$DISPOSABLE" HEAD
ln -s "$OUTSIDE" "$WORKTREE/build/escape"
ln -s "$OUTSIDE" "$SCRATCHPAD/escape"

# The registry / authorization rules below are exercised on worktrees that live
# under the temp roots, which the guard would otherwise accept as throwaway
# trees. Run this suite against a copy with the temp roots emptied so those
# rules stay observable; the temp-root rule has its own suite.
sed 's|^TEMP_ROOTS=.*|TEMP_ROOTS=()|' "$GUARD" >"$TEST_ROOT/guard-no-temp-roots.sh" || exit 1
grep -q '^TEMP_ROOTS=()$' "$TEST_ROOT/guard-no-temp-roots.sh" || { printf 'FAIL: guard has no TEMP_ROOTS constant to rewrite\n'; exit 1; }
GUARD="$TEST_ROOT/guard-no-temp-roots.sh"
# Budget expiries run on a further copy whose alarm is 1 s, so the suite does
# not wait out the real 8 s budget for each one.
sed 's/^TIMEOUT_SECONDS=[0-9]*$/TIMEOUT_SECONDS=1/' "$GUARD" >"$TEST_ROOT/guard-fast-alarm.sh" || exit 1
grep -q '^TIMEOUT_SECONDS=1$' "$TEST_ROOT/guard-fast-alarm.sh" || { printf 'FAIL: guard has no TIMEOUT_SECONDS constant to rewrite\n'; exit 1; }
FAST_GUARD="$TEST_ROOT/guard-fast-alarm.sh"
# The warn wrapper prints one stderr line and then runs the engine under test.
WARN="$FIXTURES/warn-then-exec.sh"
WARN_TEXT='bash: fork: retry: Resource temporarily unavailable'

# run_json invokes ADAPTER with ADAPTER_ENV added to its environment, and
# records the call in LAST_RC, LAST_OUTPUT, LAST_TEXT (permissionDecisionReason),
# LAST_REASON (the reason code the text names) and LAST_AUDIT (its stderr).
ADAPTER="$GUARD"
ADAPTER_ENV=()
CALL_ERR="$TEST_ROOT/call.stderr"
LAST_RC=0
LAST_OUTPUT=""
LAST_TEXT=""
LAST_REASON=""
LAST_AUDIT=""
TEXT_PREFIX='Destructive command guard denied the command (reason='
DENY_RE='^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z event=destructive-command-deny reason=[a-z_]+$'
HINT_UNSAFE_RM='an rm target is not a literal path strictly below the current worktree or the named session scratchpad; spell a worktree target relative to the current folder, because an absolute home path is refused by spelling'
HINT_ENGINE_TIMEOUT='the guard did not finish within 8 seconds; retry the same command once and keep commands under about 4 KB'
HINT_DEFAULT='see the reason code'
NO_BYPASS='This deny is final: never use or offer a ! command at the prompt, a sandbox override, or a settings, permission or allowlist change to get around it; tell the person the guard denied it, and that running the command themselves is their own call.'

FAILURES=0
DENIALS=0
pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; FAILURES=$((FAILURES + 1)); }

payload() {
  "$JQ" -cn --arg cwd "$WORKTREE" --arg command "$1" \
    '{hook_event_name:"PreToolUse",tool_name:"Bash",tool_input:{command:$command},cwd:$cwd}'
}

decision() {
  printf '%s' "$1" | "$JQ" -er '.hookSpecificOutput.permissionDecision' 2>/dev/null
}

record_call() {
  LAST_OUTPUT="$1"
  LAST_AUDIT="$(cat "$CALL_ERR")"
  cat "$CALL_ERR" >>"$LOG"
  LAST_TEXT="$(printf '%s' "$1" | "$JQ" -er '.hookSpecificOutput.permissionDecisionReason' 2>/dev/null || :)"
  LAST_REASON=""
  case "$LAST_TEXT" in
    "$TEXT_PREFIX"*) LAST_REASON="${LAST_TEXT#"$TEXT_PREFIX"}"; LAST_REASON="${LAST_REASON%%)*}" ;;
  esac
}

run_json() {
  local label="$1" expected="$2" json_data="$3"
  shift 3
  local output got
  output="$(printf '%s' "$json_data" | /usr/bin/env DCG_WARN_ENGINE="$GUARD" DCG_WARN_TEXT="$WARN_TEXT" ${ADAPTER_ENV[@]+"${ADAPTER_ENV[@]}"} /bin/bash --noprofile --norc -p "$ADAPTER" --scratchpad "$SCRATCHPAD" --authorized-worktree "$DISPOSABLE" "$@" 2>"$CALL_ERR")"
  LAST_RC=$?
  record_call "$output"
  if [ -z "$output" ]; then got=none; else got="$(decision "$output" 2>/dev/null || printf invalid)"; fi
  if [ "$got" = "$expected" ]; then
    pass "$label -> $expected"
  else
    fail "$label: expected $expected, got $got"
  fi
  # N4: every adapter call is also a never-allow check. The adapter's only
  # decisions are a deny line and silence.
  case "$got" in
    deny|none) ;;
    *) fail "N4 $label: the adapter's decision is $got; it may only deny or stay silent" ;;
  esac
  [ "$expected" = "deny" ] && DENIALS=$((DENIALS + 1))
}

run_command() {
  local label="$1" expected="$2" command_data="$3"
  shift 3
  run_json "$label" "$expected" "$(payload "$command_data")" "$@"
}

# expect_reason ID LABEL CODE: the last adapter call's text names CODE, and the
# call wrote exactly one fixed-shape audit line naming the same code (C3).
expect_reason() {
  local id="$1" label="$2" code="$3" lines
  if [ "$LAST_REASON" = "$code" ]; then
    pass "$id $label: text names reason=$code"
  else
    fail "$id $label: expected reason=$code, got reason=${LAST_REASON:-none}"
  fi
  lines="$(printf '%s\n' "$LAST_AUDIT" | grep -c .)"
  if [ "$lines" -eq 1 ] && printf '%s\n' "$LAST_AUDIT" | grep -Eq "$DENY_RE" && [ "${LAST_AUDIT##* reason=}" = "$code" ]; then
    pass "C3 $id audit is one fixed-shape line naming reason=$code"
  else
    fail "C3 $id audit is not one fixed-shape line naming reason=$code ($lines lines)"
  fi
}

# expect_silent ID LABEL: the last adapter call exited 0 with no stdout, so
# Claude's own permission flow decides. Its audit is empty (C4).
expect_silent() {
  local id="$1" label="$2"
  if [ "$LAST_RC" -eq 0 ] && [ -z "$LAST_OUTPUT" ]; then
    pass "$id $label: silent (exit 0, no output)"
  else
    fail "$id $label: expected silence, got exit $LAST_RC and output ${LAST_OUTPUT:0:120}"
  fi
  if [ -z "$LAST_AUDIT" ]; then
    pass "C4 $id audit is empty"
  else
    fail "C4 $id audit is not empty"
  fi
}

# expect_text ID LABEL TEXT: the last adapter call's text is exactly TEXT (C1).
expect_text() {
  if [ "$LAST_TEXT" = "$3" ]; then
    pass "C1 $1 $2: text is the template plus the code's hint"
  else
    fail "C1 $1 $2: text differs: $LAST_TEXT"
  fi
}

# expect_no_input ID: neither the last call's stdout nor its audit carries an
# input marker, the stderr marker, or a fixture path (C2).
expect_no_input() {
  local needle leaked=""
  for needle in ZZMARKERZZ ZZSTDERRZZ "$OUTSIDE" "$WORKTREE" "$TEST_ROOT"; do
    case "$LAST_OUTPUT$LAST_AUDIT" in *"$needle"*) leaked="$leaked $needle" ;; esac
  done
  if [ -z "$leaked" ]; then
    pass "C2 $1 text and audit carry no input"
  else
    fail "C2 $1 text or audit carries input:$leaked"
  fi
}

run_policy_command() {
  local label="$1" expected_decision="$2" expected_reason="$3" command_data="$4"
  local input output got_decision got_reason
  input="$($JQ -cn --arg cwd "$WORKTREE" --arg command "$command_data" '{cwd:$cwd,command:$command}')"
  output="$(printf '%s' "$input" | /bin/bash --noprofile --norc -p "$GUARD" --policy \
    --scratchpad "$SCRATCHPAD" --authorized-worktree "$DISPOSABLE" 2>>"$LOG")"
  got_decision="$(printf '%s' "$output" | "$JQ" -er '.decision' 2>/dev/null || printf invalid)"
  got_reason="$(printf '%s' "$output" | "$JQ" -er '.reason' 2>/dev/null || printf invalid)"
  if [ "$got_decision" = "$expected_decision" ] && [ "$got_reason" = "$expected_reason" ]; then
    pass "$label -> $expected_decision/$expected_reason"
  else
    fail "$label: expected $expected_decision/$expected_reason, got $got_decision/$got_reason"
  fi
  [ "$expected_decision" = "deny" ] && DENIALS=$((DENIALS + 1))
}

run_without_authorizations() {
  local label="$1" expected="$2" command_data="$3" output got
  output="$(payload "$command_data" | /bin/bash --noprofile --norc -p "$GUARD" 2>>"$LOG")"
  if [ -z "$output" ]; then got=none; else got="$(decision "$output" 2>/dev/null || printf invalid)"; fi
  if [ "$got" = "$expected" ]; then pass "$label -> $expected"; else fail "$label: expected $expected, got $got"; fi
  [ "$expected" = "deny" ] && DENIALS=$((DENIALS + 1))
}

run_broad_scratchpad() {
  local output got
  output="$(payload 'printf safe' | /bin/bash --noprofile --norc -p "$GUARD" --scratchpad "$(cd /tmp && pwd -P)" 2>>"$LOG")"
  if [ -z "$output" ]; then got=none; else got="$(decision "$output" 2>/dev/null || printf invalid)"; fi
  if [ "$got" = none ]; then
    pass "invalid scratchpad is irrelevant to an ordinary command -> none"
  else
    fail "invalid scratchpad affected an ordinary command"
  fi
}

printf '%s\n' '== incident replay and mandatory positives =='
CALL1="$("$JQ" --arg cwd "$WORKTREE" '.cwd=$cwd' "$FIXTURES/incident-call-1.json")"
CALL2="$("$JQ" --arg cwd "$WORKTREE" '.cwd=$cwd' "$FIXTURES/incident-call-2.json")"
run_json "incident call 1 HOME export" none "$CALL1"
run_json "incident call 2 in a fresh invocation" deny "$CALL2"
run_command "literal build descendant" none 'rm -rf ./build'
run_command "two literal relative descendants" none 'rm -rf ./build ./cache'
run_command "explicitly authorized disposable worktree" none "git worktree remove $DISPOSABLE"
if [ -d "$WORKTREE/build" ]; then pass "rm fixture remained present"; else fail "rm fixture was executed"; fi
if [ -d "$DISPOSABLE" ]; then pass "worktree fixture remained present"; else fail "worktree fixture was executed"; fi

printf '%s\n' '== literal containment and scratchpad boundary =='
run_command "literal nested build descendant" none 'rm -f ./build/cache/file'
run_command "named scratchpad descendant" none "rm -rf $SCRATCHPAD/cache"
run_without_authorizations "scratchpad absent authorization" deny "rm -rf $SCRATCHPAD/cache"
run_command "scratchpad root" deny "rm -rf $SCRATCHPAD"
run_broad_scratchpad
run_command "outside target" deny "rm -rf $OUTSIDE"
run_command "worktree root" deny 'rm -rf .'
run_command "filesystem root" deny 'rm -rf /'
run_command "Users root" deny "rm -rf $USERS_ROOT/example"
run_command "Users descendant remains protected" deny "rm -rf $USERS_ROOT/example/project/build"
run_command "relative escape" deny 'rm -rf ../outside'
run_command "multiple targets, one escape" deny 'rm -rf ./build ../outside'
run_command "symlink uncertainty" deny 'rm -rf ./build/escape'
run_command "symlink descendant uncertainty" deny 'rm -rf ./build/escape/payload'
run_command "symlink parent traversal uncertainty" deny 'rm -rf ./build/escape/..'
run_command "scratchpad symlink parent traversal uncertainty" deny "rm -rf $SCRATCHPAD/escape/.."
run_command "protected Git metadata" deny 'rm -rf ./.git'
run_command "uppercase protected Git metadata" deny 'rm -rf ./.GIT'
run_command "mixed-case protected Git metadata" deny 'rm -rf ./.GiT'

printf '%s\n' '== explicit-rm uncertainty and out-of-scope Bash workloads =='
RM_SPELLINGS=(/bin/rm /usr/bin/rm ./rm ../bin/rm 'command rm' 'exec rm' 'env rm' 'sudo rm' 'nohup rm' 'nice rm')
for rm_spelling in "${RM_SPELLINGS[@]}"; do
  run_command "$rm_spelling protected target" deny "$rm_spelling -rf ~"
  run_command "$rm_spelling literal descendant" none "$rm_spelling -rf ./build"
done
run_command "nested shell" deny "bash -c 'rm -rf /'"
run_command "eval" deny "eval 'rm -rf /'"
run_command "source" none 'source ./cleanup.sh'
run_command "dot source" none '. ./cleanup.sh'
run_command "script delegation" none './cleanup.sh'
run_command "interpreter delegation" none "python3 -I -c 'print(1)'"
run_command "awk system delegation" none "awk 'BEGIN { system(\"rm -rf /\") }'"
run_policy_command "echo carries rm text as data" no_opinion not_explicit_operation 'echo rm -rf ~'
run_policy_command "printf carries rm text as data" no_opinion not_explicit_operation \
  "printf '%s\\n' 'rm -rf ~'"
run_policy_command "git commit message carries rm text as data" no_opinion not_explicit_operation \
  "git commit -m 'rm -rf ~'"
run_policy_command "grep pattern carries rm text as data" no_opinion not_explicit_operation \
  "grep -r 'rm -rf ~' ."
run_policy_command "time wrapper exposes recursive removal" deny unsafe_rm \
  'time rm -rf ~'
run_command "command substitution" deny 'rm -rf "$(pwd)/build"'
run_command "backtick substitution" deny 'rm -rf `pwd`/build'
run_command "ANSI-C quoting" deny "rm -rf \$'./build'"
run_command "encoded delegation" none 'base64 Zm9v'
run_command "stateful export" none 'export HOME=/private/tmp/session'
run_command "variable command" none '$RM -rf ./build'
run_command "variable option" deny 'rm "$FLAGS" ./build'
run_command "variable target" deny 'rm -rf "$TARGET"'
run_command "tilde expansion" deny 'rm -rf ~/build'
run_command "quoted HOME marker" deny "rm -rf '\$HOME'"
run_command "quoted tilde marker" deny "rm -rf '~'"
run_command "glob target" deny 'rm -rf ./build/*'
# xargs is a carrier: an rm beside it denies removal_beside_xargs (the
# non_literal_rm_targets code is retired).
run_policy_command "pipeline xargs targets are non-literal" deny removal_beside_xargs \
  'printf ./build | xargs rm -rf'
run_policy_command "xargs stdin targets are non-literal" deny removal_beside_xargs 'xargs rm -rf'
run_policy_command "find null-delimited xargs targets are non-literal" deny removal_beside_xargs \
  'find ./build -print0 | xargs -0 rm -rf'
run_command "stdin redirection target" deny 'rm -rf < targets.txt'
run_policy_command "symlink creation can repoint a later rm target" deny path_repointing_ln \
  'ln -s / ./future; rm -rf ./future/etc'
run_policy_command "move can repoint a later rm target" deny path_repointing_mv \
  'mv ./build ./future; rm -rf ./future/cache'
run_policy_command "symbolic copy can repoint a later rm target" deny path_repointing_cp_symlink \
  'cp -s ./build ./future; rm -rf ./future/cache'
run_policy_command "cd can repoint a later rm target" deny path_repointing_cd \
  'cd ./build; rm -rf ./cache'
run_policy_command "pushd can repoint a later rm target" deny path_repointing_pushd \
  'pushd ./build; rm -rf ./cache'
run_policy_command "mkdir can change path resolution before rm" deny path_repointing_mkdir \
  'mkdir ./future; rm -rf ./future/cache'
run_policy_command "mount can repoint a later rm target" deny path_repointing_mount \
  'mount / ./future; rm -rf ./future/etc'
run_policy_command "sibling assignment makes rm resolution stateful" deny path_repointing_assignment \
  'DEST=./future; rm -rf ./build'
# eval is a carrier (the path_repointing_eval code is retired).
run_policy_command "eval can repoint a later rm target" deny removal_beside_eval \
  "eval 'ln -s / ./future'; rm -rf ./future/etc"
run_policy_command "subshell can repoint a later rm target" deny path_repointing_subshell \
  '(cd /); rm -rf ./build'
# A substitution is a carrier (the path_repointing_substitution code is retired).
run_policy_command "command substitution feeding rm target" deny removal_beside_substitution \
  'rm -rf "$(pwd)/build"'
# An accepted removal cannot authorize another segment in the same command.
run_command "conditional compound reaches explicit rm" none 'true && rm -rf ./build'
run_command "protected rm after conditional" deny 'cd x && rm -rf ~'
run_command "protected rm after newline" deny $'echo hi\nrm -rf "$HOME"'
# A line continuation beside a removal word denies (rule G).
run_command "continued literal rm" deny $'rm -rf \\\n./build'
expect_reason "-" "continued literal rm" hidden_separator
run_command "tab-separated literal rm" none $'rm\t-rf\t./build'
run_command "ordinary chain across lines" none $'printf hi &&\nprintf bye'
run_command "heredoc writes data" none $'cat > output.txt <<EOF\nordinary data\nEOF'
# A heredoc is a carrier: a body that holds rm denies, quoted or not.
run_command "rm only in quoted heredoc body" deny $'cat <<\'EOF\'\nrm -rf ~\nEOF'
expect_reason "-" "rm only in quoted heredoc body" removal_beside_heredoc
run_command "leading negation operator with literal descendant" none '! rm -rf ./build'
run_command "leading negation operator with home target" deny '! rm -rf ~'
run_command "single ampersand terminates protected rm" deny 'rm -rf ~ &'
run_command "single ampersand preserves literal rm validation" none 'rm -rf ./build & printf done'
run_command "assignment prefix with literal descendant" none 'FOO=1 rm -rf ./build'
run_command "assignment prefix with home target" deny 'FOO=1 rm -rf ~'
run_command "sudo option with literal descendant" none 'sudo -n rm -rf ./build'
run_command "sudo option with home target" deny 'sudo -E rm -rf ~'
run_command "sudo H option with home target" deny 'sudo -H rm -rf ~'
run_command "sudo user option with home target" deny 'sudo -u root rm -rf ~'
run_command "env option with literal descendant" none 'env -i rm -rf ./build'
run_command "env assignment with home target" deny 'env NAME=value rm -rf ~'
run_command "nice arguments with home target" deny 'nice -n 5 rm -rf ~'
run_command "command option with home target" deny 'command -p rm -rf ~'
run_command "exec arguments with home target" deny 'exec -a x rm -rf ~'
run_command "timeout argument with home target" deny 'timeout 5 rm -rf ~'
run_command "timeout foreground argument with home target" deny 'timeout --foreground 5 rm -rf ~'
run_command "nohup with home target" deny 'nohup rm -rf ~'
run_policy_command "unknown sudo option fails closed" deny unsupported_rm_prefix \
  'sudo --preserve-env=PATH rm -rf ~'
# env -S carries a command string: a carrier.
run_policy_command "unknown env option fails closed" deny removal_beside_env_split \
  "env -S 'rm -rf ~'"
run_command "doas with home target" deny 'doas rm -rf ~'
run_command "doas option with home target" deny 'doas -n rm -rf ~'
run_command "doas user option with home target" deny 'doas -u root rm -rf ~'
run_command "caffeinate option with home target" deny 'caffeinate -i rm -rf ~'
run_command "caffeinate sleep option with home target" deny 'caffeinate -s rm -rf ~'
run_command "caffeinate display option with home target" deny 'caffeinate -d rm -rf ~'
run_policy_command "xargs zero-delimited targets are non-literal" deny removal_beside_xargs \
  'xargs -0 rm -rf'
run_policy_command "xargs no-run-if-empty targets are non-literal" deny removal_beside_xargs \
  'xargs -r rm -rf'
run_policy_command "xargs bounded targets are non-literal" deny removal_beside_xargs \
  'xargs -n 1 rm -rf'
run_policy_command "xargs replacement targets are non-literal" deny removal_beside_xargs \
  'xargs -I{} rm -rf'
run_command "nested supported prefixes with literal descendant" none \
  'sudo -n env NAME=value command -p rm -rf ./build'
run_command "unterminated quote" deny "rm -rf './build"
run_command "concatenated quote encoding" deny 'rm -r"f" ./build'
run_command "unsupported rm option" deny 'rm -rfv ./build'
run_command "missing rm target" deny 'rm -rf'
run_command "unsupported plain rm" deny 'rm ./build/file'

printf '%s\n' '== authorized worktree boundary =='
run_without_authorizations "worktree removal absent authorization" deny "git worktree remove $DISPOSABLE"
run_command "worktree force flag" deny "git worktree remove --force $DISPOSABLE"
run_command "unregistered worktree" deny "git worktree remove $OUTSIDE"
run_command "alternate git form" deny "git -C $WORKTREE worktree remove $DISPOSABLE"
run_command "Git config alias delegation" none "git -c 'alias.erase=!rm -rf /' erase"
run_command "unknown Git alias subcommand" none 'git erase-everything'
run_command "Git remote-helper delegation" none "git fetch 'ext::sh -c rm -rf /'"

printf '%s\n' '== protocol and dependency faults =='
run_json "malformed JSON" deny '{not-json'
run_json "missing cwd" deny '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"printf safe"}}'
run_json "relative cwd is irrelevant to an ordinary command" none '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"printf safe"},"cwd":"."}'
run_json "non-repository cwd is irrelevant to an ordinary command" none "$("$JQ" -cn --arg cwd "$OUTSIDE" '{hook_event_name:"PreToolUse",tool_name:"Bash",tool_input:{command:"printf safe"},cwd:$cwd}')"
run_json "wrong command type" deny "$("$JQ" -cn --arg cwd "$WORKTREE" '{hook_event_name:"PreToolUse",tool_name:"Bash",tool_input:{command:[]},cwd:$cwd}')"
run_json "wrong cwd type" deny '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"printf safe"},"cwd":7}'
run_json "wrong tool" deny "$("$JQ" -cn --arg cwd "$WORKTREE" '{hook_event_name:"PreToolUse",tool_name:"Write",tool_input:{command:"printf safe"},cwd:$cwd}')"
run_json "encoded NUL" deny "$(printf '%s' "{\"hook_event_name\":\"PreToolUse\",\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"printf\\u0000safe\"},\"cwd\":\"$WORKTREE\"}")"
run_json "multiple JSON documents" deny "$(payload 'printf safe')$(payload 'printf safe')"

# roots_copy SOURCE DEST DIR...: a copy of the guard whose TRUSTED_ROOTS line
# names DIR... instead of the shipped roots. The guard reads no variable,
# option or file for its roots, so this rewritten copy is the only way a test
# can choose them. Fails the suite when the rewrite changes nothing.
roots_copy() {
  local src="$1" dst="$2" line
  shift 2
  line="TRUSTED_ROOTS=($*)"
  sed "s|^TRUSTED_ROOTS=.*|$line|" "$src" >"$dst" || exit 1
  if ! grep -qxF "$line" "$dst" || cmp -s "$src" "$dst"; then
    fail "roots fixture: $dst has no TRUSTED_ROOTS line to rewrite"
    exit 1
  fi
}
EMPTY_BIN="$TEST_ROOT/empty-bin"
FAKE_BIN="$TEST_ROOT/fake-bin"
mkdir -p "$EMPTY_BIN" "$FAKE_BIN"
printf '%s\n' '#!/bin/bash' 'exit 0' >"$FAKE_BIN/jq"
chmod +x "$FAKE_BIN/jq"
# B9: no trusted root holds jq. PATH is not read for jq, so emptying it would
# hide nothing; the copy's roots are empty folders.
roots_copy "$GUARD" "$TEST_ROOT/guard-roots-empty.sh" "$EMPTY_BIN"
ADAPTER="$TEST_ROOT/guard-roots-empty.sh"
run_command "B9 missing_jq_is_named" deny 'printf safe'
ADAPTER="$GUARD"
expect_reason B9 missing_jq_is_named dependency_missing_jq
case "$LAST_TEXT" in
  *"jq was not found in "*) pass "B9 the hint names jq and the roots searched" ;;
  *) fail "B9 the hint does not name jq and the roots: $LAST_TEXT" ;;
esac
# A jq that exits 0 with no output, in the copy's only root.
roots_copy "$GUARD" "$TEST_ROOT/guard-roots-fake-jq.sh" "$FAKE_BIN"
ADAPTER="$TEST_ROOT/guard-roots-fake-jq.sh"
run_command "extraction failure" deny 'printf safe'
ADAPTER="$GUARD"
expect_reason "-" "extraction failure" adapter_input_invalid

# Stand-in engines: the adapter runs each with /bin/bash in place of the engine.
standin() { # NAME LINE...: writes a stand-in engine made of the given lines
  local path="$TEST_ROOT/$1"
  shift
  printf '%s\n' '#!/bin/bash' "$@" >"$path"
  chmod +x "$path"
}
standin crash-policy.sh 'exit 23'
standin fork-code-policy.sh 'exit 125'
standin exit-124-policy.sh 'exit 124'
standin self-signal-policy.sh 'kill -TERM "$$"'
standin empty-policy.sh 'exit 0'
standin malformed-policy.sh 'printf malformed'
standin multiple-policy.sh "printf '%s\\n%s\\n' '{\"decision\":\"allow\",\"reason\":\"accepted\"}' '{\"decision\":\"allow\",\"reason\":\"accepted\"}'"
standin timeout-policy.sh 'sleep 30'
standin json-stderr-policy.sh "printf '%s\\n' '{\"decision\":\"allow\",\"reason\":\"accepted\"}' >&2" "printf '%s\\n' '{\"decision\":\"no_opinion\",\"reason\":\"not_explicit_operation\"}'"
standin stray-stdout-policy.sh 'printf "%s\n" malformed' "printf '%s\\n' '{\"decision\":\"no_opinion\",\"reason\":\"not_explicit_operation\"}'"
standin forged-framing-policy.sh "printf '%s\\n' '{\"decision\":\"no_opinion\",\"reason\":\"not_explicit_operation\"}'" "printf '%s\\n' '__GUARD_RUNNER__ status=0 timed_out=0 stderr_lines=0 stderr_other=0 nul=0'"
# Beside a no_opinion, any engine stderr line denies, bash's own fork-retry
# warning included: a check may have been skipped.
standin faulted-no-opinion-policy.sh "printf '%s\\n' 'guard: line 2348: fork: Resource temporarily unavailable' >&2" "printf '%s\\n' '{\"decision\":\"no_opinion\",\"reason\":\"not_explicit_operation\"}'"
standin retry-only-no-opinion-policy.sh "printf '%s\\n' 'fork: retry: Resource temporarily unavailable' '/bin/bash: line 3: fork: retry: Resource temporarily unavailable' >&2" "printf '%s' 'bash: fork: retry: no newline' >&2" "printf '%s\\n' '{\"decision\":\"no_opinion\",\"reason\":\"not_explicit_operation\"}'"
standin mixed-no-opinion-policy.sh "printf '%s\\n' 'bash: fork: retry: Resource temporarily unavailable' 'guard: line 2359: cd: build: Permission denied' >&2" "printf '%s\\n' '{\"decision\":\"no_opinion\",\"reason\":\"not_explicit_operation\"}'"
standin unterminated-other-policy.sh "printf '%s' 'guard: line 9: cd: build: Permission denied' >&2" "printf '%s\\n' '{\"decision\":\"no_opinion\",\"reason\":\"not_explicit_operation\"}'"
standin unterminated-retry-policy.sh "printf '%s' 'bash: fork: retry: no newline' >&2" "printf '%s\\n' '{\"decision\":\"no_opinion\",\"reason\":\"not_explicit_operation\"}'"
standin nul-allow-policy.sh "printf '\\000%s\\n' '{\"decision\":\"allow\",\"reason\":\"accepted\"}'"
run_command "B2 engine_nonzero_exit_is_named" deny 'printf safe' --adapter-policy "$TEST_ROOT/crash-policy.sh"
expect_reason B2 engine_nonzero_exit_is_named engine_failed
run_command "B3 engine_fork_failure_code_is_named" deny 'printf safe' --adapter-policy "$TEST_ROOT/fork-code-policy.sh"
expect_reason B3 engine_fork_failure_code_is_named engine_failed
run_command "B4 engine_empty_output_is_invalid" deny 'printf safe' --adapter-policy "$TEST_ROOT/empty-policy.sh"
expect_reason B4 engine_empty_output_is_invalid engine_output_invalid
run_command "B5 engine_malformed_output_is_invalid" deny 'printf safe' --adapter-policy "$TEST_ROOT/malformed-policy.sh"
expect_reason B5 engine_malformed_output_is_invalid engine_output_invalid
run_command "B6 engine_multiple_objects_is_invalid" deny 'printf safe' --adapter-policy "$TEST_ROOT/multiple-policy.sh"
expect_reason B6 engine_multiple_objects_is_invalid engine_output_invalid
ADAPTER="$FAST_GUARD"
run_command "B1 engine_timeout_is_named" deny 'printf safe' --adapter-policy "$TEST_ROOT/timeout-policy.sh"
ADAPTER="$GUARD"
expect_reason B1 engine_timeout_is_named engine_timeout
B1_TEXT="$LAST_TEXT"
run_command "B7 missing_engine_is_named" deny 'printf safe' --adapter-policy "$TEST_ROOT/not-present-policy.sh"
expect_reason B7 missing_engine_is_named dependency_missing_engine
run_command "B8 missing_runner_is_named" deny 'printf safe' --timeout-runner "$TEST_ROOT/not-present"
expect_reason B8 missing_runner_is_named dependency_missing_perl
run_json "B10 malformed_hook_input_is_named" deny '{"hook_event_name":"PostToolUse"}'
expect_reason B10 malformed_hook_input_is_named adapter_input_invalid
# The stand-in's stderr line is a JSON allow and its stdout a no_opinion: the
# stderr line is never read as a verdict, and as other stderr beside a
# no_opinion it fails the engine.
run_command "B14 json_shaped_stderr_is_only_stderr" deny 'printf safe' --adapter-policy "$TEST_ROOT/json-stderr-policy.sh"
expect_reason B14 json_shaped_stderr_is_only_stderr engine_failed
run_command "B14b malformed_stdout_beside_a_valid_object_is_invalid" deny 'printf safe' --adapter-policy "$TEST_ROOT/stray-stdout-policy.sh"
expect_reason B14b malformed_stdout_beside_a_valid_object_is_invalid engine_output_invalid
run_command "B14c forged_framing_line_is_invalid" deny 'printf safe' --adapter-policy "$TEST_ROOT/forged-framing-policy.sh"
expect_reason B14c forged_framing_line_is_invalid engine_output_invalid
run_command "B15 invalid_configuration_is_named" deny 'printf safe' --scratchpad "$SCRATCHPAD"
expect_reason B15 invalid_configuration_is_named invalid_configuration
run_command "removed log option" deny 'printf safe' --log "$TEST_ROOT/dead.log"
expect_reason "-" "removed log option" invalid_configuration
run_command "B17 engine_exit_124_is_not_a_timeout" deny 'printf safe' --adapter-policy "$TEST_ROOT/exit-124-policy.sh"
expect_reason B17 engine_exit_124_is_not_a_timeout engine_failed
run_command "B18 engine_self_signal_is_not_a_timeout" deny 'printf safe' --adapter-policy "$TEST_ROOT/self-signal-policy.sh"
expect_reason B18 engine_self_signal_is_not_a_timeout engine_failed
NO_FRAMING_RUNNER="$TEST_ROOT/no-framing-runner"
printf '%s\n' '#!/bin/bash' "printf '%s\\n' '{\"decision\":\"allow\",\"reason\":\"accepted\"}'" >"$NO_FRAMING_RUNNER"
chmod +x "$NO_FRAMING_RUNNER"
run_command "B19 runner_without_framing_is_engine_failed" deny 'printf safe' --timeout-runner "$NO_FRAMING_RUNNER"
expect_reason B19 runner_without_framing_is_engine_failed engine_failed
run_command "B20 faulted_engine_stderr_with_no_opinion_is_engine_failed" deny 'printf safe' --adapter-policy "$TEST_ROOT/faulted-no-opinion-policy.sh"
expect_reason B20 faulted_engine_stderr_with_no_opinion_is_engine_failed engine_failed
run_command "B21 fork_retry_only_stderr_with_no_opinion_is_engine_failed" deny 'printf safe' --adapter-policy "$TEST_ROOT/retry-only-no-opinion-policy.sh"
expect_reason B21 fork_retry_only_stderr_with_no_opinion_is_engine_failed engine_failed
run_command "B22 retry_and_other_stderr_with_no_opinion_is_engine_failed" deny 'printf safe' --adapter-policy "$TEST_ROOT/mixed-no-opinion-policy.sh"
expect_reason B22 retry_and_other_stderr_with_no_opinion_is_engine_failed engine_failed
run_command "B26 unterminated_other_stderr_with_no_opinion_is_engine_failed" deny 'printf safe' --adapter-policy "$TEST_ROOT/unterminated-other-policy.sh"
expect_reason B26 unterminated_other_stderr_with_no_opinion_is_engine_failed engine_failed
run_command "B27 unterminated_fork_retry_stderr_with_no_opinion_is_engine_failed" deny 'printf safe' --adapter-policy "$TEST_ROOT/unterminated-retry-policy.sh"
expect_reason B27 unterminated_fork_retry_stderr_with_no_opinion_is_engine_failed engine_failed
run_command "B23 nul_prefixed_allow_is_invalid" deny 'printf safe' --adapter-policy "$TEST_ROOT/nul-allow-policy.sh"
expect_reason B23 nul_prefixed_allow_is_invalid engine_output_invalid

# B16: a timeout kills the engine's whole process group. The stand-in starts
# two children whose command lines carry a marker unique to this run; none
# may survive the denial.
PG_MARK="sc7pgmark$$x$RANDOM"
standin "pgroup-$PG_MARK.sh" "/bin/bash -c 'sleep 20; :' $PG_MARK &" "/bin/bash -c 'sleep 20; :' $PG_MARK" 'wait'
ADAPTER="$FAST_GUARD"
run_command "B16 timeout_kills_the_engine_process_group" deny 'printf safe' --adapter-policy "$TEST_ROOT/pgroup-$PG_MARK.sh"
ADAPTER="$GUARD"
expect_reason B16 timeout_kills_the_engine_process_group engine_timeout
pg_left=1
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
  if ! pgrep -f "$PG_MARK" >/dev/null 2>&1; then pg_left=0; break; fi
  sleep 0.1
done
if [ "$pg_left" -eq 0 ]; then
  pass "B16 no stand-in process survives the timeout"
else
  fail "B16 a stand-in process survived the timeout (pgrep -f $PG_MARK)"
fi

printf '%s\n' '== command length cap =='
assert_len() {
  local label="$1" value="$2" want="$3"
  if [ "${#value}" -eq "$want" ]; then pass "$label fixture is exactly $want characters"; else fail "$label fixture length ${#value}, expected $want"; fi
}
PAD_5995="$(head -c 5995 /dev/zero | tr '\0' a)"
AT_LIMIT_CMD="echo $PAD_5995"
assert_len "at-limit" "$AT_LIMIT_CMD" 6000
run_policy_command "command at the length limit is evaluated" no_opinion not_explicit_operation "$AT_LIMIT_CMD"
OVER_CMD="${AT_LIMIT_CMD}a"
assert_len "over-limit" "$OVER_CMD" 6001
run_policy_command "command over the length limit is denied" deny unsupported_syntax "$OVER_CMD"
TRAIL_CMD="$AT_LIMIT_CMD"$'\n'
assert_len "trailing-newline" "$TRAIL_CMD" 6001
run_policy_command "at-limit command plus a trailing newline is denied" deny unsupported_syntax "$TRAIL_CMD"
TRAIL_MANY_CMD="$AT_LIMIT_CMD"$'\n\n\n\n\n'
assert_len "trailing-newlines" "$TRAIL_MANY_CMD" 6005
run_policy_command "at-limit command plus trailing newlines is denied" deny unsupported_syntax "$TRAIL_MANY_CMD"
MB_CMD="echo $(head -c 5995 /dev/zero | tr '\0' a | sed 's/a/é/g')"
MB_CP="$("$JQ" -n --arg c "$MB_CMD" '$c | length')"
MB_BYTES="$(printf '%s' "$MB_CMD" | LC_ALL=C wc -c | tr -d '[:space:]')"
if [ "$MB_CP" -eq 6000 ] && [ "$MB_BYTES" -gt 6000 ]; then pass "multibyte fixture is 6000 codepoints and $MB_BYTES bytes"; else fail "multibyte fixture codepoints $MB_CP bytes $MB_BYTES"; fi
run_policy_command "multibyte command at 6000 codepoints but over 6000 bytes is denied" deny unsupported_syntax "$MB_CMD"
MB_OK_CMD="echo a$(head -c 2997 /dev/zero | tr '\0' a | sed 's/a/é/g')"
MB_OK_BYTES="$(printf '%s' "$MB_OK_CMD" | LC_ALL=C wc -c | tr -d '[:space:]')"
if [ "$MB_OK_BYTES" -eq 6000 ]; then pass "multibyte fixture is exactly 6000 bytes"; else fail "multibyte at-limit fixture is $MB_OK_BYTES bytes"; fi
run_policy_command "multibyte command at exactly 6000 bytes is evaluated" no_opinion not_explicit_operation "$MB_OK_CMD"
MB_OVER_CMD="echo $(head -c 2998 /dev/zero | tr '\0' a | sed 's/a/é/g')"
MB_OVER_BYTES="$(printf '%s' "$MB_OVER_CMD" | LC_ALL=C wc -c | tr -d '[:space:]')"
if [ "$MB_OVER_BYTES" -eq 6001 ]; then pass "multibyte fixture is exactly 6001 bytes"; else fail "multibyte over-limit fixture is $MB_OVER_BYTES bytes"; fi
run_policy_command "multibyte command at exactly 6001 bytes is denied" deny unsupported_syntax "$MB_OVER_CMD"
# A jq that fails on -j (the length read), in the only root of a copy whose
# root also holds wc and tr, so the engine tier passes.
JQ_LEN_BIN="$TEST_ROOT/jq-len-fail-bin"
mkdir -p "$JQ_LEN_BIN"
printf '%s\n' '#!/bin/bash' 'for a in "$@"; do [ "$a" = "-j" ] && exit 1; done' "exec $JQ \"\$@\"" >"$JQ_LEN_BIN/jq"
chmod +x "$JQ_LEN_BIN/jq"
ln -s "$(command -v wc)" "$JQ_LEN_BIN/wc"
ln -s "$(command -v tr)" "$JQ_LEN_BIN/tr"
roots_copy "$GUARD" "$TEST_ROOT/guard-roots-jq-len.sh" "$JQ_LEN_BIN"
LEN_FAIL_IN="$($JQ -cn --arg cwd "$WORKTREE" --arg command "$OVER_CMD" '{cwd:$cwd,command:$command}')"
LEN_FAIL_OUT="$(printf '%s' "$LEN_FAIL_IN" | /bin/bash --noprofile --norc -p "$TEST_ROOT/guard-roots-jq-len.sh" --policy --scratchpad "$SCRATCHPAD" --authorized-worktree "$DISPOSABLE" 2>>"$LOG")"
LEN_FAIL_GOT="$(printf '%s' "$LEN_FAIL_OUT" | "$JQ" -er '.decision + "/" + .reason' 2>/dev/null || printf invalid)"
if [ "$LEN_FAIL_GOT" = "deny/extraction_failure" ]; then pass "failed length extraction on an oversized command -> deny/extraction_failure"; else fail "failed length extraction: got $LEN_FAIL_GOT"; fi
DENIALS=$((DENIALS + 1))
RM_PAD="$(head -c 5974 /dev/zero | tr '\0' a)"
RM_OVER_CMD="rm -rf ./build/cache; echo $RM_PAD"
assert_len "padded-rm" "$RM_OVER_CMD" 6001
run_policy_command "padded forced removal over the limit is denied" deny unsupported_syntax "$RM_OVER_CMD"
RM_TRAIL_CMD="rm -rf ./build/cache"
RM_NL_COUNT=$((6001 - ${#RM_TRAIL_CMD}))
while [ "$RM_NL_COUNT" -gt 0 ]; do RM_TRAIL_CMD+=$'\n'; RM_NL_COUNT=$((RM_NL_COUNT - 1)); done
assert_len "rm-trailing-newlines" "$RM_TRAIL_CMD" 6001
run_policy_command "forced removal padded with trailing newlines is denied" deny unsupported_syntax "$RM_TRAIL_CMD"

printf '%s\n' '== narrow destructive-operation scope =='
run_command "unlisted literal command" none 'printf safe'
run_command "ordinary ls" none 'ls ./build'
run_command "rm word as echo data" none 'echo rm'
run_command "rm word as grep pattern" none 'grep rm file'
run_command "non-recursive xargs rm targets are non-literal too" deny 'xargs rm -f'
expect_reason S1 "non-recursive xargs rm" removal_beside_xargs
run_command "unsupported ordinary Git command" none 'git status --short'
run_command "remote Git command" none 'git push origin feature'

printf '%s\n' '== reason codes: engine verdict x adapter output =='
run_command "A1 adapter_accept_is_silent" none 'rm -rf ./build'
expect_silent A1 adapter_accept_is_silent
run_policy_command "A1 engine_accept_keeps_its_reason" no_opinion accepted 'rm -rf ./build'
run_command "A2 adapter_no_opinion_is_silent" none "cat $WORKTREE/fixture.txt"
expect_silent A2 adapter_no_opinion_is_silent
run_command "A3 deny_names_unsafe_rm" deny "rm -rf $OUTSIDE/x"
expect_reason A3 deny_names_unsafe_rm unsafe_rm
expect_text A3 unsafe_rm "Destructive command guard denied the command (reason=unsafe_rm): $HINT_UNSAFE_RM. $NO_BYPASS"
run_command "A4 deny_names_unsafe_worktree_remove" deny "git worktree remove --force $DISPOSABLE"
expect_reason A4 deny_names_unsafe_worktree_remove unsafe_worktree_remove
A5_CMD="printf $(head -c 5994 /dev/zero | tr '\0' a)"
assert_len "A5 over-limit printf" "$A5_CMD" 6001
run_command "A5 deny_names_unsupported_syntax_over_cap" deny "$A5_CMD"
expect_reason A5 deny_names_unsupported_syntax_over_cap unsupported_syntax
case "$LAST_TEXT" in
  *"over 6000 bytes"*) pass "A5 hint names the 6000-byte cap" ;;
  *) fail "A5 hint does not name the 6000-byte cap: $LAST_TEXT" ;;
esac
run_command "A6 deny_names_removal_beside_substitution" deny 'rm -rf $(pwd)/build'
expect_reason A6 deny_names_removal_beside_substitution removal_beside_substitution
run_command "A7 deny_names_path_repointing_cd" deny 'cd build; rm -rf ./cache'
expect_reason A7 deny_names_path_repointing_cd path_repointing_cd
printf '%s\n' existing >"$OUTSIDE/file"
run_command "A8 deny_names_unsafe_redirect" deny "printf x > $WORKTREE/build/escape/file"
expect_reason A8 deny_names_unsafe_redirect unsafe_redirect
run_command "A9 deny_names_removal_beside_xargs" deny 'find . -name x | xargs rm -rf'
expect_reason A9 deny_names_removal_beside_xargs removal_beside_xargs
standin future-code-policy.sh "printf '%s\\n' '{\"decision\":\"deny\",\"reason\":\"some_future_code\"}'"
standin allow-foreign-policy.sh "printf '%s\\n' '{\"decision\":\"allow\",\"reason\":\"unsafe_rm\"}'"
standin no-opinion-foreign-policy.sh "printf '%s\\n' '{\"decision\":\"no_opinion\",\"reason\":\"unsafe_rm\"}'"
run_command "A10 unknown_well_formed_code_passes_through" deny 'printf safe' --adapter-policy "$TEST_ROOT/future-code-policy.sh"
expect_reason A10 unknown_well_formed_code_passes_through some_future_code
expect_text A10 some_future_code "Destructive command guard denied the command (reason=some_future_code): $HINT_DEFAULT. $NO_BYPASS"
run_command "A11 allow_with_foreign_reason_is_invalid" deny 'printf safe' --adapter-policy "$TEST_ROOT/allow-foreign-policy.sh"
expect_reason A11 allow_with_foreign_reason_is_invalid engine_output_invalid
run_command "A12 no_opinion_with_foreign_reason_is_invalid" deny 'printf safe' --adapter-policy "$TEST_ROOT/no-opinion-foreign-policy.sh"
expect_reason A12 no_opinion_with_foreign_reason_is_invalid engine_output_invalid
expect_text_of() { # ID LABEL SAVED_TEXT WANT
  if [ "$3" = "$4" ]; then pass "C1 $1 $2: text is the template plus the code's hint"; else fail "C1 $1 $2: text differs: $3"; fi
}
expect_text_of B1 engine_timeout "$B1_TEXT" "Destructive command guard denied the command (reason=engine_timeout): $HINT_ENGINE_TIMEOUT. $NO_BYPASS"

printf '%s\n' '== reason codes: engine stderr noise (warn wrapper) =='
run_command "B11 stderr_noise_with_no_opinion_is_engine_failed" deny "cat $WORKTREE/fixture.txt" --adapter-policy "$WARN"
expect_reason B11 stderr_noise_with_no_opinion_is_engine_failed engine_failed
run_command "B12 stderr_noise_with_accept_fails_closed" deny 'rm -rf ./build' --adapter-policy "$WARN"
expect_reason B12 stderr_noise_with_accept_fails_closed engine_stderr_on_accept
run_command "B13 stderr_noise_keeps_the_policy_code" deny "rm -rf $OUTSIDE/x" --adapter-policy "$WARN"
expect_reason B13 stderr_noise_keeps_the_policy_code unsafe_rm
# The real engine with one check faulted: the cwd lookup of the redirect pass
# fails with a fork error on stderr, so the pass skips a relative target. The
# engine then says no_opinion where the whole engine says unsafe_redirect
# (A8); the adapter must not use that verdict.
FAULT_OLD='redirect_cwd="$(cd -- "$cwd_raw" 2>/dev/null && pwd -P)"'
FAULT_NEW='redirect_cwd="$(printf "%s\n" "guard: fork: Resource temporarily unavailable" >/dev/stderr; false)"'
FAULTED_ENGINE="$TEST_ROOT/guard-faulted-redirect-cwd.sh"
FAULT_OLD="$FAULT_OLD" FAULT_NEW="$FAULT_NEW" /usr/bin/perl -pe 's/\Q$ENV{FAULT_OLD}\E/$ENV{FAULT_NEW}/' "$GUARD" >"$FAULTED_ENGINE"
if grep -qF "$FAULT_NEW" "$FAULTED_ENGINE" && ! grep -qF "$FAULT_OLD" "$FAULTED_ENGINE"; then
  pass "B24 faulted engine copy written"
else
  fail "B24 faulted engine copy not written (the redirect cwd lookup moved)"
fi
B24_CMD='printf x > build/escape/file'
B24_POLICY="$(printf '%s' "$("$JQ" -cn --arg cwd "$WORKTREE" --arg command "$B24_CMD" '{cwd:$cwd,command:$command}')" | /bin/bash --noprofile --norc -p "$FAULTED_ENGINE" --policy --scratchpad "$SCRATCHPAD" --authorized-worktree "$DISPOSABLE" 2>/dev/null)"
if [ "$B24_POLICY" = '{"decision":"no_opinion","reason":"not_explicit_operation"}' ]; then
  pass "B24 the faulted engine skips the redirect check -> no_opinion"
else
  fail "B24 the faulted engine did not skip the redirect check: $B24_POLICY"
fi
run_command "B24 skipped_check_is_not_a_verdict" deny "$B24_CMD" --adapter-policy "$FAULTED_ENGINE"
expect_reason B24 skipped_check_is_not_a_verdict engine_failed
run_command "B24 skipped_check_is_not_a_verdict whole engine" deny "$B24_CMD"
expect_reason B24 "whole engine" unsafe_redirect
WARN_TEXT='guard: line 2348: fork: Resource temporarily unavailable'
run_command "B25 other_stderr_beside_a_real_no_opinion_is_engine_failed" deny "cat $WORKTREE/fixture.txt" --adapter-policy "$WARN"
expect_reason B25 other_stderr_beside_a_real_no_opinion_is_engine_failed engine_failed
run_command "B25 other_stderr_beside_a_real_deny_keeps_the_code" deny "rm -rf $OUTSIDE/x" --adapter-policy "$WARN"
expect_reason B25 other_stderr_beside_a_real_deny_keeps_the_code unsafe_rm
run_command "B25 other_stderr_beside_a_real_accept_fails_closed" deny 'rm -rf ./build' --adapter-policy "$WARN"
expect_reason B25 other_stderr_beside_a_real_accept_fails_closed engine_stderr_on_accept
WARN_TEXT='bash: fork: retry: Resource temporarily unavailable'

printf '%s\n' '== reason codes: text and audit carry no input =='
run_command "C2 deny_text_and_audit_carry_no_input deny" deny "rm -rf $OUTSIDE/ZZMARKERZZ"
expect_reason C2 "marker deny" unsafe_rm
expect_no_input "deny"
WARN_TEXT=ZZSTDERRZZ
run_command "C2 deny_text_and_audit_carry_no_input noisy deny" deny "rm -rf $OUTSIDE/ZZMARKERZZ" --adapter-policy "$WARN"
expect_reason C2 "marker noisy deny" unsafe_rm
expect_no_input "noisy deny"
run_command "C2 deny_text_and_audit_carry_no_input noisy no_opinion" deny "cat $WORKTREE/ZZMARKERZZ" --adapter-policy "$WARN"
expect_reason C2 "marker noisy no_opinion" engine_failed
expect_no_input "noisy no_opinion"
WARN_TEXT='bash: fork: retry: ZZSTDERRZZ'
run_command "C2 deny_text_and_audit_carry_no_input retry-noise no_opinion" deny "cat $WORKTREE/ZZMARKERZZ" --adapter-policy "$WARN"
expect_reason C2 "marker retry-noise no_opinion" engine_failed
expect_no_input "retry-noise no_opinion"
WARN_TEXT=ZZSTDERRZZ
run_command "C2 deny_text_and_audit_carry_no_input noisy accept" deny 'rm -rf ./build' --adapter-policy "$WARN"
expect_reason C2 "marker noisy accept" engine_stderr_on_accept
expect_no_input "noisy accept"
WARN_TEXT='bash: fork: retry: Resource temporarily unavailable'

printf '%s\n' '== the 2026-10-23 refused lines and their neighbours =='
printf '%s\n' reply >"$SCRATCHPAD/reply-2.md"
printf '%s\n' 'printf ok' >"$SCRATCHPAD/x.sh"
printf '%s\n' previous >"$SCRATCHPAD/out.txt"
printf '%s\n' 'a regular line' >"$SCRATCHPAD/a.md"
printf '%s\n' 'a patch line' >"$SCRATCHPAD/b.patch"
# Each read-only or write-but-not-remove line: the engine has no opinion, and
# the adapter stays silent; with engine stderr noise it denies engine_failed.
row_no_opinion() { # ID NAME COMMAND
  run_policy_command "$1 $2 policy" no_opinion not_explicit_operation "$3"
  run_command "$1 $2 adapter" none "$3"
  expect_silent "$1" "$2 adapter"
  run_command "$1 $2 warn wrapper" deny "$3" --adapter-policy "$WARN"
  expect_reason "$1" "$2 warn wrapper" engine_failed
}
row_no_opinion E1 incident_cat_reply "cat $SCRATCHPAD/reply-2.md"
row_no_opinion E2 incident_git_diff_stat_short_sha \
  "git -C $WORKTREE diff --stat 3d2a8f66 origin/main -- docs/map hooks .github/workflows/lint.yml .github/workflows/ci.yml README.md"
row_no_opinion E3 incident_git_diff_stat_full_sha \
  "git -C $WORKTREE diff --stat 3d2a8f66c0ffee1234567890abcdef1234567890 1f2e3d4c5b6a79881726354453627180a9b8c7d6 -- docs/map hooks README.md"
row_no_opinion E4 long_quoted_status_text \
  "$SCRATCHPAD/notify.sh $SCRATCHPAD status \"18:00 … (FIFO/no S_ISREG in read_file; holds); … #1 A/B vs main running. Count: 5\" \"How does it proceed past the round cap?\" --watch $SCRATCHPAD --log $SCRATCHPAD/notes.log --count 2"
row_no_opinion E5 incident_bash_script_redirect "bash $SCRATCHPAD/x.sh > $SCRATCHPAD/out.txt 2>&1"
E6_BODY="$(i=0; while [ "$i" -lt 240 ]; do printf 'heredoc body %04d\n' "$i"; i=$((i + 1)); done)"
row_no_opinion E6 heredoc_write_is_not_a_removal "cat > $SCRATCHPAD/x.md <<'EOF'"$'\n'"$E6_BODY"$'\n'"EOF"
row_no_opinion E7 long_quoted_argument_returns_a_verdict "python3 -I -c \"$(head -c 3000 /dev/zero | tr '\0' x)\""
row_no_opinion E8 read_only_chain_with_pipes \
  "date '+%F'; grep -n regular $SCRATCHPAD/a.md | cut -c1-250 | head -5; sed -n '10,20p' $SCRATCHPAD/b.patch | cut -c1-140"
row_no_opinion E9 quoted_rm_text_is_data "echo 'rm -rf / # A/B'"
row_no_opinion E9 quoted_rm_text_is_data_printf "printf '%s\\n' \"rm -rf $OUTSIDE\""
row_no_opinion E10 directory_listing "ls $SCRATCHPAD/"
run_policy_command "E11 chained_line_with_outside_rm_still_denied policy" deny unsafe_rm "ls $SCRATCHPAD; rm -rf $OUTSIDE/x; echo done"
run_command "E11 chained_line_with_outside_rm_still_denied" deny "ls $SCRATCHPAD; rm -rf $OUTSIDE/x; echo done"
expect_reason E11 chained_line_with_outside_rm_still_denied unsafe_rm
run_command "E12 quoted_command_string_is_not_data" deny "sh -c 'rm -rf $OUTSIDE/x'"
expect_reason E12 quoted_command_string_is_not_data removal_beside_shell
run_command "E13 bare_outside_rm_still_denied" deny "rm -rf $OUTSIDE/x"
expect_reason E13 bare_outside_rm_still_denied unsafe_rm
# The scratchpad is a temp folder; its own rule still allows a strict
# descendant and refuses the root, now naming the code.
run_command "E14 temp_folder_rm_still_allowed_by_its_rule descendant" none "rm -rf $SCRATCHPAD/cache"
run_command "E14 temp_folder_rm_still_allowed_by_its_rule root" deny "rm -rf $SCRATCHPAD"
expect_reason E14 "temp folder root" unsafe_rm
# An absolute home spelling is refused by spelling even for a folder inside
# the worktree; the fixture worktree sits under a temp root, so the absolute
# spelling here is an account-root path. The relative spelling is accepted.
run_command "E15 absolute_in_worktree_rm_names_its_rule absolute" deny "rm -r $USERS_ROOT/example/worktree/build"
expect_reason E15 absolute_in_worktree_rm_names_its_rule unsafe_rm
case "$LAST_TEXT" in
  *"spell a worktree target relative to the current folder"*) pass "E15 the unsafe_rm hint names the relative spelling" ;;
  *) fail "E15 the unsafe_rm hint does not name the relative spelling: $LAST_TEXT" ;;
esac
run_command "E15 absolute_in_worktree_rm_names_its_rule relative" none 'rm -r build'

printf '%s\n' '== engine defect: the wrapped-fragment cap counts only real openers =='
chain_of() { # N: "echo a1 && echo a2 && ... && echo aN"
  local n="$1" i=2 line="echo a1"
  while [ "$i" -le "$n" ]; do line="$line && echo a$i"; i=$((i + 1)); done
  printf '%s' "$line"
}
# Seventeen segments after the first start with a blank: one past
# MAX_FRAGMENTS when each of them was counted as a wrapped fragment.
run_policy_command "H1 seventeen_distinct_segments_are_not_fragments" no_opinion not_explicit_operation "$(chain_of 18)"
run_policy_command "H2 sixteen_segments_pass_as_before" no_opinion not_explicit_operation "$(chain_of 17)"
# The shape of the 2026-10-23 refused chain: 16 &&-joined parts, three of them
# pipelines, 19 segments in all.
run_policy_command "H4 incident_chain_passes" no_opinion not_explicit_operation \
  "git -C $WORKTREE status --short && git -C $WORKTREE log --oneline -5 | head -3 && ls $SCRATCHPAD && grep -c x $SCRATCHPAD/a.md && wc -l $SCRATCHPAD/b.md && git -C $WORKTREE diff --stat | tail -1 && date '+%F' && head -5 $SCRATCHPAD/a.md && tail -5 $SCRATCHPAD/b.md && cat $SCRATCHPAD/c.md | head -2 && git -C $WORKTREE branch --show-current && git -C $WORKTREE rev-parse HEAD && ls -la $WORKTREE && echo checked && printf '%s\\n' done && true"
run_policy_command "H5 padded_segments_plus_outside_rm_still_denied" deny unsafe_rm "$(chain_of 18) && rm -rf $OUTSIDE/x"
run_policy_command "H6 subshell_and_brace_openers_still_queue rm subshell" deny unsafe_rm "(rm -rf $OUTSIDE/x)"
run_policy_command "H6 subshell_and_brace_openers_still_queue rm group" deny unsafe_rm "{ rm -rf $OUTSIDE/x; }"
# A worktree removal inside a subshell or group is judged by the top-level
# loop on the segment with its opener stripped, so these two rows are the
# ones a loop that stops stripping turns into no_opinion.
run_policy_command "H6 subshell_and_brace_openers_still_queue worktree subshell" deny unsafe_worktree_remove "(git worktree remove $OUTSIDE/w)"
run_policy_command "H6 subshell_and_brace_openers_still_queue worktree group" deny unsafe_worktree_remove "{ git worktree remove $OUTSIDE/w; }"

printf '%s\n' '== engine defect: delegated-text recursion keeps its outer cursor =='
# The engine in --policy mode under a 5 s alarm: a parse that never ends is a
# failure of its row, never a stalled suite.
run_policy_bounded() { # LABEL DECISION REASON COMMAND
  local label="$1" expected_decision="$2" expected_reason="$3" command_data="$4" input output rc got
  input="$("$JQ" -cn --arg cwd "$WORKTREE" --arg command "$command_data" '{cwd:$cwd,command:$command}')"
  output="$(printf '%s' "$input" | /usr/bin/perl -e 'alarm shift; exec { $ARGV[0] } @ARGV' 5 /bin/bash --noprofile --norc -p "$GUARD" --policy \
    --scratchpad "$SCRATCHPAD" --authorized-worktree "$DISPOSABLE" 2>>"$LOG")"
  rc=$?
  got="$(printf '%s' "$output" | "$JQ" -er '.decision + "/" + .reason' 2>/dev/null || printf 'no verdict (exit %s)' "$rc")"
  if [ "$got" = "$expected_decision/$expected_reason" ]; then
    pass "$label -> $got"
  else
    fail "$label: expected $expected_decision/$expected_reason within 5 s, got $got"
  fi
  [ "$expected_decision" = "deny" ] && DENIALS=$((DENIALS + 1))
}
run_policy_bounded "H7 quoted_word_before_nested_substitution_terminates" no_opinion not_explicit_operation \
  "echo 'a' \"\$(echo \"x\")\""
run_policy_bounded "H8 incident_hang_line_terminates" no_opinion not_explicit_operation \
  "for s in a b; do printf '%s=%s ' \"\$s\" \"\$(grep -c \"^\\| \$s[0-9]\" $SCRATCHPAD/f)\"; done"
run_policy_bounded "H9 removal_inside_the_substitution_still_seen rm" deny removal_beside_substitution \
  "echo 'a' \"\$(rm -rf $OUTSIDE/x)\""
run_policy_bounded "H9 removal_inside_the_substitution_still_seen worktree" deny removal_beside_substitution \
  "echo 'a' \"\$(git worktree remove --force $OUTSIDE/w)\""
run_policy_bounded "H10 backtick_branch_terminates" no_opinion not_explicit_operation \
  "echo 'a' \"\`echo \"x\"\`\""
# The rm word and the $( beside it are read from the text, quoted or not:
# a removal word beside a substitution denies.
run_policy_bounded "H11 scan_state_after_substitution_is_right" deny removal_beside_substitution \
  "echo \"\$(echo \"xxxxxxxxxx\")\" '\$(rm -rf $OUTSIDE/x)'"

printf '%s\n' '== redacted denial audit =='
DENY_LINES="$(grep -c 'event=destructive-command-deny' "$LOG")"
if [ -f "$LOG" ] && [ "$DENY_LINES" -eq "$DENIALS" ]; then
  pass "every denial emitted exactly once"
else
  fail "denial audit count $DENY_LINES differs from $DENIALS"
fi
if ! grep -q 'event=destructive-command-noise' "$LOG"; then
  pass "no verdict is used beside engine stderr, so no noise record is written"
else
  fail "a noise audit record was written"
fi
# C7: one fixed shape per line, the reason code last and nothing after it.
AUDIT_SHAPE='!/^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z event=destructive-command-deny reason=[a-z_]+$/ { bad=1 } END { exit bad }'
if [ -f "$LOG" ] \
    && ! grep -qF 'rm -rf' "$LOG" \
    && ! grep -qF "$WORKTREE" "$LOG" \
    && ! grep -qF "$SCRATCHPAD" "$LOG" \
    && awk "$AUDIT_SHAPE" "$LOG"; then
  pass "C7 denial audit is fixed-shape with the reason code last, and redacted"
else
  fail "C7 denial audit contains variable or sensitive content"
fi
if printf '%s\n' '2026-10-24T00:00:00Z event=destructive-command-deny reason=unsafe_rm rm -rf /' | awk "$AUDIT_SHAPE"; then
  fail "C7 the audit shape check accepts a line with text after the code"
else
  pass "C7 the audit shape check refuses a line with text after the code"
fi

if [ "$FAILURES" -eq 0 ]; then
  printf 'ALL DESTRUCTIVE COMMAND GUARD TESTS PASSED (%s denials)\n' "$DENIALS"
  printf 'Fixtures retained at %s (suite performs no deletion)\n' "$TEST_ROOT"
  exit 0
fi
printf '%s TEST(S) FAILED\n' "$FAILURES"
exit 1
