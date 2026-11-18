# The git guard suites' shared harness. Sourced by each suite after it sets
# HERE (its own folder) and SUITE (a short name); never run on its own.
#
# Every row runs the guard as hooks.json runs it:
#   /usr/bin/env -u BASH_ENV -u ENV /bin/bash --noprofile --norc -p GUARD
#     --push-to-main V --force-push V --commit-style V
# with the hook input on stdin and an environment cleared by env -i that holds
# only HOME (a fresh folder), PATH and the row's own variables. Command
# strings are inert JSON data and are never executed.
#
# Fixture repositories are made with --initial-branch under a fixed HOME:
# REPO_FEATURE (on feature, remote example.invalid/acme/widget), REPO_MAIN (on
# main) and REPO_MASTER (on master), side by side as feature-repo, main-repo
# and master-repo.
set -u
LC_ALL=C

GUARD="$HERE/../hooks/git-guard.sh"
HOOKS="$HERE/../hooks/hooks.json"
PLUGIN_ROOT="$(cd "$HERE/.." && pwd)"
JQ="$(command -v jq)" || { printf 'FAIL: jq is not on PATH\n'; exit 1; }
command -v git >/dev/null 2>&1 || { printf 'FAIL: git is not on PATH\n'; exit 1; }
T="$(mktemp -d "$(cd /tmp && pwd -P)/git-guard-$SUITE.XXXXXX")" || exit 1
GG_HOME="$T/home"
GG_PATH="/usr/bin:/bin"
mkdir -p "$GG_HOME" "$T/repos"

FAILURES=0
PASSES=0
pass() { printf 'PASS: %s\n' "$1"; PASSES=$((PASSES + 1)); }
fail() { printf 'FAIL: %s\n' "$1"; FAILURES=$((FAILURES + 1)); }

# git for fixtures only, under the fixed HOME and a fixed identity.
git_q() { HOME="$GG_HOME" git -c user.name=t -c user.email=t@example.invalid -c init.defaultBranch=main "$@"; }
REPO_FEATURE="$T/repos/feature-repo"
REPO_MAIN="$T/repos/main-repo"
REPO_MASTER="$T/repos/master-repo"
git_q init -q --initial-branch=feature "$REPO_FEATURE" || exit 1
git_q -C "$REPO_FEATURE" remote add origin https://example.invalid/acme/widget.git || exit 1
git_q init -q --initial-branch=main "$REPO_MAIN" || exit 1
git_q -C "$REPO_MAIN" remote add origin https://example.invalid/acme/gadget.git || exit 1
git_q init -q --initial-branch=master "$REPO_MASTER" || exit 1

# The options every row passes unless it sets its own: the plugin's defaults.
GG_MAIN=ask
GG_FORCE=ask
GG_COMMIT=off

gg_payload() { # CWD COMMAND
  "$JQ" -cn --arg cwd "$1" --arg command "$2" \
    '{hook_event_name:"PreToolUse",tool_name:"Bash",tool_input:{command:$command},cwd:$cwd}'
}

# gg_hook CWD COMMAND [ENV=VAL...]: runs the hook; sets OUT, ERR, RC,
# DECISION (silent, ask, deny or other) and CODE (the reason code, or "").
OUT=""
ERR=""
RC=0
DECISION=""
CODE=""
GG_GUARD=""
gg_hook() {
  local cwd="$1" command_data="$2" input reason
  local guard="${GG_GUARD:-$GUARD}"
  shift 2
  input="$(gg_payload "$cwd" "$command_data")"
  OUT="$(cd "$T" && printf '%s' "$input" | /usr/bin/env -i HOME="$GG_HOME" PATH="$GG_PATH" "$@" \
    /usr/bin/env -u BASH_ENV -u ENV /bin/bash --noprofile --norc -p "$guard" \
    --push-to-main "$GG_MAIN" --force-push "$GG_FORCE" --commit-style "$GG_COMMIT" 2>"$T/stderr")"
  RC=$?
  ERR="$(cat "$T/stderr")"
  gg_classify
}

# gg_hook_args CWD COMMAND ARG...: runs the hook with exactly the given
# arguments after the guard's path (no option pairs of its own); sets the
# same variables as gg_hook.
gg_hook_args() {
  local cwd="$1" command_data="$2" input
  shift 2
  input="$(gg_payload "$cwd" "$command_data")"
  OUT="$(cd "$T" && printf '%s' "$input" | /usr/bin/env -i HOME="$GG_HOME" PATH="$GG_PATH" \
    /usr/bin/env -u BASH_ENV -u ENV /bin/bash --noprofile --norc -p "${GG_GUARD:-$GUARD}" "$@" 2>"$T/stderr")"
  RC=$?
  ERR="$(cat "$T/stderr")"
  gg_classify
}

