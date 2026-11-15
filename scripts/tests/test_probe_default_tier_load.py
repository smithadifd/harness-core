"""Tests of scripts/probe-default-tier-load.sh, the default tier's load probe.

The probe runs real Claude Code sessions, so these tests put a stub `claude`
(and a stub `git` for load C) first on PATH. The stubs record every call (argv,
HOME, the variables that could redirect the config folder, whether the token
was the one given) and return canned replies chosen by a scenario. They read
the rules file the probe placed in its target home, run the plugin hook
script the probe wrote, and (load C) run the hooks of the plugin the probe
installed through the shell and the PATH it was given, as Claude Code does, so
a reply carries a sentinel, and a hook run is logged, only when the probe
really put it where the stub looks. No real Claude Code, no network and no
real home folder is touched: the invoking HOME is a scratch folder with a
canary in it.

The grammar under test (one line per sub-load, then a summary):

    <PASS|FAIL|UNVERIFIED> <id> — <evidence>
    summary: <n> pass, <n> fail, <n> unverified

with exit 0 (all PASS), 1 (any FAIL, no UNVERIFIED), 2 (any UNVERIFIED) or 3
(usage error, refused PROBE_HOME).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "probe-default-tier-load.sh"
BASH = shutil.which(os.environ.get("PROBE_TEST_BASH", "bash")) or "bash"
TOKEN = "tok-SECRET-0123456789abcdef"
LINE = re.compile(r"^(PASS|FAIL|UNVERIFIED) (\S+) — (.*)$")
SUMMARY = re.compile(r"^summary: (\d+) pass, (\d+) fail, (\d+) unverified$")
ALL_IDS = ["A1", "A2", "A3", "B1", "B2", "C"]
# The commit the probe pins the ledger plugin to (ledger tag v0.3.1).
PIN_SHA = "346cb3595180fcea702ebdd37b32ff8c36325ea5"
# Every variable the probe removes from each claude command: the world sets all
# of them, the stub records which are still set, and a test asserts none is.
SCRUBBED = [
    "CLAUDE_CONFIG_DIR", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
    "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME",
    "CLAUDE_ENV_FILE", "LEDGER_DB", "LEDGER_HARNESS", "LEDGER_OPERATOR",
]

CLAUDE_STUB = r'''#!__PYTHON__
import glob, json, os, re, shutil, subprocess, sys, tempfile, time

argv = sys.argv[1:]
home = os.environ.get("HOME", "")
scn = json.loads(os.environ.get("STUB_SCENARIO", "{}"))
state = os.environ["STUB_STATE"]
SCRUBBED = __SCRUBBED__
# The live run's refusal of a bare plugin id, and its launch notice for a
# background agent (cut where the live-run record cuts it).
BARE_ID_ERROR = ('No installed plugin has the id "%s". Use its full id (name@marketplace), '
                 'as `claude plugin list` shows it.')
LAUNCH_NOTICE = "Async agent launched successfully. (This tool result is internal metadata ...)"


def log(**extra):
    record = {
        "tool": "claude", "argv": argv, "home": home, "cwd": os.getcwd(),
        "config_dir_set": "CLAUDE_CONFIG_DIR" in os.environ,
        "api_key_set": "ANTHROPIC_API_KEY" in os.environ,
        "ledger_db_set": "LEDGER_DB" in os.environ,
        "scrubbed_present": [name for name in SCRUBBED if name in os.environ],
        "token_ok": os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") == os.environ.get("STUB_EXPECT_TOKEN"),
        "path0": os.environ.get("PATH", "").split(":")[0],
        "path_head": os.environ.get("PATH", "").split(":")[:2],
    }
    record.update(extra)
    with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def read_state(name, default=""):
    try:
        with open(os.path.join(state, name), encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return default


def write_state(name, text, mode="w"):
    os.makedirs(state, exist_ok=True)
    with open(os.path.join(state, name), mode, encoding="utf-8") as handle:
        handle.write(text)


def fail_step(step):
    if scn.get("fail_step") == step:
        sys.stderr.write("stub: %s failed\n" % step)
        sys.exit(9)


def tree(path):
    found = {}
    for base, _dirs, files in os.walk(path):
        for name in files:
            full = os.path.join(base, name)
            with open(full, encoding="utf-8", errors="replace") as handle:
                found[os.path.relpath(full, path)] = handle.read()
    return found


def home_files():
    return sorted(os.path.relpath(os.path.join(b, n), home) for b, _d, fs in os.walk(home) for n in fs)


def emit(event):
    print(json.dumps(event))


def tool_use(name, given, ident, parent=None):
    emit({"type": "assistant", "parent_tool_use_id": parent,
          "message": {"content": [{"type": "tool_use", "id": ident, "name": name, "input": given}]}})


def tool_result(ident, text, parent=None, is_error=False):
    block = {"type": "tool_result", "tool_use_id": ident, "content": text}
    if is_error:
        block["is_error"] = True
    emit({"type": "user", "parent_tool_use_id": parent, "message": {"content": [block]}})


def say(text):
    emit({"type": "assistant", "parent_tool_use_id": None,
          "message": {"content": [{"type": "text", "text": text}]}})


def finish(text, **kw):
    event = {"type": "result", "subtype": "success", "is_error": False, "result": text}
    event.update(kw)
    emit(event)


def rules_sentinel():
    for path in sorted(glob.glob(os.path.join(home, ".claude", "rules", "*.md"))):
        with open(path, encoding="utf-8") as handle:
            found = re.search(r"probe marker is (PROBE-RULES-[0-9a-f]+)", handle.read())
        if found:
            return found.group(1)
    return None


def option_value():
    installed = read_state("installed").split()
    options = read_state("options.json", "{}")
    market = read_state("market_path").strip()
    if not any(item.startswith("probe-option@") for item in installed) or not market:
        return ""
    value = json.loads(options).get("probe_value", "")
    script = os.path.join(market, "probe-option", "hooks", "export-option.sh")
    if not os.path.exists(script):
        return ""
    with tempfile.TemporaryDirectory() as scratch:
        env_file = os.path.join(scratch, "env")
        open(env_file, "w").close()
        env = dict(os.environ, CLAUDE_ENV_FILE=env_file, CLAUDE_PLUGIN_OPTION_PROBE_VALUE=value)
        subprocess.run(["sh", script], env=env, check=False)
        found = re.search(r"^export HARNESS_PROBE_OPTION=(.*)$", open(env_file).read(), re.M)
    return found.group(1) if found else ""


def install_ledger(plugin_id):
    """What `claude plugin install` leaves in the target home: the plugin's files in
    a cache folder (a name the probe could not guess) and an install record that
    names it."""
    sources = [p for p in read_state("added").split("\n")
               if p and os.path.isdir(os.path.join(p, "plugins", "ledger-hooks"))]
    if scn.get("c_no_install_record") or not sources:
        return
    if scn.get("c_install_outside"):
        root = os.path.join(state, "outside", "ledger-hooks")
    else:
        root = os.path.join(home, ".claude", "plugins", "cache", "ledger-plugins", "ledger-hooks", "build-7f3a")
    shutil.copytree(os.path.join(sources[-1], "plugins", "ledger-hooks"), root, dirs_exist_ok=True)
    entry = {"scope": "user", "installPath": root, "version": "0.3.1"}
    entries = [entry, dict(entry)] if scn.get("c_two_installs") else [entry]
    record = os.path.join(home, ".claude", "plugins", "installed_plugins.json")
    os.makedirs(os.path.dirname(record), exist_ok=True)
    with open(record, "w", encoding="utf-8") as handle:
        json.dump({"version": 2, "plugins": {plugin_id: entries}}, handle)


def installed_root():
    try:
        with open(os.path.join(home, ".claude", "plugins", "installed_plugins.json"), encoding="utf-8") as handle:
            record = json.load(handle)
    except (OSError, ValueError):
        return None
    for entries in record.get("plugins", {}).values():
        return entries[0]["installPath"]
    return None


def run_hooks(event_name):
    """Run what the installed plugin's hooks.json lists for an event the way Claude
    Code does: each command through a shell (which finds `sh` on the PATH it was
    given) with CLAUDE_PLUGIN_ROOT set and the event's JSON on stdin. Returns the
    exit code, stdout and stderr of the last command."""
    root = installed_root()
    result = (0, "", "")
    if not root:
        return result
    if scn.get("c_other_root"):
        other = os.path.join(state, "other-root", "ledger-hooks")
        shutil.copytree(root, other, dirs_exist_ok=True)
        root = other
    with open(os.path.join(root, "hooks", "hooks.json"), encoding="utf-8") as handle:
        listed = json.load(handle)["hooks"].get(event_name, [])
    for group in listed:
        for hook in group["hooks"]:
            command = hook["command"]
            if any(name in command for name in scn.get("c_omit_hook", [])):
                continue
            if scn.get("c_bypass_shim"):
                command = "/bin/" + command
            if scn.get("c_extra_arg"):
                command += " extra"
            done = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                input=json.dumps({"hook_event_name": event_name, "session_id": "stub"}),
                env=dict(os.environ, CLAUDE_PLUGIN_ROOT=root))
            result = (done.returncode, done.stdout, done.stderr)
    return result


def plugin_command():
    sub = argv[1] if len(argv) > 1 else ""
    if sub == "marketplace":
        path = argv[3]
        log(plugin="marketplace add", market_path=path, files=tree(path))
        fail_step("marketplace add")
        write_state("market_path", path if "probe-marketplace" in path else read_state("market_path"))
        write_state("added", path + "\n", "a")
    elif sub == "install":
        log(plugin="install", plugin_id=argv[2])
        fail_step("install")
        write_state("installed", argv[2] + "\n", "a")
        if argv[2].startswith("ledger-hooks@"):
            install_ledger(argv[2])
    elif sub == "configure":
        data = sys.stdin.read()
        log(plugin="configure", plugin_id=argv[2], stdin=data)
        fail_step("configure")
        if argv[2] not in read_state("installed").split():
            sys.stderr.write(BARE_ID_ERROR % argv[2] + "\n")
            sys.exit(1)
        write_state("options.json", data)
    else:
        log(plugin=sub)
    sys.exit(0)


def maybe_fail(tag):
    if scn.get("sleep", {}).get(tag):
        time.sleep(scn["sleep"][tag])
    code = scn.get("rc", {}).get(tag)
    if code:
        sys.stderr.write("stub: failing with the token %s\n" % os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", ""))
        sys.exit(code)


def stream_session():
    """A3: one process, three user messages on stdin (seed, /compact, question)."""
    texts = [json.loads(line)["message"]["content"] for line in sys.stdin.read().splitlines() if line.strip()]
    log(tag="A3", rules_sentinel=rules_sentinel(), inputs=texts, home_files=home_files())
    maybe_fail("A3")
    sentinel = rules_sentinel() or ""
    mode = scn.get("A3", "ok")
    emit({"type": "system", "subtype": "init"})
    if mode == "badjson":
        print("this line is not json")
    if mode == "boundary_early":
        emit({"type": "system", "subtype": "compact_boundary", "compact_metadata": {"trigger": "manual"}})
    late = mode == "boundary_late"
    for text in texts:
        if text == "/compact":
            if mode in ("noboundary", "boundary_late", "boundary_early"):
                finish("Not enough messages to compact.")
            elif mode == "fewresults":
                emit({"type": "system", "subtype": "compact_boundary", "compact_metadata": {"trigger": "manual"}})
            else:
                emit({"type": "system", "subtype": "compact_boundary", "compact_metadata": {"trigger": "manual"}})
                finish("compacted")
        elif text.startswith("[probe A3 seed]"):
            reply = sentinel if mode == "leak" else "READY"
            say(reply)
            finish(reply)
        else:
            reply = {"none": "NONE", "wrong": "PROBE-RULES-0000000000000000"}.get(mode, sentinel)
            say(reply)
            if mode == "errresult":
                emit({"type": "result", "subtype": "error_max_turns", "is_error": True, "result": "stopped"})
            else:
                finish(reply)
    if late:
        emit({"type": "system", "subtype": "compact_boundary", "compact_metadata": {"trigger": "manual"}})


def main():
    if argv and argv[0] == "plugin":
        plugin_command()
    if "--input-format" in argv:
        stream_session()
        return
    prompt = argv[argv.index("-p") + 1]
    found = re.match(r"\[probe ([^\]]+)\]", prompt)
    tag = found.group(1) if found else "?"
    log(tag=tag, rules_sentinel=rules_sentinel(), home_files=home_files())
    maybe_fail(tag)
    sentinel = rules_sentinel() or ""
    mode = scn.get(tag, "ok")
    if tag == "A1":
        reply = {"ok": sentinel, "none": "NONE", "wrong": "PROBE-RULES-0000000000000000"}.get(mode)
        if mode == "empty":
            sys.exit(0)
        if mode == "badutf8":
            sys.stdout.buffer.write(b"\xff\xfe\x00")
            sys.exit(0)
        if mode == "ctl":
            reply = sentinel + "\x07"
        if mode == "big":
            reply = sentinel + "x" * 1048700
        print(reply)
    elif tag in ("A2", "B2"):
        if tag == "A2":
            inner = sentinel if mode in (
                "ok", "okfg", "leak", "nospawn", "topbash", "nottgp", "noresult",
                "missingtype", "emptytype", "casetype", "twocalls", "twocalls_task",
                "tworesults", "noid") else "NONE"
            pattern = inner
        else:
            value = "" if mode == "empty" else option_value()
            pattern = "PROBE_OPT=[%s]" % value
        emit({"type": "system", "subtype": "init"})
        if mode == "badjson":
            print("this line is not json")
        if mode == "nospawn":
            emit({"type": "assistant", "parent_tool_use_id": None,
                  "message": {"content": [{"type": "text", "text": pattern}]}})
            finish(pattern)
            return
        given = {"subagent_type": {"nottgp": "Explore", "emptytype": "", "casetype": "General-Purpose"}.get(
                     mode, "general-purpose"),
                 "prompt": ("sentinel is %s" % sentinel) if mode == "leak" else "stub subagent prompt"}
        if mode == "missingtype":
            del given["subagent_type"]
        if mode in ("background", "bgflag"):
            given["run_in_background"] = True
        if mode == "okfg":
            given["run_in_background"] = False
        tool_use("Agent", given, None if mode == "noid" else "toolu_1")
        if mode in ("twocalls", "twocalls_task"):
            tool_use("Task" if mode == "twocalls_task" else "Agent", dict(given), "toolu_4")
        if mode == "foreign":
            tool_result("toolu_9", [{"type": "text", "text": sentinel}])
            pattern = "NONE"
        if mode == "topbash":
            tool_use("Bash", {"command": "echo hi"}, "toolu_2")
        tool_use("Bash", {"command": "echo inner"}, "toolu_3", parent="toolu_1")
        if mode in ("background", "launch_notice"):
            pattern = LAUNCH_NOTICE
        if mode != "noresult":
            tool_result(None if mode == "noid" else "toolu_1", [{"type": "text", "text": pattern}],
                        is_error=(mode == "agent_error"))
        if mode == "tworesults":
            tool_result("toolu_1", [{"type": "text", "text": pattern}])
        if mode == "errresult":
            emit({"type": "result", "subtype": "error_max_turns", "is_error": True, "result": "stopped"})
        else:
            finish(pattern)
    elif tag == "B1":
        value = "" if mode == "empty" else option_value()
        print("PROBE_OPT=[%s]" % value)
    elif tag == "C":
        emit({"type": "system", "subtype": "init"})
        # The hooks run first, as at session start and at the prompt. The stream
        # then names each response by its event, never by its plugin (the live
        # run's labels).
        runs = dict((name, run_hooks(name)) for name in ("SessionStart", "UserPromptSubmit"))
        if scn.get("c_call_ledger"):
            subprocess.run(["ledger", "resume", "--brief"], check=False)
        if scn.get("c_write"):
            target = os.path.join(home, ".local", "share", "ledger")
            os.makedirs(target, exist_ok=True)
            open(os.path.join(target, "ledger.db"), "w").close()
        os.makedirs(os.path.join(home, ".claude", "projects"), exist_ok=True)
        open(os.path.join(home, ".claude", "projects", "s.jsonl"), "w").close()
        open(os.path.join(home, ".claude.json"), "w").close()
        labels = {"SessionStart": "SessionStart:startup", "UserPromptSubmit": "UserPromptSubmit"}
        for name in ("SessionStart", "UserPromptSubmit"):
            if mode == "noevent_" + name:
                continue
            code, out_text, err_text = runs[name]
            event = {"type": "system", "subtype": "hook_response", "hook_event": name,
                     "hook_name": labels[name], "exit_code": code,
                     "outcome": "success" if code == 0 else "error", "stdout": out_text, "stderr": err_text}
            # A stream that names the plugin in the hook's name: it is no attribution.
            if mode == "ledger_label":
                event["hook_name"] = name + ":ledger-hooks"
            if mode == "dirty_stdout" and name == "SessionStart":
                event["stdout"] = "ledger-hooks: ledger resume --brief exited 2"
            if mode == "dirty_stderr" and name == "UserPromptSubmit":
                event["stderr"] = "ledger-hooks: prompt capture failed (exit 1)"
            if mode == "dirty_outcome" and name == "SessionStart":
                event["outcome"] = "cancelled"
            if mode == "dirty_exit" and name == "UserPromptSubmit":
                event["exit_code"] = 2
                event["outcome"] = "blocking"
            if mode == "no_exit_code":
                del event["exit_code"]
            emit(event)
        # A second source's response of the same event, which is not clean.
        if mode == "foreign_dirty_extra":
            emit({"type": "system", "subtype": "hook_response", "hook_event": "SessionStart",
                  "hook_name": "SessionStart:startup", "exit_code": 2, "outcome": "blocking",
                  "stdout": "other plugin noise", "stderr": ""})
        finish("ACK")
    else:
        print("stub: unknown prompt", file=sys.stderr)
        sys.exit(64)


main()
'''

GIT_STUB = r'''#!__PYTHON__
import json, os, shutil, sys

argv = sys.argv[1:]
args = list(argv)
workdir = None
while args and args[0] in ("-C", "-c"):
    if args[0] == "-C":
        workdir = args[1]
    args = args[2:]
verb = args[0] if args else ""
with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps({
        "tool": "git", "argv": argv, "verb": verb, "workdir": workdir,
        "home": os.environ.get("HOME", ""),
        "config_global": os.environ.get("GIT_CONFIG_GLOBAL"),
        "token_set": "CLAUDE_CODE_OAUTH_TOKEN" in os.environ}) + "\n")
state = os.environ["STUB_STATE"]
os.makedirs(state, exist_ok=True)
fetched = os.path.join(state, "git_fetched")
if os.environ.get("STUB_GIT_FAIL") == verb:
    sys.stderr.write("stub: %s failed\n" % verb)
    sys.exit(128)
if verb == "init":
    os.makedirs(args[-1], exist_ok=True)
elif verb == "fetch":
    with open(fetched, "w", encoding="utf-8") as handle:
        handle.write(args[-1])
elif verb == "checkout":
    shutil.copytree(os.environ["STUB_LEDGER_FIXTURE"], workdir, dirs_exist_ok=True)
elif verb == "rev-parse":
    print(os.environ.get("STUB_GIT_HEAD") or open(fetched, encoding="utf-8").read())
else:
    sys.exit(64)
'''

PIN = (
    "# fixture pin\n"
    "repo example/ledger\n"
    "tag v0.1.13\n"
    "sha da439e9eca5de9537275ad55103b3cff85383ec7\n"
)


def write_exec(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.replace("__PYTHON__", sys.executable).replace("__SCRUBBED__", repr(SCRUBBED)),
                    encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


# The ledger-hooks plugin as the pinned commit ships it: every hook is
# sh "${CLAUDE_PLUGIN_ROOT}/hooks/<name>.sh". Each script here is silent and
# notes its own name, argument count, what `command -v ledger` finds and its
# stdin in a folder outside the target home, for a test to read.
HOOKS_JSON = {"hooks": {
    "SessionStart": [{"hooks": [{"type": "command", "command": 'sh "${CLAUDE_PLUGIN_ROOT}/hooks/session-start.sh"'}]}],
    "UserPromptSubmit": [{"hooks": [{"type": "command", "command": 'sh "${CLAUDE_PLUGIN_ROOT}/hooks/prompt.sh"'}]}],
}}
HOOK_SCRIPT = r'''#!/bin/sh
[ -n "${STUB_STATE:-}" ] || exit 0
mkdir -p "$STUB_STATE/hooks"
{
  printf '%s\n' "$0" "$#" "$(command -v ledger || echo none)"
  cat
} >"$STUB_STATE/hooks/NAME"
exit 0
'''


def ledger_fixture(root: Path, with_plugin: bool = True) -> Path:
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "marketplace.json").write_text(json.dumps({
        "name": "ledger-plugins", "owner": {"name": "x"},
        "plugins": [{"name": "ledger-hooks", "source": "./plugins/ledger-hooks"}]}))
    if with_plugin:
        hooks = root / "plugins" / "ledger-hooks" / "hooks"
        hooks.mkdir(parents=True)
        (hooks / "hooks.json").write_text(json.dumps(HOOKS_JSON))
        for script, name in (("session-start.sh", "session-start"), ("prompt.sh", "prompt")):
            (hooks / script).write_text(HOOK_SCRIPT.replace("NAME", name))
    return root


class Result:
    def __init__(self, proc, calls, world):
        self.proc = proc
        self.rc = proc.returncode
        self.out = proc.stdout
        self.err = proc.stderr
        self.calls = calls
        self.world = world
        self.verdicts = {}
        self.summary = None
        for line in self.out.splitlines():
            found = LINE.match(line)
            if found:
                self.verdicts[found.group(2)] = (found.group(1), found.group(3))
            elif SUMMARY.match(line):
                self.summary = tuple(int(n) for n in SUMMARY.match(line).groups())

    def status(self, ident):
        return self.verdicts[ident][0]

    def evidence(self, ident):
        return self.verdicts[ident][1]

    def claude(self):
        return [call for call in self.calls if call["tool"] == "claude"]

    def tagged(self, tag):
        return [call for call in self.claude() if call.get("tag") == tag]


class World:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.repo = tmp / "repo"
        self.script = self.repo / "scripts" / "probe-default-tier-load.sh"
        self.script.parent.mkdir(parents=True)
        shutil.copyfile(SCRIPT, self.script)
        (self.repo / "ledger-vendor.pin").write_text(PIN)
        self.bin = tmp / "bin"
        write_exec(self.bin / "claude", CLAUDE_STUB)
        write_exec(self.bin / "git", GIT_STUB)
        self.real_home = tmp / "real_home"
        (self.real_home / ".claude").mkdir(parents=True)
        (self.real_home / ".claude" / "CANARY").write_text("untouched")
        self.tmpdir = tmp / "tmpdir"
        self.tmpdir.mkdir()
        self.state = tmp / "state"
        self.log = tmp / "calls.jsonl"
        self.fixture = ledger_fixture(tmp / "ledger-fixture")
        self.runs = 0

    def tools(self, without=()) -> Path:
        """A folder of links to the few real tools the probe needs, and to
        neither a real claude nor a real git."""
        folder = self.tmp / ("tools" + "".join("-without-" + name for name in without))
        if not folder.exists():
            folder.mkdir()
            names = ["env", "perl", "find", "sort", "comm", "head", "cut", "tr", "wc", "awk",
                     "mktemp", "dirname", "cat", "mkdir", "chmod", "grep", "sh", "sleep", "rm"]
            for name in names:
                found = shutil.which(name)
                assert found, name
                if name not in without:
                    (folder / name).symlink_to(found)
            if "python3" not in without:
                (folder / "python3").symlink_to(sys.executable)
        return folder

    def canary(self):
        return sorted(str(p.relative_to(self.real_home)) for p in self.real_home.rglob("*"))

    def run(self, *args, scenario=None, env=None, token=TOKEN, stubs=True, home=None,
            unset=(), without=()):
        self.runs += 1
        self.log.write_text("")
        shutil.rmtree(self.state, ignore_errors=True)
        before = self.canary()
        path = os.pathsep.join(([str(self.bin)] if stubs else []) + [str(self.tools(without))])
        environ = {
            "PATH": path, "HOME": str(home or self.real_home), "TMPDIR": str(self.tmpdir),
            "STUB_LOG": str(self.log), "STUB_STATE": str(self.state), "STUB_EXPECT_TOKEN": TOKEN,
            "STUB_SCENARIO": json.dumps(scenario or {}),
            "STUB_LEDGER_FIXTURE": str(self.fixture),
            # Variables that could send a session to another config folder or
            # credential, or arm the ledger: the probe must remove each.
            "CLAUDE_CONFIG_DIR": str(self.real_home / ".claude"), "ANTHROPIC_API_KEY": "api-key-x",
            "LEDGER_DB": "/should/not/be/seen",
        }
        for name in SCRUBBED:
            environ.setdefault(name, "scrubbed-" + name)
        if token is not None:
            environ["CLAUDE_CODE_OAUTH_TOKEN"] = token
        environ.update(env or {})
        for name in unset:
            environ.pop(name, None)
        proc = subprocess.run([BASH, str(self.script), *args], env=environ, capture_output=True,
                              text=True, timeout=120, cwd=self.tmp)
        calls = [json.loads(line) for line in self.log.read_text().splitlines() if line]
        # Whatever happens, the token never reaches the probe's output or any argv,
        # and the invoking user's own folder is never changed.
        assert TOKEN not in proc.stdout and TOKEN not in proc.stderr
        for call in calls:
            assert TOKEN not in json.dumps(call["argv"])
        assert self.canary() == before
        assert (self.real_home / ".claude" / "CANARY").read_text() == "untouched"
        return Result(proc, calls, self)


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def test_every_load_passes_with_the_sentinel_each_route_returned(world):
    result = world.run()
    assert result.rc == 0, result.out + result.err
    assert [i for i in ALL_IDS if i in result.verdicts] == ALL_IDS
    assert {i: result.status(i) for i in ALL_IDS} == {i: "PASS" for i in ALL_IDS}
    # five counted lines: B1 is a diagnostic and B2 carries the verdict for B.
    assert result.summary == (5, 0, 0)
    assert "diagnostic, not counted" in result.evidence("B1")
    sentinel = result.tagged("A1")[0]["rules_sentinel"]
    assert sentinel and sentinel.startswith("PROBE-RULES-")
    for ident in ("A1", "A2", "A3"):
        assert sentinel in result.evidence(ident)
    # B2 prints the exact value seen, and it is the option value set through configure.
    configured = json.loads(next(c for c in result.calls if c.get("plugin") == "configure")["stdin"])
    assert configured["probe_value"].startswith("PROBE-OPT-")
    assert f"HARNESS_PROBE_OPTION={configured['probe_value']}" in result.evidence("B2")
    assert "ledger-hooks v0.3.1" in result.evidence("C")


def test_sentinels_are_fresh_for_every_run(world):
    first = world.run("--only", "A").tagged("A1")[0]["rules_sentinel"]
    second = world.run("--only", "A").tagged("A1")[0]["rules_sentinel"]
    assert first != second


@pytest.mark.parametrize("mode, status, words", [
    ("none", "FAIL", "lacks the rules sentinel"), ("wrong", "FAIL", "lacks the rules sentinel"),
    ("empty", "UNVERIFIED", "empty reply"), ("badutf8", "UNVERIFIED", "not readable UTF-8"),
    ("ctl", "UNVERIFIED", "control character"),
])
def test_a1_reply_without_the_sentinel_never_passes(world, mode, status, words):
    result = world.run("--only", "A", scenario={"A1": mode})
    assert result.status("A1") == status, result.out
    assert words in result.evidence("A1")
    assert result.rc == (1 if status == "FAIL" and "UNVERIFIED" not in result.out else 2)


def test_a1_fail_names_the_reply(world):
    result = world.run("--only", "A", scenario={"A1": "none"})
    assert "reply: NONE" in result.evidence("A1")


@pytest.mark.parametrize("mode, status, words", [
    ("none", "FAIL", "lacks the rules sentinel"),
    ("nospawn", "UNVERIFIED", "started no subagent"),
    ("leak", "UNVERIFIED", "carried the sentinel"),
    ("topbash", "UNVERIFIED", "ran Bash itself"),
    ("nottgp", "UNVERIFIED", "not general-purpose"),
    ("noresult", "UNVERIFIED", "no result in the stream"),
    ("foreign", "FAIL", "lacks the rules sentinel"),
    ("badjson", "UNVERIFIED", "is not JSON"),
    ("errresult", "UNVERIFIED", "error_max_turns"),
])
def test_a2_passes_only_through_a_general_purpose_subagent(world, mode, status, words):
    result = world.run("--only", "A", scenario={"A2": mode})
    assert result.status("A2") == status, result.out
    assert words in result.evidence("A2")


def test_a2_asks_for_an_agent_tool_and_no_other_tool(world):
    call = world.run("--only", "A").tagged("A2")[0]
    argv = call["argv"]
    assert "--tools=Agent" in argv and "--allowedTools=Agent" in argv
    assert argv[argv.index("--model") + 1] == "claude-haiku-5-5"
    assert "general-purpose" in argv[argv.index("-p") + 1]


def test_a3_compacts_inside_one_process_that_takes_the_three_messages(world):
    result = world.run("--only", "A")
    a3 = result.tagged("A3")
    assert len(a3) == 1, "seed, /compact and the question are one claude process"
    call = a3[0]
    argv = call["argv"]
    assert argv[argv.index("--input-format") + 1] == "stream-json"
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    for resumed in ("--resume", "--continue", "--session-id", "--no-session-persistence"):
        assert resumed not in argv
    seed, compact, ask = call["inputs"]
    assert seed.startswith("[probe A3 seed]") and compact == "/compact" and ask.startswith("[probe A3 ask]")
    assert call["rules_sentinel"] not in " ".join(call["inputs"]), "the sentinel is never in a message"
    assert len(call["inputs"]) == 3
    assert [c["tag"] for c in result.claude() if c.get("tag", "").startswith("A")] == ["A1", "A2", "A3"]
    assert result.status("A3") == "PASS" and "compact_boundary seen" in result.evidence("A3")
    assert call["rules_sentinel"] in result.evidence("A3")


def test_b_passes_on_the_subagent_and_prints_the_value_seen(world):
    result = world.run("--only", "B")
    assert result.status("B1") == "PASS" and result.status("B2") == "PASS"
    assert result.rc == 0 and result.summary == (1, 0, 0)
    value = json.loads(next(c for c in result.calls if c.get("plugin") == "configure")["stdin"])["probe_value"]
    assert f"saw HARNESS_PROBE_OPTION={value} (expected {value})" in result.evidence("B2")


def test_b_installs_a_local_plugin_that_exports_the_option_to_commands(world):
    result = world.run("--only", "B")
    steps = [(c["plugin"], c.get("plugin_id") or c.get("market_path")) for c in result.calls if c.get("plugin")]
    assert [s[0] for s in steps] == ["marketplace add", "install", "configure"]
    market = next(c for c in result.calls if c.get("plugin") == "marketplace add")
    assert Path(market["market_path"]).name == "probe-marketplace"
    manifest = json.loads(market["files"]["probe-option/.claude-plugin/plugin.json"])
    assert "probe_value" in manifest["userConfig"]
    hooks = json.loads(market["files"]["probe-option/hooks/hooks.json"])
    assert "SessionStart" in hooks["hooks"]
    script = market["files"]["probe-option/hooks/export-option.sh"]
    assert "CLAUDE_ENV_FILE" in script and "CLAUDE_PLUGIN_OPTION_PROBE_VALUE" in script
    assert next(c for c in result.calls if c.get("plugin") == "install")["plugin_id"] == (
        "probe-option@probe-marketplace")
    configure = next(c for c in result.calls if c.get("plugin") == "configure")
    assert configure["argv"][-1] == "--values-stdin"
    assert configure["plugin_id"] == "probe-option@probe-marketplace"
    assert result.status("B2") == "PASS", "the stub refuses a bare id with the live run's message"


def test_b_b1_is_a_diagnostic_and_does_not_change_the_verdict(world):
    result = world.run("--only", "B", scenario={"B1": "empty"})
    assert result.status("B1") == "FAIL" and result.status("B2") == "PASS"
    assert "diagnostic, not counted" in result.evidence("B1")
    assert result.rc == 0 and result.summary == (1, 0, 0)


def test_b2_fails_and_shows_the_empty_value_when_the_subagent_does_not_see_the_option(world):
    result = world.run("--only", "B", scenario={"B2": "empty"})
    assert result.status("B2") == "FAIL"
    assert "HARNESS_PROBE_OPTION=[]" in result.evidence("B2")
    assert result.rc == 1


def test_b2_is_unverified_when_the_main_thread_ran_bash_itself(world):
    result = world.run("--only", "B", scenario={"B2": "topbash"})
    assert result.status("B2") == "UNVERIFIED"
    assert "ran Bash itself" in result.evidence("B2")
    assert result.rc == 2


@pytest.mark.parametrize("step", ["marketplace add", "install", "configure"])
def test_b_is_unverified_when_the_plugin_cannot_be_set_up(world, step):
    result = world.run("--only", "B", scenario={"fail_step": step})
    assert result.status("B1") == "UNVERIFIED" and result.status("B2") == "UNVERIFIED"
    assert f"{step} step failed" in result.evidence("B2")
    assert result.tagged("B2") == []
    assert result.rc == 2


def test_c_passes_when_hooks_run_clean_and_nothing_is_written(world):
    result = world.run("--only", "C")
    assert result.status("C") == "PASS", result.out
    assert "SessionStart=1, UserPromptSubmit=1" in result.evidence("C")
    session = result.tagged("C")[0]
    # the recording sh stands first on PATH, then the tripwire ledger command,
    # and no ledger value is set.
    assert session["path_head"][0].endswith("probe-shim") and session["path_head"][1].endswith("probe-bin")
    assert session["ledger_db_set"] is False
    assert "--include-hook-events" in session["argv"]


def test_c_fetches_the_pinned_commit_and_checks_it_out(world):
    result = world.run("--only", "C")
    git = [c for c in result.calls if c["tool"] == "git"]
    assert [c["verb"] for c in git] == ["init", "fetch", "checkout", "rev-parse"]
    fetch = git[1]
    assert fetch["argv"][-2:] == ["https://github.com/example/ledger.git", PIN_SHA]
    assert not any("--branch" in c["argv"] or "ls-remote" in c["argv"] or "clone" in c["argv"] for c in git)
    install = next(c for c in result.calls if c.get("plugin") == "install")
    assert install["plugin_id"] == "ledger-hooks@ledger-plugins"
    assert f"ledger-hooks v0.3.1 (commit {PIN_SHA})" in result.evidence("C")


def test_c_has_no_environment_override_of_the_pinned_commit(world):
    result = world.run("--only", "C", env={"PROBE_LEDGER_TAG": "v9.9.9", "PROBE_LEDGER_SHA": "f" * 40})
    fetch = next(c for c in result.calls if c["tool"] == "git" and c["verb"] == "fetch")
    assert fetch["argv"][-1] == PIN_SHA
    assert result.status("C") == "PASS", result.out


def test_c_git_never_sees_the_token_or_the_users_git_config(world):
    result = world.run("--only", "C")
    git = [c for c in result.calls if c["tool"] == "git"]
    assert git and all(not c["token_set"] and c["config_global"] == "/dev/null" for c in git)
    assert all(c["home"] != str(world.real_home) for c in git)


@pytest.mark.parametrize("mode, words", [
    ("dirty_stdout", "SessionStart printed on stdout"),
    ("dirty_stderr", "UserPromptSubmit printed on stderr"),
    ("dirty_exit", "UserPromptSubmit exited 2"),
    ("dirty_outcome", "SessionStart outcome cancelled"),
])
def test_c_fails_when_a_hook_is_not_silent(world, mode, words):
    result = world.run("--only", "C", scenario={"C": mode})
    assert result.status("C") == "FAIL"
    assert words in result.evidence("C")
    assert result.rc == 1


def test_c_fails_when_a_hook_calls_the_ledger_command_with_no_value_set(world):
    result = world.run("--only", "C", scenario={"c_call_ledger": True})
    assert result.status("C") == "FAIL"
    assert "called the ledger command: resume --brief" in result.evidence("C")


def test_c_fails_when_the_session_writes_a_file_outside_claudes_own_state(world):
    result = world.run("--only", "C", scenario={"c_write": True})
    assert result.status("C") == "FAIL"
    assert "./.local/share/ledger/ledger.db" in result.evidence("C")


@pytest.mark.parametrize("mode, event", [
    ("noevent_SessionStart", "SessionStart"), ("noevent_UserPromptSubmit", "UserPromptSubmit"),
])
def test_c_is_unverified_when_a_hook_run_is_not_shown(world, mode, event):
    result = world.run("--only", "C", scenario={"C": mode})
    assert result.status("C") == "UNVERIFIED"
    assert f"no {event} hook event" in result.evidence("C")
    assert result.rc == 2


def test_c_is_unverified_when_a_hook_event_has_no_exit_code(world):
    result = world.run("--only", "C", scenario={"C": "no_exit_code"})
    assert result.status("C") == "UNVERIFIED"
    assert "no exit_code" in result.evidence("C")


def test_c_is_unverified_when_the_ledger_tag_ships_no_hooks_plugin(world):
    shutil.rmtree(world.fixture)
    ledger_fixture(world.fixture, with_plugin=False)
    result = world.run("--only", "C")
    assert result.status("C") == "UNVERIFIED"
    assert "ships no ledger-hooks hooks plugin" in result.evidence("C")
    assert not any(c.get("plugin") for c in result.calls)
    assert result.rc == 2


@pytest.mark.parametrize("fail", ["init", "fetch", "checkout"])
def test_c_is_unverified_when_the_pinned_commit_cannot_be_fetched(world, fail):
    result = world.run("--only", "C", env={"STUB_GIT_FAIL": fail})
    assert result.status("C") == "UNVERIFIED"
    assert f"could not fetch the ledger repository at v0.3.1 ({PIN_SHA})" in result.evidence("C")
    assert not any(c.get("plugin") for c in result.calls)
    assert result.rc == 2


def test_c_is_unverified_when_the_fetched_head_is_not_the_pinned_commit(world):
    result = world.run("--only", "C", env={"STUB_GIT_HEAD": "f" * 40})
    assert result.status("C") == "UNVERIFIED"
    assert f"is at {'f' * 40}, not the pinned {PIN_SHA}" in result.evidence("C")
    assert not any(c.get("plugin") for c in result.calls), "no ledger plugin is installed"
    assert result.tagged("C") == [], "no session runs the unpinned code"
    assert result.rc == 2


def test_c_is_unverified_without_a_pin_file(world):
    (world.repo / "ledger-vendor.pin").unlink()
    result = world.run("--only", "C")
    assert result.status("C") == "UNVERIFIED"
    assert "ledger-vendor.pin is not next to this script" in result.evidence("C")


def test_c_is_unverified_when_the_ledger_plugin_cannot_be_installed(world):
    result = world.run("--only", "C", scenario={"fail_step": "install"})
    assert result.status("C") == "UNVERIFIED"
    assert "install step failed" in result.evidence("C")
    assert result.tagged("C") == []


def test_exit_code_is_2_when_any_load_is_unverified_even_with_a_fail(world):
    result = world.run("--only", "A", scenario={"A1": "none", "A2": "nospawn"})
    assert (result.status("A1"), result.status("A2")) == ("FAIL", "UNVERIFIED")
    assert result.rc == 2
    assert result.summary == (1, 1, 1)


def test_exit_code_is_1_for_a_fail_with_no_unverified(world):
    result = world.run("--only", "A", scenario={"A1": "none"})
    assert result.summary == (2, 1, 0)
    assert result.rc == 1


def test_without_a_token_every_load_is_unverified_and_no_session_starts(world):
    result = world.run(token=None)
    assert result.rc == 2
    assert [i for i in ALL_IDS if i in result.verdicts] == ALL_IDS
    assert all(result.status(i) == "UNVERIFIED" for i in ALL_IDS)
    assert all(result.evidence(i).endswith("no CLAUDE_CODE_OAUTH_TOKEN") for i in ALL_IDS)
    assert result.calls == []
    assert list(world.tmpdir.iterdir()) == []


def test_an_empty_token_counts_as_no_token(world):
    result = world.run(token="", env={"STUB_EXPECT_TOKEN": ""})
    assert result.rc == 2 and result.calls == []
    assert result.evidence("A1").endswith("no CLAUDE_CODE_OAUTH_TOKEN")


def test_without_a_token_only_the_selected_loads_are_reported(world):
    result = world.run("--only", "B", token=None)
    assert sorted(result.verdicts) == ["B1", "B2"]
    assert result.rc == 2


def test_without_claude_on_path_every_load_is_unverified(world):
    result = world.run(stubs=False)
    assert result.rc == 2
    assert all(result.status(i) == "UNVERIFIED" for i in ALL_IDS)
    assert all("claude is not on PATH" in result.evidence(i) for i in ALL_IDS)


def test_without_python3_on_path_every_load_is_unverified(world):
    result = world.run(without=("python3",))
    assert result.rc == 2 and result.calls == []
    assert all(result.status(i) == "UNVERIFIED" for i in ALL_IDS)
    assert all("python3 and perl are both needed" in result.evidence(i) for i in ALL_IDS)


def test_every_call_runs_in_the_target_home_never_the_real_one(world):
    result = world.run()
    calls = result.calls
    assert len(calls) > 10
    homes = {call["home"] for call in calls}
    assert len(homes) == 1
    (home,) = homes
    assert Path(home).resolve() != world.real_home.resolve()
    assert Path(home).resolve().parent.parent == world.tmpdir.resolve()
    for call in calls:
        assert call["home"] == home
        if call["tool"] == "claude":
            assert call["config_dir_set"] is False
            assert call["api_key_set"] is False
            assert call["token_ok"] is True
            assert call["cwd"].startswith(home)


def test_the_probe_makes_its_own_temp_home_and_removes_it_on_exit(world):
    result = world.run()
    home = Path(result.claude()[0]["home"])
    assert not home.exists()
    assert list(world.tmpdir.iterdir()) == []


def test_the_temp_home_is_removed_when_sessions_fail_too(world):
    result = world.run(scenario={"rc": {"A1": 7, "A2": 7, "A3": 7, "B1": 7, "B2": 7, "C": 7}})
    assert result.rc == 2
    assert list(world.tmpdir.iterdir()) == []


def test_a_probe_home_the_caller_chose_is_used_and_left_in_place(world, tmp_path):
    chosen = tmp_path / "chosen" / "fresh"
    result = world.run("--only", "A", env={"PROBE_HOME": str(chosen)})
    assert result.rc == 0, result.out
    assert chosen.is_dir()
    assert {Path(c["home"]).resolve() for c in result.calls} == {chosen.resolve()}
    assert (chosen / ".claude" / "rules" / "probe-load-rules.md").is_file()
    assert list(world.tmpdir.iterdir()) == [], "the probe's own scratch folder is still removed"


REASONS = {
    "real-home": "the invoking user's own HOME", "real-home-symlink": "the invoking user's own HOME",
    "relative": "not an absolute path", "empty": "PROBE_HOME is empty", "root": "the root folder",
    "dotdot": "has a .. component", "has-rules": "already holds .claude/rules",
    "claude-symlink": ".claude is a symlink", "not-a-folder": "exists and is not a folder",
}


@pytest.mark.parametrize("kind", list(REASONS))
def test_a_refused_probe_home_exits_3_before_any_session(world, tmp_path, kind):
    candidates = {
        "real-home": str(world.real_home),
        "real-home-symlink": str(tmp_path / "link"),
        "relative": "some/relative/dir",
        "empty": "",
        "root": "/",
        "dotdot": str(tmp_path / "x" / ".." / "y"),
        "has-rules": str(tmp_path / "occupied"),
        "claude-symlink": str(tmp_path / "linked-claude"),
        "not-a-folder": str(tmp_path / "afile"),
    }
    (tmp_path / "link").symlink_to(world.real_home)
    (tmp_path / "occupied" / ".claude" / "rules").mkdir(parents=True)
    (tmp_path / "linked-claude").mkdir()
    (tmp_path / "linked-claude" / ".claude").symlink_to(world.real_home / ".claude")
    (tmp_path / "afile").write_text("x")
    result = world.run(env={"PROBE_HOME": candidates[kind]})
    assert result.rc == 3, result.out + result.err
    assert "REFUSED" in result.err and REASONS[kind] in result.err
    assert result.out == ""
    assert result.calls == []
    assert list(world.tmpdir.iterdir()) == []
    assert not (tmp_path / "occupied" / ".claude" / "rules" / "probe-load-rules.md").exists()


def test_probe_home_is_refused_when_the_invoking_home_is_not_a_folder(world, tmp_path):
    result = world.run(home=tmp_path / "no-such-home", env={"PROBE_HOME": str(tmp_path / "fresh")})
    assert result.rc == 3
    assert "cannot be told apart from it" in result.err
    assert result.calls == [] and not (tmp_path / "fresh").exists()


def test_a_refused_probe_home_is_refused_even_without_a_token(world):
    result = world.run(token=None, env={"PROBE_HOME": str(world.real_home)})
    assert result.rc == 3 and result.out == ""


def test_probe_home_equal_to_real_home_is_refused_when_spelled_with_a_trailing_slash(world):
    result = world.run(env={"PROBE_HOME": str(world.real_home) + "/"})
    assert result.rc == 3 and result.calls == []


@pytest.mark.parametrize("args", [
    ["--bogus"], ["stray"], ["--timeout"], ["--timeout", "0"], ["--timeout", "abc"],
    ["--timeout", "-5"], ["--timeout", "1.5"], ["--timeout", "9999999"], ["--only"],
    ["--only", "D"], ["--only", "a"], ["--only", "AB"], ["--only=A"], ["--"],
])
def test_a_bad_command_line_exits_3_and_starts_nothing(world, args):
    result = world.run(*args)
    assert result.rc == 3, result.out
    assert "USAGE" in result.err and "Usage:" in result.err
    assert result.out == "" and result.calls == []


def test_help_prints_usage_and_exits_0(world):
    result = world.run("-h")
    assert result.rc == 0 and "Usage: probe-default-tier-load.sh" in result.out
    assert result.calls == []


def test_only_runs_just_the_named_loads(world):
    result = world.run("--only", "A", "--only", "C")
    assert sorted(result.verdicts) == ["A1", "A2", "A3", "C"]
    tags = {c.get("tag") for c in result.claude()}
    assert "B1" not in tags and "B2" not in tags
    assert not any(c.get("plugin_id", "").startswith("probe-option") for c in result.calls)


def test_a_session_that_times_out_is_unverified_and_the_rest_still_run(world):
    result = world.run("--timeout", "1", scenario={"sleep": {"A1": 30}})
    assert result.status("A1") == "UNVERIFIED"
    assert "timed out after 1s" in result.evidence("A1")
    assert result.status("A2") == "PASS"
    assert result.rc == 2


def test_a_reply_over_the_size_cap_is_unverified(world):
    result = world.run("--only", "A", scenario={"A1": "big"})
    assert result.status("A1") == "UNVERIFIED"
    assert "over the 1048576-byte cap" in result.evidence("A1")


def test_a_failing_session_is_unverified_and_its_stderr_never_leaks_the_token(world):
    result = world.run("--only", "A", scenario={"rc": {"A1": 7}})
    assert result.status("A1") == "UNVERIFIED"
    assert "claude exited 7" in result.evidence("A1")
    assert "<redacted>" in result.evidence("A1")


def test_the_rules_file_is_in_the_target_home_when_the_first_session_starts(world):
    result = world.run("--only", "A")
    first = result.claude()[0]
    assert first["tag"] == "A1"
    assert first["rules_sentinel"].startswith("PROBE-RULES-")


def test_only_b_and_c_place_no_rules_file(world):
    result = world.run("--only", "B", "--only", "C")
    assert all(c.get("rules_sentinel") is None for c in result.claude() if "tag" in c)
    assert not any(f.startswith(".claude/rules") for c in result.claude() for f in c.get("home_files", []))


def test_model_is_haiku_and_tools_are_off_where_no_load_needs_them(world):
    result = world.run()
    for call in result.claude():
        if "tag" not in call:
            continue
        argv = call["argv"]
        assert argv[argv.index("--model") + 1] == "claude-haiku-5-5"
    for tag in ("A1", "A3", "C"):
        argv = result.tagged(tag)[0]["argv"]
        assert argv[argv.index("--tools") + 1] == ""


def test_the_script_never_exports_a_variable(world):
    text = SCRIPT.read_text(encoding="utf-8")
    assert not re.search(r"^\s*export\s", text, re.M)


@pytest.mark.parametrize("mode, status, words", [
    ("noboundary", "UNVERIFIED", "compaction could not be forced in one session"),
    ("boundary_early", "UNVERIFIED", "no compact_boundary message between the seed turn and the question"),
    ("boundary_late", "UNVERIFIED", "no compact_boundary message between the seed turn and the question"),
    ("leak", "UNVERIFIED", "the sentinel appeared before the compaction"),
    ("fewresults", "UNVERIFIED", "the stream has 2 result messages, expected 3"),
    ("errresult", "UNVERIFIED", "error_max_turns"),
    ("badjson", "UNVERIFIED", "is not JSON"),
    ("none", "FAIL", "after a compaction in the same session the reply lacks the rules sentinel"),
    ("wrong", "FAIL", "after a compaction in the same session the reply lacks the rules sentinel"),
])
def test_a3_passes_only_when_a_compaction_precedes_the_reply_that_holds_the_sentinel(world, mode, status, words):
    result = world.run("--only", "A", scenario={"A3": mode})
    assert result.status("A3") == status, result.out
    assert words in result.evidence("A3")
    assert result.rc == (2 if status == "UNVERIFIED" else 1)


def test_a3_is_unverified_with_the_reply_when_the_cli_would_not_compact(world):
    result = world.run("--only", "A", scenario={"A3": "noboundary"})
    assert "Not enough messages to compact" in result.evidence("A3")


def test_a3_is_unverified_when_the_compaction_session_fails(world):
    result = world.run("--only", "A", scenario={"rc": {"A3": 7}})
    assert result.status("A3") == "UNVERIFIED"
    assert "the compaction session failed: claude exited 7" in result.evidence("A3")


@pytest.mark.parametrize("ident", ["A2", "B2"])
@pytest.mark.parametrize("mode, words", [
    ("background", "ran in the background"),
    ("launch_notice", "ran in the background"),
    ("bgflag", "ran in the background"),
    ("missingtype", "the subagent type was (missing), not general-purpose"),
    ("emptytype", "the subagent type was (missing), not general-purpose"),
    ("casetype", "the subagent type was General-Purpose, not general-purpose"),
    ("agent_error", "the subagent call returned an error"),
])
def test_a_subagent_that_did_not_give_a_foreground_general_purpose_reply_is_unverified(world, ident, mode, words):
    result = world.run("--only", ident[0], scenario={ident: mode})
    assert result.status(ident) == "UNVERIFIED", result.out
    assert words in result.evidence(ident)
    assert result.rc == 2


@pytest.mark.parametrize("ident", ["A2", "B2"])
def test_a_foreground_subagent_that_says_run_in_background_false_passes(world, ident):
    result = world.run("--only", ident[0], scenario={ident: "okfg"})
    assert result.status(ident) == "PASS", result.out


def test_a_launch_notice_is_never_read_as_the_subagents_reply_even_with_the_live_wording(world):
    # The live run read this notice as A2's reply and reported FAIL.
    result = world.run("--only", "A", scenario={"A2": "launch_notice"})
    assert result.status("A2") != "FAIL"
    assert "Async agent launched successfully" not in result.evidence("A2")


@pytest.mark.parametrize("tag", ["A2", "B2"])
def test_the_subagent_prompts_ask_for_one_foreground_subagent(world, tag):
    argv = world.run("--only", tag[0]).tagged(tag)[0]["argv"]
    prompt = argv[argv.index("-p") + 1]
    assert 'subagent_type "general-purpose" and run_in_background false' in prompt
    assert "foreground" in prompt


def test_plugin_install_and_configure_name_the_plugin_by_its_full_id(world):
    result = world.run("--only", "B")
    ids = {c["plugin"]: c["plugin_id"] for c in result.calls if c.get("plugin") in ("install", "configure")}
    assert ids == {"install": "probe-option@probe-marketplace", "configure": "probe-option@probe-marketplace"}
    assert result.status("B2") == "PASS", result.out


def test_ledger_plugin_install_names_the_plugin_by_its_full_id(world):
    result = world.run("--only", "C")
    ids = [c["plugin_id"] for c in result.calls if c.get("plugin") == "install"]
    assert ids == ["ledger-hooks@ledger-plugins"]


def test_every_claude_command_runs_with_the_config_and_credential_variables_removed(world):
    result = world.run()
    assert len(result.claude()) >= 10
    leaked = [(call.get("tag") or call.get("plugin"), call["scrubbed_present"])
              for call in result.claude() if call["scrubbed_present"]]
    assert leaked == []


@pytest.mark.parametrize("kind", ["file", "hidden-file", "folder", "marketplace-folder", "via-symlink"])
def test_a_probe_home_that_is_not_empty_is_refused_and_left_as_it_was(world, tmp_path, kind):
    chosen = tmp_path / "occupied"
    chosen.mkdir()
    if kind == "file":
        (chosen / "notes.txt").write_text("mine")
    elif kind == "hidden-file":
        (chosen / ".profile").write_text("mine")
    elif kind == "folder":
        (chosen / "sub").mkdir()
    elif kind == "marketplace-folder":
        (chosen / "probe-marketplace" / ".claude-plugin").mkdir(parents=True)
        (chosen / "probe-marketplace" / ".claude-plugin" / "marketplace.json").write_text("mine")
    else:
        (chosen / "notes.txt").write_text("mine")
    given = tmp_path / "link" if kind == "via-symlink" else chosen
    if kind == "via-symlink":
        given.symlink_to(chosen)
    before = {str(p.relative_to(chosen)): (p.read_text() if p.is_file() else None) for p in chosen.rglob("*")}
    result = world.run(env={"PROBE_HOME": str(given)})
    assert result.rc == 3, result.out + result.err
    assert "REFUSED  PROBE_HOME is not empty" in result.err
    assert result.out == "" and result.calls == []
    assert {str(p.relative_to(chosen)): (p.read_text() if p.is_file() else None) for p in chosen.rglob("*")} == before
    assert list(world.tmpdir.iterdir()) == []


def test_an_empty_existing_probe_home_is_used(world, tmp_path):
    chosen = tmp_path / "empty-home"
    chosen.mkdir()
    result = world.run("--only", "A", env={"PROBE_HOME": str(chosen)})
    assert result.rc == 0, result.out + result.err
    assert {Path(c["home"]).resolve() for c in result.calls} == {chosen.resolve()}


def hook_note(world, name):
    """What the fixture's hook script noted: its $0, its argument count, what
    `command -v ledger` found, then its stdin."""
    return (world.state / "hooks" / name).read_text().splitlines()


def test_c_attributes_the_hook_runs_by_the_recording_sh_when_the_stream_names_no_plugin(world):
    # The live stream labels hook responses SessionStart:startup and UserPromptSubmit.
    result = world.run("--only", "C")
    assert result.status("C") == "PASS", result.out
    evidence = result.evidence("C")
    assert "SessionStart:startup" in evidence and "UserPromptSubmit" in evidence
    assert "logged a run of the install folder's hooks/session-start.sh and hooks/prompt.sh" in evidence
    assert "(session-start.sh x1, prompt.sh x1)" in evidence
    assert result.rc == 0


def test_c_runs_are_logged_with_the_scripts_argv_and_stdin_unchanged(world):
    world.run("--only", "C")
    start = hook_note(world, "session-start")
    prompt = hook_note(world, "prompt")
    assert start[0].endswith("/.claude/plugins/cache/ledger-plugins/ledger-hooks/build-7f3a/hooks/session-start.sh")
    assert prompt[0].endswith("/ledger-hooks/build-7f3a/hooks/prompt.sh")
    assert start[1] == prompt[1] == "0"
    # `ledger` resolves, to the call recorder: the hook can get past its command -v probe.
    assert start[2].endswith("/probe-bin/ledger") and prompt[2].endswith("/probe-bin/ledger")
    assert json.loads("\n".join(start[3:]))["hook_event_name"] == "SessionStart"
    assert json.loads("\n".join(prompt[3:]))["hook_event_name"] == "UserPromptSubmit"


def test_the_recording_sh_stands_outside_the_target_home_ahead_of_everything_on_path(world):
    result = world.run("--only", "C")
    session = result.tagged("C")[0]
    shim = Path(session["path_head"][0]).resolve()
    assert session["path_head"][0].endswith("/probe-shim")
    assert Path(session["home"]).resolve() not in shim.parents
    assert Path(session["home"]).resolve().parent in shim.parents, "the probe's own scratch folder"


@pytest.mark.parametrize("omitted", [["session-start.sh"], ["prompt.sh"], ["session-start.sh", "prompt.sh"]])
def test_c_is_unverified_when_the_recording_sh_logged_no_run_of_a_hook_script(world, omitted):
    # The stream still shows both hook responses, and names the plugin in each.
    result = world.run("--only", "C", scenario={"C": "ledger_label", "c_omit_hook": omitted})
    assert result.status("C") == "UNVERIFIED", result.out
    assert f"logged no run of {', '.join(omitted)} under the install folder of ledger-hooks" in result.evidence("C")
    assert result.rc == 2


def test_c_is_unverified_when_the_hook_runs_do_not_go_through_the_recording_sh(world):
    result = world.run("--only", "C", scenario={"C": "ledger_label", "c_bypass_shim": True})
    assert result.status("C") == "UNVERIFIED", result.out
    assert "logged no run of session-start.sh, prompt.sh" in result.evidence("C")
    assert "sh was run 0 time(s)" in result.evidence("C")


def test_c_is_unverified_when_the_logged_run_is_a_script_under_another_root(world):
    result = world.run("--only", "C", scenario={"c_other_root": True})
    assert result.status("C") == "UNVERIFIED", result.out
    assert "logged no run of session-start.sh, prompt.sh under the install folder" in result.evidence("C")
    assert "other-root/ledger-hooks/hooks/session-start.sh" in result.evidence("C")
    assert result.rc == 2


def test_c_is_unverified_when_the_logged_run_has_more_arguments_than_the_script(world):
    result = world.run("--only", "C", scenario={"c_extra_arg": True})
    assert result.status("C") == "UNVERIFIED", result.out
    assert "logged no run of session-start.sh, prompt.sh under the install folder" in result.evidence("C")
    assert "sh was run 2 time(s)" in result.evidence("C")


def test_c_is_unverified_when_the_install_left_no_install_record(world):
    result = world.run("--only", "C", scenario={"c_no_install_record": True})
    assert result.status("C") == "UNVERIFIED"
    assert "left no install record" in result.evidence("C")
    assert result.tagged("C") == []


def test_c_is_unverified_when_the_install_folder_is_outside_the_target_home(world):
    result = world.run("--only", "C", scenario={"c_install_outside": True})
    assert result.status("C") == "UNVERIFIED"
    assert "is outside the target home" in result.evidence("C")
    assert result.tagged("C") == []


def test_c_is_unverified_when_the_install_record_lists_two_install_folders(world):
    result = world.run("--only", "C", scenario={"c_two_installs": True})
    assert result.status("C") == "UNVERIFIED"
    assert "lists 2 install folder(s) for ledger-hooks@ledger-plugins, expected 1" in result.evidence("C")
    assert result.tagged("C") == []


def test_c_is_unverified_when_the_install_folder_lacks_a_hook_script(world):
    (world.fixture / "plugins" / "ledger-hooks" / "hooks" / "prompt.sh").unlink()
    result = world.run("--only", "C")
    assert result.status("C") == "UNVERIFIED"
    assert "holds no hooks/session-start.sh and hooks/prompt.sh" in result.evidence("C")
    assert result.tagged("C") == []


def test_c_fails_when_a_response_of_either_event_is_not_clean_whatever_its_label(world):
    # The stream does not say whose it is, so a response that is not clean is held against the run.
    result = world.run("--only", "C", scenario={"C": "foreign_dirty_extra"})
    assert result.status("C") == "FAIL", result.out
    assert "SessionStart exited 2" in result.evidence("C") and "SessionStart printed on stdout" in result.evidence("C")
    assert "the stream names the event, not the plugin" in result.evidence("C")
    assert result.rc == 1


@pytest.mark.parametrize("mode", ["twocalls", "twocalls_task"])
@pytest.mark.parametrize("ident", ["A2", "B2"])
def test_two_subagent_calls_where_only_one_replied_are_unverified(world, ident, mode):
    result = world.run("--only", ident[0], scenario={ident: mode})
    assert result.status(ident) == "UNVERIFIED", result.out
    evidence = result.evidence(ident)
    assert "made 2 Agent/Task calls, expected exactly one" in evidence
    assert "2 top-level Agent/Task call(s), 1 non-error result(s) for them" in evidence
    assert result.rc == 2


@pytest.mark.parametrize("ident", ["A2", "B2"])
def test_one_subagent_call_with_two_results_is_unverified(world, ident):
    result = world.run("--only", ident[0], scenario={ident: "tworesults"})
    assert result.status(ident) == "UNVERIFIED", result.out
    evidence = result.evidence(ident)
    assert "has 2 results in the stream, expected one" in evidence
    assert "1 top-level Agent/Task call(s), 2 non-error result(s) for them" in evidence
    assert result.rc == 2


@pytest.mark.parametrize("ident", ["A2", "B2"])
def test_a_call_and_a_result_that_carry_no_id_are_not_matched_to_each_other(world, ident):
    result = world.run("--only", ident[0], scenario={ident: "noid"})
    assert result.status(ident) == "UNVERIFIED", result.out
    assert "has no result in the stream" in result.evidence(ident)


@pytest.mark.parametrize("ident, mode, counts", [
    ("A2", "noresult", "(1 top-level Agent/Task call(s), 0 non-error result(s) for them)"),
    ("A2", "nospawn", "(0 top-level Agent/Task call(s), 0 non-error result(s) for them)"),
    ("B2", "noresult", "(1 top-level Agent/Task call(s), 0 non-error result(s) for them)"),
])
def test_the_counts_are_in_the_evidence_of_a_missing_call_or_result(world, ident, mode, counts):
    result = world.run("--only", ident[0], scenario={ident: mode})
    assert result.status(ident) == "UNVERIFIED"
    assert counts in result.evidence(ident)
