#!/bin/bash
# The rails plugin's destructive-command guard: a fail-closed destructive-command
# policy and its Claude Code PreToolUse adapter.
#
# Deny only. This guard never allows and never asks: every verdict is a deny
# with a named reason code, or silence (exit 0 with nothing on stdout), which
# leaves the command to Claude Code's own permission flow. Every path that
# cannot finish its judgment (a missing tool, an engine crash, malformed engine
# output, engine stderr, an adapter fault or a signal) denies by name.
#
# Default mode is the Claude adapter: strict JSON on stdin is normalized and
# passed to this same file's --policy mode.  --policy is the adapter-neutral
# seam for future consumers.  Command text is always data: it is never run,
# evaluated, or sourced.
#
# Every external program run by name (jq, wc, tr, git, grep) is resolved only
# from the fixed trusted roots of TRUSTED_ROOTS, never through PATH; every
# other program is run by its absolute path. A missing program denies with a
# code that names it (dependency_missing_<tool>), checked in five tiers so a
# command only needs the programs its own path runs.
#
# Accepted Bash language: simple-command segments made from literal words and
# conventional single/double quoting. Newlines and ordinary shell separators
# delimit segments; heredoc bodies are data. The segmenter reads # as text
# (split_command_segments), so it reads more of a command than bash runs,
# never less. Rule G, the invariant: a command that holds a removal word is
# judged only when every ;, &, | and newline in it ended a segment and no
# text was dropped. A ; & | or newline consumed inside quotes, after a
# backslash or as a line continuation denies as hidden_separator
# (run_policy), so a wrong quote state can only split a command too much,
# never too little. The redirection operators >&, &>, >|, <& and <| are not
# separators. A separator inside a $( ) span or a heredoc body is not
# flagged; that is safe only because $(, backticks, <( or >( and << are
# carriers R2 finds in the raw text before any segment is judged. A finite
# known-wrapper grammar is normalized before matching. This is detection,
# not prevention: an unlisted exec wrapper (for example, a user-installed
# chronic) can make rm invisible.
# A removal word (rm, or git worktree remove) anywhere in the command text,
# heredoc bodies included, beside any code-carrying construct denies without
# the guard reading inside that construct (removal_word_in_text and
# find_carrier); a command with no removal word gets no opinion.
# rm accepts only r/R/f flags and literal targets; compounds that can
# re-point path resolution fail closed.
# From a working folder in no git repository (a workspace folder), only
# literal absolute rm targets strictly below the session scratchpad are
# accepted; see validate_target_outside_worktree.
# git worktree remove accepts an explicitly authorized tree, a lane-registry
# tree with its identity grant, the exact six-word non-force `git -C REPO
# worktree remove TARGET` form for a clean linked tree registered to this seat
# whose only ignored files are Python caches, or a linked tree strictly below a
# temp root that no registry row names. Every other removal is denied, and the
# person runs it by hand outside Claude Code. The lane and cleanup registries
# are dormant unless their environment variables are set; an armed registry
# that is absent, untrusted or malformed denies by name.
#
# Cleanup admission covers command shape, registry ownership and seat binding,
# linked-worktree identity, lock/cleanliness/cache checks, and surviving refs.
# Accepted residuals are cleanup ownership keyed by path, commits reachable only
# from the target worktree's own HEAD reflog, worktree-local sequencer state, a
# different tree created later at that path, and filesystem changes between the
# last check and `git worktree remove`. They stay accepted only while the private
# registry has one seat writer, every invocation rebinds the live target to its
# repository, and removal is immediate and non-force. Shared or mutable
# registries, broader command shapes, or deferred removal reopen them.
# The next line is a marker the launcher reads to learn this guard checks
# worktree identity before a lane-registry-backed removal; keep it verbatim.
# lane-roots-identity: v1
set -u
LC_ALL=C

# Interpreter startup state the person's environment could carry into the
# programs this guard starts. Every bash the guard starts runs with -p, which
# already skips BASH_ENV, ENV and exported functions; these unsets also reach
# every perl (the runner, cleanup_git, the log writer) and any later bash.
unset BASH_ENV ENV PERL5OPT PERL5LIB PERLLIB PERL5DB PERL5DB_THREADED PERLIO \
  PERLIO_DEBUG PERL_UNICODE PERL_ENCODING PERL_SIGNALS PERL_DL_NONLAZY PERL_HASH_SEED

# The crash trap. Every verdict sets VERDICT_STARTED, prints its one line with
# a builtin printf, then sets VERDICT_DONE before any audit work that forks.
# adapter_fail_closed is the body of the EXIT trap and of every signal trap:
# after a verdict it changes nothing; between the two flags it exits 3, a hook
# failure that onFailure "block" turns into a block; before any verdict it
# prints one fixed deny line. Its first statements are builtins only, so it
# still works when the process can no longer fork.
VERDICT_STARTED=0
VERDICT_DONE=0
adapter_fail_closed() { # CODE: adapter_failed | adapter_interrupted
  [ "$VERDICT_DONE" -eq 0 ] || exit 0
  [ "$VERDICT_STARTED" -eq 0 ] || exit 3
  VERDICT_STARTED=1
  case "$1" in
    adapter_interrupted)
      printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Destructive command guard denied the command (reason=adapter_interrupted): the guard'"'"'s adapter was stopped by a signal; retry once."}}' || exit 3
      ;;
    *)
      printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Destructive command guard denied the command (reason=adapter_failed): the guard'"'"'s adapter failed; retry once and report the code if it repeats."}}' || exit 3
      ;;
  esac
  VERDICT_DONE=1
  exit 0
}
# Every signal that terminates bash by default and can be caught, by name;
# each name is set on its own, so a name the platform lacks is skipped
# without a fork.
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

# The macOS account root, refused by spelling wherever a target names it. One
# unconditional assignment: the environment can never move it.
readonly USERS_ROOT=/Users

# The trusted roots, in order. jq, wc, tr, git and grep are taken from the
# first root that holds an executable, non-directory file of that name; PATH
# is never read for them. The guard reads no variable, option or file for this
# list. The Linuxbrew root is spelled in two quoted parts on purpose.
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

# The absolute path of each tool run by name, set by require_tools.
JQ_BIN=""
WC_BIN=""
TR_BIN=""
GIT_BIN=""
GREP_BIN=""

# git and grep run through these two functions, so every bare `git` or `grep`
# word in this file (the identity lines the launcher pins by digest among
# them) runs the trusted-root program. A program its tier did not resolve
# fails like a missing command.
git() {
  [ -n "$GIT_BIN" ] || return 127
  "$GIT_BIN" "$@"
}
grep() {
  [ -n "$GREP_BIN" ] || return 127
  "$GREP_BIN" "$@"
}

# The required programs of each tier, in the order they are checked. A bare
# name is resolved from TRUSTED_ROOTS; CODE:PATH is an absolute program that
# must be executable and not a directory, denied as dependency_missing_CODE.
# The adapter tier also checks the runner (/usr/bin/perl, or --timeout-runner)
# and the engine file itself; the armed tier also needs one usable
# STAT_CANDIDATES entry (dependency_missing_stat).
ADAPTER_TOOLS=(jq)
ENGINE_TOOLS=(jq wc tr)
CONTEXT_TOOLS=(git env:/usr/bin/env)
WORKTREE_TOOLS=(system_git:/usr/bin/git perl:/usr/bin/perl find:/usr/bin/find readlink:/usr/bin/readlink cat:/bin/cat)
ARMED_TOOLS=(grep id:/usr/bin/id date:/bin/date)

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
          grep) GREP_BIN="$RESOLVED_TOOL" ;;
          *) MISSING_TOOL_CODE="$entry"; return 1 ;;
        esac
        ;;
    esac
  done
  return 0
}

# Sets DEPENDENCY_CODE to dependency_missing_<code> for MISSING_TOOL_CODE.
# Only these literals are ever assigned, so the reason a dependency denial
# names is always one of them.
DEPENDENCY_CODE=""
dependency_code() {
  case "$MISSING_TOOL_CODE" in
    jq) DEPENDENCY_CODE=dependency_missing_jq ;;
    wc) DEPENDENCY_CODE=dependency_missing_wc ;;
    tr) DEPENDENCY_CODE=dependency_missing_tr ;;
    git) DEPENDENCY_CODE=dependency_missing_git ;;
    grep) DEPENDENCY_CODE=dependency_missing_grep ;;
    env) DEPENDENCY_CODE=dependency_missing_env ;;
    system_git) DEPENDENCY_CODE=dependency_missing_system_git ;;
    perl) DEPENDENCY_CODE=dependency_missing_perl ;;
    find) DEPENDENCY_CODE=dependency_missing_find ;;
    readlink) DEPENDENCY_CODE=dependency_missing_readlink ;;
    cat) DEPENDENCY_CODE=dependency_missing_cat ;;
    id) DEPENDENCY_CODE=dependency_missing_id ;;
    date) DEPENDENCY_CODE=dependency_missing_date ;;
    stat) DEPENDENCY_CODE=dependency_missing_stat ;;
    *) DEPENDENCY_CODE=dependency_missing_engine ;;
  esac
}

SELF="${BASH_SOURCE[0]}"
MODE="adapter"
POLICY_PATH="$SELF"
SCRATCHPAD=""
AUTHORIZED_WORKTREE=""
TIMEOUT_RUNNER="/usr/bin/perl"
# The engine's wall-time budget. Parse time grows faster than linearly with
# command length (about 3.75 s at the 6000-byte cap on an idle machine), so the
# budget keeps twice that margin and stays at least 2 s under the hook's own
# timeout in hooks.json: an expiry is always this adapter's named denial,
# never the harness's generic hook timeout.
TIMEOUT_SECONDS=8
ARG_ERROR=0
# The adapter's normalized input, kept only so a denial can name its cwd in
# the persistent guard log; empty until the input is normalized.
GUARD_LOG_INPUT=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --policy)
      [ "$MODE" = "adapter" ] || ARG_ERROR=1
      MODE="policy"
      shift
      ;;
    --scratchpad|--authorized-worktree|--adapter-policy|--timeout-runner)
      [ "$#" -ge 2 ] || { ARG_ERROR=1; break; }
      case "$1" in
        --scratchpad) [ -z "$SCRATCHPAD" ] || ARG_ERROR=1; SCRATCHPAD="$2" ;;
        --authorized-worktree) [ -z "$AUTHORIZED_WORKTREE" ] || ARG_ERROR=1; AUTHORIZED_WORKTREE="$2" ;;
        --adapter-policy) POLICY_PATH="$2" ;;
        --timeout-runner) TIMEOUT_RUNNER="$2" ;;
      esac
      shift 2
      ;;
    *) ARG_ERROR=1; break ;;
  esac
done

emit_policy() {
  printf '{"decision":"%s","reason":"%s"}\n' "$1" "$2"
}

policy_deny() {
  redacted_log "$1"
  emit_policy deny "$1"
  exit 0
}

policy_no_opinion() {
  emit_policy no_opinion not_explicit_operation
  exit 0
}

# The engine's verdict for a command it judged and accepted. It is silent at
# the adapter, exactly like no opinion; the reason keeps the two apart for
# the engine's own tests.
policy_accepted() {
  emit_policy no_opinion accepted
  exit 0
}

# Denies with the dependency code for the first missing program of a tier.
policy_deny_dependency() {
  dependency_code
  policy_deny "$DEPENDENCY_CODE"
}

# Audit records: EVENT and CODE. A record deliberately contains no command,
# path, cwd, or input data, only the time, the event and a reason code; every
# code is a literal of this file or one of the adapter's own. A shell
# redirection cannot safely prove that every path component remains
# non-symlinked between inspection and open, so this record never opens a
# configured path. Stderr is the fixed audit sink; stdout remains the JSON
# protocol. The persistent guard log below is separate: it opens its file only
# through the no-follow writer and adds the cwd.
audit_record() {
  local code="$2"
  case "$code" in ''|*[!a-z_]*) code=engine_output_invalid ;; esac
  printf '%s event=%s reason=%s\n' "$(/bin/date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || printf 'time-unavailable')" "$1" "$code" >&2
}

# One record per denial, naming its reason code.
redacted_log() {
  audit_record destructive-command-deny "${1:-}"
}


# The persistent guard log, armed only. In an attended session
# (CLAUDE_SEAT_UNATTENDED is not 1) whose SEAT_SCRATCH is an existing absolute
# directory, every denial this adapter emits also appends one line to
# $SEAT_SCRATCH/guard.log. Unarmed, nothing is written anywhere. Like the
# stderr record above, the line never holds the command or any argument text:
# only the time, the event, the reason code, the command class and the hook
# input's cwd. The class is a pure function of the reason code
# (guard_log_class), never a re-parse of the command. The log fails open: a
# failed or slow append is ignored and never changes the decision, the output
# or the exit code. The --policy engine never writes this log; the adapter
# does, once per denial, after the deny line is printed.
# The persistent guard log writer (core Perl only). Arguments: BASE NAME EVENT
# CODE CLASS CWD. BASE must be an existing absolute directory. The file is
# opened for append with O_NOFOLLOW and O_NONBLOCK, created 0600, and written
# only when it is a regular file, so a symlink, FIFO or directory planted at
# the log path is never followed, waited on or written. One JSON object per
# line with exactly the keys timestamp, event, code, class, cwd; strings are
# escaped the way JSON.stringify escapes them. The whole append is bounded by
# a 0.5 s alarm. Exit 0 means the line was written; any failure exits 1, and
# the caller ignores the status.
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
exit 1 unless $event eq "deny" && $code =~ /\A[a-z_]+\z/;
exit 1 unless $class =~ /\A(?:git-worktree-remove|rm|redirect|other)\z/;
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

# Sets GUARD_LOG_CLASS for a reason code. Codes that more than one branch
# emits (unsupported_syntax, cwd_outside_worktree) and adapter or
# configuration faults are other.
guard_log_class() {
  case "$1" in
    unsafe_rm|unsupported_rm_prefix|non_literal_rm_targets|path_repointing_*) GUARD_LOG_CLASS=rm ;;
    unsafe_worktree_remove|lane_registry_*|lane_ids_*|cleanup_*) GUARD_LOG_CLASS=git-worktree-remove ;;
    unsafe_redirect) GUARD_LOG_CLASS=redirect ;;
    *) GUARD_LOG_CLASS=other ;;
  esac
}

guard_log_append() { # EVENT CODE
  local cwd=""
  [ "${CLAUDE_SEAT_UNATTENDED:-}" = "1" ] && return 0
  [ -x /usr/bin/perl ] || return 0
  # Armed only: an existing absolute SEAT_SCRATCH directory, read live at
  # every append. There is no default location.
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

# The trusted roots as a hint names them. The Linuxbrew root is spelled in two
# quoted parts on purpose; the printed text is the plain path.
ROOTS_HINT="/usr/bin, /bin, /usr/local/bin, /opt/homebrew/bin or /home""/linuxbrew/.linuxbrew/bin"

# Sets DENY_HINT to the one fixed sentence for a reason code. A hint never
# carries input or a path from input; a path in a hint is a literal of this
# file.
deny_hint() {
  case "$1" in
    unsafe_rm) DENY_HINT='an rm target is not a literal path strictly below the current worktree or the named session scratchpad; spell a worktree target relative to the current folder, because an absolute home path is refused by spelling' ;;
    unsafe_worktree_remove) DENY_HINT='git worktree remove must name exactly one registered or authorized worktree and never --force; otherwise run the removal yourself, outside Claude Code' ;;
    unsupported_syntax) DENY_HINT='the command is over 6000 bytes or has an unbalanced substitution or quote; split or shorten it' ;;
    hidden_separator) DENY_HINT='this command mentions a removal and holds a quoted or escaped separator or a line continuation; run the removal as its own one-line command, and put any text that mentions it in a file' ;;
    unsupported_rm_prefix) DENY_HINT='rm reached through a wrapper or xargs; write the rm bare with literal targets' ;;
    path_repointing_subshell|path_repointing_assignment|path_repointing_ln|path_repointing_mv|path_repointing_cd|path_repointing_pushd|path_repointing_popd|path_repointing_mkdir|path_repointing_mount|path_repointing_cp_symlink) DENY_HINT='a cd, mv, ln, mkdir, eval, subshell or substitution shares the line with a recursive rm; run the rm alone' ;;
    removal_beside_substitution) DENY_HINT='an rm or git worktree remove shares the command with a $(...) substitution, which the guard does not read inside; run the removal as its own plain command; for a commit or PR message that mentions it, write the message to a file and use git commit -F or gh --body-file' ;;
    removal_beside_backtick) DENY_HINT='an rm or git worktree remove shares the command with a backtick substitution, which the guard does not read inside; run the removal as its own plain command' ;;
    removal_beside_process_substitution) DENY_HINT='an rm or git worktree remove shares the command with a <(...) or >(...) process substitution, which the guard does not read inside; run the removal as its own plain command' ;;
    removal_beside_heredoc) DENY_HINT='an rm or git worktree remove shares the command with a heredoc or here-string, which the guard does not read inside; run the removal as its own plain command, and write files that mention rm with the Write tool; for a commit or PR message that mentions it, write the message to a file and use git commit -F or gh --body-file' ;;
    removal_beside_eval) DENY_HINT='an rm or git worktree remove shares the command with eval, which the guard does not read inside; run the removal as its own plain command' ;;
    removal_beside_source) DENY_HINT='an rm or git worktree remove shares the command with source or ., which the guard does not read inside; run the removal as its own plain command' ;;
    removal_beside_trap) DENY_HINT='an rm or git worktree remove shares the command with trap, which the guard does not read inside; run the removal as its own plain command' ;;
    removal_beside_alias) DENY_HINT='an rm or git worktree remove shares the command with alias, which the guard does not read inside; run the removal as its own plain command' ;;
    removal_beside_xargs) DENY_HINT='an rm or git worktree remove shares the command with xargs, which the guard does not read inside; run the removal as its own plain command' ;;
    removal_beside_find_exec) DENY_HINT='an rm or git worktree remove shares the command with find -exec, -execdir or -ok, which the guard does not read inside; run the removal as its own plain command' ;;
    removal_beside_env_split) DENY_HINT='an rm or git worktree remove shares the command with env -S, which the guard does not read inside; run the removal as its own plain command' ;;
    removal_beside_shell) DENY_HINT='an rm or git worktree remove shares the command with a shell or shell runner, which the guard does not read inside; run the removal as its own plain command' ;;
    removal_beside_variable_command) DENY_HINT='an rm or git worktree remove shares the command with a command word built from a variable or expansion, which the guard does not read inside; run the removal as its own plain command' ;;
    removal_beside_git_foreach) DENY_HINT='an rm or git worktree remove shares the command with git submodule foreach, which the guard does not read inside; run the removal as its own plain command' ;;
    removal_beside_git_rebase_exec) DENY_HINT='an rm or git worktree remove shares the command with git rebase --exec, which the guard does not read inside; run the removal as its own plain command' ;;
    removal_beside_git_bisect_run) DENY_HINT='an rm or git worktree remove shares the command with git bisect run, which the guard does not read inside; run the removal as its own plain command' ;;
    removal_beside_flock) DENY_HINT='an rm or git worktree remove shares the command with flock, which the guard does not read inside; run the removal as its own plain command' ;;
    unsafe_redirect) DENY_HINT='an output redirection targets an existing path reached through a symlink' ;;
    invalid_cwd|invalid_scratchpad|invalid_authorized_worktree|invalid_configuration) DENY_HINT="the guard's working folder or options are invalid; this is a configuration fault, not the command" ;;
    cwd_outside_worktree) DENY_HINT='the working folder is not inside a git worktree, so only absolute targets strictly below the session scratchpad are accepted; run the command from a worktree' ;;
    inherited_git_environment) DENY_HINT='a GIT_ variable that selects a repository is set in the session; unset it and retry' ;;
    dependency_missing_jq) DENY_HINT="jq was not found in $ROOTS_HINT; install jq in one of them" ;;
    dependency_missing_wc) DENY_HINT="wc was not found in $ROOTS_HINT; install wc in one of them" ;;
    dependency_missing_tr) DENY_HINT="tr was not found in $ROOTS_HINT; install tr in one of them" ;;
    dependency_missing_git) DENY_HINT="git was not found in $ROOTS_HINT; install git in one of them" ;;
    dependency_missing_grep) DENY_HINT="grep was not found in $ROOTS_HINT; install grep in one of them" ;;
    dependency_missing_perl) DENY_HINT='/usr/bin/perl is missing; the guard runs its engine under it' ;;
    dependency_missing_engine) DENY_HINT="the guard's own policy file is missing; reinstall the rails plugin" ;;
    dependency_missing_system_git) DENY_HINT='/usr/bin/git is missing' ;;
    dependency_missing_env) DENY_HINT='/usr/bin/env is missing' ;;
    dependency_missing_find) DENY_HINT='/usr/bin/find is missing' ;;
    dependency_missing_readlink) DENY_HINT='/usr/bin/readlink is missing' ;;
    dependency_missing_cat) DENY_HINT='/bin/cat is missing' ;;
    dependency_missing_stat) DENY_HINT='neither /usr/bin/stat nor /bin/stat is usable' ;;
    dependency_missing_id) DENY_HINT='/usr/bin/id is missing' ;;
    dependency_missing_date) DENY_HINT='/bin/date is missing' ;;
    lane_registry_absent_armed) DENY_HINT='LANE_ROOTS_REGISTRY is set but names no file; create the registry before arming it, or unset the variable' ;;
    cleanup_registry_absent_armed) DENY_HINT='CLEANUP_ITEMS_REGISTRY is set but names no file; create the registry before arming it, or unset the variable' ;;
    cleanup_registry_untrusted) DENY_HINT='the file CLEANUP_ITEMS_REGISTRY names is not a private registry of this user' ;;
    cleanup_registry_malformed) DENY_HINT='the file CLEANUP_ITEMS_REGISTRY names does not have the registry header and six-field rows' ;;
    cleanup_seat_missing) DENY_HINT='CLEANUP_ITEMS_REGISTRY is set but CLEANUP_SEAT is empty' ;;
    malformed_normalized_input|extraction_failure|adapter_input_invalid) DENY_HINT='the hook input could not be read' ;;
    engine_timeout) DENY_HINT='the guard did not finish within 8 seconds; retry the same command once and keep commands under about 4 KB' ;;
    engine_failed|engine_output_invalid|engine_stderr_on_accept) DENY_HINT="the guard's engine failed; retry once and report the code if it repeats" ;;
    adapter_failed) DENY_HINT="the guard's adapter failed; retry once and report the code if it repeats" ;;
    adapter_interrupted) DENY_HINT="the guard's adapter was stopped by a signal; retry once" ;;
    *) DENY_HINT='see the reason code' ;;
  esac
}