# Sets DECISION and CODE from OUT and RC.
gg_classify() {
  local reason
  DECISION=other
  CODE=""
  # N2 on every row of every suite: the guard never prints an allow.
  case "$OUT" in *'"allow"'*) fail "N2 the guard printed an allow: ${OUT:0:200}" ;; esac
  if [ "$RC" -eq 0 ] && [ -z "$OUT" ]; then DECISION=silent; return 0; fi
  [ "$RC" -eq 0 ] || return 0
  DECISION="$(printf '%s' "$OUT" | "$JQ" -er '.hookSpecificOutput.permissionDecision' 2>/dev/null)" || DECISION=other
  reason="$(printf '%s' "$OUT" | "$JQ" -er '.hookSpecificOutput.permissionDecisionReason' 2>/dev/null)" || reason=""
  if [[ "$reason" =~ \(reason=([a-z_]+)\) ]]; then CODE="${BASH_REMATCH[1]}"; fi
  case "$DECISION" in ask|deny) ;; *) DECISION=other ;; esac
}

# gg_expect LABEL WANT: WANT is silent, ask, deny, ask:CODE or deny:CODE.
gg_expect() {
  local label="$1" want="$2" got="$DECISION"
  case "$want" in *:*) got="$DECISION:$CODE" ;; esac
  if [ "$got" = "$want" ]; then
    pass "$label -> $want"
  else
    case "$DECISION" in
      other) fail "$label: expected $want, got other (exit $RC) ${OUT:0:200}" ;;
      *) fail "$label: expected $want, got $DECISION${CODE:+:$CODE}" ;;
    esac
  fi
}

# gg_row_at CWD LABEL WANT COMMAND [ENV=VAL...]: one row. An ask row also runs
# unattended (CLAUDE_SEAT_UNATTENDED=1) and expects a deny with the same code;
# a deny row is a deny there too.
gg_row_at() {
  local cwd="$1" label="$2" want="$3" command_data="$4" unattended
  shift 4
  gg_hook "$cwd" "$command_data" "$@"
  gg_expect "$label" "$want"
  case "$want" in
    ask*|deny*)
      case "$want" in ask*) unattended="deny${want#ask}" ;; *) unattended="$want" ;; esac
      gg_hook "$cwd" "$command_data" "$@" CLAUDE_SEAT_UNATTENDED=1
      gg_expect "$label, unattended" "$unattended"
      ;;
  esac
}

# gg_row LABEL WANT COMMAND [ENV=VAL...]: a row from REPO_FEATURE.
gg_row() {
  gg_row_at "$REPO_FEATURE" "$@"
}

# gg_both LABEL WANT COMMAND: a row whose verdict is the same attended and
# unattended (a silent row checked in both contexts).
gg_both() {
  local label="$1" want="$2" command_data="$3"
  shift 3
  gg_hook "$REPO_FEATURE" "$command_data" "$@"
  gg_expect "$label, attended" "$want"
  gg_hook "$REPO_FEATURE" "$command_data" "$@" CLAUDE_SEAT_UNATTENDED=1
  gg_expect "$label, unattended" "$want"
}

# gg_policy CWD COMMAND [ARG...]: the engine alone, with the given engine
# arguments; sets OUT and RC.
gg_policy() {
  local cwd="$1" command_data="$2" input
  shift 2
  input="$("$JQ" -cn --arg cwd "$cwd" --arg command "$command_data" '{cwd:$cwd,command:$command}')"
  OUT="$(cd "$T" && printf '%s' "$input" | /usr/bin/env -i HOME="$GG_HOME" PATH="$GG_PATH" \
    /bin/bash --noprofile --norc -p "${GG_GUARD:-$GUARD}" --policy "$@" 2>/dev/null)"
  RC=$?
}

