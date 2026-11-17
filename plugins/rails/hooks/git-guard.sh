#!/bin/bash
# The rails plugin's git guard: a fail-closed git push and commit-style policy
# and its Claude Code PreToolUse adapter.
#
# What it claims. Three things are judged: a git push (the main gate and the
# force gate), a push the guard cannot read (presumed to be both), and, when
# the commit_style option is on, the first line of a top-level git commit
# message. Everything else gets no opinion: silence, and Claude Code's own
# permission flow decides.
#
# It never returns an allow. The engine's verdicts are deny, ask and
# no_opinion; the adapter prints an ask, a deny, or nothing. Every path that
# cannot finish its judgment (a missing tool, an engine crash, malformed
# engine output, engine stderr beside silence, an adapter fault, a signal,
# malformed hook input or an invalid option) denies by name.
#
# Options. push_to_main, force_push and commit_style each take ask, deny or
# off. They reach the guard only as its own arguments (--push-to-main,
# --force-push, --commit-style), which Claude Code fills from the person's
# user or managed settings; no environment variable and no file is read for
# them. A value outside the three denies when its gate fires, by name.
#
# Command text is data: it is never run, evaluated or sourced. Every program
# run by name (jq, wc, tr, git) comes from the fixed trusted roots of
# TRUSTED_ROOTS, never through PATH; every other program is run by its
# absolute path. A missing program denies as dependency_missing_<tool>.
# git runs only when a push names no destination branch, and then only as
# three read-only questions under a scrubbed environment.
#
# Threat model. This is a mistake catcher for an aligned agent: it catches a
# push to main or a force-push written by mistake or as ordinary shell. It does
# not defend against deliberate obfuscation (a push word spelled by glob,
# brace or escapes, a variable set in an earlier tool call), against a
# pushing script file, Makefile target or git alias defined in a configuration
# file, or against configuration written in an earlier tool call. The server
# (branch protection, a narrowly scoped token) is the real boundary.
#
# What the guard cannot read is presumed to be a push to main and a
# force-push, so it gets the stricter of the two options. With
# CLAUDE_SEAT_UNATTENDED=1 every ask becomes a deny; the loop's layer is
# dormant unless its own variables are set.
#
# The lexer pieces below are copied byte for byte from
# destructive-command-guard.sh and pinned by tests/git-guard-pins.test.sh. A
# fix to one lands in both files in the same change. The pinned names:
# pinned: TRUSTED_ROOTS resolve_tool ROOTS_HINT GUARD_RUNNER_PROGRAM
# pinned: collect_heredocs split_command_segments split_segments_skipping_comments
# pinned: join_segments top_level_text trim_horizontal
# pinned: skip_paren skip_backtick skip_brace skip_dquote split_shell_words
# pinned: scan_shell_words is_assignment_word
# pinned: first_free_close_paren strip_control_openers FUNCTION_HEAD_RE
# pinned: NAME_PARENS_RE COPROC_NAME_RE redirect_skip_token redirect_target_end
# pinned: strip_command_redirections
# pinned: prefix_name skip_command_prefixes canon_wrapper is_git_spelling
# pinned: RE_GIT_NAME RE_WRAPPER_NAME
# pinned: nocase_equals is_shell_name r1_join_continuations r1_normalize
# pinned: R1_COMMENT_LINE_RE r1_split_comment_continuations
# pinned: r1_split_parameter_operators r1_tokenize r1_ansi_c_escape
# pinned: env_scan_words REDIRECTION_WORD_RE REDIRECTION_OPERATOR_RE
# pinned: env_split_in_option_list ARITHMETIC_COMMAND_RE collect_command_words
# pinned: command_word_is_pattern
set -u
LC_ALL=C

# Interpreter startup state the person's environment could carry into the
# programs this guard starts. Every bash the guard starts runs with -p.
unset BASH_ENV ENV PERL5OPT PERL5LIB PERLLIB PERL5DB PERL5DB_THREADED PERLIO \
  PERLIO_DEBUG PERL_UNICODE PERL_ENCODING PERL_SIGNALS PERL_DL_NONLAZY PERL_HASH_SEED

# The crash trap. Every verdict sets VERDICT_STARTED, prints its one line with
# a builtin printf, then sets VERDICT_DONE before any audit work that forks.
# adapter_fail_closed is the body of the EXIT trap and of every signal trap:
# after a verdict it changes nothing; between the two flags it exits 3, a hook
# failure that onFailure "block" turns into a block; before any verdict it
# prints one fixed deny line. Its first statements are builtins only.
VERDICT_STARTED=0
VERDICT_DONE=0
adapter_fail_closed() { # CODE: adapter_failed | adapter_interrupted
  [ "$VERDICT_DONE" -eq 0 ] || exit 0
  [ "$VERDICT_STARTED" -eq 0 ] || exit 3
  VERDICT_STARTED=1
  case "$1" in
    adapter_interrupted)
      printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Git guard denied the command (reason=adapter_interrupted): the guard'"'"'s adapter was stopped by a signal; retry once."}}' || exit 3
      ;;
    *)
      printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Git guard denied the command (reason=adapter_failed): the guard'"'"'s adapter failed; retry once and report the code if it repeats."}}' || exit 3
      ;;
  esac
  VERDICT_DONE=1
  exit 0
}
trap 'adapter_fail_closed adapter_failed' EXIT
trap 'adapter_fail_closed adapter_interrupted' HUP 2>/dev/null || :
trap 'adapter_fail_closed adapter_interrupted' INT 2>/dev/null || :
trap 'adapter_fail_closed adapter_interrupted' QUIT 2>/dev/null || :
trap 'adapter_fail_closed adapter_interrupted' ILL 2>/dev/null || :
trap 'adapter_fail_closed adapter_interrupted' TRAP 2>/dev/null || :
trap 'adapter_fail_closed adapter_interrupted' ABRT 2>/dev/null || :
trap 'adapter_fail_closed adapter_interrupted' BUS 2>/dev/null || :
trap 'adapter_fail_closed adapter_interrupted' FPE 2>/dev/null || :
trap 'adapter_fail_closed adapter_interrupted' USR1 2>/dev/null || :
trap 'adapter_fail_closed adapter_interrupted' SEGV 2>/dev/null || :
trap 'adapter_fail_closed adapter_interrupted' USR2 2>/dev/null || :
trap 'adapter_fail_closed adapter_interrupted' PIPE 2>/dev/null || :
trap 'adapter_fail_closed adapter_interrupted' ALRM 2>/dev/null || :
trap 'adapter_fail_closed adapter_interrupted' TERM 2>/dev/null || :
trap 'adapter_fail_closed adapter_interrupted' XCPU 2>/dev/null || :
trap 'adapter_fail_closed adapter_interrupted' XFSZ 2>/dev/null || :
trap 'adapter_fail_closed adapter_interrupted' VTALRM 2>/dev/null || :
trap 'adapter_fail_closed adapter_interrupted' PROF 2>/dev/null || :
trap 'adapter_fail_closed adapter_interrupted' SYS 2>/dev/null || :

# The trusted roots, in order. The guard reads no variable, option or file for
# this list. The Linuxbrew root is spelled in two quoted parts on purpose.
TRUSTED_ROOTS=(/usr/bin /bin /usr/local/bin /opt/homebrew/bin "/home"/linuxbrew/.linuxbrew/bin)

# Sets RESOLVED_TOOL to ROOT/NAME for the first trusted root that holds it, or
# to "" (and returns 1) when none does. Builtins only: it forks nothing.
RESOLVED_TOOL=""
resolve_tool() { # NAME
  local root
  RESOLVED_TOOL=""
  for root in "${TRUSTED_ROOTS[@]}"; do
    if [ -x "$root/$1" ] && [ ! -d "$root/$1" ]; then
      RESOLVED_TOOL="$root/$1"
      return 0
    fi
  done
  return 1
}

# The absolute path of each tool run by name, set by check_tools.
JQ_BIN=""
WC_BIN=""
TR_BIN=""
GIT_BIN=""

# A bare git in this file runs the trusted-root program, or fails like a
# missing command when its tier did not resolve it.
git() {
  [ -n "$GIT_BIN" ] || return 127
  "$GIT_BIN" "$@"
}

# The required programs of each tier, in the order they are checked. A bare
# name is resolved from TRUSTED_ROOTS; CODE:PATH is an absolute program that
# must be executable and not a directory, denied as dependency_missing_CODE.
# The adapter tier also checks the runner (/usr/bin/perl, or --timeout-runner)
# and the engine file itself. The target tier runs only when a push names no
# destination branch and the guard must ask git which branch is current.
ADAPTER_TOOLS=(jq)
ENGINE_TOOLS=(jq wc tr)
TARGET_TOOLS=(git env:/usr/bin/env)

# Checks one tier's list in order. Sets MISSING_TOOL_CODE to the first missing
# program's code and returns 1; returns 0 when every program is present.
# Builtins only.
MISSING_TOOL_CODE=""
check_tools() { # ENTRY...
  local entry code path
  MISSING_TOOL_CODE=""
  for entry in "$@"; do
    case "$entry" in
      *:*)
        code="${entry%%:*}"
        path="${entry#*:}"
        if [ ! -x "$path" ] || [ -d "$path" ]; then
          MISSING_TOOL_CODE="$code"
          return 1
        fi
        ;;
      *)
        if ! resolve_tool "$entry"; then
          MISSING_TOOL_CODE="$entry"
          return 1
        fi
        case "$entry" in
          jq) JQ_BIN="$RESOLVED_TOOL" ;;
          wc) WC_BIN="$RESOLVED_TOOL" ;;
          tr) TR_BIN="$RESOLVED_TOOL" ;;
          git) GIT_BIN="$RESOLVED_TOOL" ;;
          *) MISSING_TOOL_CODE="$entry"; return 1 ;;
        esac
        ;;
    esac
  done
  return 0
}

# Sets DEPENDENCY_CODE to dependency_missing_<code> for MISSING_TOOL_CODE.
# Only these literals are ever assigned.
DEPENDENCY_CODE=""
dependency_code() {
  case "$MISSING_TOOL_CODE" in
    jq) DEPENDENCY_CODE=dependency_missing_jq ;;
    wc) DEPENDENCY_CODE=dependency_missing_wc ;;
    tr) DEPENDENCY_CODE=dependency_missing_tr ;;
    git) DEPENDENCY_CODE=dependency_missing_git ;;
    env) DEPENDENCY_CODE=dependency_missing_env ;;
    perl) DEPENDENCY_CODE=dependency_missing_perl ;;
    *) DEPENDENCY_CODE=dependency_missing_engine ;;
  esac
}

SELF="${BASH_SOURCE[0]}"
MODE="adapter"
POLICY_PATH="$SELF"
TIMEOUT_RUNNER="/usr/bin/perl"
# The engine's wall-time budget: at least 2 s under the hook's own timeout in
# hooks.json, so an expiry is always this adapter's named denial.
TIMEOUT_SECONDS=8
ARG_ERROR=0
# The three option arguments, each taken exactly once.
OPT_MAIN=""
OPT_FORCE=""
OPT_COMMIT=""
SEEN_MAIN=0
SEEN_FORCE=0
SEEN_COMMIT=0
# The adapter's normalized input, kept so the guard log can name its cwd and
# the owed record can name the command; empty until normalized.
GUARD_LOG_INPUT=""

# --adapter-policy and --timeout-runner are test seams of the adapter only;
# hooks.json passes neither.
while [ "$#" -gt 0 ]; do
  case "$1" in
    --policy)
      [ "$MODE" = "adapter" ] || ARG_ERROR=1
      MODE="policy"
      shift
      ;;
    --push-to-main|--force-push|--commit-style|--adapter-policy|--timeout-runner)
      [ "$#" -ge 2 ] || { ARG_ERROR=1; break; }
      case "$1" in
        --push-to-main) SEEN_MAIN=$((SEEN_MAIN + 1)); OPT_MAIN="$2" ;;
        --force-push) SEEN_FORCE=$((SEEN_FORCE + 1)); OPT_FORCE="$2" ;;
        --commit-style) SEEN_COMMIT=$((SEEN_COMMIT + 1)); OPT_COMMIT="$2" ;;
        --adapter-policy) POLICY_PATH="$2" ;;
        --timeout-runner) TIMEOUT_RUNNER="$2" ;;
      esac
      shift 2
      ;;
    *) ARG_ERROR=1; break ;;
  esac
done
{ [ "$SEEN_MAIN" -eq 1 ] && [ "$SEEN_FORCE" -eq 1 ] && [ "$SEEN_COMMIT" -eq 1 ]; } || ARG_ERROR=1

# ---------------------------------------------------------------------------
# Verdict text.
# ---------------------------------------------------------------------------

# The trusted roots as a hint names them. The Linuxbrew root is spelled in two
# quoted parts on purpose; the printed text is the plain path.
ROOTS_HINT="/usr/bin, /bin, /usr/local/bin, /opt/homebrew/bin or /home""/linuxbrew/.linuxbrew/bin"

# Sets VERDICT_HINT to the one fixed sentence for a reason code. A hint never
# carries input or a path from input, and holds no character that needs JSON
# escaping.
VERDICT_HINT=""
verdict_hint() {
  case "$1" in
    push_main_branch) VERDICT_HINT="this push updates main or master; confirm it, or set the rails plugin's push_to_main option" ;;
    push_main_unresolved) VERDICT_HINT='the guard could not tell which branch this push updates (the current branch, the folder or the repository is unknown), so it treats it as a push to main' ;;
    push_config_remap) VERDICT_HINT="git configuration (push.default, or a remote's push or mirror setting) decides what this push updates, so the guard treats it as a push to main" ;;
    push_all_branches) VERDICT_HINT='--all, --branches or --mirror pushes every branch, main included' ;;
    push_matching_refspec|push_pattern_refspec) VERDICT_HINT='a matching or wildcard refspec can update main' ;;
    push_force) VERDICT_HINT="this is a force-push (--force, --force-with-lease, --force-if-includes, -f or a + refspec); confirm it, or set the rails plugin's force_push option" ;;
    push_delete) VERDICT_HINT='this deletes a remote branch (--delete, -d or a :branch refspec)' ;;
    push_mirror) VERDICT_HINT='--mirror force-updates and deletes every ref on the remote' ;;
    push_prune) VERDICT_HINT='--prune deletes remote branches' ;;
    push_beside_substitution) VERDICT_HINT='a git push shares the command with a command substitution, which the guard does not read inside; run the push as its own plain command' ;;
    push_beside_backtick) VERDICT_HINT='a git push shares the command with a backtick substitution, which the guard does not read inside; run the push as its own plain command' ;;
    push_beside_process_substitution) VERDICT_HINT='a git push shares the command with a process substitution, which the guard does not read inside; run the push as its own plain command' ;;
    push_beside_heredoc) VERDICT_HINT='a git push shares the command with a heredoc or here-string that feeds more than cat, tee, echo, printf, git commit -F - or gh --body-file -, which the guard does not read inside; run the push as its own plain command, and write text that mentions it to a file' ;;
    push_beside_eval) VERDICT_HINT='a git push shares the command with eval, which the guard does not read inside; run the push as its own plain command' ;;
    push_beside_alias) VERDICT_HINT='a git push shares the command with alias, which the guard does not read inside; run the push as its own plain command' ;;
    push_beside_env_split) VERDICT_HINT='a git push shares the command with env -S, which the guard does not read inside; run the push as its own plain command' ;;
    push_beside_variable_command) VERDICT_HINT='a git push shares the command with a command word built from a variable, expansion or pattern, which the guard does not read inside; run the push as its own plain command' ;;
    push_beside_git_foreach) VERDICT_HINT='a git push shares the command with git submodule foreach, which the guard does not read inside; run the push as its own plain command' ;;
    push_beside_git_rebase_exec) VERDICT_HINT='a git push shares the command with git rebase --exec, which the guard does not read inside; run the push as its own plain command' ;;
    push_beside_git_bisect_run) VERDICT_HINT='a git push shares the command with git bisect run, which the guard does not read inside; run the push as its own plain command' ;;
    push_beside_source) VERDICT_HINT='a git push shares the command with source or ., which the guard does not read inside; run the push as its own plain command' ;;
    push_beside_trap) VERDICT_HINT='a git push shares the command with trap, which the guard does not read inside; run the push as its own plain command' ;;
    push_beside_xargs) VERDICT_HINT='a git push shares the command with xargs, which the guard does not read inside; run the push as its own plain command' ;;
    push_beside_find_exec) VERDICT_HINT='a git push shares the command with find -exec, -execdir or -ok, which the guard does not read inside; run the push as its own plain command' ;;
    push_beside_shell) VERDICT_HINT='a git push shares the command with a shell or shell runner, which the guard does not read inside; run the push as its own plain command' ;;
    push_beside_flock) VERDICT_HINT='a git push shares the command with flock, which the guard does not read inside; run the push as its own plain command' ;;
    push_beside_interpreter) VERDICT_HINT='a git push shares the command with an interpreter running inline code, which the guard does not read inside; run the push as its own plain command' ;;
    push_hidden_separator) VERDICT_HINT='this command mentions push and holds a quoted or escaped separator or a line continuation where the guard cannot be sure of the quoting; run the push as its own one-line command' ;;
    push_unreadable_word|push_unreadable_segment) VERDICT_HINT='a word of the push is built from a variable, substitution, glob, brace or group, or does not split, so the guard cannot read where it goes; spell it literally' ;;
    push_unknown_option|push_unknown_git_option) VERDICT_HINT='the push uses an option the guard does not know' ;;
    push_unsupported_prefix) VERDICT_HINT='the push runs behind a wrapper option the guard does not read' ;;
    push_beside_wrapper) VERDICT_HINT='git push runs behind a command the guard does not list' ;;
    push_config_override) VERDICT_HINT='the push sets git configuration with -c or --config-env' ;;
    push_git_environment) VERDICT_HINT='a GIT_ variable is set on this command line beside a push' ;;
    inherited_git_environment) VERDICT_HINT='a GIT_ variable that selects a repository or injects configuration is set in the session' ;;
    git_config_injection) VERDICT_HINT='git configuration that can define an alias or change a push is injected on this command line' ;;
    push_alias_definition) VERDICT_HINT='this command defines a git alias beside a push' ;;
    push_send_pack|push_receive_pack|push_subtree) VERDICT_HINT='git send-pack, --receive-pack or --exec, or git subtree push, is not read by the guard' ;;
    push_command_too_long) VERDICT_HINT='the command is over 6000 bytes; push in a shorter command of its own' ;;
    commit_nonconventional) VERDICT_HINT="the commit message's first line does not start with a conventional prefix: feat, fix, docs, refactor, chore, test, ci, build, perf, style or revert, an optional scope in parentheses, an optional !, then a colon and a space" ;;
    commit_message_unreadable) VERDICT_HINT='the guard cannot read the commit message (a variable, a substitution or an unquoted heredoc), so it cannot check its conventional prefix; write the message literally, or to a file and use git commit -F' ;;
    option_invalid_push_to_main) VERDICT_HINT="the rails plugin's push_to_main option holds a value other than ask, deny or off; set it in /config" ;;
    option_invalid_force_push) VERDICT_HINT="the rails plugin's force_push option holds a value other than ask, deny or off; set it in /config" ;;
    option_invalid_commit_style) VERDICT_HINT="the rails plugin's commit_style option holds a value other than ask, deny or off; set it in /config" ;;
    unsupported_syntax) VERDICT_HINT='the command is over 6000 bytes or has an unbalanced substitution or quote; split or shorten it' ;;
    invalid_configuration) VERDICT_HINT="the guard's working folder or options are invalid; this is a configuration fault, not the command" ;;
    dependency_missing_jq) VERDICT_HINT="jq was not found in $ROOTS_HINT; install jq in one of them" ;;
    dependency_missing_wc) VERDICT_HINT="wc was not found in $ROOTS_HINT; install wc in one of them" ;;
    dependency_missing_tr) VERDICT_HINT="tr was not found in $ROOTS_HINT; install tr in one of them" ;;
    dependency_missing_git) VERDICT_HINT="git was not found in $ROOTS_HINT; install git in one of them" ;;
    dependency_missing_perl) VERDICT_HINT='/usr/bin/perl is missing; the guard runs its engine under it' ;;
    dependency_missing_engine) VERDICT_HINT="the guard's own policy file is missing; reinstall the rails plugin" ;;
    dependency_missing_env) VERDICT_HINT='/usr/bin/env is missing' ;;
    malformed_normalized_input|extraction_failure|adapter_input_invalid) VERDICT_HINT='the hook input could not be read' ;;
    engine_timeout) VERDICT_HINT='the guard did not finish within 8 seconds; retry the same command once and keep commands under about 4 KB' ;;
    engine_failed|engine_output_invalid|engine_stderr_on_silence) VERDICT_HINT="the guard's engine failed; retry once and report the code if it repeats" ;;
    adapter_failed) VERDICT_HINT="the guard's adapter failed; retry once and report the code if it repeats" ;;
    adapter_interrupted) VERDICT_HINT="the guard's adapter was stopped by a signal; retry once" ;;
    *) VERDICT_HINT='see the reason code' ;;
  esac
}

# Audit records: EVENT and CODE, on stderr, the fixed audit sink. A record
# holds no command, path, cwd or input data.
audit_record() {
  local code="$2"
  case "$code" in ''|*[!a-z_]*) code=engine_output_invalid ;; esac
  printf '%s event=%s reason=%s\n' "$(/bin/date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || printf 'time-unavailable')" "$1" "$code" >&2
}

