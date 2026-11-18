#!/bin/bash
# The git guard never allows (design § 5): it asks, denies, or stays silent
# (exit 0, nothing on stdout) and leaves the command to Claude Code's own
# permission flow.
#   N1 static: no code line of the guard holds the word allow; the only
#      permissionDecision values in the file are ask and deny; neither of the
#      git guard's two hooks.json entries sets one; no userConfig option lists
#      allow.
#   N2 dynamic: a 40-row core corpus under all 27 option combinations prints
#      ask, deny or nothing, never allow; N2b the same under --tighten-only. (Every row of every other git guard
#      suite is checked the same way by the harness.)
#   N3 an engine stand-in that prints an allow verdict is denied as invalid
#      engine output.
#   N4 the hook argument --force-push allow and a force-push denies as
#      option_invalid_force_push, and the output never holds an allow.
# Command strings are inert JSON data and are never executed.
set -u
LC_ALL=C
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUITE=never-allow
. "$HERE/fixtures/git-guard/harness.sh"
MANIFEST="$HERE/../.claude-plugin/plugin.json"

printf '%s\n' '== N1 static =='
N1_HITS="$(awk '
  /^[[:space:]]*#/ { next }
  { low = " " tolower($0) " "
    if (match(low, /[^a-z0-9_]allow[^a-z0-9_]/)) print NR ": " $0 }
' "$GUARD")"
if [ -z "$N1_HITS" ]; then pass "N1 no code line of the guard holds the word allow"; else fail "N1 code lines hold allow: $N1_HITS"; fi
N1_DECISIONS="$(grep -oE 'permissionDecision"?:"?[A-Za-z_]*' "$GUARD" | sort -u | tr '\n' ' ')"
if [ "$N1_DECISIONS" = 'permissionDecision":"ask permissionDecision":"deny ' ]; then
  pass "N1 the only permissionDecision values in the guard are ask and deny"
else
  fail "N1 permissionDecision values: $N1_DECISIONS"
fi
N1_HOOK="$("$JQ" -r '[.. | objects | select(has("args") and ((.args | map(tostring) | join(" ")) | contains("/hooks/git-guard.sh")))] | if length == 2 then ([.[] | [paths(type == "string") as $p | getpath($p)] | map(select(test("permissionDecision"))) | length] | add | tostring) else "entries=\(length)" end' "$HOOKS" 2>/dev/null)"
if [ "$N1_HOOK" = 0 ]; then pass "N1 neither git guard hook entry sets a permissionDecision"; else fail "N1 the git guard's hook entries: $N1_HOOK"; fi
N1_OPTIONS="$("$JQ" -r '[.userConfig // {} | .[] | .options // [] | .[] | select(ascii_downcase == "allow")] | length' "$MANIFEST" 2>/dev/null)"
if [ "$N1_OPTIONS" = 0 ]; then pass "N1 no userConfig option lists allow"; else fail "N1 userConfig options list allow: $N1_OPTIONS"; fi
N1_DEFAULTS="$("$JQ" -r '[.userConfig // {} | .[] | .default | tostring | ascii_downcase | select(. == "allow")] | length' "$MANIFEST" 2>/dev/null)"
if [ "$N1_DEFAULTS" = 0 ]; then pass "N1 no userConfig default is allow"; else fail "N1 a userConfig default is allow"; fi