# gg_pair MAIN FORCE COMMIT COMMAND [ENV=VAL...]: every git guard entry of
# hooks.json, in file order, run on one hook input from REPO_FEATURE the way
# the walk's probes saw Claude Code run plugin hooks: an entry runs only when
# every ${user_config.KEY} its arguments reference is stored, with the stored
# values in place of the references; MAIN, FORCE and COMMIT are the stored
# push_to_main, force_push and commit_style, and "-" is unstored. Every entry
# that runs gets the same environment. Sets, per entry (0 is the floor, 1 the
# stored-options hook), PAIR_DECISIONS (skipped, silent, ask, deny or other),
# PAIR_CODES, PAIR_OUTS and PAIR_ERRS; PAIR_RAN lists the entries that ran;
# DECISION, CODE and OUT are the strictest of them (deny over ask over
# silent, the first entry to reach it; any other result is other), the model
# of Claude Code combining several PreToolUse verdicts.
PAIR_RAN=""
PAIR_DECISIONS=()
PAIR_CODES=()
PAIR_OUTS=()
PAIR_ERRS=()
gg_pair() {
  local main="$1" force="$2" commit="$3" command_data="$4" stored entries line input word
  local index=0 level best=-1 best_code="" best_out="" other=0
  local -a argv
  shift 4
  PAIR_RAN=""
  PAIR_DECISIONS=()
  PAIR_CODES=()
  PAIR_OUTS=()
  PAIR_ERRS=()
  DECISION=other
  CODE=""
  OUT=""
  stored="$("$JQ" -cn --arg m "$main" --arg f "$force" --arg c "$commit" \
    '{push_to_main: $m, force_push: $f, commit_style: $c} | with_entries(select(.value != "-"))')" \
    || { fail "gg_pair fixture: the stored values"; return 0; }
  entries="$("$JQ" -c --arg root "$PLUGIN_ROOT" --argjson stored "$stored" '
    [.hooks.PreToolUse[] | select(.matcher == "Bash") | .hooks[]
      | select(.type == "command" and ((.args // []) | any(endswith("/hooks/git-guard.sh"))))]
    | .[]
    | ([.args[] | scan("\\$\\{user_config\\.([A-Za-z0-9_]+)\\}") | .[0]] | unique) as $refs
    | if ($refs | all(. as $k | $stored | has($k))) then
        [.command] + (.args | map(gsub("\\$\\{CLAUDE_PLUGIN_ROOT\\}"; $root)
          | gsub("\\$\\{user_config\\.(?<k>[A-Za-z0-9_]+)\\}"; $stored[.k] // "")))
      else "skipped" end' "$HOOKS")" || { fail "gg_pair fixture: hooks.json"; return 0; }
  [ -n "$entries" ] || { fail "gg_pair fixture: hooks.json has no git guard entry"; return 0; }
  input="$(gg_payload "$REPO_FEATURE" "$command_data")"
  while IFS= read -r line; do
    if [ "$line" = '"skipped"' ]; then
      PAIR_DECISIONS[index]=skipped
      PAIR_CODES[index]=""
      PAIR_OUTS[index]=""
      PAIR_ERRS[index]=""
      index=$((index + 1))
      continue
    fi
    argv=()
    while IFS= read -r word; do
      case "$word" in */hooks/git-guard.sh) [ -z "$GG_GUARD" ] || word="$GG_GUARD" ;; esac
      argv+=("$word")
    done < <("$JQ" -r '.[]' <<<"$line")
    OUT="$(cd "$T" && printf '%s' "$input" | /usr/bin/env -i HOME="$GG_HOME" PATH="$GG_PATH" "$@" "${argv[@]}" 2>"$T/stderr")"
    RC=$?
    ERR="$(cat "$T/stderr")"
    gg_classify
    PAIR_RAN="${PAIR_RAN:+$PAIR_RAN }$index"
    PAIR_DECISIONS[index]="$DECISION"
    PAIR_CODES[index]="$CODE"
    PAIR_OUTS[index]="$OUT"
    PAIR_ERRS[index]="$ERR"
    case "$DECISION" in
      silent) level=0 ;;
      ask) level=1 ;;
      deny) level=2 ;;
      *) other=1; level=-1 ;;
    esac
    if [ "$level" -gt "$best" ]; then best="$level"; best_code="$CODE"; best_out="$OUT"; fi
    index=$((index + 1))
  done <<<"$entries"
  CODE="$best_code"
  OUT="$best_out"
  if [ "$other" -eq 1 ] || [ "$best" -lt 0 ]; then
    DECISION=other
  else
    case "$best" in 0) DECISION=silent ;; 1) DECISION=ask ;; *) DECISION=deny ;; esac
  fi
}

# gg_pair_hook N WANT LABEL: entry N's own verdict in the last gg_pair; WANT is
# skipped, silent, ask, deny, ask:CODE or deny:CODE.
gg_pair_hook() {
  local got="${PAIR_DECISIONS[$1]:-missing}"
  case "$2" in *:*) got="$got:${PAIR_CODES[$1]:-}" ;; esac
  if [ "$got" = "$2" ]; then pass "$3 -> $2"; else fail "$3: expected $2, got $got"; fi
}

# replace_copy DEST OLD NEW [SOURCE]: a copy of SOURCE (the guard) with OLD,
# which must occur exactly once, replaced by NEW.
replace_copy() {
  python3 -I -c 'import sys
dst, old, new, src = sys.argv[1:5]
text = open(src, encoding="utf-8").read()
n = text.count(old)
if n != 1:
    sys.exit("replace_copy: %d matches for %r" % (n, old[:80]))
open(dst, "w", encoding="utf-8").write(text.replace(old, new))' "$1" "$2" "$3" "${4:-$GUARD}" || fail "fixture: $1 could not be written"
}

gg_finish() { # TITLE
  if [ "$FAILURES" -eq 0 ]; then
    printf 'ALL %s TESTS PASSED (%s)\n' "$1" "$PASSES"
    exit 0
  fi
  printf '%s TEST(S) FAILED\n' "$FAILURES"
  exit 1
}