# ---------------------------------------------------------------------------
# The dormant loop layer: the guard log and the owed record. Both are armed
# only by the loop's own SEAT_SCRATCH variable, and neither ever changes a
# verdict, the output or the exit code.
# ---------------------------------------------------------------------------

# The persistent guard log writer (core Perl only). Arguments: BASE NAME EVENT
# CODE CLASS CWD. The file is opened for append with O_NOFOLLOW and
# O_NONBLOCK, created 0600, and written only when it is a regular file. One
# JSON object per line with exactly the keys timestamp, event, code, class,
# cwd. The whole append is bounded by a 0.5 s alarm.
# The single-quoted Perl program must retain Perl's own $ variables.
# shellcheck disable=SC2016
GUARD_LOG_WRITER='
use strict;
use Fcntl qw(O_WRONLY O_APPEND O_CREAT O_NOFOLLOW O_NONBLOCK);
use POSIX qw(strftime);
use Time::HiRes ();
$SIG{ALRM} = sub { exit 1 };
Time::HiRes::alarm(0.5);
my ($base, $name, $event, $code, $class, $cwd) = @ARGV;
exit 1 unless defined $cwd;
exit 1 unless $event =~ /\A(?:ask|deny)\z/ && $code =~ /\A[a-z_]+\z/;
exit 1 unless $class =~ /\A(?:git-push|text-match:conventional-commit|other)\z/;
exit 1 unless $name =~ /\A[a-z.]+\z/ && $base =~ m{\A/} && -d $base;
umask 077;
my %short = ("\x08" => "\\b", "\t" => "\\t", "\n" => "\\n", "\x0c" => "\\f", "\r" => "\\r");
sub json_string {
  my $s = shift;
  $s =~ s/(["\\])/\\$1/g;
  $s =~ s/([\x00-\x1f])/exists $short{$1} ? $short{$1} : sprintf("\\u%04x", ord $1)/ge;
  return "\"$s\"";
}
my $line = "{\"timestamp\":" . json_string(strftime("%Y-%m-%dT%H:%M:%SZ", gmtime))
  . ",\"event\":" . json_string($event)
  . ",\"code\":" . json_string($code)
  . ",\"class\":" . json_string($class)
  . ",\"cwd\":" . json_string($cwd) . "}\n";
sysopen(my $fh, "$base/$name", O_WRONLY | O_APPEND | O_CREAT | O_NOFOLLOW | O_NONBLOCK, 0600) or exit 1;
exit 1 unless -f $fh;
my $wrote = syswrite($fh, $line);
close($fh);
exit((defined $wrote && $wrote == length $line) ? 0 : 1);
'

# The owed record writer (core Perl only). Arguments: BASE REASON COMMAND. One
# line appended to BASE/owed.log: the UTC time, the original ask reason and
# the first 200 bytes of the command, tabs and newlines flattened to spaces,
# tab-separated and LF-ended. The same no-follow, regular-file-only, 0.5 s
# rules as the guard log.
# shellcheck disable=SC2016
OWED_WRITER='
use strict;
use Fcntl qw(O_WRONLY O_APPEND O_CREAT O_NOFOLLOW O_NONBLOCK);
use POSIX qw(strftime);
use Time::HiRes ();
$SIG{ALRM} = sub { exit 1 };
Time::HiRes::alarm(0.5);
my ($base, $reason, $command) = @ARGV;
exit 1 unless defined $command && $base =~ m{\A/} && -d $base;
umask 077;
$reason =~ tr/\t\n/  /;
$command = substr($command, 0, 200);
$command =~ tr/\t\n/  /;
my $line = strftime("%Y-%m-%dT%H:%M:%SZ", gmtime) . "\t" . $reason . "\t" . $command . "\n";
sysopen(my $fh, "$base/owed.log", O_WRONLY | O_APPEND | O_CREAT | O_NOFOLLOW | O_NONBLOCK, 0600) or exit 1;
exit 1 unless -f $fh;
my $wrote = syswrite($fh, $line);
close($fh);
exit((defined $wrote && $wrote == length $line) ? 0 : 1);
'

# Sets GUARD_LOG_CLASS for a reason code: a pure function of the code.
GUARD_LOG_CLASS=""
guard_log_class() {
  case "$1" in
    commit_*) GUARD_LOG_CLASS=text-match:conventional-commit ;;
    push_*|inherited_git_environment|git_config_injection) GUARD_LOG_CLASS=git-push ;;
    *) GUARD_LOG_CLASS=other ;;
  esac
}

