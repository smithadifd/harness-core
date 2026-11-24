#!/bin/bash
# The git guard's three options (design § 4): push_to_main and force_push
# default to ask, commit_style to off; each takes ask, deny or off, read only
# from the hook's own arguments. An empty value, or the unsubstituted
# ${user_config.KEY} text, is the default; any other value is invalid and
# denies by name when its gate fires, and never otherwise. A presumed push
# takes the stricter of the two push options.
# T: hooks.json runs the guard twice (WALKFIX-HC design § 4.3): the floor
# with the literal defaults on every install, and the stored-options hook,
# --tighten-only, only once all three options are stored; the verdict is the
# stricter of the two, never weaker than the floor (gg_pair).
# Command strings are inert JSON data and are never executed.
set -u
LC_ALL=C
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUITE=options
. "$HERE/fixtures/git-guard/harness.sh"

M1='git push origin main'
F1='git push -f origin feature'
K2='git commit -m nope'
P_U1='git push origin $B'

printf '%s\n' '== O-D: nothing set (every argument empty) gives the defaults =='
gg_hook_args "$REPO_FEATURE" "$M1" --push-to-main '' --force-push '' --commit-style ''
gg_expect "O-D1 push to main asks" ask:push_main_branch
gg_hook_args "$REPO_FEATURE" "$F1" --push-to-main '' --force-push '' --commit-style ''
gg_expect "O-D2 a force-push asks" ask:push_force
gg_hook_args "$REPO_FEATURE" "$K2" --push-to-main '' --force-push '' --commit-style ''
gg_expect "O-D3 a non-conventional commit is silent" silent

printf '%s\n' '== O-1: each gate at ask, deny and off =='
for value in ask deny off; do
  GG_MAIN="$value"
  if [ "$value" = off ]; then want=silent; else want="$value:push_main_branch"; fi
  gg_row "O-1 push_to_main=$value, M-1" "$want" "$M1"
  GG_MAIN=ask
  GG_FORCE="$value"
  if [ "$value" = off ]; then want=silent; else want="$value:push_force"; fi
  gg_row "O-1 force_push=$value, F-1" "$want" "$F1"
  GG_FORCE=ask
  GG_COMMIT="$value"
  if [ "$value" = off ]; then want=silent; else want="$value:commit_nonconventional"; fi
  gg_row "O-1 commit_style=$value, K-2" "$want" "$K2"
  GG_COMMIT=off
done

printf '%s\n' '== O-E1: one argument empty, the others explicit =='
gg_hook_args "$REPO_FEATURE" "$M1" --push-to-main '' --force-push off --commit-style off
gg_expect "O-E1 push_to_main empty is ask" ask:push_main_branch
gg_hook_args "$REPO_FEATURE" "$F1" --push-to-main off --force-push '' --commit-style off
gg_expect "O-E1 force_push empty is ask" ask:push_force
gg_hook_args "$REPO_FEATURE" "$K2" --push-to-main off --force-push off --commit-style ''
gg_expect "O-E1 commit_style empty is off" silent

printf '%s\n' '== O-LIT: the unsubstituted reference is the default =='
gg_hook_args "$REPO_FEATURE" "$M1" --push-to-main '${user_config.push_to_main}' --force-push '${user_config.force_push}' --commit-style '${user_config.commit_style}'
gg_expect "O-LIT push_to_main" ask:push_main_branch
gg_hook_args "$REPO_FEATURE" "$F1" --push-to-main '${user_config.push_to_main}' --force-push '${user_config.force_push}' --commit-style '${user_config.commit_style}'
gg_expect "O-LIT force_push" ask:push_force
gg_hook_args "$REPO_FEATURE" "$K2" --push-to-main '${user_config.push_to_main}' --force-push '${user_config.force_push}' --commit-style '${user_config.commit_style}'
gg_expect "O-LIT commit_style" silent
gg_hook_args "$REPO_FEATURE" "$F1" --push-to-main ask --force-push '${user_config.push_to_main}' --commit-style off
gg_expect "O-LIT another key's reference is invalid" deny:option_invalid_force_push

