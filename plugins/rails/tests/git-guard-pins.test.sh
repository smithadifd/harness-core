#!/bin/bash
# The git guard's lexer is the destructive-command guard's, copied byte for
# byte (design § 3.1). Each function is read from its `name() {` line to the
# first following line that is exactly `}`; each constant is its assignment
# line, and GUARD_RUNNER_PROGRAM runs through the line that is exactly `'`.
#   P-1 each pinned name is byte-equal in both files (one PASS per name).
#   P-2 a copy of the git guard with one byte changed in
#       split_command_segments fails P-1 by name (the suite tests its own
#       extraction).
#   P-3 the pinned names listed here equal the git guard's header list.
set -u
LC_ALL=C
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUITE=pins
. "$HERE/fixtures/git-guard/harness.sh"
DESTRUCTIVE="$HERE/../hooks/destructive-command-guard.sh"

PINNED="TRUSTED_ROOTS resolve_tool ROOTS_HINT GUARD_RUNNER_PROGRAM
collect_heredocs split_command_segments split_segments_skipping_comments
join_segments top_level_text trim_horizontal
skip_paren skip_backtick skip_brace skip_dquote split_shell_words
scan_shell_words is_assignment_word
first_free_close_paren strip_control_openers FUNCTION_HEAD_RE
NAME_PARENS_RE COPROC_NAME_RE redirect_skip_token redirect_target_end
strip_command_redirections
prefix_name skip_command_prefixes canon_wrapper is_git_spelling
RE_GIT_NAME RE_WRAPPER_NAME
nocase_equals is_shell_name r1_join_continuations r1_normalize
R1_COMMENT_LINE_RE r1_split_comment_continuations
r1_split_parameter_operators r1_tokenize r1_ansi_c_escape
env_scan_words REDIRECTION_WORD_RE REDIRECTION_OPERATOR_RE
env_split_in_option_list ARITHMETIC_COMMAND_RE collect_command_words
command_word_is_pattern"

# compare GIT_GUARD: prints one line per name, "same NAME" or "differs NAME"
# or "missing NAME FILE".
compare() {
  python3 -I - "$DESTRUCTIVE" "$1" $PINNED <<'PY'
import re, sys
def extract(lines, name):
    fn = re.compile(r"^" + re.escape(name) + r"\(\) \{")
    for i, line in enumerate(lines):
        if fn.match(line):
            for j in range(i + 1, len(lines)):
                if lines[j] == "}":
                    return "\n".join(lines[i:j + 1])
            return None
    const = re.compile(r"^" + re.escape(name) + r"=")
    for i, line in enumerate(lines):
        if const.match(line):
            if name == "GUARD_RUNNER_PROGRAM":
                for j in range(i + 1, len(lines)):
                    if lines[j] == "'":
                        return "\n".join(lines[i:j + 1])
                return None
            return line
    return None
a = open(sys.argv[1], encoding="utf-8").read().split("\n")
b = open(sys.argv[2], encoding="utf-8").read().split("\n")
for name in sys.argv[3:]:
    x, y = extract(a, name), extract(b, name)
    if x is None:
        print("missing %s destructive" % name)
    elif y is None:
        print("missing %s git" % name)
    else:
        print(("same %s" if x == y else "differs %s") % name)
PY
}

printf '%s\n' '== P-1 every pinned name is byte-equal =='
while read -r verdict name where; do
  case "$verdict" in
    same) pass "P-1 $name is byte-equal in both guards" ;;
    differs) fail "P-1 $name differs from the destructive guard's copy" ;;
    *) fail "P-1 $name is missing from the $where guard" ;;
  esac
done < <(compare "$GUARD")

printf '%s\n' '== P-2 the suite catches a one-byte change =='
replace_copy "$T/one-byte.sh" '  SEGMENTS=()
  SEGMENT_DELIMITERS=()
  HIDDEN_SEPARATOR=0' '  SEGMENTS=()
  SEGMENT_DELIMITERS=()
  HIDDEN_SEPARATOR=1'
P2="$(compare "$T/one-byte.sh" | grep -v '^same ')"
if [ "$P2" = "differs split_command_segments" ]; then
  pass "P-2 a one-byte change in split_command_segments fails P-1 by name, and only there"
else
  fail "P-2 expected exactly 'differs split_command_segments', got: $P2"
fi

printf '%s\n' "== P-3 the list here is the git guard's header list =="
HEADER="$(sed -n 's/^# pinned: //p' "$GUARD" | tr ' ' '\n' | sed '/^$/d' | sort)"
LISTED="$(printf '%s\n' $PINNED | sort)"
if [ "$HEADER" = "$LISTED" ]; then
  pass "P-3 the $(printf '%s\n' $PINNED | wc -l | tr -d ' ') pinned names equal the header list"
else
  fail "P-3 the header list differs: $(diff <(printf '%s\n' "$HEADER") <(printf '%s\n' "$LISTED") | tr '\n' ' ')"
fi

gg_finish PIN
