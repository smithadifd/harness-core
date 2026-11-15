"""Tests of scripts/probe-default-tier-load.sh, the default tier's load probe.

The probe runs real Claude Code sessions, so these tests put a stub `claude`
(and a stub `git` for load C) first on PATH. The stubs record every call (argv,
HOME, the variables that could redirect the config folder, whether the token
was the one given) and return canned replies chosen by a scenario. They read
the rules file the probe placed in its target home, and run the plugin hook
script the probe wrote, so a reply carries a sentinel only when the probe
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

CLAUDE_STUB = r'''#!__PYTHON__
import glob, json, os, re, subprocess, sys, tempfile, time

argv = sys.argv[1:]
home = os.environ.get("HOME", "")
scn = json.loads(os.environ.get("STUB_SCENARIO", "{}"))
state = os.environ["STUB_STATE"]


def log(**extra):
    record = {
        "tool": "claude", "argv": argv, "home": home, "cwd": os.getcwd(),
        "config_dir_set": "CLAUDE_CONFIG_DIR" in os.environ,
        "api_key_set": "ANTHROPIC_API_KEY" in os.environ,
        "ledger_db_set": "LEDGER_DB" in os.environ,
        "token_ok": os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") == os.environ.get("STUB_EXPECT_TOKEN"),
        "path0": os.environ.get("PATH", "").split(":")[0],
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


def emit(event):
    print(json.dumps(event))


def tool_use(name, given, ident, parent=None):
    emit({"type": "assistant", "parent_tool_use_id": parent,
          "message": {"content": [{"type": "tool_use", "id": ident, "name": name, "input": given}]}})


def tool_result(ident, text, parent=None):
    emit({"type": "user", "parent_tool_use_id": parent,
          "message": {"content": [{"type": "tool_result", "tool_use_id": ident, "content": text}]}})


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
    elif sub == "configure":
        data = sys.stdin.read()
        log(plugin="configure", plugin_id=argv[2], stdin=data)
        fail_step("configure")
        write_state("options.json", data)
    else:
        log(plugin=sub)
    sys.exit(0)


def main():
    if argv and argv[0] == "plugin":
        plugin_command()
    prompt = argv[argv.index("-p") + 1]
    found = re.match(r"\[probe ([^\]]+)\]", prompt)
    tag = found.group(1) if found else ("compact" if prompt == "/compact" else "?")
    stream = "stream-json" in argv
    session_id = argv[argv.index("--session-id") + 1] if "--session-id" in argv else None
    resume_id = argv[argv.index("--resume") + 1] if "--resume" in argv else None
    if session_id:
        write_state("sessions", session_id + "\n", "a")
    log(tag=tag, rules_sentinel=rules_sentinel(), session_id=session_id, resume_id=resume_id,
        resume_known=bool(resume_id) and resume_id in read_state("sessions").split(),
        compacted=read_state("compacted") == "yes", home_files=sorted(
            os.path.relpath(os.path.join(b, n), home) for b, _d, fs in os.walk(home) for n in fs))
    if scn.get("sleep", {}).get(tag):
        time.sleep(scn["sleep"][tag])
    code = scn.get("rc", {}).get(tag)
    if code:
        sys.stderr.write("stub: failing with the token %s\n" % os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", ""))
        sys.exit(code)
    sentinel = rules_sentinel() or ""
    mode = scn.get(tag, "ok")
    if tag in ("A1", "A3 ask"):
        reply = {"ok": sentinel, "none": "NONE", "wrong": "PROBE-RULES-0000000000000000"}.get(mode)
        if tag == "A3 ask" and read_state("compacted") != "yes":
            reply = "NONE"
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
    elif tag == "A3 seed":
        print(sentinel if mode == "leak" else "READY")
    elif tag == "compact":
        if mode == "no" or not resume_id:
            emit({"type": "system", "subtype": "init"})
            finish("Not enough messages to compact.")
        else:
            write_state("compacted", "yes")
            emit({"type": "system", "subtype": "compact_boundary",
                  "compact_metadata": {"trigger": "manual", "pre_tokens": 1}})
            finish("compacted")
    elif tag in ("A2", "B2"):
        if tag == "A2":
            inner = sentinel if mode in ("ok", "leak", "nospawn", "topbash", "nottgp", "noresult") else "NONE"
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
        given = {"subagent_type": "Explore" if mode == "nottgp" else "general-purpose",
                 "prompt": ("sentinel is %s" % sentinel) if mode == "leak" else "stub subagent prompt"}
        tool_use("Agent", given, "toolu_1")
        if mode == "foreign":
            tool_result("toolu_9", [{"type": "text", "text": sentinel}])
            pattern = "NONE"
        if mode == "topbash":
            tool_use("Bash", {"command": "echo hi"}, "toolu_2")
        tool_use("Bash", {"command": "echo inner"}, "toolu_3", parent="toolu_1")
        if mode != "noresult":
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
        if scn.get("c_call_ledger"):
            subprocess.run(["ledger", "resume", "--brief"], check=False)
        if scn.get("c_write"):
            target = os.path.join(home, ".local", "share", "ledger")
            os.makedirs(target, exist_ok=True)
            open(os.path.join(target, "ledger.db"), "w").close()
        os.makedirs(os.path.join(home, ".claude", "projects"), exist_ok=True)
        open(os.path.join(home, ".claude", "projects", "s.jsonl"), "w").close()
        open(os.path.join(home, ".claude.json"), "w").close()
        for name in ("SessionStart", "UserPromptSubmit"):
            if mode == "noevent_" + name:
                continue
            event = {"type": "system", "subtype": "hook_response", "hook_event": name,
                     "hook_name": name + ":ledger-hooks", "exit_code": 0, "outcome": "success",
                     "stdout": "", "stderr": ""}
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
        finish("ACK")
    else:
        print("stub: unknown prompt", file=sys.stderr)
        sys.exit(64)


main()
'''

GIT_STUB = r'''#!__PYTHON__
import json, os, shutil, sys

argv = sys.argv[1:]
with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps({
        "tool": "git", "argv": argv, "home": os.environ.get("HOME", ""),
        "config_global": os.environ.get("GIT_CONFIG_GLOBAL"),
        "token_set": "CLAUDE_CODE_OAUTH_TOKEN" in os.environ}) + "\n")
if "ls-remote" in argv:
    if os.environ.get("STUB_GIT_FAIL") == "ls-remote":
        sys.stderr.write("stub: ls-remote failed\n")
        sys.exit(128)
    for tag in filter(None, os.environ.get("STUB_GIT_TAGS", "").split(",")):
        print("0123456789012345678901234567890123456789\trefs/tags/%s" % tag)
elif "clone" in argv:
    if os.environ.get("STUB_GIT_FAIL") == "clone":
        sys.stderr.write("stub: clone failed\n")
        sys.exit(128)
    shutil.copytree(os.environ["STUB_LEDGER_FIXTURE"], argv[-1])
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
    path.write_text(text.replace("__PYTHON__", sys.executable), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def ledger_fixture(root: Path, with_plugin: bool = True) -> Path:
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "marketplace.json").write_text(json.dumps({
        "name": "ledger-plugins", "owner": {"name": "x"},
        "plugins": [{"name": "ledger-hooks", "source": "./plugins/ledger-hooks"}]}))
    if with_plugin:
        hooks = root / "plugins" / "ledger-hooks" / "hooks"
        hooks.mkdir(parents=True)
        (hooks / "hooks.json").write_text('{"hooks": {}}')
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
            tags="v0.1.13,v0.3.1", unset=(), without=()):
        self.runs += 1
        self.log.write_text("")
        shutil.rmtree(self.state, ignore_errors=True)
        before = self.canary()
        path = os.pathsep.join(([str(self.bin)] if stubs else []) + [str(self.tools(without))])
        environ = {
            "PATH": path, "HOME": str(home or self.real_home), "TMPDIR": str(self.tmpdir),
            "STUB_LOG": str(self.log), "STUB_STATE": str(self.state), "STUB_EXPECT_TOKEN": TOKEN,
            "STUB_SCENARIO": json.dumps(scenario or {}), "STUB_GIT_TAGS": tags,
            "STUB_LEDGER_FIXTURE": str(self.fixture),
            # Variables that could send a session to another config folder or
            # credential, or arm the ledger: the probe must remove each.
            "CLAUDE_CONFIG_DIR": str(self.real_home / ".claude"), "ANTHROPIC_API_KEY": "api-key-x",
            "LEDGER_DB": "/should/not/be/seen",
        }
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


def test_a3_resumes_one_session_through_a_compaction(world):
    result = world.run("--only", "A")
    seed = result.tagged("A3 seed")[0]
    compact = result.tagged("compact")[0]
    ask = result.tagged("A3 ask")[0]
    assert seed["session_id"] and seed["session_id"] == compact["resume_id"] == ask["resume_id"]
    assert compact["resume_known"] and ask["resume_known"]
    assert ask["compacted"], "the question must come after the compaction"
    assert "--no-session-persistence" not in seed["argv"]
    assert "compact_boundary" in result.evidence("A3")
    order = [c["tag"] for c in result.claude() if c.get("tag", "").startswith(("A3", "compact"))]
    assert order == ["A3 seed", "compact", "A3 ask"]


def test_a3_is_unverified_never_pass_when_compaction_was_not_forced(world):
    result = world.run("--only", "A", scenario={"compact": "no"})
    assert result.status("A3") == "UNVERIFIED"
    assert "compaction could not be forced" in result.evidence("A3")
    assert "Not enough messages" in result.evidence("A3")
    assert result.tagged("A3 ask") == [], "no question is asked of an uncompacted session"
    assert result.rc == 2


def test_a3_fails_when_the_sentinel_is_gone_after_compaction(world):
    result = world.run("--only", "A", scenario={"A3 ask": "none"})
    assert result.status("A3") == "FAIL"
    assert "after a forced compaction" in result.evidence("A3")
    assert result.rc == 1


def test_a3_is_unverified_when_the_sentinel_was_already_in_the_seed_turn(world):
    result = world.run("--only", "A", scenario={"A3 seed": "leak"})
    assert result.status("A3") == "UNVERIFIED"
    assert "before the compaction" in result.evidence("A3")
    assert result.tagged("compact") == []


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
    assert next(c for c in result.calls if c.get("plugin") == "configure")["argv"][-1] == "--values-stdin"


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
    # the tripwire ledger command stands first on PATH, and no ledger value is set.
    assert session["path0"].endswith("probe-bin")
    assert session["ledger_db_set"] is False
    assert "--include-hook-events" in session["argv"]


def test_c_installs_the_ledger_plugin_from_the_newest_tag_by_number(world):
    result = world.run("--only", "C", tags="v0.9.0,v0.10.0,v0.2.1,not-a-version")
    git = [c for c in result.calls if c["tool"] == "git"]
    clone = next(c for c in git if "clone" in c["argv"])
    assert clone["argv"][clone["argv"].index("--branch") + 1] == "v0.10.0"
    assert "https://github.com/example/ledger.git" in clone["argv"]
    install = next(c for c in result.calls if c.get("plugin") == "install")
    assert install["plugin_id"] == "ledger-hooks@ledger-plugins"
    assert "ledger-hooks v0.10.0" in result.evidence("C")


def test_c_probe_ledger_tag_overrides_the_newest(world):
    result = world.run("--only", "C", env={"PROBE_LEDGER_TAG": "v0.2.1"})
    git = [c for c in result.calls if c["tool"] == "git"]
    assert not any("ls-remote" in c["argv"] for c in git)
    clone = next(c for c in git if "clone" in c["argv"])
    assert clone["argv"][clone["argv"].index("--branch") + 1] == "v0.2.1"


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


@pytest.mark.parametrize("fail, words", [
    ("ls-remote", "could not read a ledger tag"),
    ("clone", "could not fetch the ledger repository at v0.3.1"),
])
def test_c_is_unverified_when_the_ledger_repository_cannot_be_read(world, fail, words):
    result = world.run("--only", "C", env={"STUB_GIT_FAIL": fail})
    assert result.status("C") == "UNVERIFIED"
    assert words in result.evidence("C")


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
    result = world.run(scenario={"rc": {"A1": 7, "A2": 7, "B1": 7, "B2": 7, "C": 7}})
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
    for tag in ("A1", "A3 seed", "A3 ask", "C"):
        argv = result.tagged(tag)[0]["argv"]
        assert argv[argv.index("--tools") + 1] == ""


def test_the_script_never_exports_a_variable(world):
    text = SCRIPT.read_text(encoding="utf-8")
    assert not re.search(r"^\s*export\s", text, re.M)