printf '%s\n' '== O-I: an invalid value denies by name when its gate fires =='
for bad in Ask allow ' ask' yes; do
  GG_MAIN="$bad"
  gg_row "O-I1 push_to_main='$bad', M-1" deny:option_invalid_push_to_main "$M1"
  gg_row "O-I4 push_to_main='$bad', git status" silent 'git status'
  gg_row "O-I4 push_to_main='$bad', a feature push" silent 'git push origin feature'
  GG_MAIN=ask
  GG_FORCE="$bad"
  gg_row "O-I2 force_push='$bad', F-1" deny:option_invalid_force_push "$F1"
  gg_row "O-I4 force_push='$bad', a feature push" silent 'git push origin feature'
  GG_FORCE=ask
  GG_COMMIT="$bad"
  gg_row "O-I3 commit_style='$bad', K-2" deny:option_invalid_commit_style "$K2"
  gg_row "O-I4 commit_style='$bad', a conventional commit" silent 'git commit -m "feat: x"'
  GG_COMMIT=off
done
GG_MAIN=deny
GG_FORCE=yes
gg_row "O-I5 a presumed push: an invalid force_push beats push_to_main=deny" deny:option_invalid_force_push "$P_U1"
GG_MAIN=ask
GG_FORCE=ask

printf '%s\n' '== O-P: a presumed push takes the stricter option =='
GG_MAIN=off
GG_FORCE=deny
gg_row "O-P1 push_to_main=off, force_push=deny, P-U1" deny:push_unreadable_word "$P_U1"
GG_FORCE=off
gg_row "O-P2 both off, P-U1" silent "$P_U1"
GG_MAIN=deny
GG_FORCE=ask
gg_row "O-P3 push_to_main=deny, force_push=ask, a force-push to main" deny:push_main_branch 'git push -f origin main'
GG_MAIN=ask
GG_FORCE=off
gg_row "O-P4 force_push=off, a force-push to a feature branch" silent "$F1"
gg_row "O-P5 force_push=off, push_to_main=ask, P-U1" ask:push_unreadable_word "$P_U1"
GG_FORCE=ask

printf '%s\n' '== O-OFF: the gates off =='
GG_MAIN=off
gg_row "O-OFF1 push_to_main=off: a push to main is silent" silent 'git push origin main'
gg_row "O-OFF2 push_to_main=off: a force-push to main still asks" ask:push_force 'git push --force origin main'
GG_MAIN=ask

printf '%s\n' '== O-PRJ: the environment never sets an option =='
gg_hook "$REPO_FEATURE" "$F1" CLAUDE_PLUGIN_OPTION_FORCE_PUSH=off CLAUDE_PLUGIN_OPTION_PUSH_TO_MAIN=off CLAUDE_PLUGIN_OPTION_COMMIT_STYLE=off
gg_expect "O-PRJ F-1 with the option variables set to off" ask:push_force
gg_hook "$REPO_FEATURE" "$M1" CLAUDE_PLUGIN_OPTION_FORCE_PUSH=off CLAUDE_PLUGIN_OPTION_PUSH_TO_MAIN=off
gg_expect "O-PRJ M-1 with the option variables set to off" ask:push_main_branch
GG_COMMIT=off
gg_hook "$REPO_FEATURE" "$K2" CLAUDE_PLUGIN_OPTION_COMMIT_STYLE=deny
gg_expect "O-PRJ a commit_style variable set to deny" silent

printf '%s\n' '== O-ARG: each option argument exactly once =='
gg_hook_args "$REPO_FEATURE" "$M1" --push-to-main ask --commit-style off
gg_expect "O-ARG --force-push missing" deny:invalid_configuration
gg_hook_args "$REPO_FEATURE" 'git status' --push-to-main ask --force-push ask --force-push ask --commit-style off
gg_expect "O-ARG --force-push repeated" deny:invalid_configuration
gg_hook_args "$REPO_FEATURE" 'git status' --push-to-main ask --force-push ask --commit-style
gg_expect "O-ARG --commit-style with no value" deny:invalid_configuration
gg_hook_args "$REPO_FEATURE" 'git status' --push-to-main ask --force-push ask --commit-style off --frobnicate
gg_expect "O-ARG an unknown argument" deny:invalid_configuration