# Denies with a reason code: the fixed template and the code's hint, printed
# by one builtin printf between the two verdict flags, then the audit record
# and the guard log, which fork. The hints hold no characters that need JSON
# escaping.
emit_claude_deny() {
  local code="${1:-}"
  case "$code" in ''|*[!a-z_]*) code=engine_output_invalid ;; esac
  deny_hint "$code"
  VERDICT_STARTED=1
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Destructive command guard denied the command (reason=%s): %s."}}\n' "$code" "$DENY_HINT" || exit 3
  VERDICT_DONE=1
  redacted_log "$code"
  guard_log_append deny "$code"
  exit 0
}

# Denies with the dependency code for the first missing program of a tier.
emit_claude_deny_dependency() {
  dependency_code
  emit_claude_deny "$DEPENDENCY_CODE"
}

normalize_absolute() {
  local rest component result=""
  rest="${1#/}"
  while :; do
    case "$rest" in
      */*) component="${rest%%/*}"; rest="${rest#*/}" ;;
      *) component="$rest"; rest="" ;;
    esac
    case "$component" in
      ""|.) ;;
      ..) result="${result%/*}" ;;
      *) result="$result/$component" ;;
    esac
    [ -n "$rest" ] || break
  done
  [ -n "$result" ] && printf '%s\n' "$result" || printf '/\n'
}

strict_descendant() {
  [ "$1" != "$2" ] && [ "${1#"$2"/}" != "$1" ]
}

canonical_target() {
  local raw="$1" base="$2" candidate existing leaf suffix="" resolved
  case "$raw" in
    /*) candidate="$raw" ;;
    *) candidate="$base/$raw" ;;
  esac
  candidate="$(normalize_absolute "$candidate")" || return 1

  # Any existing symlink component makes the filesystem interpretation
  # uncertain. This includes a symlink used as the leaf being removed.
  existing="$candidate"
  while [ "$existing" != "/" ]; do
    [ ! -L "$existing" ] || return 2
    existing="${existing%/*}"
    [ -n "$existing" ] || existing="/"
  done

  existing="$candidate"
  while [ "$existing" != "/" ] && [ ! -e "$existing" ]; do
    leaf="${existing##*/}"
    suffix="/$leaf$suffix"
    existing="${existing%/*}"
    [ -n "$existing" ] || existing="/"
  done
  if [ -d "$existing" ]; then
    resolved="$(cd -- "$existing" 2>/dev/null && pwd -P)" || return 1
  else
    leaf="${existing##*/}"
    suffix="/$leaf$suffix"
    existing="${existing%/*}"
    [ -n "$existing" ] || existing="/"
    resolved="$(cd -- "$existing" 2>/dev/null && pwd -P)" || return 1
  fi
  normalize_absolute "$resolved$suffix"
}

WORDS=()
parse_simple_command() {
  local input="$1" length i=0 ch state="plain" word="" in_word=0 backslash
  backslash="\\"
  WORDS=()
  length=${#input}
  while [ "$i" -lt "$length" ]; do
    ch="${input:$i:1}"
    case "$state" in
      single)
        if [ "$ch" = "'" ]; then state="quoted_done"; else word="$word$ch"; fi
        ;;
      double)
        case "$ch" in
          '"') state="quoted_done" ;;
          '$'|'`'|"$backslash") return 1 ;;
          *) word="$word$ch" ;;
        esac
        ;;
      quoted_done)
        case "$ch" in
          ' '|$'\t') WORDS[${#WORDS[@]}]="$word"; word=""; in_word=0; state="plain" ;;
          *) return 1 ;;
        esac
        ;;
      plain)
        case "$ch" in
          ' '|$'\t')
            if [ "$in_word" -eq 1 ]; then WORDS[${#WORDS[@]}]="$word"; word=""; in_word=0; fi
            ;;
          "'") [ "$in_word" -eq 0 ] || return 1; state="single"; in_word=1 ;;
          '"') [ "$in_word" -eq 0 ] || return 1; state="double"; in_word=1 ;;
          '$'|'`'|"$backslash"|'~'|'*'|'?'|'['|']'|'{'|'}'|'('|')'|'<'|'>'|'|'|'&'|';'|'!'|'#'|$'\n'|$'\r') return 1 ;;
          *) word="$word$ch"; in_word=1 ;;
        esac
        ;;
    esac
    i=$((i + 1))
  done
  case "$state" in
    single|double) return 1 ;;
    quoted_done) WORDS[${#WORDS[@]}]="$word" ;;
    plain) [ "$in_word" -eq 0 ] || WORDS[${#WORDS[@]}]="$word" ;;
  esac
  [ "${#WORDS[@]}" -gt 0 ]
}

SEGMENTS=()
HEREDOC_DELIMS=()
HEREDOC_STRIP_TABS=()

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

# The top-level segmenter. A # is text here, like any other character, so the
# segmenter reads more of a command than bash runs, never less: a comment's
# words are read as command words.
# Rule G (G1): HIDDEN_SEPARATOR is set to 1 when a ;, &, | or newline is
# consumed other than as a segment delimiter: inside single, $'...' or double
# quotes, after a backslash, or as a line continuation. The redirection
# operators >&, <&, &>, >| and <| are not separators and do not set it, so
# 2>&1 does not. A ; & | or newline inside a $( ) span (skip_paren) or a
# heredoc body is not flagged: that is safe only because $(, backticks, <( or
# >( and << are carriers that R2 finds in the raw text before any segment is
# judged. run_policy reads the flag (G2).
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

# R1 and R2's comment-skipping reading (top_level_text, collect_command_words):
# a private copy of the segmenter as it read comments before rule G. An
# unquoted # that begins a word (at the start of the text, or after a blank, a
# newline or one of ; & | ( )) starts a comment that runs to the end of its
# line: its text opens no quote, no line continuation, no heredoc and no
# substitution, and the newline after it still ends the segment. The comment
# stays in its segment as a lone #. A # inside a word (a#b, ${#x}, $#,
# @(a)#x), after an escape or inside quotes is text, and so is a # after a
# blank inside an arithmetic (( )) or $[ ], a ${ } expansion, a [[ ]] test, an
# extglob or array group glued to a word (@( ), x=( ), a[ ]) or a glob
# bracket. This reading never decides alone: top_level_text and
# collect_command_words read the segmenter's segments beside it, so it can
# add command words and segments but never remove them; the segmenter that
# judges commands reads # as text. Like split_command_segments it writes
# SEGMENTS and SEGMENT_DELIMITERS: run_policy splits again after R1 and R2
# run, and copies the segments before judging them.
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

# The command as R1 and R2 read it at the top level, in two readings: the
# segments with their delimiters, line continuations joined and heredoc
# bodies left out, as the segmenter reads them (TOP_LEVEL_TEXT_G,
# split_command_segments, where # is text) and as the comment-skipping copy
# reads them (TOP_LEVEL_TEXT, split_segments_skipping_comments, comments
# reduced to #). R1 and R2 read both beside the raw text, so the copy can add
# a reading but never take one away. When TEXT holds no #, the two
# segmenters give the same segments, so the segmenter is not run for it. A
# newline delimiter is written after a blank, so R1's line join can never
# read a segment that ends in an escaped backslash, and the newline after
# it, as a continuation: these readings do not depend on that join being
# right.
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

# Redirections are not words of the command they decorate. Bash performs a
# redirection wherever it stands in a simple command, before, between or
# after the words, so `2>/dev/null rm -rf /`, `FOO=1 2>/dev/null rm -rf /`
# and `sudo 2>/dev/null rm -rf /` all run rm. Every check that finds a
# command word (the rm scan, the worktree-removal patterns, the repointing
# classes and the delegated-text walk) reads this copy of the segment: each
# unquoted redirection operator (an optional descriptor number or {name},
# then <, >, >>, >|, <>, <<, <<-, <<<, <&, >&, &> or &>>) is removed with its
# target word. A process substitution, <(...) or >(...), is a word and stays.
# The copy only finds the command: the rm and worktree validators still parse
# the untouched segment, where a redirection is outside their grammar, so a
# judged command that carries one denies. A redirection whose target cannot
# be read (none follows, or its quote or substitution never closes) is
# outside the grammar and denies unsupported_syntax. Text with a quote that
# never closes before any redirection is copied unchanged from there on, as
# the scanners after this one read it today. Sets REDIR_STRIPPED (trimmed)
# and REDIR_FOUND (1 when an operator was removed).
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

# Control words and grouping openers are not command words: the command after
# them still runs. Sets CONTROL_STRIPPED to the trimmed segment with every
# leading opener removed: then, do, else, if, elif, while, until, coproc
# (with the name of a named coprocess), time (with its -p), ! and { (each
# followed by a blank), (, a case statement's head up to its first pattern, a
# case arm's pattern (a first word that ends in an unquoted ")" and opens no
# parenthesis, with a command after it), and a function definition's head
# (NAME(), NAME (), function NAME).
CONTROL_STRIPPED=""
FUNCTION_HEAD_RE='^function[[:blank:]]+[^[:blank:](){}]+([[:blank:]]*\(\))?'
NAME_PARENS_RE='^[A-Za-z_][A-Za-z0-9_.:-]*[[:blank:]]*\(\)'
COPROC_NAME_RE='^[A-Za-z_][A-Za-z0-9_]*[[:blank:]]+[({]'
strip_control_openers() {
  local segment="$1" previous_segment="" keyword
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
        case\ *' in '*') '*) segment="${segment#*) }" ;;
        *)
          if [[ "$segment" =~ $FUNCTION_HEAD_RE ]] || [[ "$segment" =~ $NAME_PARENS_RE ]]; then
            segment="${segment:${#BASH_REMATCH[0]}}"
          elif split_shell_words "$segment" && [ "${#SPLIT_RAW[@]}" -gt 1 ] \
            && [[ "${SPLIT_RAW[0]}" == *')' ]] && [[ "${SPLIT_RAW[0]}" != *'('* ]]; then
            segment="${segment#"${SPLIT_RAW[0]}"}"
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
# Sets PREFIX_NAME to the canonical name of a command prefix the guard skips,
# matched in any letter case on the word's last path component: on a
# case-insensitive filesystem ENV and TIME run /usr/bin/env and /usr/bin/time,
# and /usr/bin/env is env. Sets "" and returns 1 for any other word. xargs is
# not a prefix: it is a carrier (find_carrier).
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

# rm in any letter case, with or without a directory part: on a
# case-insensitive filesystem RM runs /bin/rm.
is_rm_word() {
  local rc=1
  shopt -s nocasematch
  case "$1" in rm|*/rm) rc=0 ;; esac
  shopt -u nocasematch
  return "$rc"
}

rm_is_recursive_after() {
  local index=$((1 + $1)) token chars end_options=0
  while [ "$index" -lt "${#SCAN_WORDS[@]}" ]; do
    token="${SCAN_WORDS[$index]}"
    if [ "$end_options" -eq 0 ] && [ "$token" = "--" ]; then
      end_options=1
    elif [ "$end_options" -eq 0 ]; then
      case "$token" in
        --recursive) return 0 ;;
        -?*) chars="${token#-}"; case "$chars" in *r*|*R*) return 0 ;; esac ;;
      esac
    fi
    index=$((index + 1))
  done
  return 1
}

KNOWN_RM=0
HAS_RECURSIVE_RM=0
UNSUPPORTED_RM_PREFIX=0
# The words are read with the segment's redirections removed
# (strip_command_redirections), so a redirection before, between or after the
# prefixes never hides the rm. Any rm after a prefix that cannot be skipped
# sets UNSUPPORTED_RM_PREFIX, recursive or not. An rm that xargs runs never
# reaches this scan: xargs is a carrier.
analyze_rm_segment() {
  local index rm_index=-1 recursive_pattern
  KNOWN_RM=0
  HAS_RECURSIVE_RM=0
  UNSUPPORTED_RM_PREFIX=0
  strip_command_redirections "$1"
  scan_shell_words "$REDIR_STRIPPED" || :
  index=0
  while [ "$index" -lt "${#SCAN_WORDS[@]}" ]; do
    if is_rm_word "${SCAN_WORDS[$index]}" && rm_is_recursive_after "$index"; then
      HAS_RECURSIVE_RM=1
      rm_index="$index"
      break
    fi
    index=$((index + 1))
  done
  if ! skip_command_prefixes; then
    if [ "$rm_index" -ge 0 ]; then
      UNSUPPORTED_RM_PREFIX=1
    else
      recursive_pattern='(^|[[:space:]])([^[:space:]]*/)?[Rr][Mm][[:space:]]+(-[^[:space:]]*[rR][^[:space:]]*|--recursive)([[:space:]]|$)'
      index="$PREFIX_INDEX"
      while [ "$index" -lt "${#SCAN_WORDS[@]}" ]; do
        if is_rm_word "${SCAN_WORDS[$index]}" || [[ "${SCAN_WORDS[$index]}" =~ $recursive_pattern ]]; then
          UNSUPPORTED_RM_PREFIX=1
          break
        fi
        index=$((index + 1))
      done
    fi
    return 0
  fi
  if is_rm_word "${SCAN_WORDS[$PREFIX_INDEX]:-}"; then
    KNOWN_RM=1
    if rm_is_recursive_after "$PREFIX_INDEX"; then
      HAS_RECURSIVE_RM=1
    fi
  fi
  return 0
}

strip_leading_negation() {
  RM_PARSE_INPUT="$1"
  trim_horizontal "$RM_PARSE_INPUT"
  RM_PARSE_INPUT="$TRIMMED"
  while [ "${RM_PARSE_INPUT#'!'}" != "$RM_PARSE_INPUT" ]; do
    case "$RM_PARSE_INPUT" in
      '! '*) RM_PARSE_INPUT="${RM_PARSE_INPUT#'!'}" ;;
      '!'$'\t'*) RM_PARSE_INPUT="${RM_PARSE_INPUT#'!'}" ;;
      *) break ;;
    esac
    trim_horizontal "$RM_PARSE_INPUT"
    RM_PARSE_INPUT="$TRIMMED"
  done
}

looks_explicit_rm() {
  analyze_rm_segment "$1" || return 1
  [ "$KNOWN_RM" -eq 1 ]
}

normalize_explicit_rm_words() {
  SCAN_WORDS=("${WORDS[@]}")
  skip_command_prefixes || return 1
  is_rm_word "${WORDS[$PREFIX_INDEX]:-}" || return 1
  WORDS=("${WORDS[@]:$PREFIX_INDEX}")
  WORDS[0]="rm"
}

REPOINT_CLASS=""
classify_repointing_segment() {
  local segment="$1" command program index token chars end_options=0
  REPOINT_CLASS=""
  trim_horizontal "$segment"
  segment="$TRIMMED"
  case "$segment" in '('*) REPOINT_CLASS=subshell; return 0 ;; esac
  # The command word comes after any control word or grouping opener, with the
  # segment's redirections removed: `then cd /` and `2>/dev/null cd /` both
  # change the folder.
  strip_control_openers "$segment"
  strip_command_redirections "$CONTROL_STRIPPED"
  scan_shell_words "$REDIR_STRIPPED" || :
  [ "${#SCAN_WORDS[@]}" -gt 0 ] || return 1
  skip_command_prefixes || return 1
  if [ "$PREFIX_ASSIGNMENT" -eq 1 ]; then REPOINT_CLASS=assignment; return 0; fi
  command="${SCAN_WORDS[$PREFIX_INDEX]:-}"
  case "$command" in
    cd) REPOINT_CLASS="cd"; return 0 ;;
    pushd) REPOINT_CLASS="pushd"; return 0 ;;
    popd) REPOINT_CLASS="popd"; return 0 ;;
  esac
  # Program names in any letter case: a case-insensitive filesystem runs LN
  # as ln.
  shopt -s nocasematch
  case "$command" in
    ln|/*/ln) program=ln ;;
    mv|/*/mv) program=mv ;;
    mkdir|/*/mkdir) program=mkdir ;;
    mount|/*/mount) program=mount ;;
    cp|/*/cp) program=cp ;;
    *) program="" ;;
  esac
  shopt -u nocasematch
  case "$program" in
    ln) REPOINT_CLASS="ln"; return 0 ;;
    mv) REPOINT_CLASS="mv"; return 0 ;;
    mkdir) REPOINT_CLASS="mkdir"; return 0 ;;
    mount) REPOINT_CLASS="mount"; return 0 ;;
    cp)
      index=$((PREFIX_INDEX + 1))
      while [ "$index" -lt "${#SCAN_WORDS[@]}" ]; do
        token="${SCAN_WORDS[$index]}"
        if [ "$end_options" -eq 0 ] && [ "$token" = "--" ]; then
          end_options=1
        elif [ "$end_options" -eq 0 ]; then
          case "$token" in
            --symbolic-link) REPOINT_CLASS=cp_symlink; return 0 ;;
            -?*) chars="${token#-}"; case "$chars" in *s*) REPOINT_CLASS=cp_symlink; return 0 ;; esac ;;
          esac
        fi
        index=$((index + 1))
      done
      ;;
  esac
  return 1
}

validate_target() {
  local target="$1" resolved status
  [ -n "$target" ] || return 1
  # Reject before lexical normalization: kernel resolution follows a symlink
  # before applying a later .. component, so collapsing it first is unsafe.
  case "/$target/" in */../*) return 1 ;; esac
  # A working folder in no repository has no worktree to anchor a target.
  if [ -z "$WORKTREE_ROOT" ]; then
    validate_target_outside_worktree "$target"
    return
  fi
  # These patterns intentionally match literal shell markers without expansion.
  # shellcheck disable=SC2016,SC2088
  case "$target" in
    /|"$USERS_ROOT"|"$USERS_ROOT"/*|.|'$HOME'|'$HOME/'*|'${HOME}'|'${HOME}/'*|'~'|'~/'*) return 1 ;;
  esac
  resolved="$(canonical_target "$target" "$CWD_CANON")"
  status=$?
  [ "$status" -eq 0 ] || return 1
  case "$resolved" in */.[gG][iI][tT]|*/.[gG][iI][tT]/*) return 1 ;; esac
  # strict_descendant against an empty root is true for every absolute path.
  [ -n "$WORKTREE_ROOT" ] && strict_descendant "$resolved" "$WORKTREE_ROOT" && return 0
  [ -n "$SCRATCH_ROOT" ] && strict_descendant "$resolved" "$SCRATCH_ROOT" && return 0
  return 1
}

# True when $1, a canonical absolute path, is inside a git repository or
# worktree, or when that cannot be ruled out. Only git can recognize a bare
# repository (it has no .git entry), so this fails closed: it is false only
# when git runs from the nearest existing directory at or above $1 and fails
# with its plain "not a git repository" error as its only output line, and
# neither $1 nor any ancestor holds a .git entry. A missing git, or any other
# git failure (dubious ownership, an unreadable folder), counts as inside.
# git is the trusted-root git the context tier resolved (GIT_BIN), run by its
# absolute path through /usr/bin/env, never looked up on PATH. It runs with
# every variable that selects or bounds the repository cleared (an inherited
# GIT_DIR would otherwise answer for the wrong repository) and with discovery
# allowed across filesystems, so only the path itself decides.
path_in_git_repo() {
  local dir="$1" err
  while [ "$dir" != "/" ] && [ ! -d "$dir" ]; do
    dir="${dir%/*}"
    [ -n "$dir" ] || dir="/"
  done
  [ -n "$GIT_BIN" ] || return 0
  if err="$(/usr/bin/env -u GIT_DIR -u GIT_WORK_TREE -u GIT_COMMON_DIR \
    -u GIT_INDEX_FILE -u GIT_OBJECT_DIRECTORY -u GIT_ALTERNATE_OBJECT_DIRECTORIES \
    -u GIT_NAMESPACE -u GIT_CEILING_DIRECTORIES GIT_DISCOVERY_ACROSS_FILESYSTEM=1 \
    LC_ALL=C "$GIT_BIN" -C "$dir" rev-parse --git-dir 2>&1 >/dev/null)"; then
    return 0
  fi
  # One line, starting with git's own error: a path quoted inside some other
  # message can never match.
  case "$err" in
    *$'\n'*) return 0 ;;
    'fatal: not a git repository'*) ;;
    *) return 0 ;;
  esac
  while :; do
    { [ -e "$dir/.git" ] || [ -L "$dir/.git" ]; } && return 0
    [ "$dir" != "/" ] || return 1
    dir="${dir%/*}"
    [ -n "$dir" ] || dir="/"
  done
}

# The target rule for a working folder in no git repository (a workspace
# folder). Only a literal absolute target is judged, on its own path:
#   - the spellings the worktree rule refuses (/, /Users, ., $HOME and ~
#     forms) are refused the same way;
#   - a relative target is refused as cwd_outside_worktree, since no worktree
#     anchors it;
#   - a target reached through a symlink, inside a .git path, or inside any
#     git repository or worktree (registered lane roots included) is refused;
#   - anything strictly below the session scratchpad is accepted, as it is
#     from a worktree;
#   - everything else is refused.
validate_target_outside_worktree() {
  local target="$1" resolved status
  # These patterns intentionally match literal shell markers without expansion.
  # shellcheck disable=SC2016,SC2088
  case "$target" in
    /|"$USERS_ROOT"|.|'$HOME'|'$HOME/'*|'${HOME}'|'${HOME}/'*|'~'|'~/'*) return 1 ;;
    /*) ;;
    *) policy_deny cwd_outside_worktree ;;
  esac
  resolved="$(canonical_target "$target" "$CWD_CANON")"
  status=$?
  [ "$status" -eq 0 ] || return 1
  case "$resolved" in */.[gG][iI][tT]|*/.[gG][iI][tT]/*) return 1 ;; esac
  path_in_git_repo "$resolved" && return 1
  [ -n "$SCRATCH_ROOT" ] && strict_descendant "$resolved" "$SCRATCH_ROOT" && return 0
  return 1
}

validate_rm() {
  local i=1 token chars end_options=0 saw_option=0 targets=0
  while [ "$i" -lt "${#WORDS[@]}" ]; do
    token="${WORDS[$i]}"
    if [ "$end_options" -eq 0 ] && [ "$token" = "--" ]; then
      end_options=1
    elif [ "$end_options" -eq 0 ] && [ "${token#-}" != "$token" ]; then
      case "$token" in
        --recursive|--force) saw_option=1 ;;
        --*) return 1 ;;
        -?*)
          chars="${token#-}"
          case "$chars" in *[!rRf]*) return 1 ;; *) saw_option=1 ;; esac
          ;;
        *) return 1 ;;
      esac
    else
      targets=$((targets + 1))
      validate_target "$token" || return 1
    fi
    i=$((i + 1))
  done
  [ "$saw_option" -eq 1 ] && [ "$targets" -gt 0 ]
}

has_symlink_component() {
  local remainder component current=""
  remainder="${1#/}"
  while [ -n "$remainder" ]; do
    case "$remainder" in
      */*) component="${remainder%%/*}"; remainder="${remainder#*/}" ;;
      *) component="$remainder"; remainder="" ;;
    esac
    current="$current/$component"
    [ ! -L "$current" ] || return 0
  done
  return 1
}