# The guard log, armed only: an attended session whose SEAT_SCRATCH is an
# existing absolute directory, read live at every append. There is no default
# location. The line never holds the command or any argument text.
guard_log_append() { # EVENT CODE
  local cwd=""
  [ "${CLAUDE_SEAT_UNATTENDED:-}" = "1" ] && return 0
  [ -x /usr/bin/perl ] || return 0
  case "${SEAT_SCRATCH:-}" in
    /*) [ -d "$SEAT_SCRATCH" ] || return 0 ;;
    *) return 0 ;;
  esac
  guard_log_class "$2"
  if [ -n "$GUARD_LOG_INPUT" ] && [ -n "$JQ_BIN" ]; then
    cwd="$(printf '%s' "$GUARD_LOG_INPUT" | HOME=/dev/null "$JQ_BIN" -j '.cwd' 2>/dev/null)" || cwd=""
  fi
  /usr/bin/perl -e "$GUARD_LOG_WRITER" -- "$SEAT_SCRATCH" guard.log "$1" "$2" "$GUARD_LOG_CLASS" "$cwd" </dev/null >/dev/null 2>&1 || :
  return 0
}

# The owed record, written only when an ask was turned into a deny because the
# session is unattended. A failure prints one fixed stderr line and never
# changes the verdict.
owed_record() { # ORIGINAL_REASON
  local command=""
  case "${SEAT_SCRATCH:-}" in
    '')
      printf '%s\n' "rails git-guard: unattended denial not recorded: SEAT_SCRATCH is unset or empty" >&2
      return 0
      ;;
    /*) ;;
    *)
      printf '%s\n' "rails git-guard: unattended denial not recorded: SEAT_SCRATCH is not an existing absolute directory: $SEAT_SCRATCH" >&2
      return 0
      ;;
  esac
  if [ ! -d "$SEAT_SCRATCH" ]; then
    printf '%s\n' "rails git-guard: unattended denial not recorded: SEAT_SCRATCH is not an existing absolute directory: $SEAT_SCRATCH" >&2
    return 0
  fi
  if [ -n "$GUARD_LOG_INPUT" ] && [ -n "$JQ_BIN" ]; then
    command="$(printf '%s' "$GUARD_LOG_INPUT" | HOME=/dev/null "$JQ_BIN" -j '.command' 2>/dev/null)" || command=""
  fi
  if [ ! -x /usr/bin/perl ] || ! /usr/bin/perl -e "$OWED_WRITER" -- "$SEAT_SCRATCH" "$1" "$command" </dev/null >/dev/null 2>&1; then
    printf '%s\n' "rails git-guard: unattended denial not recorded: could not append to $SEAT_SCRATCH/owed.log" >&2
  fi
  return 0
}

# ---------------------------------------------------------------------------
# The adapter's two templates.
# ---------------------------------------------------------------------------

# Prints one verdict. DECISION is ask or deny; the two templates below are the
# only lines this adapter prints, each by one builtin printf between the two
# verdict flags, then the audit record and the dormant layer, which fork. An
# ask in an unattended session (CLAUDE_SEAT_UNATTENDED exactly 1) is printed
# as a deny with the unattended clause, and its owed record is written.
emit_claude_verdict() { # DECISION CODE
  local decision="$1" code="${2:-}" hint converted=0
  case "$code" in ''|*[!a-z_]*) decision=deny; code=engine_output_invalid ;; esac
  [ "$decision" = ask ] || decision=deny
  verdict_hint "$code"
  hint="$VERDICT_HINT"
  if [ "$decision" = ask ] && [ "${CLAUDE_SEAT_UNATTENDED:-}" = "1" ]; then
    decision=deny
    converted=1
    hint="the session is unattended, so a confirmation is a denial; $VERDICT_HINT"
  fi
  VERDICT_STARTED=1
  if [ "$decision" = ask ]; then
    printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"Git guard asks before this command (reason=%s): %s."}}\n' "$code" "$hint" || exit 3
  else
    printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Git guard denied the command (reason=%s): %s."}}\n' "$code" "$hint" || exit 3
  fi
  VERDICT_DONE=1
  audit_record "git-guard-$decision" "$code"
  if [ "$converted" -eq 1 ]; then
    owed_record "Git guard asks before this command (reason=$code): $VERDICT_HINT."
  else
    guard_log_append "$decision" "$code"
  fi
  exit 0
}

emit_claude_deny() {
  emit_claude_verdict deny "${1:-}"
}

emit_claude_deny_dependency() {
  dependency_code
  emit_claude_deny "$DEPENDENCY_CODE"
}

# ---------------------------------------------------------------------------
# The engine's verdicts.
# ---------------------------------------------------------------------------

emit_policy() {
  printf '{"decision":"%s","reason":"%s"}\n' "$1" "$2"
}

# The command's verdict so far: BEST_LEVEL is 0 (no opinion), 1 (ask), 2
# (deny) or 3 (deny for an invalid option, which wins a tie); BEST_CODE is
# the first code that reached it, in reading order.
BEST_LEVEL=0
BEST_CODE=""
PRESUMED_SEEN=0
# 1 once the gates have started segmenting the command (§ 3.2): a segmenter
# refusal then gives each live gate its unreadable verdict.
GATES_LIVE=0
HAS_PUSH=0
HAS_COMMIT=0
HAS_MARKER=0
COMMIT_LIVE=0

LEVEL=0
option_level() { # VALUE
  case "$1" in
    ask) LEVEL=1 ;;
    deny) LEVEL=2 ;;
    invalid) LEVEL=3 ;;
    *) LEVEL=0 ;;
  esac
}

# Sets GATE to main, force, commit or presumed for a verdict code.
GATE=""
code_gate() { # CODE
  case "$1" in
    push_main_branch|push_main_unresolved|push_config_remap|push_all_branches|push_matching_refspec|push_pattern_refspec) GATE=main ;;
    push_force|push_delete|push_mirror|push_prune) GATE=force ;;
    commit_nonconventional|commit_message_unreadable) GATE=commit ;;
    *) GATE=presumed ;;
  esac
}

# Records one code: its gate's option decides its level; a presumed code takes
# the stricter of push_to_main and force_push. An invalid option is a deny
# named for the option. The first code to reach the highest level is kept.
record_code() { # CODE
  local code="$1" level printed="$1" main_level force_level
  code_gate "$code"
  case "$GATE" in
    main)
      option_level "$OPT_MAIN"; level="$LEVEL"
      [ "$level" -lt 3 ] || printed=option_invalid_push_to_main
      ;;
    force)
      option_level "$OPT_FORCE"; level="$LEVEL"
      [ "$level" -lt 3 ] || printed=option_invalid_force_push
      ;;
    commit)
      option_level "$OPT_COMMIT"; level="$LEVEL"
      [ "$level" -lt 3 ] || printed=option_invalid_commit_style
      ;;
    *)
      PRESUMED_SEEN=1
      option_level "$OPT_MAIN"; main_level="$LEVEL"
      option_level "$OPT_FORCE"; force_level="$LEVEL"
      if [ "$main_level" -ge "$force_level" ]; then
        level="$main_level"
        [ "$level" -lt 3 ] || printed=option_invalid_push_to_main
      else
        level="$force_level"
        [ "$level" -lt 3 ] || printed=option_invalid_force_push
      fi
      ;;
  esac
  if [ "$level" -gt "$BEST_LEVEL" ]; then
    BEST_LEVEL="$level"
    BEST_CODE="$printed"
  fi
  return 0
}

# Prints the command's verdict and exits.
finish_verdict() {
  case "$BEST_LEVEL" in
    0) emit_policy no_opinion not_gated ;;
    1) audit_record git-guard-ask "$BEST_CODE"; emit_policy ask "$BEST_CODE" ;;
    *) audit_record git-guard-deny "$BEST_CODE"; emit_policy deny "$BEST_CODE" ;;
  esac
  exit 0
}

policy_no_opinion() {
  emit_policy no_opinion not_gated
  exit 0
}

# A fault: deny by name. The copied segmenter and redirection stripper call
# policy_deny unsupported_syntax on text they cannot balance; once the gates
# have started (§ 3.2), that gives each live gate its unreadable verdict and
# the stricter is printed: a push word is presumed (push_unreadable_segment),
# or with no push word a § 7.2 marker is (git_config_injection); a commit word
# with commit style on fails the commit check (commit_message_unreadable).
policy_deny() {
  if [ "$1" = unsupported_syntax ] && [ "$GATES_LIVE" -eq 1 ]; then
    GATES_LIVE=0
    if [ "$HAS_PUSH" -eq 1 ]; then
      record_code push_unreadable_segment
    elif [ "$HAS_MARKER" -eq 1 ]; then
      record_code git_config_injection
    fi
    if [ "$COMMIT_LIVE" -eq 1 ]; then
      record_code commit_message_unreadable
    fi
    finish_verdict
  fi
  audit_record git-guard-deny "$1"
  emit_policy deny "$1"
  exit 0
}

policy_deny_dependency() {
  dependency_code
  policy_deny "$DEPENDENCY_CODE"
}

# ---------------------------------------------------------------------------
# The lexer, copied byte for byte from destructive-command-guard.sh.
# ---------------------------------------------------------------------------

SEGMENTS=()
SEGMENT_DELIMITERS=()
HEREDOC_DELIMS=()
HEREDOC_STRIP_TABS=()
TRIMMED=""

collect_heredocs() {
  local input="$1" length i=0 j ch next state="plain" delimiter strip_tabs
  HEREDOC_DELIMS=()
  HEREDOC_STRIP_TABS=()
  length=${#input}
  while [ "$i" -lt "$length" ]; do
    ch="${input:$i:1}"
    case "$state" in
      single) [ "$ch" = "'" ] && state="plain" ;;
      double)
        if [ "$ch" = '"' ]; then
          state="plain"
        elif [ "$ch" = "\\" ]; then
          i=$((i + 1))
        fi
        ;;
      plain)
        case "$ch" in
          "'") state="single" ;;
          '"') state="double" ;;
          "\\") i=$((i + 1)) ;;
          '<')
            if [ "${input:$((i + 1)):1}" = '<' ] && [ "${input:$((i + 2)):1}" = '<' ]; then
              # A here-string (<<<) carries no heredoc body.
              i=$((i + 2))
            elif [ "${input:$((i + 1)):1}" = '<' ]; then
              j=$((i + 2))
              strip_tabs=0
              if [ "${input:$j:1}" = '-' ]; then strip_tabs=1; j=$((j + 1)); fi
              while [ "$j" -lt "$length" ]; do
                ch="${input:$j:1}"
                [ "$ch" = ' ' ] || [ "$ch" = $'\t' ] || break
                j=$((j + 1))
              done
              delimiter=""
              state="plain"
              while [ "$j" -lt "$length" ]; do
                ch="${input:$j:1}"
                case "$state" in
                  single)
                    if [ "$ch" = "'" ]; then state="plain"; else delimiter="$delimiter$ch"; fi
                    ;;
                  double)
                    if [ "$ch" = '"' ]; then
                      state="plain"
                    elif [ "$ch" = "\\" ] && [ "$j" -lt $((length - 1)) ]; then
                      j=$((j + 1)); delimiter="$delimiter${input:$j:1}"
                    else
                      delimiter="$delimiter$ch"
                    fi
                    ;;
                  plain)
                    case "$ch" in
                      "'") state="single" ;;
                      '"') state="double" ;;
                      "\\")
                        if [ "$j" -lt $((length - 1)) ]; then
                          j=$((j + 1)); delimiter="$delimiter${input:$j:1}"
                        fi
                        ;;
                      ' '|$'\t'|'<'|'>'|'|'|'&'|';') break ;;
                      *) delimiter="$delimiter$ch" ;;
                    esac
                    ;;
                esac
                j=$((j + 1))
              done
              if [ -n "$delimiter" ]; then
                HEREDOC_DELIMS[${#HEREDOC_DELIMS[@]}]="$delimiter"
                HEREDOC_STRIP_TABS[${#HEREDOC_STRIP_TABS[@]}]="$strip_tabs"
              fi
              state="plain"
              i=$((j - 1))
            fi
            ;;
        esac
        ;;
    esac
    i=$((i + 1))
  done
}

# The top-level segmenter. A # is text here. HIDDEN_SEPARATOR is set to 1
# when a ;, &, | or newline is consumed inside quotes, after a backslash or as
# a line continuation (rule G).
HIDDEN_SEPARATOR=0
split_command_segments() {
  local input="$1" length i=0 j k ch next state="plain" segment="" header="" line delimiter redirect_open=0 redirect_before=0
  SEGMENTS=()
  SEGMENT_DELIMITERS=()
  HIDDEN_SEPARATOR=0
  length=${#input}
  while [ "$i" -lt "$length" ]; do
    ch="${input:$i:1}"
    next="${input:$((i + 1)):1}"
    case "$state" in
      single)
        segment="$segment$ch"
        case "$ch" in
          "'") state="plain" ;;
          ';'|'&'|'|'|$'\n') HIDDEN_SEPARATOR=1 ;;
        esac
        i=$((i + 1))
        ;;
      ansi)
        if [ "$ch" = "\\" ] && [ -n "$next" ]; then
          case "$next" in ';'|'&'|'|'|$'\n') HIDDEN_SEPARATOR=1 ;; esac
          segment="$segment$ch$next"; i=$((i + 2))
        else
          segment="$segment$ch"
          case "$ch" in
            "'") state="plain" ;;
            ';'|'&'|'|'|$'\n') HIDDEN_SEPARATOR=1 ;;
          esac
          i=$((i + 1))
        fi
        ;;
      double)
        if [ "$ch" = "\\" ] && [ -n "$next" ]; then
          case "$next" in ';'|'&'|'|'|$'\n') HIDDEN_SEPARATOR=1 ;; esac
          if [ "$next" != $'\n' ]; then segment="$segment$ch$next"; fi
          i=$((i + 2))
        elif [ "$ch" = '$' ] && [ "$next" = '(' ]; then
          skip_paren "$input" $((i + 2)) || policy_deny unsupported_syntax
          segment="$segment${input:i:SKIP_END-i}"
          i="$SKIP_END"
        else
          segment="$segment$ch"
          case "$ch" in
            '"') state="plain" ;;
            ';'|'&'|'|'|$'\n') HIDDEN_SEPARATOR=1 ;;
          esac
          i=$((i + 1))
        fi
        ;;
      plain)
        # redirect_open is 1 right after an unquoted, unescaped < or >.
        redirect_before="$redirect_open"
        redirect_open=0
        case "$ch" in
          '$')
            if [ "$next" = '(' ]; then
              skip_paren "$input" $((i + 2)) || policy_deny unsupported_syntax
              segment="$segment${input:i:SKIP_END-i}"
              i="$SKIP_END"
            elif [ "$next" = "'" ]; then
              segment="$segment$ch$next"; state="ansi"; i=$((i + 2))
            else
              segment="$segment$ch"; i=$((i + 1))
            fi
            ;;
          "'"|'"') segment="$segment$ch"; state=$([ "$ch" = "'" ] && printf single || printf double); i=$((i + 1)) ;;
          "\\")
            if [ "$next" = $'\n' ]; then
              # A line continuation joins the two lines with nothing between.
              HIDDEN_SEPARATOR=1
              i=$((i + 2))
            elif [ -n "$next" ]; then
              # An escaped character never opens a quote or ends a segment.
              case "$next" in ';'|'&'|'|') HIDDEN_SEPARATOR=1 ;; esac
              segment="$segment$ch$next"; i=$((i + 2))
            else
              segment="$segment$ch"; i=$((i + 1))
            fi
            ;;
          ';') header="$header$segment;"; SEGMENT_DELIMITERS[${#SEGMENTS[@]}]=';'; SEGMENTS[${#SEGMENTS[@]}]="$segment"; segment=""; i=$((i + 1)) ;;
          '&')
            if [ "$redirect_before" -eq 1 ] || [ "$next" = '>' ]; then
              # `>&N`, `<&N`, `&>FILE` and `&>>FILE` are redirection
              # operators, not a separator: the command they decorate goes on.
              segment="$segment$ch"; i=$((i + 1))
              continue
            fi
            if [ "$next" = '&' ]; then header="$header$segment&&"; SEGMENT_DELIMITERS[${#SEGMENTS[@]}]='&&'; i=$((i + 2)); else header="$header$segment&"; SEGMENT_DELIMITERS[${#SEGMENTS[@]}]='&'; i=$((i + 1)); fi
            SEGMENTS[${#SEGMENTS[@]}]="$segment"
            segment=""
            ;;
          '|')
            if [ "$redirect_before" -eq 1 ]; then
              # `>|` is one redirection operator, not a pipe.
              segment="$segment$ch"; i=$((i + 1))
              continue
            fi
            if [ "$next" = '|' ]; then header="$header$segment||"; SEGMENT_DELIMITERS[${#SEGMENTS[@]}]='||'; i=$((i + 2)); else header="$header$segment|"; SEGMENT_DELIMITERS[${#SEGMENTS[@]}]='|'; i=$((i + 1)); fi
            SEGMENTS[${#SEGMENTS[@]}]="$segment"
            segment=""
            ;;
          $'\n')
            SEGMENT_DELIMITERS[${#SEGMENTS[@]}]=$'\n'
            SEGMENTS[${#SEGMENTS[@]}]="$segment"
            collect_heredocs "$header$segment"
            segment=""
            header=""
            i=$((i + 1))
            k=0
            while [ "$k" -lt "${#HEREDOC_DELIMS[@]}" ]; do
              delimiter="${HEREDOC_DELIMS[$k]}"
              while [ "$i" -lt "$length" ]; do
                j="$i"
                while [ "$j" -lt "$length" ] && [ "${input:$j:1}" != $'\n' ]; do j=$((j + 1)); done
                line="${input:$i:$((j - i))}"
                [ "${HEREDOC_STRIP_TABS[$k]}" -eq 0 ] || while [ "${line#$'\t'}" != "$line" ]; do line="${line#$'\t'}"; done
                i="$j"
                [ "$i" -ge "$length" ] || i=$((i + 1))
                [ "$line" = "$delimiter" ] && break
              done
              k=$((k + 1))
            done
            ;;
          *)
            segment="$segment$ch"; i=$((i + 1))
            case "$ch" in '<'|'>') redirect_open=1 ;; esac
            ;;
        esac
        ;;
    esac
  done
  SEGMENTS[${#SEGMENTS[@]}]="$segment"
}

# The comment-skipping reading of the segmenter (R1 and R2's top-level text).
split_segments_skipping_comments() {
  local input="$1" length i=0 j k ch next state="plain" segment="" header="" line delimiter redirect_open=0 redirect_before=0
  # word_start is 1 where an unquoted # would begin a word; parens, brackets
  # and braces count the open constructs in which bash reads # as text.
  local word_start=1 parens=0 brackets=0 braces=0
  SEGMENTS=()
  SEGMENT_DELIMITERS=()
  length=${#input}
  while [ "$i" -lt "$length" ]; do
    ch="${input:$i:1}"
    next="${input:$((i + 1)):1}"
    case "$state" in
      single)
        segment="$segment$ch"
        if [ "$ch" = "'" ]; then state="plain"; fi
        i=$((i + 1))
        ;;
      ansi)
        if [ "$ch" = "\\" ] && [ -n "$next" ]; then
          segment="$segment$ch$next"; i=$((i + 2))
        else
          segment="$segment$ch"
          if [ "$ch" = "'" ]; then state="plain"; fi
          i=$((i + 1))
        fi
        ;;
      double)
        if [ "$ch" = "\\" ] && [ -n "$next" ]; then
          if [ "$next" != $'\n' ]; then segment="$segment$ch$next"; fi
          i=$((i + 2))
        elif [ "$ch" = '$' ] && [ "$next" = '(' ]; then
          skip_paren "$input" $((i + 2)) || policy_deny unsupported_syntax
          segment="$segment${input:i:SKIP_END-i}"
          i="$SKIP_END"
        else
          segment="$segment$ch"
          if [ "$ch" = '"' ]; then state="plain"; fi
          i=$((i + 1))
        fi
        ;;
      plain)
        # redirect_open is 1 right after an unquoted, unescaped < or >.
        redirect_before="$redirect_open"
        redirect_open=0
        case "$ch" in
          '$')
            if [ "$next" = '(' ]; then
              skip_paren "$input" $((i + 2)) || policy_deny unsupported_syntax
              segment="$segment${input:i:SKIP_END-i}"
              i="$SKIP_END"
            elif [ "$next" = "'" ]; then
              segment="$segment$ch$next"; state="ansi"; i=$((i + 2))
            elif [ "$next" = '{' ]; then
              segment="$segment$ch$next"; braces=$((braces + 1)); i=$((i + 2))
            elif [ "$next" = '[' ]; then
              segment="$segment$ch$next"; brackets=$((brackets + 1)); i=$((i + 2))
            else
              segment="$segment$ch"; i=$((i + 1))
            fi
            word_start=0
            ;;
          "'"|'"') segment="$segment$ch"; state=$([ "$ch" = "'" ] && printf single || printf double); i=$((i + 1)); word_start=0 ;;
          "\\")
            if [ "$next" = $'\n' ]; then
              # A line continuation joins the two lines with nothing between.
              i=$((i + 2))
            elif [ -n "$next" ]; then
              # An escaped character never opens a quote or ends a segment.
              segment="$segment$ch$next"; i=$((i + 2)); word_start=0
            else
              segment="$segment$ch"; i=$((i + 1)); word_start=0
            fi
            ;;
          '#')
            if [ "$word_start" -eq 1 ] && [ $((parens + brackets + braces)) -eq 0 ]; then
              # A comment: skip to the newline, which the next pass reads.
              segment="$segment#"
              while [ "$i" -lt "$length" ] && [ "${input:$i:1}" != $'\n' ]; do i=$((i + 1)); done
            else
              segment="$segment$ch"; i=$((i + 1)); word_start=0
            fi
            ;;
          ';') header="$header$segment;"; SEGMENT_DELIMITERS[${#SEGMENTS[@]}]=';'; SEGMENTS[${#SEGMENTS[@]}]="$segment"; segment=""; i=$((i + 1)); word_start=1 ;;
          '&')
            word_start=1
            if [ "$redirect_before" -eq 1 ] || [ "$next" = '>' ]; then
              # `>&N`, `<&N`, `&>FILE` and `&>>FILE` are redirection
              # operators, not a separator: the command they decorate goes on.
              segment="$segment$ch"; i=$((i + 1))
              continue
            fi
            if [ "$next" = '&' ]; then header="$header$segment&&"; SEGMENT_DELIMITERS[${#SEGMENTS[@]}]='&&'; i=$((i + 2)); else header="$header$segment&"; SEGMENT_DELIMITERS[${#SEGMENTS[@]}]='&'; i=$((i + 1)); fi
            SEGMENTS[${#SEGMENTS[@]}]="$segment"
            segment=""
            ;;
          '|')
            word_start=1
            if [ "$redirect_before" -eq 1 ]; then
              # `>|` is one redirection operator, not a pipe.
              segment="$segment$ch"; i=$((i + 1))
              continue
            fi
            if [ "$next" = '|' ]; then header="$header$segment||"; SEGMENT_DELIMITERS[${#SEGMENTS[@]}]='||'; i=$((i + 2)); else header="$header$segment|"; SEGMENT_DELIMITERS[${#SEGMENTS[@]}]='|'; i=$((i + 1)); fi
            SEGMENTS[${#SEGMENTS[@]}]="$segment"
            segment=""
            ;;
          $'\n')
            SEGMENT_DELIMITERS[${#SEGMENTS[@]}]=$'\n'
            SEGMENTS[${#SEGMENTS[@]}]="$segment"
            collect_heredocs "$header$segment"
            segment=""
            header=""
            i=$((i + 1))
            word_start=1
            k=0
            while [ "$k" -lt "${#HEREDOC_DELIMS[@]}" ]; do
              delimiter="${HEREDOC_DELIMS[$k]}"
              while [ "$i" -lt "$length" ]; do
                j="$i"
                while [ "$j" -lt "$length" ] && [ "${input:$j:1}" != $'\n' ]; do j=$((j + 1)); done
                line="${input:$i:$((j - i))}"
                [ "${HEREDOC_STRIP_TABS[$k]}" -eq 0 ] || while [ "${line#$'\t'}" != "$line" ]; do line="${line#$'\t'}"; done
                i="$j"
                [ "$i" -ge "$length" ] || i=$((i + 1))
                [ "$line" = "$delimiter" ] && break
              done
              k=$((k + 1))
            done
            ;;
          *)
            segment="$segment$ch"; i=$((i + 1))
            case "$ch" in
              ' '|$'\t') word_start=1 ;;
              '(')
                # (( at a word start, a ( glued to a word, and every ( inside
                # one of them open a group; a lone ( at a word start is a
                # subshell, where # comments as usual.
                if [ "$parens" -gt 0 ] || [ "$word_start" -eq 0 ] || [ "$next" = '(' ]; then parens=$((parens + 1)); fi
                word_start=1 ;;
              ')')
                # The ) that closes a counted group is inside its word (bash
                # reads @(a)#x and x=(a)#b as one word), so a # right after it
                # is text. Any other ) is an operator.
                if [ "$parens" -gt 0 ]; then parens=$((parens - 1)); word_start=0; else word_start=1; fi
                ;;
              '[')
                # [[ at a word start and a [ glued to a word open a group; a
                # lone [ at a word start is the test command.
                if [ "$brackets" -gt 0 ] || [ "$word_start" -eq 0 ] || [ "$next" = '[' ]; then brackets=$((brackets + 1)); fi
                word_start=0 ;;
              ']') [ "$brackets" -eq 0 ] || brackets=$((brackets - 1)); word_start=0 ;;
              '{') [ "$braces" -eq 0 ] || braces=$((braces + 1)); word_start=0 ;;
              '}') [ "$braces" -eq 0 ] || braces=$((braces - 1)); word_start=0 ;;
              '<'|'>') redirect_open=1; word_start=0 ;;
              *) word_start=0 ;;
            esac
            ;;
        esac
        ;;
    esac
  done
  SEGMENTS[${#SEGMENTS[@]}]="$segment"
}

TOP_LEVEL_TEXT=""
TOP_LEVEL_TEXT_G=""
JOINED_SEGMENTS=""
join_segments() { # reads SEGMENTS and SEGMENT_DELIMITERS
  local k=0 out="" nl=$'\n'
  while [ "$k" -lt "${#SEGMENTS[@]}" ]; do
    if [ "${SEGMENT_DELIMITERS[$k]-}" = "$nl" ]; then
      out="$out${SEGMENTS[$k]} $nl"
    else
      out="$out${SEGMENTS[$k]}${SEGMENT_DELIMITERS[$k]-}"
    fi
    k=$((k + 1))
  done
  JOINED_SEGMENTS="$out"
}
top_level_text() { # TEXT
  case "$1" in
    *'#'*)
      split_command_segments "$1"
      join_segments
      TOP_LEVEL_TEXT_G="$JOINED_SEGMENTS"
      ;;
  esac
  split_segments_skipping_comments "$1"
  join_segments
  TOP_LEVEL_TEXT="$JOINED_SEGMENTS"
  case "$1" in *'#'*) ;; *) TOP_LEVEL_TEXT_G="$TOP_LEVEL_TEXT" ;; esac
}

trim_horizontal() {
  TRIMMED="$1"
  while [ "${TRIMMED# }" != "$TRIMMED" ] || [ "${TRIMMED#$'\t'}" != "$TRIMMED" ]; do
    TRIMMED="${TRIMMED# }"
    TRIMMED="${TRIMMED#$'\t'}"
  done
}

REDIR_STRIPPED=""
REDIR_FOUND=0
redirect_skip_token() { # STR INDEX: SKIP_END past the quote or $-form at INDEX
  local s="$1" i="$2" n="${#1}" end
  case "${s:i:1}" in
    \')
      end=$((i + 1))
      while [ "$end" -lt "$n" ] && [ "${s:end:1}" != "'" ]; do end=$((end + 1)); done
      [ "$end" -lt "$n" ] || return 1
      SKIP_END=$((end + 1)) ;;
    \") skip_dquote "$s" $((i + 1)) || return 1 ;;
    \`) skip_backtick "$s" $((i + 1)) || return 1 ;;
    '$')
      case "${s:i+1:1}" in
        '(') skip_paren "$s" $((i + 2)) || return 1 ;;
        '{') skip_brace "$s" $((i + 2)) || return 1 ;;
        '"') skip_dquote "$s" $((i + 2)) || return 1 ;;
        "'")
          end=$((i + 2))
          while [ "$end" -lt "$n" ] && [ "${s:end:1}" != "'" ]; do
            [ "${s:end:1}" != "\\" ] || end=$((end + 1))
            end=$((end + 1))
          done
          [ "$end" -lt "$n" ] || return 1
          SKIP_END=$((end + 1)) ;;
        *) SKIP_END=$((i + 1)) ;;
      esac ;;
    *) return 1 ;;
  esac
}
redirect_target_end() { # STR INDEX: SKIP_END past the target word at INDEX
  local s="$1" i="$2" n="${#1}" c
  while [ "$i" -lt "$n" ]; do
    c="${s:i:1}"
    case "$c" in
      ' '|$'\t'|$'\n'|';'|'|'|'&') break ;;
      \\) i=$((i + 2)) ;;
      \'|\"|\`|'$') redirect_skip_token "$s" "$i" || return 1; i="$SKIP_END" ;;
      '<'|'>')
        [ "${s:i+1:1}" = '(' ] || break
        skip_paren "$s" $((i + 2)) || return 1
        i="$SKIP_END" ;;
      *) i=$((i + 1)) ;;
    esac
  done
  [ "$i" -le "$n" ] || i="$n"
  SKIP_END="$i"
}
strip_command_redirections() {
  local s="$1" n="${#1}" i=0 c next out="" word="" op_len start
  REDIR_FOUND=0
  case "$s" in
    *'<'*|*'>'*) ;;
    *) trim_horizontal "$s"; REDIR_STRIPPED="$TRIMMED"; return 0 ;;
  esac
  while [ "$i" -lt "$n" ]; do
    c="${s:i:1}"
    next="${s:i+1:1}"
    case "$c" in
      ' '|$'\t'|$'\n')
        out="$out$c"; word=""; i=$((i + 1)); continue ;;
      \\)
        out="$out${s:i:2}"; word="$word${s:i:2}"; i=$((i + 2)); continue ;;
      \'|\"|\`|'$')
        if ! redirect_skip_token "$s" "$i"; then out="$out${s:i}"; break; fi
        out="$out${s:i:SKIP_END-i}"; word="$word${s:i:SKIP_END-i}"; i="$SKIP_END"; continue ;;
      '<'|'>')
        if [ "$next" = '(' ]; then
          if ! skip_paren "$s" $((i + 2)); then out="$out${s:i}"; break; fi
          out="$out${s:i:SKIP_END-i}"; word="$word${s:i:SKIP_END-i}"; i="$SKIP_END"; continue
        fi ;;
      '&')
        if [ "$next" != '>' ]; then out="$out$c"; word="$word$c"; i=$((i + 1)); continue; fi ;;
      *)
        out="$out$c"; word="$word$c"; i=$((i + 1)); continue ;;
    esac
    # A redirection operator starts at i.
    case "${s:i:3}" in
      '<<<'|'<<-'|'&>>') op_len=3 ;;
      *)
        case "${s:i:2}" in
          '<<'|'<>'|'<&'|'>>'|'>|'|'>&'|'&>') op_len=2 ;;
          *) op_len=1 ;;
        esac ;;
    esac
    # A descriptor number or {name} written right before the operator belongs
    # to it; any other text there is a word of its own that the operator ends.
    if [[ "$word" =~ ^([0-9]+|\{[A-Za-z_][A-Za-z0-9_]*\})$ ]]; then
      out="${out:0:${#out}-${#word}}"
    fi
    i=$((i + op_len))
    while [ "${s:i:1}" = ' ' ] || [ "${s:i:1}" = $'\t' ]; do i=$((i + 1)); done
    start="$i"
    redirect_target_end "$s" "$i" || policy_deny unsupported_syntax
    [ "$SKIP_END" -gt "$start" ] || policy_deny unsupported_syntax
    i="$SKIP_END"
    out="$out "
    word=""
    REDIR_FOUND=1
  done
  trim_horizontal "$out"
  REDIR_STRIPPED="$TRIMMED"
}

CONTROL_STRIPPED=""
FUNCTION_HEAD_RE='^function[[:blank:]]+[^[:blank:](){}]+([[:blank:]]*\(\))?'
NAME_PARENS_RE='^[A-Za-z_][A-Za-z0-9_.:-]*[[:blank:]]*\(\)'
COPROC_NAME_RE='^[A-Za-z_][A-Za-z0-9_]*[[:blank:]]+[({]'
CLOSE_INDEX=0
first_free_close_paren() { # WORD
  local s="$1" n="${#1}" i=0 depth=0
  while [ "$i" -lt "$n" ]; do
    case "${s:i:1}" in
      \\) i=$((i + 2)); continue ;;
      \') i=$((i + 1))
          while [ "$i" -lt "$n" ] && [ "${s:i:1}" != "'" ]; do i=$((i + 1)); done ;;
      \") skip_dquote "$s" $((i + 1)) || return 1; i="$SKIP_END"; continue ;;
      \`) skip_backtick "$s" $((i + 1)) || return 1; i="$SKIP_END"; continue ;;
      '$')
        case "${s:i+1:1}" in
          '(') skip_paren "$s" $((i + 2)) || return 1; i="$SKIP_END"; continue ;;
          '{') skip_brace "$s" $((i + 2)) || return 1; i="$SKIP_END"; continue ;;
          "'") i=$((i + 2))
               while [ "$i" -lt "$n" ] && [ "${s:i:1}" != "'" ]; do
                 [ "${s:i:1}" != "\\" ] || i=$((i + 1))
                 i=$((i + 1))
               done ;;
        esac ;;
      '(') depth=$((depth + 1)) ;;
      ')')
        if [ "$depth" -eq 0 ]; then CLOSE_INDEX="$i"; return 0; fi
        depth=$((depth - 1)) ;;
    esac
    i=$((i + 1))
  done
  return 1
}
strip_control_openers() {
  local segment="$1" previous_segment="" keyword first
  trim_horizontal "$segment"; segment="$TRIMMED"
  while [ -n "$segment" ] && [ "$segment" != "$previous_segment" ]; do
    previous_segment="$segment"
    for keyword in then do else if elif while until time coproc '!' '{'; do
      case "$segment" in
        "$keyword"[[:blank:]]*)
          segment="${segment#"$keyword"}"
          trim_horizontal "$segment"; segment="$TRIMMED"
          case "$keyword:$segment" in
            time:-p[[:blank:]]*) segment="${segment#-p}" ;;
            coproc:*) if [[ "$segment" =~ $COPROC_NAME_RE ]]; then segment="${segment#*[[:blank:]]}"; fi ;;
          esac
          break ;;
      esac
    done
    if [ "$segment" = "$previous_segment" ]; then
      case "$segment" in
        '('*) segment="${segment#?}" ;;
        *)
          if [[ "$segment" =~ $FUNCTION_HEAD_RE ]] || [[ "$segment" =~ $NAME_PARENS_RE ]]; then
            segment="${segment:${#BASH_REMATCH[0]}}"
          elif split_shell_words "$segment" && [ "${#SPLIT_RAW[@]}" -gt 3 ] \
            && [ "${SPLIT_RAW[0]}" = case ] && [ "${SPLIT_RAW[2]}" = in ]; then
            # The case head: case, its word and in. The pattern after it is
            # cut on the next pass.
            segment="${segment#case}"
            trim_horizontal "$segment"; segment="${TRIMMED#"${SPLIT_RAW[1]}"}"
            trim_horizontal "$segment"; segment="${TRIMMED#in}"
          elif [ "${#SPLIT_RAW[@]}" -gt 0 ]; then
            first="${SPLIT_RAW[0]}"
            if first_free_close_paren "$first" \
              && { [ $((CLOSE_INDEX + 1)) -lt "${#first}" ] || [ "${#SPLIT_RAW[@]}" -gt 1 ]; }; then
              segment="${segment:$((CLOSE_INDEX + 1))}"
            elif [ "${#SPLIT_RAW[@]}" -gt 1 ] && [[ "${SPLIT_RAW[1]}" == ')'* ]] \
              && { [ "${#SPLIT_RAW[1]}" -gt 1 ] || [ "${#SPLIT_RAW[@]}" -gt 2 ]; }; then
              segment="${segment#"$first"}"
              trim_horizontal "$segment"; segment="${TRIMMED#?}"
            fi
          fi ;;
      esac
    fi
    trim_horizontal "$segment"; segment="$TRIMMED"
  done
  CONTROL_STRIPPED="$segment"
}

SCAN_WORDS=()
scan_shell_words() {
  local input="$1" length i=0 ch next state="plain" word="" in_word=0
  SCAN_WORDS=()
  length=${#input}
  while [ "$i" -lt "$length" ]; do
    ch="${input:$i:1}"
    next="${input:$((i + 1)):1}"
    case "$state" in
      single)
        if [ "$ch" = "'" ]; then state="plain"; else word="$word$ch"; fi
        in_word=1
        ;;
      double)
        if [ "$ch" = '"' ]; then
          state="plain"
        elif [ "$ch" = "\\" ] && [ -n "$next" ]; then
          word="$word$next"
          in_word=1
          i=$((i + 1))
        else
          word="$word$ch"
          in_word=1
        fi
        ;;
      plain)
        case "$ch" in
          ' '|$'\t')
            if [ "$in_word" -eq 1 ]; then SCAN_WORDS[${#SCAN_WORDS[@]}]="$word"; word=""; in_word=0; fi
            ;;
          "'") state="single"; in_word=1 ;;
          '"') state="double"; in_word=1 ;;
          "\\")
            if [ -n "$next" ]; then word="$word$next"; in_word=1; i=$((i + 1)); else return 1; fi
            ;;
          *) word="$word$ch"; in_word=1 ;;
        esac
        ;;
    esac
    i=$((i + 1))
  done
  [ "$in_word" -eq 0 ] || SCAN_WORDS[${#SCAN_WORDS[@]}]="$word"
  [ "$state" = "plain" ] || return 1
  [ "${#SCAN_WORDS[@]}" -gt 0 ]
}

is_assignment_word() {
  local name
  case "$1" in [a-zA-Z_]*=*) ;; *) return 1 ;; esac
  name="${1%%=*}"
  case "$name" in *[!a-zA-Z0-9_]*) return 1 ;; esac
  return 0
}

PREFIX_INDEX=0
PREFIX_ASSIGNMENT=0
PREFIX_NAME=""
prefix_name() { # WORD
  local name="${1##*/}"
  PREFIX_NAME=""
  shopt -s nocasematch
  case "$name" in
    sudo) PREFIX_NAME=sudo ;;
    env) PREFIX_NAME=env ;;
    nice) PREFIX_NAME=nice ;;
    command) PREFIX_NAME=command ;;
    exec) PREFIX_NAME=exec ;;
    timeout) PREFIX_NAME=timeout ;;
    caffeinate) PREFIX_NAME=caffeinate ;;
    doas) PREFIX_NAME=doas ;;
    nohup) PREFIX_NAME=nohup ;;
    builtin) PREFIX_NAME=builtin ;;
    setsid) PREFIX_NAME=setsid ;;
    stdbuf) PREFIX_NAME=stdbuf ;;
    time) PREFIX_NAME=time ;;
    noglob) PREFIX_NAME=noglob ;;
    nocorrect) PREFIX_NAME=nocorrect ;;
  esac
  shopt -u nocasematch
  [ -n "$PREFIX_NAME" ]
}
skip_command_prefixes() {
  local index=0
  PREFIX_ASSIGNMENT=0
  while [ "${SCAN_WORDS[$index]:-}" = "!" ]; do index=$((index + 1)); done
  while is_assignment_word "${SCAN_WORDS[$index]:-}"; do PREFIX_ASSIGNMENT=1; index=$((index + 1)); done
  while prefix_name "${SCAN_WORDS[$index]:-}"; do
    case "$PREFIX_NAME" in
      sudo)
        index=$((index + 1))
        while :; do
          case "${SCAN_WORDS[$index]:-}" in
            -n|-E|-H) index=$((index + 1)) ;;
            -u)
              [ -n "${SCAN_WORDS[$((index + 1))]:-}" ] || { PREFIX_INDEX="$index"; return 1; }
              index=$((index + 2))
              ;;
            -*) PREFIX_INDEX="$index"; return 1 ;;
            *) break ;;
          esac
        done
        ;;
      env)
        index=$((index + 1))
        [ "${SCAN_WORDS[$index]:-}" != "-i" ] || index=$((index + 1))
        while :; do
          case "${SCAN_WORDS[$index]:-}" in
            -*) PREFIX_INDEX="$index"; return 1 ;;
            *)
              if is_assignment_word "${SCAN_WORDS[$index]:-}"; then index=$((index + 1)); else break; fi
              ;;
          esac
        done
        ;;
      nice)
        index=$((index + 1))
        if [ "${SCAN_WORDS[$index]:-}" = "-n" ]; then
          [ -n "${SCAN_WORDS[$((index + 1))]:-}" ] || { PREFIX_INDEX="$index"; return 1; }
          index=$((index + 2))
        elif case "${SCAN_WORDS[$index]:-}" in -*) true ;; *) false ;; esac; then
          PREFIX_INDEX="$index"; return 1
        fi
        ;;
      command)
        index=$((index + 1))
        [ "${SCAN_WORDS[$index]:-}" != "-p" ] || index=$((index + 1))
        case "${SCAN_WORDS[$index]:-}" in -*) PREFIX_INDEX="$index"; return 1 ;; esac
        ;;
      exec)
        index=$((index + 1))
        if [ "${SCAN_WORDS[$index]:-}" = "-a" ]; then
          [ -n "${SCAN_WORDS[$((index + 1))]:-}" ] || { PREFIX_INDEX="$index"; return 1; }
          index=$((index + 2))
        elif case "${SCAN_WORDS[$index]:-}" in -*) true ;; *) false ;; esac; then
          PREFIX_INDEX="$index"; return 1
        fi
        ;;
      timeout)
        index=$((index + 1))
        [ "${SCAN_WORDS[$index]:-}" != "--foreground" ] || index=$((index + 1))
        case "${SCAN_WORDS[$index]:-}" in
          ""|-*) PREFIX_INDEX="$index"; return 1 ;;
          *) index=$((index + 1)) ;;
        esac
        ;;
      caffeinate)
        index=$((index + 1))
        while :; do
          case "${SCAN_WORDS[$index]:-}" in
            -i|-s|-d) index=$((index + 1)) ;;
            -*) PREFIX_INDEX="$index"; return 1 ;;
            *) break ;;
          esac
        done
        ;;
      doas)
        index=$((index + 1))
        while :; do
          case "${SCAN_WORDS[$index]:-}" in
            -n) index=$((index + 1)) ;;
            -u)
              [ -n "${SCAN_WORDS[$((index + 1))]:-}" ] || { PREFIX_INDEX="$index"; return 1; }
              index=$((index + 2))
              ;;
            -*) PREFIX_INDEX="$index"; return 1 ;;
            *) break ;;
          esac
        done
        ;;
      nohup|builtin)
        index=$((index + 1))
        case "${SCAN_WORDS[$index]:-}" in -*) PREFIX_INDEX="$index"; return 1 ;; esac
        ;;
      setsid)
        index=$((index + 1))
        while :; do
          case "${SCAN_WORDS[$index]:-}" in
            -c|-f|-w|--ctty|--fork|--wait) index=$((index + 1)) ;;
            -*) PREFIX_INDEX="$index"; return 1 ;;
            *) break ;;
          esac
        done
        ;;
      stdbuf)
        # -i, -o and -e take a value: stdbuf -o 0 rm reaches the rm.
        index=$((index + 1))
        while :; do
          case "${SCAN_WORDS[$index]:-}" in
            -i|-o|-e)
              [ -n "${SCAN_WORDS[$((index + 1))]:-}" ] || { PREFIX_INDEX="$index"; return 1; }
              index=$((index + 2))
              ;;
            -i?*|-o?*|-e?*|--input=*|--output=*|--error=*) index=$((index + 1)) ;;
            -*) PREFIX_INDEX="$index"; return 1 ;;
            *) break ;;
          esac
        done
        ;;
      time)
        # The reserved word time is stripped with the control openers; this
        # is TIME or /usr/bin/time, which a case-insensitive filesystem runs.
        index=$((index + 1))
        [ "${SCAN_WORDS[$index]:-}" != "-p" ] || index=$((index + 1))
        case "${SCAN_WORDS[$index]:-}" in -*) PREFIX_INDEX="$index"; return 1 ;; esac
        ;;
      noglob|nocorrect)
        # zsh's precommand modifiers take no options.
        index=$((index + 1))
        ;;
    esac
  done
  PREFIX_INDEX="$index"
  return 0
}