printf '%s\n' '== N2 dynamic: 40 rows under 27 option combinations =='
CORPUS=(
  'git status'
  'git push origin feature'
  'git push origin main'
  'git push -f origin feature'
  'git push --force origin main'
  'git push -d origin feature'
  'git push --mirror origin'
  'git push --prune origin'
  'git push --all origin'
  'git push origin :'
  'git push'
  'git push origin HEAD'
  'git push origin $B'
  'git push --frobnicate origin feature'
  'git -c alias.p=log p'
  'git -c core.x=y push origin feature'
  'GIT_DIR=/x git push origin feature'
  'sh -c "git push origin feature"'
  'git push $(echo origin) feature'
  'eval git push origin feature'
  'printf x | xargs git push origin'
  'echo "a;b" \; git push origin feature'
  'chronic git push origin feature'
  'timeout -s KILL 5 git push origin feature'
  'git send-pack origin feature'
  'git commit -m "feat: x"'
  'git commit -m nope'
  'git commit -m "$MSG"'
  'git commit --amend -m nope'
  'echo git push --force origin main'
  'cat x | sh'
  '(git push origin main)'
  'git push origin feature # to main'
  'bash scripts/test.sh && git push origin feature'
  'git checkout main && git push'
  'cd ../main-repo && git push'
  $'cat <<EOF | sh\ngit push -f\nEOF'
  "git commit -m \$'nope' && git push -f origin main"
  'git subtree push --prefix d origin feature'
  'git config --global alias.p "push -f"'
)
if [ "${#CORPUS[@]}" -eq 40 ]; then pass "N2 the core corpus has 40 rows"; else fail "N2 the core corpus has ${#CORPUS[@]} rows"; fi
N2_BAD=0
N2_RUNS=0
for main in ask deny off; do
  for force in ask deny off; do
    for commit in ask deny off; do
      GG_MAIN="$main"
      GG_FORCE="$force"
      GG_COMMIT="$commit"
      for command_data in "${CORPUS[@]}"; do
        gg_hook "$REPO_FEATURE" "$command_data"
        N2_RUNS=$((N2_RUNS + 1))
        case "$DECISION" in
          ask|deny|silent) ;;
          *) N2_BAD=$((N2_BAD + 1)); fail "N2 $main/$force/$commit '$command_data': not ask, deny or silent: exit $RC ${OUT:0:160}" ;;
        esac
        case "$OUT" in *'"allow"'*) N2_BAD=$((N2_BAD + 1)); fail "N2 $main/$force/$commit '$command_data': an allow" ;; esac
      done
    done
  done
done
GG_MAIN=ask
GG_FORCE=ask
GG_COMMIT=off
if [ "$N2_BAD" -eq 0 ] && [ "$N2_RUNS" -eq 1080 ]; then pass "N2 1080 runs: every one an ask, a deny or silence"; else fail "N2 $N2_BAD bad of $N2_RUNS runs"; fi

printf '%s\n' '== N2b the stored-options hook (--tighten-only): 40 rows under 27 option combinations =='
N2_BAD=0
N2_RUNS=0
for main in ask deny off; do
  for force in ask deny off; do
    for commit in ask deny off; do
      for command_data in "${CORPUS[@]}"; do
        gg_hook_args "$REPO_FEATURE" "$command_data" --tighten-only --push-to-main "$main" --force-push "$force" --commit-style "$commit"
        N2_RUNS=$((N2_RUNS + 1))
        case "$DECISION" in
          ask|deny|silent) ;;
          *) N2_BAD=$((N2_BAD + 1)); fail "N2b $main/$force/$commit '$command_data': not ask, deny or silent: exit $RC ${OUT:0:160}" ;;
        esac
        case "$OUT" in *'"allow"'*) N2_BAD=$((N2_BAD + 1)); fail "N2b $main/$force/$commit '$command_data': an allow" ;; esac
      done
    done
  done
done
if [ "$N2_BAD" -eq 0 ] && [ "$N2_RUNS" -eq 1080 ]; then pass "N2b 1080 runs: every one an ask, a deny or silence"; else fail "N2b $N2_BAD bad of $N2_RUNS runs"; fi

printf '%s\n' '== N3 an engine allow is never passed through =='
gg_hook_args "$REPO_FEATURE" 'git status' --push-to-main ask --force-push ask --commit-style off --adapter-policy "$HERE/fixtures/git-guard/allow-engine.sh"
gg_expect "N3 an engine allow verdict" deny:engine_output_invalid

printf '%s\n' '== N4 an allow option value =='
gg_hook_args "$REPO_FEATURE" 'git push -f origin feature' --push-to-main ask --force-push allow --commit-style off
gg_expect "N4 --force-push allow with a force-push" deny:option_invalid_force_push
case "$OUT" in *'"permissionDecision":"allow"'*) fail "N4 the output holds an allow" ;; *) pass "N4 the output holds no allow" ;; esac
gg_hook_args "$REPO_FEATURE" 'git push origin main' --push-to-main allow --force-push ask --commit-style off
gg_expect "N4 --push-to-main allow with a push to main" deny:option_invalid_push_to_main
gg_hook_args "$REPO_FEATURE" 'git commit -m nope' --push-to-main ask --force-push ask --commit-style allow
gg_expect "N4 --commit-style allow with a non-conventional commit" deny:option_invalid_commit_style

gg_finish NEVER-ALLOW