is_registered_linked_worktree() {
  local line entry=0
  [ -n "$AUTHORIZED_ROOT" ] || return 1
  [ -n "$WORKTREE_ROOT" ] || return 1
  [ "$AUTHORIZED_ROOT" != "$WORKTREE_ROOT" ] || return 1
  # Git lists the repository's primary worktree first. Only a later, linked
  # entry is eligible for this narrowly authorized removal form.
  while IFS= read -r line; do
    case "$line" in
      "worktree "*)
        entry=$((entry + 1))
        [ "$entry" -gt 1 ] && [ "${line#worktree }" = "$AUTHORIZED_ROOT" ] && return 0
        ;;
    esac
  done < <(git -C "$WORKTREE_ROOT" worktree list --porcelain 2>/dev/null)
  return 1
}

# Registry-backed removal. The registry location comes only from the
# LANE_ROOTS_REGISTRY environment variable of the hook process, never from the
# command text. The registry must look like one this user's launcher wrote: a
# regular, non-symlink file named lane-roots.tsv, owned by the current user,
# not group- or world-writable, inside a directory that is owned by the
# current user and not group- or world-writable.
LANE_REGISTRY_BASENAME="lane-roots.tsv"
LANE_REGISTRY_HEADER=$'path\trepo\tbranch\tcreated_at'

# Prints "<uid> <octal mode>" for $1. BSD stat takes -f FORMAT; on GNU stat -f
# means "filesystem status", so the flavour is detected from `stat --version`
# and only that flavour's syntax is ever used. A stat that prints a version
# banner takes the GNU syntax, whatever the banner names: GNU coreutils and
# its uutils rewrite (which some Linux distributions ship as /usr/bin/stat,
# with a banner that does not say GNU) both do. BSD stat rejects the flag
# and prints nothing on standard output. The binary is taken from fixed
# absolute paths only, never from PATH (a planted `stat` must not be able to vouch for a registry).
# No usable binary, or a failing stat, fails closed.
STAT_CANDIDATES=(/usr/bin/stat /bin/stat)
stat_fields() { # BSD_FORMAT GNU_FORMAT PATH
  local stat_bin="" candidate out
  for candidate in "${STAT_CANDIDATES[@]}"; do
    if [ -x "$candidate" ] && [ ! -d "$candidate" ]; then stat_bin="$candidate"; break; fi
  done
  [ -n "$stat_bin" ] || return 1
  if "$stat_bin" --version 2>/dev/null | grep -q .; then
    out="$("$stat_bin" -c "$2" -- "$3" 2>/dev/null)" || return 1
  else
    out="$("$stat_bin" -f "$1" -- "$3" 2>/dev/null)" || return 1
  fi
  printf '%s' "$out"
}

stat_owner_mode() {
  stat_fields '%u %Lp' '%u %a' "$1"
}

owned_and_private() {
  local info owner mode
  info="$(stat_owner_mode "$1")" || return 1
  owner="${info%% *}"
  mode="${info#* }"
  [ "$owner" = "$(/usr/bin/id -u 2>/dev/null)" ] || return 1
  case "$mode" in *[!0-7]*|"") return 1 ;; esac
  while [ "${#mode}" -lt 3 ]; do mode="0$mode"; done
  mode="${mode: -3}"
  case "${mode:1:1}${mode:2:1}" in *[2367]*) return 1 ;; esac
  return 0
}

canonical_dir() {
  [ -d "$1" ] || return 1
  (cd -- "$1" 2>/dev/null && pwd -P)
}

trusted_lane_registry() {
  local reg="${LANE_ROOTS_REGISTRY:-}" dir
  case "$reg" in /*) ;; *) return 1 ;; esac
  case "/$reg/" in */../*|*/./*) return 1 ;; esac
  [ "${reg##*/}" = "$LANE_REGISTRY_BASENAME" ] || return 1
  [ -f "$reg" ] && [ ! -L "$reg" ] || return 1
  dir="$(canonical_dir "${reg%/*}")" || return 1
  owned_and_private "$dir" || return 1
  owned_and_private "$reg" || return 1
  return 0
}

# Identity binding for a registry row. Beside lane-roots.tsv the launcher
# keeps two more files, both of which must pass the same ownership and mode
# checks as the registry and start with these exact header lines:
#   lane-roots.ids.tsv      path, generation, identity, seat, registered_at:
#                           the identity recorded when the tree was registered
#   lane-prune-pending.tsv  path, repo, generation, identity, expires_at:
#                           a short-lived permission to remove one generation
# A removal is allowed only when the newest pending row for the path is
# unexpired, names the same repository, and its generation and identity equal
# the path's current (highest-generation) identity row, and the identity the
# guard computes now from the live tree equals them too. A tree removed and
# re-created at the same path has a new identity, so an old permission no
# longer matches. Any malformed row in either file denies every removal.
# Every identity row must also be what the launcher writes: a generation of
# at least 1 and an absolute path that is already canonical, each path at
# most once in the file. The whole file is checked before any row is used,
# and a row that breaks one of these rules denies by name. A row whose tree
# is gone stays in the file until the launcher's next sweep, so it is
# skipped, but its path must still be written canonically.
# This hook never writes either file.
LANE_IDS_BASENAME="lane-roots.ids.tsv"
LANE_IDS_HEADER=$'lane-roots-ids/v1\tpath\tgeneration\tidentity\tseat\tregistered_at'
LANE_PENDING_BASENAME="lane-prune-pending.tsv"
LANE_PENDING_HEADER=$'lane-prune-pending/v1\tpath\trepo\tgeneration\tidentity\texpires_at'
DATE_BIN="/bin/date"

# Splits $1 on tabs into TSV_FIELDS; true only for exactly $2 fields. Empty
# fields are kept (a read with IFS=tab would merge them).
TSV_FIELDS=()
split_tsv_line() {
  local rest="$1" n=0
  TSV_FIELDS=()
  while :; do
    case "$rest" in
      *$'\t'*) TSV_FIELDS[n]="${rest%%$'\t'*}"; rest="${rest#*$'\t'}" ;;
      *) TSV_FIELDS[n]="$rest"; break ;;
    esac
    n=$((n + 1))
  done
  [ "${#TSV_FIELDS[@]}" -eq "$2" ]
}

# A non-negative decimal of at most 18 digits (stays inside shell arithmetic).
is_small_decimal() {
  case "$1" in ""|*[!0-9]*) return 1 ;; esac
  [ "${#1}" -le 18 ]
}

private_lane_file() {
  [ -f "$1" ] && [ ! -L "$1" ] || return 1
  owned_and_private "$1"
}

# Prints the 32-character lowercase hex nonce stored at
# $1/lane-roots-nonce, or returns 1. The file must be a regular
# non-symlink file, user-owned with permission bits exactly 0600,
# exactly 33 bytes, and those bytes must be 32 of [0-9a-f] plus LF.
# The mode is read whole, setuid/setgid/sticky included (BSD `%Mp%Lp`,
# GNU `%a`; BSD `%Lp` alone drops them): at most four octal digits, and
# with leading zeroes stripped it must be exactly 600, so 1600, 2600,
# 4600 or any other mode refuses.
# Unreadable / malformed / missing: return 1. Never writes.
# bash 3.2 has no `read -N`; `IFS= read -r -d '' -n 34` is the same
# observable (up to 34 bytes, NUL delimiter, LF kept, rc 1 at EOF).
lane_nonce() { # ADMIN_DIR
  local f data hex sz info owner mode
  f="$1/lane-roots-nonce"
  [ -f "$f" ] && [ ! -L "$f" ] || return 1
  owned_and_private "$f" || return 1
  info="$(stat_fields '%u %Mp%Lp' '%u %a' "$f")" || return 1
  owner="${info%% *}"
  mode="${info#* }"
  [ "$owner" = "$(/usr/bin/id -u 2>/dev/null)" ] || return 1
  case "$mode" in *[!0-7]*|"") return 1 ;; esac
  [ "${#mode}" -le 4 ] || return 1
  while [ "${mode#0}" != "$mode" ]; do mode="${mode#0}"; done
  [ "$mode" = "600" ] || return 1
  sz="$(stat_fields '%z' '%s' "$f")" || return 1
  [ "$sz" = 33 ] || return 1
  data=""
  IFS= read -r -d '' -n 34 data < "$f"
  hex="${data%$'\n'}"
  [ "$data" = "$hex"$'\n' ] || return 1
  [ "${#hex}" -eq 32 ] || return 1
  case "$hex" in *[!0-9a-f]*) return 1 ;; esac
  printf '%s' "$hex"
}

# Prints the identity token of the linked worktree at $1:
#   <canonical admin dir>:<st_dev>:<st_ino>:<birth seconds>.<birth nanoseconds>:<nonce>
# of the admin dir's `gitdir` file plus the per-creation nonce. The birth
# time is read to the nanosecond (BSD `%.9FB`, GNU `%.9W`). The nonce is
# the 32 lowercase hex digits in `lane-roots-nonce` (see lane_nonce). A
# missing file, a birth time that is unavailable, 0 or of any other shape,
# or an unreadable nonce, fails; there is no fallback to whole seconds or
# to another timestamp. This helper never writes the nonce.
live_worktree_identity() {
  local admin canon_admin info dev ino birth rest secs nanos nonce
  admin="$(git -C "$1" rev-parse --path-format=absolute --git-dir 2>/dev/null)" || return 1
  case "$admin" in /*) ;; *) return 1 ;; esac
  canon_admin="$(canonical_dir "$admin")" || return 1
  [ -f "$canon_admin/gitdir" ] && [ ! -L "$canon_admin/gitdir" ] || return 1
  info="$(stat_fields '%d %i %.9FB' '%d %i %.9W' "$canon_admin/gitdir")" || return 1
  nonce="$(lane_nonce "$canon_admin")" || return 1
  dev="${info%% *}"; rest="${info#* }"
  ino="${rest%% *}"; birth="${rest#* }"
  secs="${birth%.*}"; nanos="${birth##*.}"
  [ "$birth" = "$secs.$nanos" ] || return 1
  is_small_decimal "$dev" && is_small_decimal "$ino" && is_small_decimal "$secs" || return 1
  is_small_decimal "$nanos" && [ "${#nanos}" -eq 9 ] || return 1
  case "$secs" in 0*) return 1 ;; esac
  printf '%s:%s:%s:%s:%s' "$canon_admin" "$dev" "$ino" "$birth" "$nonce"
}

# Prints the path the launcher would have stored for the identity row path
# $1, which does not resolve: its nearest existing ancestor (`/` at worst)
# resolved as canonical_dir resolves it, with the missing components after
# it appended as written. Fails when that ancestor does not resolve (a file,
# a dangling symlink, or a directory that cannot be entered).
stale_ids_row_path() {
  local p="$1" suffix="" resolved
  while [ ! -e "$p" ] && [ ! -L "$p" ]; do
    suffix="/${p##*/}$suffix"
    p="${p%/*}"
    [ -n "$p" ] || p="/"
  done
  resolved="$(canonical_dir "$p")" || return 1
  [ "$resolved" != "/" ] || resolved=""
  printf '%s' "$resolved$suffix"
}