printf '%s\n' '== O-ENG: the engine checks its own option arguments =='
gg_policy "$REPO_FEATURE" "$M1" --push-to-main ask --force-push ask
if [ "$OUT" = '{"decision":"deny","reason":"invalid_configuration"}' ]; then pass "O-ENG a missing engine option"; else fail "O-ENG a missing engine option: $OUT"; fi
gg_policy "$REPO_FEATURE" "$M1" --push-to-main ask --push-to-main ask --force-push ask --commit-style off
if [ "$OUT" = '{"decision":"deny","reason":"invalid_configuration"}' ]; then pass "O-ENG a repeated engine option"; else fail "O-ENG a repeated engine option: $OUT"; fi
gg_policy "$REPO_FEATURE" "$M1" --push-to-main Ask --force-push ask --commit-style off
if [ "$OUT" = '{"decision":"deny","reason":"invalid_configuration"}' ]; then pass "O-ENG an engine value outside ask, deny, off and invalid"; else fail "O-ENG an unknown engine value: $OUT"; fi
gg_policy "$REPO_FEATURE" "$M1" --push-to-main invalid --force-push ask --commit-style off
if [ "$OUT" = '{"decision":"deny","reason":"option_invalid_push_to_main"}' ]; then pass "O-ENG invalid reaches the engine as invalid"; else fail "O-ENG invalid: $OUT"; fi
gg_policy "$REPO_FEATURE" "$M1" --push-to-main ask --force-push ask --commit-style off
if [ "$OUT" = '{"decision":"ask","reason":"push_main_branch"}' ]; then pass "O-ENG the engine's own ask line"; else fail "O-ENG ask: $OUT"; fi

printf '%s\n' '== Rows moved from the source suite (GT:<line> is the source line) =='
GG_COMMIT=off
gg_row_at "$REPO_FEATURE" $'GT:1714 headless, bare defaults, no override' ask $'git push origin main'
gg_row_at "$REPO_FEATURE" $'GT:1715 headless, bare defaults, master branch' ask $'git push origin master'
GG_MAIN=off
gg_row_at "$REPO_FEATURE" $'GT:1716 O-OFF1 (GT:1716) push_to_main=off: a push to main' silent $'git push origin main'
GG_MAIN=ask
gg_row_at "$REPO_FEATURE" $'GT:1720 feature-branch push is never gated (sanity)' silent $'git push origin feature/x'
GG_COMMIT=off
GG_MAIN=ask
GG_FORCE=ask

printf '%s\n' '== T: the floor and the stored-options hook, as hooks.json runs them =='
PLAIN='git status'
for env_set in none all-off; do
  if [ "$env_set" = none ]; then
    set --
  else
    set -- CLAUDE_PLUGIN_OPTION_PUSH_TO_MAIN=off CLAUDE_PLUGIN_OPTION_FORCE_PUSH=off CLAUDE_PLUGIN_OPTION_COMMIT_STYLE=off
  fi
  gg_pair - - - "$M1" "$@"
  gg_expect "T1 none stored (option variables: $env_set), a push to main" ask:push_main_branch
  gg_pair_hook 1 skipped "T1 none stored ($env_set): the stored-options hook does not run"
  gg_pair - - - "$F1" "$@"
  gg_expect "T2 none stored (option variables: $env_set), a force-push" ask:push_force
  gg_pair - - - "$K2" "$@"
  gg_expect "T3 none stored (option variables: $env_set), a non-conventional commit" silent
done
set --
gg_pair deny - - "$M1"
gg_expect "T4 only push_to_main stored (deny): a push to main asks" ask:push_main_branch
gg_pair_hook 1 skipped "T4 the stored-options hook does not run while an option is unstored"
gg_pair ask ask off "$M1"
gg_expect "T5 all stored at the defaults, a push to main" ask:push_main_branch
gg_pair_hook 0 ask:push_main_branch "T5 the floor asks before a push to main"
gg_pair_hook 1 silent "T5 the stored-options hook is silent at the defaults (push to main)"
gg_pair ask ask off "$F1"
gg_expect "T5 all stored at the defaults, a force-push" ask:push_force
gg_pair_hook 1 silent "T5 the stored-options hook is silent at the defaults (force-push)"
gg_pair deny ask off "$M1"
gg_expect "T6 push_to_main=deny stored, a push to main" deny:push_main_branch
gg_pair_hook 1 deny:push_main_branch "T6 the deny is the stored-options hook's"
gg_pair ask deny off "$F1"
gg_expect "T7 force_push=deny stored, a force-push" deny:push_force
gg_pair_hook 1 deny:push_force "T7 the deny is the stored-options hook's"
gg_pair off ask off "$M1"
gg_expect "T8 push_to_main=off stored (v0.1.3), a push to main asks" ask:push_main_branch
gg_pair off off off "$F1"
gg_expect "T8 force_push=off stored (v0.1.3), a force-push asks" ask:push_force
gg_pair ask ask ask "$K2"
gg_expect "T9 commit_style=ask stored, a non-conventional commit" ask:commit_nonconventional
gg_pair ask ask deny "$K2"
gg_expect "T9 commit_style=deny stored, a non-conventional commit" deny:commit_nonconventional
gg_pair allow ask off "$M1"
gg_expect "T10 push_to_main=allow stored, a push to main" deny:option_invalid_push_to_main
gg_pair allow ask off "$PLAIN"
gg_expect "T10 push_to_main=allow stored, git status" silent
gg_pair ask yes off "$F1"
gg_expect "T10b force_push=yes stored, a force-push" deny:option_invalid_force_push
gg_pair ask yes off "$PLAIN"
gg_expect "T10b force_push=yes stored, git status" silent
gg_pair ask ask Ask "$K2"
gg_expect "T10c commit_style=Ask stored, a non-conventional commit" deny:option_invalid_commit_style
gg_pair ask ask Ask "$PLAIN"
gg_expect "T10c commit_style=Ask stored, git status" silent
gg_pair deny ask off "$P_U1"
gg_expect "T11 push_to_main=deny, force_push=ask stored, a presumed push" deny:push_unreadable_word