RE_GIT_NAME='[Gg][Ii][Tt]'
RE_WRAPPER_NAME='([Ee][Nn][Vv]|[Cc][Oo][Mm][Mm][Aa][Nn][Dd]|[Ee][Xx][Ee][Cc]|[Bb][Uu][Ii][Ll][Tt][Ii][Nn]|[Tt][Ii][Mm][Ee]|[Nn][Ii][Cc][Ee]|[Ss][Uu][Dd][Oo])'
CANON_WRAPPER=""
canon_wrapper() { # NAME
  CANON_WRAPPER=""
  shopt -s nocasematch
  case "$1" in
    env) CANON_WRAPPER=env ;;
    command) CANON_WRAPPER=command ;;
    exec) CANON_WRAPPER=exec ;;
    builtin) CANON_WRAPPER=builtin ;;
    time) CANON_WRAPPER=time ;;
    nice) CANON_WRAPPER=nice ;;
    sudo) CANON_WRAPPER=sudo ;;
  esac
  shopt -u nocasematch
}

is_git_spelling() {
  local t="$1"
  t="${t#\\}"
  case "$t" in
    \"*\"|\'*\') t="${t:1:${#t}-2}" ;;
  esac
  [[ "$t" =~ ^([A-Za-z0-9_./~\$\{\}+-]*/)?$RE_GIT_NAME$ ]]
}

SPLIT_WORDS=()
SPLIT_RAW=()
SPLIT_OBSCURED=()
SKIP_END=0
skip_paren() { # STR INDEX_AFTER_OPEN: sets SKIP_END past the matching ")"
  local s="$1" i="$2" n="${#1}" depth=1 c
  while [ "$i" -lt "$n" ]; do
    c="${s:i:1}"
    case "$c" in
      \\) i=$((i + 2)); continue ;;
      \') i=$((i + 1))
          while [ "$i" -lt "$n" ] && [ "${s:i:1}" != "'" ]; do i=$((i + 1)); done
          [ "$i" -lt "$n" ] || return 1 ;;
      \") skip_dquote "$s" $((i + 1)) || return 1; i="$SKIP_END"; continue ;;
      \`) skip_backtick "$s" $((i + 1)) || return 1; i="$SKIP_END"; continue ;;
      '(') depth=$((depth + 1)) ;;
      ')') depth=$((depth - 1))
           if [ "$depth" -eq 0 ]; then SKIP_END=$((i + 1)); return 0; fi ;;
    esac
    i=$((i + 1))
  done
  return 1
}
skip_backtick() { # STR INDEX_AFTER_OPEN: sets SKIP_END past the closing backtick
  local s="$1" i="$2" n="${#1}"
  while [ "$i" -lt "$n" ]; do
    case "${s:i:1}" in
      \\) i=$((i + 2)); continue ;;
      \`) SKIP_END=$((i + 1)); return 0 ;;
    esac
    i=$((i + 1))
  done
  return 1
}
skip_brace() { # STR INDEX_AFTER_OPEN: sets SKIP_END past the matching "}"
  local s="$1" i="$2" n="${#1}" depth=1
  while [ "$i" -lt "$n" ]; do
    case "${s:i:1}" in
      \\) i=$((i + 2)); continue ;;
      \') i=$((i + 1))
          while [ "$i" -lt "$n" ] && [ "${s:i:1}" != "'" ]; do i=$((i + 1)); done
          [ "$i" -lt "$n" ] || return 1 ;;
      \") skip_dquote "$s" $((i + 1)) || return 1; i="$SKIP_END"; continue ;;
      '{') depth=$((depth + 1)) ;;
      '}') depth=$((depth - 1))
           if [ "$depth" -eq 0 ]; then SKIP_END=$((i + 1)); return 0; fi ;;
    esac
    i=$((i + 1))
  done
  return 1
}
skip_dquote() { # STR INDEX_AFTER_OPEN: sets SKIP_END past the closing quote
  local s="$1" i="$2" n="${#1}"
  while [ "$i" -lt "$n" ]; do
    case "${s:i:1}" in
      \\) i=$((i + 2)); continue ;;
      \") SKIP_END=$((i + 1)); return 0 ;;
      \`) skip_backtick "$s" $((i + 1)) || return 1; i="$SKIP_END"; continue ;;
      '$') case "${s:i+1:1}" in
             '(') skip_paren "$s" $((i + 2)) || return 1; i="$SKIP_END"; continue ;;
             '{') skip_brace "$s" $((i + 2)) || return 1; i="$SKIP_END"; continue ;;
           esac ;;
    esac
    i=$((i + 1))
  done
  return 1
}
split_shell_words() {
  local s="$1" n="${#1}" i=0 c w="" start=0 inword=0 obscured=0 end content k
  SPLIT_WORDS=()
  SPLIT_RAW=()
  SPLIT_OBSCURED=()
  while [ "$i" -lt "$n" ]; do
    c="${s:i:1}"
    case "$c" in
      [[:space:]])
        if [ "$inword" -eq 1 ]; then
          SPLIT_WORDS+=("$w"); SPLIT_RAW+=("${s:start:i-start}"); SPLIT_OBSCURED+=("$obscured"); w=""; inword=0; obscured=0
        fi
        i=$((i + 1))
        continue
        ;;
    esac
    if [ "$inword" -eq 0 ]; then inword=1; start="$i"; fi
    case "$c" in
      \\)
        if [ $((i + 1)) -lt "$n" ]; then w="$w${s:i+1:1}"; i=$((i + 2)); else w="$w\\"; i=$((i + 1)); fi
        ;;
      \')
        end=$((i + 1))
        while [ "$end" -lt "$n" ] && [ "${s:end:1}" != "'" ]; do end=$((end + 1)); done
        [ "$end" -lt "$n" ] || return 1
        w="$w${s:i+1:end-i-1}"; i=$((end + 1))
        ;;
      \")
        skip_dquote "$s" $((i + 1)) || return 1
        content="${s:i+1:SKIP_END-i-2}"
        k=0
        while [ "$k" -lt "${#content}" ]; do
          if [ "${content:k:1}" = "\\" ]; then
            case "${content:k+1:1}" in
              \"|\\|'$'|\`) w="$w${content:k+1:1}"; k=$((k + 2)); continue ;;
            esac
          fi
          w="$w${content:k:1}"; k=$((k + 1))
        done
        i="$SKIP_END"
        ;;
      \`)
        skip_backtick "$s" $((i + 1)) || return 1
        w="$w${s:i:SKIP_END-i}"; i="$SKIP_END"
        ;;
      '$')
        case "${s:i+1:1}" in
          "'")
            end=$((i + 2))
            while [ "$end" -lt "$n" ] && [ "${s:end:1}" != "'" ]; do
              [ "${s:end:1}" = "\\" ] && end=$((end + 1))
              end=$((end + 1))
            done
            [ "$end" -lt "$n" ] || return 1
            content="${s:i+2:end-i-2}"
            if [ "$(printf '%b' '\u0074')" != t ] && [[ "$content" == *'\u'* || "$content" == *'\U'* ]]; then
              # Bash 3.2 cannot decode these escapes. Keep the whole word
              # obscured so it cannot evade the forced-removal fallback.
              content="__ANSI_C_OBSCURED__"; obscured=1
            else
              content="$(printf '%b' "$content")"
            fi
            w="$w$content"; i=$((end + 1))
            ;;
          '"')
            skip_dquote "$s" $((i + 2)) || return 1
            content="${s:i+2:SKIP_END-i-3}"
            content="${content//\\/}"
            w="$w$content"; i="$SKIP_END"
            ;;
          '(')
            skip_paren "$s" $((i + 2)) || return 1
            w="$w${s:i:SKIP_END-i}"; i="$SKIP_END"
            ;;
          '{')
            skip_brace "$s" $((i + 2)) || return 1
            w="$w${s:i:SKIP_END-i}"; i="$SKIP_END"
            ;;
          *) w="$w$c"; i=$((i + 1)) ;;
        esac
        ;;
      *) w="$w$c"; i=$((i + 1)) ;;
    esac
  done
  if [ "$inword" -eq 1 ]; then SPLIT_WORDS+=("$w"); SPLIT_RAW+=("${s:start}"); SPLIT_OBSCURED+=("$obscured"); fi
  return 0
}

nocase_equals() { # A B
  local rc=1
  shopt -s nocasematch
  [[ "$1" == "$2" ]] && rc=0
  shopt -u nocasematch
  return "$rc"
}

is_shell_name() { # WORD
  local name="${1##*/}" rc=1
  shopt -s nocasematch
  case "$name" in
    sh|bash|zsh|dash|ksh|mksh|ash|yash|fish|csh|tcsh|busybox) rc=0 ;;
    su|script|watch|parallel) rc=0 ;;
  esac
  shopt -u nocasematch
  return "$rc"
}

R1_JOINED=""
r1_join_continuations() { # TEXT
  local t="$1" out="" head run nl=$'\n'
  while :; do
    case "$t" in *"\\$nl"*) ;; *) break ;; esac
    head="${t%%"\\$nl"*}"
    t="${t:${#head}+2}"
    run="${head##*[!\\]}"
    if [ $((${#run} % 2)) -eq 0 ]; then
      out="$out$head"
    else
      out="$out$head\\$nl"
    fi
  done
  R1_JOINED="$out$t"
}

R1_NORMALIZED=""
r1_normalize() { # TEXT
  local t
  r1_join_continuations "$1"
  t="$R1_JOINED"
  t="${t//\$\'/\'}"
  t="${t//\$\"/\"}"
  t="${t//\'/}"
  t="${t//\"/}"
  t="${t//\\/}"
  R1_NORMALIZED="$t"
}

R1_SPLIT_TEXT=""
R1_COMMENT_LINE_RE='(^|[[:blank:];&|()])#'
r1_split_comment_continuations() { # TEXT
  local rest="$1" out="" line nl=$'\n'
  while :; do
    case "$rest" in *"$nl"*) ;; *) break ;; esac
    line="${rest%%"$nl"*}"
    rest="${rest#*"$nl"}"
    case "$line" in
      *"\\")
        if [[ "${line%?}" =~ $R1_COMMENT_LINE_RE ]]; then
          out="$out${line%?}$nl"
        else
          out="$out$line$nl"
        fi
        ;;
      *) out="$out$line$nl" ;;
    esac
  done
  R1_SPLIT_TEXT="$out$rest"
}

R1_OPSPLIT=""
r1_split_parameter_operators() { # NORMALIZED
  local t="$1" out="" span depth i n c
  while :; do
    case "$t" in *'${'*) ;; *) break ;; esac
    out="$out${t%%\$\{*}\${"
    t="${t#*\$\{}"
    depth=1
    i=0
    n=${#t}
    while [ "$i" -lt "$n" ]; do
      c="${t:i:1}"
      if [ "$c" = '{' ]; then
        depth=$((depth + 1))
      elif [ "$c" = '}' ]; then
        depth=$((depth - 1))
        [ "$depth" -gt 0 ] || break
      fi
      i=$((i + 1))
    done
    span="${t:0:i}"
    span="${span//:-/ }"
    span="${span//:=/ }"
    span="${span//:+/ }"
    span="${span//:\?/ }"
    span="${span//-/ }"
    span="${span//+/ }"
    span="${span//\?/ }"
    out="$out$span"
    t="${t:i}"
  done
  R1_OPSPLIT="$out$t"
}

R1_TOKENS=()
r1_tokenize() { # TEXT
  local IFS=$' \t\n;&|()<>`={},' tok extra
  local -a found
  set -f
  found=($1)
  set +f
  R1_TOKENS=()
  for tok in ${found[@]+"${found[@]}"}; do
    [ -n "$tok" ] || continue
    R1_TOKENS+=("$tok")
    case "$tok" in
      --*) ;;
      -*S*)
        extra="${tok#*S}"
        [ -z "$extra" ] || R1_TOKENS+=("$extra")
        ;;
    esac
  done
}

r1_ansi_c_escape() { # TEXT
  local rest="$1" span i n c
  while :; do
    case "$rest" in *"\$'"*) ;; *) return 1 ;; esac
    rest="${rest#*\$\'}"
    span=""
    i=0
    n=${#rest}
    while [ "$i" -lt "$n" ]; do
      c="${rest:i:1}"
      if [ "$c" = "\\" ]; then
        span="$span${rest:i:2}"
        i=$((i + 2))
        continue
      fi
      [ "$c" != "'" ] || break
      span="$span$c"
      i=$((i + 1))
    done
    case "$span" in
      *'\x'*|*'\u'*|*'\U'*|*'\c'*|*'\'[0-9]*) return 0 ;;
    esac
  done
}

ENV_SCAN_WORDS=()
env_scan_words() { # TEXT
  local s="$1" n=${#1} i=0 c next prev="" w="" inword=0 state=plain
  ENV_SCAN_WORDS=()
  while [ "$i" -lt "$n" ]; do
    c="${s:i:1}"
    next="${s:i+1:1}"
    case "$state" in
      single)
        w="$w$c"
        [ "$c" != "'" ] || state=plain
        ;;
      double)
        w="$w$c"
        if [ "$c" = "\\" ]; then
          w="$w$next"
          i=$((i + 1))
        elif [ "$c" = '"' ]; then
          state=plain
        fi
        ;;
      plain)
        case "$c" in
          ' '|$'\t'|$'\n')
            if [ "$inword" -eq 1 ]; then ENV_SCAN_WORDS+=("$w"); w=""; inword=0; fi
            ;;
          "'") w="$w$c"; inword=1; state=single ;;
          '"') w="$w$c"; inword=1; state=double ;;
          "\\") w="$w$c$next"; inword=1; i=$((i + 1)) ;;
          ';'|'('|')'|'&'|'|')
            if { [ "$c" = '&' ] && { [ "$prev" = '<' ] || [ "$prev" = '>' ] || [ "$next" = '>' ]; }; } \
              || { [ "$c" = '|' ] && [ "$prev" = '>' ]; }; then
              w="$w$c"
              inword=1
            else
              if [ "$inword" -eq 1 ]; then ENV_SCAN_WORDS+=("$w"); w=""; inword=0; fi
              ENV_SCAN_WORDS+=("")
            fi
            ;;
          *) w="$w$c"; inword=1 ;;
        esac
        ;;
    esac
    prev="$c"
    i=$((i + 1))
  done
  [ "$inword" -eq 0 ] || ENV_SCAN_WORDS+=("$w")
}

REDIRECTION_WORD_RE='^([0-9]+|\{[A-Za-z_][A-Za-z0-9_]*\})?(<<<|<<-|&>>|<<|<>|<&|>>|>\||>&|&>|<|>)'
REDIRECTION_OPERATOR_RE='^([0-9]+|\{[A-Za-z_][A-Za-z0-9_]*\})?(<<<|<<-|&>>|<<|<>|<&|>>|>\||>&|&>|<|>)$'