# Returns 0 when authorized, 1 when not, and 2 when an identity row breaks
# the launcher's grammar; LANE_REGISTRY_FAILURE then names the rule:
#   lane_ids_bad_generation     the generation is 0
#   lane_ids_relative_path      the path is not absolute
#   lane_ids_noncanonical_path  the path is not its own canonical form, or,
#                               when it does not resolve, it has a `.` or
#                               `..` component, a `//` or a trailing `/`, or
#                               it is not the path stale_ids_row_path rebuilds
#   lane_ids_duplicate_path     the path appears on more than one row
lane_identity_authorized() {
  local target="$1" repo="$2" dir="$3" ids pending line first canon now live
  local want_repo="" want_gen="" want_ident="" want_exp="" cur_gen="" cur_ident=""
  local seen=$'\n'
  ids="$dir/$LANE_IDS_BASENAME"
  pending="$dir/$LANE_PENDING_BASENAME"
  private_lane_file "$ids" || return 1
  private_lane_file "$pending" || return 1

  first=1
  while IFS= read -r line || [ -n "$line" ]; do
    if [ "$first" -eq 1 ]; then
      first=0
      [ "$line" = "$LANE_PENDING_HEADER" ] || return 1
      continue
    fi
    split_tsv_line "$line" 5 || return 1
    [ -n "${TSV_FIELDS[0]}" ] && [ -n "${TSV_FIELDS[1]}" ] && [ -n "${TSV_FIELDS[3]}" ] || return 1
    is_small_decimal "${TSV_FIELDS[2]}" && is_small_decimal "${TSV_FIELDS[4]}" || return 1
    canon="$(canonical_dir "${TSV_FIELDS[0]}")" || continue
    [ "$canon" = "$target" ] || continue
    # Later rows are newer; the newest row for the path wins.
    want_repo="${TSV_FIELDS[1]}"; want_gen="${TSV_FIELDS[2]}"
    want_ident="${TSV_FIELDS[3]}"; want_exp="${TSV_FIELDS[4]}"
  done < "$pending"
  [ "$first" -eq 0 ] || return 1
  [ -n "$want_gen" ] || return 1
  canon="$(canonical_dir "$want_repo")" || return 1
  [ "$canon" = "$repo" ] || return 1
  now="$("$DATE_BIN" +%s 2>/dev/null)" || return 1
  is_small_decimal "$now" || return 1
  [ "$now" -lt "$want_exp" ] || return 1

  first=1
  while IFS= read -r line || [ -n "$line" ]; do
    if [ "$first" -eq 1 ]; then
      first=0
      [ "$line" = "$LANE_IDS_HEADER" ] || return 1
      continue
    fi
    split_tsv_line "$line" 5 || return 1
    [ -n "${TSV_FIELDS[0]}" ] && [ -n "${TSV_FIELDS[2]}" ] || return 1
    is_small_decimal "${TSV_FIELDS[1]}" || return 1
    [ "${TSV_FIELDS[1]}" -ge 1 ] || { LANE_REGISTRY_FAILURE=lane_ids_bad_generation; return 2; }
    case "${TSV_FIELDS[0]}" in /*) ;; *) LANE_REGISTRY_FAILURE=lane_ids_relative_path; return 2 ;; esac
    if canon="$(canonical_dir "${TSV_FIELDS[0]}")"; then
      [ "$canon" = "${TSV_FIELDS[0]}" ] || { LANE_REGISTRY_FAILURE=lane_ids_noncanonical_path; return 2; }
    else
      # The tree is gone and the next sweep drops its row: skip it, but
      # only once its path is the one the launcher would have stored.
      case "${TSV_FIELDS[0]}/" in
        *//*|*/./*|*/../*) LANE_REGISTRY_FAILURE=lane_ids_noncanonical_path; return 2 ;;
      esac
      canon="$(stale_ids_row_path "${TSV_FIELDS[0]}")" || { LANE_REGISTRY_FAILURE=lane_ids_noncanonical_path; return 2; }
      [ "$canon" = "${TSV_FIELDS[0]}" ] || { LANE_REGISTRY_FAILURE=lane_ids_noncanonical_path; return 2; }
    fi
    case "$seen" in *$'\n'"$canon"$'\n'*) LANE_REGISTRY_FAILURE=lane_ids_duplicate_path; return 2 ;; esac
    seen="$seen$canon"$'\n'
    [ "$canon" = "$target" ] || continue
    if [ -z "$cur_gen" ] || [ "${TSV_FIELDS[1]}" -gt "$cur_gen" ]; then
      cur_gen="${TSV_FIELDS[1]}"; cur_ident="${TSV_FIELDS[2]}"
    fi
  done < "$ids"
  [ "$first" -eq 0 ] || return 1
  [ -n "$cur_gen" ] || return 1
  [ "$cur_gen" -eq "$want_gen" ] || return 1
  [ "$cur_ident" = "$want_ident" ] || return 1

  live="$(live_worktree_identity "$target")" || return 1
  [ "$live" = "$want_ident" ] || return 1
  return 0
}

# True when LANE_ROOTS_REGISTRY is unset or empty, or when it is an absolute
# path with no `.` or `..` component whose directory resolves and which itself
# has no entry (not even a dangling symlink). Unset or empty is the dormant
# layer (path_in_lane_registry answers 3); a set variable that names no entry
# is armed and absent, which validate_lane_registry_file denies as
# lane_registry_absent_armed. A registry that exists in any form must pass
# trusted_lane_registry.
lane_registry_absent() {
  local reg="${LANE_ROOTS_REGISTRY:-}"
  [ -n "$reg" ] || return 0
  case "$reg" in /*) ;; *) return 1 ;; esac
  case "/$reg/" in */../*|*/./*) return 1 ;; esac
  canonical_dir "${reg%/*}" >/dev/null || return 1
  [ ! -e "$reg" ] && [ ! -L "$reg" ]
}

# True when the registry row path $1 provably names nothing on disk: it is
# absolute with no `.` or `..` component, and walking up from it reaches a
# directory that resolves while the entry just below that directory does not
# exist. A lookup inside a directory that resolves cannot fail for want of
# search permission, so the missing entry is real. Such a row (a stale row
# for a tree already removed) cannot name the removal target, which exists
# and resolves. Any other row path that does not resolve is malformed.
lane_row_path_gone() {
  local p="$1" parent
  case "$p" in /*) ;; *) return 1 ;; esac
  case "/$p/" in */../*|*/./*) return 1 ;; esac
  while :; do
    [ ! -e "$p" ] && [ ! -L "$p" ] || return 1
    parent="${p%/*}"
    [ -n "$parent" ] || parent="/"
    canonical_dir "$parent" >/dev/null && return 0
    [ "$parent" != "/" ] || return 1
    p="$parent"
  done
}

# Reads the registry for the removal of $1 from repository $2:
#   0  a row names the target for this repository and its identity is authorized
#   1  the registry was read and validated in full and no row names the target
#   2  a row names the target for this repository; its identity is not authorized
#   3  there is no registry: LANE_ROOTS_REGISTRY is unset or empty
#   4  the registry could not be read or validated; LANE_REGISTRY_FAILURE names why:
#      lane_registry_absent_armed  the variable is set and names no file
#      lane_registry_untrusted     it fails trusted_lane_registry
#      lane_registry_bad_header    it is empty or unreadable, or its first line
#                                  is not the header
#      lane_registry_bad_row       a row is not 4 fields with absolute path and
#                                  repo, a row's path does not resolve and is not
#                                  provably gone, or the repo of a row naming the
#                                  target does not resolve
#      lane_registry_repo_mismatch a row names the target for another repository
#      lane_ids_*                  an identity row breaks the launcher's grammar
#                                  (see lane_identity_authorized)
# Only 1 and 3 may fall through to the temp-root rule. Every row is validated
# before any decision, so a malformed row anywhere denies.
LANE_REGISTRY_FAILURE=""

# The whole-file checks of an armed lane registry, shared by
# initialize_context (every judged removal in an armed session) and
# path_in_lane_registry, so there is one copy of them: the file exists, passes
# trusted_lane_registry, starts with the header, and every row is 4 fields
# with an absolute path and repository whose path resolves or is provably
# gone. Returns 1 with LANE_REGISTRY_FAILURE set on the first failure.
validate_lane_registry_file() {
  local first=1 line reg="${LANE_ROOTS_REGISTRY:-}"
  LANE_REGISTRY_FAILURE=""
  if lane_registry_absent; then LANE_REGISTRY_FAILURE=lane_registry_absent_armed; return 1; fi
  trusted_lane_registry || { LANE_REGISTRY_FAILURE=lane_registry_untrusted; return 1; }
  while IFS= read -r line || [ -n "$line" ]; do
    if [ "$first" -eq 1 ]; then
      first=0
      [ "$line" = "$LANE_REGISTRY_HEADER" ] || { LANE_REGISTRY_FAILURE=lane_registry_bad_header; return 1; }
      continue
    fi
    split_tsv_line "$line" 4 || { LANE_REGISTRY_FAILURE=lane_registry_bad_row; return 1; }
    case "${TSV_FIELDS[0]}" in /*) ;; *) LANE_REGISTRY_FAILURE=lane_registry_bad_row; return 1 ;; esac
    case "${TSV_FIELDS[1]}" in /*) ;; *) LANE_REGISTRY_FAILURE=lane_registry_bad_row; return 1 ;; esac
    if ! canonical_dir "${TSV_FIELDS[0]}" >/dev/null; then
      lane_row_path_gone "${TSV_FIELDS[0]}" && continue
      LANE_REGISTRY_FAILURE=lane_registry_bad_row; return 1
    fi
  done < "$reg"
  [ "$first" -eq 0 ] || { LANE_REGISTRY_FAILURE=lane_registry_bad_header; return 1; }
  return 0
}

path_in_lane_registry() {
  local target="$1" repo="$2" first=1 line canon_path canon_repo matched=0
  local reg="${LANE_ROOTS_REGISTRY:-}"
  LANE_REGISTRY_FAILURE=""
  [ -n "$reg" ] || return 3
  validate_lane_registry_file || return 4
  while IFS= read -r line || [ -n "$line" ]; do
    if [ "$first" -eq 1 ]; then
      first=0
      continue
    fi
    # The file was validated above; a row that no longer passes the same
    # shape checks (the file changed in between) still denies.
    split_tsv_line "$line" 4 || { LANE_REGISTRY_FAILURE=lane_registry_bad_row; return 4; }
    case "${TSV_FIELDS[0]}" in /*) ;; *) LANE_REGISTRY_FAILURE=lane_registry_bad_row; return 4 ;; esac
    case "${TSV_FIELDS[1]}" in /*) ;; *) LANE_REGISTRY_FAILURE=lane_registry_bad_row; return 4 ;; esac
    canon_path="$(canonical_dir "${TSV_FIELDS[0]}")" || continue
    [ "$canon_path" = "$target" ] || continue
    canon_repo="$(canonical_dir "${TSV_FIELDS[1]}")" || { LANE_REGISTRY_FAILURE=lane_registry_bad_row; return 4; }
    [ "$canon_repo" = "$repo" ] || { LANE_REGISTRY_FAILURE=lane_registry_repo_mismatch; return 4; }
    matched=1
  done < "$reg"
  [ "$matched" -eq 1 ] || return 1
  lane_identity_authorized "$target" "$repo" "${reg%/*}"
  case $? in
    0) return 0 ;;
    2) return 4 ;;
  esac
  return 2
}

# True when $1 is a linked (non-primary) worktree of the repository at $2.
is_linked_worktree_of() {
  local target="$1" repo="$2" line entry=0 listed
  while IFS= read -r line; do
    case "$line" in
      "worktree "*)
        entry=$((entry + 1))
        [ "$entry" -gt 1 ] || continue
        listed="$(canonical_dir "${line#worktree }")" || continue
        [ "$listed" = "$target" ] && return 0
        ;;
    esac
  done < <(git -C "$repo" worktree list --porcelain 2>/dev/null)
  return 1
}

# Throwaway worktrees (a reviewer's checkout made under mktemp -d) are never in
# the registry. A canonical target strictly below one of these roots is
# accepted once it is a linked worktree of the repository; the roots
# themselves and everything outside them are not. Only reached for the
# non-forced forms, so git still refuses a dirty or locked tree, and only
# when the registry was read and validated in full with no row for the
# target, or when there is no registry at all.
TEMP_ROOTS=(/tmp /private/tmp /var/folders /private/var/folders)
path_in_temp_root() {
  local root
  for root in ${TEMP_ROOTS[@]+"${TEMP_ROOTS[@]}"}; do
    strict_descendant "$1" "$root" && return 0
  done
  return 1
}

# Seat-bound cleanup registry. Its path and seat come only from the hook
# process's environment, never from command text. It is an independent fallback
# after the existing worktree validators and never authorizes rm.
CLEANUP_REGISTRY_BASENAME="cleanup-items.tsv"
CLEANUP_REGISTRY_HEADER=$'repo\tpath\tkind\tseat\trow\tcreated_at'

owned_with_exact_mode() { # PATH MODE-WITHOUT-LEADING-ZEROES
  local info owner mode
  info="$(stat_fields '%u %Mp%Lp' '%u %a' "$1")" || return 1
  owner="${info%% *}"
  mode="${info#* }"
  [ "$owner" = "$(/usr/bin/id -u 2>/dev/null)" ] || return 1
  case "$mode" in ""|*[!0-7]*) return 1 ;; esac
  while [ "${mode#0}" != "$mode" ]; do mode="${mode#0}"; done
  [ "$mode" = "$2" ]
}

trusted_cleanup_registry() {
  local reg="${CLEANUP_ITEMS_REGISTRY:-}" dir
  case "$reg" in /*) ;; *) return 1 ;; esac
  case "/$reg/" in */../*|*/./*) return 1 ;; esac
  [ "${reg##*/}" = "$CLEANUP_REGISTRY_BASENAME" ] || return 1
  [ -f "$reg" ] && [ ! -L "$reg" ] || return 1
  has_symlink_component "$reg" && return 1
  dir="$(canonical_dir "${reg%/*}")" || return 1
  [ "$dir" = "${reg%/*}" ] || return 1
  owned_with_exact_mode "$dir" 700 || return 1
  owned_with_exact_mode "$reg" 600
}

# The whole-file checks of an armed cleanup registry (CLEANUP_ITEMS_REGISTRY
# non-empty), run by initialize_context for every judged removal in an armed
# session: the file exists, passes trusted_cleanup_registry, holds the exact
# header and only 6-field rows with an absolute repository and path, and
# CLEANUP_SEAT is non-empty, since every row is bound to a seat. Returns 1
# with CLEANUP_REGISTRY_FAILURE set on the first failure.
CLEANUP_REGISTRY_FAILURE=""
validate_cleanup_registry_file() {
  local reg="${CLEANUP_ITEMS_REGISTRY:-}" first=1 line
  CLEANUP_REGISTRY_FAILURE=""
  if [ ! -e "$reg" ] && [ ! -L "$reg" ]; then
    CLEANUP_REGISTRY_FAILURE=cleanup_registry_absent_armed
    return 1
  fi
  trusted_cleanup_registry || { CLEANUP_REGISTRY_FAILURE=cleanup_registry_untrusted; return 1; }
  while IFS= read -r line || [ -n "$line" ]; do
    if [ "$first" -eq 1 ]; then
      first=0
      [ "$line" = "$CLEANUP_REGISTRY_HEADER" ] || { CLEANUP_REGISTRY_FAILURE=cleanup_registry_malformed; return 1; }
      continue
    fi
    split_tsv_line "$line" 6 || { CLEANUP_REGISTRY_FAILURE=cleanup_registry_malformed; return 1; }
    case "${TSV_FIELDS[0]}" in /*) ;; *) CLEANUP_REGISTRY_FAILURE=cleanup_registry_malformed; return 1 ;; esac
    case "${TSV_FIELDS[1]}" in /*) ;; *) CLEANUP_REGISTRY_FAILURE=cleanup_registry_malformed; return 1 ;; esac
  done < "$reg"
  [ "$first" -eq 0 ] || { CLEANUP_REGISTRY_FAILURE=cleanup_registry_malformed; return 1; }
  [ -n "${CLEANUP_SEAT:-}" ] || { CLEANUP_REGISTRY_FAILURE=cleanup_seat_missing; return 1; }
  return 0
}

literal_cleanup_dir() {
  local raw="$1" resolved
  case "$raw" in /*) ;; *) return 1 ;; esac
  case "$raw" in */|*[\*\?\[\]\{\}\$\`\\\~\!\;\&\|\<\>\(\)\#]*) return 1 ;; esac
  case "/$raw/" in */../*|*/./*) return 1 ;; esac
  has_symlink_component "$raw" && return 1
  resolved="$(canonical_dir "$raw")" || return 1
  [ "$resolved" = "$raw" ] || return 1
  printf '%s' "$resolved"
}

cleanup_raw_path_word_matches() { # RAW DEQUOTED
  [ "$1" = "$2" ] || [ "$1" = "'$2'" ] || [ "$1" = "\"$2\"" ]
}

cleanup_git_raw_shape() {
  local segment="$1"
  split_shell_words "$segment" || return 1
  [ "${#SPLIT_WORDS[@]}" -eq 6 ] || return 1
  [ "${SPLIT_RAW[0]}" = git ] && [ "${SPLIT_RAW[1]}" = -C ] \
    && [ "${SPLIT_RAW[3]}" = worktree ] && [ "${SPLIT_RAW[4]}" = remove ] || return 1
  cleanup_raw_path_word_matches "${SPLIT_RAW[2]}" "${SPLIT_WORDS[2]}" || return 1
  cleanup_raw_path_word_matches "${SPLIT_RAW[5]}" "${SPLIT_WORDS[5]}"
}

# Reads every row before returning true. A malformed row anywhere cannot
# authorize a removal.
cleanup_registry_worktree_row() { # REPO PATH REQUIRE-SEAT(0|1)
  local want_repo="$1" want_path="$2" require_seat="$3"
  local seat="${CLEANUP_SEAT:-}" first=1 line matched=0
  [ "$require_seat" -eq 0 ] || [ -n "$seat" ] || return 1
  trusted_cleanup_registry || return 1
  while IFS= read -r line || [ -n "$line" ]; do
    if [ "$first" -eq 1 ]; then
      first=0
      [ "$line" = "$CLEANUP_REGISTRY_HEADER" ] || return 1
      continue
    fi
    split_tsv_line "$line" 6 || return 1
    case "${TSV_FIELDS[0]}" in /*) ;; *) return 1 ;; esac
    case "${TSV_FIELDS[1]}" in /*) ;; *) return 1 ;; esac
    [ "${TSV_FIELDS[0]}" = "$want_repo" ] || continue
    [ "${TSV_FIELDS[1]}" = "$want_path" ] || continue
    [ "${TSV_FIELDS[2]}" = worktree ] || continue
    [ "$require_seat" -eq 0 ] || [ "${TSV_FIELDS[3]}" = "$seat" ] || continue
    matched=1
  done < "$CLEANUP_ITEMS_REGISTRY"
  [ "$first" -eq 0 ] && [ "$matched" -eq 1 ]
}

# Pre-emption needs only evidence that the configured registry names this
# canonical pair. Trust, grammar and seat binding are checked later by the
# validator. Reading an untrusted row can only force a denial; it can never
# authorize one.
cleanup_registry_mentions_worktree() { # REPO PATH
  local want_repo="$1" want_path="$2" reg="${CLEANUP_ITEMS_REGISTRY:-}" line
  [ -n "$reg" ] && [ -r "$reg" ] || return 1
  while IFS= read -r line || [ -n "$line" ]; do
    split_tsv_line "$line" 6 || continue
    [ "${TSV_FIELDS[0]}" = "$want_repo" ] || continue
    [ "${TSV_FIELDS[1]}" = "$want_path" ] || continue
    [ "${TSV_FIELDS[2]}" = worktree ] || continue
    return 0
  done < "$reg"
  return 1
}

CLEANUP_GIT_DIR=""
CLEANUP_GIT_WORK_TREE=""
CLEANUP_GIT_OUTPUT=""
CLEANUP_GIT_STATUS=0
CLEANUP_GIT_TIMEOUT_SECONDS=2

# Every Git inspection is pinned to the already-bound admin directory and work
# tree, ignores ambient repository/config selection, disables fsmonitor and
# hooks, and runs in a process group killed by the two-second alarm.
cleanup_git() {
  local status=0
  CLEANUP_GIT_OUTPUT="$(
    /usr/bin/env -u GIT_DIR -u GIT_WORK_TREE -u GIT_COMMON_DIR \
      -u GIT_INDEX_FILE -u GIT_OBJECT_DIRECTORY -u GIT_ALTERNATE_OBJECT_DIRECTORIES \
      -u GIT_NAMESPACE -u GIT_CEILING_DIRECTORIES -u GIT_CONFIG_PARAMETERS \
      -u GIT_CONFIG_COUNT -u GIT_CONFIG_SYSTEM -u GIT_CONFIG_GLOBAL \
      GIT_DIR="$CLEANUP_GIT_DIR" GIT_WORK_TREE="$CLEANUP_GIT_WORK_TREE" \
      GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null LC_ALL=C \
      /usr/bin/perl -MPOSIX -e '
        my $seconds = shift @ARGV;
        POSIX::setpgid(0, 0) == 0 or exit 127;
        my $pid = fork();
        defined $pid or exit 127;
        if (!$pid) {
          exec { $ARGV[0] } @ARGV;
          exit 127;
        }
        $SIG{ALRM} = sub {
          $SIG{TERM} = "IGNORE";
          kill "TERM", -$$;
          select undef, undef, undef, 0.1;
          kill "KILL", -$$;
          exit 124;
        };
        alarm $seconds;
        waitpid($pid, 0);
        alarm 0;
        exit 128 + ($? & 127) if $? & 127;
        exit $? >> 8;
      ' "$CLEANUP_GIT_TIMEOUT_SECONDS" /usr/bin/git \
        -c core.fsmonitor=false -c core.hooksPath=/dev/null "$@" 2>/dev/null
  )" || status=$?
  CLEANUP_GIT_STATUS="$status"
  [ "$status" -eq 0 ]
}

read_cleanup_pointer() { # FILE PREFIX
  local value
  [ -f "$1" ] && [ ! -L "$1" ] || return 1
  value="$(/bin/cat -- "$1" 2>/dev/null)" || return 1
  case "$value" in *$'\n'*) return 1 ;; esac
  case "$value" in "$2"*) value="${value#"$2"}" ;; *) return 1 ;; esac
  [ -n "$value" ] || return 1
  printf '%s' "$value"
}

# Binds TARGET's .git file in both directions to one direct child of the
# repository's common-dir/worktrees directory. No Git process runs before this
# succeeds, so a swapped gitfile cannot select executable repository config.
bind_cleanup_worktree_gitdir() { # TARGET REPO
  local target="$1" repo="$2" target_ref admin commondir_ref common
  local admin_gitdir repo_git candidate
  target_ref="$(read_cleanup_pointer "$target/.git" "gitdir: ")" || return 1
  case "$target_ref" in /*) candidate="$target_ref" ;; *) candidate="$target/$target_ref" ;; esac
  admin="$(canonical_dir "$candidate")" || return 1
  commondir_ref="$(read_cleanup_pointer "$admin/commondir" "")" || return 1
  case "$commondir_ref" in /*) candidate="$commondir_ref" ;; *) candidate="$admin/$commondir_ref" ;; esac
  common="$(canonical_dir "$candidate")" || return 1
  [ "${admin%/*}" = "$common/worktrees" ] || return 1
  admin_gitdir="$(read_cleanup_pointer "$admin/gitdir" "")" || return 1
  case "$admin_gitdir" in /*) candidate="$admin_gitdir" ;; *) candidate="$admin/$admin_gitdir" ;; esac
  [ -f "$candidate" ] && [ ! -L "$candidate" ] || return 1
  candidate="$(canonical_dir "${candidate%/*}")/${candidate##*/}"
  [ "$candidate" = "$target/.git" ] || return 1
  repo_git="$(canonical_dir "$repo/.git")" || return 1
  [ "$repo_git" = "$common" ] || return 1
  { [ ! -e "$admin/locked" ] && [ ! -L "$admin/locked" ]; } || return 1
  CLEANUP_GIT_DIR="$admin"
  CLEANUP_GIT_WORK_TREE="$target"
}

# Python caches are the only ignored paths a registered cleanup removal allows.
cleanup_cache_path_allowed() { # TARGET PATH
  local target="$1" path="${2%/}"
  case "$path" in ""|/*|../*|*/../*) return 1 ;; esac
  case "/$path" in
    */__pycache__|*/__pycache__/*|*/.pytest_cache|*/.pytest_cache/*) return 0 ;;
    *.pyc)
      [ -f "$target/$path" ] && [ ! -L "$target/$path" ] && return 0
      ;;
  esac
  return 1
}

cleanup_nested_git_absent() {
  local target="$1" found status=0
  found="$(/usr/bin/find -P "$target" -mindepth 2 -iname .git \
    -print -quit 2>/dev/null)" || status=$?
  [ "$status" -eq 0 ] && [ -z "$found" ]
}


# Refuses a repository-shaped entry (a .git, HEAD, objects, refs, ...) inside
# a directory the ignored-path allowance admits: the Python caches.
cleanup_cache_markers_absent() { # TARGET
  local target="$1" found status=0
  local -a scope=(-path "$target/__pycache__/*" -o -path "$target/*/__pycache__/*"
    -o -path "$target/.pytest_cache/*" -o -path "$target/*/.pytest_cache/*")
  found="$(/usr/bin/find -P "$target" -mindepth 2 \
    \( "${scope[@]}" \) \
    \( -iname .git -o -iname HEAD -o -iname objects -o -iname refs \
      -o -iname commondir -o -iname gitdir -o -iname packed-refs \) \
    -print -quit 2>/dev/null)" || status=$?
  [ "$status" -eq 0 ] && [ -z "$found" ]
}

# A tracked symlink's index blob is its link text. Hashing the path would
# follow the link and hash the target's content instead, so hash the text
# readlink returns (no trailing newline) and compare it with the index blob.
cleanup_symlink_matches_index() { # PATH OID
  local link_text
  link_text="$(/usr/bin/readlink -- "$1" && printf x)" || return 1
  case "$link_text" in *$'\n'x) link_text="${link_text%$'\n'x}" ;; *) return 1 ;; esac
  cleanup_git hash-object --stdin < <(printf '%s' "$link_text") || return 1
  [ "$CLEANUP_GIT_OUTPUT" = "$2" ]
}

cleanup_tracked_files_match_index() {
  local line meta mode rest oid stage path hash_paths="" expected=""
  cleanup_git diff-index --cached --quiet --no-ext-diff HEAD -- || return 1
  cleanup_git ls-files --stage || return 1
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    case "$line" in *$'\t'*) ;; *) return 1 ;; esac
    meta="${line%%$'\t'*}"
    path="${line#*$'\t'}"
    case "$path" in \"*|/*|../*|*/../*) return 1 ;; esac
    mode="${meta%% *}"; rest="${meta#* }"
    oid="${rest%% *}"; stage="${rest#* }"
    [ "$stage" = 0 ] || return 1
    case "$mode" in
      100644) [ -f "$CLEANUP_GIT_WORK_TREE/$path" ] && [ ! -L "$CLEANUP_GIT_WORK_TREE/$path" ] \
        && [ ! -x "$CLEANUP_GIT_WORK_TREE/$path" ] || return 1 ;;
      100755) [ -f "$CLEANUP_GIT_WORK_TREE/$path" ] && [ ! -L "$CLEANUP_GIT_WORK_TREE/$path" ] \
        && [ -x "$CLEANUP_GIT_WORK_TREE/$path" ] || return 1 ;;
      120000)
        [ -L "$CLEANUP_GIT_WORK_TREE/$path" ] || return 1
        cleanup_symlink_matches_index "$CLEANUP_GIT_WORK_TREE/$path" "$oid" || return 1
        continue
        ;;
      *) return 1 ;;
    esac
    hash_paths="${hash_paths}${hash_paths:+$'\n'}$CLEANUP_GIT_WORK_TREE/$path"
    expected="${expected}${expected:+$'\n'}$oid"
  done < <(printf '%s\n' "$CLEANUP_GIT_OUTPUT")
  [ -n "$hash_paths" ] || return 0
  cleanup_git -C "$CLEANUP_GIT_WORK_TREE" hash-object --no-filters --stdin-paths <<< "$hash_paths" || return 1
  [ "$CLEANUP_GIT_OUTPUT" = "$expected" ]
}

# Every ignored path must be a Python cache (cleanup_cache_path_allowed), and
# no allowed directory may hold a repository-shaped entry.
cleanup_ignored_paths_allowed() {
  local path
  cleanup_git ls-files --others --ignored --exclude-standard || return 1
  while IFS= read -r path; do
    [ -n "$path" ] || continue
    case "$path" in \"*) return 1 ;; esac
    case "/$path/" in *//*|*/./*|*/../*) return 1 ;; esac
    cleanup_cache_path_allowed "$CLEANUP_GIT_WORK_TREE" "$path" || return 1
  done < <(printf '%s\n' "$CLEANUP_GIT_OUTPUT")
  cleanup_cache_markers_absent "$CLEANUP_GIT_WORK_TREE" || return 1
}

# Tracked files equal the index, no untracked file, no nested repository, no
# skip-worktree or assume-unchanged entry, in every MODE. MODE (default cache)
# then chooses the ignored-path rule: cache runs cleanup_ignored_paths_allowed;
# any skips the ignored listing, for a caller that judges ignored paths
# itself.
cleanup_worktree_is_clean() { # [MODE: cache|any]
  local mode="${1:-cache}" line tag
  cleanup_tracked_files_match_index || return 1
  cleanup_nested_git_absent "$CLEANUP_GIT_WORK_TREE" || return 1
  cleanup_git ls-files --others --exclude-standard || return 1
  [ -z "$CLEANUP_GIT_OUTPUT" ] || return 1
  if [ "$mode" != any ]; then
    cleanup_ignored_paths_allowed || return 1
  fi
  cleanup_git ls-files -v || return 1
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    tag="${line:0:1}"
    case "$tag" in [a-z]|S) return 1 ;; esac
  done < <(printf '%s\n' "$CLEANUP_GIT_OUTPUT")
}

cleanup_worktree_head_is_safe() {
  local head line
  cleanup_git for-each-ref --format='%(refname)' refs/worktree refs/bisect || return 1
  [ -z "$CLEANUP_GIT_OUTPUT" ] || return 1
  cleanup_git rev-parse --verify 'HEAD^{commit}' || return 1
  head="$CLEANUP_GIT_OUTPUT"
  case "$head" in ""|*$'\n'*) return 1 ;; esac
  cleanup_git for-each-ref --format='%(objectname)' refs/heads || return 1
  while IFS= read -r line; do
    [ "$line" = "$head" ] && return 0
  done < <(printf '%s\n' "$CLEANUP_GIT_OUTPUT")
  cleanup_git for-each-ref --contains="$head" --format='%(refname)' refs/remotes || return 1
  [ -n "$CLEANUP_GIT_OUTPUT" ]
}

CLEANUP_COMMAND_IS_WHOLE=0

validate_registered_cleanup_worktree_remove() {
  local raw_segment="$1" repo target
  [ "$CLEANUP_COMMAND_IS_WHOLE" -eq 1 ] || return 1
  cleanup_git_raw_shape "$raw_segment" || return 1
  [ "${#WORDS[@]}" -eq 6 ] && [ "${WORDS[0]}" = git ] && [ "${WORDS[1]}" = -C ] \
    && [ "${WORDS[3]}" = worktree ] && [ "${WORDS[4]}" = remove ] || return 1
  repo="$(literal_cleanup_dir "${WORDS[2]}")" || return 1
  target="$(literal_cleanup_dir "${WORDS[5]}")" || return 1
  cleanup_registry_worktree_row "$repo" "$target" 1 || return 1
  [ "$target" != "$repo" ] || return 1
  [ "$target" != "$WORKTREE_ROOT" ] && [ "$target" != "$CWD_CANON" ] || return 1
  strict_descendant "$CWD_CANON" "$target" && return 1
  bind_cleanup_worktree_gitdir "$target" "$repo" || return 1
  # Tracked state, untracked files, nested repositories and HEAD refuse first.
  cleanup_worktree_is_clean any || return 1
  cleanup_worktree_head_is_safe || return 1
  # Ignored Python caches are the only ignored paths allowed. Any other
  # ignored path (build output, .env, a local note) refuses: the removal is
  # then the person's to run by hand.
  cleanup_ignored_paths_allowed
}

validate_registered_lane_remove() {
  local repo_arg target_arg repo target reg_rc
  TEMP_ONLY_REMOVE=0
  case "${#WORDS[@]}" in
    4)
      # The plain form removes from the cwd's repository, which a working
      # folder in no repository does not have.
      [ -n "$WORKTREE_ROOT" ] || policy_deny cwd_outside_worktree
      repo="$WORKTREE_ROOT"; target_arg="${WORDS[3]}"
      ;;
    6)
      [ "${WORDS[1]}" = "-C" ] && [ "${WORDS[3]}" = "worktree" ] && [ "${WORDS[4]}" = "remove" ] || return 1
      repo_arg="${WORDS[2]}"
      case "$repo_arg" in /*) ;; *) return 1 ;; esac
      repo="$(canonical_dir "$repo_arg")" || return 1
      target_arg="${WORDS[5]}"
      ;;
    *) return 1 ;;
  esac
  case "$target_arg" in /*) ;; *) return 1 ;; esac
  case "$target_arg" in *[\*\?\[\]\{\}\$\`\\\~\!\;\&\|\<\>\(\)\#]*) return 1 ;; esac
  target="$(canonical_dir "$target_arg")" || return 1
  [ "$target" != "$repo" ] || return 1
  # Never remove the tree the command runs in, or one that contains it.
  [ "$target" != "$WORKTREE_ROOT" ] && [ "$target" != "$CWD_CANON" ] || return 1
  strict_descendant "$CWD_CANON" "$target" && return 1
  is_linked_worktree_of "$target" "$repo" || return 1
  # Dormant unless armed: with LANE_ROOTS_REGISTRY unset or empty no
  # registry function runs, and the result is 3, "there is no registry".
  if [ -n "${LANE_ROOTS_REGISTRY:-}" ]; then
    path_in_lane_registry "$target" "$repo"
    reg_rc=$?
  else
    reg_rc=3
  fi
  case "$reg_rc" in
    0) return 0 ;;
    1|3) ;;
    4) policy_deny "${LANE_REGISTRY_FAILURE:-unsafe_worktree_remove}" ;;
    *) return 1 ;;
  esac
  path_in_temp_root "$target" || return 1
  TEMP_ONLY_REMOVE=1
}

validate_git_worktree_remove() {
  local raw_segment="${1:-}" cleanup_repo cleanup_target
  # The worktree tier: the programs the cleanup checks run. Only a worktree
  # removal needs them, so an ordinary rm never depends on them.
  check_tools "${WORKTREE_TOOLS[@]}" || policy_deny_dependency
  # The four-word form still needs the cwd's repository. Keep this before the
  # independent cleanup -C branch: a workspace cwd must retain this reason.
  if [ "${#WORDS[@]}" -eq 4 ] && [ -z "$WORKTREE_ROOT" ]; then
    policy_deny cwd_outside_worktree
  fi
  # A cleanup row pre-empts lane/temp-root fallbacks. A failed safety check for
  # a registered target must deny rather than inherit a broader old rule.
  # Dormant unless armed: with CLEANUP_ITEMS_REGISTRY unset or empty no
  # cleanup-registry function runs.
  if [ -n "${CLEANUP_ITEMS_REGISTRY:-}" ] && [ "${#WORDS[@]}" -eq 6 ] && [ "${WORDS[0]:-}" = git ] && [ "${WORDS[1]:-}" = -C ] \
    && [ "${WORDS[3]:-}" = worktree ] && [ "${WORDS[4]:-}" = remove ]; then
    cleanup_repo="$(canonical_dir "${WORDS[2]}")" || cleanup_repo=""
    cleanup_target="$(canonical_dir "${WORDS[5]}")" || cleanup_target=""
    if [ -n "$cleanup_repo" ] && [ -n "$cleanup_target" ] \
      && cleanup_registry_mentions_worktree "$cleanup_repo" "$cleanup_target"; then
      validate_registered_cleanup_worktree_remove "$raw_segment"
      return
    fi
  fi
  if [ "${#WORDS[@]}" -eq 4 ] && [ -n "$AUTHORIZED_ROOT" ] && [ "${WORDS[3]}" = "$AUTHORIZED_ROOT" ]; then
    is_registered_linked_worktree
    return
  fi
  validate_registered_lane_remove && return 0
  [ -n "${CLEANUP_ITEMS_REGISTRY:-}" ] || return 1
  validate_registered_cleanup_worktree_remove "$raw_segment"
}

# The names of git and of the wrappers, in any letter case: on a
# case-insensitive filesystem GIT and ENV run the programs. Each letter is a
# bracket expression, so the git subcommand words beside them in a pattern
# (worktree, remove) stay exact, as git itself reads them.
RE_GIT_NAME='[Gg][Ii][Tt]'
RE_WRAPPER_NAME='([Ee][Nn][Vv]|[Cc][Oo][Mm][Mm][Aa][Nn][Dd]|[Ee][Xx][Ee][Cc]|[Bb][Uu][Ii][Ll][Tt][Ii][Nn]|[Tt][Ii][Mm][Ee]|[Nn][Ii][Cc][Ee]|[Ss][Uu][Dd][Oo])'
# Sets CANON_WRAPPER to the lower-case name of a wrapper name in any case.
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

# True when a raw shell word names git: after stripping one leading backslash
# and one pair of surrounding quotes, its basename is `git` in any letter case
# and any directory part is a plain path (letters, digits, `_.+-/~$`, braces).
# So `\git`, `/bin/git`, `~/bin/git`, `$HOME/bin/git`, `"/usr/bin/git"` and
# `GIT` are git; `gitx`, `/bin/git-foo` and `legit` are not.
is_git_spelling() {
  local t="$1"
  t="${t#\\}"
  case "$t" in
    \"*\"|\'*\') t="${t:1:${#t}-2}" ;;
  esac
  [[ "$t" =~ ^([A-Za-z0-9_./~\$\{\}+-]*/)?$RE_GIT_NAME$ ]]
}

# Sets PREFIX_CANON to the wrapper (env, command, exec, ...) when the raw shell word is that command
# respelled the way git is (one leading backslash and surrounding quotes
# stripped, any plain directory part allowed: `/bin/env`, `./env`, `\command`).
PREFIX_CANON=""
prefix_spelling() {
  local t="$1"
  PREFIX_CANON=""
  t="${t#\\}"
  case "$t" in
    \"*\"|\'*\') t="${t:1:${#t}-2}" ;;
  esac
  [[ "$t" =~ ^([A-Za-z0-9_./~\$\{\}+-]*/)?$RE_WRAPPER_NAME$ ]] || return 1
  canon_wrapper "${BASH_REMATCH[2]}"
  PREFIX_CANON="$CANON_WRAPPER"
}

# Sets GIT_HEAD_REWRITTEN to the segment with the git word (the first word, or
# the first word after env/command/assignment/option prefixes) respelled as plain
# `git`; any other segment is returned unchanged.
GIT_HEAD_REWRITTEN=""
rewrite_git_head() {
  local rest="$1" out="" tok lead first=1 curw="" val vlead
  GIT_HEAD_REWRITTEN="$1"
  while [ -n "$rest" ]; do
    lead="${rest%%[![:space:]]*}"
    out="$out$lead"
    rest="${rest#"$lead"}"
    [ -n "$rest" ] || return 0
    tok="${rest%%[[:space:]]*}"
    rest="${rest#"$tok"}"
    if [ "$first" -eq 1 ]; then
      if ! prefix_spelling "$tok"; then
        case "$tok" in
          [A-Za-z_]*=*) ;;
          *) is_git_spelling "$tok" || return 0 ;;
        esac
      fi
      first=0
    fi
    if is_git_spelling "$tok"; then
      GIT_HEAD_REWRITTEN="${out}git$rest"
      return 0
    fi
    # Only prefix words, assignments and options may precede the git word; any
    # other word (an rm operand named git, say) ends the search untouched.
    if prefix_spelling "$tok"; then
      tok="$PREFIX_CANON"
      curw="$tok"
    else
      case "$tok" in
        -*|[A-Za-z_]*=*) ;;
        *) return 0 ;;
      esac
      # A value-taking option of the wrapper carries the next word with it.
      if [ -n "$curw" ] && wrapper_option_takes_value "$curw" "$tok"; then
        out="$out$tok"
        vlead="${rest%%[![:space:]]*}"
        out="$out$vlead"
        rest="${rest#"$vlead"}"
        val="${rest%%[[:space:]]*}"
        rest="${rest#"$val"}"
        tok="$val"
      fi
    fi
    out="$out$tok"
  done
  return 0
}

# Recognizes a `git worktree remove` that is spelled through a command prefix
# (env, command, VAR=value, an absolute git path) and/or git global options
# (-c k=v, --git-dir, --work-tree, -C PATH, ...). On a match it returns 0 and
# leaves WORDS as a canonical `git [-C PATH] worktree remove ...` command, so
# the ordinary validators (which deny --force and any extra word) apply
# unchanged. NORMALIZE_UNSAFE=1 marks forms whose extra options or prefixes
# could repoint the repository or change the command: those are always denied.
# Returns 1 when WORDS is not a worktree removal. Only reached for segments the
# plain `git worktree remove` and `git -C P worktree remove` shapes did not take.
NORMALIZE_UNSAFE=0
is_git_word() {
  [[ "$1" =~ ^([A-Za-z0-9_./~\$\{\}+-]*/)?$RE_GIT_NAME$ ]]
}
# True for a command word that merely runs the next word: env, command, exec,
# builtin, time, nice or sudo in any letter case, optionally spelled with a
# plain directory part.
is_wrapper_word() {
  [[ "$1" =~ ^([A-Za-z0-9_./~\$\{\}+-]*/)?$RE_WRAPPER_NAME$ ]]
}
# Sets WRAPPER_NAME to the lower-case wrapper name of $1 (see is_wrapper_word).
WRAPPER_NAME=""
wrapper_name() {
  WRAPPER_NAME=""
  [[ "$1" =~ ^([A-Za-z0-9_./~\$\{\}+-]*/)?$RE_WRAPPER_NAME$ ]] || return 1
  canon_wrapper "${BASH_REMATCH[2]}"
  WRAPPER_NAME="$CANON_WRAPPER"
}
# True when OPTION takes a separate value word for the wrapper WRAPPER.
wrapper_option_takes_value() {
  case "$1:$2" in
    env:-u|env:--unset|env:-C|env:--chdir|env:-S|env:--split-string|env:-P) return 0 ;;
    command:-u|command:--unset|command:-C|command:--chdir|command:-S|command:--split-string|command:-P) return 0 ;;
    nice:-n|nice:--adjustment) return 0 ;;
    exec:-a) return 0 ;;
    time:-f|time:-o|time:--format|time:--output) return 0 ;;
    sudo:-u|sudo:-g|sudo:-h|sudo:-p|sudo:-r|sudo:-t|sudo:-U|sudo:-D|sudo:-R|sudo:-T|sudo:-C) return 0 ;;
    sudo:--user|sudo:--group|sudo:--host|sudo:--prompt|sudo:--role|sudo:--type|sudo:--other-user|sudo:--chdir|sudo:--close-from) return 0 ;;
  esac
  return 1
}
normalize_git_worktree_words() {
  local n=${#WORDS[@]} i=0 g w c_count=0 c_path="" unsafe=0 cur=""
  local -a rebuilt
  NORMALIZE_UNSAFE=0
  # The segment must open with a prefix word or with git itself; anything
  # else (echo git worktree remove ...) is not a git invocation.
  w="${WORDS[0]:-}"
  case "$w" in
    [A-Za-z_]*=*) ;;
    *) is_wrapper_word "$w" || is_git_word "$w" || return 1 ;;
  esac
  # Skip prefix words. A plain wrapper (env, command, exec, builtin, time,
  # nice) is benign; assignments and options mark the form unsafe, and so does
  # sudo, which runs git as another user. The first other word must be git.
  while [ "$i" -lt "$n" ]; do
    w="${WORDS[$i]}"
    is_git_word "$w" && break
    if wrapper_name "$w"; then
      cur="$WRAPPER_NAME"
      [ "$cur" != "sudo" ] || unsafe=1
      i=$((i + 1))
      continue
    fi
    case "$w" in
      # Options and assignments after a wrapper are tolerated but never
      # benign. Options that take a value consume it.
      -*)
        unsafe=1
        if wrapper_option_takes_value "$cur" "$w"; then i=$((i + 1)); fi
        ;;
      [A-Za-z_]*=*) unsafe=1 ;;
      # Any other word is the command being run, and it is not git.
      *) return 1 ;;
    esac
    i=$((i + 1))
  done
  [ "$i" -lt "$n" ] || return 1
  g=$i
  i=$((g + 1))
  # Skip git global options.
  while [ "$i" -lt "$n" ]; do
    w="${WORDS[$i]}"
    case "$w" in
      -C)
        c_count=$((c_count + 1))
        [ $((i + 1)) -lt "$n" ] || unsafe=1
        c_path="${WORDS[$((i + 1))]:-}"
        i=$((i + 2))
        ;;
      # -c k=v and --git-dir only carry configuration or a repository hint;
      # they are dropped so the command gets the same verdict as the plain
      # `git worktree remove` it reduces to (--force is still denied).
      -c|--git-dir) i=$((i + 2)) ;;
      --git-dir=*) i=$((i + 1)) ;;
      --work-tree|--namespace|--super-prefix|--config-env|--exec-path)
        unsafe=1
        i=$((i + 2))
        ;;
      -*) unsafe=1; i=$((i + 1)) ;;
      *) break ;;
    esac
  done
  [ "${WORDS[$i]:-}" = "worktree" ] && [ "${WORDS[$((i + 1))]:-}" = "remove" ] || return 1
  [ "$c_count" -le 1 ] || unsafe=1
  NORMALIZE_UNSAFE=$unsafe
  [ "$unsafe" -eq 0 ] || return 0
  rebuilt=(git)
  [ "$c_count" -eq 0 ] || rebuilt+=(-C "$c_path")
  rebuilt+=("${WORDS[@]:$i}")
  WORDS=("${rebuilt[@]}")
  return 0
}

# Pieces shared by the two spelling patterns below: a wrapper word (env,
# command, exec, builtin, time, nice, sudo, any plain directory part), a
# VAR=value assignment, and an option word with at most one value word.
RE_WRAPPER='([A-Za-z0-9_./~${}+-]*/)?'"$RE_WRAPPER_NAME"
RE_ASSIGN='[A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*'
RE_OPTION='-[^[:space:]]*[[:space:]]+([^-[:space:]][^[:space:]]*[[:space:]]+)?'
RE_PREFIXES="(($RE_WRAPPER|$RE_ASSIGN)[[:space:]]+($RE_OPTION|($RE_WRAPPER|$RE_ASSIGN)[[:space:]]+)*)?"
RE_GIT_WORD='([A-Za-z0-9_./~${}+-]*/)?'"$RE_GIT_NAME"

# Any spelling of a worktree removal, including ones that do not parse as a
# simple command (globs, substitutions): never left without a decision.
GIT_WORKTREE_REMOVE_RE="^${RE_PREFIXES}${RE_GIT_WORD}([[:space:]]+-[^[:space:]]+([[:space:]]+[^-[:space:]][^[:space:]]*)?)*[[:space:]]+worktree[[:space:]]+remove([[:space:]]|\$)"

# A `git worktree remove` recognisable once quoting and backslashes are
# removed from the segment: $'..' / $".." openers, quotes and backslashes are
# deleted, then the result must open with optional wrapper/assignment prefixes
# and a git word (respelled as by is_git_spelling) and carry `worktree remove`.
# GIT_FORCED_REMOVE_STRIPPED_RE also requires a force flag (--force, -f, or
# short flags containing f). Only consulted for segments that did not parse as
# a simple command.
RE_STRIPPED_HEAD="^${RE_PREFIXES}${RE_GIT_WORD}([[:space:]]+(-[Cc]|--git-dir|--work-tree|--namespace|--super-prefix|--config-env|--exec-path)[[:space:]]+[^[:space:]]+|[[:space:]]+-[^[:space:]]*)*[[:space:]]+worktree[[:space:]]+remove"
GIT_REMOVE_STRIPPED_RE="${RE_STRIPPED_HEAD}([[:space:]]|\$)"
GIT_FORCED_REMOVE_STRIPPED_RE="${RE_STRIPPED_HEAD}([[:space:]]+[^[:space:]]+)*[[:space:]]+(--force|-[A-Za-z]*f[A-Za-z]*)([[:space:]]|\$)"
# Quote-aware word splitter for the fallbacks below. It keeps every shell word
# whole (single quotes, double quotes with nested $(...) and backticks, bare
# $(...) with balanced parentheses, backticks, ${...}, $'..', backslash
# escapes), then dequotes each word; substitutions stay in the word as opaque
# text. It never executes or expands anything. SPLIT_WORDS holds the dequoted
# words and SPLIT_RAW the matching source text. Returns 1 when the text cannot
# be tokenized (unbalanced quote, parenthesis or brace).
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

# Anchored check on already split and dequoted words (SPLIT_WORDS): optional
# env/command/assignment/option prefixes (env's value-taking options consume
# exactly one word), a git word, git global options (value-taking ones consume
# exactly one word), then `worktree` as the first non-option word, `remove`,
# and a force flag among the remaining words.
GIT_WORD_RE='^([A-Za-z0-9_./~${}+-]*/)?'"$RE_GIT_NAME"'$'
FORCE_FLAG_RE='^(--force|-[A-Za-z]*f[A-Za-z]*)$'
# With $1 = any the force flag is not required: a removal of any kind counts.
words_are_forced_remove() {
  local n=${#SPLIT_WORDS[@]} i=0 w cur=""
  while [ "$i" -lt "$n" ]; do
    w="${SPLIT_WORDS[$i]}"
    if wrapper_name "$w"; then
      cur="$WRAPPER_NAME"
    elif [[ "$w" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
      :
    elif [[ "$w" == -* ]]; then
      if [ -n "$cur" ] && wrapper_option_takes_value "$cur" "$w"; then i=$((i + 1)); fi
    else
      break
    fi
    i=$((i + 1))
  done
  [ "$i" -lt "$n" ] && { [[ "${SPLIT_WORDS[$i]}" =~ $GIT_WORD_RE ]] || [ "${SPLIT_OBSCURED[$i]:-0}" -eq 1 ]; } || return 1
  i=$((i + 1))
  while [ "$i" -lt "$n" ]; do
    case "${SPLIT_WORDS[$i]}" in
      -C|-c|--git-dir|--work-tree|--namespace|--super-prefix|--config-env|--exec-path) i=$((i + 2)) ;;
      -*) i=$((i + 1)) ;;
      *) break ;;
    esac
  done
  { [ "${SPLIT_WORDS[$i]:-}" = "worktree" ] || [ "${SPLIT_OBSCURED[$i]:-0}" -eq 1 ]; } \
    && { [ "${SPLIT_WORDS[$((i + 1))]:-}" = "remove" ] || [ "${SPLIT_OBSCURED[$((i + 1))]:-0}" -eq 1 ]; } || return 1
  i=$((i + 2))
  [ "${1:-}" != "any" ] || return 0
  while [ "$i" -lt "$n" ]; do
    [[ "${SPLIT_WORDS[$i]}" =~ $FORCE_FLAG_RE ]] && return 0
    i=$((i + 1))
  done
  return 1
}

# Fallback for segments whose words cannot be tokenized: strip quotes and
# backslashes, split on whitespace (an escaped space stays joined) and match
# the stripped-words pattern.
is_forced_remove_stripped_text() { # TEXT [any]
  local t="$1"
  t="${t//\\ /$'\001'}"
  t="${t//\$\'/\'}"
  t="${t//\$\"/\"}"
  t="${t//\'/}"
  t="${t//\"/}"
  t="${t//\\/}"
  if [ "${2:-}" = "any" ]; then
    [[ "$t" =~ $GIT_REMOVE_STRIPPED_RE ]]
  else
    [[ "$t" =~ $GIT_FORCED_REMOVE_STRIPPED_RE ]]
  fi
}
is_forced_remove_when_stripped() { # TEXT [any]
  if split_shell_words "$1"; then
    words_are_forced_remove "${2:-}"
  else
    is_forced_remove_stripped_text "$1" "${2:-}"
  fi
}

READ_WORD_VALUE=""
READ_WORD_LITERAL=0
READ_WORD_NEXT_INDEX=0
# Reads one simple-command word starting at byte index $2 of $1, using the
# same literal-word grammar as parse_simple_command: single/double quoting
# is honored, and any unquoted metacharacter (including a second
# redirection) both ends the word and marks it non-literal. Unlike
# parse_simple_command this never aborts the caller's scan: it always
# returns the index one past what it consumed, so scan_redirect_targets can
# keep walking the rest of the segment for further redirections.
read_redirect_word() {
  local input="$1" i="$2" length ch word="" state="plain" literal=1 backslash="\\"
  length=${#input}
  while [ "$i" -lt "$length" ]; do
    ch="${input:$i:1}"
    case "$state" in
      single)
        if [ "$ch" = "'" ]; then state="plain"; else word="$word$ch"; fi
        ;;
      double)
        case "$ch" in
          '"') state="plain" ;;
          '$'|'`'|"$backslash") literal=0; word="$word$ch" ;;
          *) word="$word$ch" ;;
        esac
        ;;
      plain)
        case "$ch" in
          ' '|$'\t') break ;;
          "'") state="single" ;;
          '"') state="double" ;;
          '$'|'`'|"$backslash"|'~'|'*'|'?'|'['|']'|'{'|'}'|'('|')'|'<'|'>'|'|'|'&'|';'|'!'|'#'|$'\n'|$'\r')
            literal=0
            word="$word$ch"
            ;;
          *) word="$word$ch" ;;
        esac
        ;;
    esac
    i=$((i + 1))
  done
  [ "$state" = "plain" ] || literal=0
  READ_WORD_VALUE="$word"
  READ_WORD_LITERAL="$literal"
  READ_WORD_NEXT_INDEX="$i"
}

