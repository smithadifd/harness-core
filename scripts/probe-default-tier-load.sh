#!/usr/bin/env bash
# probe-default-tier-load.sh [--timeout <secs>] [--only A|B|C]... [-h]
#
# Read-only probe of the default tier's three load points, run against a
# fresh home folder that is not the invoking user's. It reports one line per
# sub-load, `<PASS|FAIL|UNVERIFIED> <id> — <evidence>`, then a summary line.
#
#   A1  a rules file reaches the main thread
#   A2  ... and a general-purpose subagent
#   A3  ... and a session after a forced compaction
#   B1  (diagnostic) a plugin option reaches the main thread's Bash commands
#   B2  a plugin option reaches a general-purpose subagent's Bash commands
#   C   the ledger plugin's hooks are silent before setup (no values set)
#
# PASS needs a live session in the target home to return the load's own random
# sentinel through the route under test. Anything else is FAIL (the route ran
# and the sentinel was not there) or UNVERIFIED (the route could not be run or
# the reply could not be read). Nothing is ever PASS by default.
#
# Exit codes: 0 all PASS, 1 any FAIL with no UNVERIFIED, 2 any UNVERIFIED
# (it wins over FAIL), 3 usage error or a refused PROBE_HOME.
#
# Environment:
#   PROBE_HOME              absolute path of a fresh, empty-or-absent folder to
#                           use as the target home. Refused (exit 3, before
#                           anything is created) when it equals the invoking
#                           user's HOME, or exists and is not empty (a file
#                           of the caller's would otherwise be overwritten).
#                           Unset: the probe makes its own temp home and
#                           removes it on exit. A PROBE_HOME the caller chose
#                           is left in place.
#   CLAUDE_CODE_OAUTH_TOKEN the only credential the probe uses. It is passed
#                           inline to each claude command and never printed.
#                           Unset: every selected load is UNVERIFIED.
#
# Load C runs the public ledger repository's hooks plugin at the one commit
# pinned below (LEDGER_PIN_SHA); no environment variable changes it. The stream
# of a session names a hook response by its event, never by its plugin, so a
# hook run is attributed to the plugin by a recording sh that stands first on
# the session's PATH and logs each run of the plugin's own hook scripts.
#
# Every claude command gets HOME=<target home> inline, with CLAUDE_CONFIG_DIR
# and the other config-folder and credential variables removed, so the
# invoking user's own Claude folder is never read or written.
set -uo pipefail

EX_PASS=0; EX_FAIL=1; EX_UNVERIFIED=2; EX_USAGE=3
MODEL=claude-haiku-5-5
MAX_REPLY_BYTES=1048576
LEDGER_PLUGIN=ledger-hooks
# Load C runs the ledger plugin's hooks in a session that holds the operator's
# token, so the code under test is pinned by commit, never by a tag name or a
# newest-tag lookup: C is UNVERIFIED unless the fetched HEAD is exactly this
# commit. Moving the pin is a change to this script, reviewed as one.
LEDGER_PIN_TAG=v0.3.1
LEDGER_PIN_SHA=346cb3595180fcea702ebdd37b32ff8c36325ea5
# The local test plugin of load B. A plugin verb that takes an id is given
# <plugin>@<marketplace>, the form `claude plugin list` shows.
PROBE_PLUGIN=probe-option
PROBE_MARKET=probe-marketplace

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
pin_file="$repo_root/ledger-vendor.pin"
probe_timeout=120
only=()

usage() {
  cat <<'EOF'
Usage: probe-default-tier-load.sh [--timeout <secs>] [--only <A|B|C>]... [-h]

Probes where the default tier's files and options reach a session, in a fresh
home folder. Needs CLAUDE_CODE_OAUTH_TOKEN in the environment, `claude`,
python3 and perl on PATH, and (load C) git and network access to the public
ledger repository.

  --timeout <secs>  per-command limit, a positive whole number (default 120)
  --only <A|B|C>    run only that load; repeatable (default: all three)

Environment: PROBE_HOME (absolute, empty or absent folder to use as the target
home), CLAUDE_CODE_OAUTH_TOKEN.

Exit codes:
  0  every sub-load PASS
  1  one or more FAIL, none UNVERIFIED
  2  one or more UNVERIFIED (takes precedence over FAIL)
  3  usage error, or PROBE_HOME refused
EOF
}

usage_error() {
  printf 'USAGE  %s\n' "$1" >&2
  usage >&2
  exit "$EX_USAGE"
}

refuse_home() {
  printf 'REFUSED  %s\n' "$1" >&2
  exit "$EX_USAGE"
}

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help)
      usage
      exit "$EX_PASS"
      ;;
    --timeout)
      [ $# -ge 2 ] || usage_error '--timeout needs a positive whole number of seconds'
      case "$2" in
        ''|*[!0-9]*) usage_error "invalid timeout: $2" ;;
      esac
      [ "${#2}" -le 6 ] || usage_error "invalid timeout: $2"
      probe_timeout=$((10#$2))
      [ "$probe_timeout" -gt 0 ] || usage_error "invalid timeout: $2"
      shift 2
      ;;
    --only)
      [ $# -ge 2 ] || usage_error '--only needs A, B or C'
      case "$2" in
        A|B|C) only+=("$2") ;;
        *) usage_error "invalid --only value: $2" ;;
      esac
      shift 2
      ;;
    *)
      usage_error "unknown argument: $1"
      ;;
  esac
done

