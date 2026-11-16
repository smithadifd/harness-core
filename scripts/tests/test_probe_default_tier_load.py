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

import hashlib
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
import glob, hashlib, json, os, re, shutil, socket, subprocess, sys, tempfile, time

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
    first = os.environ.get("PATH", "").split(":")[0]
    if os.path.basename(first) == "probe-shim" and os.path.isfile(os.path.join(first, "sh")):
        with open(os.path.join(first, "sh"), encoding="utf-8") as shim:
            record["shim"] = shim.read()
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


def checkout_path():
    """The probe's own ledger checkout: the folder `plugin marketplace add` was given."""
    sources = [p for p in read_state("added").split("\n")
               if p and os.path.isdir(os.path.join(p, "plugins", "ledger-hooks"))]
    return sources[-1] if sources else None


def where(spec):
    """"home:<path>" or "checkout:<path>" as a path; "." is the root itself."""
    root, _sep, rel = spec.partition(":")
    base = home if root == "home" else checkout_path()
    return base if rel == "." else os.path.join(base, rel)


def run_acts(acts):
    """The acts a scenario lists (c_pre: during the ledger plugin's install, before
    the first manifest; c_acts: in the session, between the two manifests). They
    are the writes a hook of the plugin could make. The first sleep lets a
    timestamp that should move move."""
    time.sleep(0.05)
    for act in acts:
        op, args = act[0], act[1:]
        if op in ("write", "append"):
            full = where(args[0])
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w" if op == "write" else "a", encoding="utf-8") as handle:
                handle.write(args[1])
        elif op == "mkdir":
            os.makedirs(where(args[0]), exist_ok=True)
            if len(args) > 1:
                os.chmod(where(args[0]), args[1])
        elif op == "symlink":
            os.symlink(args[1], where(args[0]))
        elif op == "fifo":
            os.mkfifo(where(args[0]))
        elif op == "socket":
            # A unix socket path is short-limited, so bind a bare name from inside its folder.
            folder, name = os.path.split(where(args[0]))
            here = os.getcwd()
            os.chdir(folder)
            sock = socket.socket(socket.AF_UNIX)
            sock.bind(name)
            sock.close()
            os.chdir(here)
        elif op == "remove":
            os.unlink(where(args[0]))
        elif op == "rmtree":
            shutil.rmtree(where(args[0]))
        elif op == "chmod":
            os.chmod(where(args[0]), args[1])
        elif op == "link":
            os.link(where(args[0]), where(args[1]))
        elif op == "touch_remove":
            full = where(args[0])
            open(full, "w").close()
            os.unlink(full)
        elif op == "truncate":
            open(where(args[0]), "w").close()
        elif op == "forge_before":
            # A session that rewrites the before manifest in the probe's scratch
            # folder so that it matches the home as it now is (it adds the entry of
            # the file named by args[0], as the probe's helper would have recorded it).
            manifest = os.path.join(os.path.dirname(os.environ["PATH"].split(":")[0]), "manifest.before.json")
            with open(manifest, encoding="utf-8") as handle:
                document = json.load(handle)
            full = where("home:" + args[0])
            info = os.lstat(full)
            with open(full, "rb") as handle:
                digest = hashlib.sha256(handle.read()).hexdigest()
            document["roots"]["home"].append({
                "path": args[0], "type": "reg", "mode": info.st_mode, "uid": info.st_uid, "gid": info.st_gid,
                "nlink": info.st_nlink, "size": info.st_size, "mtime_ns": info.st_mtime_ns,
                "ctime_ns": info.st_ctime_ns, "ino": info.st_ino, "sha256": digest})
            document["roots"]["home"].sort(key=lambda item: item["path"])
            with open(manifest, "w", encoding="utf-8") as handle:
                json.dump(document, handle)
        else:
            sys.exit("stub: unknown act %s" % op)


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
    sources = [p for p in read_state("added").split("\n")
               if p and os.path.isdir(os.path.join(p, "plugins", "ledger-hooks"))]
    source = os.path.join(sources[-1], "plugins", "ledger-hooks") if sources else root
    if (scn.get("c_from_source") or scn.get("c_r3_shape") or scn.get("c_r4_shape") or scn.get("c_burst")
            or scn.get("c_script_gone") or scn.get("c_merged_record") or scn.get("c_relative_record")):
        # A plugin installed from a local-directory marketplace runs from its
        # source folder: the probe's own checkout of the ledger repository.
        root = os.path.realpath(source) if scn.get("c_resolved") else source
        if scn.get("c_r4_shape"):
            # The live r4 log: the shell logs the checkout in the spelling the hook
            # was started with (one slash, through the symlinked temp folder), not
            # the double-slash spelling the probe was given or the resolved one.
            root = os.path.normpath(source)
    if scn.get("c_other_root"):
        other = os.path.join(state, "other-root", "ledger-hooks")
        shutil.copytree(root, other, dirs_exist_ok=True)
        root = other
    if scn.get("c_lookalike_root"):
        # The same plugin under a folder that is named like the checkout but is not it.
        other = os.path.join(state, "lookalike", "ledger-src", "plugins", "ledger-hooks")
        shutil.copytree(source, other, dirs_exist_ok=True)
        root = other
    if scn.get("c_prefix_sibling_plugin"):
        # A plugin folder whose name starts with the trusted one's (placed in the
        # checkout at install time, before the first manifest).
        root = os.path.join(os.path.dirname(source), "ledger-hooks-evil")
    if scn.get("c_prefix_sibling_checkout"):
        # A checkout folder whose name starts with the trusted one's.
        other = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(source))) + "/ledger-src-evil",
                             "plugins", "ledger-hooks")
        shutil.copytree(source, other, dirs_exist_ok=True)
        root = other
    if scn.get("c_sibling_in_checkout"):
        # Another plugin folder inside the checkout (placed there at install time,
        # before the first manifest).
        root = os.path.join(os.path.dirname(source), "other-plugin")
    if scn.get("c_r3_shape") and event_name == "SessionStart":
        # The live r3 log held 8 sh runs: five of shell one-liners, the option
        # plugin's export hook under the target home, and the two ledger hooks.
        for _ in range(5):
            subprocess.run(["sh", "-c", "exit 0"], check=False)
        subprocess.run(["sh", os.path.join(home, "probe-marketplace", "probe-option", "hooks", "export-option.sh")],
                       check=False, capture_output=True)
    export = os.path.join(home, "probe-marketplace", "probe-option", "hooks", "export-option.sh")
    quiet = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    side = []
    if scn.get("c_r4_shape") and event_name == "SessionStart":
        # The live r4 log held 8 sh runs: five one-liners, then the option plugin's
        # export hook started together with the ledger hook (Claude Code starts a
        # session's hooks at once).
        for _ in range(5):
            subprocess.run(["sh", "-c", "exit 0"], check=False)
        side.append(subprocess.Popen(["sh", export], **quiet))
    if scn.get("c_burst") and event_name == "SessionStart":
        for _ in range(scn["c_burst"]):
            side.append(subprocess.Popen(["sh", export], **quiet))
            side.append(subprocess.Popen('sh "${CLAUDE_PLUGIN_ROOT}/hooks/session-start.sh"', shell=True,
                                         env=dict(os.environ, CLAUDE_PLUGIN_ROOT=root), **quiet))
    if scn.get("c_relative_record") and event_name == "SessionStart":
        # Records of the two hook scripts, each as a path relative to the folder the
        # probe was started in: the files exist, but the log gives no absolute path.
        shim_log = os.path.join(os.path.dirname(os.environ["PATH"].split(":")[0]), "sh-calls.log")
        with open(shim_log, "a", encoding="utf-8") as handle:
            for name in ("session-start.sh", "prompt.sh"):
                handle.write("RUN\t%s\tEND\n" % os.path.relpath(os.path.join(root, "hooks", name),
                                                               scn["c_relative_record"]))
    if scn.get("c_merged_record") and event_name == "SessionStart":
        # Two records glued into one line (what a record cut by another run's looked like).
        shim_log = os.path.join(os.path.dirname(os.environ["PATH"].split(":")[0]), "sh-calls.log")
        with open(shim_log, "a", encoding="utf-8") as handle:
            handle.write("RUN\t%s\tENDRUN\t%s\tEND\n" % (export, os.path.join(root, "hooks", "session-start.sh")))
    gone = []
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
            if scn.get("c_script_gone"):
                gone.append(os.path.join(root, "hooks", re.search(r"hooks/([\w.-]+\.sh)", command).group(1)))
    for process in side:
        process.wait()
    for script in gone:
        os.unlink(script)
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
            run_acts(scn.get("c_pre", []))
            for name, flag in (("other-plugin", "c_sibling_in_checkout"), ("ledger-hooks-evil", "c_prefix_sibling_plugin")):
                if scn.get(flag):
                    plugin = os.path.join(checkout_path(), "plugins", "ledger-hooks")
                    shutil.copytree(plugin, os.path.join(os.path.dirname(plugin), name), dirs_exist_ok=True)
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
                "tworesults", "noid", "nobash", "twobash") else "NONE"
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
        if mode == "topread":
            tool_use("Read", {"file_path": "/etc/hosts"}, "toolu_2")
        # The subagent's own Bash call and its result: events whose parent_tool_use_id
        # is the Agent call's id (a Bash result's content is a plain string). The
        # modes below leave the call out, double it, leave its result out, make the
        # result an error or double it, give the call another parent, keep the value
        # out of the Bash result (bashmiss: the Agent's reply still has it) or out of
        # the Agent's reply (replyfree: the Bash result has it).
        nested_text = "PROBE_OPT=[]" if mode == "bashmiss" else pattern
        if mode == "replyfree":
            pattern = "The subagent has finished."
        owner = "toolu_8" if mode == "bashforeign" else "toolu_1"
        # The Bash result event's own parent: the Agent call's id, but for the two modes that
        # give it another's (resforeign) or none (resnoparent).
        result_parent = {"resforeign": "toolu_8", "resnoparent": None}.get(mode, owner)
        if mode != "nobash":
            command = {"cmdnoname": "echo inner",
                       "cmdvalue": 'echo "HARNESS_PROBE_OPTION=%s"' % option_value()}.get(
                mode, 'echo "PROBE_OPT=[${HARNESS_PROBE_OPTION}]"')
            tool_use("Bash", {"command": command}, "toolu_3", parent=owner)
            if mode == "twobash":
                tool_use("Bash", {"command": "echo again"}, "toolu_6", parent=owner)
            if mode != "bashnores":
                tool_result("toolu_3", nested_text, parent=result_parent, is_error=(mode == "basherr"))
                if mode == "twobash":
                    tool_result("toolu_6", nested_text, parent=owner)
                # A second result for the same call: under another parent, under none, or
                # an error beside the good one.
                if mode == "resplusforeign":
                    tool_result("toolu_3", nested_text, parent="toolu_8")
                if mode == "resplusnull":
                    tool_result("toolu_3", nested_text, parent=None)
                if mode == "errplusgood":
                    tool_result("toolu_3", "the command failed", parent=owner, is_error=True)
                if mode == "bashtworesults":
                    tool_result("toolu_3", nested_text, parent=owner)
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
        for folder, name in (("c_write_cache", ".cache/ledger/ledger.db"), ("c_write_npm", ".npm/_logs/a.log"),
                             ("c_write_claude", ".claude/todos/t.json"),
                             ("c_write_claude_json", ".claude.json.backup")):
            if scn.get(folder):
                target = os.path.join(home, name)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                open(target, "w").close()
        for number in range(scn.get("c_write_many", 0)):
            target = os.path.join(home, ".cache", "many", "f%02d" % number)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            open(target, "w").close()
        os.makedirs(os.path.join(home, ".claude", "projects"), exist_ok=True)
        open(os.path.join(home, ".claude", "projects", "s.jsonl"), "w").close()
        open(os.path.join(home, ".claude.json"), "w").close()
        run_acts(scn.get("c_acts", []))
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