env_split_in_option_list() { # TEXT
  local i=0 n w bare found=0
  shopt -s nocasematch
  [[ "$1" == *env* ]] && found=1
  shopt -u nocasematch
  [ "$found" -eq 1 ] || return 1
  env_scan_words "$1"
  n=${#ENV_SCAN_WORDS[@]}
  while [ "$i" -lt "$n" ]; do
    w="${ENV_SCAN_WORDS[$i]}"
    i=$((i + 1))
    [ -n "$w" ] || continue
    bare="${w//\'/}"
    bare="${bare//\"/}"
    nocase_equals "${bare##*/}" env || continue
    while [ "$i" -lt "$n" ]; do
      w="${ENV_SCAN_WORDS[$i]}"
      [ -n "$w" ] || break
      bare="${w//\'/}"
      bare="${bare//\"/}"
      case "$bare" in
        --s*) return 0 ;;
        -u|-C|-P|--unset|--chdir) i=$((i + 2)); continue ;;
        --*) ;;
        -*S*) return 0 ;;
        -*) ;;
        *)
          if [[ "$bare" =~ $REDIRECTION_OPERATOR_RE ]]; then
            i=$((i + 1))
          elif [[ "$bare" =~ $REDIRECTION_WORD_RE ]]; then
            :
          elif ! is_assignment_word "$bare"; then
            break
          fi
          ;;
      esac
      i=$((i + 1))
    done
  done
  return 1
}

CMD_WORDS_RAW=()
CMD_WORDS_DEQ=()
ARITHMETIC_COMMAND_RE='^(([!{]|then|do|else|if|elif|while|until|time)[[:blank:]]+)*\(\('
collect_command_words() { # TEXT
  local segment k
  local -a segs raw deq
  CMD_WORDS_RAW=()
  CMD_WORDS_DEQ=()
  segs=()
  case "$1" in
    *'#'*)
      split_command_segments "$1"
      segs=(${SEGMENTS[@]+"${SEGMENTS[@]}"})
      ;;
  esac
  split_segments_skipping_comments "$1"
  segs+=(${SEGMENTS[@]+"${SEGMENTS[@]}"})
  for segment in ${segs[@]+"${segs[@]}"}; do
    trim_horizontal "$segment"
    segment="$TRIMMED"
    [ -n "$segment" ] || continue
    [[ "$segment" =~ $ARITHMETIC_COMMAND_RE ]] && continue
    strip_control_openers "$segment"
    strip_command_redirections "$CONTROL_STRIPPED"
    split_shell_words "$REDIR_STRIPPED" || continue
    raw=(${SPLIT_RAW[@]+"${SPLIT_RAW[@]}"})
    deq=(${SPLIT_WORDS[@]+"${SPLIT_WORDS[@]}"})
    [ "${#raw[@]}" -gt 0 ] || continue
    k=0
    while [ "$k" -lt "${#raw[@]}" ] && is_assignment_word "${raw[$k]}"; do k=$((k + 1)); done
    if [ "$k" -lt "${#raw[@]}" ]; then
      CMD_WORDS_RAW+=("${raw[$k]}")
      CMD_WORDS_DEQ+=("${deq[$k]}")
    fi
    SCAN_WORDS=(${deq[@]+"${deq[@]}"})
    if skip_command_prefixes && [ "$PREFIX_INDEX" -lt "${#raw[@]}" ]; then
      CMD_WORDS_RAW+=("${raw[$PREFIX_INDEX]}")
      CMD_WORDS_DEQ+=("${deq[$PREFIX_INDEX]}")
    fi
  done
}

command_word_is_pattern() { # RAW
  local w="$1" n=${#1} i=0 c prev="" state=plain
  case "$w" in '['|'[[') return 1 ;; esac
  while [ "$i" -lt "$n" ]; do
    c="${w:i:1}"
    case "$state" in
      single) [ "$c" != "'" ] || state=plain ;;
      double)
        case "$c" in
          '"') state=plain ;;
          "\\") i=$((i + 1)) ;;
        esac
        ;;
      plain)
        case "$c" in
          "'") state=single ;;
          '"') state=double ;;
          "\\") i=$((i + 1)) ;;
          '*'|'?'|'[') return 0 ;;
          '{') [ "$prev" = '$' ] || return 0 ;;
          '(') [ "$i" -eq 0 ] || [ "$prev" = '$' ] || return 0 ;;
        esac
        ;;
    esac
    prev="$c"
    i=$((i + 1))
  done
  return 1
}

# ---------------------------------------------------------------------------
# The git guard's own reading, built on the pinned pieces above.
#
# Reading order (run_policy): the push word, the commit word and the § 7.2
# markers; the length cap; inherited git variables; configuration injection
# and GIT_ names on the command line; the carriers (text-wide, then scoped to
# their pipeline); rule G outside plain text; each top-level segment's push;
# each top-level segment's commit message. Nothing exits early once the gates
# have started: every step records its codes, and the stricter verdict is
# printed, reason from the first code that reached it.
# ---------------------------------------------------------------------------

# The words P1 looks for, on one reading of TEXT: the push word (push or
# send-pack, or a word ending -push or -send-pack), the commit word (commit,
# or ending -commit), and a git token (git or a path ending /git), each in any
# letter case. Adds to WT_PUSH, WT_COMMIT and WT_GIT.
WT_PUSH=0
WT_COMMIT=0
WT_GIT=0
word_tokens_scan() { # TEXT
  local tok part rest
  r1_normalize "$1"
  r1_split_parameter_operators "$R1_NORMALIZED"
  r1_tokenize "$R1_OPSPLIT"
  shopt -s nocasematch
  for tok in ${R1_TOKENS[@]+"${R1_TOKENS[@]}"}; do
    # Each token is also read in its pieces between [, ], * and ?, so a list
    # element (['git','push']) or a glob suffix (push*) is still the word.
    rest="$tok"
    while :; do
      part="${rest%%[][*?]*}"
      case "$part" in
        push|send-pack|*-push|*-send-pack) WT_PUSH=1 ;;
        commit|*-commit) WT_COMMIT=1 ;;
        git|*/git) WT_GIT=1 ;;
      esac
      [ "$part" != "$rest" ] || break
      rest="${rest:${#part}+1}"
    done
  done
  shopt -u nocasematch
}

# P1 on TEXT: its own reading and, when it holds a #, the comment-continuation
# reading. An ANSI-C escape that can spell a letter presumes the push word.
text_words() { # TEXT
  WT_PUSH=0
  WT_COMMIT=0
  WT_GIT=0
  word_tokens_scan "$1"
  case "$1" in
    *'#'*)
      r1_split_comment_continuations "$1"
      [ "$R1_SPLIT_TEXT" = "$1" ] || word_tokens_scan "$R1_SPLIT_TEXT"
      ;;
  esac
  if r1_ansi_c_escape "$1"; then WT_PUSH=1; fi
  return 0
}

# P3: plain text holds none of #, a backslash, $', $", ${, a backtick, $(,
# <( or >(. In plain text the segmenter's quote state is bash's.
plain_text() { # TEXT
  case "$1" in
    *'#'*|*"\\"*|*"\$'"*|*'$"'*|*'${'*|*'`'*|*'$('*|*'<('*|*'>('*) return 1 ;;
  esac
  return 0
}

# P6's raw-word rule: true when the raw shell word RAW is literal. A word that
# holds, outside single quotes, a $ or a backtick (escaped or not), or,
# outside any quotes, one of * ? [ ] { } ( ) < > | ; & !, or that starts with
# an unquoted ~, or that the splitter marked obscured (OBSCURED=1), is not.
raw_word_literal() { # RAW OBSCURED
  local s="$1" n=${#1} i=0 c state=plain
  [ "${2:-0}" = 0 ] || return 1
  case "$s" in '~'*) return 1 ;; esac
  while [ "$i" -lt "$n" ]; do
    c="${s:i:1}"
    case "$state" in
      single) [ "$c" != "'" ] || state=plain ;;
      double)
        case "$c" in
          '"') state=plain ;;
          '$'|'`') return 1 ;;
          "\\") i=$((i + 1)); case "${s:i:1}" in '$'|'`') return 1 ;; esac ;;
        esac
        ;;
      plain)
        case "$c" in
          "'") state=single ;;
          '"') state=double ;;
          "\\") i=$((i + 1)); case "${s:i:1}" in '$'|'`') return 1 ;; esac ;;
          '$'|'`'|'*'|'?'|'['|']'|'{'|'}'|'('|')'|'<'|'>'|'|'|';'|'&'|'!') return 1 ;;
        esac
        ;;
    esac
    i=$((i + 1))
  done
  return 0
}

# A name P6 protects: after removing a leading refs/ and then a leading
# heads/, main or master in any letter case.
protected_name() { # NAME
  local name="$1"
  name="${name#refs/}"
  name="${name#heads/}"
  nocase_equals "$name" main || nocase_equals "$name" master
}

# HEAD in any letter case, or @: the current branch.
names_current_branch() { # NAME
  [ "$1" = "@" ] || nocase_equals "$1" HEAD
}

# ---------------------------------------------------------------------------
# One top-level segment, read as P4 reads it: trimmed, control openers
# stripped, redirections removed, words split; a raw word that starts with an
# unquoted # ends the words bash reads. RS_STATE is empty, arith, comment,
# unsplit or words. RS_CMD is the command word's index after assignments and
# known prefixes (it may equal the word count: an assignment-only segment);
# RS_PREFIX_OK is 0 when a prefix option is one the skipper does not read.
# ---------------------------------------------------------------------------
RS_STATE=""
RS_WORDS=()
RS_RAW=()
RS_OBS=()
RS_CMD=0
RS_PREFIX_OK=1
read_segment() { # SEGMENT
  local seg k n
  RS_STATE=empty
  RS_WORDS=()
  RS_RAW=()
  RS_OBS=()
  RS_CMD=0
  RS_PREFIX_OK=1
  trim_horizontal "$1"
  seg="$TRIMMED"
  [ -n "$seg" ] || return 0
  if [[ "$seg" =~ $ARITHMETIC_COMMAND_RE ]]; then RS_STATE=arith; return 0; fi
  strip_control_openers "$seg"
  strip_command_redirections "$CONTROL_STRIPPED"
  if ! split_shell_words "$REDIR_STRIPPED"; then RS_STATE=unsplit; return 0; fi
  n=${#SPLIT_RAW[@]}
  k=0
  while [ "$k" -lt "$n" ]; do
    case "${SPLIT_RAW[$k]}" in '#'*) break ;; esac
    RS_WORDS[k]="${SPLIT_WORDS[$k]}"
    RS_RAW[k]="${SPLIT_RAW[$k]}"
    RS_OBS[k]="${SPLIT_OBSCURED[$k]}"
    k=$((k + 1))
  done
  if [ "$k" -eq 0 ]; then
    [ "$n" -eq 0 ] || RS_STATE=comment
    return 0
  fi
  RS_STATE=words
  SCAN_WORDS=(${RS_WORDS[@]+"${RS_WORDS[@]}"})
  if skip_command_prefixes; then RS_PREFIX_OK=1; else RS_PREFIX_OK=0; fi
  RS_CMD="$PREFIX_INDEX"
  return 0
}

# ---------------------------------------------------------------------------
# P5's closed list of git global options, walked from index START of the
# current segment's words. Sets GW_SUB to the subcommand's index (or the word
# count when there is none), GW_UNKNOWN when an option outside the list stands
# there, GW_CONFIG when -c or --config-env is present, GW_REPO when --bare,
# --git-dir, --work-tree or --namespace select a repository the target reads
# cannot reproduce, GW_CPATHS to the -C values (GW_CLIT 0 when one is not
# literal), and GW_INJECT when a -c key in lower case starts with alias. or
# ends with .push, .pushurl or .mirror, or --config-env is present.
# ---------------------------------------------------------------------------
GW_SUB=0
GW_UNKNOWN=0
GW_CONFIG=0
GW_REPO=0
GW_CLIT=1
GW_INJECT=0
GW_CPATHS=()
config_key_injects() { # KEY
  local key="${1%%=*}" rc=1
  shopt -s nocasematch
  case "$key" in alias.*|*.push|*.pushurl|*.mirror) rc=0 ;; esac
  shopt -u nocasematch
  return "$rc"
}
git_global_walk() { # START
  local i="$1" n=${#RS_WORDS[@]} w
  GW_UNKNOWN=0
  GW_CONFIG=0
  GW_REPO=0
  GW_CLIT=1
  GW_INJECT=0
  GW_CPATHS=()
  while [ "$i" -lt "$n" ]; do
    w="${RS_WORDS[$i]}"
    case "$w" in
      -C)
        [ $((i + 1)) -lt "$n" ] || { GW_UNKNOWN=1; break; }
        raw_word_literal "${RS_RAW[$((i + 1))]}" "${RS_OBS[$((i + 1))]}" || GW_CLIT=0
        GW_CPATHS+=("${RS_WORDS[$((i + 1))]}")
        i=$((i + 2))
        ;;
      -c)
        GW_CONFIG=1
        [ $((i + 1)) -lt "$n" ] || { GW_UNKNOWN=1; break; }
        config_key_injects "${RS_WORDS[$((i + 1))]}" && GW_INJECT=1
        i=$((i + 2))
        ;;
      -c?*)
        GW_CONFIG=1
        config_key_injects "${w#-c}" && GW_INJECT=1
        i=$((i + 1))
        ;;
      --config-env)
        GW_CONFIG=1
        GW_INJECT=1
        i=$((i + 2))
        ;;
      --config-env=*)
        GW_CONFIG=1
        GW_INJECT=1
        i=$((i + 1))
        ;;
      --git-dir|--work-tree|--namespace)
        GW_REPO=1
        i=$((i + 2))
        ;;
      --git-dir=*|--work-tree=*|--namespace=*)
        GW_REPO=1
        i=$((i + 1))
        ;;
      --bare)
        GW_REPO=1
        i=$((i + 1))
        ;;
      --attr-source|--exec-path) i=$((i + 2)) ;;
      --attr-source=*|--exec-path=*) i=$((i + 1)) ;;
      -p|--paginate|-P|--no-pager|--no-replace-objects|--no-lazy-fetch|--no-optional-locks|--no-advice|--literal-pathspecs|--glob-pathspecs|--noglob-pathspecs|--icase-pathspecs) i=$((i + 1)) ;;
      -*) GW_UNKNOWN=1; break ;;
      *) break ;;
    esac
  done
  [ "$i" -le "$n" ] || i="$n"
  GW_SUB="$i"
  return 0
}

# ---------------------------------------------------------------------------
# § 7.2: GIT_ names that reach a later command. RN_NAMES gets every name
# written as an assignment before a segment's command word, in an
# assignment-only segment, or as an argument (assignment or bare name) of
# export, declare, typeset, readonly, local or env. Reads the current segment.
# ---------------------------------------------------------------------------
RN_NAMES=()
reaching_names() {
  local n=${#RS_WORDS[@]} k=0 w base stop=0
  RN_NAMES=()
  [ "$RS_STATE" = words ] || return 0
  while [ "$k" -lt "$n" ] && [ "${RS_WORDS[$k]}" = "!" ]; do k=$((k + 1)); done
  # Every assignment word before the command word (the skipper's own, env's
  # included) reaches.
  while [ "$k" -lt "$n" ]; do
    w="${RS_WORDS[$k]}"
    if [ "$RS_PREFIX_OK" -eq 1 ] && [ "$k" -ge "$RS_CMD" ]; then break; fi
    if is_assignment_word "$w"; then
      RN_NAMES+=("${w%%=*}")
    elif [ "$RS_PREFIX_OK" -eq 0 ]; then
      case "$w" in -*) ;; *) prefix_name "$w" || break ;; esac
    fi
    k=$((k + 1))
  done
  [ "$RS_PREFIX_OK" -eq 1 ] || return 0
  [ "$RS_CMD" -lt "$n" ] || return 0
  base="${RS_WORDS[$RS_CMD]##*/}"
  shopt -s nocasematch
  case "$base" in export|declare|typeset|readonly|local|env) ;; *) stop=1 ;; esac
  shopt -u nocasematch
  [ "$stop" -eq 0 ] || return 0
  k=$((RS_CMD + 1))
  while [ "$k" -lt "$n" ]; do
    w="${RS_WORDS[$k]}"
    case "$w" in
      -u|-C|-P|--unset|--chdir) k=$((k + 2)); continue ;;
      -*|+*) k=$((k + 1)); continue ;;
    esac
    if is_assignment_word "$w"; then
      RN_NAMES+=("${w%%=*}")
    elif [[ "$w" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
      RN_NAMES+=("$w")
    fi
    k=$((k + 1))
  done
  return 0
}

# The variables that select a repository or inject configuration (§ 7.1).
git_environment_name() { # NAME
  local rc=1
  shopt -s nocasematch
  case "$1" in
    GIT_DIR|GIT_WORK_TREE|GIT_COMMON_DIR|GIT_INDEX_FILE|GIT_OBJECT_DIRECTORY|GIT_ALTERNATE_OBJECT_DIRECTORIES|GIT_NAMESPACE|GIT_CEILING_DIRECTORIES|GIT_DISCOVERY_ACROSS_FILESYSTEM|GIT_CONFIG|GIT_CONFIG_GLOBAL|GIT_CONFIG_SYSTEM|GIT_CONFIG_NOSYSTEM|GIT_CONFIG_PARAMETERS|GIT_CONFIG_COUNT|GIT_CONFIG_KEY_*|GIT_CONFIG_VALUE_*) rc=0 ;;
  esac
  shopt -u nocasematch
  return "$rc"
}

# The names that inject configuration that can define an alias.
git_injection_name() { # NAME
  local rc=1
  shopt -s nocasematch
  case "$1" in GIT_CONFIG_PARAMETERS|GIT_CONFIG_COUNT|GIT_CONFIG_KEY_*) rc=0 ;; esac
  shopt -u nocasematch
  return "$rc"
}

# § 7.1: any variable of the list exported into the hook's environment.
inherited_git_variable() {
  local names name rest nl=$'\n'
  names="$(compgen -e)"
  rest="$names$nl"
  while [ -n "$rest" ]; do
    name="${rest%%"$nl"*}"
    rest="${rest#*"$nl"}"
    case "$name" in
      GIT_DIR|GIT_WORK_TREE|GIT_COMMON_DIR|GIT_INDEX_FILE|GIT_OBJECT_DIRECTORY|GIT_ALTERNATE_OBJECT_DIRECTORIES|GIT_NAMESPACE|GIT_CEILING_DIRECTORIES|GIT_DISCOVERY_ACROSS_FILESYSTEM|GIT_CONFIG|GIT_CONFIG_GLOBAL|GIT_CONFIG_SYSTEM|GIT_CONFIG_NOSYSTEM|GIT_CONFIG_PARAMETERS|GIT_CONFIG_COUNT|GIT_CONFIG_KEY_*|GIT_CONFIG_VALUE_*) return 0 ;;
    esac
  done
  return 1
}

# ---------------------------------------------------------------------------
# The command's segments, copied once, and the pipelines (P2): a maximal run
# of top-level segments joined by | or |&. An empty segment right after a |
# that a newline or the & of |& ends belongs to the same pipeline.
# ---------------------------------------------------------------------------
SEGS=()
DELIMS=()
PIPE_OF=()
PIPE_TEXT=()
SEG_PUSH=()
SEG_COMMIT=()
HIDDEN_SEPARATOR_KEPT=0

# The open { } and ( ) groups after a segment: a segment that begins with
# ( or with { and a blank opens one each; a segment that is } or ends with )
# closes one. A count that would go below zero stays at zero. This only ever
# joins segments into a longer pipeline, never splits one.
GROUP_DEPTH=0
group_depth_step() { # SEGMENT
  local s
  trim_horizontal "$1"
  s="$TRIMMED"
  while :; do
    case "$s" in
      '('*) GROUP_DEPTH=$((GROUP_DEPTH + 1)); s="${s#?}" ;;
      '{') GROUP_DEPTH=$((GROUP_DEPTH + 1)); s="" ;;
      '{'[[:blank:]]*) GROUP_DEPTH=$((GROUP_DEPTH + 1)); s="${s#?}" ;;
      *) break ;;
    esac
    trim_horizontal "$s"
    s="$TRIMMED"
  done
  while :; do
    while [ "${s% }" != "$s" ] || [ "${s%$'\t'}" != "$s" ]; do s="${s% }"; s="${s%$'\t'}"; done
    case "$s" in
      '}'|*[[:blank:]]'}') s="${s%\}}" ;;
      *')') s="${s%)}" ;;
      *) break ;;
    esac
    [ "$GROUP_DEPTH" -eq 0 ] || GROUP_DEPTH=$((GROUP_DEPTH - 1))
  done
  return 0
}