REDIRECT_TARGETS=()
# A `>`, `>>`, or `>|` operator opens (and for `>`/`>|`, truncates) its
# target before the command it decorates ever runs, following any symlink
# in the target exactly as redacted_log() above declines to risk for its
# own writes; `>>` follows a symlink to its target the same way, so all
# three are collected identically here and left for the caller to decide
# on. `>&N` and `>&-` duplicate or close a descriptor and name no path;
# `>&WORD` with any other WORD names a file and is read like `>WORD`. `&>target` and
# `&>>target` name a path: split_command_segments() keeps them inside the
# segment, and the `>` after the `&` is read exactly like a plain `>`, so
# their target is classified the same way. Any operand this cannot read as a plain
# quoted-or-bare literal (an unquoted substitution, variable, or glob) is
# skipped: this pass only ever adds a new denial, so an operand it cannot
# read literally is left exactly as unclassified as it was before this
# pass existed, never turned into a denial by default.
scan_redirect_targets() {
  local input="$1" length i=0 j ch next state="plain"
  REDIRECT_TARGETS=()
  length=${#input}
  while [ "$i" -lt "$length" ]; do
    ch="${input:$i:1}"
    next="${input:$((i + 1)):1}"
    case "$state" in
      single)
        [ "$ch" != "'" ] || state="plain"
        ;;
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
          '>')
            j=$((i + 1))
            case "$next" in
              '>'|'|') j=$((j + 1)) ;;
            esac
            if [ "${input:$j:1}" = '&' ]; then
              j=$((j + 1))
              while [ "${input:$j:1}" = ' ' ] || [ "${input:$j:1}" = $'\t' ]; do j=$((j + 1)); done
              read_redirect_word "$input" "$j"
              j="$READ_WORD_NEXT_INDEX"
              case "$READ_WORD_VALUE" in
                ''|-) ;;
                *[!0-9]*)
                  [ "$READ_WORD_LITERAL" -ne 1 ] || REDIRECT_TARGETS[${#REDIRECT_TARGETS[@]}]="$READ_WORD_VALUE"
                  ;;
              esac
            else
              while [ "${input:$j:1}" = ' ' ] || [ "${input:$j:1}" = $'\t' ]; do j=$((j + 1)); done
              read_redirect_word "$input" "$j"
              j="$READ_WORD_NEXT_INDEX"
              if [ "$READ_WORD_LITERAL" -eq 1 ] && [ -n "$READ_WORD_VALUE" ]; then
                REDIRECT_TARGETS[${#REDIRECT_TARGETS[@]}]="$READ_WORD_VALUE"
              fi
            fi
            i="$j"
            continue
            ;;
        esac
        ;;
    esac
    i=$((i + 1))
  done
}

# Every judged rm and worktree removal reaches this first.
#
# Inherited git repository selectors deny: the command runs with the same
# environment, so a selector changes which repository the removal acts on,
# and the guard would judge another one. Inherited git configuration
# (GIT_CONFIG_PARAMETERS, GIT_CONFIG_COUNT with its GIT_CONFIG_KEY_<n> and
# GIT_CONFIG_VALUE_<n> pairs, GIT_CONFIG, GIT_CONFIG_GLOBAL, GIT_CONFIG_SYSTEM)
# is cleared from this process, so every git the guard starts runs without
# it; it does not choose the repository, and a sandboxed session sets
# GIT_CONFIG_PARAMETERS on every command. Both steps are builtins.
#
# Then the context tier (git, /usr/bin/env), and, only when a registry
# variable is set, the armed tier and every armed registry's whole-file check,
# all before any rule is applied.
initialize_context() {
  local users_relative home_root tmp_root name stat_ok=0 candidate
  [ "${CONTEXT_READY:-0}" -eq 0 ] || return 0
  if [ -n "${GIT_DIR+set}" ] || [ -n "${GIT_WORK_TREE+set}" ] || [ -n "${GIT_COMMON_DIR+set}" ] \
    || [ -n "${GIT_INDEX_FILE+set}" ] || [ -n "${GIT_OBJECT_DIRECTORY+set}" ] \
    || [ -n "${GIT_ALTERNATE_OBJECT_DIRECTORIES+set}" ] || [ -n "${GIT_NAMESPACE+set}" ] \
    || [ -n "${GIT_CEILING_DIRECTORIES+set}" ] || [ -n "${GIT_DISCOVERY_ACROSS_FILESYSTEM+set}" ]; then
    policy_deny inherited_git_environment
  fi
  for name in ${!GIT_CONFIG_@}; do
    case "$name" in GIT_CONFIG_KEY_*|GIT_CONFIG_VALUE_*) unset "$name" ;; esac
  done
  unset GIT_CONFIG_PARAMETERS GIT_CONFIG_COUNT GIT_CONFIG GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM
  check_tools "${CONTEXT_TOOLS[@]}" || policy_deny_dependency
  if [ -n "${LANE_ROOTS_REGISTRY:-}" ] || [ -n "${CLEANUP_ITEMS_REGISTRY:-}" ]; then
    for candidate in "${STAT_CANDIDATES[@]}"; do
      if [ -x "$candidate" ] && [ ! -d "$candidate" ]; then stat_ok=1; break; fi
    done
    [ "$stat_ok" -eq 1 ] || { MISSING_TOOL_CODE=stat; policy_deny_dependency; }
    check_tools "${ARMED_TOOLS[@]}" || policy_deny_dependency
    if [ -n "${LANE_ROOTS_REGISTRY:-}" ]; then
      validate_lane_registry_file || policy_deny "${LANE_REGISTRY_FAILURE:-unsafe_worktree_remove}"
    fi
    if [ -n "${CLEANUP_ITEMS_REGISTRY:-}" ]; then
      validate_cleanup_registry_file || policy_deny "${CLEANUP_REGISTRY_FAILURE:-unsafe_worktree_remove}"
    fi
  fi
  case "$cwd_raw" in /*) ;; *) policy_deny invalid_cwd ;; esac
  CWD_CANON="$(cd -- "$cwd_raw" 2>/dev/null && pwd -P)" || policy_deny invalid_cwd
  [ "$cwd_raw" = "$CWD_CANON" ] || policy_deny invalid_cwd
  if WORKTREE_ROOT="$(git -C "$CWD_CANON" rev-parse --show-toplevel 2>/dev/null)"; then
    WORKTREE_ROOT="$(cd -- "$WORKTREE_ROOT" 2>/dev/null && pwd -P)" || policy_deny invalid_cwd
    { [ "$CWD_CANON" = "$WORKTREE_ROOT" ] || strict_descendant "$CWD_CANON" "$WORKTREE_ROOT"; } || policy_deny invalid_cwd
  else
    # A working folder in no git repository at all (a workspace folder) is a
    # valid context with no worktree: WORKTREE_ROOT stays empty, and every
    # rule that needs it refuses as cwd_outside_worktree. A folder git does
    # not call a worktree but that sits in or under a repository (its .git
    # directory, say) is still a configuration fault.
    WORKTREE_ROOT=""
    # An inherited GIT_DIR or GIT_WORK_TREE points git somewhere other than
    # this folder, so "no repository here" cannot be trusted.
    [ -z "${GIT_DIR+set}" ] && [ -z "${GIT_WORK_TREE+set}" ] || policy_deny invalid_cwd
    case "$CWD_CANON/" in */.[gG][iI][tT]/*) policy_deny invalid_cwd ;; esac
    path_in_git_repo "$CWD_CANON" && policy_deny invalid_cwd
  fi

  SCRATCH_ROOT=""
  if [ -n "$SCRATCHPAD" ]; then
    case "$SCRATCHPAD" in /*) ;; *) policy_deny invalid_scratchpad ;; esac
    SCRATCH_ROOT="$(cd -- "$SCRATCHPAD" 2>/dev/null && pwd -P)" || policy_deny invalid_scratchpad
    [ "$SCRATCHPAD" = "$SCRATCH_ROOT" ] && [ ! -L "$SCRATCHPAD" ] || policy_deny invalid_scratchpad
    case "$SCRATCH_ROOT" in /|/tmp|/private/tmp|"$USERS_ROOT"|"$USERS_ROOT"/*) policy_deny invalid_scratchpad ;; esac
    # Strictly below the physical temp root: /private/tmp on macOS, /tmp on
    # Linux.
    tmp_root="$(cd /tmp 2>/dev/null && pwd -P)" || policy_deny invalid_scratchpad
    if ! strict_descendant "$SCRATCH_ROOT" "$tmp_root"; then
      policy_deny invalid_scratchpad
    fi
  fi

  AUTHORIZED_ROOT=""
  if [ -n "$AUTHORIZED_WORKTREE" ]; then
    case "$AUTHORIZED_WORKTREE" in /*) ;; *) policy_deny invalid_authorized_worktree ;; esac
    AUTHORIZED_ROOT="$(cd -- "$AUTHORIZED_WORKTREE" 2>/dev/null && pwd -P)" || policy_deny invalid_authorized_worktree
    [ "$AUTHORIZED_WORKTREE" = "$AUTHORIZED_ROOT" ] || policy_deny invalid_authorized_worktree
    has_symlink_component "$AUTHORIZED_WORKTREE" && policy_deny invalid_authorized_worktree
    case "$AUTHORIZED_ROOT" in
      /|"$USERS_ROOT") policy_deny invalid_authorized_worktree ;;
      "$USERS_ROOT"/*)
        users_relative="${AUTHORIZED_ROOT#"$USERS_ROOT"/}"
        case "$users_relative" in */*) ;; *) policy_deny invalid_authorized_worktree ;; esac
        ;;
    esac
    home_root=""
    case "${HOME:-}" in
      /*) home_root="$(cd -- "$HOME" 2>/dev/null && pwd -P)" || home_root="$(normalize_absolute "$HOME")" ;;
    esac
    [ -z "$home_root" ] || [ "$AUTHORIZED_ROOT" != "$home_root" ] || policy_deny invalid_authorized_worktree
  fi
  CONTEXT_READY=1
}

# Judges one segment as a possible `git worktree remove`. Returns 0 when it is
# an explicit removal that was validated (a deny exits here), 1 when the
# segment is not a worktree removal.
judge_worktree_segment() {
  local segment="$1" raw_segment="${2:-$1}" detect
  TEMP_ONLY_REMOVE=0
  # The two patterns below find the git word on the segment with its
  # redirections removed; the validators parse the untouched segment, so a
  # removal that carries a redirection is denied by the patterns.
  strip_command_redirections "$segment"
  rewrite_git_head "$REDIR_STRIPPED"
  detect="$GIT_HEAD_REWRITTEN"
  if parse_simple_command "$segment" \
    && [[ "${WORDS[0]}" =~ ^$RE_GIT_NAME$ ]] \
    && [ "${WORDS[1]:-}" = "worktree" ] \
    && [ "${WORDS[2]:-}" = "remove" ]; then
    initialize_context
    validate_git_worktree_remove "$raw_segment" || policy_deny unsafe_worktree_remove
    return 0
  elif parse_simple_command "$segment" \
    && [[ "${WORDS[0]}" =~ ^$RE_GIT_NAME$ ]] \
    && [ "${WORDS[1]:-}" = "-C" ] \
    && [ "${WORDS[3]:-}" = "worktree" ] \
    && [ "${WORDS[4]:-}" = "remove" ]; then
    initialize_context
    validate_git_worktree_remove "$raw_segment" || policy_deny unsafe_worktree_remove
    return 0
  elif parse_simple_command "$segment" && normalize_git_worktree_words; then
    # Prefixes and Git global options remain valid only for the existing
    # authorizations. The cleanup path checks the untouched raw segment and
    # therefore refuses every spelling except the exact six-word form.
    [ "$NORMALIZE_UNSAFE" -eq 0 ] || policy_deny unsafe_worktree_remove
    initialize_context
    validate_git_worktree_remove "$raw_segment" || policy_deny unsafe_worktree_remove
    return 0
  elif [[ "$detect" =~ $GIT_WORKTREE_REMOVE_RE ]]; then
    # A worktree removal that cannot be parsed as a simple command (globs,
    # substitutions, repeated -C) is never left without a decision.
    policy_deny unsafe_worktree_remove
  elif ! parse_simple_command "$segment" && is_forced_remove_when_stripped "$detect" any; then
    # Quoting or escapes hide the words, but with them removed this is a
    # worktree removal that no validator could read: deny rather than leave it
    # without a decision.
    policy_deny unsafe_worktree_remove
  fi
  return 1
}

# ---------------------------------------------------------------------------
# The removal word and the carriers beside it.
#
# A command is judged only when its text holds a removal word: rm, or the
# words worktree and remove together (removal_word_in_text). A command with no
# removal word gets no opinion beyond the redirect pass. A removal word beside
# any code-carrying construct (find_carrier) denies as removal_beside_<class>
# without the guard reading inside that construct: substitutions, heredocs,
# eval, a shell and the rest are where every earlier bypass came from. A
# removal word beside a ; & | or newline the segmenter did not split on
# denies as hidden_separator (rule G, run_policy). A
# removal word with no carrier is judged as plain top-level commands. All of
# this reads the command as text; it never runs, sources or expands it, and it
# forks nothing.
# ---------------------------------------------------------------------------

# True when A equals B in any letter case.
nocase_equals() { # A B
  local rc=1
  shopt -s nocasematch
  [[ "$1" == "$2" ]] && rc=0
  shopt -u nocasematch
  return "$rc"
}

# True when WORD's last path component names a shell, or a shell runner that
# hands its arguments to sh -c, in any letter case.
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

# R1's line join. A backslash and the newline after it are a line
# continuation, and are deleted, only when the run of backslashes that ends at
# that newline has odd length. In an even run every backslash is escaped (a\\
# then a newline is a\ and a newline), so the newline stays and still ends the
# command.
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

# The removal-word test's normal form: line continuations (r1_join_continuations),
# the $ of a $'...' or $"..." opener, and every quote and backslash are deleted.
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

# A line continuation on a line that holds a word-initial # may sit inside a
# comment, where bash does not join the lines (in a heredoc body or a quoted
# string handed to another shell as well as at the top level). R1 reads no
# quotes, so it cannot tell: R1_SPLIT_TEXT is TEXT with each continuation on
# such a line read as a plain newline, and R1 and R2 read it beside the raw
# text, where every continuation is joined.
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

# Inside each ${...} span (brace matched), the default-value operators :- -
# := :+ + :? ? also split, so ${RM:-rm} yields the token rm.
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

# The tokens: maximal runs between the delimiters space, tab, newline, ; & |
# ( ) < > ` = { } and comma. For a token that begins with a single - and holds
# S, the text after its first S is a token too (env -S'rm -rf X' normalizes to
# -Srm).
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

# The whitespace-delimited chunks of the normal form.
R1_CHUNKS=()
r1_chunks() { # NORMALIZED
  local IFS=$' \t\n'
  set -f
  R1_CHUNKS=($1)
  set +f
}

# True when TEXT holds a glob or extglob metacharacter: *, ?, [, @(, !(, +(,
# or zsh's bare group ( ... | ... ).
r1_has_pattern_meta() { # TEXT
  case "$1" in
    *[\*\?\[]*|*'@('*|*'!('*|*'+('*|*'('*'|'*')'*) return 0 ;;
  esac
  return 1
}

# True when PATTERN spells rm to bash's own matcher, with extglob and
# nocasematch set. A ( that no extglob character precedes is first rewritten
# to @(, so zsh's r(m|x) is tested as r@(m|x).
r1_pattern_spells_rm() { # PATTERN
  local rest="$1" pattern="" pre rc=1
  while :; do
    case "$rest" in *'('*) ;; *) break ;; esac
    pre="${rest%%(*}"
    rest="${rest#*(}"
    case "$pre" in
      *[@!+*?]) pattern="$pattern$pre(" ;;
      *) pattern="$pattern$pre@(" ;;
    esac
  done
  pattern="$pattern$rest"
  shopt -s extglob nocasematch
  [[ rm == $pattern ]] && rc=0
  shopt -u extglob nocasematch
  return "$rc"
}

# R1 (a) and (b) on one token: rm or */rm in any letter case, or a glob or
# extglob pattern that spells rm.
r1_token_is_rm() { # TOKEN
  local rc=1
  shopt -s nocasematch
  case "$1" in rm|*/rm) rc=0 ;; esac
  shopt -u nocasematch
  [ "$rc" -eq 0 ] && return 0
  r1_has_pattern_meta "$1" && r1_pattern_spells_rm "$1"
}

