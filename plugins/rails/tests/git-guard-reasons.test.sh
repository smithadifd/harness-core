#!/bin/bash
# The git guard's reasons (design § 8): every code the guard can print has
# exactly one fixed hint; a hint holds no input marker (%, $ or a
# placeholder), no double quote, no backslash and never the word allow; the
# dependency hints name the trusted roots through the guard's own ROOTS_HINT;
# the adapter prints only its two templates; and the hook timeout in
# hooks.json is at least the engine budget plus 2 seconds. Every deny line,
# and no ask line, ends with the one sentence that says the deny is final and
# offers no way around it, and no hint invites a way around the guard.
set -u
LC_ALL=C
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUITE=reasons
. "$HERE/fixtures/git-guard/harness.sh"

printf '%s\n' '== every code has one hint =='
REPORT="$(python3 -I - "$GUARD" <<'PY'
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
code = "\n".join(l for l in text.split("\n") if not l.lstrip().startswith("#"))
emitted = set(re.findall(r"\b(?:record_code|policy_deny|emit_claude_deny|adapter_fail_closed)\s+([a-z_]+)\b", code))
emitted |= {"push_beside_" + c for c in re.findall(r"CARRIER_CLASS=([a-z_]+)", code)}
emitted |= {"push_beside_" + c for c in re.findall(r"for class in ([a-z_ ]+); do", code)[0].split()}
emitted |= set(re.findall(r"printed=(option_invalid_[a-z_]+)", code))
emitted |= set(re.findall(r"DEPENDENCY_CODE=(dependency_missing_[a-z_]+)", code))
emitted |= set(re.findall(r"emit_claude_deny (engine_[a-z_]+|adapter_input_invalid|invalid_configuration|dependency_missing_[a-z]+)", code))
emitted |= {"adapter_failed", "adapter_interrupted"}
emitted -= {"push_beside_"}
body = re.search(r"^verdict_hint\(\) \{\n(.*?)^\}", text, re.S | re.M).group(1)
labels = {}
problems = []
for m in re.finditer(r"^    ([a-z_|]+)\) VERDICT_HINT=(\"[^\"]*\"|'[^']*') ;;$", body, re.M):
    hint = m.group(2)[1:-1].replace("$ROOTS_HINT", "ROOTS")
    for label in m.group(1).split("|"):
        if label in labels:
            problems.append("two hints for " + label)
        labels[label] = hint
for name in sorted(emitted):
    if name not in labels:
        problems.append("no hint for " + name)
for name, hint in sorted(labels.items()):
    if re.search(r'["\\%$]', hint) or "{" in hint or re.search(r"(?i)(^|[^a-z0-9_])allow([^a-z0-9_]|$)", hint):
        problems.append("hint of %s holds a marker, quote, backslash or allow: %s" % (name, hint))
    if not hint:
        problems.append("empty hint for " + name)
print("codes=%d hints=%d" % (len(emitted), len(labels)))
for p in problems:
    print("PROBLEM " + p)
PY
)"
COUNTS="$(printf '%s\n' "$REPORT" | head -1)"
if printf '%s\n' "$REPORT" | grep -q '^PROBLEM'; then
  printf '%s\n' "$REPORT" | grep '^PROBLEM' | while IFS= read -r line; do printf 'FAIL: R1 %s\n' "${line#PROBLEM }"; done
  FAILURES=$((FAILURES + 1))
else
  pass "R1 every code the guard prints has one fixed hint with no marker, quote, backslash or allow ($COUNTS)"
fi