build_segments() { # TEXT
  local k=0 n p=0 d prev="" seg text=""
  GROUP_DEPTH=0
  split_command_segments "$1"
  HIDDEN_SEPARATOR_KEPT="$HIDDEN_SEPARATOR"
  SEGS=(${SEGMENTS[@]+"${SEGMENTS[@]}"})
  DELIMS=()
  n=${#SEGS[@]}
  while [ "$k" -lt "$n" ]; do
    DELIMS[k]="${SEGMENT_DELIMITERS[$k]-}"
    k=$((k + 1))
  done
  PIPE_OF=()
  PIPE_TEXT=()
  SEG_PUSH=()
  SEG_COMMIT=()
  k=0
  while [ "$k" -lt "$n" ]; do
    seg="${SEGS[$k]}"
    d="${DELIMS[$k]}"
    PIPE_OF[k]="$p"
    # A newline delimiter is written after a blank, as join_segments does, so
    # a heredoc delimiter at the end of its line stops where the segmenter's
    # did.
    if [ "$d" = $'\n' ]; then text="$text$seg $d"; else text="$text$seg$d"; fi
    text_words "$seg"
    SEG_PUSH[k]="$WT_PUSH"
    SEG_COMMIT[k]="$WT_COMMIT"
    group_depth_step "$seg"
    trim_horizontal "$seg"
    if [ "$d" = '|' ]; then
      :
    elif [ "$prev" = '|' ] && [ -z "$TRIMMED" ] && { [ "$d" = '&' ] || [ "$d" = $'\n' ]; }; then
      :
    elif [ "$GROUP_DEPTH" -gt 0 ]; then
      # Inside a { ... } or ( ... ) group the pipeline goes on: the group is
      # one stage, so `printf ... | { cat; sh; }` feeds the sh.
      :
    else
      PIPE_TEXT[p]="$text"
      text=""
      p=$((p + 1))
    fi
    prev="$d"
    k=$((k + 1))
  done
  [ -z "$text" ] || PIPE_TEXT[p]="$text"
  return 0
}

# ---------------------------------------------------------------------------
# P2: the carriers. CT_TOKENS are the R1 tokens of TEXT as the destructive
# guard's find_carrier reads them: the text itself, the text with every
# backslash deleted, and, when it holds a #, the comment-continuation reading
# and both top-level readings.
# ---------------------------------------------------------------------------
CT_TOKENS=()
CT_TOP=""
CT_TOP_G=""
carrier_tokens() { # TEXT
  local text="$1"
  CT_TOKENS=()
  CT_TOP=""
  CT_TOP_G=""
  r1_normalize "$text"; r1_split_parameter_operators "$R1_NORMALIZED"; r1_tokenize "$R1_OPSPLIT"
  CT_TOKENS+=(${R1_TOKENS[@]+"${R1_TOKENS[@]}"})
  case "$text" in
    *"\\"*)
      r1_normalize "${text//\\/}"; r1_split_parameter_operators "$R1_NORMALIZED"; r1_tokenize "$R1_OPSPLIT"
      CT_TOKENS+=(${R1_TOKENS[@]+"${R1_TOKENS[@]}"})
      ;;
  esac
  case "$text" in
    *'#'*)
      r1_split_comment_continuations "$text"
      r1_normalize "$R1_SPLIT_TEXT"; r1_split_parameter_operators "$R1_NORMALIZED"; r1_tokenize "$R1_OPSPLIT"
      CT_TOKENS+=(${R1_TOKENS[@]+"${R1_TOKENS[@]}"})
      top_level_text "$text"
      CT_TOP="$TOP_LEVEL_TEXT"
      CT_TOP_G="$TOP_LEVEL_TEXT_G"
      r1_normalize "$CT_TOP"; r1_split_parameter_operators "$R1_NORMALIZED"; r1_tokenize "$R1_OPSPLIT"
      CT_TOKENS+=(${R1_TOKENS[@]+"${R1_TOKENS[@]}"})
      r1_normalize "$CT_TOP_G"; r1_split_parameter_operators "$R1_NORMALIZED"; r1_tokenize "$R1_OPSPLIT"
      CT_TOKENS+=(${R1_TOKENS[@]+"${R1_TOKENS[@]}"})
      ;;
  esac
  return 0
}

# True when a here-string operator <<< stands outside quotes in TEXT.
here_string_outside_quotes() { # TEXT
  local s="$1" n=${#1} i=0 c state=plain
  while [ "$i" -lt "$n" ]; do
    c="${s:i:1}"
    case "$state" in
      single) [ "$c" != "'" ] || state=plain ;;
      double)
        case "$c" in
          '"') state=plain ;;
          "\\") i=$((i + 1)) ;;
        esac
        ;;
      plain)
        case "$c" in
          "'") state=single ;;
          '"') state=double ;;
          "\\") i=$((i + 1)) ;;
          '<') [ "${s:i:3}" != '<<<' ] || return 0 ;;
        esac
        ;;
    esac
    i=$((i + 1))
  done
  return 1
}

# § 2.4: true when the command word WORD of SEGMENT is a data consumer: cat,
# tee, echo, printf, true or :; git commit with -F -, -F-, --file - or
# --file=-; gh with --body-file -, --body-file=- or -F -.
data_consumer() { # WORD SEGMENT
  local k n saw_commit=0 rc=1
  case "$1" in
    cat|tee|echo|printf|true|:) return 0 ;;
  esac
  read_segment "$2"
  [ "$RS_STATE" = words ] || return 1
  n=${#RS_WORDS[@]}
  if is_git_spelling "$1"; then
    k=0
    while [ "$k" -lt "$n" ]; do
      [ "${RS_WORDS[$k]}" != commit ] || saw_commit=1
      if [ "$saw_commit" -eq 1 ]; then
        case "${RS_WORDS[$k]}" in
          -F-|--file=-) rc=0 ;;
          -F|--file) [ "${RS_WORDS[$((k + 1))]:-}" != - ] || rc=0 ;;
        esac
      fi
      k=$((k + 1))
    done
    return "$rc"
  fi
  if [ "$1" = gh ]; then
    k=0
    while [ "$k" -lt "$n" ]; do
      case "${RS_WORDS[$k]}" in
        --body-file=-) rc=0 ;;
        --body-file|-F) [ "${RS_WORDS[$((k + 1))]:-}" != - ] || rc=0 ;;
      esac
      k=$((k + 1))
    done
    return "$rc"
  fi
  return 1
}

# § 2.4: the heredoc carrier does not fire when the text is plain, every
# pipeline that holds a heredoc or here-string operator has command words and
# every one of them is a data consumer, and every recorded delimiter is made of
# letters, digits, _ and - and has its closing line later in the text.
heredoc_exempt() { # TEXT
  local text="$1" p=0 k n has_op count w seg j d strip line rest first_op nl=$'\n'
  local -a delims strips words
  plain_text "$text" || return 1
  delims=()
  strips=()
  n=${#SEGS[@]}
  while [ "$p" -lt "${#PIPE_TEXT[@]}" ]; do
    has_op=0
    collect_heredocs "${PIPE_TEXT[$p]}"
    if [ "${#HEREDOC_DELIMS[@]}" -gt 0 ]; then
      has_op=1
      j=0
      while [ "$j" -lt "${#HEREDOC_DELIMS[@]}" ]; do
        delims+=("${HEREDOC_DELIMS[$j]}")
        strips+=("${HEREDOC_STRIP_TABS[$j]}")
        j=$((j + 1))
      done
    fi
    here_string_outside_quotes "${PIPE_TEXT[$p]}" && has_op=1
    if [ "$has_op" -eq 1 ]; then
      count=0
      k=0
      while [ "$k" -lt "$n" ]; do
        if [ "${PIPE_OF[$k]}" = "$p" ]; then
          seg="${SEGS[$k]}"
          collect_command_words "$seg"
          words=(${CMD_WORDS_DEQ[@]+"${CMD_WORDS_DEQ[@]}"})
          for w in ${words[@]+"${words[@]}"}; do
            count=$((count + 1))
            data_consumer "$w" "$seg" || return 1
          done
        fi
        k=$((k + 1))
      done
      [ "$count" -gt 0 ] || return 1
    fi
    p=$((p + 1))
  done
  # Condition 3: a clean delimiter, and its closing line after the first
  # heredoc operator's line.
  first_op="${text%%<<*}"
  case "$first_op" in *"$nl"*) first_op="${first_op##*"$nl"}" ;; esac
  j=0
  while [ "$j" -lt "${#delims[@]}" ]; do
    d="${delims[$j]}"
    [[ "$d" =~ ^[A-Za-z0-9_-]+$ ]] || return 1
    rest="${text#*<<}"
    case "$rest" in *"$nl"*) rest="${rest#*"$nl"}" ;; *) return 1 ;; esac
    strip="${strips[$j]}"
    k=1
    while :; do
      line="${rest%%"$nl"*}"
      if [ "$strip" = 1 ]; then
        while [ "${line#$'\t'}" != "$line" ]; do line="${line#$'\t'}"; done
      fi
      if [ "$line" = "$d" ]; then k=0; break; fi
      case "$rest" in *"$nl"*) rest="${rest#*"$nl"}" ;; *) break ;; esac
    done
    [ "$k" -eq 0 ] || return 1
    j=$((j + 1))
  done
  return 0
}

# The interpreters of P2's interpreter class, by basename in any letter case.
is_interpreter_name() { # WORD
  local name="${1##*/}" rc=1
  shopt -s nocasematch
  case "$name" in
    python|python2|python3|perl|ruby|node|nodejs|deno|bun|php|lua|osascript|tclsh|expect|awk|gawk|mawk|nawk) rc=0 ;;
    python3.*) [[ "$name" =~ ^[Pp][Yy][Tt][Hh][Oo][Nn]3\.[0-9]+$ ]] && rc=0 ;;
  esac
  shopt -u nocasematch
  return "$rc"
}

# True when SEGMENT's command word is an interpreter and any word of the
# segment is -c, -e, -E, -r or --eval.
segment_runs_inline_code() { # SEGMENT
  local k n found=0
  collect_command_words "$1"
  k=0
  while [ "$k" -lt "${#CMD_WORDS_DEQ[@]}" ]; do
    is_interpreter_name "${CMD_WORDS_DEQ[$k]}" && found=1
    k=$((k + 1))
  done
  [ "$found" -eq 1 ] || return 1
  read_segment "$1"
  [ "$RS_STATE" = words ] || return 0
  n=${#RS_WORDS[@]}
  k=0
  while [ "$k" -lt "$n" ]; do
    case "${RS_WORDS[$k]}" in -c|-e|-E|-r|--eval) return 0 ;; esac
    k=$((k + 1))
  done
  return 1
}

# The pipeline-scoped class CLASS in pipeline P: its R1 tokens (the text and
# the text with backslashes deleted) or command words.
pipeline_has_class() { # CLASS P
  local class="$1" p="$2" text="${PIPE_TEXT[$2]}" tok k n rc=1
  local -a toks
  r1_normalize "$text"; r1_split_parameter_operators "$R1_NORMALIZED"; r1_tokenize "$R1_OPSPLIT"
  toks=(${R1_TOKENS[@]+"${R1_TOKENS[@]}"})
  case "$text" in
    *"\\"*)
      r1_normalize "${text//\\/}"; r1_split_parameter_operators "$R1_NORMALIZED"; r1_tokenize "$R1_OPSPLIT"
      toks+=(${R1_TOKENS[@]+"${R1_TOKENS[@]}"})
      ;;
  esac
  case "$class" in
    shell)
      for tok in ${toks[@]+"${toks[@]}"}; do
        if is_shell_name "$tok"; then return 0; fi
      done
      return 1
      ;;
    interpreter)
      n=${#SEGS[@]}
      k=0
      while [ "$k" -lt "$n" ]; do
        if [ "${PIPE_OF[$k]}" = "$p" ] && segment_runs_inline_code "${SEGS[$k]}"; then return 0; fi
        k=$((k + 1))
      done
      return 1
      ;;
  esac
  shopt -s nocasematch
  for tok in ${toks[@]+"${toks[@]}"}; do
    case "$class:$tok" in
      source:source|trap:trap|xargs:xargs|xargs:*/xargs|flock:flock|flock:*/flock) rc=0 ;;
      find_exec:-exec|find_exec:-execdir|find_exec:-ok|find_exec:-okdir) rc=0 ;;
    esac
  done
  shopt -u nocasematch
  [ "$rc" -eq 0 ] && return 0
  if [ "$class" = source ]; then
    collect_command_words "$text"
    k=0
    while [ "$k" -lt "${#CMD_WORDS_DEQ[@]}" ]; do
      [ "${CMD_WORDS_DEQ[$k]}" != . ] || return 0
      k=$((k + 1))
    done
  fi
  return 1
}

# P2's text-wide classes, in order; sets CARRIER_CLASS to the first found.
CARRIER_CLASS=""
text_wide_carrier() { # TEXT
  local text="$1" tok k raw
  local has_eval=0 has_alias=0 has_git=0 has_submodule=0 has_foreach=0 has_rebase=0 has_exec_option=0 has_bisect=0 has_run=0
  CARRIER_CLASS=""
  case "$text" in *'$('*) CARRIER_CLASS=substitution; return 0 ;; esac
  case "$text" in *'`'*) CARRIER_CLASS=backtick; return 0 ;; esac
  case "$text" in *'<('*|*'>('*) CARRIER_CLASS=process_substitution; return 0 ;; esac
  case "$text" in
    *'<<'*) heredoc_exempt "$text" || { CARRIER_CLASS=heredoc; return 0; } ;;
  esac
  carrier_tokens "$text"
  shopt -s nocasematch
  for tok in ${CT_TOKENS[@]+"${CT_TOKENS[@]}"}; do
    case "$tok" in
      eval|*/eval) has_eval=1 ;;
      alias) has_alias=1 ;;
      git|*/git) has_git=1 ;;
      submodule) has_submodule=1 ;;
      foreach) has_foreach=1 ;;
      rebase) has_rebase=1 ;;
      bisect) has_bisect=1 ;;
      run) has_run=1 ;;
    esac
    case "$tok" in
      -x|--exec) has_exec_option=1 ;;
      --*) ;;
      -*x*) has_exec_option=1 ;;
    esac
  done
  shopt -u nocasematch
  [ "$has_eval" -eq 0 ] || { CARRIER_CLASS=eval; return 0; }
  [ "$has_alias" -eq 0 ] || { CARRIER_CLASS=alias; return 0; }
  if env_split_in_option_list "$text"; then CARRIER_CLASS=env_split; return 0; fi
  if [ -n "$CT_TOP" ] && env_split_in_option_list "$CT_TOP"; then CARRIER_CLASS=env_split; return 0; fi
  if [ -n "$CT_TOP_G" ] && env_split_in_option_list "$CT_TOP_G"; then CARRIER_CLASS=env_split; return 0; fi
  collect_command_words "$text"
  k=0
  while [ "$k" -lt "${#CMD_WORDS_RAW[@]}" ]; do
    raw="${CMD_WORDS_RAW[$k]}"
    case "$raw" in
      *'$'*|*'`'*|=*) CARRIER_CLASS=variable_command; return 0 ;;
    esac
    if command_word_is_pattern "$raw"; then CARRIER_CLASS=variable_command; return 0; fi
    k=$((k + 1))
  done
  [ "$has_git" -eq 0 ] || [ "$has_submodule" -eq 0 ] || [ "$has_foreach" -eq 0 ] || { CARRIER_CLASS=git_foreach; return 0; }
  [ "$has_git" -eq 0 ] || [ "$has_rebase" -eq 0 ] || [ "$has_exec_option" -eq 0 ] || { CARRIER_CLASS=git_rebase_exec; return 0; }
  [ "$has_git" -eq 0 ] || [ "$has_bisect" -eq 0 ] || [ "$has_run" -eq 0 ] || { CARRIER_CLASS=git_bisect_run; return 0; }
  return 1
}

# P2's pipeline-scoped classes, in order: a class fires only in a pipeline
# that also holds the push word or a $ anywhere in its raw text.
pipeline_carrier() {
  local class p
  CARRIER_CLASS=""
  for class in source trap xargs find_exec shell flock interpreter; do
    p=0
    while [ "$p" -lt "${#PIPE_TEXT[@]}" ]; do
      if pipeline_is_live "$p" && pipeline_has_class "$class" "$p"; then
        CARRIER_CLASS="$class"
        return 0
      fi
      p=$((p + 1))
    done
  done
  return 1
}

pipeline_is_live() { # P
  case "${PIPE_TEXT[$1]}" in *'$'*) return 0 ;; esac
  text_words "${PIPE_TEXT[$1]}"
  [ "$WT_PUSH" -eq 1 ]
}

# ---------------------------------------------------------------------------
# § 7.2, per segment: configuration injection on the command line.
# ---------------------------------------------------------------------------
segment_injects_config() { # SEGMENT
  local k n w hit=0 prev=""
  read_segment "$1"
  case "$RS_STATE" in
    unsplit)
      # A segment that does not split is read by its R1 tokens instead.
      r1_normalize "$1"; r1_split_parameter_operators "$R1_NORMALIZED"; r1_tokenize "$R1_OPSPLIT"
      shopt -s nocasematch
      for w in ${R1_TOKENS[@]+"${R1_TOKENS[@]}"}; do
        case "$w" in
          --config-env|alias.*|GIT_CONFIG_PARAMETERS|GIT_CONFIG_COUNT|GIT_CONFIG_KEY_*) hit=1 ;;
        esac
        if [ "$prev" = "-c" ]; then config_key_injects "$w" && hit=1; fi
        case "$w" in -c?*) config_key_injects "${w#-c}" && hit=1 ;; esac
        prev="$w"
      done
      shopt -u nocasematch
      return $((1 - hit))
      ;;
    words) ;;
    *) return 1 ;;
  esac
  reaching_names
  k=0
  while [ "$k" -lt "${#RN_NAMES[@]}" ]; do
    git_injection_name "${RN_NAMES[$k]}" && return 0
    k=$((k + 1))
  done
  [ "$RS_PREFIX_OK" -eq 1 ] || return 1
  n=${#RS_WORDS[@]}
  [ "$RS_CMD" -lt "$n" ] || return 1
  is_git_spelling "${RS_RAW[$RS_CMD]}" || return 1
  git_global_walk $((RS_CMD + 1))
  [ "$GW_INJECT" -eq 0 ] || return 0
  k=$((RS_CMD + 1))
  shopt -s nocasematch
  while [ "$k" -lt "$n" ]; do
    case "${RS_WORDS[$k]}" in
      alias.*|--config-env|--config-env=*) shopt -u nocasematch; return 0 ;;
    esac
    k=$((k + 1))
  done
  shopt -u nocasematch
  return 1
}

# A reaching GIT_ name of § 7.1's list in the current segment.
segment_sets_git_environment() { # SEGMENT
  local k
  read_segment "$1"
  [ "$RS_STATE" = words ] || return 1
  reaching_names
  k=0
  while [ "$k" -lt "${#RN_NAMES[@]}" ]; do
    git_environment_name "${RN_NAMES[$k]}" && return 0
    k=$((k + 1))
  done
  return 1
}

# ---------------------------------------------------------------------------
# P4 to P7: one segment that holds the push word.
# ---------------------------------------------------------------------------
judge_push_segment() { # INDEX
  local idx="$1" n cmd raw deq base k
  read_segment "${SEGS[$idx]}"
  case "$RS_STATE" in
    unsplit) record_code push_unreadable_segment; return 0 ;;
    words) ;;
    *) return 0 ;;
  esac
  if [ "$RS_PREFIX_OK" -eq 0 ]; then record_code push_unsupported_prefix; return 0; fi
  n=${#RS_WORDS[@]}
  cmd="$RS_CMD"
  [ "$cmd" -lt "$n" ] || return 0
  raw="${RS_RAW[$cmd]}"
  deq="${RS_WORDS[$cmd]}"
  base="${deq##*/}"
  if is_git_spelling "$raw"; then
    judge_git_segment "$idx" $((cmd + 1))
    return 0
  fi
  if nocase_equals "$base" git-push; then
    GW_REPO=0
    GW_CLIT=1
    GW_CPATHS=()
    judge_push_words "$idx" $((cmd + 1))
    return 0
  fi
  if nocase_equals "$base" git-send-pack; then record_code push_send_pack; return 0; fi
  shopt -s nocasematch
  case "$base" in
    echo|printf|cat) shopt -u nocasematch; return 0 ;;
  esac
  shopt -u nocasematch
  k=$((cmd + 1))
  while [ "$k" -lt "$n" ]; do
    if is_git_spelling "${RS_RAW[$k]}" || nocase_equals "${RS_WORDS[$k]##*/}" git-push || nocase_equals "${RS_WORDS[$k]##*/}" git-send-pack; then
      record_code push_beside_wrapper
      return 0
    fi
    k=$((k + 1))
  done
  return 0
}

# P5 after the git word at START - 1.
judge_git_segment() { # INDEX START
  local idx="$1" n=${#RS_WORDS[@]} sub k
  git_global_walk "$2"
  if [ "$GW_UNKNOWN" -eq 1 ]; then record_code push_unknown_git_option; return 0; fi
  [ "$GW_SUB" -lt "$n" ] || return 0
  if ! raw_word_literal "${RS_RAW[$GW_SUB]}" "${RS_OBS[$GW_SUB]}"; then
    record_code push_unreadable_word
    return 0
  fi
  sub="${RS_WORDS[$GW_SUB]}"
  if nocase_equals "$sub" push; then
    [ "$GW_CONFIG" -eq 0 ] || record_code push_config_override
    judge_push_words "$idx" $((GW_SUB + 1))
  elif nocase_equals "$sub" send-pack; then
    record_code push_send_pack
  elif nocase_equals "$sub" subtree; then
    k=$((GW_SUB + 1))
    while [ "$k" -lt "$n" ]; do
      if nocase_equals "${RS_WORDS[$k]}" push; then record_code push_subtree; return 0; fi
      k=$((k + 1))
    done
  elif nocase_equals "$sub" config; then
    k=$((GW_SUB + 1))
    shopt -s nocasematch
    while [ "$k" -lt "$n" ]; do
      case "${RS_WORDS[$k]}" in
        alias.*) shopt -u nocasematch; record_code push_alias_definition; return 0 ;;
      esac
      k=$((k + 1))
    done
    shopt -u nocasematch
  fi
  return 0
}