# R1 (c): the chunk holds a brace expansion (a { not directly after $, with ,
# or .. before its matching }) and both an r and an m, in any letter case.
r1_chunk_brace_spells_rm() { # CHUNK
  local c="$1" n=${#1} i=0 j depth ch prev="" inner
  case "$c" in *[rR]*) ;; *) return 1 ;; esac
  case "$c" in *[mM]*) ;; *) return 1 ;; esac
  case "$c" in *'{'*'}'*) ;; *) return 1 ;; esac
  while [ "$i" -lt "$n" ]; do
    ch="${c:i:1}"
    if [ "$ch" = '{' ] && [ "$prev" != '$' ]; then
      depth=1
      j=$((i + 1))
      while [ "$j" -lt "$n" ]; do
        case "${c:j:1}" in
          '{') depth=$((depth + 1)) ;;
          '}') depth=$((depth - 1)); [ "$depth" -gt 0 ] || break ;;
        esac
        j=$((j + 1))
      done
      if [ "$j" -lt "$n" ]; then
        inner="${c:i+1:j-i-1}"
        case "$inner" in *,*|*..*) return 0 ;; esac
      fi
    fi
    prev="$ch"
    i=$((i + 1))
  done
  return 1
}

# R1 (d): a $'...' span of the raw text holds an escape that can spell a
# letter: \x, \u, \U, \c, or a backslash before a digit. Every $' opens a
# span of its own, so a $' that is only text (in a comment, say) cannot pair
# with the quote of a real one and hide it.
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