# A python3 for the probe's base PATH that runs the real one, and spoils only the snapshot
# helper's call or the compare's call (the call whose argument after the program text is
# "snapshot" or "compare"): the second snapshot for fail-after, hang-after and no-digest, the
# first for fail-before, and the compare for the compare-* modes. The real program runs first,
# so a valid manifest, digest and output exist: only the exit status, the alarm or the missing
# or malformed output tells the probe the call failed. Every other python3 call of the probe
# goes straight through.
PYSHIM = r'''#!__PYTHON__
import os, subprocess, sys, time

real = "__PYTHON__"
argv = sys.argv[1:]
mode = os.environ.get("PYSHIM", "")
program = argv[3:4]
if mode and argv[:2] == ["-I", "-c"] and program in (["snapshot"], ["compare"]):
    done = subprocess.run([real] + argv, capture_output=True, text=True)
    out, code = done.stdout, done.returncode
    if program == ["snapshot"]:
        state = os.environ["STUB_STATE"]
        os.makedirs(state, exist_ok=True)
        marker = os.path.join(state, "pyshim-snapshots")
        with open(marker, "a", encoding="utf-8") as handle:
            handle.write("x")
        with open(marker, encoding="utf-8") as handle:
            count = len(handle.read())
        if (mode, count) in (("fail-after", 2), ("fail-before", 1)):
            code = 7
        elif (mode, count) == ("hang-after", 2):
            time.sleep(5)
        elif (mode, count) == ("no-digest", 2):
            out = ""
    elif mode == "compare-fail":
        code = 7
    elif mode == "compare-hang":
        time.sleep(5)
    elif mode == "compare-noise":
        out = "HELLO\n"
    elif mode == "compare-short":
        out = 'DIFF 3\nhome "a" added reg\n'
    elif mode == "compare-extra":
        out = "SAME\nextra\n"
    sys.stdout.write(out)
    sys.stderr.write(done.stderr)
    sys.exit(code)
os.execv(real, [real] + argv)
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

    def tools(self, without=(), pyshim=False) -> Path:
        """A folder of links to the few real tools the probe needs, and to
        neither a real claude nor a real git."""
        folder = self.tmp / ("tools" + "".join("-without-" + name for name in without) + ("-pyshim" if pyshim else ""))
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
                if pyshim:
                    write_exec(folder / "python3", PYSHIM)
                else:
                    (folder / "python3").symlink_to(sys.executable)
        return folder

    def canary(self):
        return sorted(str(p.relative_to(self.real_home)) for p in self.real_home.rglob("*"))

    def run(self, *args, scenario=None, env=None, token=TOKEN, stubs=True, home=None,
            unset=(), without=(), pyshim=None):
        self.runs += 1
        self.log.write_text("")
        shutil.rmtree(self.state, ignore_errors=True)
        before = self.canary()
        path = os.pathsep.join(([str(self.bin)] if stubs else []) + [str(self.tools(without, bool(pyshim)))])
        environ = {
            "PYSHIM": pyshim or "",
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
    assert "changed the target home" not in result.evidence("C"), "the ledger call is named first"


def test_c_fails_when_the_session_writes_a_file_outside_claudes_own_state(world):
    result = world.run("--only", "C", scenario={"c_write": True})
    assert result.status("C") == "FAIL"
    assert 'home ".local/share/ledger/ledger.db" added reg' in result.evidence("C")


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
    assert "logged a run of hooks/session-start.sh and hooks/prompt.sh from the install" in evidence
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


def test_c_passes_in_the_live_r3_shape_the_hooks_run_from_the_pinned_checkout(world):
    # Live r3: 8 sh runs; the option plugin's export hook under the target home and
    # both ledger hooks under the probe's own checkout, not the install record's folder.
    result = world.run("--only", "C", scenario={"c_r3_shape": True})
    assert result.status("C") == "PASS", result.out
    assert "from the pinned checkout" in result.evidence("C")
    assert "(session-start.sh x1, prompt.sh x1)" in result.evidence("C")
    assert result.rc == 0
    start = hook_note(world, "session-start")
    assert "/ledger-src/plugins/ledger-hooks/hooks/session-start.sh" in start[0]
    assert "/.claude/plugins/cache/" not in start[0]


def test_c_passes_when_the_checkout_is_reached_through_a_symlinked_temp_folder(world):
    # The hooks run from the checkout's resolved path while the probe wrote the
    # path it got through a symlinked temp folder: both spellings are the checkout.
    link = world.tmp / "tmplink"
    link.symlink_to(world.tmpdir, target_is_directory=True)
    result = world.run("--only", "C", env={"TMPDIR": str(link)}, scenario={"c_r3_shape": True, "c_resolved": True})
    assert result.status("C") == "PASS", result.out
    assert "from the pinned checkout" in result.evidence("C")


def test_c_r3_shape_logs_eight_sh_runs_as_the_live_run_did(world):
    # The fixture is pinned to the live count: a stub that logs fewer runs would
    # prove less than r3 showed. With an extra argument on the two ledger hooks
    # they are not trusted, and the evidence reports the count and the scripts.
    result = world.run("--only", "C", scenario={"c_r3_shape": True, "c_extra_arg": True})
    assert result.status("C") == "UNVERIFIED", result.out
    assert "sh was run 8 time(s)" in result.evidence("C")
    assert "probe-marketplace/probe-option/hooks/export-option.sh" in result.evidence("C")
    # The evidence field is cut at 600 characters, so a long temp path can cut the third entry.
    assert re.search(r"export-option\.sh, /\S+/ledger-src/plugins/ledger-hooks/hooks/session-start\.sh, /",
                     result.evidence("C")), result.evidence("C")


def test_c_is_unverified_when_the_checkout_commit_check_failed_whatever_ran_from_it(world):
    # The same r3-shaped hook runs, but the fetched HEAD is not the pinned commit:
    # the probe starts no session on that checkout and trusts nothing under it.
    result = world.run("--only", "C", env={"STUB_GIT_HEAD": "f" * 40}, scenario={"c_r3_shape": True})
    assert result.status("C") == "UNVERIFIED", result.out
    assert result.tagged("C") == []
    assert result.rc == 2


@pytest.mark.parametrize("scenario", ["c_lookalike_root", "c_sibling_in_checkout", "c_other_root"])
def test_c_is_unverified_when_the_hooks_run_from_any_other_folder(world, scenario):
    result = world.run("--only", "C", scenario={scenario: True})
    assert result.status("C") == "UNVERIFIED", result.out
    assert "logged no run of session-start.sh, prompt.sh under the install folder" in result.evidence("C")
    assert "or its pinned checkout" in result.evidence("C")
    assert result.rc == 2


def test_c_unverified_evidence_names_the_scripts_that_ran_under_the_untrusted_root(world):
    result = world.run("--only", "C", scenario={"c_lookalike_root": True})
    assert "/lookalike/ledger-src/plugins/ledger-hooks/hooks/session-start.sh" in result.evidence("C")


@pytest.mark.parametrize("scenario, path", [
    ("c_write_cache", ".cache/ledger/ledger.db"),
    ("c_write_npm", ".npm/_logs/a.log"),
])
def test_c_fails_when_a_hook_writes_under_cache_or_npm(world, scenario, path):
    result = world.run("--only", "C", scenario={scenario: True})
    assert result.status("C") == "FAIL", result.out
    assert 'home "%s" added reg' % path in result.evidence("C")
    assert result.rc == 1


def test_c_fail_names_every_new_path(world):
    result = world.run("--only", "C", scenario={"c_write_cache": True, "c_write_npm": True, "c_write": True})
    assert result.status("C") == "FAIL"
    for path in (".cache/ledger/ledger.db", ".npm/_logs/a.log", ".local/share/ledger/ledger.db"):
        assert 'home "%s" added reg' % path in result.evidence("C")


def test_c_fail_says_how_many_differences_past_the_twenty_it_names(world):
    # 23 new files and the two new folders that hold them: 25 differences, 20 named.
    result = world.run("--only", "C", scenario={"c_write_many": 23})
    assert result.status("C") == "FAIL"
    evidence = result.evidence("C")
    assert ": 25 difference(s): " in evidence
    assert 'home ".cache/many/f17" added reg' in evidence and ".cache/many/f18" not in evidence
    assert evidence.endswith("(and 5 more)")
    assert evidence.count("; ") == 19


@pytest.mark.parametrize("scenario", ["c_write_claude", "c_write_claude_json"])
def test_c_passes_when_the_session_writes_only_under_claudes_own_state(world, scenario):
    result = world.run("--only", "C", scenario={scenario: True})
    assert result.status("C") == "PASS", result.out


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


# B2 reads the value from the subagent's own Bash result. A stream-json --verbose stream
# marks a subagent's events with the id of the Agent call that started it: its Bash call
# is an assistant event, its result a user event, each with that id as parent_tool_use_id
# (top-level events carry null). The stub follows that shape.


def test_b2_reads_the_value_from_the_subagents_own_bash_result_never_from_the_agent_reply(world):
    # The Agent's reply holds no value and the nested Bash result does: PASS. Mutation F17-b
    # (read the Agent's reply instead of the nested Bash result).
    result = world.run("--only", "B", scenario={"B2": "replyfree"})
    assert result.status("B2") == "PASS", result.out
    assert "saw HARNESS_PROBE_OPTION=PROBE-OPT-" in result.evidence("B2")
    assert result.rc == 0


def test_b2_fails_when_the_subagents_own_bash_result_lacks_the_value_though_the_reply_has_it(world):
    # The Agent's reply (what the main thread was handed) holds the value; the subagent's own
    # Bash result does not: FAIL. Mutation F17-b.
    result = world.run("--only", "B", scenario={"B2": "bashmiss"})
    assert result.status("B2") == "FAIL", result.out
    assert "HARNESS_PROBE_OPTION=[]" in result.evidence("B2")
    assert "Bash output: PROBE_OPT=[]" in result.evidence("B2")
    assert result.rc == 1


@pytest.mark.parametrize("mode, counts", [
    # The Sol probe's shape: the value only in the Agent reply, no Bash of the subagent's own.
    # Mutation F17-a (do not require a nested Bash call).
    pytest.param("nobash", "0 nested Bash call(s) and 0 non-error result(s)", id="no-nested-bash"),
    # Mutation F17-c (accept more than one nested Bash call).
    pytest.param("twobash", "2 nested Bash call(s) and 2 non-error result(s)", id="two-nested-bash"),
    # Mutation F17-d (accept a nested Bash call that has no result).
    pytest.param("bashnores", "1 nested Bash call(s) and 0 non-error result(s)", id="nested-bash-without-result"),
    # Mutation F17-e (count an error result as a result).
    pytest.param("basherr", "1 nested Bash call(s) and 0 non-error result(s)", id="nested-bash-error-result"),
    # Mutation F17-f (accept more than one result for the call).
    pytest.param("bashtworesults", "1 nested Bash call(s) and 2 non-error result(s)", id="nested-bash-two-results"),
    # Mutation F17-g (count a Bash call whatever its parent).
    pytest.param("bashforeign", "0 nested Bash call(s) and 0 non-error result(s)", id="bash-of-another-parent"),
    # The Sol settle probe's shape: the Bash call is under the Agent call and its result has the
    # right tool_use_id, but the result event's own parent is another call's, or missing. Mutation
    # F17-j (drop the parent check on the result).
    pytest.param("resforeign", "1 nested Bash call(s) and 1 non-error result(s)", id="result-of-another-parent"),
    pytest.param("resnoparent", "1 nested Bash call(s) and 1 non-error result(s)", id="result-without-parent"),
    # A right-parent result with the value plus a second result for the same call under a foreign
    # parent, or none: counted before the parent is asked, so not one result. Mutation F17-k (keep
    # only the results that are non-error and under the Agent call, then count them).
    pytest.param("resplusforeign", "1 nested Bash call(s) and 2 non-error result(s)", id="good-result-plus-foreign-parent-result"),
    pytest.param("resplusnull", "1 nested Bash call(s) and 2 non-error result(s)", id="good-result-plus-parentless-result"),
    # An error result and a good one for one call. Mutation F17-k.
    pytest.param("errplusgood", "1 nested Bash call(s) and 1 non-error result(s)", id="error-result-plus-good-result"),
])
def test_b2_is_unverified_unless_the_agent_call_has_exactly_one_nested_bash_call_and_result(world, mode, counts):
    result = world.run("--only", "B", scenario={"B2": mode})
    assert result.status("B2") == "UNVERIFIED", result.out
    evidence = result.evidence("B2")
    assert counts in evidence
    assert "expected exactly one of each" in evidence
    if mode in ("resplusforeign", "resplusnull", "errplusgood"):
        assert "(2 in all, 1 under it as a non-error result)" in evidence
    assert "the output of a subagent's own Bash is not shown" in evidence
    assert "saw HARNESS_PROBE_OPTION" not in evidence
    assert result.rc == 2


def test_b2_is_unverified_when_the_nested_bash_command_does_not_name_the_option(world):
    # A command that never names HARNESS_PROBE_OPTION does not show the subagent's Bash reading
    # the option. Mutation F17-l (drop the command-names check).
    result = world.run("--only", "B", scenario={"B2": "cmdnoname"})
    assert result.status("B2") == "UNVERIFIED", result.out
    assert "does not name HARNESS_PROBE_OPTION" in result.evidence("B2")
    assert "saw HARNESS_PROBE_OPTION" not in result.evidence("B2")
    assert result.rc == 2


def test_b2_is_unverified_when_the_value_is_in_the_nested_bash_commands_own_input(world):
    # The command echoes the value itself, so the result proves a relayed value, not a read.
    # Mutation F17-m (drop the check of the value in the command).
    result = world.run("--only", "B", scenario={"B2": "cmdvalue"})
    assert result.status("B2") == "UNVERIFIED", result.out
    assert "the subagent's Bash command carried the value" in result.evidence("B2")
    assert "saw HARNESS_PROBE_OPTION" not in result.evidence("B2")
    assert result.rc == 2


def test_b2_is_unverified_when_the_main_thread_called_any_tool_but_the_agent_call(world):
    # A top-level Read could return the value; only the session flags kept it out. Mutation F17-n
    # (count only a top-level Bash call).
    result = world.run("--only", "B", scenario={"B2": "topread"})
    assert result.status("B2") == "UNVERIFIED", result.out
    assert "the main thread ran Read itself 1 time(s)" in result.evidence("B2")
    assert "saw HARNESS_PROBE_OPTION" not in result.evidence("B2")
    assert result.rc == 2


@pytest.mark.parametrize("mode", ["nobash", "twobash"])
def test_a2_needs_no_nested_bash_call(world, mode):
    # A2's evidence is the subagent's context, not a command: its rule is unchanged. Mutation
    # F17-h (apply the nested Bash rule to A2 as well).
    result = world.run("--only", "A", scenario={"A2": mode})
    assert result.status("A2") == "PASS", result.out


def test_c_passes_in_the_live_r4_shape_logged_through_a_symlink_while_the_root_is_held_resolved(world):
    # Live r4: the hooks ran from the pinned checkout, but the recording sh logged them
    # as /var/... while the probe held the checkout as /private/var/... (and, with a
    # TMPDIR that ends in a slash, as .../T//probe-...). Both sides are resolved
    # before they are compared.
    link = world.tmp / "tmplink"
    link.symlink_to(world.tmpdir, target_is_directory=True)
    result = world.run("--only", "C", env={"TMPDIR": str(link) + "/"}, scenario={"c_r4_shape": True})
    assert result.status("C") == "PASS", result.out
    assert "from the pinned checkout" in result.evidence("C")
    assert "(session-start.sh x1, prompt.sh x1)" in result.evidence("C")
    assert result.rc == 0
    logged = hook_note(world, "session-start")[0]
    assert logged.startswith(str(link) + "/probe-default-tier."), "logged in the unresolved spelling"
    assert str(world.tmpdir) not in logged
    assert logged.endswith("/ledger-src/plugins/ledger-hooks/hooks/session-start.sh")


def test_c_r4_shape_logs_eight_runs_each_as_one_record_with_a_comma_and_space_between_scripts(world):
    link = world.tmp / "tmplink"
    link.symlink_to(world.tmpdir, target_is_directory=True)
    result = world.run("--only", "C", env={"TMPDIR": str(link) + "/"},
                       scenario={"c_r4_shape": True, "c_extra_arg": True})
    assert result.status("C") == "UNVERIFIED", result.out
    evidence = result.evidence("C")
    assert "sh was run 8 time(s)" in evidence
    assert "malformed" not in evidence
    listed = evidence.split("scripts run: ")[1]
    # Entries are separated by a comma and a space; the field is cut at 600
    # characters, so a long temp path can cut the last entry short.
    entries = listed.split(", ")
    assert len(entries) >= 3, listed
    assert not re.search(r"\.sh/", listed), "no script path runs into the next"
    for entry in entries[:2]:
        assert entry.startswith("/") and entry.endswith(".sh"), entry


@pytest.mark.parametrize("scenario", ["c_prefix_sibling_plugin", "c_prefix_sibling_checkout"])
def test_c_is_unverified_when_the_hooks_run_from_a_folder_named_like_the_trusted_one_plus_more(world, scenario):
    # Whole path components, never a string prefix: ledger-hooks-evil is not ledger-hooks
    # and ledger-src-evil is not ledger-src.
    result = world.run("--only", "C", scenario={scenario: True})
    assert result.status("C") == "UNVERIFIED", result.out
    assert "logged no run of session-start.sh, prompt.sh" in result.evidence("C")
    assert "-evil/" in result.evidence("C")
    assert result.rc == 2


def test_c_fails_when_the_session_removes_the_hook_scripts_from_the_pinned_checkout(world):
    # The session removes the checkout's hook scripts after they ran. That used to be
    # read as "the logged script no longer exists" (see the analyzer test below); the
    # removal itself is a change of the checkout, which the manifest now reports first.
    result = world.run("--only", "C", scenario={"c_script_gone": True})
    assert result.status("C") == "FAIL", result.out
    assert 'checkout "plugins/ledger-hooks/hooks/session-start.sh" removed reg' in result.evidence("C")
    assert 'checkout "plugins/ledger-hooks/hooks/prompt.sh" removed reg' in result.evidence("C")
    assert result.rc == 1


def test_a_logged_script_that_does_not_exist_is_never_trusted_whatever_its_spelling(tmp_path):
    # The log names the pinned checkout's hook scripts exactly as the probe spells it,
    # but the files are not there when the log is read: a path that cannot be resolved
    # to a file is never trusted by its spelling. (Run on the reader directly: a stubbed
    # session that removed the files would now be reported by the manifest first.)
    root = tmp_path / "ledger-src" / "plugins" / "ledger-hooks"
    (root / "hooks").mkdir(parents=True)
    log = tmp_path / "sh-calls.log"
    log.write_text("".join("RUN\t%s\tEND\n" % (root / "hooks" / name) for name in ("session-start.sh", "prompt.sh")))

    def verdict():
        return subprocess.run([sys.executable, "-I", "-c", embedded("ANALYZER"), "shimlog", str(log),
                               "pinned checkout:%s" % root], capture_output=True, text=True, timeout=30).stdout

    assert verdict().startswith("NORUN|session-start.sh, prompt.sh|"), verdict()
    for name in ("session-start.sh", "prompt.sh"):
        (root / "hooks" / name).write_text("")
    assert verdict().startswith("RAN|"), verdict()


def test_the_recording_sh_writes_each_record_with_one_command(world):
    # A record written in pieces is cut by another run's when hooks start together
    # (live r4: one line held the export hook and the ledger hook).
    shim = world.run("--only", "C").tagged("C")[0]["shim"]
    writers = [line for line in shim.splitlines() if "printf" in line]
    assert len(writers) == 1 and shim.count(">>") == 1, shim
    assert ">>" in writers[0] and writers[0].startswith("printf "), "not inside the argument loop"
    assert writers[0].endswith("2>/dev/null")
    assert shim.rstrip().endswith('"$@"') and "exec " in shim.splitlines()[-1]


def test_c_counts_every_run_of_a_burst_of_hooks_started_together(world):
    result = world.run("--only", "C", scenario={"c_burst": 40})
    assert result.status("C") == "PASS", result.out
    assert "(session-start.sh x41, prompt.sh x1)" in result.evidence("C")


def test_c_reads_a_line_holding_two_records_as_malformed_and_never_as_a_run(world):
    result = world.run("--only", "C", scenario={"c_merged_record": True, "c_omit_hook": ["session-start.sh"]})
    assert result.status("C") == "UNVERIFIED", result.out
    assert "logged no run of session-start.sh under the install folder" in result.evidence("C")
    assert "1 malformed record(s) not counted" in result.evidence("C")
    assert result.rc == 2


def test_c_is_unverified_when_the_logged_scripts_are_given_as_relative_paths(world):
    # The scripts exist and the relative paths lead to them from the probe's folder,
    # but a path that is not absolute is never trusted.
    result = world.run("--only", "C", scenario={
        "c_relative_record": str(world.tmp), "c_omit_hook": ["session-start.sh", "prompt.sh"]})
    assert result.status("C") == "UNVERIFIED", result.out
    assert "logged no run of session-start.sh, prompt.sh" in result.evidence("C")
    assert result.rc == 2


# --- load C's no-write check: the manifest of both roots ---------------------------
#
# Each live-path case below is a stubbed act in the fake session world: the stubbed
# install (c_pre, before the first manifest) or the stubbed session (c_acts, between
# the two manifests) makes the write a hook of the plugin could make. The walk cases
# run the snapshot helper's own source under a fault-injecting wrapper, and the
# compare cases run the compare's source on synthetic manifests. Each case says
# which one-site mutation of the script turns it red.


def embedded(name):
    """The python source embedded in the script as the heredoc `name`."""
    text = SCRIPT.read_text(encoding="utf-8")
    found = re.search(r"^IFS= read -r -d '' %s <<'PYEOF' \|\| true\n(.*?)^PYEOF$" % name, text, re.S | re.M)
    assert found, name
    return found.group(1)


def c_run(world, acts=(), pre=(), *args, **kwargs):
    scenario = {"c_acts": list(acts), "c_pre": list(pre)}
    scenario.update(kwargs.pop("scenario", {}))
    return world.run("--only", "C", *args, scenario=scenario, **kwargs)


FILE_F = [["write", "home:f", "abcd"], ["chmod", "home:f", 0o644]]


@pytest.mark.parametrize("pre, acts, wanted", [
    # 1-4: a new entry that is not a regular file. Mutation M1 (record regular files only).
    pytest.param([], [["symlink", "home:x", "/etc"]], [r'home "x" added lnk'], id="row01-new-symlink"),
    pytest.param([], [["mkdir", "home:d"]], [r'home "d" added dir'], id="row02-new-empty-folder"),
    # 3 also shows that the walk never opens a fifo (extra mutation M28: open every entry that is
    # not a folder or a symlink for hashing; the fifo then fails the regular-file check, UNVERIFIED).
    pytest.param([], [["fifo", "home:p"]], [r'home "p" added fifo'], id="row03-new-fifo"),
    pytest.param([], [["socket", "home:s"]], [r'home "s" added sock'], id="row04-new-socket"),
    # 5-9: a removal or a change of an entry that was there. Mutation M2 (report additions only).
    pytest.param([], [["remove", "home:probe-bin/ledger"]],
                 [r'home "probe-bin/ledger" removed reg', r'home "probe-bin" changed'], id="row05-deleted-file"),
    pytest.param(FILE_F, [["write", "home:f", "wxyz"]],
                 [r'home "f" changed [a-z0-9_,]*sha256'], id="row06-same-size-rewrite"),
    pytest.param(FILE_F, [["chmod", "home:f", 0o600]],
                 [r'home "f" changed mode,ctime_ns(;|$)'], id="row07-chmod"),
    pytest.param(FILE_F, [["link", "home:f", "home:.claude/g"]],
                 [r'1 difference\(s\): home "f" changed nlink,ctime_ns$'], id="row08-hard-link-from-exempt-state"),
    pytest.param([["mkdir", "home:sub"]], [["touch_remove", "home:sub/t"]],
                 [r'home "sub" changed [a-z0-9_,]*mtime_ns'], id="row09-create-then-delete-in-a-subfolder"),
    # 10: a write in the pinned checkout, where the hooks run. Mutation M3 (compare the home only).
    pytest.param([], [["append", "checkout:.claude-plugin/marketplace.json", "\n"]],
                 [r'checkout "\.claude-plugin/marketplace\.json" changed [a-z0-9_,]*sha256'],
                 id="row10-write-in-the-checkout"),
    # 11: a newline in a name. Mutation M4 (print the path unquoted).
    pytest.param([], [["write", "home:a\nb", "x"]],
                 [r'1 difference\(s\): home "a\\nb" added reg$'], id="row11-newline-in-a-name"),
    # 12: the session's own working folder is compared. Mutation M5 (exempt probe-work).
    pytest.param([], [["touch_remove", "home:probe-work/t"]],
                 [r'home "probe-work" changed [a-z0-9_,]*mtime_ns'], id="row12-probe-work-touched"),
    # 13: the home root's mode is compared. Mutation M6 (exempt every field of the home root).
    pytest.param([], [["chmod", "home:.", 0o710]],
                 [r'1 difference\(s\): home "\." changed mode$'], id="row13-home-root-mode"),
])
def test_c_manifest_fails_and_names_each_entry_and_field_that_changed(world, pre, acts, wanted):
    result = c_run(world, acts, pre)
    assert result.status("C") == "FAIL", result.out
    evidence = result.evidence("C")
    assert "the session changed the target home or the ledger checkout: " in evidence
    for pattern in wanted:
        assert re.search(pattern, evidence), (pattern, evidence)
    assert result.rc == 1


@pytest.mark.parametrize("act, words", [
    # 14: an unreadable folder. 15: an unreadable regular file. Mutation M7 (swallow walk errors).
    pytest.param([["mkdir", "home:u", 0]], "Permission denied: 'u'", id="row14-unreadable-folder"),
    pytest.param([["write", "home:z", "x"], ["chmod", "home:z", 0]], "Permission denied: 'z'",
                 id="row15-unreadable-file"),
])
def test_c_manifest_is_unverified_when_the_walk_cannot_read_an_entry(world, tmp_path, act, words):
    # The caller's own PROBE_HOME stays after the probe's scratch folder is removed, so
    # the mode is put back here. Permissions are not enforced for uid 0, where this fails
    # for want of an error to see; the same two errors are injected for any user by the
    # walk cases below (W5 and W6).
    chosen = tmp_path / "chosen-home"
    try:
        result = c_run(world, act, (), env={"PROBE_HOME": str(chosen)})
    finally:
        for name, mode in (("u", 0o755), ("z", 0o644)):
            if (chosen / name).exists():
                (chosen / name).chmod(mode)
    assert result.status("C") == "UNVERIFIED", result.out
    assert "the after snapshot failed" in result.evidence("C")
    assert words in result.evidence("C")
    assert result.rc == 2


def test_c_row16_manifest_is_unverified_when_a_root_is_gone_at_the_after_snapshot(world):
    # Mutation M8 (a missing root reads as an empty list: the entries would read as removed, a FAIL).
    result = c_run(world, [["rmtree", "checkout:."]])
    assert result.status("C") == "UNVERIFIED", result.out
    assert "the after snapshot failed: exit 1: the checkout root cannot be opened as a folder" in result.evidence("C")
    assert result.rc == 2


def test_c_row17_manifest_is_unverified_when_the_helper_exits_non_zero_after_the_session(world):
    # The shim runs the real helper, so a valid manifest and digest exist, then exits 7:
    # only the exit status says the call failed (mutation M9, ignore the helper's exit).
    result = world.run("--only", "C", pyshim="fail-after")
    assert result.status("C") == "UNVERIFIED", result.out
    assert "the after snapshot failed: exit 7" in result.evidence("C")
    assert result.rc == 2


def test_c_row18_manifest_is_unverified_when_the_helper_hangs_past_the_alarm(world):
    # The shim runs the real helper, then sleeps 5 seconds: only the alarm stops it
    # (mutation M16, no alarm around the helper: the call ends, with a valid digest, and C passes).
    result = world.run("--only", "C", "--timeout", "2", pyshim="hang-after")
    assert result.status("C") == "UNVERIFIED", result.out
    assert "the after snapshot failed: timed out after 2s" in result.evidence("C")
    assert result.rc == 2


def test_c_manifest_before_snapshot_that_fails_stops_the_run_before_the_session(world):
    # Mutation M23 (carry on after a before snapshot that failed).
    result = world.run("--only", "C", pyshim="fail-before")
    assert result.status("C") == "UNVERIFIED", result.out
    assert "the before snapshot failed: exit 7, so no session was run" in result.evidence("C")
    assert result.tagged("C") == []
    assert result.rc == 2


def test_c_row19_manifest_is_unverified_when_the_session_rewrites_the_baseline(world):
    # The stubbed session writes a file in the home and rewrites the before manifest in
    # the probe's scratch folder to list it: the two manifests then agree, and only the
    # digest the probe holds says the baseline is not the one it took (mutation M17).
    result = c_run(world, [["write", "home:forged", "payload"], ["forge_before", "forged"]])
    assert result.status("C") == "UNVERIFIED", result.out
    assert "the manifest compare failed: exit 1: the before manifest does not match its digest" in result.evidence("C")
    assert result.rc == 2


def test_c_row20_passes_when_only_claude_state_moves(world):
    # Mutation M10 (compare the home root's mtime_ns, ctime_ns, nlink and size).
    result = c_run(world, [
        ["write", "home:.claude/state/z.json", "x"], ["write", "home:.claude.json.backup", "x"],
        ["write", "home:.claude.json.lock", "x"], ["remove", "home:.claude.json.lock"]])
    assert result.status("C") == "PASS", result.out
    assert result.rc == 0


def test_c_row21_passes_with_a_dangling_symlink_at_rest(world):
    # Mutation M11 (follow symlinks: the dangling one cannot be stat-ed, so UNVERIFIED).
    result = c_run(world, (), [["symlink", "home:dl", "/nonexistent"]])
    assert result.status("C") == "PASS", result.out


def test_c_row22_the_ledger_call_is_named_before_the_manifest_difference(world):
    # The recorder appends to probe-bin/ledger-calls.log, which is in the first manifest too.
    # Mutation M12 (the manifest verdict before the ledger-call check).
    result = c_run(world, (), (), scenario={"c_call_ledger": True})
    assert result.status("C") == "FAIL", result.out
    assert "called the ledger command: resume --brief" in result.evidence("C")
    assert "changed the target home" not in result.evidence("C")


CALLS_LOG = "home:probe-bin/ledger-calls.log"
CALLS_LOG_PATH = r'home "probe-bin/ledger-calls\.log"'


def test_c_f18_the_calls_log_exists_empty_in_probe_bin_before_the_session_starts(world):
    # The log is in the first manifest only if it is there before it. Mutation F18-a (create
    # the log lazily, on the recorder's first call).
    result = world.run("--only", "C")
    assert result.status("C") == "PASS", result.out
    files = result.tagged("C")[0]["home_files"]
    assert "probe-bin/ledger-calls.log" in files
    assert "probe-ledger-calls.log" not in files, "no recorder log at the top of the home"


def test_c_f18_a_hook_that_calls_the_recorder_then_removes_the_log_is_a_fail(world):
    # The shape a review probe used: the log never existed in the first manifest and the home
    # root's four fields are not compared, so the removal was invisible. Mutation F18-a.
    result = c_run(world, [["remove", CALLS_LOG]], scenario={"c_call_ledger": True})
    assert result.status("C") == "FAIL", result.out
    evidence = result.evidence("C")
    assert re.search(CALLS_LOG_PATH + " removed reg", evidence), evidence
    assert "never called" not in evidence
    assert result.rc == 1


def test_c_f18_a_hook_that_calls_the_recorder_then_empties_the_log_is_a_fail(world):
    # Mutation F18-a (lazy creation names the log as added, not changed).
    result = c_run(world, [["truncate", CALLS_LOG]], scenario={"c_call_ledger": True})
    assert result.status("C") == "FAIL", result.out
    evidence = result.evidence("C")
    assert re.search(CALLS_LOG_PATH + r" changed [a-z0-9_,]*mtime_ns", evidence), evidence
    assert "called the ledger command" not in evidence, "the log reads empty: the manifest names it"
    assert result.rc == 1


def test_c_f18_a_hook_that_removes_and_recreates_the_log_is_a_fail(world):
    # An empty log recreated in place is the same bytes under a new inode and new times.
    # Mutation F18-a.
    result = c_run(world, [["remove", CALLS_LOG], ["write", CALLS_LOG, ""]])
    assert result.status("C") == "FAIL", result.out
    assert re.search(CALLS_LOG_PATH + r" changed [a-z0-9_,]*ctime_ns", result.evidence("C")), result.evidence("C")
    assert result.rc == 1


def test_c_f18_a_call_that_leaves_the_log_is_the_ledger_call_fail(world):
    result = c_run(world, (), (), scenario={"c_call_ledger": True})
    assert result.status("C") == "FAIL", result.out
    assert "called the ledger command: resume --brief" in result.evidence("C")


def test_c_f18_a_log_swapped_for_a_symlink_to_a_file_with_text_is_never_read_as_a_call(world):
    # The ledger-call check reads the log only while it is still a regular file; a symlink put
    # in its place is read as nothing and the manifest names the change. Mutation F18-b (read
    # the log without checking that it is still a regular file).
    result = c_run(world, [["write", "home:decoy.txt", "resume --brief\n"], ["remove", CALLS_LOG],
                           ["symlink", CALLS_LOG, "../decoy.txt"]])
    assert result.status("C") == "FAIL", result.out
    evidence = result.evidence("C")
    assert re.search(CALLS_LOG_PATH + r" changed type", evidence), evidence
    assert "called the ledger command" not in evidence
    assert "never called" not in evidence
    assert result.rc == 1


def test_c_f18_a_log_swapped_for_a_folder_is_never_read_and_the_probe_prints_nothing_to_stderr(world):
    # Reading a folder as the log would make head complain on the probe's stderr. Mutation F18-d
    # (read the log without checking that it is a regular file, leaving the symlink check).
    result = c_run(world, [["remove", CALLS_LOG], ["mkdir", CALLS_LOG]])
    assert result.status("C") == "FAIL", result.out
    assert re.search(CALLS_LOG_PATH + r" changed type", result.evidence("C")), result.evidence("C")
    assert "called the ledger command" not in result.evidence("C")
    assert result.err == "", result.err


def test_c_pass_says_what_no_file_was_left_written_changed_or_removed_where(world):
    result = world.run("--only", "C")
    assert ("no file was left written, changed or removed in the target home or the pinned ledger "
            "checkout outside Claude's own state (the .claude folder and the .claude.json files)") in result.evidence("C")


# --- the snapshot helper, run directly ---------------------------------------------


def snapshot_run(args, source=None, timeout=30):
    return subprocess.run([sys.executable, "-I", "-c", source or embedded("SNAPSHOT_HELPER"), "snapshot", *map(str, args)],
                          capture_output=True, text=True, timeout=timeout)


def test_w0_snapshot_records_every_entry_in_full_and_exempts_only_top_level_claude_state(tmp_path):
    # Mutation M26 (exempt the names at every depth, not only at the top of the home).
    home, checkout = tmp_path / "home", tmp_path / "checkout"
    for folder in (".claude/x", ".claude.json.d", "sub/.claude", ".git"):
        target = home / folder if folder != ".git" else checkout / folder
        target.mkdir(parents=True)
    (home / ".claude" / "x" / "in.txt").write_text("in")
    (home / ".claude.json").write_text("{}")
    (home / ".claude.json.d" / "in.txt").write_text("in")
    (home / "sub" / ".claude" / "y").write_text("y")
    (home / "f").write_text("hi\n")
    (home / "dl").symlink_to("/nonexistent")
    os.mkfifo(home / "p")
    (checkout / ".git" / "a").write_text("a")
    out = tmp_path / "scratch.json"
    done = snapshot_run([out, home, checkout])
    assert done.returncode == 0 and done.stderr == "", done.stderr
    data = out.read_bytes()
    assert done.stdout == hashlib.sha256(data).hexdigest() + "\n"
    document = json.loads(data)
    assert document["version"] == 1 and set(document["roots"]) == {"home", "checkout"}
    paths = [item["path"] for item in document["roots"]["home"]]
    assert paths == sorted(paths)
    assert set(paths) == {".", "dl", "f", "p", "sub", "sub/.claude", "sub/.claude/y"}
    assert [item["path"] for item in document["roots"]["checkout"]] == [".", ".git", ".git/a"]
    by_path = {item["path"]: item for item in document["roots"]["home"]}
    f = by_path["f"]
    info = os.lstat(home / "f")
    assert f == {"path": "f", "type": "reg", "mode": info.st_mode, "uid": info.st_uid, "gid": info.st_gid,
                 "nlink": info.st_nlink, "size": 3, "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns,
                 "ino": info.st_ino, "sha256": hashlib.sha256(b"hi\n").hexdigest()}
    assert by_path["dl"]["target"] == "/nonexistent" and "sha256" not in by_path["dl"]
    assert by_path["p"]["type"] == "fifo" and by_path["sub"]["type"] == "dir" and by_path["."]["type"] == "dir"


def test_w0_exempt_state_is_never_entered_so_nothing_inside_it_can_raise_an_error(tmp_path):
    # Mutation M27 (walk the exempt names too: the unreadable folder inside .claude is then an error).
    home, checkout = tmp_path / "home", tmp_path / "checkout"
    (home / ".claude" / "locked").mkdir(parents=True)
    checkout.mkdir()
    (home / ".claude" / "locked").chmod(0)
    try:
        done = snapshot_run([tmp_path / "m.json", home, checkout])
    finally:
        (home / ".claude" / "locked").chmod(0o755)
    assert done.returncode == 0, done.stderr
    assert [item["path"] for item in json.loads((tmp_path / "m.json").read_bytes())["roots"]["home"]] == ["."]


def test_w0_the_snapshot_records_no_access_time(tmp_path):
    # The walk itself reads every file, so atime is not recorded; ctime is (it covers a metadata-only
    # write that a process cannot hide). Mutation M45 (record atime_ns as well).
    (tmp_path / "home").mkdir()
    (tmp_path / "checkout").mkdir()
    (tmp_path / "home" / "f").write_text("x")
    done = snapshot_run([tmp_path / "m.json", tmp_path / "home", tmp_path / "checkout"])
    assert done.returncode == 0, done.stderr
    item = next(item for item in json.loads((tmp_path / "m.json").read_bytes())["roots"]["home"] if item["path"] == "f")
    assert "ctime_ns" in item and not [key for key in item if "atime" in key]


@pytest.mark.parametrize("make, words", [
    # Mutations M24 (no overlap check) and M25 (no check that the manifest lies outside both roots).
    pytest.param(lambda t: (t / "home", t / "home" / "inner", t / "m.json"), "the two roots overlap", id="checkout-inside-home"),
    pytest.param(lambda t: (t / "home" / "inner", t / "home", t / "m.json"), "the two roots overlap", id="home-inside-checkout"),
    pytest.param(lambda t: (t / "home", t / "checkout", t / "home" / "m.json"), "the manifest path lies inside a root",
                 id="manifest-in-home"),
    pytest.param(lambda t: (t / "home", t / "checkout", t / "checkout" / "deeper" / "m.json"),
                 "the manifest path lies inside a root", id="manifest-in-checkout"),
])
def test_w7_the_helper_refuses_overlapping_roots_and_a_manifest_inside_a_root(tmp_path, make, words):
    for name in ("home/inner", "checkout/deeper"):
        (tmp_path / name).mkdir(parents=True)
    home, checkout, out = make(tmp_path)
    done = snapshot_run([out, home, checkout])
    assert done.returncode == 1 and done.stdout == ""
    assert words in done.stderr
    assert not out.exists()


def test_w7_the_helper_never_follows_a_root_that_is_a_symlink(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "checkout").mkdir()
    (tmp_path / "home").symlink_to(tmp_path / "real", target_is_directory=True)
    done = snapshot_run([tmp_path / "m.json", tmp_path / "home", tmp_path / "checkout"])
    assert done.returncode == 1 and "the home root cannot be opened as a folder" in done.stderr


HELPER_HARNESS = r'''
import errno, os, sys