printf '%s\n' '== the dependency hints name the roots through ROOTS_HINT =='
ROOTS_LINE="$(grep '^ROOTS_HINT=' "$GUARD")"
ROOTS_VALUE="$(eval "printf '%s' ${ROOTS_LINE#ROOTS_HINT=}")"
for tool in jq wc tr git; do
  if grep -qF "dependency_missing_$tool) VERDICT_HINT=\"$tool was not found in \$ROOTS_HINT; install $tool in one of them\" ;;" "$GUARD"; then
    pass "R2 dependency_missing_$tool names the guard's own ROOTS_HINT"
  else
    fail "R2 dependency_missing_$tool does not use ROOTS_HINT"
  fi
done
if [ "$ROOTS_VALUE" = "/usr/bin, /bin, /usr/local/bin, /opt/homebrew/bin or /home""/linuxbrew/.linuxbrew/bin" ]; then
  pass "R2 ROOTS_HINT reads as the five roots"
else
  fail "R2 ROOTS_HINT reads: $ROOTS_VALUE"
fi

printf '%s\n' '== the adapter has two templates =='
TEMPLATES="$(grep -c "printf '{\"hookSpecificOutput\"" "$GUARD")"
ASK_T="$(grep -c "printf '{\"hookSpecificOutput\":{\"hookEventName\":\"PreToolUse\",\"permissionDecision\":\"ask\",\"permissionDecisionReason\":\"Git guard asks before this command (reason=%s): %s.\"}}\\\\n'" "$GUARD")"
if [ "$TEMPLATES" = 2 ] && [ "$ASK_T" = 1 ]; then
  pass "R3 exactly two templates: one ask and one deny (a deny, or an unattended ask)"
else
  fail "R3 template printf lines: $TEMPLATES, ask templates: $ASK_T"
fi

printf '%s\n' '== a printed reason is valid JSON with its fixed hint =='
NO_BYPASS='This deny is final: never use or offer a ! command at the prompt, a sandbox override, or a settings, permission or allowlist change to get around it; tell the person the guard denied it, and that running the command themselves is their own call.'
GG_COMMIT=deny
gg_hook "$REPO_FEATURE" 'git commit -m "$MSG"'
HINT="$(printf '%s' "$OUT" | "$JQ" -r '.hookSpecificOutput.permissionDecisionReason' 2>/dev/null)"
if [ "$HINT" = "Git guard denied the command (reason=commit_message_unreadable): the guard cannot read the commit message (a variable, a substitution or an unquoted heredoc), so it cannot check its conventional prefix; write the message literally, or to a file and use git commit -F. $NO_BYPASS" ]; then
  pass "R4 the deny reason, verbatim"
else
  fail "R4 the deny reason: $HINT"
fi
GG_COMMIT=off
gg_hook "$REPO_FEATURE" 'git push origin "$(echo main)"'
HINT="$(printf '%s' "$OUT" | "$JQ" -r '.hookSpecificOutput.permissionDecisionReason' 2>/dev/null)"
if [ "$HINT" = "Git guard asks before this command (reason=push_beside_substitution): a git push shares the command with a command substitution, which the guard does not read inside; run the push as its own plain command." ]; then
  pass "R4 the ask reason, verbatim, with no input in it"
else
  fail "R4 the ask reason: $HINT"
fi

printf '%s\n' '== the deny is final and invites no way around it =='
CODE_LINES="$(grep -v '^[[:space:]]*#' "$GUARD")"
DENY_LINES="$(printf '%s\n' "$CODE_LINES" | grep -F '"permissionDecision":"deny"')"
DENY_COUNT="$(printf '%s\n' "$DENY_LINES" | grep -c .)"
FINAL_COUNT="$(printf '%s\n' "$DENY_LINES" | grep -cF ". $NO_BYPASS\"}}")"
if [ "$DENY_COUNT" -eq 3 ] && [ "$FINAL_COUNT" -eq 3 ]; then
  pass "R6 all three deny lines (the template and the two crash-trap lines) end with the no-bypass sentence"
else
  fail "R6 deny lines: $DENY_COUNT, of which end with the no-bypass sentence: $FINAL_COUNT"
fi
if printf '%s\n' "$CODE_LINES" | grep -F '"permissionDecision":"ask"' | grep -qF "$NO_BYPASS"; then
  fail "R6 the ask template carries the no-bypass sentence"
else
  pass "R6 the ask template does not carry the no-bypass sentence"
fi
HINT_LINES="$(printf '%s\n' "$CODE_LINES" | grep -E '^    [a-z_|]+\) VERDICT_HINT=')"
BYPASS_HINTS="$(printf '%s\n' "$HINT_LINES" | grep -iE 'yourself|outside claude code|sandbox|settings|allowlist|permission|dangerously|(^|[^a-z])set (it|the) |confirm it')"
if [ -n "$HINT_LINES" ] && [ -z "$BYPASS_HINTS" ]; then
  pass "R6 no hint invites a way around the guard ($(printf '%s\n' "$HINT_LINES" | grep -c .) hint lines)"
else
  fail "R6 a hint invites a way around the guard: ${BYPASS_HINTS:-no hint lines found}"
fi
GG_COMMIT=off
GG_FORCE=deny
gg_hook "$REPO_FEATURE" 'git push --force origin feature'
HINT="$(printf '%s' "$OUT" | "$JQ" -r '.hookSpecificOutput.permissionDecisionReason' 2>/dev/null)"
if [ "$HINT" = "Git guard denied the command (reason=push_force): this is a force-push (--force, --force-with-lease, --force-if-includes, -f or a + refspec), which the rails plugin's force_push option guards. $NO_BYPASS" ]; then
  pass "R6 a force-push denied by force_push=deny: the hint names the option and offers no settings change"
else
  fail "R6 the force_push deny reason: $HINT"
fi
GG_FORCE=ask

printf '%s\n' '== the hook timeout covers the engine budget =='
BUDGET="$(sed -n 's/^TIMEOUT_SECONDS=\([0-9][0-9]*\)$/\1/p' "$GUARD")"
HOOK_TIMEOUT="$("$JQ" -r '[.hooks.PreToolUse[].hooks[] | select((.args // []) | any(endswith("/hooks/git-guard.sh")))][0].timeout' "$HOOKS")"
if [ -n "$BUDGET" ] && [ "$HOOK_TIMEOUT" -ge $((BUDGET + 2)) ]; then
  pass "R5 the hook timeout ($HOOK_TIMEOUT s) is at least the engine budget ($BUDGET s) plus 2 s"
else
  fail "R5 hook timeout $HOOK_TIMEOUT, engine budget $BUDGET"
fi

gg_finish REASON