# --- PROBE_HOME: refuse before any session or any file is made ----------------
probe_home_given=0
probe_home=""
if [ "${PROBE_HOME+set}" = set ]; then
  probe_home_given=1
  probe_home=$PROBE_HOME
  [ -n "$probe_home" ] || refuse_home 'PROBE_HOME is empty'
  case "$probe_home" in
    /*) ;;
    *) refuse_home 'PROBE_HOME is not an absolute path' ;;
  esac
  case "/$probe_home/" in
    */../*) refuse_home 'PROBE_HOME has a .. component' ;;
  esac
  real_home=""
  if [ -n "${HOME:-}" ] && [ -d "$HOME" ]; then
    real_home="$(cd -P "$HOME" && pwd)"
  fi
  [ -n "$real_home" ] || refuse_home 'HOME is not a folder, so PROBE_HOME cannot be told apart from it'
  if [ -e "$probe_home" ] || [ -L "$probe_home" ]; then
    [ -d "$probe_home" ] || refuse_home 'PROBE_HOME exists and is not a folder'
    resolved_home="$(cd -P "$probe_home" && pwd)"
  else
    resolved_home="${probe_home%/}"
  fi
  [ "$resolved_home" != "/" ] || refuse_home 'PROBE_HOME is the root folder'
  [ "$resolved_home" != "$real_home" ] || refuse_home 'PROBE_HOME is the invoking user'"'"'s own HOME'
  if [ -e "$probe_home/.claude/rules" ] || [ -L "$probe_home/.claude/rules" ]; then
    refuse_home 'PROBE_HOME already holds .claude/rules'
  fi
  if [ -L "$probe_home/.claude" ]; then
    refuse_home 'PROBE_HOME/.claude is a symlink'
  fi
  if [ -d "$probe_home" ]; then
    # find's own error text counts as an entry: an unreadable folder is refused.
    first_entry="$(find "$resolved_home" -mindepth 1 -maxdepth 1 2>&1 | head -n 1)"
    [ -z "$first_entry" ] || refuse_home 'PROBE_HOME is not empty'
  fi
fi

# --- result lines --------------------------------------------------------------
n_pass=0; n_fail=0; n_unv=0

scrub() {
  local s=$1 redacted='<redacted>'
  if [ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
    s=${s//"$CLAUDE_CODE_OAUTH_TOKEN"/$redacted}
  fi
  printf '%s' "$s"
}

# emit <PASS|FAIL|UNVERIFIED> <id> <evidence>. B1 is a diagnostic: it is
# printed in the same grammar and not counted, since B2 carries the verdict.
emit() {
  local evidence
  evidence="$(scrub "$3")"
  if [ "$2" = B1 ]; then
    printf '%s %s — diagnostic, not counted: %s\n' "$1" "$2" "$evidence"
    return
  fi
  printf '%s %s — %s\n' "$1" "$2" "$evidence"
  case "$1" in
    PASS) n_pass=$((n_pass + 1)) ;;
    FAIL) n_fail=$((n_fail + 1)) ;;
    *) n_unv=$((n_unv + 1)) ;;
  esac
}

finish() {
  printf 'summary: %d pass, %d fail, %d unverified\n' "$n_pass" "$n_fail" "$n_unv"
  if [ "$n_unv" -gt 0 ]; then
    exit "$EX_UNVERIFIED"
  fi
  if [ "$n_fail" -gt 0 ]; then
    exit "$EX_FAIL"
  fi
  exit "$EX_PASS"
}

selected() {
  local group
  [ "${#only[@]}" -gt 0 ] || return 0
  for group in "${only[@]}"; do
    [ "$group" = "$1" ] && return 0
  done
  return 1
}

group_ids() {
  case "$1" in
    A) echo 'A1 A2 A3' ;;
    B) echo 'B1 B2' ;;
    C) echo 'C' ;;
  esac
}

unverify_group() {
  local id
  for id in $(group_ids "$1"); do
    emit UNVERIFIED "$id" "$2"
  done
}

unverify_all() {
  local group
  for group in A B C; do
    selected "$group" && unverify_group "$group" "$1"
  done
  return 0
}

# --- preflight: fail closed before any session ---------------------------------
if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
  unverify_all 'no CLAUDE_CODE_OAUTH_TOKEN'
  finish
fi
if ! command -v claude >/dev/null 2>&1; then
  unverify_all 'claude is not on PATH'
  finish
fi
if ! command -v python3 >/dev/null 2>&1 || ! command -v perl >/dev/null 2>&1; then
  unverify_all 'python3 and perl are both needed on PATH'
  finish
fi

scratch="$(mktemp -d "${TMPDIR:-/tmp}/probe-default-tier.XXXXXX")" || scratch=""
if [ -z "$scratch" ] || [ ! -d "$scratch" ]; then
  unverify_all 'could not create a scratch folder'
  finish
fi
cleanup() {
  if [ -n "${scratch:-}" ] && [ -d "$scratch" ]; then
    python3 -I -c 'import shutil, sys; shutil.rmtree(sys.argv[1], ignore_errors=True)' "$scratch"
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [ "$probe_home_given" -eq 1 ]; then
  mkdir -p "$probe_home" || { unverify_all 'could not create PROBE_HOME'; finish; }
  target_home="$(cd -P "$probe_home" && pwd)"
else
  mkdir "$scratch/home" || { unverify_all 'could not create the temp home'; finish; }
  target_home="$(cd -P "$scratch/home" && pwd)"
fi
workdir="$target_home/probe-work"
mkdir -p "$workdir" || { unverify_all 'could not create the working folder'; finish; }

out_file="$scratch/reply.out"
err_file="$scratch/reply.err"
stdin_file=/dev/null
base_path="$PATH"
why=""

# --- running claude ------------------------------------------------------------
# session <PATH to use> <claude args...>: one claude command in the target home.
# HOME and the token are inline on this one command (never exported); the
# variables that could point claude at another config folder or credential are
# removed. Returns 0 on a clean run within the size cap; else 1 with $why set.
session() {
  local spath=$1 rc size first
  shift
  : >"$out_file"
  : >"$err_file"
  (
    cd "$workdir" || exit 125
    HOME="$target_home" CLAUDE_CODE_OAUTH_TOKEN="$CLAUDE_CODE_OAUTH_TOKEN" PATH="$spath" \
      exec env -u CLAUDE_CONFIG_DIR -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN \
        -u XDG_CONFIG_HOME -u XDG_DATA_HOME -u XDG_STATE_HOME -u XDG_CACHE_HOME \
        -u CLAUDE_ENV_FILE -u LEDGER_DB -u LEDGER_HARNESS -u LEDGER_OPERATOR \
        perl -e 'alarm shift; exec @ARGV' "$probe_timeout" claude "$@"
  ) <"$stdin_file" >"$out_file" 2>"$err_file"
  rc=$?
  stdin_file=/dev/null
  if [ "$rc" -eq 142 ]; then
    why="timed out after ${probe_timeout}s"
    return 1
  fi
  if [ "$rc" -ne 0 ]; then
    first="$(head -n 1 "$err_file" | cut -c1-200)"
    why="claude exited $rc${first:+: $first}"
    return 1
  fi
  size="$(wc -c <"$out_file" | tr -d ' ')"
  if [ "$size" -gt "$MAX_REPLY_BYTES" ]; then
    why="reply over the ${MAX_REPLY_BYTES}-byte cap ($size bytes)"
    return 1
  fi
  return 0
}

rand_hex() {
  python3 -I -c 'import secrets; print(secrets.token_hex(8))'
}

# The reader of a probe session's reply. One verdict line per call:
#   <CODE>|<field 1>|<field 2>
# CODE is BAD (field 1 says why the reply cannot be read), FOUND or MISSING
# (the wanted text is, or is not, in the route under test; field 1 is the
# value seen, field 2 an excerpt), NOSPAWN, COUNT, NOTGP, BGRUN, TOPBASH or
# LEAK (the route was not the one under test; COUNT is more or fewer than one
# Agent/Task call or result; NOBASH is, for B2, more or fewer than one Bash call
# of the subagent itself, than one result for it, or a result not under the
# Agent call, or a command that is not exactly the one the prompt gave it; TOPBASH is any
# top-level tool call but the Agent call; LEAK is also the value in that
# Bash command), NOBOUNDARY or LEAKSEED (the compaction was not
# shown, or the sentinel was there before it), ROOT (the install folder the
# install record names), RAN or NORUN (the recording sh logged the plugin's two
# hook scripts under a trusted root, or did not; RAN's second field names the
# root kinds; NORUN's second field also counts the log's malformed records),
# NOEVENT, DIRTY or CLEAN (the stream's hook
# responses, which name an event and not a plugin).
IFS= read -r -d '' ANALYZER <<'PYEOF' || true
import json, os, re, sys

mode, path = sys.argv[1], sys.argv[2]
extra = sys.argv[3:]


def clean(value, limit=160):
    value = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value))
    value = re.sub(r"\s+", " ", value).strip().replace("|", "/")
    return value[:limit]


def out(code, first="", second="", limit=160):
    print("%s|%s|%s" % (code, clean(first, limit), clean(second, limit)))
    sys.exit(0)


if mode == "shimlog":
    # The recording sh's log: one record per run, on one line, written in one
    # write: RUN, each argument and END, separated by tabs. A line that is not
    # exactly one such record (two runs glued together, a cut record) is
    # malformed: it is counted and read as no run. PASS needs a run whose only
    # argument is a hook script of the plugin under a trusted root, given as
    # kind:folder: "install" is the install folder the install record names;
    # "pinned checkout" is the plugin folder inside the probe's own ledger
    # checkout, whose HEAD the caller verified equal to the pinned commit before
    # it passed the root (a plugin installed from a local-directory marketplace
    # runs from its source folder, not from the install record's folder). Any
    # other root is untrusted. Both sides of the comparison are resolved the same
    # way first: a logged script is its folder's physical path plus its base name
    # (the shell logs the spelling it was given, such as /var/... for
    # /private/var/... on macOS) and a root is its physical path; the two are
    # equal when they have the same path components, never by a string prefix,
    # so /a/ledger-src-evil is not /a/ledger-src. A logged script that does not
    # exist, or is not an absolute path, is untrusted whatever its spelling. A
    # missing log is an empty one: nothing ran.
    roots = []
    for item in extra:
        kind, _sep, folder = item.partition(":")
        if not folder.startswith("/"):
            continue
        real = os.path.realpath(folder)
        if os.path.isdir(real) and (kind, real) not in roots:
            roots.append((kind, real))

    def components(value):
        return [piece for piece in value.split("/") if piece]

    def physical(script):
        if not script.startswith("/"):
            return None
        folder, name = os.path.split(script)
        full = os.path.join(os.path.realpath(folder), name)
        return full if os.path.isfile(full) else None

    wanted = ("session-start.sh", "prompt.sh")
    try:
        with open(path, "rb") as handle:
            lines = handle.read().decode("utf-8", "replace").split("\n")
    except OSError:
        lines = []
    runs = []
    malformed = 0
    for line in lines:
        if not line:
            continue
        fields = line.split("\t")
        if (len(fields) >= 2 and fields[0] == "RUN" and fields[-1] == "END"
                and line.count("RUN\t") == 1 and line.count("\tEND") == 1):
            runs.append(fields[1:-1])
        else:
            malformed += 1
    found = dict((name, 0) for name in wanted)
    kinds = []
    for argv in runs:
        if len(argv) != 1:
            continue
        full = physical(argv[0])
        if full is None:
            continue
        for name in wanted:
            for kind, root in roots:
                if components(full) == components(root) + ["hooks", name]:
                    found[name] += 1
                    if kind not in kinds:
                        kinds.append(kind)
    missing = [name for name in wanted if not found[name]]
    if missing:
        scripts = [argv[0] for argv in runs if argv and argv[0].endswith(".sh")]
        out("NORUN", ", ".join(missing),
            "sh was run %d time(s)%s%s" % (
                len(runs),
                ", %d malformed record(s) not counted" % malformed if malformed else "",
                "; scripts run: " + ", ".join(scripts[:3]) if scripts else ""),
            limit=600)
    out("RAN", "session-start.sh x%d, prompt.sh x%d" % (found["session-start.sh"], found["prompt.sh"]),
        " and ".join(kinds))


try:
    with open(path, "rb") as handle:
        text = handle.read().decode("utf-8")
except (OSError, UnicodeDecodeError):
    out("BAD", "reply is not readable UTF-8 text")
if not text.strip():
    out("BAD", "empty reply")
if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", text):
    out("BAD", "reply holds a control character")

OPT = re.compile(r"PROBE_OPT=\[([^\]]*)\]")
LAUNCH = re.compile(r"Async agent launched|This tool result is internal metadata", re.I)


def seen_option(blob):
    found = OPT.search(blob)
    return found.group(1) if found else "(no PROBE_OPT line)"


if mode == "text":
    needle = extra[0]
    if needle in text:
        out("FOUND", seen_option(text), text)
    out("MISSING", seen_option(text), text)

if mode == "installroot":
    # The install record Claude Code leaves in the target home: the install
    # folder of one plugin id, which must lie inside the target home.
    plugin_id, home = extra[0], extra[1]
    try:
        data = json.loads(text)
    except ValueError:
        out("BAD", "the install record is not JSON")
    table = data.get("plugins") if isinstance(data, dict) else None
    entries = table.get(plugin_id) if isinstance(table, dict) else None
    if isinstance(entries, dict):
        entries = [entries]
    paths = [entry["installPath"] for entry in entries or []
             if isinstance(entry, dict) and isinstance(entry.get("installPath"), str)]
    if len(paths) != 1:
        out("BAD", "the install record lists %d install folder(s) for %s, expected 1" % (len(paths), plugin_id))
    root = paths[0]
    if not os.path.isabs(root):
        out("BAD", "the install folder %s is not an absolute path" % root, limit=600)
    real = os.path.realpath(root)
    base = os.path.realpath(home)
    if real != base and not real.startswith(base.rstrip("/") + "/"):
        out("BAD", "the install folder %s is outside the target home" % root, limit=600)
    out("ROOT", root, real, limit=4096)

events = []
for number, line in enumerate(text.splitlines(), 1):
    if not line.strip():
        continue
    try:
        events.append(json.loads(line))
    except ValueError:
        out("BAD", "line %d of the stream is not JSON" % number)
events = [event for event in events if isinstance(event, dict)]


def result_event():
    found = [event for event in events if event.get("type") == "result"]
    if not found:
        out("BAD", "the stream has no result message")
    last = found[-1]
    if last.get("is_error") or last.get("subtype") != "success":
        out("BAD", "result %s, is_error %s: %s" % (
            last.get("subtype"), last.get("is_error"), last.get("result", "")))
    return last


def blocks(event):
    content = (event.get("message") or {}).get("content")
    return [item for item in content if isinstance(item, dict)] if isinstance(content, list) else []


def block_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(item.get("text", "") for item in value if isinstance(item, dict))
    return ""


if mode == "compacted":
    # One process, three turns: the seed, the /compact, the question. Each
    # turn ends in a result message. PASS needs a compact_boundary between the
    # seed's result and the question's result, so the reply that holds the
    # sentinel came after a compaction in the same process.
    needle = extra[0]
    results = [number for number, event in enumerate(events) if event.get("type") == "result"]
    if len(results) != 3:
        out("BAD", "the stream has %d result messages, expected 3 (seed, compaction, question)" % len(results))
    for number in results:
        event = events[number]
        if event.get("is_error") or event.get("subtype") != "success":
            out("BAD", "result %s, is_error %s: %s" % (
                event.get("subtype"), event.get("is_error"), event.get("result", "")))
    seed_at, ask_at = results[0], results[2]
    marks = [number for number, event in enumerate(events)
             if event.get("type") == "system" and event.get("subtype") == "compact_boundary"
             and seed_at < number < ask_at]
    if not marks:
        out("NOBOUNDARY", "no compact_boundary message between the seed turn and the question",
            events[results[1]].get("result", ""))
    for event in events[:marks[0]]:
        if event.get("type") in ("assistant", "user", "result") and needle in json.dumps(event):
            out("LEAKSEED", "the sentinel appeared before the compaction")
    final = events[ask_at].get("result", "")
    if needle in final:
        out("FOUND", "compact_boundary seen before the reply", final)
    out("MISSING", "", final)

if mode == "subagent":
    needle, pattern = extra[0], extra[1]
    result_event()
    top = [event for event in events if not event.get("parent_tool_use_id")]
    spawned = {}
    calls = 0
    top_other = []
    for event in top:
        if event.get("type") != "assistant":
            continue
        for item in blocks(event):
            if item.get("type") != "tool_use":
                continue
            if item.get("name") in ("Agent", "Task"):
                calls += 1
                spawned[item.get("id")] = item.get("input") or {}
            else:
                top_other.append(str(item.get("name")))
    # The results of the top-level Agent/Task calls, by call id.
    answers = []
    errors = []
    for event in top:
        if event.get("type") != "user":
            continue
        for item in blocks(event):
            if (item.get("type") == "tool_result" and isinstance(item.get("tool_use_id"), str)
                    and item.get("tool_use_id") in spawned):
                (errors if item.get("is_error") else answers).append(block_text(item.get("content")))
    counts = "%d top-level Agent/Task call(s), %d non-error result(s) for them" % (calls, len(answers))
    if not calls:
        out("NOSPAWN", "the main thread started no subagent (%s)" % counts)
    if top_other:
        out("TOPBASH", "the main thread ran %s itself %d time(s)" % (", ".join(sorted(set(top_other))), len(top_other)))
    # Exactly one call and one result: with more, a reply of one subagent could
    # stand for the route while another's was never read.
    if calls != 1 or len(spawned) != 1:
        out("COUNT", "the main thread made %d Agent/Task calls, expected exactly one (%s)" % (calls, counts))
    for given in spawned.values():
        kind = given.get("subagent_type")
        if kind != "general-purpose":
            out("NOTGP", kind if isinstance(kind, str) and kind else "(missing)")
        if str(given.get("run_in_background")).lower() == "true":
            out("BGRUN", "subagent ran in the background")
        if needle in json.dumps(given):
            out("LEAK", "the subagent's prompt carried the sentinel")
    if errors:
        out("BAD", "the subagent call returned an error: %s" % errors[0])
    if not answers:
        out("COUNT", "the subagent call has no result in the stream (%s)" % counts)
    if len(answers) != 1:
        out("COUNT", "the subagent call has %d results in the stream, expected one (%s)" % (len(answers), counts))
    reply = answers[0]
    # A background launch answers the Agent call with a notice, not with the
    # subagent's final reply; reading it as the reply would be a false FAIL.
    if LAUNCH.search(reply):
        out("BGRUN", "subagent ran in the background", reply)
    if len(extra) > 2 and extra[2] == "nested-bash":
        # B2 only. The Agent call's reply is what the main thread was handed, and
        # the main thread could have read the value itself and passed it on; the
        # subagent's own Bash is what the line says saw it. So the Agent call must
        # have exactly one Bash call of its own (a tool_use whose parent_tool_use_id
        # is the Agent call's id), the command must not carry the value and must equal,
        # after stripping surrounding whitespace only, the command the prompt hands the
        # subagent (the shell passes it from the same constant the prompt is built from),
        # and the call must have exactly one result of any kind:
        # every tool_result for its id is counted whatever its parent and is_error,
        # and that one must be a non-error result whose own parent_tool_use_id is
        # the Agent call's id. The value is read from that result, never from the
        # Agent's reply. (Counting first, then requiring the parent, keeps a second
        # result under another parent from being dropped by a filter.)
        agent_id = next(iter(spawned))
        nested = [item for event in events
                  if event.get("type") == "assistant" and event.get("parent_tool_use_id") == agent_id
                  for item in blocks(event)
                  if item.get("type") == "tool_use" and item.get("name") == "Bash"]
        ids = [item.get("id") for item in nested if isinstance(item.get("id"), str) and item.get("id")]
        every = [(event, item) for event in events if event.get("type") == "user"
                 for item in blocks(event)
                 if item.get("type") == "tool_result" and item.get("tool_use_id") in ids]
        plain = [item for _event, item in every if not item.get("is_error")]
        shown = [item for event, item in every
                 if not item.get("is_error") and event.get("parent_tool_use_id") == agent_id]
        if len(nested) != 1 or len(ids) != 1 or len(every) != 1 or len(shown) != 1:
            out("NOBASH", "the Agent call has %d nested Bash call(s) and %d non-error result(s) for them "
                "(%d in all, %d under it as a non-error result), "
                "expected exactly one of each" % (len(nested), len(plain), len(every), len(shown)))
        given_input = nested[0].get("input")
        command = given_input.get("command") if isinstance(given_input, dict) else None
        if needle in json.dumps(given_input):
            out("LEAK", "the subagent's Bash command carried the value")
        # A command that merely mentions the option can have read the value from a
        # file; only the prompted command reads the environment variable.
        if not isinstance(command, str) or command.strip(" \t\r\n") != extra[3]:
            cut = command.strip(" \t\r\n")[:120] if isinstance(command, str) else "(no command)"
            out("NOBASH", "the subagent's Bash command is not the command it was given: %s" % cut, limit=400)
        reply = block_text(shown[0].get("content"))
    if pattern in reply:
        out("FOUND", seen_option(reply), reply)
    out("MISSING", seen_option(reply), reply)

if mode == "hooks":
    # Corroboration only. The stream names a hook response by its event
    # (SessionStart:startup, UserPromptSubmit), never by its plugin, so it cannot
    # say whose hook ran: the recording sh's log does that. Here: each event has a
    # response, and no response of either event failed or printed.
    result_event()
    wanted = ("SessionStart", "UserPromptSubmit")
    counts = dict((name, 0) for name in wanted)
    dirty = []
    seen = []

    for event in events:
        if event.get("type") != "system" or event.get("subtype") != "hook_response":
            continue
        name = event.get("hook_event")
        seen.append(clean(event.get("hook_name") or name or "?", 60))
        if name not in counts:
            continue
        counts[name] += 1
        code = event.get("exit_code")
        if not isinstance(code, int):
            out("BAD", "a %s hook_response has no exit_code" % name)
        if code != 0:
            dirty.append("%s exited %d" % (name, code))
        if event.get("outcome") not in (None, "", "success"):
            dirty.append("%s outcome %s" % (name, event.get("outcome")))
        if str(event.get("stdout") or "").strip():
            dirty.append("%s printed on stdout" % name)
        if str(event.get("stderr") or "").strip():
            dirty.append("%s printed on stderr" % name)
    labels = ", ".join(sorted(set(seen))[:5]) or "none"
    for name in wanted:
        if counts[name] == 0:
            out("NOEVENT", name, labels)
    if dirty:
        out("DIRTY", "; ".join(dirty), labels)
    out("CLEAN", "SessionStart=%d, UserPromptSubmit=%d" % (counts["SessionStart"], counts["UserPromptSubmit"]), labels)

out("BAD", "unknown analyzer mode")
PYEOF

# The recording sh of load C, written by one call: it appends one record (RUN,
# its arguments and END, tab-separated, on one line) to a log outside the target
# home, then runs the system sh with the same arguments and the same stdin.
# Arguments: file, log, the sh. The record is built first and written by one
# command, so it reaches the log in one write: Claude Code starts a session's
# hooks together, and a record written in pieces is cut by another run's.
IFS= read -r -d '' SHIM_WRITER <<'PYEOF' || true
import os, shlex, sys

path, log, real = sys.argv[1:4]
text = "\n".join([
    "#!/bin/sh",
    "# Records one run on one line, in one write, then runs the system sh unchanged.",
    "probe_record=RUN",
    'for probe_arg in "$@"; do',
    '  probe_record="$probe_record\t$probe_arg"',
    "done",
    'probe_record="$probe_record\tEND"',
    "printf '%%s\\n' \"$probe_record\" >>%s 2>/dev/null" % shlex.quote(log),
    'exec %s "$@"' % shlex.quote(real),
    "",
])
descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o755)
with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
    handle.write(text)
os.chmod(path, 0o755)
PYEOF

a_code=""; a_one=""; a_two=""
# analyze <mode> <file> [args...]: run the reader, split its verdict line.
analyze() {
  local line
  line="$(python3 -I -c "$ANALYZER" "$@" 2>/dev/null)"
  IFS='|' read -r a_code a_one a_two <<<"$line"
  [ -n "$a_code" ] || { a_code=BAD; a_one='the reply reader produced no verdict'; }
}

# --- load A: a rules file ---------------------------------------------------------
P_A1='[probe A1] Reply with only the probe marker given in your instructions, exactly as written. If your instructions contain no probe marker, reply exactly NONE.'
P_A2='[probe A2] Use the Agent tool exactly once, with subagent_type "general-purpose" and run_in_background false, to start one foreground subagent and wait for its reply. Tell the subagent: "Use no tools. Reply with only the probe marker given in your instructions, exactly as written, or exactly NONE if your instructions contain no probe marker." Do not write any marker value in the subagent prompt and do not answer from your own context. When the subagent replies, reply with exactly what it replied and nothing else.'
P_A3_SEED='[probe A3 seed] Reply with the single word READY.'
P_A3_ASK='[probe A3 ask] Reply with only the probe marker given in your instructions, exactly as written. If your instructions contain no probe marker, reply exactly NONE.'

# A message for a failed A2/B2 reading, by analyzer code.
route_not_run() {
  case "$a_code" in
    NOSPAWN) printf '%s, so the subagent route was not exercised' "$a_one" ;;
    NOTGP) printf 'the subagent type was %s, not general-purpose' "$a_one" ;;
    BGRUN) printf '%s, so the subagent'"'"'s own final reply was not read' "$a_one" ;;
    TOPBASH) printf '%s, so the subagent route was not isolated' "$a_one" ;;
    NOBASH) printf '%s, so the output of a subagent'"'"'s own Bash is not shown' "$a_one" ;;
    LEAK) printf '%s, so its reply proves nothing' "$a_one" ;;
    *) printf '%s' "${a_one:-unreadable reply}" ;;
  esac
}

group_a() {
  local sentinel a3_input
  sentinel="PROBE-RULES-$(rand_hex)"
  mkdir -p "$target_home/.claude/rules" \
    && printf '# Probe rules file\n\nThe probe marker is %s. When you are asked for the probe marker, reply with exactly that value.\n' \
      "$sentinel" >"$target_home/.claude/rules/probe-load-rules.md" \
    || { unverify_group A 'could not place the rules file in the target home'; return; }

  # A1: the main thread.
  if session "$base_path" -p "$P_A1" --model "$MODEL" --max-turns 1 --no-session-persistence --tools ""; then
    analyze text "$out_file" "$sentinel"
    case "$a_code" in
      FOUND) emit PASS A1 "the main thread returned the rules sentinel $sentinel" ;;
      MISSING) emit FAIL A1 "the main thread's reply lacks the rules sentinel; reply: $a_two" ;;
      *) emit UNVERIFIED A1 "$a_one" ;;
    esac
  else
    emit UNVERIFIED A1 "$why"
  fi

  # A2: a general-purpose subagent, shown by a tool_use of the Agent tool and
  # the sentinel in that call's own result.
  if session "$base_path" -p "$P_A2" --model "$MODEL" --max-turns 6 --no-session-persistence \
      --output-format stream-json --verbose --permission-mode dontAsk --allowedTools=Agent --tools=Agent; then
    analyze subagent "$out_file" "$sentinel" "$sentinel"
    case "$a_code" in
      FOUND) emit PASS A2 "a general-purpose subagent returned the rules sentinel $sentinel" ;;
      MISSING) emit FAIL A2 "the general-purpose subagent's reply lacks the rules sentinel; reply: $a_two" ;;
      *) emit UNVERIFIED A2 "$(route_not_run)" ;;
    esac
  else
    emit UNVERIFIED A2 "$why"
  fi

  # A3: a compaction inside one claude process. The headless route is the
  # Agent SDK's slash-command page, section "Compact history with /compact"
  # (code.claude.com/docs/en/agent-sdk/slash-commands): /compact sent as a
  # user message, then the compact_boundary system message in the stream-json
  # output. Here the seed turn, /compact and the question are three user
  # messages on one process's stdin (--input-format stream-json), so the
  # question is answered by the process that compacted, not by a new one
  # resumed from a transcript. The seed never mentions the sentinel. What a
  # PASS shows is in the README: the rules file is still in the session's
  # context after a compaction in that session.
  a3_input="$scratch/a3-input.jsonl"
  if ! python3 -I -c '
import json, sys
for text in sys.argv[1:]:
    print(json.dumps({"type": "user", "message": {"role": "user", "content": text}}))
' "$P_A3_SEED" '/compact' "$P_A3_ASK" >"$a3_input"; then
    emit UNVERIFIED A3 'could not write the compaction session input'
  else
    stdin_file="$a3_input"
    if session "$base_path" -p --input-format stream-json --output-format stream-json --verbose \
        --model "$MODEL" --tools ""; then
      analyze compacted "$out_file" "$sentinel"
      case "$a_code" in
        FOUND) emit PASS A3 "in one session, after a compaction (compact_boundary seen) the reply returned the rules sentinel $sentinel" ;;
        MISSING) emit FAIL A3 "after a compaction in the same session the reply lacks the rules sentinel; reply: $a_two" ;;
        NOBOUNDARY) emit UNVERIFIED A3 "compaction could not be forced in one session ($a_one); reply: $a_two" ;;
        LEAKSEED) emit UNVERIFIED A3 "$a_one, so the reply proves nothing about it" ;;
        *) emit UNVERIFIED A3 "$a_one" ;;
      esac
    else
      emit UNVERIFIED A3 "the compaction session failed: $why"
    fi
  fi
}

# --- load B: a plugin option reaches a subagent's commands ---------------------
P_B1='[probe B1] Run exactly this command with the Bash tool and reply with its output only: echo "PROBE_OPT=[${HARNESS_PROBE_OPTION}]"'
# The one command B2 hands the subagent: the prompt below and the analyzer's command
# check both read it from here, so they cannot drift. (B1 is a diagnostic that reads
# only the reply, not the command, and keeps its own literal.)
P_B2_CMD='echo "PROBE_OPT=[${HARNESS_PROBE_OPTION}]"'
P_B2_CMD_QUOTED=${P_B2_CMD//\"/\\\"}
P_B2="[probe B2] Use the Agent tool exactly once, with subagent_type \"general-purpose\" and run_in_background false, to start one foreground subagent and wait for its reply. Tell the subagent: \"Run exactly this command with the Bash tool and reply with its output only: ${P_B2_CMD_QUOTED}\". Do not run Bash yourself. When the subagent replies, reply with exactly what it replied and nothing else."

# A local marketplace with one plugin: a user option carrying the sentinel, and
# a SessionStart hook that appends an export line to the session's env file,
# which Claude Code applies to the session's Bash commands.
make_option_plugin() {
  local market=$1 plugin="$1/probe-option"
  mkdir -p "$market/.claude-plugin" "$plugin/.claude-plugin" "$plugin/hooks" || return 1
  cat >"$market/.claude-plugin/marketplace.json" <<'EOF'
{
  "name": "probe-marketplace",
  "owner": { "name": "probe" },
  "plugins": [
    { "name": "probe-option", "source": "./probe-option", "description": "Probe plugin: one user option exported to the session's commands." }
  ]
}
EOF
  cat >"$plugin/.claude-plugin/plugin.json" <<'EOF'
{
  "name": "probe-option",
  "version": "0.0.1",
  "description": "Probe plugin: one user option exported to the session's commands.",
  "userConfig": {
    "probe_value": {
      "type": "string",
      "title": "Probe value",
      "description": "A sentinel the probe reads back from a subagent's command."
    }
  }
}
EOF
  cat >"$plugin/hooks/hooks.json" <<'EOF'
{
  "hooks": {
    "SessionStart": [
      { "hooks": [ { "type": "command", "command": "sh \"${CLAUDE_PLUGIN_ROOT}/hooks/export-option.sh\"" } ] }
    ]
  }
}
EOF
  cat >"$plugin/hooks/export-option.sh" <<'EOF'
#!/bin/sh
# SessionStart: export the plugin option to the session's commands.
[ -n "${CLAUDE_ENV_FILE:-}" ] || exit 0
printf 'export HARNESS_PROBE_OPTION=%s\n' "${CLAUDE_PLUGIN_OPTION_PROBE_VALUE:-}" >>"$CLAUDE_ENV_FILE"
exit 0
EOF
}

group_b() {
  local value market step
  value="PROBE-OPT-$(rand_hex)"
  market="$target_home/probe-marketplace"
  if ! make_option_plugin "$market"; then
    unverify_group B 'could not write the local test plugin'
    return
  fi
  step="marketplace add"
  if session "$base_path" plugin marketplace add "$market"; then
    step="install"
    if session "$base_path" plugin install "$PROBE_PLUGIN@$PROBE_MARKET"; then
      step="configure"
      printf '{"probe_value":"%s"}\n' "$value" >"$scratch/option.json"
      stdin_file="$scratch/option.json"
      session "$base_path" plugin configure "$PROBE_PLUGIN@$PROBE_MARKET" --values-stdin && step=""
    fi
  fi
  if [ -n "$step" ]; then
    unverify_group B "the test plugin's $step step failed: $why"
    return
  fi

  # B1: the main thread's Bash tool.
  if session "$base_path" -p "$P_B1" --model "$MODEL" --max-turns 4 --no-session-persistence \
      --permission-mode dontAsk --allowedTools=Bash --tools=Bash; then
    analyze text "$out_file" "PROBE_OPT=[$value]"
    case "$a_code" in
      FOUND) emit PASS B1 "the main thread's Bash saw HARNESS_PROBE_OPTION=$a_one" ;;
      MISSING) emit FAIL B1 "the main thread's Bash saw HARNESS_PROBE_OPTION=[$a_one], expected [$value]; reply: $a_two" ;;
      *) emit UNVERIFIED B1 "$a_one" ;;
    esac
  else
    emit UNVERIFIED B1 "$why"
  fi

  # B2 (the verdict for B): a general-purpose subagent's Bash tool.
  if session "$base_path" -p "$P_B2" --model "$MODEL" --max-turns 6 --no-session-persistence \
      --output-format stream-json --verbose --permission-mode dontAsk \
      --allowedTools=Agent,Bash --tools=Agent,Bash; then
    analyze subagent "$out_file" "$value" "PROBE_OPT=[$value]" nested-bash "$P_B2_CMD"
    case "$a_code" in
      FOUND) emit PASS B2 "a general-purpose subagent's Bash saw HARNESS_PROBE_OPTION=$a_one (expected $value)" ;;
      MISSING) emit FAIL B2 "a general-purpose subagent's Bash saw HARNESS_PROBE_OPTION=[$a_one], expected [$value]; Bash output: $a_two" ;;
      *) emit UNVERIFIED B2 "$(route_not_run)" ;;
    esac
  else
    emit UNVERIFIED B2 "$why"
  fi
}

# --- load C: the ledger hooks are silent before setup -----------------------------
# The no-write check is a manifest taken before the session and one taken after
# it, over two roots: the target home and the probe's pinned ledger checkout (a
# plugin installed from a local-directory marketplace runs its hooks from that
# checkout). Every entry under both roots is recorded in full by a python3
# helper that walks by descriptor and follows no symlink, and the two manifests
# are compared for equality. Claude Code's own state is the only exemption, and
# it is not recorded: the top-level .claude folder and the top-level
# .claude.json files of the target home, never walked. Beside it, the target
# home root entry's own mtime_ns, ctime_ns, nlink and size are not compared (all
# four follow from the root's entry set, which is compared in full). The
# .cache and .npm folders, probe-work, probe-bin and the whole checkout are
# compared like any other entry. The probe holds each manifest's digest in a
# shell variable the session cannot reach, and the compare refuses a manifest
# that does not match it. Any error is UNVERIFIED, never PASS. The claim covers
# state left on disk by the session and its hooks once they have exited: a
# process a hook leaves running in the background is outside it. The ledger
# call recorder's log lives in probe-bin and is created, empty, before the
# first manifest, so it is in both and compared like any other file: an
# append, a truncation, a removal and a replacement are each a difference.
IFS= read -r -d '' SNAPSHOT_HELPER <<'PYEOF' || true
import hashlib, json, os, stat, sys

# One snapshot of both roots of load C as a manifest file, by descriptor: no
# path is looked up again after it was checked, and no symlink is followed.
# Usage: snapshot <manifest file> <target home> <ledger checkout>
# On success the manifest's sha256 is the only line printed; on any error the
# reason is one line on stderr and the exit status is 1.
VERSION = 1
CHUNK = 1 << 20
TYPES = {
    stat.S_IFREG: "reg",
    stat.S_IFDIR: "dir",
    stat.S_IFLNK: "lnk",
    stat.S_IFIFO: "fifo",
    stat.S_IFSOCK: "sock",
    stat.S_IFCHR: "chr",
    stat.S_IFBLK: "blk",
}


class SnapshotError(Exception):
    pass


def require(ok, message):
    if not ok:
        raise SnapshotError(message)


def kind(mode):
    return TYPES.get(stat.S_IFMT(mode), "other")


def same_object(left, right):
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def parts(path):
    return [piece for piece in path.split("/") if piece]


def inside(path, base):
    here, there = parts(path), parts(base)
    return here[:len(there)] == there


def physical(path, what):
    # The folder part resolved, the last name kept: a root that is itself a
    # symlink is refused when it is opened, never followed.
    require(os.path.isabs(path), "%s is not an absolute path" % what)
    folder, name = os.path.split(path.rstrip("/"))
    require(name, "%s has no final name" % what)
    return os.path.join(os.path.realpath(folder), name)


def exempt_home(name):
    # Claude Code's own state at the top of the target home, never walked.
    return name == ".claude" or name.startswith(".claude.json")


def record(path, info, target=None, digest=None):
    item = {
        "path": path,
        "type": kind(info.st_mode),
        "mode": info.st_mode,
        "uid": info.st_uid,
        "gid": info.st_gid,
        "nlink": info.st_nlink,
        "size": info.st_size,
        "mtime_ns": info.st_mtime_ns,
        "ctime_ns": info.st_ctime_ns,
        "ino": info.st_ino,
    }
    if target is not None:
        item["target"] = target
    if digest is not None:
        item["sha256"] = digest
    return item


def hash_file(dir_fd, name, first, path):
    # O_NONBLOCK so a file swapped for a fifo cannot block the open; the
    # descriptor must be the very regular file that was stat-ed.
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
    try:
        opened = os.fstat(fd)
        require(stat.S_ISREG(opened.st_mode) and same_object(opened, first),
                "a file changed between its stat and its open: " + path)
        digest = hashlib.sha256()
        while True:
            block = os.read(fd, CHUNK)
            if not block:
                break
            digest.update(block)
    finally:
        os.close(fd)
    again = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    require(same_object(again, first) and again.st_size == first.st_size
            and again.st_mtime_ns == first.st_mtime_ns and again.st_ctime_ns == first.st_ctime_ns,
            "a file changed while it was hashed: " + path)
    return digest.hexdigest()


def visit(dir_fd, rel, name, records, exempt):
    path = name if not rel else rel + "/" + name
    info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    if stat.S_ISDIR(info.st_mode):
        child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd)
        try:
            require(same_object(os.fstat(child), info),
                    "a folder changed between its stat and its open: " + path)
            records.append(record(path, info))
            walk(child, path, records, None)
        finally:
            os.close(child)
    elif stat.S_ISLNK(info.st_mode):
        records.append(record(path, info, target=os.readlink(name, dir_fd=dir_fd)))
    elif stat.S_ISREG(info.st_mode):
        records.append(record(path, info, digest=hash_file(dir_fd, name, info, path)))
    else:
        records.append(record(path, info))


def walk(dir_fd, rel, records, exempt):
    with os.scandir(dir_fd) as listing:
        names = sorted(entry.name for entry in listing)
    for name in names:
        if exempt is not None and exempt(name):
            continue
        visit(dir_fd, rel, name, records, exempt)


def walk_root(path, label, exempt):
    try:
        first = os.lstat(path)
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as error:
        raise SnapshotError("the %s root cannot be opened as a folder: %s" % (label, error))
    try:
        require(same_object(os.fstat(fd), first), "the %s root changed between its stat and its open" % label)
        records = [record(".", first)]
        walk(fd, "", records, exempt)
    finally:
        os.close(fd)
    return sorted(records, key=lambda item: item["path"])


def snapshot(out, home, checkout):
    home = physical(home, "the home root")
    checkout = physical(checkout, "the checkout root")
    require(not inside(home, checkout) and not inside(checkout, home), "the two roots overlap")
    target = physical(out, "the manifest path")
    require(not inside(target, home) and not inside(target, checkout), "the manifest path lies inside a root")
    document = {"version": VERSION, "roots": {
        "home": walk_root(home, "home", exempt_home),
        "checkout": walk_root(checkout, "checkout", None),
    }}
    data = json.dumps(document, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(descriptor, view):]
    finally:
        os.close(descriptor)
    return hashlib.sha256(data).hexdigest()


def main(argv):
    try:
        require(len(argv) == 4 and argv[0] == "snapshot",
                "usage: snapshot <manifest file> <target home> <ledger checkout>")
        digest = snapshot(argv[1], argv[2], argv[3])
    except Exception as error:
        text = str(error) if isinstance(error, SnapshotError) else "%s: %s" % (type(error).__name__, error)
        sys.stderr.write(" ".join(text.splitlines()) + "\n")
        return 1
    sys.stdout.write(digest + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
PYEOF

IFS= read -r -d '' MANIFEST_COMPARE <<'PYEOF' || true
import hashlib, json, re, sys

# The comparison of load C's two manifests. Each manifest file is read once, into
# memory; its digest is checked on that buffer and the same buffer is parsed.
# Usage: compare <before file> <after file> <before sha256> <after sha256>
# Output: SAME, or DIFF <n> then one line per difference, a path always printed
# as a JSON string so that no name can break a line:
#   <root> <path> added <type> | removed <type> | changed <field>,<field>...
# On any error nothing is printed on stdout, one line goes to stderr, exit 1.
VERSION = 1
ROOTS = ("checkout", "home")
RECORDED = ("type", "mode", "uid", "gid", "nlink", "size", "mtime_ns", "ctime_ns", "ino", "target", "sha256")
COMPARED = ("type", "mode", "uid", "gid", "nlink", "size", "mtime_ns", "ctime_ns", "ino", "target", "sha256")
NUMBERS = ("mode", "uid", "gid", "nlink", "size", "mtime_ns", "ctime_ns", "ino")
KINDS = ("reg", "dir", "lnk", "fifo", "sock", "chr", "blk", "other")
# On the target home's root entry only: all four follow from the root's entry
# set, which is compared in full.
HOME_ROOT_FIELDS = ("mtime_ns", "ctime_ns", "nlink", "size")
ABSENT = object()


class CompareError(Exception):
    pass


def require(ok, message):
    if not ok:
        raise CompareError(message)


def check_record(label, root, item):
    require(isinstance(item, dict), "%s manifest: a %s entry is not an object" % (label, root))
    require(isinstance(item.get("path"), str), "%s manifest: a %s entry has no path" % (label, root))
    require(item.get("type") in KINDS, "%s manifest: %s entry %s has no known type" % (label, root, item["path"]))
    for field in NUMBERS:
        require(type(item.get(field)) is int, "%s manifest: %s entry %s has no integer %s" % (
            label, root, item["path"], field))
    if "target" in item:
        require(isinstance(item["target"], str), "%s manifest: %s entry %s has a bad target" % (
            label, root, item["path"]))
    if "sha256" in item:
        require(isinstance(item["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", item["sha256"]),
                "%s manifest: %s entry %s has a bad sha256" % (label, root, item["path"]))


def load(label, path, digest):
    require(re.fullmatch(r"[0-9a-f]{64}", digest) is not None, "the %s digest is not a sha256" % label)
    with open(path, "rb") as handle:
        data = handle.read()
    require(hashlib.sha256(data).hexdigest() == digest, "the %s manifest does not match its digest" % label)
    try:
        document = json.loads(data.decode("ascii"))
    except ValueError as error:
        raise CompareError("the %s manifest does not parse: %s" % (label, error))
    require(isinstance(document, dict) and type(document.get("version")) is int and document["version"] == VERSION,
            "the %s manifest is not version %d" % (label, VERSION))
    roots = document.get("roots")
    require(isinstance(roots, dict), "the %s manifest has no roots" % label)
    found = {}
    for root in ROOTS:
        require(isinstance(roots.get(root), list), "the %s manifest lacks the %s root" % (label, root))
        found[root] = {}
        for item in roots[root]:
            check_record(label, root, item)
            require(item["path"] not in found[root], "the %s manifest lists %s twice" % (label, item["path"]))
            found[root][item["path"]] = item
    return found


def exempt(root, path, field):
    return root == "home" and path == "." and field in HOME_ROOT_FIELDS


def differences(before, after):
    lines = []
    for root in ROOTS:
        old, new = before[root], after[root]
        for path in sorted(set(old) | set(new)):
            if path not in old:
                text = "added " + new[path]["type"]
            elif path not in new:
                text = "removed " + old[path]["type"]
            else:
                names = [field for field in COMPARED
                         if not exempt(root, path, field)
                         and old[path].get(field, ABSENT) != new[path].get(field, ABSENT)]
                if not names:
                    continue
                text = "changed " + ",".join(names)
            lines.append("%s %s %s" % (root, json.dumps(path), text))
    return lines


def main(argv):
    try:
        require(len(argv) == 5 and argv[0] == "compare",
                "usage: compare <before file> <after file> <before sha256> <after sha256>")
        before = load("before", argv[1], argv[3])
        after = load("after", argv[2], argv[4])
        lines = differences(before, after)
    except Exception as error:
        text = str(error) if isinstance(error, CompareError) else "%s: %s" % (type(error).__name__, error)
        sys.stderr.write(" ".join(text.splitlines()) + "\n")
        return 1
    if lines:
        sys.stdout.write("DIFF %d\n" % len(lines) + "".join(line + "\n" for line in lines))
    else:
        sys.stdout.write("SAME\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
PYEOF

manifest_digest=""; manifest_err=""; manifest_diff=""

# take_manifest <before|after> <manifest file> <target home> <ledger checkout>:
# one snapshot of both roots by the python3 on the probe's own PATH (never the
# session's), under the same per-command alarm as git_run. Returns 0 with
# manifest_digest set to the sha256 of the manifest file the helper wrote; else
# 1 with manifest_err naming what went wrong. A timeout is an exit other than 0.
take_manifest() {
  local label=$1 out=$2 digest rc detail
  manifest_digest=""
  manifest_err=""
  digest="$(perl -e 'alarm shift; exec @ARGV' "$probe_timeout" \
    python3 -I -c "$SNAPSHOT_HELPER" snapshot "$out" "$3" "$4" 2>"$scratch/manifest.err")"
  rc=$?
  if [ "$rc" -ne 0 ]; then
    if [ "$rc" -eq 142 ]; then
      detail="timed out after ${probe_timeout}s"
    else
      detail="$(head -n 1 "$scratch/manifest.err" | cut -c1-300)"
      detail="exit $rc${detail:+: $detail}"
    fi
    manifest_err="the $label snapshot failed: $detail"
    return 1
  fi
  case "$digest" in
    *[!0-9a-f]*) digest="" ;;
  esac
  if [ "${#digest}" -ne 64 ] || [ ! -f "$out" ]; then
    manifest_err="the $label snapshot failed: the helper printed no sha256 of a manifest file"
    return 1
  fi
  manifest_digest=$digest
  return 0
}

# compare_manifests <before file> <after file> <before sha256> <after sha256>:
# returns 0 with manifest_diff empty (the manifests are equal) or set to
# "<n> difference(s): <the first 20 lines joined by "; "> (and <n-20> more)";
# returns 1 with manifest_err set when the compare failed or its output is not
# SAME, or DIFF <n> followed by exactly n non-empty lines.
compare_manifests() {
  local out rc detail first rest line n count shown blank
  manifest_err=""
  manifest_diff=""
  out="$(perl -e 'alarm shift; exec @ARGV' "$probe_timeout" \
    python3 -I -c "$MANIFEST_COMPARE" compare "$1" "$2" "$3" "$4" 2>"$scratch/manifest.err")"
  rc=$?
  if [ "$rc" -ne 0 ]; then
    if [ "$rc" -eq 142 ]; then
      detail="timed out after ${probe_timeout}s"
    else
      detail="$(head -n 1 "$scratch/manifest.err" | cut -c1-300)"
      detail="exit $rc${detail:+: $detail}"
    fi
    manifest_err="the manifest compare failed: $detail"
    return 1
  fi
  first="${out%%$'\n'*}"
  if [ "$first" = SAME ] && [ "$out" = SAME ]; then
    return 0
  fi
  n=""
  case "$first" in
    DIFF\ [1-9]*) n="${first#DIFF }" ;;
  esac
  case "$n" in
    *[!0-9]*) n="" ;;
  esac
  if [ -z "$n" ] || [ "${#n}" -gt 9 ] || [ "$out" = "$first" ]; then
    manifest_err="the manifest compare failed: its output is neither SAME nor DIFF <n> with its lines"
    return 1
  fi
  rest="${out#*$'\n'}"
  count=0
  blank=0
  shown=""
  while IFS= read -r line; do
    count=$((count + 1))
    [ -n "$line" ] || blank=1
    if [ "$count" -le 20 ]; then
      shown="${shown:+$shown; }$line"
    fi
  done <<<"$rest"
  if [ "$blank" -ne 0 ] || [ "$count" -ne "$n" ]; then
    manifest_err="the manifest compare failed: DIFF $n is followed by $count line(s)"
    return 1
  fi
  manifest_diff="$n difference(s): $shown"
  if [ "$n" -gt 20 ]; then
    manifest_diff="$manifest_diff (and $((n - 20)) more)"
  fi
  return 0
}

# git_run <args...>: git in the target home, with no user or system git
# configuration and no prompt, under the same time limit.
git_run() {
  (
    HOME="$target_home" GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_TERMINAL_PROMPT=0 \
      exec env -u CLAUDE_CODE_OAUTH_TOKEN perl -e 'alarm shift; exec @ARGV' "$probe_timeout" git "$@"
  )
}

group_c() {
  local repo url ledger_src head_sha market_name calls step spath bin_dir calls_log
  local before_m after_m before_digest after_digest m_err
  local plugin_root plugin_root_real shim_dir sh_log real_sh h_code h_one h_two s_code s_one s_two
  local src_root
  if [ ! -f "$pin_file" ]; then
    emit UNVERIFIED C 'ledger-vendor.pin is not next to this script, so the ledger repository is unknown'
    return
  fi
  repo="$(awk '$1 == "repo" { print $2; exit }' "$pin_file")"
  case "$repo" in
    ''|*[!A-Za-z0-9._/-]*|*/*/*|/*|*/) emit UNVERIFIED C 'ledger-vendor.pin names no usable ledger repository'; return ;;
    */*) ;;
    *) emit UNVERIFIED C 'ledger-vendor.pin names no usable ledger repository'; return ;;
  esac
  url="https://github.com/$repo.git"
  if ! command -v git >/dev/null 2>&1; then
    emit UNVERIFIED C 'git is not on PATH, so the ledger plugin cannot be fetched'
    return
  fi
  # Fetch the pinned commit itself, then refuse unless HEAD is exactly it.
  ledger_src="$scratch/ledger-src"
  if ! git_run init --quiet "$ledger_src" 2>"$err_file" \
      || ! git_run -C "$ledger_src" fetch --quiet --depth 1 "$url" "$LEDGER_PIN_SHA" 2>"$err_file" \
      || ! git_run -C "$ledger_src" -c advice.detachedHead=false checkout --quiet --detach FETCH_HEAD 2>"$err_file"; then
    emit UNVERIFIED C "could not fetch the ledger repository at $LEDGER_PIN_TAG ($LEDGER_PIN_SHA): $(head -n 1 "$err_file" | cut -c1-200)"
    return
  fi
  head_sha="$(git_run -C "$ledger_src" rev-parse HEAD 2>/dev/null)"
  if [ "$head_sha" != "$LEDGER_PIN_SHA" ]; then
    emit UNVERIFIED C "the fetched ledger repository is at ${head_sha:-no commit}, not the pinned $LEDGER_PIN_SHA, so its hooks are not run"
    return
  fi
  if [ ! -f "$ledger_src/.claude-plugin/marketplace.json" ] || [ ! -f "$ledger_src/plugins/$LEDGER_PLUGIN/hooks/hooks.json" ]; then
    emit UNVERIFIED C "the public ledger repository at $LEDGER_PIN_TAG ships no $LEDGER_PLUGIN hooks plugin (a later ledger row adds it)"
    return
  fi
  market_name="$(python3 -I -c '
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
names = [entry.get("name") for entry in data.get("plugins", [])]
print(data["name"] if sys.argv[2] in names else "")
' "$ledger_src/.claude-plugin/marketplace.json" "$LEDGER_PLUGIN" 2>/dev/null)"
  if [ -z "$market_name" ]; then
    emit UNVERIFIED C "the ledger marketplace at $LEDGER_PIN_TAG does not list $LEDGER_PLUGIN"
    return
  fi

  step="marketplace add"
  if session "$base_path" plugin marketplace add "$ledger_src"; then
    step="install"
    session "$base_path" plugin install "$LEDGER_PLUGIN@$market_name" && step=""
  fi
  if [ -n "$step" ]; then
    emit UNVERIFIED C "the ledger plugin's $step step failed: $why"
    return
  fi

  # The install folder, read from the install record Claude Code left in the
  # target home (never assumed), and the two hook scripts the plugin's hooks run
  # from it.
  if [ ! -f "$target_home/.claude/plugins/installed_plugins.json" ]; then
    emit UNVERIFIED C "the install of $LEDGER_PLUGIN left no install record in the target home, so its install folder is not known"
    return
  fi
  analyze installroot "$target_home/.claude/plugins/installed_plugins.json" "$LEDGER_PLUGIN@$market_name" "$target_home"
  if [ "$a_code" != ROOT ]; then
    emit UNVERIFIED C "the install folder of $LEDGER_PLUGIN could not be read from the install record: $a_one"
    return
  fi
  plugin_root=$a_one
  plugin_root_real=$a_two
  if [ ! -f "$plugin_root_real/hooks/session-start.sh" ] || [ ! -f "$plugin_root_real/hooks/prompt.sh" ]; then
    emit UNVERIFIED C "the install folder of $LEDGER_PLUGIN holds no hooks/session-start.sh and hooks/prompt.sh, so the hook runs to look for are not known"
    return
  fi

  # A ledger command that records every call stands on PATH, and no ledger
  # value is set: a hook that went past its "no values" check would call it.
  # Its log is created here, empty, beside it and before the before manifest, so
  # the log is in both manifests: a hook that calls the recorder and then removes,
  # empties or replaces the log changes an entry that was already recorded.
  bin_dir="$target_home/probe-bin"
  calls_log="$bin_dir/ledger-calls.log"
  mkdir -p "$bin_dir" \
    && printf '#!/bin/sh\nprintf "%%s\\n" "$*" >>"%s"\nexit 0\n' "$calls_log" >"$bin_dir/ledger" \
    && chmod 755 "$bin_dir/ledger" \
    && : >"$calls_log" \
    || { emit UNVERIFIED C 'could not place the ledger call recorder'; return; }

  # A recording sh stands before everything on PATH, in the probe's own scratch
  # folder (outside the target home): the plugin's hooks run as
  # sh "${CLAUDE_PLUGIN_ROOT}/hooks/<name>.sh", so a logged run of the installed
  # plugin's own scripts is what attributes a hook run to the plugin.
  shim_dir="$scratch/probe-shim"
  sh_log="$scratch/sh-calls.log"
  real_sh="$(PATH="$base_path" command -v sh 2>/dev/null)"
  case "$real_sh" in
    /*) ;;
    *) real_sh="" ;;
  esac
  if [ -z "$real_sh" ] || [ ! -x "$real_sh" ] || ! mkdir "$shim_dir" \
      || ! python3 -I -c "$SHIM_WRITER" "$shim_dir/sh" "$sh_log" "$real_sh" \
      || ! : >"$sh_log"; then
    emit UNVERIFIED C 'could not place the recording sh'
    return
  fi
  spath="$shim_dir:$bin_dir:$base_path"

  # The before snapshot is taken after everything the probe placed (the checkout,
  # the install, probe-bin, the recording sh) and before the session. A before
  # snapshot that fails is UNVERIFIED and the session is not run.
  before_m="$scratch/manifest.before.json"
  after_m="$scratch/manifest.after.json"
  if ! take_manifest before "$before_m" "$target_home" "$ledger_src"; then
    emit UNVERIFIED C "$manifest_err, so no session was run"
    return
  fi
  before_digest=$manifest_digest
  if ! session "$spath" -p '[probe C] Reply with the single word ACK.' --model "$MODEL" --max-turns 1 \
      --no-session-persistence --output-format stream-json --verbose --include-hook-events --tools ""; then
    emit UNVERIFIED C "the ledger session failed: $why"
    return
  fi
  # The after snapshot and the compare, whatever the stream shows: an error here
  # is kept and read in the verdict, after the ledger-call check.
  m_err=""
  if take_manifest after "$after_m" "$target_home" "$ledger_src"; then
    after_digest=$manifest_digest
    compare_manifests "$before_m" "$after_m" "$before_digest" "$after_digest" || m_err=$manifest_err
  else
    m_err=$manifest_err
  fi
  analyze hooks "$out_file"
  if [ "$a_code" = BAD ]; then
    emit UNVERIFIED C "$a_one"
    return
  fi
  h_code=$a_code; h_one=$a_one; h_two=$a_two
  # The second trusted root: the plugin folder inside the probe's own ledger
  # checkout. Only a checkout whose HEAD equals the pinned commit gets here (the
  # check above returns before any session otherwise).
  src_root="$ledger_src/plugins/$LEDGER_PLUGIN"
  analyze shimlog "$sh_log" "install:$plugin_root" "pinned checkout:$src_root"
  s_code=$a_code; s_one=$a_one; s_two=$a_two
  # The log is read only while it is still a regular file (a symlink, a fifo or a
  # folder put in its place is read as nothing, and the manifest names the change).
  calls=""
  if [ -f "$calls_log" ] && [ ! -L "$calls_log" ] && [ -s "$calls_log" ]; then
    calls="$(head -n 1 "$calls_log" | cut -c1-120)"
  fi
  if [ -n "$calls" ]; then
    emit FAIL C "with no ledger value set a hook called the ledger command: $calls"
  elif [ -n "$m_err" ]; then
    emit UNVERIFIED C "$m_err, so the claim that nothing was left written, changed or removed is not shown"
  elif [ -n "$manifest_diff" ]; then
    emit FAIL C "with no ledger value set the session changed the target home or the ledger checkout: $manifest_diff"
  elif [ "$s_code" != RAN ]; then
    emit UNVERIFIED C "the recording sh logged no run of $s_one under the install folder of $LEDGER_PLUGIN or its pinned checkout ($s_two), so no hook run of the plugin is shown"
  elif [ "$h_code" = NOEVENT ]; then
    emit UNVERIFIED C "the stream shows no $h_one hook event (hook responses seen: $h_two), so the exit and output of the logged hook run are not shown"
  elif [ "$h_code" = DIRTY ]; then
    emit FAIL C "with no ledger value set a hook was not silent (the stream names the event, not the plugin): $h_one"
  else
    emit PASS C "$LEDGER_PLUGIN $LEDGER_PIN_TAG (commit $LEDGER_PIN_SHA): the recording sh logged a run of hooks/session-start.sh and hooks/prompt.sh from the $s_two ($s_one), the stream's hook responses exited clean with no output ($h_one; seen: $h_two), the ledger command was never called, and no file was left written, changed or removed in the target home or the pinned ledger checkout outside Claude's own state (the .claude folder and the .claude.json files)"
  fi
}

selected A && group_a
selected B && group_b
selected C && group_c
finish