# A test-only program: it runs the snapshot helper's own source with one function of
# the os module replaced by a wrapper that acts between the helper's stat and its open
# (or its stat and its second stat), as another process could. The production script
# has no hook for this.
source_file, MODE, base = sys.argv[1:4]
namespace = {"__name__": "snapshot_helper"}
with open(source_file, encoding="utf-8") as handle:
    exec(compile(handle.read(), "snapshot-helper", "exec"), namespace)
real_open, real_stat, real_read = os.open, os.stat, os.read
stats = {"victim.txt": 0}


def open_wrapper(path, flags, *rest, dir_fd=None):
    if dir_fd is None and MODE == "root-decoy" and path == os.path.join(os.path.realpath(base), "home"):
        path = os.path.join(base, "decoyroot")     # a swap of the root, then a swap back
    if dir_fd is not None:
        if MODE == "folder-decoy" and path == "victim":
            path = "decoy"      # what a swap of the folder, then a swap back, shows the helper
        elif MODE == "folder-symlink" and path == "victim":
            os.rename("victim", "moved", src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
            os.symlink(os.path.join(base, "outside"), "victim", dir_fd=dir_fd)
        elif MODE == "file-fifo" and path == "victim.txt":
            os.unlink(path, dir_fd=dir_fd)
            os.mkfifo(path, dir_fd=dir_fd)
        elif MODE == "file-decoy" and path == "victim.txt":
            path = "decoy.txt"  # what a swap of the file, then a swap back, shows the helper
        elif MODE == "folder-denied" and path == "victim":
            raise PermissionError(errno.EACCES, "Permission denied", path)
    return real_open(path, flags, *rest, dir_fd=dir_fd)


def stat_wrapper(path, *, dir_fd=None, follow_symlinks=True):
    if MODE == "replace-while-hashed" and dir_fd is not None and path == "victim.txt":
        stats["victim.txt"] += 1
        if stats["victim.txt"] == 2:    # the stat after the contents were hashed
            os.rename("replacement.txt", "victim.txt", src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    return real_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)


def read_wrapper(fd, length):
    if MODE == "file-unreadable":
        raise OSError(errno.EIO, "Input/output error")
    return real_read(fd, length)


os.open, os.stat, os.read = open_wrapper, stat_wrapper, read_wrapper
home, checkout = os.path.join(base, "home"), os.path.join(base, "checkout")
sys.exit(namespace["main"](["snapshot", os.path.join(base, "out.json"), home, checkout]))
'''


def harness_run(tmp_path, mode):
    base = tmp_path / "w"
    (base / "home" / "victim").mkdir(parents=True)
    (base / "home" / "decoy").mkdir()
    (base / "outside").mkdir()
    (base / "decoyroot").mkdir()
    (base / "checkout").mkdir()
    (base / "home" / "victim" / "inner.txt").write_text("inner")
    (base / "home" / "decoy" / "other.txt").write_text("other")
    for name in ("victim.txt", "decoy.txt", "replacement.txt"):
        (base / "home" / name).write_text(name + " contents")
    (base / "checkout" / "f").write_text("f")
    source, harness = tmp_path / "helper.py", tmp_path / "harness.py"
    source.write_text(embedded("SNAPSHOT_HELPER"), encoding="utf-8")
    harness.write_text(HELPER_HARNESS, encoding="utf-8")
    try:
        return subprocess.run([sys.executable, "-I", str(harness), str(source), mode, str(base)],
                              capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired:
        pytest.fail("the snapshot helper blocked (mode %s): a file swapped for a fifo was opened without O_NONBLOCK" % mode)


def test_w0_the_fault_injecting_harness_runs_the_helper_clean_when_it_injects_nothing(tmp_path):
    done = harness_run(tmp_path, "none")
    assert done.returncode == 0, done.stderr
    assert (tmp_path / "w" / "out.json").is_file()


@pytest.mark.parametrize("mode, words", [
    # W1, mutation M18 (drop the folder identity check): the decoy is a real folder, so only the check sees it.
    pytest.param("folder-decoy", "a folder changed between its stat and its open: victim", id="W1-folder-swapped-for-another"),
    # W1, the root itself (extra mutation M29, drop the root identity check).
    pytest.param("root-decoy", "the home root changed between its stat and its open", id="W1-root-swapped-for-another"),
    # W1: swapped for a symlink to an outside folder; the open refuses it (O_NOFOLLOW).
    pytest.param("folder-symlink", "victim", id="W1-folder-swapped-for-a-symlink"),
    # W2, mutation M19a (drop O_NONBLOCK): the open of a fifo with no writer would block.
    pytest.param("file-fifo", "a file changed between its stat and its open: victim.txt", id="W2-file-swapped-for-a-fifo"),
    # W2, mutation M19b (drop the file identity check): the decoy is a regular file, and the name still
    # reads as the first stat after the swap back, so only the identity check sees it.
    pytest.param("file-decoy", "a file changed between its stat and its open: victim.txt", id="W2-file-swapped-for-another"),
    # W4, mutation M21 (drop the post-hash stat).
    pytest.param("replace-while-hashed", "a file changed while it was hashed: victim.txt", id="W4-file-replaced-while-hashed"),
    # W5 and W6, mutation M7 (swallow walk errors), for any user, root included.
    pytest.param("folder-denied", "Permission denied", id="W5-folder-cannot-be-opened"),
    pytest.param("file-unreadable", "Input/output error", id="W6-file-cannot-be-read"),
])
def test_w_the_helper_exits_non_zero_naming_the_fault(tmp_path, mode, words):
    done = harness_run(tmp_path, mode)
    assert done.returncode == 1, (done.stdout, done.stderr)
    assert done.stdout == ""
    assert words in done.stderr, done.stderr
    assert not (tmp_path / "w" / "out.json").exists(), "no manifest is left for a failed snapshot"


def test_w3_every_file_type_maps_to_its_own_name_and_an_unknown_one_to_other():
    # A char or block device cannot be made without privilege, so the mapping is called on modes.
    # Mutation M20 (map chr and blk to other).
    namespace = {"__name__": "snapshot_helper"}
    exec(compile(embedded("SNAPSHOT_HELPER"), "snapshot-helper", "exec"), namespace)
    kind = namespace["kind"]
    table = [(stat.S_IFREG, "reg"), (stat.S_IFDIR, "dir"), (stat.S_IFLNK, "lnk"), (stat.S_IFIFO, "fifo"),
             (stat.S_IFSOCK, "sock"), (stat.S_IFCHR, "chr"), (stat.S_IFBLK, "blk")]
    assert [kind(fmt | 0o644) for fmt, _name in table] == [name for _fmt, name in table]
    assert kind(stat.S_IFMT(0o170000) | 0o644) == "other"


# --- the compare, run directly on synthetic manifests --------------------------------


def rec(path, **fields):
    item = {"path": path, "type": "reg", "mode": 33188, "uid": 501, "gid": 20, "nlink": 1, "size": 4,
            "mtime_ns": 1000, "ctime_ns": 2000, "ino": 77, "sha256": "ab" * 32}
    item.update(fields)
    return {key: value for key, value in item.items() if value is not None}


def manifest_file(tmp_path, name, home=(), checkout=(), version=1, raw=None):
    data = raw if raw is not None else json.dumps(
        {"version": version, "roots": {"home": list(home), "checkout": list(checkout)}}, ensure_ascii=True).encode("ascii")
    path = tmp_path / name
    path.write_bytes(data)
    return str(path), hashlib.sha256(data).hexdigest()


def compare_run(before, after, source=None):
    return subprocess.run([sys.executable, "-I", "-c", source or embedded("MANIFEST_COMPARE"), "compare",
                           before[0], after[0], before[1], after[1]], capture_output=True, text=True, timeout=30)


C1_PAIRS = {
    "type": ({"type": "reg"}, {"type": "dir"}),
    "mode": ({"mode": 33188}, {"mode": 33261}),
    "uid": ({"uid": 501}, {"uid": 502}),
    "gid": ({"gid": 20}, {"gid": 21}),
    "nlink": ({"nlink": 1}, {"nlink": 2}),
    "size": ({"size": 4}, {"size": 5}),
    "mtime_ns": ({"mtime_ns": 1000}, {"mtime_ns": 1001}),
    "ctime_ns": ({"ctime_ns": 2000}, {"ctime_ns": 2001}),
    "ino": ({"ino": 77}, {"ino": 78}),
    "target": ({"type": "lnk", "sha256": None, "target": "a"}, {"type": "lnk", "sha256": None, "target": "b"}),
    "sha256": ({"sha256": "ab" * 32}, {"sha256": "cd" * 32}),
}


@pytest.mark.parametrize("field", list(C1_PAIRS))
def test_c1_compare_names_a_field_that_alone_differs(tmp_path, field):
    # Mutation M13 (drop this field from the compared fields), one run per field.
    old, new = C1_PAIRS[field]
    done = compare_run(manifest_file(tmp_path, "b.json", [rec("f", **old)]),
                       manifest_file(tmp_path, "a.json", [rec("f", **new)]))
    assert (done.returncode, done.stdout) == (0, 'DIFF 1\nhome "f" changed %s\n' % field), done.stderr


def test_c1_compare_names_a_field_present_on_one_side_only(tmp_path):
    done = compare_run(manifest_file(tmp_path, "b.json", [rec("l", type="lnk", sha256=None)]),
                       manifest_file(tmp_path, "a.json", [rec("l", type="lnk", sha256=None, target="a")]))
    assert done.stdout == 'DIFF 1\nhome "l" changed target\n'


def test_c2_compare_reads_a_renamed_path_as_one_removed_and_one_added(tmp_path):
    # Mutation M2 (report added paths only).
    done = compare_run(manifest_file(tmp_path, "b.json", [rec("a")]), manifest_file(tmp_path, "a.json", [rec("b")]))
    assert done.stdout == 'DIFF 2\nhome "a" removed reg\nhome "b" added reg\n'


def test_c2_compare_sorts_by_root_then_path_and_quotes_every_path(tmp_path):
    before = manifest_file(tmp_path, "b.json", [rec("z")], [rec("m")])
    after = manifest_file(tmp_path, "a.json", [rec("z", size=9), rec("a\nb")], [rec("m", size=9)])
    done = compare_run(before, after)
    assert done.stdout == ('DIFF 3\ncheckout "m" changed size\nhome "a\\nb" added reg\nhome "z" changed size\n')


def test_c3_compare_leaves_four_fields_of_the_home_root_entry_out(tmp_path):
    # Mutation M10 (compare them).
    root = {"type": "dir", "mode": 16877, "sha256": None}
    before = manifest_file(tmp_path, "b.json", [rec(".", **root)])
    after = manifest_file(tmp_path, "a.json", [rec(".", mtime_ns=5, ctime_ns=6, nlink=7, size=8, **root)])
    done = compare_run(before, after)
    assert (done.returncode, done.stdout) == (0, "SAME\n")


@pytest.mark.parametrize("field, value", [("mode", 16832), ("uid", 502), ("gid", 21), ("ino", 78), ("type", "lnk")])
def test_c3_compare_still_compares_the_other_fields_of_the_home_root_entry(tmp_path, field, value):
    # Mutation M6 (exempt every field of the home root entry).
    root = {"type": "dir", "mode": 16877, "sha256": None}
    before = manifest_file(tmp_path, "b.json", [rec(".", **root)])
    after = manifest_file(tmp_path, "a.json", [rec(".", **dict(root, **{field: value}))])
    assert compare_run(before, after).stdout == 'DIFF 1\nhome "." changed %s\n' % field


def test_c4_compare_leaves_nothing_out_for_the_checkout_root_entry(tmp_path):
    # Mutation M14 (apply the home root's exemption to both roots).
    root = {"type": "dir", "mode": 16877, "sha256": None}
    before = manifest_file(tmp_path, "b.json", checkout=[rec(".", **root)])
    after = manifest_file(tmp_path, "a.json", checkout=[rec(".", mtime_ns=5, **root)])
    assert compare_run(before, after).stdout == 'DIFF 1\ncheckout "." changed mtime_ns\n'


def test_c4_compare_reads_the_checkout_root_too(tmp_path):
    # Mutation M3 (compare the home only).
    done = compare_run(manifest_file(tmp_path, "b.json", checkout=[rec("a")]), manifest_file(tmp_path, "a.json"))
    assert done.stdout == 'DIFF 1\ncheckout "a" removed reg\n'


def with_home(*items):
    return {"version": 1, "roots": {"home": list(items), "checkout": []}}


@pytest.mark.parametrize("name, raw, words", [
    # Mutation M15 (read an unparsable manifest as an empty one) for the first two; for each other
    # case the mutation drops the one check whose message is named.
    pytest.param("truncated", b'{"version": 1, "roots": {"home": [{"path"', "does not parse", id="truncated"),
    pytest.param("not-ascii", '{"version": 1, "roots": {"home": [], "checkout": ["é"]}}'.encode("utf-8"),
                 "does not parse", id="not-ascii"),
    pytest.param("wrong-version", json.dumps({"version": 2, "roots": {"home": [], "checkout": []}}).encode(),
                 "is not version 1", id="wrong-version"),
    pytest.param("true-version", json.dumps({"version": True, "roots": {"home": [], "checkout": []}}).encode(),
                 "is not version 1", id="boolean-version"),
    pytest.param("not-an-object", b"[]", "is not version 1", id="not-an-object-document"),
    pytest.param("no-roots", b'{"version": 1, "roots": []}', "has no roots", id="no-roots"),
    pytest.param("missing-root", b'{"version": 1, "roots": {"home": []}}', "lacks the checkout root", id="missing-root"),
    pytest.param("entry-not-object", b'{"version": 1, "roots": {"home": [1], "checkout": []}}',
                 "a home entry is not an object", id="entry-not-an-object"),
    pytest.param("no-path", json.dumps(with_home({"type": "reg"})).encode(), "a home entry has no path", id="entry-without-path"),
    pytest.param("no-type", json.dumps(with_home({"path": "a"})).encode(), "has no known type", id="entry-without-type"),
    pytest.param("odd-type", json.dumps(with_home(rec("a", type="weird"))).encode(), "has no known type", id="entry-odd-type"),
    pytest.param("no-integer", json.dumps(with_home(rec("a", mode="x"))).encode(), "has no integer mode", id="entry-text-for-a-number"),
    pytest.param("bool-integer", json.dumps(with_home(rec("a", size=True))).encode(), "has no integer size", id="entry-boolean-for-a-number"),
    pytest.param("bad-target", json.dumps(with_home(rec("a", target=5))).encode(), "has a bad target", id="entry-bad-target"),
    pytest.param("bad-sha", json.dumps(with_home(rec("a", sha256="zz"))).encode(), "has a bad sha256", id="entry-bad-sha256"),
    pytest.param("twice", json.dumps(with_home(rec("a"), rec("a"))).encode(), "lists a twice", id="duplicate-path"),
])
def test_c5_compare_exits_non_zero_and_prints_nothing_on_bad_input(tmp_path, name, raw, words):
    good = manifest_file(tmp_path, "b.json", [rec("a")])
    done = compare_run(good, manifest_file(tmp_path, "bad.json", raw=raw))
    assert done.returncode == 1 and done.stdout == "", (done.stdout, done.stderr)
    assert words in done.stderr, done.stderr


def test_c5_compare_refuses_a_digest_that_is_not_the_files(tmp_path):
    # Mutation M17 (no digest check), on either side.
    good = manifest_file(tmp_path, "b.json", [rec("a")])
    other = manifest_file(tmp_path, "o.json", [rec("b")])
    for name, pair in {"after": (good, (other[0], good[1])), "before": ((good[0], other[1]), other)}.items():
        done = compare_run(*pair)
        assert done.returncode == 1 and done.stdout == "", (name, done.stdout)
        assert "the %s manifest does not match its digest" % name in done.stderr, (name, done.stderr)


def test_c5_compare_refuses_a_digest_that_is_not_a_sha256_and_a_wrong_argument_count(tmp_path):
    good = manifest_file(tmp_path, "b.json", [rec("a")])
    done = compare_run(good, (good[0], "not-a-digest"))
    assert done.returncode == 1 and "the after digest is not a sha256" in done.stderr
    done = subprocess.run([sys.executable, "-I", "-c", embedded("MANIFEST_COMPARE"), "compare", good[0]],
                          capture_output=True, text=True, timeout=30)
    assert done.returncode == 1 and "usage: compare" in done.stderr and done.stdout == ""


COMPARE_HARNESS = r'''
import builtins, io, sys

# A test-only program: it runs the compare's own source with the open of the before
# manifest replaced by one that, as another process could, rewrites the file with forged
# bytes right after its true bytes were read. The production script has no hook for this.
source_file, target, forged_file = sys.argv[1:4]
namespace = {"__name__": "manifest_compare"}
with open(source_file, encoding="utf-8") as handle:
    exec(compile(handle.read(), "manifest-compare", "exec"), namespace)
real_open = builtins.open
state = {"swapped": False}


def swapping_open(path, mode="r", *rest, **named):
    if not state["swapped"] and str(path) == target and "b" in mode:
        state["swapped"] = True
        with real_open(path, "rb") as handle:
            data = handle.read()
        with real_open(forged_file, "rb") as handle:
            forged = handle.read()
        with real_open(path, "wb") as handle:
            handle.write(forged)
        return io.BytesIO(data)
    return real_open(path, mode, *rest, **named)


builtins.open = swapping_open
sys.exit(namespace["main"](["compare"] + sys.argv[4:8]))
'''


def test_c6_compare_never_parses_bytes_it_did_not_hash(tmp_path):
    # Mutation M22 (parse the manifest from a second read instead of the hashed buffer).
    before = manifest_file(tmp_path, "b.json", [rec("a")])
    after = manifest_file(tmp_path, "a.json", [rec("a"), rec("added")])
    forged = tmp_path / "forged.json"
    forged.write_bytes(Path(after[0]).read_bytes())
    source, harness = tmp_path / "compare.py", tmp_path / "compare-harness.py"
    source.write_text(embedded("MANIFEST_COMPARE"), encoding="utf-8")
    harness.write_text(COMPARE_HARNESS, encoding="utf-8")
    done = subprocess.run([sys.executable, "-I", str(harness), str(source), before[0], str(forged),
                           before[0], after[0], before[1], after[1]], capture_output=True, text=True, timeout=30)
    # The forged before manifest equals the after one: parsed, it would read SAME.
    assert done.stdout != "SAME\n", "the forged bytes were parsed"
    assert done.returncode != 0 or done.stdout == 'DIFF 1\nhome "added" added reg\n', (done.stdout, done.stderr)


@pytest.mark.parametrize("mode, extra, words", [
    # Each mode leaves the real compare's run alone and spoils what the probe reads from it.
    pytest.param("compare-fail", [], "the manifest compare failed: exit 7", id="compare-exits-non-zero"),
    pytest.param("compare-hang", ["--timeout", "2"], "the manifest compare failed: timed out after 2s", id="compare-hangs"),
    pytest.param("compare-noise", [], "its output is neither SAME nor DIFF <n> with its lines", id="compare-prints-noise"),
    pytest.param("compare-extra", [], "its output is neither SAME nor DIFF <n> with its lines", id="compare-prints-more-than-SAME"),
    pytest.param("compare-short", [], "the manifest compare failed: DIFF 3 is followed by 1 line(s)", id="compare-count-is-not-its-lines"),
])
def test_c_manifest_is_unverified_when_the_compare_fails_or_prints_what_it_should_not(world, mode, extra, words):
    # Mutations: M40 (ignore the compare's exit), M39 (no alarm around the compare), M41 (no check that the
    # first line is SAME or DIFF <n>), M43 (SAME with more after it read as SAME), M42 (no count of lines).
    result = world.run("--only", "C", *extra, pyshim=mode)
    assert result.status("C") == "UNVERIFIED", result.out
    assert words in result.evidence("C")
    assert result.rc == 2


def test_c_manifest_is_unverified_when_the_helper_prints_no_digest(world):
    # Mutation M38 (no check that the helper printed a sha256 and wrote a manifest).
    result = world.run("--only", "C", pyshim="no-digest")
    assert result.status("C") == "UNVERIFIED", result.out
    assert "the after snapshot failed: the helper printed no sha256 of a manifest file" in result.evidence("C")


@pytest.mark.parametrize("args, words", [
    pytest.param([], "usage: snapshot", id="no-arguments"),
    pytest.param(["m.json", "home", "checkout"], "the home root is not an absolute path", id="relative-path"),
    pytest.param(["/tmp/never-written.json", "/", "/tmp"], "the home root has no final name", id="root-of-the-filesystem"),
])
def test_w7_the_helper_refuses_a_bad_command_line(tmp_path, args, words):
    # Mutations: M30 (no argument count check), M31 (no absolute path check), M32 (no final name check).
    (tmp_path / "home").mkdir()
    (tmp_path / "checkout").mkdir()
    done = subprocess.run([sys.executable, "-I", "-c", embedded("SNAPSHOT_HELPER"), "snapshot", *args],
                          capture_output=True, text=True, timeout=30, cwd=tmp_path)
    assert done.returncode == 1 and done.stdout == ""
    assert words in done.stderr, done.stderr