printf '%s\n' '== T24: --tighten-only once, in adapter mode only =='
gg_hook_args "$REPO_FEATURE" "$M1" --tighten-only --push-to-main deny --force-push ask --commit-style off
gg_expect "T24 control: --tighten-only once with push_to_main=deny" deny:push_main_branch
gg_hook_args "$REPO_FEATURE" 'git status' --tighten-only --tighten-only --push-to-main ask --force-push ask --commit-style off
gg_expect "T24 --tighten-only twice" deny:invalid_configuration
gg_hook_args "$REPO_FEATURE" 'git status' --push-to-main ask --tighten-only --force-push ask --commit-style off --tighten-only
gg_expect "T24 --tighten-only twice, apart" deny:invalid_configuration
for near in --tighten-only=1 --TIGHTEN-ONLY --tighten_only '--tighten-only ' -tighten-only --tighten; do
  gg_hook_args "$REPO_FEATURE" 'git status' "$near" --push-to-main ask --force-push ask --commit-style off
  gg_expect "T24 a near miss '$near'" deny:invalid_configuration
done
gg_policy "$REPO_FEATURE" "$M1" --tighten-only --push-to-main ask --force-push ask --commit-style off
if [ "$OUT" = '{"decision":"deny","reason":"invalid_configuration"}' ]; then pass "T24 --policy --tighten-only"; else fail "T24 --policy --tighten-only: $OUT"; fi
T24_INPUT="$("$JQ" -cn --arg cwd "$REPO_FEATURE" --arg command "$M1" '{cwd:$cwd,command:$command}')"
OUT="$(cd "$T" && printf '%s' "$T24_INPUT" | /usr/bin/env -i HOME="$GG_HOME" PATH="$GG_PATH" \
  /bin/bash --noprofile --norc -p "$GUARD" --tighten-only --policy --push-to-main ask --force-push ask --commit-style off 2>/dev/null)"
if [ "$OUT" = '{"decision":"deny","reason":"invalid_configuration"}' ]; then pass "T24 --tighten-only --policy"; else fail "T24 --tighten-only --policy: $OUT"; fi

printf '%s\n' '== Pipeline review belongs only to the fixed floor =='
for stored in ask deny off invalid; do
  gg_pair "$stored" "$stored" "$stored" 'cat x | sh'
  gg_expect "pipeline_floor stored=$stored" ask:pipeline_to_shell
  gg_pair_hook 0 ask:pipeline_to_shell "pipeline_floor asks once"
  gg_pair_hook 1 silent "pipeline_floor stored-options hook stays silent"
  gg_pair "$stored" "$stored" "$stored" 'cat x | sh' CLAUDE_SEAT_UNATTENDED=1
  gg_expect "pipeline_floor unattended stored=$stored" deny:pipeline_to_shell
  gg_pair_hook 1 silent "pipeline_floor unattended stored-options hook stays silent"
done
GG_MAIN=off
GG_FORCE=off
gg_row "pipeline_fixed both push options off" ask:pipeline_to_shell 'cat x | sh'
gg_row "R8 split opener with both push options off" ask:pipeline_to_shell $'echo ${\\\n fetch x | sh; }'

GG_MAIN=deny
GG_FORCE=deny
gg_row "F5 unreadable pipeline retains push presumption" deny:push_unreadable_segment 'git push --force origin main; echo x | cat >'
GG_MAIN=ask
GG_FORCE=ask
GG_COMMIT=deny
gg_row "F5 unreadable pipeline retains commit presumption" deny:commit_message_unreadable 'git commit -m "docs: x"; echo x | cat >'
GG_COMMIT=off
gg_row "F5 unreadable pipeline without stricter gate" ask:pipeline_to_shell 'echo x | cat >'
gg_both "F5 readable data pipeline" silent 'echo x | cat > out'

gg_finish OPTIONS