# git push's long options, for git's unique-prefix abbreviations.
PUSH_LONG_OPTIONS="force force-with-lease force-if-includes delete mirror prune all branches tags receive-pack exec repo push-option recurse-submodules dry-run porcelain follow-tags signed atomic set-upstream thin quiet verbose progress verify ipv4 ipv6"
# Sets LONG_NAME to the option NAME names in the space-separated LIST, by its
# exact name or a unique prefix; LONG_NAME is "" when NAME names none, and
# "?" when it is a prefix of several.
LONG_NAME=""
LONG_CANDIDATES=""
resolve_long_option() { # NAME LIST
  local name="$1" option count=0
  LONG_NAME=""
  LONG_CANDIDATES=""
  [ -n "$name" ] || return 0
  for option in $2; do
    if [ "$option" = "$name" ]; then LONG_NAME="$option"; LONG_CANDIDATES="$option"; return 0; fi
  done
  for option in $2; do
    case "$option" in
      "$name"*) count=$((count + 1)); LONG_NAME="$option"; LONG_CANDIDATES="$LONG_CANDIDATES $option" ;;
    esac
  done
  [ "$count" -le 1 ] || LONG_NAME="?"
  return 0
}

# P6 and P7: the words after push, from START. Force-gate and presumed codes
# are recorded in reading order; main-gate codes after them.
judge_push_words() { # INDEX START
  local idx="$1" i="$2" n=${#RS_WORDS[@]} w raw name has_eq after_dd=0 j c rest
  local delete=0 mirror=0 all=0 operands_n=0 need_p7=0 no_refspec=0 r dst value_next
  local -a operands main_codes
  operands=()
  main_codes=()
  while [ "$i" -lt "$n" ]; do
    w="${RS_WORDS[$i]}"
    raw="${RS_RAW[$i]}"
    if ! raw_word_literal "$raw" "${RS_OBS[$i]}"; then
      record_code push_unreadable_word
      i=$((i + 1))
      continue
    fi
    if [ "$after_dd" -eq 1 ]; then
      operands+=("$w")
      i=$((i + 1))
      continue
    fi
    value_next=0
    case "$w" in
      --)
        after_dd=1
        ;;
      --*)
        name="${w#--}"
        has_eq=0
        case "$name" in *=*) has_eq=1; name="${name%%=*}" ;; esac
        case "$name" in
          no-*)
            resolve_long_option "${name#no-}" "$PUSH_LONG_OPTIONS"
            case "$LONG_NAME" in
              ''|'?') record_code push_unknown_option ;;
              *) [ "$has_eq" -eq 0 ] || record_code push_unknown_option ;;
            esac
            ;;
          *)
            resolve_long_option "$name" "$PUSH_LONG_OPTIONS"
            case "$LONG_NAME" in
              force) [ "$has_eq" -eq 0 ] && record_code push_force || record_code push_unknown_option ;;
              force-with-lease) record_code push_force ;;
              force-if-includes) [ "$has_eq" -eq 0 ] && record_code push_force || record_code push_unknown_option ;;
              delete) delete=1; [ "$has_eq" -eq 0 ] && record_code push_delete || record_code push_unknown_option ;;
              mirror) mirror=1; [ "$has_eq" -eq 0 ] && record_code push_mirror || record_code push_unknown_option ;;
              prune) [ "$has_eq" -eq 0 ] && record_code push_prune || record_code push_unknown_option ;;
              all|branches) all=1; [ "$has_eq" -eq 0 ] || record_code push_unknown_option ;;
              receive-pack|exec) record_code push_receive_pack; [ "$has_eq" -eq 1 ] || value_next=1 ;;
              repo|push-option|recurse-submodules) [ "$has_eq" -eq 1 ] || value_next=1 ;;
              signed) ;;
              tags|dry-run|porcelain|follow-tags|atomic|set-upstream|thin|quiet|verbose|progress|verify|ipv4|ipv6)
                [ "$has_eq" -eq 0 ] || record_code push_unknown_option ;;
              *) record_code push_unknown_option ;;
            esac
            ;;
        esac
        ;;
      -)
        operands+=("$w")
        ;;
      -*)
        j=1
        while [ "$j" -lt "${#w}" ]; do
          c="${w:j:1}"
          case "$c" in
            f) record_code push_force ;;
            d) delete=1; record_code push_delete ;;
            n|q|u|v|4|6) ;;
            o)
              rest="${w:j+1}"
              [ -n "$rest" ] || value_next=1
              break
              ;;
            *) record_code push_unknown_option; break ;;
          esac
          j=$((j + 1))
        done
        ;;
      *)
        operands+=("$w")
        ;;
    esac
    i=$((i + 1))
    if [ "$value_next" -eq 1 ] && [ "$i" -lt "$n" ]; then
      raw_word_literal "${RS_RAW[$i]}" "${RS_OBS[$i]}" || record_code push_unreadable_word
      i=$((i + 1))
    fi
  done
  [ "$mirror" -eq 0 ] || main_codes+=(push_all_branches)
  [ "$all" -eq 0 ] || main_codes+=(push_all_branches)
  operands_n=${#operands[@]}
  if [ "$operands_n" -ge 1 ]; then
    # The repository word, read as one refspec for the main gate only.
    r="${operands[0]#+}"
    case "$r" in *:*) dst="${r##*:}" ;; *) dst="$r" ;; esac
    if [ -n "$dst" ] && protected_name "$dst"; then main_codes+=(push_main_branch); fi
  fi
  j=1
  while [ "$j" -lt "$operands_n" ]; do
    r="${operands[$j]}"
    j=$((j + 1))
    if [ "$delete" -eq 1 ]; then
      r="${r#+}"
      r="${r#:}"
      if protected_name "$r"; then main_codes+=(push_main_branch); fi
      continue
    fi
    case "$r" in
      +*) record_code push_force; r="${r#+}" ;;
    esac
    case "$r" in
      :)
        main_codes+=(push_matching_refspec)
        ;;
      :*)
        record_code push_delete
        dst="${r#:}"
        if protected_name "$dst"; then main_codes+=(push_main_branch); fi
        ;;
      *:*)
        dst="${r##*:}"
        case "$dst" in
          *'*'*) main_codes+=(push_pattern_refspec) ;;
          *)
            if names_current_branch "$dst"; then
              need_p7=1
            elif protected_name "$dst"; then
              main_codes+=(push_main_branch)
            fi
            ;;
        esac
        ;;
      *)
        case "$r" in
          *'*'*) main_codes+=(push_pattern_refspec) ;;
          *)
            if names_current_branch "$r"; then
              need_p7=1
            elif protected_name "$r"; then
              main_codes+=(push_main_branch)
            fi
            ;;
        esac
        ;;
    esac
  done
  if [ "$operands_n" -le 1 ] && [ "$mirror" -eq 0 ] && [ "$all" -eq 0 ] && [ "$delete" -eq 0 ]; then
    need_p7=1
    no_refspec=1
  fi
  j=0
  while [ "$j" -lt "${#main_codes[@]}" ]; do
    record_code "${main_codes[$j]}"
    j=$((j + 1))
  done
  if [ "$need_p7" -eq 1 ]; then
    resolve_target "$idx" "$no_refspec"
  fi
  return 0
}

# ---------------------------------------------------------------------------
# P7 and § 7.3: the target of a push with no destination in its text.
# ---------------------------------------------------------------------------

# The git subcommands an earlier segment may run without changing the branch
# or the configuration a later push reads.
known_safe_git_subcommand() { # WORD
  case "$1" in
    add|commit|status|log|diff|show|fetch|pull|push|merge|tag|stash|reset|restore|rev-parse|rev-list|ls-files|ls-remote|grep|describe|shortlog|blame|clean|apply|revert|cherry-pick|show-ref|for-each-ref|merge-base|diff-tree|cat-file|reflog|mv|rm|notes|gc|version|help) return 0 ;;
  esac
  return 1
}

# True when every delimiter from segment FROM up to segment TO (not included)
# is &&.
and_chain() { # FROM TO
  local k="$1"
  while [ "$k" -lt "$2" ]; do
    [ "${DELIMS[$k]}" = '&&' ] || return 1
    k=$((k + 1))
  done
  return 0
}

# Joins a cd or -C path to FOLDER as git and cd do.
JOINED_FOLDER=""
join_folder() { # FOLDER PATH
  case "$2" in
    /*) JOINED_FOLDER="$2" ;;
    *) JOINED_FOLDER="$1/$2" ;;
  esac
}

# Runs one read-only git question under a fixed environment: only HOME and
# XDG_CONFIG_HOME (when set and absolute) reach it from the hook's own.
TR_OUT=""
TR_RC=0
target_read() { # FOLDER ARGS...
  local folder="$1"
  local -a fixed
  shift
  fixed=()
  case "${HOME:-}" in /*) fixed+=("HOME=$HOME") ;; esac
  case "${XDG_CONFIG_HOME:-}" in /*) fixed+=("XDG_CONFIG_HOME=$XDG_CONFIG_HOME") ;; esac
  fixed+=(LC_ALL=C PATH=/usr/bin:/bin GIT_TERMINAL_PROMPT=0 GIT_OPTIONAL_LOCKS=0)
  TR_RC=0
  TR_OUT="$(/usr/bin/env -i "${fixed[@]}" "$GIT_BIN" --no-pager -C "$folder" "$@" </dev/null 2>/dev/null)" || TR_RC=$?
  return 0
}

# An earlier segment that can change the branch or the configuration before
# the push runs makes the target unresolved: a shell, shell runner,
# interpreter, eval, source, ., xargs, flock, trap, alias, env -S, a variable
# command word, find -exec, a reaching GIT_ name, or a git command that
# switches branches or writes configuration. Sets EARLIER_UNRESOLVED, and
# tracks the folder (cd, pushd) and a branch set by checkout -b.
EARLIER_UNRESOLVED=0
EARLIER_FOLDER=""
EARLIER_BRANCH=""
EARLIER_BRANCH_FOLDER=""
earlier_segment() { # INDEX PUSH_INDEX
  local idx="$1" push_idx="$2" n cmd raw deq base k w sub name tok
  read_segment "${SEGS[$idx]}"
  case "$RS_STATE" in
    empty|comment|arith) return 0 ;;
    unsplit) EARLIER_UNRESOLVED=1; return 0 ;;
  esac
  reaching_names
  k=0
  while [ "$k" -lt "${#RN_NAMES[@]}" ]; do
    if git_environment_name "${RN_NAMES[$k]}"; then EARLIER_UNRESOLVED=1; return 0; fi
    k=$((k + 1))
  done
  [ "$RS_PREFIX_OK" -eq 1 ] || { EARLIER_UNRESOLVED=1; return 0; }
  n=${#RS_WORDS[@]}
  cmd="$RS_CMD"
  [ "$cmd" -lt "$n" ] || return 0
  # Carrier words anywhere in the segment.
  r1_normalize "${SEGS[$idx]}"; r1_split_parameter_operators "$R1_NORMALIZED"; r1_tokenize "$R1_OPSPLIT"
  shopt -s nocasematch
  for tok in ${R1_TOKENS[@]+"${R1_TOKENS[@]}"}; do
    case "$tok" in
      eval|*/eval|source|trap|alias|xargs|*/xargs|flock|*/flock|-exec|-execdir|-ok|-okdir) shopt -u nocasematch; EARLIER_UNRESOLVED=1; return 0 ;;
    esac
  done
  shopt -u nocasematch
  raw="${RS_RAW[$cmd]}"
  deq="${RS_WORDS[$cmd]}"
  base="${deq##*/}"
  case "$raw" in *'$'*|*'`'*|=*) EARLIER_UNRESOLVED=1; return 0 ;; esac
  if command_word_is_pattern "$raw"; then EARLIER_UNRESOLVED=1; return 0; fi
  if is_shell_name "$deq" || is_interpreter_name "$deq" || [ "$deq" = . ]; then EARLIER_UNRESOLVED=1; return 0; fi
  if [ "$base" = cd ] || [ "$base" = pushd ]; then
    if [ $((cmd + 2)) -ne "$n" ]; then EARLIER_UNRESOLVED=1; return 0; fi
    w="${RS_WORDS[$((cmd + 1))]}"
    raw_word_literal "${RS_RAW[$((cmd + 1))]}" "${RS_OBS[$((cmd + 1))]}" || { EARLIER_UNRESOLVED=1; return 0; }
    case "$w" in ''|-*|'~'*) EARLIER_UNRESOLVED=1; return 0 ;; esac
    and_chain "$idx" "$push_idx" || { EARLIER_UNRESOLVED=1; return 0; }
    join_folder "$EARLIER_FOLDER" "$w"
    EARLIER_FOLDER="$JOINED_FOLDER"
    return 0
  fi
  if [ "$base" = popd ]; then EARLIER_UNRESOLVED=1; return 0; fi
  if [ "$base" = gh ]; then
    if [ "${RS_WORDS[$((cmd + 1))]:-}" = pr ]; then
      case "${RS_WORDS[$((cmd + 2))]:-}" in checkout|co) EARLIER_UNRESOLVED=1; return 0 ;; esac
    fi
    return 0
  fi
  if is_git_spelling "$raw"; then
    git_global_walk $((cmd + 1))
    [ "$GW_UNKNOWN" -eq 0 ] || { EARLIER_UNRESOLVED=1; return 0; }
    [ "$GW_SUB" -lt "$n" ] || return 0
    raw_word_literal "${RS_RAW[$GW_SUB]}" "${RS_OBS[$GW_SUB]}" || { EARLIER_UNRESOLVED=1; return 0; }
    sub="${RS_WORDS[$GW_SUB]}"
    case "$sub" in
      checkout|switch)
        # checkout -b NAME or switch -c NAME, with a literal NAME, no folder
        # or repository option of its own, and only && up to the push.
        w="${RS_WORDS[$((GW_SUB + 1))]:-}"
        case "$sub:$w" in
          checkout:-b|checkout:-B|switch:-c|switch:-C) ;;
          *) EARLIER_UNRESOLVED=1; return 0 ;;
        esac
        k=$((GW_SUB + 2))
        if [ "$k" -ge "$n" ] || [ $((n - k)) -gt 2 ]; then EARLIER_UNRESOLVED=1; return 0; fi
        name="${RS_WORDS[$k]}"
        raw_word_literal "${RS_RAW[$k]}" "${RS_OBS[$k]}" || { EARLIER_UNRESOLVED=1; return 0; }
        case "$name" in ''|-*) EARLIER_UNRESOLVED=1; return 0 ;; esac
        if [ $((k + 1)) -lt "$n" ]; then
          raw_word_literal "${RS_RAW[$((k + 1))]}" "${RS_OBS[$((k + 1))]}" || { EARLIER_UNRESOLVED=1; return 0; }
          case "${RS_WORDS[$((k + 1))]}" in -*) EARLIER_UNRESOLVED=1; return 0 ;; esac
        fi
        [ "${#GW_CPATHS[@]}" -eq 0 ] && [ "$GW_REPO" -eq 0 ] || { EARLIER_UNRESOLVED=1; return 0; }
        and_chain "$idx" "$push_idx" || { EARLIER_UNRESOLVED=1; return 0; }
        EARLIER_BRANCH="$name"
        EARLIER_BRANCH_FOLDER="$EARLIER_FOLDER"
        return 0
        ;;
    esac
    known_safe_git_subcommand "$sub" || { EARLIER_UNRESOLVED=1; return 0; }
    return 0
  fi
  return 0
}

# Resolves the current branch for the push in segment INDEX and records its
# main-gate code. NO_REFSPEC is 1 for a push with no refspec at all.
resolve_target() { # INDEX NO_REFSPEC
  local idx="$1" no_refspec="$2" k folder branch remap=0 value
  local -a cpaths
  cpaths=(${GW_CPATHS[@]+"${GW_CPATHS[@]}"})
  if [ "$GW_REPO" -eq 1 ] || [ "$GW_CLIT" -eq 0 ]; then record_code push_main_unresolved; return 0; fi
  case "$cwd_raw" in /*) ;; *) record_code push_main_unresolved; return 0 ;; esac
  EARLIER_UNRESOLVED=0
  EARLIER_FOLDER="$cwd_raw"
  EARLIER_BRANCH=""
  EARLIER_BRANCH_FOLDER=""
  k=0
  while [ "$k" -lt "$idx" ]; do
    earlier_segment "$k" "$idx"
    [ "$EARLIER_UNRESOLVED" -eq 0 ] || { record_code push_main_unresolved; return 0; }
    k=$((k + 1))
  done
  folder="$EARLIER_FOLDER"
  k=0
  while [ "$k" -lt "${#cpaths[@]}" ]; do
    join_folder "$folder" "${cpaths[$k]}"
    folder="$JOINED_FOLDER"
    k=$((k + 1))
  done
  branch=""
  if [ -n "$EARLIER_BRANCH" ]; then
    [ "$EARLIER_BRANCH_FOLDER" = "$folder" ] || { record_code push_main_unresolved; return 0; }
    branch="$EARLIER_BRANCH"
  fi
  check_tools "${TARGET_TOOLS[@]}" || policy_deny_dependency
  if [ -z "$branch" ]; then
    target_read "$folder" symbolic-ref --quiet --short HEAD
    case "$TR_OUT" in *$'\n'*) TR_OUT="" ;; esac
    if [ "$TR_RC" -ne 0 ] || [ -z "$TR_OUT" ]; then record_code push_main_unresolved; return 0; fi
    branch="$TR_OUT"
  fi
  if [ "$no_refspec" -eq 1 ]; then
    target_read "$folder" config --get push.default
    case "$TR_RC" in
      0)
        value="$TR_OUT"
        case "$value" in simple|current) ;; *) remap=1 ;; esac
        ;;
      1) ;;
      *) record_code push_main_unresolved; return 0 ;;
    esac
  fi
  target_read "$folder" config --get-regexp '^remote\..*\.(push|mirror)$'
  case "$TR_RC" in
    0) remap=1 ;;
    1) ;;
    *) record_code push_main_unresolved; return 0 ;;
  esac
  if protected_name "$branch"; then record_code push_main_branch; fi
  [ "$remap" -eq 0 ] || record_code push_config_remap
  return 0
}

# ---------------------------------------------------------------------------
# C1: commit style on one top-level segment.
# ---------------------------------------------------------------------------

# git commit's long options, for git's unique-prefix abbreviations.
COMMIT_LONG_OPTIONS="quiet verbose file author date message reedit-message reuse-message fixup squash reset-author trailer signoff template edit cleanup status gpg-sign all include interactive patch only no-verify verify dry-run short branch ahead-behind porcelain long null amend no-post-rewrite post-rewrite untracked-files pathspec-from-file pathspec-file-nul"
CONVENTIONAL_RE='^(feat|fix|docs|refactor|chore|test|ci|build|perf|style|revert)(\([^)]*\))?!?: '

# True when RAW, the raw word that holds a message value, holds a $ or a
# backtick outside single quotes, other than the opener of an ANSI-C quote.
message_word_has_expansion() { # RAW
  local s="$1" n=${#1} i=0 c state=plain
  while [ "$i" -lt "$n" ]; do
    c="${s:i:1}"
    case "$state" in
      single) [ "$c" != "'" ] || state=plain ;;
      ansi)
        case "$c" in
          "\\") i=$((i + 1)) ;;
          "'") state=plain ;;
        esac
        ;;
      double)
        case "$c" in
          '"') state=plain ;;
          '$'|'`') return 0 ;;
          "\\") i=$((i + 1)); case "${s:i:1}" in '$'|'`') return 0 ;; esac ;;
        esac
        ;;
      plain)
        case "$c" in
          "'") state=single ;;
          '"') state=double ;;
          "\\") i=$((i + 1)); case "${s:i:1}" in '$'|'`') return 0 ;; esac ;;
          '`') return 0 ;;
          '$')
            if [ "${s:i+1:1}" = "'" ]; then state=ansi; i=$((i + 1)); else return 0; fi
            ;;
        esac
        ;;
    esac
    i=$((i + 1))
  done
  return 1
}

# The one substitution shape Claude Code writes for a multi-line message: a
# value that is exactly a $(cat <<'D' line (or <<"D"), body lines, a line that
# is exactly D, then ) with only blanks and newlines before it. Sets
# HEREDOC_SUBJECT to the first body line.
HEREDOC_SUBJECT=""
HEREDOC_CLOSE_RE='^[[:space:]]*[)]$'
heredoc_message_subject() { # VALUE
  local v="$1" first d rest line nl=$'\n' subject_set=0
  HEREDOC_SUBJECT=""
  first="${v%%"$nl"*}"
  [ "$first" != "$v" ] || return 1
  case "$first" in
    "\$(cat <<'"*"'") d="${first#"\$(cat <<'"}"; d="${d%"'"}" ;;
    "\$(cat <<\""*'"') d="${first#"\$(cat <<\""}"; d="${d%\"}" ;;
    *) return 1 ;;
  esac
  [[ "$d" =~ ^[A-Za-z0-9_-]+$ ]] || return 1
  rest="${v#*"$nl"}"
  while :; do
    line="${rest%%"$nl"*}"
    if [ "$line" = "$d" ]; then
      if [ "$line" = "$rest" ]; then return 1; fi
      rest="${rest#*"$nl"}"
      [[ "$rest" =~ $HEREDOC_CLOSE_RE ]] || return 1
      return 0
    fi
    if [ "$subject_set" -eq 0 ]; then HEREDOC_SUBJECT="$line"; subject_set=1; fi
    [ "$line" != "$rest" ] || return 1
    rest="${rest#*"$nl"}"
  done
}

# True when NAME (with no leading dashes) is a non-empty prefix of message.
message_prefix() { # NAME
  [ -n "$1" ] && case message in "$1"*) true ;; *) false ;; esac
}

judge_commit_segment() { # SEGMENT
  local seg="$1" n cmd raw deq base sub start
  read_segment "$seg"
  case "$RS_STATE" in
    words) ;;
    unsplit)
      text_words "$seg"
      [ "$WT_COMMIT" -eq 0 ] || record_code commit_message_unreadable
      return 0
      ;;
    *) return 0 ;;
  esac
  n=${#RS_WORDS[@]}
  if [ "$RS_PREFIX_OK" -eq 0 ]; then
    text_words "$seg"
    [ "$WT_COMMIT" -eq 0 ] || record_code commit_message_unreadable
    return 0
  fi
  cmd="$RS_CMD"
  [ "$cmd" -lt "$n" ] || return 0
  raw="${RS_RAW[$cmd]}"
  deq="${RS_WORDS[$cmd]}"
  base="${deq##*/}"
  if is_git_spelling "$raw"; then
    git_global_walk $((cmd + 1))
    if [ "$GW_UNKNOWN" -eq 1 ]; then
      text_words "$seg"
      [ "$WT_COMMIT" -eq 0 ] || record_code commit_message_unreadable
      return 0
    fi
    [ "$GW_SUB" -lt "$n" ] || return 0
    if ! raw_word_literal "${RS_RAW[$GW_SUB]}" "${RS_OBS[$GW_SUB]}"; then
      text_words "$seg"
      [ "$WT_COMMIT" -eq 0 ] || record_code commit_message_unreadable
      return 0
    fi
    sub="${RS_WORDS[$GW_SUB]}"
    nocase_equals "$sub" commit || return 0
    start=$((GW_SUB + 1))
  elif nocase_equals "$base" git-commit; then
    start=$((cmd + 1))
  else
    return 0
  fi
  commit_role_walk "$start"
}