# The words of TEXT for the env split option: split at unquoted whitespace and
# at unquoted ; & | ( ), quotes kept in the word. An unquoted separator is the
# empty word. An & or | that belongs to a redirection operator (>&, <&, &>,
# >|) is part of its word, not a separator.
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

# A redirection word, operator and target, or the operator alone.
REDIRECTION_WORD_RE='^([0-9]+|\{[A-Za-z_][A-Za-z0-9_]*\})?(<<<|<<-|&>>|<<|<>|<&|>>|>\||>&|&>|<|>)'
REDIRECTION_OPERATOR_RE='^([0-9]+|\{[A-Za-z_][A-Za-z0-9_]*\})?(<<<|<<-|&>>|<<|<>|<&|>>|>\||>&|&>|<|>)$'

# R1 (h) and the env_split carrier: true when an env split option stands in
# an env word's own option list. The env word is any word whose last path
# component is env (quotes ignored, any letter case), wherever it stands. Its
# option list holds the words after it that are options, values of -u, -C,
# -P, --unset and --chdir, NAME=value assignments or redirections; env's
# command word or an unquoted separator ends it. The split option is a
# single-dash option holding S, or a long option beginning --s.
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

# R1 (g) chunks and command positions. Chunks come from the raw text with only
# line continuations joined; the text splits at whitespace, quoted or not,
# except inside an open pattern group: a [ that is not the whole word [ or [[;
# an extglob group @( !( +( *( ?(; a zsh group, a ( glued to a word character
# whose top-level content holds |; a brace group, a { not directly after $ and
# not followed by whitespace whose matching } encloses a top-level , or .. (or
# that never closes). $(, <(, >(, ${ and a ( that begins a word are not
# pattern groups.
#
# A chunk is in command position when it is the first chunk; follows a chunk
# that ends with ; & | ( { a backtick or $(, or a newline; follows a control
# word (if then do else elif while until !); follows a single-dash option
# ending in c that follows a shell name or shell runner (with its options and
# their values between); follows eval, -exec, -execdir, -ok or -okdir; or is
# the first chunk that is not an option after xargs or after a prefix the
# guard skips (any case, any directory part).
#
# R1G_PRESUMED is 1 when a chunk in command position that holds an r and an m
# (quotes removed, any case) has a [, extglob or zsh group holding a quote or
# whitespace, a brace group holding a quote or whitespace, or an opener that
# never closes.
R1G_CHUNKS=()
R1G_CP=()
R1G_PRESUMED=0
# Sets R1G_LOOKAHEAD for the ( at INDEX: 1 when its top-level content holds |.
R1G_LOOKAHEAD=0
r1g_zsh_group_at() { # TEXT INDEX
  local s="$1" i=$(($2 + 1)) n=${#1} depth=1 c
  R1G_LOOKAHEAD=0
  while [ "$i" -lt "$n" ]; do
    c="${s:i:1}"
    case "$c" in
      '(') depth=$((depth + 1)) ;;
      ')') depth=$((depth - 1)); [ "$depth" -gt 0 ] || return 0 ;;
      '|') [ "$depth" -ne 1 ] || R1G_LOOKAHEAD=1 ;;
    esac
    i=$((i + 1))
  done
  return 0
}
# Sets R1G_LOOKAHEAD for the { at INDEX: 1 when its matching } encloses a
# top-level , or .., 2 when it never closes, 0 otherwise.
r1g_brace_group_at() { # TEXT INDEX
  local s="$1" i=$(($2 + 1)) n=${#1} depth=1 c hit=0
  R1G_LOOKAHEAD=2
  while [ "$i" -lt "$n" ]; do
    c="${s:i:1}"
    case "$c" in
      '{') depth=$((depth + 1)) ;;
      '}')
        depth=$((depth - 1))
        if [ "$depth" -eq 0 ]; then R1G_LOOKAHEAD="$hit"; return 0; fi
        ;;
      ',') [ "$depth" -ne 1 ] || hit=1 ;;
      '.') [ "$depth" -ne 1 ] || [ "${s:i+1:1}" != '.' ] || hit=1 ;;
    esac
    i=$((i + 1))
  done
  return 0
}

# The state carried from one chunk to the next.
R1G_BASE_CP=1
R1G_PENDING=""
R1G_SHELL=0
R1G_SHELL_VALUE=0
# Judges and records one finished chunk, then sets the state for the next.
# HIT is 1 when a [, extglob or zsh group in the chunk held a quote or
# whitespace; HIT_BRACE when a brace group did; OPEN_STACK holds the groups
# that never closed (b for [, p for extglob or zsh, c for brace).
r1g_finish_chunk() { # RAW HIT HIT_BRACE OPEN_STACK NEWLINE_AFTER
  local raw="$1" hit="$2" hit_brace="$3" open="$4" bare cp=0 next_cp=0 last name option=0 r_and_m=0
  bare="${raw//\'/}"
  bare="${bare//\"/}"
  case "$bare" in -*) option=1 ;; esac
  [ "$R1G_BASE_CP" -eq 0 ] || cp=1
  if [ -n "$R1G_PENDING" ] && [ "$option" -eq 0 ]; then cp=1; fi
  case "$open" in *[bp]*) hit=1 ;; esac
  case "$open" in *c*) hit_brace=1 ;; esac
  case "$bare" in *[rR]*) case "$bare" in *[mM]*) r_and_m=1 ;; esac ;; esac
  if [ "$cp" -eq 1 ]; then
    [ "$hit" -eq 0 ] || [ "$r_and_m" -eq 0 ] || R1G_PRESUMED=1
    [ "$hit_brace" -eq 0 ] || [ "$r_and_m" -eq 0 ] || R1G_PRESUMED=1
  fi
  R1G_CHUNKS+=("$raw")
  R1G_CP+=("$cp")
  # The next chunk's command position.
  last="${raw:${#raw}-1:1}"
  case "$last" in ';'|'&'|'|'|'('|'{'|'`') next_cp=1 ;; esac
  [ "$5" -eq 0 ] || next_cp=1
  case "$bare" in if|then|do|else|elif|while|until|'!'|-exec|-execdir|-ok|-okdir) next_cp=1 ;; esac
  nocase_equals "$bare" eval && next_cp=1
  name="${bare##*/}"
  # xargs and the prefixes: the first chunk after them that is not an option.
  if [ -n "$R1G_PENDING" ] && [ "$option" -eq 1 ]; then
    :
  else
    R1G_PENDING=""
    if nocase_equals "$name" xargs; then
      R1G_PENDING=xargs
    elif prefix_name "$bare"; then
      R1G_PENDING=prefix
    fi
  fi
  # A shell or shell runner, its options and their values, then -c.
  if is_shell_name "$bare"; then
    R1G_SHELL=1
    R1G_SHELL_VALUE=0
  elif [ "$R1G_SHELL" -eq 1 ]; then
    if [ "$R1G_SHELL_VALUE" -eq 1 ]; then
      R1G_SHELL_VALUE=0
    else
      case "$bare" in
        -O|+O|-o|+o|--rcfile|--init-file) R1G_SHELL_VALUE=1 ;;
        -*c) case "$bare" in --*) ;; *) next_cp=1 ;; esac ;;
        -*|+*) ;;
        *) R1G_SHELL=0 ;;
      esac
    fi
  fi
  if [ "$next_cp" -eq 1 ]; then
    case "$last" in ';'|'&'|'|') R1G_PENDING=""; R1G_SHELL=0 ;; esac
    [ "$5" -eq 0 ] || { R1G_PENDING=""; R1G_SHELL=0; }
  fi
  R1G_BASE_CP="$next_cp"
}
r1_scan_chunks() { # TEXT
  local s nl=$'\n' n i=0 c prev="" chunk="" stack="" flags="" hit=0 hit_brace=0 in_chunk=0 newline=0
  local word top flag k
  r1_join_continuations "$1"
  s="$R1_JOINED"
  n=${#s}
  R1G_CHUNKS=()
  R1G_CP=()
  R1G_PRESUMED=0
  R1G_BASE_CP=1
  R1G_PENDING=""
  R1G_SHELL=0
  R1G_SHELL_VALUE=0
  while [ "$i" -lt "$n" ]; do
    c="${s:i:1}"
    if [ -z "$stack" ]; then
      case "$c" in
        ' '|$'\t'|$'\n')
          if [ "$c" = "$nl" ]; then newline=1; fi
          if [ "$in_chunk" -eq 1 ]; then
            # The newline that ends this chunk counts once the whitespace run
            # is read, so look ahead through it.
            k="$i"
            while [ "$k" -lt "$n" ]; do
              case "${s:k:1}" in ' '|$'\t') ;; "$nl") newline=1 ;; *) break ;; esac
              k=$((k + 1))
            done
            r1g_finish_chunk "$chunk" "$hit" "$hit_brace" "" "$newline"
            in_chunk=0
            chunk=""
            hit=0
            hit_brace=0
            i="$k"
            newline=0
            prev=" "
            continue
          fi
          prev="$c"
          i=$((i + 1))
          continue
          ;;
      esac
    fi
    if [ "$in_chunk" -eq 0 ]; then
      in_chunk=1
      # A newline before the first chunk of a run also starts a command.
      [ "$newline" -eq 0 ] || R1G_BASE_CP=1
      newline=0
    fi
    if [ -n "$stack" ]; then
      case "$c" in
        ' '|$'\t'|$'\n'|"'"|'"') flags="${flags//0/1}" ;;
      esac
    fi
    case "$c" in
      '[')
        word="${chunk//\'/}"
        word="${word//\"/}"
        if [ -z "$stack" ] && [ -z "$word" ]; then
          # The whole word [ or [[ (quotes ignored) is not a pattern group.
          k="$i"
          while [ "$k" -lt "$n" ]; do
            case "${s:k:1}" in ' '|$'\t'|$'\n') break ;; esac
            k=$((k + 1))
          done
          word="${s:i:k-i}"
          word="${word//\'/}"
          word="${word//\"/}"
          if [ "$word" = '[' ] || [ "$word" = '[[' ]; then
            chunk="$chunk${s:i:k-i}"
            prev="${s:k-1:1}"
            i="$k"
            continue
          fi
        fi
        stack="${stack}b"
        flags="${flags}0"
        ;;
      ']')
        if [ -n "$stack" ] && [ "${stack:${#stack}-1:1}" = b ]; then
          flag="${flags:${#flags}-1:1}"
          stack="${stack%?}"
          flags="${flags%?}"
          [ "$flag" -eq 0 ] || hit=1
        fi
        ;;
      '(')
        case "$prev" in
          '@'|'!'|'+'|'*'|'?') stack="${stack}p"; flags="${flags}0" ;;
          [A-Za-z0-9_])
            r1g_zsh_group_at "$s" "$i"
            if [ "$R1G_LOOKAHEAD" -eq 1 ]; then
              stack="${stack}p"
              flags="${flags}0"
            elif [ -n "$stack" ]; then
              stack="${stack}q"
              flags="${flags}0"
            fi
            ;;
          *)
            if [ -n "$stack" ]; then stack="${stack}q"; flags="${flags}0"; fi
            ;;
        esac
        ;;
      ')')
        top=""
        [ -z "$stack" ] || top="${stack:${#stack}-1:1}"
        if [ "$top" = p ] || [ "$top" = q ]; then
          flag="${flags:${#flags}-1:1}"
          stack="${stack%?}"
          flags="${flags%?}"
          [ "$top" != p ] || [ "$flag" -eq 0 ] || hit=1
        fi
        ;;
      '{')
        if [ "$prev" != '$' ]; then
          case "${s:i+1:1}" in
            ''|' '|$'\t'|$'\n') [ -z "$stack" ] || { stack="${stack}x"; flags="${flags}0"; } ;;
            *)
              r1g_brace_group_at "$s" "$i"
              if [ "$R1G_LOOKAHEAD" -ne 0 ]; then
                stack="${stack}c"
                flags="${flags}0"
              elif [ -n "$stack" ]; then
                stack="${stack}x"
                flags="${flags}0"
              fi
              ;;
          esac
        elif [ -n "$stack" ]; then
          stack="${stack}x"
          flags="${flags}0"
        fi
        ;;
      '}')
        top=""
        [ -z "$stack" ] || top="${stack:${#stack}-1:1}"
        if [ "$top" = c ] || [ "$top" = x ]; then
          flag="${flags:${#flags}-1:1}"
          stack="${stack%?}"
          flags="${flags%?}"
          [ "$top" != c ] || [ "$flag" -eq 0 ] || hit_brace=1
        fi
        ;;
    esac
    chunk="$chunk$c"
    prev="$c"
    i=$((i + 1))
  done
  [ "$in_chunk" -eq 0 ] || r1g_finish_chunk "$chunk" "$hit" "$hit_brace" "$stack" 0
  return 0
}
# R1 (g): true when a chunk in command position holds r and m and a pattern
# group the guard cannot read. Runs only when the text holds an opener.
r1_unreadable_pattern_group() { # TEXT
  case "$1" in *'['*|*'('*|*'{'*) ;; *) return 1 ;; esac
  r1_scan_chunks "$1"
  [ "$R1G_PRESUMED" -eq 1 ]
}

# The command words of TEXT, for R1 (f), the source carrier's . and the
# variable_command carrier. For each top-level segment of both readings, the
# segmenter's (split_command_segments, where # is text) and the
# comment-skipping copy's (split_segments_skipping_comments), so the copy can
# add command words but never remove them (heredoc bodies are not segments;
# when TEXT holds no # the two give the same segments, and the segmenter is
# not run for it), with its control openers and redirections removed: the
# first raw
# word that is not an assignment, and the raw word at PREFIX_INDEX after the
# known prefixes (only the first when prefix skipping fails). A segment that
# begins with (( is an arithmetic command and has none.
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

# R1 (f): true when a raw command word holds an unquoted glob, extglob or
# brace metacharacter: *, ?, [, a { not directly after $, or a ( that is
# neither the word's first character nor directly after $. The words [ and
# [[ are not patterns.
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

# R1: true when TEXT holds a removal word. R1 is a union of readings and is
# true when any of them finds one. The word tests read the raw text; when it
# holds a backslash, the text with every backslash deleted and every newline
# kept (a reading that needs no lexer and no line join); both top-level
# readings of every command (top_level_text), the segmenter's and the
# comment-skipping copy's; and, when the text holds a #, the
# comment-continuation reading (r1_split_comment_continuations). (f) reads
# the command words of both readings (collect_command_words). The top-level
# readings find a removal word the segmenter reads as a command word even
# where R1's line join reads the raw text wrongly. Rule G (run_policy) is
# gated on R1, so a reading is only ever added here, never taken away.
removal_word_in_text() { # TEXT
  local text="$1" k
  r1_word_tests "$text" && return 0
  case "$text" in
    *"\\"*) r1_word_tests "${text//\\/}" && return 0 ;;
  esac
  case "$text" in
    *'#'*)
      r1_split_comment_continuations "$text"
      if [ "$R1_SPLIT_TEXT" != "$text" ]; then r1_word_tests "$R1_SPLIT_TEXT" && return 0; fi
      ;;
  esac
  top_level_text "$text"
  if [ "$TOP_LEVEL_TEXT" != "$text" ]; then r1_word_tests "$TOP_LEVEL_TEXT" && return 0; fi
  if [ "$TOP_LEVEL_TEXT_G" != "$text" ] && [ "$TOP_LEVEL_TEXT_G" != "$TOP_LEVEL_TEXT" ]; then
    r1_word_tests "$TOP_LEVEL_TEXT_G" && return 0
  fi
  # (f) a command word spelled with a pattern is presumed a removal word.
  collect_command_words "$text"
  k=0
  while [ "$k" -lt "${#CMD_WORDS_RAW[@]}" ]; do
    command_word_is_pattern "${CMD_WORDS_RAW[$k]}" && return 0
    k=$((k + 1))
  done
  return 1
}

# R1 (a) to (e), (g) and (h) on one reading of the text.
r1_word_tests() { # TEXT
  local text="$1" tok chunk last saw_worktree=0 saw_remove=0
  r1_normalize "$text"
  r1_split_parameter_operators "$R1_NORMALIZED"
  r1_tokenize "$R1_OPSPLIT"
  # (a) and (b) on every token, and (e): worktree and remove anywhere.
  for tok in ${R1_TOKENS[@]+"${R1_TOKENS[@]}"}; do
    r1_token_is_rm "$tok" && return 0
    nocase_equals "$tok" worktree && saw_worktree=1
    nocase_equals "$tok" remove && saw_remove=1
  done
  [ "$saw_worktree" -eq 0 ] || [ "$saw_remove" -eq 0 ] || return 0
  # (b) on the last path component of every chunk, and (c).
  r1_chunks "$R1_NORMALIZED"
  for chunk in ${R1_CHUNKS[@]+"${R1_CHUNKS[@]}"}; do
    last="${chunk##*/}"
    if r1_has_pattern_meta "$last" && r1_pattern_spells_rm "$last"; then return 0; fi
    r1_chunk_brace_spells_rm "$chunk" && return 0
  done
  # (d) an escape in $'...' that can spell a letter.
  r1_ansi_c_escape "$text" && return 0
  # (h) an env split option and any backslash: env -S reads \_ \t \n and \c
  # itself, so the normal form cannot be trusted there.
  case "$text" in *"\\"*) env_split_in_option_list "$text" && return 0 ;; esac
  # (g) a pattern group the guard cannot read, in command position.
  r1_unreadable_pattern_group "$text" && return 0
  return 1
}

