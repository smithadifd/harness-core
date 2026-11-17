#!/bin/bash
# Focused fixture suite for output-redirection classification. Every command
# under review is inert JSON data; none is evaluated, sourced, or sent to a
# shell for execution. Redirection through a symlink is exercised only via
# fixtures this file builds and owns, never by actually redirecting into one.
#
# The fixture root is a plain, default `mktemp -d` on purpose. On macOS that
# lands under /var, which is itself a symlink to /private/var, so every
# absolute path this suite hands the guard crosses a real system symlink that
# no fixture created. An earlier revision hardcoded a /private/tmp root, which
# structurally bypassed that symlink and let an absolute-versus-relative
# asymmetry in the guard go unseen by a fully green run. A root that avoids
# the hazard cannot pin behaviour at the hazard.
set -u
LC_ALL=C

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUARD="$HERE/../hooks/destructive-command-guard.sh"
JQ="$(command -v jq)" || { printf 'FAIL: jq is not on PATH\n'; exit 1; }
TEST_ROOT="$(mktemp -d)" || exit 1
TEST_ROOT_PHYSICAL="$(cd -- "$TEST_ROOT" && pwd -P)" || exit 1
WORKTREE="$TEST_ROOT/worktree"
SCRATCHPAD="$TEST_ROOT/session-scratchpad"
OUTSIDE="$TEST_ROOT/outside"

# Say in the output whether the root alias is actually in play, so a reader
# can tell a run that exercised it from one that did not.
TEST_ROOT_ENTRY="${TEST_ROOT#/}"
TEST_ROOT_ENTRY="/${TEST_ROOT_ENTRY%%/*}"
printf 'TEST_ROOT=%s\n' "$TEST_ROOT"
printf 'TEST_ROOT (physical)=%s\n' "$TEST_ROOT_PHYSICAL"
if [ -L "$TEST_ROOT_ENTRY" ]; then
  printf 'root entry %s is a symlink -> %s: absolute fixture paths cross a real system symlink\n' \
    "$TEST_ROOT_ENTRY" "$(readlink "$TEST_ROOT_ENTRY")"
else
  printf 'NOTE: root entry %s is not a symlink; this run does not exercise the root-alias hazard\n' \
    "$TEST_ROOT_ENTRY"
fi

mkdir -p "$WORKTREE/build" "$SCRATCHPAD" "$OUTSIDE"
git init -q "$WORKTREE"
printf '%s\n' 'a regular existing file' >"$WORKTREE/existing-regular.txt"
printf '%s\n' 'the real target of the symlink' >"$OUTSIDE/real-file.txt"
printf '%s\n' 'reached only through the ancestor symlink' >"$OUTSIDE/via-ancestor.txt"
ln -s "$OUTSIDE/real-file.txt" "$WORKTREE/existing-symlink.txt"
ln -s "$OUTSIDE" "$WORKTREE/build/escape"
ln -s "$OUTSIDE/never-created.txt" "$WORKTREE/dangling-symlink.txt"

FAILURES=0
TESTS=0
pass() { TESTS=$((TESTS + 1)); printf 'PASS: %s\n' "$1"; }
fail() { TESTS=$((TESTS + 1)); FAILURES=$((FAILURES + 1)); printf 'FAIL: %s\n' "$1"; }

policy_payload() {
  "$JQ" -cn --arg cwd "$1" --arg command "$2" '{cwd:$cwd,command:$command}'
}

adapter_payload() {
  "$JQ" -cn --arg cwd "$1" --arg command "$2" \
    '{hook_event_name:"PreToolUse",tool_name:"Bash",tool_input:{command:$command},cwd:$cwd}'
}

# The redirect pass never consults --scratchpad; it is passed so the
# invocation has the adapter's shape, not because it changes any verdict.
run_policy_command_from() {
  local cwd="$1" label="$2" expected_decision="$3" expected_reason="$4" command_data="$5"
  local output got_decision got_reason
  output="$(policy_payload "$cwd" "$command_data" | /bin/bash --noprofile --norc -p "$GUARD" --policy --scratchpad "$SCRATCHPAD" 2>/dev/null)"
  got_decision="$(printf '%s' "$output" | "$JQ" -er '.decision' 2>/dev/null || printf invalid)"
  got_reason="$(printf '%s' "$output" | "$JQ" -er '.reason' 2>/dev/null || printf invalid)"
  if [ "$got_decision" = "$expected_decision" ] && [ "$got_reason" = "$expected_reason" ]; then
    pass "$label -> $expected_decision/$expected_reason"
  else
    fail "$label: expected $expected_decision/$expected_reason, got $got_decision/$got_reason"
  fi
}

run_policy_command() {
  run_policy_command_from "$WORKTREE" "$@"
}

run_adapter_ordinary() {
  local label="$1" command_data="$2" output exit_code
  output="$(adapter_payload "$WORKTREE" "$command_data" | /bin/bash --noprofile --norc -p "$GUARD" --scratchpad "$SCRATCHPAD" 2>/dev/null)"
  exit_code=$?
  if [ "$exit_code" -eq 0 ] && [ -z "$output" ]; then
    pass "$label -> exit 0, no output"
  else
    fail "$label: expected exit 0 and no output, got exit=$exit_code output=$output"
  fi
}

# 1: > through a pre-existing symlinked path -> deny (the row this exists for)
run_policy_command "redirect into pre-existing symlink, relative target" deny unsafe_redirect \
  'cat > existing-symlink.txt'