# The words after commit, from START, walked once left to right: each word is
# an option, a value or an operand before any exemption is looked at. Only the
# value-taking options listed here consume a separate following word: -m, -F,
# -C, -c, -t and the long forms (and unique prefixes) of --message, --file,
# --reuse-message, --reedit-message, --template, --fixup, --squash, --author,
# --date, --cleanup, --trailer and --pathspec-from-file. --gpg-sign and
# --untracked-files take only an attached = value, as -S and -u take only an
# attached one; every other long option takes no value.
commit_role_walk() { # START
  local i="$1" n=${#RS_WORDS[@]} w name has_eq j c rest exempt=0 found=0
  local msg_value="" msg_raw="" msg_obs=0 consume subject
  while [ "$i" -lt "$n" ]; do
    w="${RS_WORDS[$i]}"
    consume=0
    case "$w" in
      --?*)
        name="${w#--}"
        has_eq=0
        case "$name" in *=*) has_eq=1; name="${name%%=*}" ;; esac
        if message_prefix "$name"; then
          if [ "$has_eq" -eq 1 ]; then
            if [ "$found" -eq 0 ]; then found=1; msg_value="${w#*=}"; msg_raw="${RS_RAW[$i]}"; msg_obs="${RS_OBS[$i]}"; fi
          else
            consume=1
            if [ "$found" -eq 0 ] && [ $((i + 1)) -lt "$n" ]; then
              found=1; msg_value="${RS_WORDS[$((i + 1))]}"; msg_raw="${RS_RAW[$((i + 1))]}"; msg_obs="${RS_OBS[$((i + 1))]}"
            fi
          fi
        else
          resolve_long_option "$name" "$COMMIT_LONG_OPTIONS"
          case "$LONG_NAME" in
            amend) exempt=1 ;;
            fixup|squash|file|reuse-message|reedit-message)
              exempt=1
              [ "$has_eq" -eq 1 ] || consume=1
              ;;
            template|author|date|cleanup|trailer|pathspec-from-file)
              [ "$has_eq" -eq 1 ] || consume=1
              ;;
          esac
        fi
        ;;
      -?*)
        j=1
        while [ "$j" -lt "${#w}" ]; do
          c="${w:j:1}"
          rest="${w:j+1}"
          case "$c" in
            m)
              if [ -n "$rest" ]; then
                if [ "$found" -eq 0 ]; then found=1; msg_value="$rest"; msg_raw="${RS_RAW[$i]}"; msg_obs="${RS_OBS[$i]}"; fi
              else
                consume=1
                if [ "$found" -eq 0 ] && [ $((i + 1)) -lt "$n" ]; then
                  found=1; msg_value="${RS_WORDS[$((i + 1))]}"; msg_raw="${RS_RAW[$((i + 1))]}"; msg_obs="${RS_OBS[$((i + 1))]}"
                fi
              fi
              break
              ;;
            F|C|c)
              exempt=1
              [ -n "$rest" ] || consume=1
              break
              ;;
            t)
              [ -n "$rest" ] || consume=1
              break
              ;;
            u|S) break ;;
          esac
          j=$((j + 1))
        done
        ;;
    esac
    i=$((i + 1))
    [ "$consume" -eq 0 ] || i=$((i + 1))
  done
  [ "$exempt" -eq 0 ] || return 0
  [ "$found" -eq 1 ] || return 0
  if [ "$msg_obs" != 0 ]; then
    record_code commit_message_unreadable
    return 0
  fi
  if message_word_has_expansion "$msg_raw"; then
    if heredoc_message_subject "$msg_value"; then
      subject="$HEREDOC_SUBJECT"
    else
      record_code commit_message_unreadable
      return 0
    fi
  else
    subject="${msg_value%%$'\n'*}"
  fi
  [[ "$subject" =~ $CONVENTIONAL_RE ]] || record_code commit_nonconventional
  return 0
}

# ---------------------------------------------------------------------------
# The engine.
# ---------------------------------------------------------------------------
MAX_COMMAND_CHARS=6000
cwd_raw=""

run_policy() {
  local normalized command_raw command_len k n
  [ "$ARG_ERROR" -eq 0 ] || policy_deny invalid_configuration
  for k in "$OPT_MAIN" "$OPT_FORCE" "$OPT_COMMIT"; do
    case "$k" in ask|deny|off|invalid) ;; *) policy_deny invalid_configuration ;; esac
  done
  # The engine tier: jq, wc and tr, from the trusted roots.
  check_tools "${ENGINE_TOOLS[@]}" || policy_deny_dependency
  # jq runs with HOME=/dev/null, so no $HOME/.jq can redefine a function
  # these filters use.
  normalized="$(HOME=/dev/null "$JQ_BIN" -cers '
    select(
      length == 1 and
      (.[0] | type == "object" and keys == ["command","cwd"] and
        (.cwd | type == "string" and length > 0 and (test("[\u0000-\u0008\u000b-\u001f\u007f]") | not)) and
        (.command | type == "string" and length > 0 and (test("[\u0000-\u0008\u000b-\u001f\u007f]") | not)))
    ) | .[0] | {cwd:.cwd,command:.command}
  ' 2>/dev/null)" || policy_deny malformed_normalized_input
  [ -n "$normalized" ] || policy_deny malformed_normalized_input
  cwd_raw="$(printf '%s' "$normalized" | HOME=/dev/null "$JQ_BIN" -er '.cwd' 2>/dev/null)" || policy_deny extraction_failure
  command_raw="$(printf '%s' "$normalized" | HOME=/dev/null "$JQ_BIN" -er '.command' 2>/dev/null)" || policy_deny extraction_failure
  [ -n "$cwd_raw" ] && [ -n "$command_raw" ] || policy_deny extraction_failure
  # Bytes, counted on the JSON value itself (a substitution drops trailing
  # newlines).
  command_len="$(set -o pipefail; printf '%s' "$normalized" | HOME=/dev/null "$JQ_BIN" -j '.command' 2>/dev/null | LC_ALL=C "$WC_BIN" -c | "$TR_BIN" -d '[:space:]')" || policy_deny extraction_failure
  case "$command_len" in ''|*[!0-9]*) policy_deny extraction_failure ;; esac

  # P1: the push word, the commit word and the § 7.2 markers. None of them (a
  # commit word counts only with commit style on): no opinion, before any
  # segmenting.
  text_words "$command_raw"
  HAS_PUSH="$WT_PUSH"
  HAS_COMMIT="$WT_COMMIT"
  COMMIT_LIVE=0
  [ "$HAS_COMMIT" -eq 0 ] || [ "$OPT_COMMIT" = off ] || COMMIT_LIVE=1
  HAS_MARKER=0
  if [ "$WT_GIT" -eq 1 ]; then
    shopt -s nocasematch
    case "$command_raw" in
      *--config-env*|*GIT_CONFIG*|*alias.*|*.push*|*.mirror*) HAS_MARKER=1 ;;
    esac
    shopt -u nocasematch
  fi
  [ "$HAS_PUSH" -eq 1 ] || [ "$COMMIT_LIVE" -eq 1 ] || [ "$HAS_MARKER" -eq 1 ] || policy_no_opinion

  # The length cap: a push word in an over-long command is presumed; nothing
  # else is judged there.
  if [ "$command_len" -gt "$MAX_COMMAND_CHARS" ]; then
    [ "$HAS_PUSH" -eq 1 ] || policy_no_opinion
    record_code push_command_too_long
    finish_verdict
  fi
  GATES_LIVE=1

  # § 7.1: a variable that selects a repository or injects configuration,
  # inherited from the session, beside a push word.
  if [ "$HAS_PUSH" -eq 1 ] && inherited_git_variable; then
    record_code inherited_git_environment
  fi

  build_segments "$command_raw"
  n=${#SEGS[@]}

  # § 7.2: GIT_ names that reach a later command beside a push, then
  # configuration injected on the command line, with or without a push word.
  if [ "$HAS_PUSH" -eq 1 ]; then
    k=0
    while [ "$k" -lt "$n" ]; do
      if segment_sets_git_environment "${SEGS[$k]}"; then record_code push_git_environment; break; fi
      k=$((k + 1))
    done
  fi
  if [ "$HAS_MARKER" -eq 1 ]; then
    k=0
    while [ "$k" -lt "$n" ]; do
      if segment_injects_config "${SEGS[$k]}"; then record_code git_config_injection; break; fi
      k=$((k + 1))
    done
  fi

  if [ "$HAS_PUSH" -eq 1 ]; then
    # P2's carriers, text-wide and then scoped to their pipeline, so a carrier
    # names itself; then P3, rule G outside plain text.
    if text_wide_carrier "$command_raw"; then
      record_code "push_beside_$CARRIER_CLASS"
    fi
    if [ "$PRESUMED_SEEN" -eq 0 ] && pipeline_carrier; then
      record_code "push_beside_$CARRIER_CLASS"
    fi
    if [ "$PRESUMED_SEEN" -eq 0 ] && [ "$HIDDEN_SEPARATOR_KEPT" -eq 1 ] && ! plain_text "$command_raw"; then
      record_code push_hidden_separator
    fi
    # P4 to P7, each segment that holds the push word, in text order. A push
    # already presumed takes the stricter option, which no segment can
    # exceed, so the segments are not read (and git is never asked).
    if [ "$PRESUMED_SEEN" -eq 0 ]; then
      k=0
      while [ "$k" -lt "$n" ]; do
        if [ "${SEG_PUSH[$k]}" -eq 1 ]; then judge_push_segment "$k"; fi
        k=$((k + 1))
      done
    fi
  fi

  # C1 on every top-level segment, whatever the push steps found.
  if [ "$COMMIT_LIVE" -eq 1 ]; then
    k=0
    while [ "$k" -lt "$n" ]; do
      if [ "${SEG_COMMIT[$k]}" -eq 1 ]; then judge_commit_segment "${SEGS[$k]}"; fi
      k=$((k + 1))
    done
  fi
  finish_verdict
}

GUARD_RUNNER_PROGRAM='
my $seconds = shift @ARGV;
my $fork_retry = qr/^(.*: )?fork: retry: /;
pipe(my $out_r, my $out_w) or exit 125;
pipe(my $err_r, my $err_w) or exit 125;
my $pid = fork();
defined $pid or exit 125;
if (!$pid) {
  setpgrp(0, 0);
  open(STDOUT, ">&", $out_w) or exit 127;
  open(STDERR, ">&", $err_w) or exit 127;
  exec { $ARGV[0] } @ARGV;
  exit 127;
}
setpgrp($pid, $pid);
close $out_w;
close $err_w;
my ($out, $err_lines, $err_other, $err_buf, $timed_out) = ("", 0, 0, "", 0);
my %open = (fileno($out_r) => $out_r, fileno($err_r) => $err_r);
my $finished = eval {
  local $SIG{ALRM} = sub { die "guard-runner-timeout\n" };
  alarm $seconds;
  while (%open) {
    my $want = "";
    vec($want, $_, 1) = 1 for keys %open;
    next if select(my $ready = $want, undef, undef, undef) < 0;
    for my $fd (keys %open) {
      next unless vec($ready, $fd, 1);
      my $chunk;
      my $got = sysread($open{$fd}, $chunk, 65536);
      if (!$got) { delete $open{$fd}; next; }
      if ($fd == fileno($out_r)) { $out .= $chunk; next; }
      $err_buf .= $chunk;
      while ((my $nl = index($err_buf, "\n")) >= 0) {
        my $line = substr($err_buf, 0, $nl, "");
        substr($err_buf, 0, 1, "");
        $err_lines++;
        $err_other++ unless $line =~ $fork_retry;
      }
    }
  }
  waitpid($pid, 0);
  alarm 0;
  1;
};
if (!$finished) {
  kill 9, -$pid;
  waitpid($pid, 0);
  exit 126 unless $@ eq "guard-runner-timeout\n";
  $timed_out = 1;
}
my $status = ($? & 127) ? 128 + ($? & 127) : $? >> 8;
if (length $err_buf) {
  $err_lines++;
  $err_other++ unless $err_buf =~ $fork_retry;
}
my $nul = ($out =~ tr/\0//) ? 1 : 0;
$out = "" if $nul;
$out .= "\n" if length($out) && substr($out, -1) ne "\n";
print $out;
print "__GUARD_RUNNER__ status=$status timed_out=$timed_out stderr_lines=$err_lines stderr_other=$err_other nul=$nul\n";
exit 0;
'


# Reads one option argument as the adapter received it: empty, or exactly the
# unsubstituted text ${user_config.KEY} of its own key, is the option's
# default; ask, deny and off are themselves; anything else is invalid.
OPTION_VALUE=""
adapter_option_value() { # RAW KEY DEFAULT
  case "$1" in
    '') OPTION_VALUE="$3" ;;
    ask|deny|off) OPTION_VALUE="$1" ;;
    *)
      if [ "$1" = "\${user_config.$2}" ]; then OPTION_VALUE="$3"; else OPTION_VALUE=invalid; fi
      ;;
  esac
}

# Every denial names its reason code. A fault is still a denial (fail
# closed), but it is named as a fault, never as a rule:
#   engine_timeout            the runner's framing line says timed_out=1
#   engine_failed             the runner broke or the engine exited nonzero
#   engine_output_invalid     the engine's stdout is not exactly one valid
#                             verdict (deny, ask or no_opinion), or holds a NUL
#   engine_stderr_on_silence  the engine said no_opinion and wrote stderr
#   dependency_missing_*      jq, the runner (perl) or the engine file
#   adapter_input_invalid     the hook input is not one valid Bash event
#   invalid_configuration     the adapter's own arguments are invalid
# An ask or a deny stands whatever the engine wrote to stderr, since the
# engine's own audit record is stderr. The runner reports stderr as counts
# only; no stderr text reaches the verdict or the audit.
run_adapter() {
  local normalized response body framing parsed decision reason status timed_out stderr_lines nul
  local main_value force_value commit_value
  local runner_rc=0
  local framing_re='^__GUARD_RUNNER__ status=([0-9]+) timed_out=([01]) stderr_lines=([0-9]+) stderr_other=([0-9]+) nul=([01])$'
  [ "$ARG_ERROR" -eq 0 ] || emit_claude_deny invalid_configuration
  # The adapter tier: jq from the trusted roots, the runner and the engine
  # file. Builtins only, so this works when the process can no longer fork.
  check_tools "${ADAPTER_TOOLS[@]}" || emit_claude_deny_dependency
  [ -x "$TIMEOUT_RUNNER" ] && [ ! -d "$TIMEOUT_RUNNER" ] || emit_claude_deny dependency_missing_perl
  [ -f "$POLICY_PATH" ] || emit_claude_deny dependency_missing_engine
  normalized="$(HOME=/dev/null "$JQ_BIN" -cers '
    select(
      length == 1 and
      (.[0] | type == "object" and
        .hook_event_name == "PreToolUse" and .tool_name == "Bash" and
        (.tool_input | type == "object") and
        (.tool_input.command | type == "string" and length > 0 and (test("[\u0000-\u0008\u000b-\u001f\u007f]") | not)) and
        (.cwd | type == "string" and length > 0 and startswith("/") and (test("[\u0000-\u0008\u000b-\u001f\u007f]") | not)))
    ) | .[0] | {cwd:.cwd,command:.tool_input.command}
  ' 2>/dev/null)" || emit_claude_deny adapter_input_invalid
  [ -n "$normalized" ] || emit_claude_deny adapter_input_invalid
  GUARD_LOG_INPUT="$normalized"
  adapter_option_value "$OPT_MAIN" push_to_main ask
  main_value="$OPTION_VALUE"
  adapter_option_value "$OPT_FORCE" force_push ask
  force_value="$OPTION_VALUE"
  adapter_option_value "$OPT_COMMIT" commit_style off
  commit_value="$OPTION_VALUE"
  # Only the runner's stdout is captured. The engine's stderr never reaches
  # it, and the runner's own stderr is discarded.
  response="$(printf '%s\n' "$normalized" | "$TIMEOUT_RUNNER" -e "$GUARD_RUNNER_PROGRAM" "$TIMEOUT_SECONDS" /bin/bash --noprofile --norc -p "$POLICY_PATH" --policy --push-to-main "$main_value" --force-push "$force_value" --commit-style "$commit_value" 2>/dev/null)" || runner_rc=$?
  [ "$runner_rc" -eq 0 ] || emit_claude_deny engine_failed
  # The framing line is the runner's last line; everything before it is the
  # engine's stdout.
  framing="${response##*$'\n'}"
  if [ "$framing" = "$response" ]; then body=""; else body="${response%$'\n'*}"; fi
  [[ "$framing" =~ $framing_re ]] || emit_claude_deny engine_failed
  status="${BASH_REMATCH[1]}"
  timed_out="${BASH_REMATCH[2]}"
  stderr_lines="${BASH_REMATCH[3]}"
  nul="${BASH_REMATCH[5]}"
  [ "$timed_out" = 0 ] || emit_claude_deny engine_timeout
  [ "$status" = 0 ] || emit_claude_deny engine_failed
  [ "$nul" = 0 ] || emit_claude_deny engine_output_invalid
  # The engine's stdout must be exactly one line holding one verdict object.
  case "$body" in ''|*$'\n'*) emit_claude_deny engine_output_invalid ;; esac
  # Exactly three decisions are read: deny, ask and no_opinion. Anything else
  # is invalid output and denies.
  parsed="$(printf '%s' "$body" | HOME=/dev/null "$JQ_BIN" -ers '
    select(
      length == 1 and
      (.[0] | type == "object" and keys == ["decision","reason"] and
        (.decision == "deny" or .decision == "ask" or .decision == "no_opinion") and
        (.reason | type == "string" and test("^[a-z_]+$")))
    ) | .[0] | [.decision,.reason] | @tsv
  ' 2>/dev/null)" || emit_claude_deny engine_output_invalid
  [ -n "$parsed" ] || emit_claude_deny engine_output_invalid
  decision="${parsed%%$'\t'*}"
  reason="${parsed#*$'\t'}"
  case "$decision" in
    no_opinion)
      [ "$stderr_lines" = 0 ] || emit_claude_deny engine_stderr_on_silence
      VERDICT_STARTED=1
      VERDICT_DONE=1
      exit 0
      ;;
    ask) emit_claude_verdict ask "$reason" ;;
    deny) emit_claude_verdict deny "$reason" ;;
  esac
  emit_claude_deny engine_output_invalid
}

if [ "$MODE" = "policy" ]; then
  # The engine has no crash trap: a death or a fault here reaches the adapter
  # as a non-zero exit or a broken frame and denies there as engine_failed.
  trap - EXIT
  for GUARD_SIGNAL in HUP INT QUIT ILL TRAP ABRT BUS FPE USR1 SEGV USR2 PIPE ALRM TERM XCPU XFSZ VTALRM PROF SYS; do
    trap - "$GUARD_SIGNAL" 2>/dev/null || :
  done
  run_policy
else
  run_adapter
fi