# R2: sets CARRIER_CLASS to the first code-carrying construct of TEXT, in this
# order, and returns 1 when it has none. Every token match is in any letter
# case. CARRIER_CLASS is assigned only from these literals. When TEXT holds a
# backslash, the tokens also come from the text with every backslash deleted
# and every newline kept. When TEXT holds a
# #, the tokens also come from the comment-continuation reading and both
# top-level readings (the segmenter's and the comment-skipping copy's), and
# the env split is also read in both top-level ones, so a comment cannot
# glue a carrier word to its line or hide it in a quote it opens, and the
# comment-skipping copy cannot hide one by dropping text bash runs. The .
# and variable_command checks read the command words of both readings.
CARRIER_CLASS=""
find_carrier() { # TEXT
  local text="$1" tok k raw top_text="" top_text_g=""
  local -a toks
  local has_eval=0 has_source=0 has_trap=0 has_alias=0 has_xargs=0 has_find_exec=0 has_shell=0
  local has_git=0 has_submodule=0 has_foreach=0 has_rebase=0 has_exec_option=0 has_bisect=0 has_run=0 has_flock=0
  CARRIER_CLASS=""
  case "$text" in *'$('*) CARRIER_CLASS=substitution; return 0 ;; esac
  case "$text" in *'`'*) CARRIER_CLASS=backtick; return 0 ;; esac
  case "$text" in *'<('*|*'>('*) CARRIER_CLASS=process_substitution; return 0 ;; esac
  case "$text" in *'<<'*) CARRIER_CLASS=heredoc; return 0 ;; esac
  r1_normalize "$text"
  r1_split_parameter_operators "$R1_NORMALIZED"
  r1_tokenize "$R1_OPSPLIT"
  toks=(${R1_TOKENS[@]+"${R1_TOKENS[@]}"})
  case "$text" in
    *"\\"*)
      r1_normalize "${text//\\/}"
      r1_split_parameter_operators "$R1_NORMALIZED"
      r1_tokenize "$R1_OPSPLIT"
      toks+=(${R1_TOKENS[@]+"${R1_TOKENS[@]}"})
      ;;
  esac
  case "$text" in
    *'#'*)
      r1_split_comment_continuations "$text"
      r1_normalize "$R1_SPLIT_TEXT"
      r1_split_parameter_operators "$R1_NORMALIZED"
      r1_tokenize "$R1_OPSPLIT"
      toks+=(${R1_TOKENS[@]+"${R1_TOKENS[@]}"})
      top_level_text "$text"
      top_text="$TOP_LEVEL_TEXT"
      top_text_g="$TOP_LEVEL_TEXT_G"
      r1_normalize "$top_text"
      r1_split_parameter_operators "$R1_NORMALIZED"
      r1_tokenize "$R1_OPSPLIT"
      toks+=(${R1_TOKENS[@]+"${R1_TOKENS[@]}"})
      r1_normalize "$top_text_g"
      r1_split_parameter_operators "$R1_NORMALIZED"
      r1_tokenize "$R1_OPSPLIT"
      toks+=(${R1_TOKENS[@]+"${R1_TOKENS[@]}"})
      ;;
  esac
  shopt -s nocasematch
  for tok in ${toks[@]+"${toks[@]}"}; do
    case "$tok" in
      eval|*/eval) has_eval=1 ;;
      source) has_source=1 ;;
      trap) has_trap=1 ;;
      alias) has_alias=1 ;;
      xargs|*/xargs) has_xargs=1 ;;
      -exec|-execdir|-ok|-okdir) has_find_exec=1 ;;
      git|*/git) has_git=1 ;;
      submodule) has_submodule=1 ;;
      foreach) has_foreach=1 ;;
      rebase) has_rebase=1 ;;
      bisect) has_bisect=1 ;;
      run) has_run=1 ;;
      flock|*/flock) has_flock=1 ;;
    esac
    case "$tok" in
      -x|--exec) has_exec_option=1 ;;
      --*) ;;
      -*x*) has_exec_option=1 ;;
    esac
  done
  shopt -u nocasematch
  for tok in ${toks[@]+"${toks[@]}"}; do
    if is_shell_name "$tok"; then has_shell=1; break; fi
  done
  [ "$has_eval" -eq 0 ] || { CARRIER_CLASS=eval; return 0; }
  [ "$has_source" -eq 0 ] || { CARRIER_CLASS=source; return 0; }
  collect_command_words "$text"
  k=0
  while [ "$k" -lt "${#CMD_WORDS_DEQ[@]}" ]; do
    [ "${CMD_WORDS_DEQ[$k]}" != . ] || { CARRIER_CLASS=source; return 0; }
    k=$((k + 1))
  done
  [ "$has_trap" -eq 0 ] || { CARRIER_CLASS=trap; return 0; }
  [ "$has_alias" -eq 0 ] || { CARRIER_CLASS=alias; return 0; }
  [ "$has_xargs" -eq 0 ] || { CARRIER_CLASS=xargs; return 0; }
  [ "$has_find_exec" -eq 0 ] || { CARRIER_CLASS=find_exec; return 0; }
  if env_split_in_option_list "$text"; then CARRIER_CLASS=env_split; return 0; fi
  if [ -n "$top_text" ] && env_split_in_option_list "$top_text"; then CARRIER_CLASS=env_split; return 0; fi
  if [ -n "$top_text_g" ] && env_split_in_option_list "$top_text_g"; then CARRIER_CLASS=env_split; return 0; fi
  [ "$has_shell" -eq 0 ] || { CARRIER_CLASS=shell; return 0; }
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
  [ "$has_flock" -eq 0 ] || { CARRIER_CLASS=flock; return 0; }
  return 1
}

# The redirect pass: an output redirection is explicit and destructive on its
# own, with no rm needed alongside it. A fresh name is not in scope, so this
# only ever inspects a target that already names something; if that something
# is reached through a symlink, redirecting into it can silently repoint the
# write at whatever the link ultimately names, so it is denied. This
# deliberately does not require the target to be under the worktree or
# scratchpad: an already-existing symlinked path is unsafe wherever it is, and
# a fresh name stays out of scope wherever it is.
#
# Both spellings of a target are anchored the same way before the symlink
# walk: the anchor is resolved physically (cd && pwd -P, the idiom
# canonical_target and initialize_context already use) and every component
# below it is checked lexically by has_symlink_component. A relative target is
# anchored at the cwd. An absolute target is anchored at its root-level entry:
# on macOS /etc, /tmp and /var are system symlinks into /private, and the
# default mktemp -d lands under /var, so without this step the same existing
# file was allowed when spelled relative to a physical cwd and denied when
# spelled absolute. Only the root entry is resolved, never anything below it,
# so a symlink an unprivileged process can actually plant is still seen by the
# walk; and the leaf is never resolved, so a fresh name stays out of scope and
# a leaf that is itself a symlink is still denied. canonical_target is not
# reused here because its own lexical symlink walk rejects every path under
# /var before it resolves anything, which is exactly the denial this pass must
# not produce. Reads SEGMENTS and cwd_raw.
deny_unsafe_redirections() {
  local redirect_cwd="" redirect_target redirect_candidate redirect_normalized redirect_index
  local redirect_root redirect_root_physical segment
  case "$cwd_raw" in
    /*) redirect_cwd="$(cd -- "$cwd_raw" 2>/dev/null && pwd -P)" ;;
  esac
  for segment in ${SEGMENTS[@]+"${SEGMENTS[@]}"}; do
    scan_redirect_targets "$segment"
    redirect_index=0
    while [ "$redirect_index" -lt "${#REDIRECT_TARGETS[@]}" ]; do
      redirect_target="${REDIRECT_TARGETS[$redirect_index]}"
      redirect_index=$((redirect_index + 1))
      case "$redirect_target" in
        /*) redirect_candidate="$redirect_target" ;;
        *)
          [ -n "$redirect_cwd" ] || continue
          redirect_candidate="$redirect_cwd/$redirect_target"
          ;;
      esac
      redirect_normalized="$(normalize_absolute "$redirect_candidate")" || continue
      case "$redirect_normalized" in
        /*/*)
          redirect_root="${redirect_normalized#/}"
          redirect_root="/${redirect_root%%/*}"
          if redirect_root_physical="$(cd -- "$redirect_root" 2>/dev/null && pwd -P)"; then
            redirect_normalized="$(normalize_absolute "$redirect_root_physical${redirect_normalized#"$redirect_root"}")" || continue
          fi
          ;;
      esac
      { [ -e "$redirect_normalized" ] || [ -L "$redirect_normalized" ]; } || continue
      has_symlink_component "$redirect_normalized" && policy_deny unsafe_redirect
    done
  done
}

MAX_COMMAND_CHARS=6000

run_policy() {
  local normalized command_raw command_len segment raw_segment saw_explicit=0 saw_worktree=0 saw_rm=0 saw_temp_only=0 all_accepted=1 command_has_rm=0 index detect_segment stripped segment_index hidden_separator
  local rm_segments=()
  local -a judged
  [ "$ARG_ERROR" -eq 0 ] || policy_deny invalid_configuration
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
  # Parsing cost grows roughly quadratically with command length, so refuse
  # oversized commands up front. This is a deny, never a skip. Measure the raw
  # command as received: command substitution above drops trailing newlines, so
  # the length comes from the JSON value itself. Count BYTES, because
  # the parser consumes bytes, not codepoints.
  command_len="$(set -o pipefail; printf '%s' "$normalized" | HOME=/dev/null "$JQ_BIN" -j '.command' 2>/dev/null | LC_ALL=C "$WC_BIN" -c | "$TR_BIN" -d '[:space:]')" || policy_deny extraction_failure
  case "$command_len" in ''|*[!0-9]*) policy_deny extraction_failure ;; esac
  [ "$command_len" -le "$MAX_COMMAND_CHARS" ] || policy_deny unsupported_syntax

  CONTEXT_READY=0
  # A command whose text holds no removal word gets no opinion; only the
  # redirect pass judges it.
  if ! removal_word_in_text "$command_raw"; then
    split_command_segments "$command_raw"
    deny_unsafe_redirections
    policy_no_opinion
  fi
  # A removal word beside any code-carrying construct denies, without the
  # guard reading inside the construct.
  if find_carrier "$command_raw"; then
    policy_deny "removal_beside_$CARRIER_CLASS"
  fi

  # A removal word with no carrier: every top-level segment is judged as a
  # plain command. Only explicit rm and git worktree remove segments are in
  # scope; find -delete, rsync --delete and git clean remain outside this
  # policy's charter.
  split_command_segments "$command_raw"
  # Judging splits other text, so the top-level split's rule G flag is kept
  # here.
  hidden_separator="$HIDDEN_SEPARATOR"
  index=0
  while [ "$index" -lt "${#SEGMENTS[@]}" ]; do
    segment="${SEGMENTS[$index]}"
    trim_horizontal "$segment"
    segment="$TRIMMED"
    rm_segments[index]=0
    if [ -n "$segment" ]; then
      analyze_rm_segment "$segment" || :
      [ "$UNSUPPORTED_RM_PREFIX" -eq 0 ] || policy_deny unsupported_rm_prefix
      if [ "$HAS_RECURSIVE_RM" -eq 0 ] && [ "$KNOWN_RM" -eq 0 ]; then
        # An rm after a control word or grouping opener (then rm, { rm, (rm,
        # a case arm) runs all the same: its prefixes are judged the same way.
        strip_control_openers "$segment"
        if [ "$CONTROL_STRIPPED" != "$segment" ]; then
          analyze_rm_segment "$CONTROL_STRIPPED" || :
          [ "$UNSUPPORTED_RM_PREFIX" -eq 0 ] || policy_deny unsupported_rm_prefix
        fi
      fi
      # Every rm, recursive or not, gets the repointing checks below.
      if [ "$HAS_RECURSIVE_RM" -eq 1 ] || [ "$KNOWN_RM" -eq 1 ]; then
        rm_segments[index]=1
        command_has_rm=1
      fi
    fi
    index=$((index + 1))
  done

  if [ "$command_has_rm" -eq 1 ]; then
    index=0
    while [ "$index" -lt "${#SEGMENTS[@]}" ]; do
      segment="${SEGMENTS[$index]}"
      if [ "${rm_segments[$index]:-0}" -eq 0 ]; then
        trim_horizontal "$segment"
        segment="$TRIMMED"
        if [ -n "$segment" ] && classify_repointing_segment "$segment"; then
          policy_deny "path_repointing_$REPOINT_CLASS"
        fi
      fi
      index=$((index + 1))
    done
  fi

  # A standalone pass, independent of the rm scan above.
  deny_unsafe_redirections

  # The judging loop reads each segment after its control openers (then, do,
  # {, (, !, a case arm, a function head) are stripped. A segment the
  # stripping did not change is judged as before, and an rm in it that does
  # not parse denies unsupported_syntax. In a segment it changed, an rm that
  # does not parse or fails validation denies unsafe_rm. The segments are
  # copied first: judging a segment's text can split other text.
  judged=(${SEGMENTS[@]+"${SEGMENTS[@]}"})
  segment_index=0
  while [ "$segment_index" -lt "${#judged[@]}" ]; do
    segment="${judged[$segment_index]}"
    segment_index=$((segment_index + 1))
    trim_horizontal "$segment"
    segment="$TRIMMED"
    [ -n "$segment" ] || { all_accepted=0; continue; }
    raw_segment="$segment"
    CLEANUP_COMMAND_IS_WHOLE=0
    if [ "${#judged[@]}" -eq 1 ] && [ "$command_raw" = "$raw_segment" ]; then
      CLEANUP_COMMAND_IS_WHOLE=1
    fi
    # A segment holding a removal word whose words cannot be read denies.
    if ! split_shell_words "$segment" && removal_word_in_text "$segment"; then
      policy_deny unsupported_syntax
    fi
    strip_control_openers "$segment"
    stripped=0
    if [ "$CONTROL_STRIPPED" != "$segment" ]; then
      stripped=1
      segment="$CONTROL_STRIPPED"
      [ -n "$segment" ] || { all_accepted=0; continue; }
    fi
    # The command word is found with the segment's redirections removed; the
    # rm and worktree validators still parse the untouched segment.
    strip_command_redirections "$segment"
    detect_segment="$REDIR_STRIPPED"
    rewrite_git_head "$segment"
    segment="$GIT_HEAD_REWRITTEN"
    rewrite_git_head "$detect_segment"
    detect_segment="$GIT_HEAD_REWRITTEN"
    if looks_explicit_rm "$detect_segment"; then
      if [ "$stripped" -eq 0 ]; then
        strip_leading_negation "$segment"
        parse_simple_command "$RM_PARSE_INPUT" || policy_deny unsupported_syntax
        normalize_explicit_rm_words || policy_deny unsupported_syntax
      else
        parse_simple_command "$segment" || policy_deny unsafe_rm
        normalize_explicit_rm_words || policy_deny unsafe_rm
      fi
      initialize_context
      validate_rm || policy_deny unsafe_rm
      saw_explicit=1
      saw_rm=1
    elif judge_worktree_segment "$segment" "$raw_segment"; then
      saw_explicit=1
      saw_worktree=1
      [ "$TEMP_ONLY_REMOVE" -eq 0 ] || saw_temp_only=1
    else
      all_accepted=0
    fi
  done

  CLEANUP_COMMAND_IS_WHOLE=0

  # Main denies a removal accepted solely by the temp-root rule. Keep that
  # denial if any other segment prevents the whole command from being allowed.
  if [ "$saw_temp_only" -eq 1 ] && { [ "$all_accepted" -eq 0 ] || [ "$saw_rm" -eq 1 ]; }; then
    policy_deny unsafe_worktree_remove
  fi
  # Rule G (G2): a removal word, and a ; & | or newline that the top-level
  # split consumed other than as a delimiter (split_command_segments): bash
  # may end a command there that the guard did not judge, so deny. unsafe_rm
  # and the path_repointing_ codes keep precedence where they fire first.
  [ "$hidden_separator" -eq 0 ] || policy_deny hidden_separator
  [ "$saw_explicit" -eq 1 ] || policy_no_opinion
  [ "$all_accepted" -eq 1 ] || policy_no_opinion
  # A worktree removal can accept only worktree removals, never an rm.
  [ "$saw_worktree" -eq 0 ] || [ "$saw_rm" -eq 0 ] || policy_no_opinion
  policy_accepted
}

# The runner the adapter puts around the engine (core Perl only). The engine
# runs in its own process group under a wall-time alarm. The runner keeps the
# engine's two streams apart. Stdout passes through verbatim, except that
# stdout holding a NUL byte is withheld and reported as nul=1: a shell capture
# drops NUL bytes, so a prefixed verdict would otherwise read as clean.
# Stderr is read line by line, counted and classified, but its text is never
# kept or printed: a line is bash's own fork-retry warning when it matches
# ^(.*: )?fork: retry: and counts as other stderr when it does not. The
# runner ends its own stdout with one framing line:
#   __GUARD_RUNNER__ status=<n> timed_out=<0|1> stderr_lines=<n> stderr_other=<n> nul=<0|1>
# A timeout kills the engine's whole process group and sets timed_out=1
# whatever the status says. The runner exits 0 whenever it printed the
# framing line; no framing line means the runner itself broke (for example, a
# failed fork, exit 125).
# The single-quoted Perl program must retain Perl's own $ variables.
# shellcheck disable=SC2016
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

# Every denial names its reason code. A fault is still a denial (fail
# closed), but it is named as a fault, never as a rule:
#   engine_timeout           the runner's framing line says timed_out=1
#   engine_failed            the runner broke, the engine exited nonzero, or a
#                            no_opinion came with any stderr
#   engine_output_invalid    the engine's stdout is not exactly one valid
#                            verdict, or it holds a NUL byte
#   engine_stderr_on_accept  the engine accepted but wrote to stderr
#   dependency_missing_*     jq, the runner (perl) or the engine file is missing
#   adapter_input_invalid    the hook input is not one valid Bash event
#   invalid_configuration    the adapter's own options are invalid
# A deny stands whatever the engine wrote to stderr, since the engine's own
# audit record is stderr. Silence needs an engine that wrote no stderr at all:
# any stderr line, a fork-retry warning included, can mean an engine check
# failed or was skipped, so a no_opinion beside it denies as engine_failed and
# an accepted verdict beside it as engine_stderr_on_accept. The runner reports
# stderr as counts only; no stderr text reaches the denial or the audit.
run_adapter() {
  local normalized response body framing parsed decision reason status timed_out stderr_lines
  local runner_rc=0
  local framing_re='^__GUARD_RUNNER__ status=([0-9]+) timed_out=([01]) stderr_lines=([0-9]+) stderr_other=([0-9]+) nul=([01])$'
  local policy_args=()
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
        (.cwd | type == "string" and length > 0 and (test("[\u0000-\u0008\u000b-\u001f\u007f]") | not)))
    ) | .[0] | {cwd:.cwd,command:.tool_input.command}
  ' 2>/dev/null)" || emit_claude_deny adapter_input_invalid
  [ -n "$normalized" ] || emit_claude_deny adapter_input_invalid
  GUARD_LOG_INPUT="$normalized"
  policy_args=(--policy)
  [ -z "$SCRATCHPAD" ] || policy_args+=(--scratchpad "$SCRATCHPAD")
  [ -z "$AUTHORIZED_WORKTREE" ] || policy_args+=(--authorized-worktree "$AUTHORIZED_WORKTREE")
  # Only the runner's stdout is captured. The engine's stderr never reaches
  # it, and the runner's own stderr is discarded so the audit sink keeps its
  # fixed shape.
  response="$(printf '%s\n' "$normalized" | "$TIMEOUT_RUNNER" -e "$GUARD_RUNNER_PROGRAM" "$TIMEOUT_SECONDS" /bin/bash --noprofile --norc -p "$POLICY_PATH" "${policy_args[@]}" 2>/dev/null)" || runner_rc=$?
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
  # The engine's stdout must be exactly one line holding one verdict object; a
  # stray stdout line is never treated as noise.
  case "$body" in ''|*$'\n'*) emit_claude_deny engine_output_invalid ;; esac
  # Exactly two decisions are read: deny, and no_opinion with the reason
  # not_explicit_operation or accepted. Anything else, an allow included, is
  # invalid output and denies.
  parsed="$(printf '%s' "$body" | HOME=/dev/null "$JQ_BIN" -ers '
    select(
      length == 1 and
      (.[0] | type == "object" and keys == ["decision","reason"] and
        (.decision == "deny" or .decision == "no_opinion") and
        (.reason | type == "string" and test("^[a-z_]+$")))
    ) | .[0] | [.decision,.reason] | @tsv
  ' 2>/dev/null)" || emit_claude_deny engine_output_invalid
  [ -n "$parsed" ] || emit_claude_deny engine_output_invalid
  decision="${parsed%%$'\t'*}"
  reason="${parsed#*$'\t'}"
  case "$decision/$reason" in
    no_opinion/accepted)
      [ "$stderr_lines" = 0 ] || emit_claude_deny engine_stderr_on_accept
      VERDICT_STARTED=1
      VERDICT_DONE=1
      exit 0 ;;
    no_opinion/not_explicit_operation)
      [ "$stderr_lines" = 0 ] || emit_claude_deny engine_failed
      VERDICT_STARTED=1
      VERDICT_DONE=1
      exit 0 ;;
    deny/*) emit_claude_deny "$reason" ;;
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