run_policy_command "redirect into pre-existing symlink, absolute target" deny unsafe_redirect \
  "cat > $WORKTREE/existing-symlink.txt"
run_policy_command "no-space form still reaches the same target" deny unsafe_redirect \
  'cat>existing-symlink.txt'
run_policy_command "append also follows the symlink to its target" deny unsafe_redirect \
  'cat >> existing-symlink.txt'
run_policy_command "ancestor directory symlink, leaf already exists through it" deny unsafe_redirect \
  'cat > build/escape/via-ancestor.txt'
run_policy_command "stderr redirect into the same pre-existing symlink" deny unsafe_redirect \
  'echo hi 2> existing-symlink.txt'

# 2/3: > to a fresh name below the worktree or the scratchpad -> stays allowed
run_policy_command "fresh name below the worktree stays allowed" no_opinion not_explicit_operation \
  'cat > fresh-name.txt'
run_policy_command "fresh name below the scratchpad stays allowed" no_opinion not_explicit_operation \
  "cat > $SCRATCHPAD/fresh-scratch.txt"
run_policy_command "fresh leaf under a symlinked ancestor stays allowed" no_opinion not_explicit_operation \
  'cat > build/escape/fresh-under-symlink.txt'
run_policy_command "existing regular file, no symlink component, stays allowed" no_opinion not_explicit_operation \
  'cat > existing-regular.txt'
run_policy_command "heredoc header targets a fresh name, still allowed" no_opinion not_explicit_operation \
  $'cat > heredoc-output.txt <<EOF\nordinary data\nEOF'

# 4: an ordinary non-redirecting command is unchanged: exit 0, no output at all
run_adapter_ordinary "ordinary command carries no opinion" 'npm test'
run_adapter_ordinary "fresh-name redirect carries no opinion at the adapter layer" 'cat > fresh-name-2.txt'

# Guardrails against over-reach: quoted data and fd-duplication are not paths
run_policy_command "a quoted literal > used as grep data is not a redirect" no_opinion not_explicit_operation \
  "grep '>' existing-regular.txt"
run_policy_command "fd duplication carries no path target" no_opinion not_explicit_operation \
  'echo hi 2>&1'

# `&>` and `>|` are redirection operators: split_command_segments() keeps
# them inside the segment, and the scanner reads the `>` in each like a plain
# `>`, so their target is classified.
run_policy_command "&> into the pre-existing symlink is denied" deny unsafe_redirect \
  'echo hi &> existing-symlink.txt'
run_policy_command "&> to a fresh name stays allowed" no_opinion not_explicit_operation \
  'echo hi &> fresh-both-streams.txt'

run_policy_command ">| into the pre-existing symlink is denied" deny unsafe_redirect \
  'cat >|existing-symlink.txt'
run_policy_command ">| to a fresh name stays allowed" no_opinion not_explicit_operation \
  'cat >|fresh-clobber.txt'

# 5: the same file, every spelling, through the default mktemp root. The
# absolute literal below is exactly what a caller pastes after reading the
# output of `mktemp -d`; it crosses the /var -> /private/var symlink and must
# get the same verdict as the relative spelling from the same directory. The
# existing-regular control and the two genuine-symlink cases share one root,
# so a deny here is attributable to the fixture symlink, not to the root.
run_policy_command "absolute fresh name under the default mktemp root stays allowed" no_opinion not_explicit_operation \
  "cat > $TEST_ROOT/msg.txt"
printf '%s\n' 'now it exists' >"$TEST_ROOT/msg.txt"
run_policy_command "second write to the same absolute literal, now existing, stays allowed" no_opinion not_explicit_operation \
  "cat > $TEST_ROOT/msg.txt"
run_policy_command_from "$TEST_ROOT" "relative spelling of that file from its own directory stays allowed" no_opinion not_explicit_operation \
  'cat > msg.txt'
run_policy_command_from "$TEST_ROOT" "absolute spelling of that file from the same cwd agrees" no_opinion not_explicit_operation \
  "cat > $TEST_ROOT/msg.txt"
run_policy_command_from "$TEST_ROOT" "physical spelling of that file agrees too" no_opinion not_explicit_operation \
  "cat > $TEST_ROOT_PHYSICAL/msg.txt"
run_policy_command "existing regular file, absolute spelling under the same root, stays allowed (control)" no_opinion not_explicit_operation \
  "cat > $WORKTREE/existing-regular.txt"
run_policy_command "genuine ancestor symlink, absolute spelling, still denied" deny unsafe_redirect \
  "cat > $WORKTREE/build/escape/via-ancestor.txt"
run_policy_command "dangling leaf symlink, relative spelling, denied" deny unsafe_redirect \
  'cat > dangling-symlink.txt'
run_policy_command "dangling leaf symlink, absolute spelling, denied" deny unsafe_redirect \
  "cat > $WORKTREE/dangling-symlink.txt"
run_policy_command "fresh leaf under a symlinked ancestor, absolute spelling, stays allowed" no_opinion not_explicit_operation \
  "cat > $WORKTREE/build/escape/fresh-under-symlink-abs.txt"

if [ "$FAILURES" -eq 0 ]; then
  printf 'ALL %s TESTS PASSED\n' "$TESTS"
  exit 0
fi
printf '%s of %s TEST(S) FAILED\n' "$FAILURES" "$TESTS"
exit 1
