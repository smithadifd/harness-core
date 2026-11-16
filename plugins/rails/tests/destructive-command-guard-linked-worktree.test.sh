#!/bin/bash
# Focused regression for an explicitly authorized registered linked worktree.
# The account-root cases below exercise the guard's account-root protection.
set -u
LC_ALL=C

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
GUARD="$HERE/../hooks/destructive-command-guard.sh"
JQ="$(command -v jq)" || { printf 'FAIL: jq is not on PATH\n'; exit 1; }
FIXTURE_ROOT="$(mktemp -d "$(cd /tmp && pwd -P)/destructive-guard-linked.XXXXXX")" || exit 1
MAIN_WORKTREE="$FIXTURE_ROOT/main"
LINKED_WORKTREE="$FIXTURE_ROOT/linked"
SYMLINKED_WORKTREE="$FIXTURE_ROOT/symlink-linked"
SANDBOX_HOME="$FIXTURE_ROOT/sandbox-home"

# The macOS account root and a fixed account name below it; never read from
# the runner's own HOME.
USERS_ROOT=/Users
USERS_HOME="$USERS_ROOT/guard-test-account"

mkdir -p "$MAIN_WORKTREE" "$SANDBOX_HOME"
if [ ! -d "$MAIN_WORKTREE/.git" ]; then
  printf '%s\n' 'inert linked-worktree regression fixture' >"$MAIN_WORKTREE/fixture.txt"
  HOME=$(mktemp -d) git init -q --initial-branch=main "$MAIN_WORKTREE" || exit 1
  HOME=$(mktemp -d) git -C "$MAIN_WORKTREE" add fixture.txt || exit 1
  HOME=$(mktemp -d) \
    GIT_AUTHOR_NAME='Destructive Guard Fixture' GIT_AUTHOR_EMAIL='destructive-guard@example.invalid' \
    GIT_COMMITTER_NAME='Destructive Guard Fixture' GIT_COMMITTER_EMAIL='destructive-guard@example.invalid' \
    git -C "$MAIN_WORKTREE" commit -qm 'test: seed inert fixture' || exit 1
fi
if ! HOME=$(mktemp -d) git -C "$MAIN_WORKTREE" worktree list --porcelain \
  | grep -Fqx -- "worktree $LINKED_WORKTREE"; then
  HOME=$(mktemp -d) git -C "$MAIN_WORKTREE" worktree add -q --detach "$LINKED_WORKTREE" HEAD || exit 1
fi
if [ ! -L "$SYMLINKED_WORKTREE" ]; then
  ln -s linked "$SYMLINKED_WORKTREE" || exit 1
fi

FAILURES=0
pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; FAILURES=$((FAILURES + 1)); }

run_case() {
  local label="$1" expected_decision="$2" expected_reason="$3" cwd="$4" authorization="$5" command_data="$6" home_mode="${7:-sandbox}"
  local input output decision reason
  input="$($JQ -cn --arg cwd "$cwd" --arg command "$command_data" '{cwd:$cwd,command:$command}')"
  if [ "$home_mode" = authorized ]; then
    output="$(printf '%s' "$input" | HOME="$authorization" /bin/bash --noprofile --norc -p "$GUARD" --policy --authorized-worktree "$authorization" 2>/dev/null)"
  else
    output="$(printf '%s' "$input" | HOME="$SANDBOX_HOME" /bin/bash --noprofile --norc -p "$GUARD" --policy --authorized-worktree "$authorization" 2>/dev/null)"
  fi
  decision="$(printf '%s' "$output" | "$JQ" -er '.decision' 2>/dev/null || printf invalid)"
  reason="$(printf '%s' "$output" | "$JQ" -er '.reason' 2>/dev/null || printf invalid)"
  if [ "$decision" = "$expected_decision" ] && [ "$reason" = "$expected_reason" ]; then
    pass "$label -> $decision/$reason"
  else
    fail "$label: expected $expected_decision/$expected_reason, got $decision/$reason"
  fi
}

printf '%s\n' '== registered linked worktree =='
run_case "authorized linked worktree" no_opinion accepted "$MAIN_WORKTREE" "$LINKED_WORKTREE" \
  "git worktree remove $LINKED_WORKTREE"

printf '%s\n' '== protected roots and worktree identity =='
run_case "filesystem root" deny invalid_authorized_worktree "$MAIN_WORKTREE" / 'git worktree remove /'
run_case "Users root" deny invalid_authorized_worktree "$MAIN_WORKTREE" "$USERS_ROOT" "git worktree remove $USERS_ROOT"
run_case "Users account root" deny invalid_authorized_worktree "$MAIN_WORKTREE" "$USERS_HOME" \
  "git worktree remove $USERS_HOME"
run_case "registered worktree equal to HOME" deny invalid_authorized_worktree "$MAIN_WORKTREE" "$LINKED_WORKTREE" \
  "git worktree remove $LINKED_WORKTREE" authorized
run_case "main worktree is never removable" deny unsafe_worktree_remove "$LINKED_WORKTREE" "$MAIN_WORKTREE" \
  "git worktree remove $MAIN_WORKTREE"
run_case "current linked worktree is never removable" deny unsafe_worktree_remove "$LINKED_WORKTREE" "$LINKED_WORKTREE" \
  "git worktree remove $LINKED_WORKTREE"
run_case "symlinked linked-worktree path" deny invalid_authorized_worktree "$MAIN_WORKTREE" "$SYMLINKED_WORKTREE" \
  "git worktree remove $SYMLINKED_WORKTREE"

printf '%s\n' '== exact literal form =='
run_case "argument differs from authorization" deny unsafe_worktree_remove "$MAIN_WORKTREE" "$LINKED_WORKTREE" \
  "git worktree remove $MAIN_WORKTREE"
run_case "force flag remains forbidden" deny unsafe_worktree_remove "$MAIN_WORKTREE" "$LINKED_WORKTREE" \
  "git worktree remove --force $LINKED_WORKTREE"

if [ "$FAILURES" -eq 0 ]; then
  printf '%s\n' 'ALL LINKED-WORKTREE REGRESSIONS PASSED'
  printf 'Fixtures retained at %s (test performs no deletion)\n' "$FIXTURE_ROOT"
  exit 0
fi
printf '%s TEST(S) FAILED\n' "$FAILURES"
exit 1
