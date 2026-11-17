"""Fixture-home tests of the harness plugin's plan, apply and remove script.

Homes are pytest tmp_path folders. A stub `claude` on PATH stands in for
`claude plugin configure`. Most runs go through a small driver that imports
setup.py in a child process, wraps the os calls setup makes (open with
O_CREAT, write, fsync, fchmod, link, replace, unlink, mkdir, rmdir), logs
each one as a numbered point, and at a chosen point can SIGKILL the child
(a hard stop), raise an exception, make a short write, or run a test action.
Nothing in setup.py knows about the driver.
"""
from __future__ import annotations

import errno
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "plugins" / "harness" / "setup.py"
RULES_SOURCE = ROOT / "plugins" / "harness" / "files" / "harness.md"
OVERRIDE_SOURCE = ROOT / "plugins" / "harness" / "files" / "harness-local.md"

_spec = importlib.util.spec_from_file_location("harness_setup_under_test", SCRIPT)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

OPERATOR = "operator"
HARNESS = "claude-code"
PRE_SETTINGS = (json.dumps({"model": "kept", "permissions": {"deny": ["Read(seeded)"]}},
                           indent=2) + "\n").encode("utf-8")
MUTATING = {"create", "write", "fchmod", "link", "replace", "unlink", "mkdir", "rmdir"}
FOLDER_OPS = {"mkdir", "link", "replace", "unlink", "rmdir"}
CONFIGURE_STAYS = "the ledger options claude plugin configure set stay set"

DRIVER = r'''
import errno, importlib.util, json, os, signal, sys
spec = json.loads(sys.argv[1])
hooks = spec.get("hooks", {})
R = {name: getattr(os, name) for name in (
    "open", "write", "fsync", "fchmod", "link", "replace", "unlink", "mkdir",
    "rmdir", "close", "fstat")}
trace = R["open"](spec["trace"], os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
if "umask" in hooks:
    os.umask(hooks["umask"])
paths = {}
count = [0]
quiet = [False]


def at(dir_fd, name):
    if not isinstance(name, str) or os.path.isabs(name) or dir_fd is None:
        return name if isinstance(name, str) else None
    base = paths.get(dir_fd)
    return f"{base}/{name}" if base is not None else None


def note(event):
    count[0] += 1
    event["n"] = count[0]
    R["write"](trace, (json.dumps(event) + "\n").encode())
    return count[0]


def after(n):
    action = hooks.get("act_after", {}).get(str(n))
    if action:
        quiet[0] = True
        try:
            exec(action, {"os": os, "home": hooks.get("home"), "Path": __import__("pathlib").Path})
        finally:
            quiet[0] = False
    if hooks.get("kill_after") == n:
        os.kill(os.getpid(), signal.SIGKILL)


def counted(op, path, perform, extra=None):
    n = count[0] + 1
    if n in hooks.get("raise_at", []):
        note({"op": op, "path": path, "raised": True})
        if hooks.get("raise_type") == "ValueError":
            raise ValueError("injected")
        raise OSError(errno.EIO, "injected")
    try:
        result = perform(n)
    except OSError as exc:
        note({"op": op, "path": path, "error": exc.errno})
        raise
    event = {"op": op, "path": path}
    if extra is not None:
        event.update(extra(result))
    note(event)
    after(n)
    return result


def w_open(path, flags, mode=0o777, *, dir_fd=None):
    if quiet[0]:
        return R["open"](path, flags, mode, dir_fd=dir_fd)
    full = at(dir_fd, path)
    if not flags & os.O_CREAT:
        fd = R["open"](path, flags, mode, dir_fd=dir_fd)
        paths[fd] = full
        return fd

    def perform(n):
        fd = R["open"](path, flags, mode, dir_fd=dir_fd)
        paths[fd] = full
        return fd

    return counted("create", full, perform,
                   lambda fd: {"mode": R["fstat"](fd).st_mode & 0o7777})


def w_write(fd, data):
    if quiet[0]:
        return R["write"](fd, data)
    data = bytes(data)

    def perform(n):
        if hooks.get("zero_at") == n:
            return 0
        if hooks.get("short_at") == n and len(data) > 1:
            return R["write"](fd, data[: len(data) // 2])
        return R["write"](fd, data)

    return counted("write", paths.get(fd), perform, lambda got: {"bytes": got})


def w_fsync(fd):
    if quiet[0]:
        return R["fsync"](fd)
    return counted("fsync", paths.get(fd), lambda n: R["fsync"](fd))


def w_fchmod(fd, mode):
    if quiet[0]:
        return R["fchmod"](fd, mode)
    return counted("fchmod", paths.get(fd), lambda n: R["fchmod"](fd, mode),
                   lambda _r: {"mode": mode})


def w_link(src, dst, *, src_dir_fd=None, dst_dir_fd=None, follow_symlinks=True):
    if quiet[0]:
        return R["link"](src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd,
                         follow_symlinks=follow_symlinks)
    return counted("link", at(dst_dir_fd, dst),
                   lambda n: R["link"](src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd,
                                       follow_symlinks=follow_symlinks),
                   lambda _r: {"src": at(src_dir_fd, src)})


def w_replace(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
    if quiet[0]:
        return R["replace"](src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)
    return counted("replace", at(dst_dir_fd, dst),
                   lambda n: R["replace"](src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd),
                   lambda _r: {"src": at(src_dir_fd, src)})


def simple(op):
    def wrapper(path, *args, dir_fd=None):
        if quiet[0]:
            return R[op](path, *args, dir_fd=dir_fd)
        return counted(op, at(dir_fd, path), lambda n: R[op](path, *args, dir_fd=dir_fd))
    return wrapper


def w_close(fd):
    paths.pop(fd, None)
    return R["close"](fd)


loaded = importlib.util.spec_from_file_location("setup_under_driver", spec["script"])
mod = importlib.util.module_from_spec(loaded)
loaded.loader.exec_module(mod)
names = {base: list(given) for base, given in hooks.get("temp_names", {}).items()}
real_temp_name = mod.temp_name
mod.temp_name = lambda base: names[base].pop(0) if names.get(base) else real_temp_name(base)
os.open, os.write, os.fsync, os.fchmod = w_open, w_write, w_fsync, w_fchmod
os.link, os.replace, os.close = w_link, w_replace, w_close
os.unlink, os.mkdir, os.rmdir = simple("unlink"), simple("mkdir"), simple("rmdir")
sys.exit(mod.main(spec["argv"]))
'''

CLAUDE_OK = """#!/bin/sh
here=$(dirname "$0")
cat > "$here/claude-stdin"
printf '%s\\n' "$@" > "$here/claude-argv"
"""
CLAUDE_FAIL = """#!/bin/sh
cat > /dev/null
echo "configure boom" >&2
exit 3
"""
CLAUDE_SLEEP = """#!/bin/sh
here=$(dirname "$0")
echo $$ > "$here/claude-pid.tmp"
mv "$here/claude-pid.tmp" "$here/claude-pid"
exec sleep 30
"""


# ---------------------------------------------------------------------------
# Helpers


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def install(folder: Path, body: str = CLAUDE_OK) -> Path:
    bin_dir = folder / "bin"
    bin_dir.mkdir(exist_ok=True)
    claude = bin_dir / "claude"
    claude.write_text(body, encoding="utf-8")
    claude.chmod(0o755)
    return bin_dir


def make_home(tmp_path: Path, settings: bytes | None = PRE_SETTINGS, *,
              mode: int = 0o644, name: str = "home") -> Path:
    home = tmp_path / name
    home.mkdir()
    home = home.resolve()
    if settings is not None:
        (home / ".claude").mkdir()
        target = home / ".claude" / "settings.json"
        target.write_bytes(settings)
        target.chmod(mode)
    return home


def fresh(tmp_path: Path, tag: str, settings: bytes | None = PRE_SETTINGS) -> Path:
    folder = tmp_path / tag
    folder.mkdir()
    return make_home(folder, settings)


def safe_path(bin_dir: Path | None) -> str:
    """PATH for a run: the stub folder and the system folders only, so no
    real `claude` is ever found."""
    system = os.pathsep.join(("/usr/bin", "/bin"))
    return system if bin_dir is None else str(bin_dir) + os.pathsep + system


def at(home: Path, rel: str) -> Path:
    return home.joinpath(*rel.split("/"))


def snap(home: Path) -> dict[str, tuple]:
    found: dict[str, tuple] = {}
    for dirpath, dirnames, filenames in os.walk(home):
        for name in dirnames + filenames:
            path = Path(dirpath) / name
            rel = path.relative_to(home).as_posix()
            info = os.lstat(path)
            if stat.S_ISLNK(info.st_mode):
                found[rel] = ("link", os.readlink(path))
            elif stat.S_ISDIR(info.st_mode):
                found[rel] = ("dir", stat.S_IMODE(info.st_mode))
            else:
                found[rel] = ("file", stat.S_IMODE(info.st_mode), sha(path.read_bytes()))
    return found


def unchanged(before: dict, after: dict) -> bool:
    """Equal apart from a .claude the lock created and the lock file."""
    after = dict(after)
    after.pop(mod.LOCK_REL, None)
    if ".claude" not in before:
        after.pop(".claude", None)
    return before == after


def run(verb: str, home: Path, *extra: str, bin_dir: Path | None = None,
        hooks: dict | None = None, timeout: float = 60,
        operator: str | None = OPERATOR) -> SimpleNamespace:
    folder = home.parent
    driver = folder / "driver.py"
    if not driver.exists():
        driver.write_text(DRIVER, encoding="utf-8")
    index = len(list(folder.glob("trace-*.jsonl")))
    trace = folder / f"trace-{index}.jsonl"
    argv = [verb, "--home", str(home)]
    if verb in ("plan", "apply") and operator is not None:
        argv += ["--operator", operator, "--harness", HARNESS]
    argv += list(extra)
    hooks = dict(hooks or {})
    hooks.setdefault("home", str(home))
    env = {key: value for key, value in os.environ.items() if key != "CLAUDE_CONFIG_DIR"}
    env["PATH"] = safe_path(bin_dir)
    spec ={"script": str(SCRIPT), "argv": argv, "hooks": hooks, "trace": str(trace)}
    done = subprocess.run([sys.executable, str(driver), json.dumps(spec)],
                          capture_output=True, text=True, env=env, timeout=timeout)
    events = []
    if trace.exists():
        events = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    return SimpleNamespace(rc=done.returncode, out=done.stdout, err=done.stderr,
                           events=events)


def apply_all(home: Path, bin_dir: Path, **kw) -> SimpleNamespace:
    return run("apply", home, "--approve-all", bin_dir=bin_dir, **kw)


def points(events: list[dict], op: str, rel_suffix: str = "") -> list[int]:
    return [e["n"] for e in events if e["op"] == op and (e.get("path") or "").endswith(rel_suffix)]


def first_point(events: list[dict], op: str, rel_suffix: str = "") -> int:
    found = points(events, op, rel_suffix)
    assert found, f"no {op} point on {rel_suffix} in the trace"
    return found[0]


def temp_points(events: list[dict], base: str) -> list[int]:
    return [e["n"] for e in events
            if e["op"] == "create" and f"/.{base}.harness-" in (e.get("path") or "")]


def writes_before_first(events: list[dict], home: Path) -> list[dict]:
    """Mutating points inside the home other than the lock and .claude."""
    allowed = {str(home / ".claude"), str(at(home, mod.LOCK_REL))}
    return [e for e in events
            if e["op"] in MUTATING and (e.get("path") or "").startswith(str(home) + "/")
            and e["path"] not in allowed]


def record_bytes(home: Path) -> bytes | None:
    path = at(home, mod.RECORD_REL)
    return path.read_bytes() if path.exists() else None


def hard_stop_line(home: Path, state: str) -> str:
    backup = str(at(home, mod.BACKUP_REL))
    shown = {
        "present": f"{backup} (present)",
        "absent": f"{backup} (absent: settings.json was not changed)",
        "none": "none (settings.json did not exist)",
    }[state]
    return ("setup: an earlier setup run stopped before it finished; "
            f"record: {at(home, mod.RECORD_REL)}; settings backup: {shown}; "
            'see KNOWN-LIMITS "After a hard stop" to recover by hand\n')


def one_refusal(done: SimpleNamespace) -> str:
    assert done.rc == 1, (done.rc, done.err)
    lines = done.err.splitlines()
    assert len(lines) == 1 and lines[0].startswith("setup: "), done.err
    assert "Traceback" not in done.err
    return lines[0]


def no_temps(home: Path) -> list[str]:
    return [rel for rel in snap(home) if ".harness-" in rel.rsplit("/", 1)[-1]]


# ---------------------------------------------------------------------------
# Plan: two files, three ledger options, each optional item as exact text.


def test_plan_writes_nothing_and_takes_no_lock(tmp_path):
    home = make_home(tmp_path, None)
    done = run("plan", home)
    assert done.rc == 0, done.err
    assert snap(home) == {}
    assert [e for e in done.events if e["op"] in MUTATING and e.get("path")] == []


def test_plan_prints_two_files_options_and_items_as_exact_text(tmp_path):
    home = make_home(tmp_path, None)
    done = run("plan", home)
    assert done.rc == 0, done.err
    rules = RULES_SOURCE.read_text(encoding="utf-8").rstrip("\n")
    override = OVERRIDE_SOURCE.read_text(encoding="utf-8").rstrip("\n")
    assert f"FILE {mod.RULES_REL}\n----\n{rules}\n----\n" in done.out
    assert f"FILE {mod.OVERRIDE_REL}\n----\n{override}\n----\n" in done.out
    values = {"database": str(home / ".local" / "share" / "ledger" / "ledger.db"),
              "operator": OPERATOR, "harness": HARNESS}
    assert json.dumps(values, indent=2) in done.out
    assert f"claude plugin configure {mod.LEDGER_PLUGIN} --values-stdin" in done.out
    for ident, text in mod.optional_items():
        assert f"OPTIONAL {ident}\n----\n{text}\n----\n" in done.out
    lower = done.out.lower()
    for word in ("allow(", "additionaldirectories", "extradirectories", "autoallow"):
        assert word not in lower


def test_proposals_grant_nothing_and_the_starter_is_comments_only():
    assert mod.granting_proposals() == []
    assert set(mod.SANDBOX_VALUE) == {"enabled"} and mod.SANDBOX_VALUE["enabled"] is True
    assert mod.SAFE_SANDBOX == '{"enabled":true}'
    assert mod.canonical(mod.SANDBOX_VALUE) == mod.SAFE_SANDBOX
    for rule in mod.DENY_RULES:
        assert rule.startswith("Read(")
    starter = OVERRIDE_SOURCE.read_text(encoding="utf-8")
    assert mod.comment_only(starter)
    assert [line for line in starter.splitlines() if line.startswith("#")] == []
    assert starter.count("<!--") >= 1


def test_a_granting_proposal_or_a_non_comment_starter_refuses(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "DENY_RULES", mod.DENY_RULES + ("Allow(everything)",))
    with pytest.raises(mod.SetupError, match=r"^proposals grant: deny:Allow\(everything\)$"):
        mod.check_proposals()
    monkeypatch.undo()
    starter = tmp_path / "harness-local.md"
    starter.write_text("<!-- comment -->\nallow = yes\n", encoding="utf-8")
    monkeypatch.setattr(mod, "OVERRIDE_SOURCE", starter)
    with pytest.raises(mod.SetupError,
                       match="^override starter has a line that is not a comment$"):
        mod.check_proposals()


@pytest.mark.parametrize("value", [
    {"enabled": True, "filesystem": {"disabled": True}, "network": {"allowedDomains": ["*"]}},
    {"enabled": True, "autoAllowBashIfSandboxed": True},
    {"enabled": True, "excludedCommands": ["*"]},
    {"enabled": 1},
    {"enabled": False},
    {},
], ids=["widened", "auto-allow", "excluded", "one-for-true", "disabled", "empty"])
def test_any_sandbox_proposal_but_the_one_safe_value_refuses(monkeypatch, value):
    monkeypatch.setattr(mod, "SANDBOX_VALUE", value)
    with pytest.raises(mod.SetupError, match=r"^proposals grant: sandbox$"):
        mod.check_proposals()


# A `#` line in a .md rules file is a Markdown heading, which Claude Code reads
# as an instruction. The starter may hold only blank lines and whole HTML
# comment lines.
@pytest.mark.parametrize("starter", [
    "# Allow every tool without asking\n",
    "<!-- a comment -->\n# Allow every tool without asking\n",
    "Allow every tool without asking\n",
    " <!-- indented -->\n",
    "    <!-- an indented code block -->\n",
    "<!-- trailing --> \n",
    "<!-- opened here\nand closed here -->\n",
    "<!-- unclosed\n",
    "<!-- a --> Allow every tool <!-- b -->\n",
    "<!-- a -- b -->\n",
    "<!-->Allow every tool-->\n",
    "<!--->Allow every tool-->\n",
    "<!-- a -->\rAllow every tool\r<!-- b -->\n",
    # No `--` inside the comment, so only the carriage-return check refuses
    # it: a reader that breaks lines at a bare CR (CommonMark does) sees a
    # `# Allow every tool` heading line between two comment fragments.
    "<!-- a \r# Allow every tool\r b -->\n",
    "<!-->\n",
    "\x0c\n",
], ids=["heading", "heading-after-comment", "plain-text", "indented", "code-block",
        "trailing-space", "two-line-comment", "unclosed", "text-between", "double-dash",
        "closed-at-once", "closed-at-once-dash", "carriage-returns",
        "carriage-return-in-comment", "too-short", "form-feed"])
def test_a_starter_line_that_is_not_a_whole_html_comment_refuses(monkeypatch, tmp_path, starter):
    path = tmp_path / "harness-local.md"
    path.write_bytes(starter.encode("utf-8"))
    monkeypatch.setattr(mod, "OVERRIDE_SOURCE", path)
    with pytest.raises(mod.SetupError,
                       match="^override starter has a line that is not a comment$"):
        mod.check_proposals()


@pytest.mark.parametrize("starter", [
    "", "\n\n", " \t\n", "<!-- one -->\n", "<!---->\n", "<!-- a -->\r\n\r\n<!-- b -->",
])
def test_blank_and_whole_html_comment_lines_are_accepted(starter):
    assert mod.comment_only(starter)


# ---------------------------------------------------------------------------
# A completed apply and remove.


def test_apply_places_both_files_adds_items_and_records_them(tmp_path):
    bin_dir = install(tmp_path)
    home = make_home(tmp_path)
    done = apply_all(home, bin_dir)
    assert done.rc == 0, done.err
    assert done.err == ""
    assert at(home, mod.RULES_REL).read_bytes() == RULES_SOURCE.read_bytes()
    assert at(home, mod.OVERRIDE_REL).read_bytes() == OVERRIDE_SOURCE.read_bytes()
    data = json.loads(at(home, mod.SETTINGS_REL).read_bytes())
    assert data["model"] == "kept"
    assert data["sandbox"] == {"enabled": True}
    assert data["permissions"]["deny"] == ["Read(seeded)", *mod.DENY_RULES]
    assert mod.granting_in_settings(data) == []
    raw = record_bytes(home)
    head, body = mod.parse_record(raw)
    assert body is not None
    assert raw.startswith(mod.header_line(mod.BACKUP_REL))
    assert {item["path"] for item in body["files"]} == {mod.RULES_REL, mod.OVERRIDE_REL}
    deny = [item for item in body["settings"]["items"] if item["kind"] == "deny"]
    assert deny[0] == {"kind": "deny", "value": mod.DENY_RULES[0], "index": 1,
                       "before": "Read(seeded)", "after": mod.DENY_RULES[1]}
    assert deny[-1]["after"] is None
    assert not at(home, mod.BACKUP_REL).exists()
    assert no_temps(home) == []
    assert (bin_dir / "claude-argv").read_text().split() == [
        "plugin", "configure", mod.LEDGER_PLUGIN, "--values-stdin"]
    assert json.loads((bin_dir / "claude-stdin").read_text()) == {
        "database": str(home / ".local" / "share" / "ledger" / "ledger.db"),
        "operator": OPERATOR, "harness": HARNESS}


def test_apply_keeps_an_existing_override_and_does_not_list_it(tmp_path):
    bin_dir = install(tmp_path)
    home = make_home(tmp_path)
    override = at(home, mod.OVERRIDE_REL)
    override.parent.mkdir()
    override.write_bytes(b"# mine\n")
    done = apply_all(home, bin_dir)
    assert done.rc == 0, done.err
    assert f"kept: {override} (already existed)" in done.out
    assert override.read_bytes() == b"# mine\n"
    _head, body = mod.parse_record(record_bytes(home))
    assert [item["path"] for item in body["files"]] == [mod.RULES_REL]
    assert run("remove", home).rc == 0
    assert override.read_bytes() == b"# mine\n"


def test_apply_never_changes_a_present_key_and_appends_absent_rules_only(tmp_path):
    bin_dir = install(tmp_path)
    present = mod.DENY_RULES[2]
    home = make_home(tmp_path, json.dumps({
        "sandbox": {"enabled": False},
        "permissions": {"allow": ["Read(seeded)"], "deny": ["Read(a)", present, "Read(b)"]},
        "autoAllow": True}, indent=2).encode() + b"\n")
    done = apply_all(home, bin_dir)
    assert done.rc == 0, done.err
    data = json.loads(at(home, mod.SETTINGS_REL).read_bytes())
    assert data["sandbox"] == {"enabled": False}
    assert data["permissions"]["allow"] == ["Read(seeded)"]
    assert data["autoAllow"] is True
    deny = data["permissions"]["deny"]
    assert deny[:3] == ["Read(a)", present, "Read(b)"]
    assert deny[3:] == [rule for rule in mod.DENY_RULES if rule != present]


def test_settings_keep_non_ascii_text(tmp_path):
    bin_dir = install(tmp_path)
    home = make_home(tmp_path, (json.dumps({"model": "café"}, indent=2,
                                           ensure_ascii=False) + "\n").encode())
    assert apply_all(home, bin_dir).rc == 0
    text = at(home, mod.SETTINGS_REL).read_text(encoding="utf-8")
    assert "café" in text and "\\u00e9" not in text


def test_remove_restores_the_home_exactly(tmp_path):
    bin_dir = install(tmp_path)
    for settings in (PRE_SETTINGS, None):
        home = make_home(tmp_path, settings, name=f"home-{settings is None}")
        before = snap(home)
        assert apply_all(home, bin_dir).rc == 0
        done = run("remove", home)
        assert done.rc == 0, done.err
        assert done.err == ""
        assert unchanged(before, snap(home))
        assert f"removed: {at(home, mod.RECORD_REL)}" in done.out
        assert f"kept: ledger database at {home}/.local/share/ledger/ledger.db" in done.out


def test_sandbox_only_apply_and_remove(tmp_path):
    bin_dir = install(tmp_path)
    home = make_home(tmp_path, b'{\n  "model": "kept"\n}\n')
    before = snap(home)
    assert run("apply", home, "--approve", "sandbox", bin_dir=bin_dir).rc == 0
    data = json.loads(at(home, mod.SETTINGS_REL).read_bytes())
    assert data == {"model": "kept", "sandbox": {"enabled": True}}
    assert run("remove", home).rc == 0
    assert unchanged(before, snap(home))


def test_apply_with_no_approvals_leaves_settings_alone(tmp_path):
    bin_dir = install(tmp_path)
    home = make_home(tmp_path)
    done = run("apply", home, bin_dir=bin_dir)
    assert done.rc == 0, done.err
    assert at(home, mod.SETTINGS_REL).read_bytes() == PRE_SETTINGS
    assert points(done.events, "replace") == []


def test_remove_keeps_a_rules_folder_that_existed(tmp_path):
    bin_dir = install(tmp_path)
    home = make_home(tmp_path)
    at(home, mod.RULES_DIR_REL).mkdir()
    assert apply_all(home, bin_dir).rc == 0
    assert run("remove", home).rc == 0
    assert at(home, mod.RULES_DIR_REL).is_dir()
    assert list(at(home, mod.RULES_DIR_REL).iterdir()) == []


# ---------------------------------------------------------------------------
# Command line and simple refusals.


@pytest.mark.parametrize("argv, message", [
    (["apply", "--home", "relative"], "setup: home must be an absolute path"),
    (["remove", "--home", "/nonexistent-harness-home"],
     "setup: home is not a folder: /nonexistent-harness-home"),
    (["apply", "--home", "{home}"], "setup: --operator is required for plan and apply"),
    (["frobnicate", "--home", "{home}"], "setup: argument verb: invalid choice"),
    (["apply", "--home", "{home}", "--operator", "o", "--approve", "allow:x"],
     "setup: unknown approval 'allow:x'"),
    (["apply", "--home", "{home}", "--operator", "o", "--approve-all", "--approve", "allow:x"],
     "setup: unknown approval 'allow:x'"),
], ids=["relative-home", "home-not-a-folder", "no-operator", "bad-verb", "unknown-approval",
        "unknown-approval-with-approve-all"])
def test_command_line_refusals_are_one_named_line(tmp_path, argv, message):
    bin_dir = install(tmp_path)
    home = make_home(tmp_path)
    before = snap(home)
    argv = [part.replace("{home}", str(home)) for part in argv]
    env = dict(os.environ, PATH=safe_path(bin_dir))
    done = subprocess.run([sys.executable, str(SCRIPT), *argv], capture_output=True,
                          text=True, env=env, timeout=30)
    line = one_refusal(SimpleNamespace(rc=done.returncode, err=done.stderr))
    assert line.startswith(message)
    assert unchanged(before, snap(home))


def test_claude_missing_refuses_before_any_write(tmp_path):
    home = make_home(tmp_path)
    before = snap(home)
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    env = dict(os.environ, PATH=safe_path(empty))
    done = subprocess.run([sys.executable, str(SCRIPT), "apply", "--home", str(home),
                           "--operator", OPERATOR], capture_output=True, text=True,
                          env=env, timeout=30)
    assert one_refusal(SimpleNamespace(rc=done.returncode, err=done.stderr)) == (
        "setup: claude is not on PATH")
    assert unchanged(before, snap(home))


@pytest.mark.parametrize("content, reason", [
    (b"\xff\xfe{}", "is not valid UTF-8"),
    (b"{not json", "is not valid JSON"),
    (b'{"a": 1, "a": 2}', "is not valid JSON"),
    (b"[1, 2]", "is not a JSON object"),
], ids=["not-utf8", "not-json", "duplicate-key", "not-an-object"])
def test_invalid_settings_refuse_by_name_before_any_write(tmp_path, content, reason):
    bin_dir = install(tmp_path)
    home = make_home(tmp_path, content)
    before = snap(home)
    done = apply_all(home, bin_dir)
    line = one_refusal(done)
    assert line.startswith(f"setup: {at(home, mod.SETTINGS_REL)} {reason}")
    assert writes_before_first(done.events, home) == []
    assert unchanged(before, snap(home))
    assert not (bin_dir / "claude-argv").exists()


def test_a_settings_folder_refuses_as_not_a_regular_file(tmp_path):
    bin_dir = install(tmp_path)
    home = make_home(tmp_path, None)
    at(home, mod.SETTINGS_REL).mkdir(parents=True)
    done = apply_all(home, bin_dir)
    assert one_refusal(done) == f"setup: {at(home, mod.SETTINGS_REL)} is not a regular file"


def test_apply_refuses_an_existing_rules_default_before_any_write(tmp_path):
    bin_dir = install(tmp_path)
    home = make_home(tmp_path)
    rules = at(home, mod.RULES_REL)
    rules.parent.mkdir()
    rules.write_bytes(b"# already\n")
    before = snap(home)
    done = apply_all(home, bin_dir)
    assert one_refusal(done) == f"setup: rules default already exists: {rules}"
    assert writes_before_first(done.events, home) == []
    assert unchanged(before, snap(home))


def test_a_second_apply_refuses_already_applied(tmp_path):
    bin_dir = install(tmp_path)
    home = make_home(tmp_path)
    assert apply_all(home, bin_dir).rc == 0
    at(home, mod.RULES_REL).unlink()
    before = snap(home)
    done = apply_all(home, bin_dir)
    assert one_refusal(done) == "setup: already applied; run remove first"
    assert snap(home) == before


def test_remove_without_a_record_refuses(tmp_path):
    home = make_home(tmp_path)
    before = snap(home)
    done = run("remove", home)
    assert one_refusal(done) == f"setup: no record at {at(home, mod.RECORD_REL)}"
    assert unchanged(before, snap(home))


# ---------------------------------------------------------------------------
# Order.


def test_a_configure_failure_writes_nothing_of_setups(tmp_path):
    bin_dir = install(tmp_path, CLAUDE_FAIL)
    home = make_home(tmp_path)
    before = snap(home)
    done = apply_all(home, bin_dir)
    assert one_refusal(done) == "setup: claude plugin configure failed: configure boom"
    assert writes_before_first(done.events, home) == []
    assert unchanged(before, snap(home))


@pytest.mark.parametrize("case", ["approval", "rules", "settings", "claude"])
def test_preconditions_run_before_the_first_write(tmp_path, case):
    bin_dir = install(tmp_path)
    home = make_home(tmp_path, b"{bad" if case == "settings" else PRE_SETTINGS)
    extra = ["--approve-all"]
    if case == "approval":
        extra = ["--approve", "sandbox", "--approve", "nope"]
    if case == "rules":
        at(home, mod.RULES_DIR_REL).mkdir()
        at(home, mod.RULES_REL).write_bytes(b"x")
    if case == "claude":
        (bin_dir / "claude").unlink()
    done = run("apply", home, *extra, bin_dir=bin_dir)
    one_refusal(done)
    assert writes_before_first(done.events, home) == []


def test_settings_that_configure_made_invalid_refuse_by_name(tmp_path):
    home = make_home(tmp_path)
    bin_dir = install(tmp_path, "#!/bin/sh\ncat > /dev/null\n"
                      f"printf '{{bad' > '{at(home, mod.SETTINGS_REL)}'\n")
    done = apply_all(home, bin_dir)
    line = one_refusal(done)
    assert line.startswith(f"setup: {at(home, mod.SETTINGS_REL)} is not valid JSON")
    assert line.endswith(CONFIGURE_STAYS)
    assert not at(home, mod.SETUP_DIR_REL).exists()
    assert at(home, mod.SETTINGS_REL).read_bytes() == b"{bad"


@pytest.mark.parametrize("settings, approve, problem", [
    ({"model": "kept", "permissions": None}, "deny:Read(.env)",
     "permissions is null, not an object"),
    ({"model": "kept", "permissions": {"deny": None}}, "deny:Read(.env)",
     "permissions.deny is null, not an array"),
    ({"permissions": "none"}, "deny:Read(.env)", "permissions is a string, not an object"),
    ({"permissions": {"deny": {"Read(.env)": True}}}, "deny:Read(.env)",
     "permissions.deny is an object, not an array"),
    ({"model": "kept", "sandbox": None}, "sandbox", "sandbox is null, not an object"),
    ({"sandbox": True}, "sandbox", "sandbox is a boolean, not an object"),
], ids=["permissions-null", "deny-null", "permissions-string", "deny-object", "sandbox-null",
        "sandbox-boolean"])
def test_a_present_key_of_the_wrong_type_refuses_before_any_write(tmp_path, settings, approve,
                                                                 problem):
    bin_dir = install(tmp_path)
    home = make_home(tmp_path, (json.dumps(settings, indent=2) + "\n").encode())
    before = snap(home)
    done = run("apply", home, "--approve", approve, bin_dir=bin_dir)
    assert one_refusal(done) == (
        f"setup: {at(home, mod.SETTINGS_REL)}: {problem}; setup does not overwrite it")
    assert writes_before_first(done.events, home) == []
    assert unchanged(before, snap(home))
    assert not (bin_dir / "claude-argv").exists()


def test_a_wrong_type_that_configure_made_refuses_by_name(tmp_path):
    home = make_home(tmp_path)
    settings = at(home, mod.SETTINGS_REL)
    bin_dir = install(tmp_path, "#!/bin/sh\ncat > /dev/null\n"
                      f"printf '{{\"permissions\": null}}' > '{settings}'\n")
    done = apply_all(home, bin_dir)
    assert one_refusal(done) == (
        f"setup: {settings}: permissions is null, not an object; setup does not overwrite it; "
        f"{CONFIGURE_STAYS}")
    assert not at(home, mod.SETUP_DIR_REL).exists()
    assert settings.read_bytes() == b'{"permissions": null}'


def test_a_wrong_type_no_approved_item_touches_is_left_alone(tmp_path):
    bin_dir = install(tmp_path)
    raw = b'{\n  "permissions": null\n}\n'
    home = make_home(tmp_path, raw)
    before = snap(home)
    done = run("apply", home, "--approve", "sandbox", bin_dir=bin_dir)
    assert done.rc == 0, done.err
    data = json.loads(at(home, mod.SETTINGS_REL).read_bytes())
    assert data == {"permissions": None, "sandbox": {"enabled": True}}
    assert run("remove", home).rc == 0
    assert unchanged(before, snap(home))


def test_apply_with_every_item_present_leaves_settings_byte_identical(tmp_path):
    bin_dir = install(tmp_path)
    compact = json.dumps({"sandbox": {"enabled": True},
                          "permissions": {"deny": list(mod.DENY_RULES)}},
                         separators=(",", ":")).encode()
    home = make_home(tmp_path, compact)
    inode = at(home, mod.SETTINGS_REL).stat().st_ino
    done = apply_all(home, bin_dir)
    assert done.rc == 0, done.err
    assert at(home, mod.SETTINGS_REL).read_bytes() == compact
    assert at(home, mod.SETTINGS_REL).stat().st_ino == inode
    _head, body = mod.parse_record(record_bytes(home))
    assert body["settings"]["items"] == []


# ---------------------------------------------------------------------------
# The promise after a hard stop.


def check_hard_stop_state(tmp_path: Path, home: Path, bin_dir: Path,
                          pre: bytes | None, post: bytes, k: int) -> str:
    settings = at(home, mod.SETTINGS_REL)
    backup = at(home, mod.BACKUP_REL)
    where = f"SIGKILL after point {k}"
    # settings.json is whole, and keeps its mode.
    if settings.exists():
        now = settings.read_bytes()
        assert now in (pre, post), where
        assert stat.S_IMODE(settings.stat().st_mode) == (0o644 if pre is not None else 0o600), where
    else:
        assert pre is None, where
    # Targets are never partial.
    for rel, source in ((mod.RULES_REL, RULES_SOURCE), (mod.OVERRIDE_REL, OVERRIDE_SOURCE)):
        if at(home, rel).exists():
            assert at(home, rel).read_bytes() == source.read_bytes(), where
    raw = record_bytes(home)
    # The record is never empty or torn in line 1.
    line1 = mod.header_line(mod.BACKUP_REL if pre is not None else None)
    if raw is not None:
        assert raw.startswith(line1), where
    outside = (at(home, mod.RULES_REL).exists() or at(home, mod.OVERRIDE_REL).exists()
               or (settings.read_bytes() if settings.exists() else None) != pre)
    _head, body = mod.parse_record(raw) if raw is not None else (None, None)
    if outside and body is None:
        assert raw is not None, where
        if pre is not None:
            assert backup.exists(), f"{where}: no backup though the home changed"
            assert backup.read_bytes() == pre, where
            assert stat.S_IMODE(backup.stat().st_mode) == 0o600, where
    if body is not None:
        again = apply_all(home, bin_dir)
        assert one_refusal(again) == "setup: already applied; run remove first", where
        removed = run("remove", home)
        assert removed.rc == 0, (where, removed.err)
        assert not backup.exists(), where
        return "complete"
    if raw is not None:
        state = "present" if backup.exists() else ("absent" if pre is not None else "none")
        if state == "absent":
            assert settings.read_bytes() == pre, where
        for verb in ("apply", "remove"):
            before = snap(home)
            again = run(verb, home, "--approve-all", bin_dir=bin_dir) if verb == "apply" \
                else run(verb, home)
            assert again.rc == 1, where
            assert again.err == hard_stop_line(home, state), where
            assert snap(home) == before, where
        return "unfinished-" + state
    assert not outside, where
    assert not backup.exists(), where
    again = apply_all(home, bin_dir)
    assert again.rc == 0, (where, again.err)
    return "none"


@pytest.mark.parametrize("pre", [PRE_SETTINGS, None], ids=["settings", "no-settings"])
def test_a_sigkill_at_every_apply_point_keeps_the_promise(tmp_path, pre):
    bin_dir = install(tmp_path)
    template = make_home(tmp_path, pre, name="template")
    traced = apply_all(template, bin_dir)
    assert traced.rc == 0, traced.err
    assert no_temps(template) == []
    post = at(template, mod.SETTINGS_REL).read_bytes()
    total = len(traced.events)
    outcomes = set()
    for k in range(1, total + 1):
        home = fresh(tmp_path, f"k{k}", pre)
        killed = apply_all(home, bin_dir, hooks={"kill_after": k})
        assert killed.rc == -signal.SIGKILL, (k, killed.rc, killed.err)
        outcomes.add(check_hard_stop_state(tmp_path, home, bin_dir, pre, post, k))
    expected = {"none", "complete", "unfinished-present" if pre else "unfinished-none"}
    if pre is not None:
        expected.add("unfinished-absent")
    assert outcomes == expected


@pytest.mark.parametrize("shape", ["line1-only", "torn-line2", "third-line", "line2-not-complete",
                                   "no-newline", "bad-header", "sandbox-value-one"])
def test_an_unfinished_record_refuses_apply_and_remove(tmp_path, shape):
    bin_dir = install(tmp_path)
    home = make_home(tmp_path)
    assert apply_all(home, bin_dir).rc == 0
    raw = record_bytes(home)
    line1, line2 = raw.splitlines(keepends=True)
    shaped = {
        "line1-only": line1,
        "torn-line2": line1 + line2[: len(line2) // 2],
        "third-line": raw + line2,
        "line2-not-complete": line1 + line2.replace(b'"complete": true', b'"complete": false'),
        "no-newline": raw[:-1],
        "bad-header": line1.replace(b"harness-setup", b"harness-other") + line2,
        # `1` is not the `true` setup writes: the compare is type-strict.
        "sandbox-value-one": line1 + line2.replace(b'"value": {"enabled": true}',
                                                   b'"value": {"enabled": 1}'),
    }[shape]
    assert shaped != raw
    at(home, mod.RECORD_REL).write_bytes(shaped)
    before = snap(home)
    for verb in ("apply", "remove"):
        done = run(verb, home, *(["--approve-all"] if verb == "apply" else []), bin_dir=bin_dir)
        assert done.err == hard_stop_line(home, "absent")
        assert done.rc == 1
        assert snap(home) == before


@pytest.mark.parametrize("verb", ["apply", "remove"])
def test_a_backup_with_no_record_refuses_by_name(tmp_path, verb):
    bin_dir = install(tmp_path)
    home = make_home(tmp_path)
    backup = at(home, mod.BACKUP_REL)
    backup.parent.mkdir(parents=True)
    backup.write_bytes(b'{"old": true}\n')
    before = snap(home)
    done = run(verb, home, *(["--approve-all"] if verb == "apply" else []), bin_dir=bin_dir)
    assert one_refusal(done) == f"setup: leftover settings backup: {backup}"
    assert unchanged(before, snap(home))


def test_a_stop_after_line_2_is_complete_and_remove_deletes_the_backup(tmp_path):
    bin_dir = install(tmp_path)
    template = make_home(tmp_path, name="template")
    traced = apply_all(template, bin_dir)
    line2_sync = [e["n"] for e in traced.events
                  if e["op"] == "fsync" and (e["path"] or "").endswith(mod.RECORD_REL)][-1]
    home = make_home(tmp_path)
    before = snap(home)
    killed = apply_all(home, bin_dir, hooks={"kill_after": line2_sync})
    assert killed.rc == -signal.SIGKILL
    assert at(home, mod.BACKUP_REL).exists()
    assert one_refusal(apply_all(home, bin_dir)) == "setup: already applied; run remove first"
    removed = run("remove", home)
    assert removed.rc == 0, removed.err
    assert not at(home, mod.BACKUP_REL).exists()
    assert unchanged(before, snap(home))


def test_a_sigkill_at_every_rollback_point_keeps_record_and_backup_or_the_home(tmp_path):
    bin_dir = install(tmp_path)
    template = make_home(tmp_path, name="template")
    traced = apply_all(template, bin_dir)
    line2 = [e["n"] for e in traced.events
             if e["op"] == "write" and (e["path"] or "").endswith(mod.RECORD_REL)][-1]
    probe = make_home(tmp_path, name="probe")
    rolled = apply_all(probe, bin_dir, hooks={"raise_at": [line2]})
    one_refusal(rolled)
    total = len(rolled.events)
    assert total > line2 + 10
    seen = set()
    for m in range(line2 + 1, total + 1):
        (tmp_path / f"m{m}").mkdir()
        home = make_home(tmp_path / f"m{m}")
        before = snap(home)
        killed = apply_all(home, bin_dir, hooks={"raise_at": [line2], "kill_after": m})
        assert killed.rc == -signal.SIGKILL, m
        record = at(home, mod.RECORD_REL).exists()
        backup = at(home, mod.BACKUP_REL).exists()
        assert backup or not record, f"record without backup after rollback point {m}"
        if not record:
            assert not at(home, mod.RULES_REL).exists(), m
            assert not at(home, mod.OVERRIDE_REL).exists(), m
            assert at(home, mod.SETTINGS_REL).read_bytes() == PRE_SETTINGS, m
        if backup:
            assert at(home, mod.BACKUP_REL).read_bytes() == PRE_SETTINGS, m
        if not backup and not record:
            # Rollback is done but for pruning: only empty folders remain.
            after = {rel: v for rel, v in snap(home).items()
                     if not (rel in mod.CREATABLE_DIRS and v[0] == "dir")}
            assert unchanged(before, after), m
        seen.add((record, backup))
    assert (True, True) in seen and (False, True) in seen


# ---------------------------------------------------------------------------
# Signals are hard stops.


def start_and_wait_for_configure(tmp_path: Path, home: Path) -> tuple[subprocess.Popen, Path]:
    bin_dir = install(tmp_path, CLAUDE_SLEEP)
    env = dict(os.environ, PATH=safe_path(bin_dir))
    proc = subprocess.Popen([sys.executable, str(SCRIPT), "apply", "--home", str(home),
                             "--operator", OPERATOR, "--approve-all"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
                            start_new_session=True)
    marker = bin_dir / "claude-pid"
    deadline = time.monotonic() + 20
    while not marker.exists():
        assert time.monotonic() < deadline, "configure never started"
        assert proc.poll() is None, proc.communicate()
        time.sleep(0.02)
    return proc, marker


def stop_fake(marker: Path) -> None:
    try:
        os.kill(int(marker.read_text()), signal.SIGKILL)
    except (ProcessLookupError, ValueError):
        pass


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM, signal.SIGHUP],
                         ids=["SIGINT", "SIGTERM", "SIGHUP"])
def test_a_signal_ends_apply_by_that_signal_with_no_traceback(tmp_path, signum):
    home = make_home(tmp_path)
    proc, marker = start_and_wait_for_configure(tmp_path, home)
    try:
        proc.send_signal(signum)
        try:
            _out, err = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            _out, err = proc.communicate()
            pytest.fail(f"setup did not end on signal {signum}")
    finally:
        stop_fake(marker)
    assert proc.returncode == -signum, (proc.returncode, err)
    assert "Traceback" not in err
    assert "KeyboardInterrupt" not in err


def test_setup_installs_no_other_signal_handling():
    source = SCRIPT.read_text(encoding="utf-8")
    assert re.findall(r"signal\.signal\(.*\)", source) == [
        "signal.signal(signal.SIGINT, signal.SIG_DFL)"]
    for name in ("sigtimedwait", "sigwaitinfo", "set_wakeup_fd", "pthread_sigmask"):
        assert name not in source


# ---------------------------------------------------------------------------
# Rollback from memory.


def test_an_exception_at_every_point_before_completion_leaves_the_home_unchanged(tmp_path):
    bin_dir = install(tmp_path)
    template = make_home(tmp_path, name="template")
    traced = apply_all(template, bin_dir)
    last = [e["n"] for e in traced.events
            if e["op"] == "fsync" and (e["path"] or "").endswith(mod.RECORD_REL)][-1]
    record_points = [e["n"] for e in traced.events if e["path"]
                     and (e["path"] or "").endswith(mod.RECORD_REL)]
    assert record_points
    for k in range(1, last + 1):
        (tmp_path / f"r{k}").mkdir()
        home = make_home(tmp_path / f"r{k}")
        before = snap(home)
        done = apply_all(home, bin_dir, hooks={"raise_at": [k]})
        line = one_refusal(done)
        assert "rollback incomplete" not in line, (k, line)
        assert unchanged(before, snap(home)), (k, line)


@pytest.mark.parametrize("kind", ["short", "zero"])
def test_a_short_write_is_finished_or_rolled_back(tmp_path, kind):
    bin_dir = install(tmp_path)
    template = make_home(tmp_path, name="template")
    traced = apply_all(template, bin_dir)
    writes = [e["n"] for e in traced.events if e["op"] == "write"
              and (e.get("path") or "").startswith(str(template))]
    assert len(writes) >= 5
    for k in writes:
        (tmp_path / f"w{k}").mkdir()
        home = make_home(tmp_path / f"w{k}")
        before = snap(home)
        done = apply_all(home, bin_dir, hooks={f"{kind}_at": k})
        if kind == "short":
            assert done.rc == 0, (k, done.err)
            assert at(home, mod.RULES_REL).read_bytes() == RULES_SOURCE.read_bytes(), k
            assert at(home, mod.OVERRIDE_REL).read_bytes() == OVERRIDE_SOURCE.read_bytes(), k
            assert mod.parse_record(record_bytes(home))[1] is not None, k
            assert at(template, mod.SETTINGS_REL).read_bytes() == \
                at(home, mod.SETTINGS_REL).read_bytes(), k
        else:
            line = one_refusal(done)
            assert "os.write wrote nothing" in line, (k, line)
            assert unchanged(before, snap(home)), k


def test_a_rollback_that_cannot_delete_a_file_refuses_rollback_incomplete(tmp_path):
    bin_dir = install(tmp_path)
    template = make_home(tmp_path, name="template")
    traced = apply_all(template, bin_dir)
    line2 = [e["n"] for e in traced.events
             if e["op"] == "write" and (e["path"] or "").endswith(mod.RECORD_REL)][-1]
    probe = make_home(tmp_path, name="probe")
    rolled = apply_all(probe, bin_dir, hooks={"raise_at": [line2]})
    rules_unlink = [e["n"] for e in rolled.events
                    if e["op"] == "unlink" and (e["path"] or "").endswith(mod.RULES_REL)][0]
    home = make_home(tmp_path)
    done = apply_all(home, bin_dir, hooks={"raise_at": [line2, rules_unlink]})
    line = one_refusal(done)
    assert line == (f"setup: rollback incomplete; kept: {at(home, mod.RULES_REL)}; "
                    f"record: {at(home, mod.RECORD_REL)}; "
                    f"settings backup: {at(home, mod.BACKUP_REL)}; "
                    f"cause: unexpected error: OSError: [Errno 5] injected; {CONFIGURE_STAYS}")
    assert at(home, mod.RULES_REL).exists()
    assert at(home, mod.RECORD_REL).exists() and at(home, mod.BACKUP_REL).exists()
    assert at(home, mod.SETTINGS_REL).read_bytes() == PRE_SETTINGS
    assert run("remove", home).err == hard_stop_line(home, "present")


def test_rollback_incomplete_keeps_the_cause_and_says_configure_stays(tmp_path):
    bin_dir = install(tmp_path)
    line2, _events = line2_point(tmp_path, bin_dir)
    probe = make_home(tmp_path, name="probe")
    rolled = apply_all(probe, bin_dir, hooks={"zero_at": line2})
    restore = [e["n"] for e in rolled.events if e["op"] == "replace" and e["n"] > line2
               and (e["path"] or "").endswith(mod.SETTINGS_REL)][0]
    home = make_home(tmp_path)
    done = apply_all(home, bin_dir, hooks={"zero_at": line2, "raise_at": [restore]})
    settings = at(home, mod.SETTINGS_REL)
    assert one_refusal(done) == (
        f"setup: rollback incomplete; kept: {settings}; record: {at(home, mod.RECORD_REL)}; "
        f"settings backup: {at(home, mod.BACKUP_REL)}; "
        "cause: unexpected error: OSError: [Errno 5] os.write wrote nothing (disk full?); "
        f"{CONFIGURE_STAYS}")
    assert at(home, mod.BACKUP_REL).read_bytes() == PRE_SETTINGS
    assert at(home, mod.RECORD_REL).exists()


def test_a_failed_record_delete_in_rollback_keeps_the_backup(tmp_path):
    bin_dir = install(tmp_path)
    line2, _events = line2_point(tmp_path, bin_dir)
    probe = make_home(tmp_path, name="probe")
    rolled = apply_all(probe, bin_dir, hooks={"raise_at": [line2]})
    record_unlink = first_point(rolled.events, "unlink", mod.RECORD_REL)
    home = make_home(tmp_path)
    record, backup = at(home, mod.RECORD_REL), at(home, mod.BACKUP_REL)
    done = apply_all(home, bin_dir, hooks={"raise_at": [line2, record_unlink]})
    assert one_refusal(done) == (
        f"setup: rollback incomplete; kept: {record}; record: {record}; "
        f"settings backup: {backup}; "
        f"cause: unexpected error: OSError: [Errno 5] injected; {CONFIGURE_STAYS}")
    assert record.exists()
    assert backup.exists(), "the backup was deleted though the record delete failed"
    assert backup.read_bytes() == PRE_SETTINGS
    assert at(home, mod.SETTINGS_REL).read_bytes() == PRE_SETTINGS
    assert not at(home, mod.RULES_REL).exists()
    assert run("remove", home).err == hard_stop_line(home, "present")


def test_rollback_keeps_and_names_a_record_it_did_not_write(tmp_path):
    bin_dir = install(tmp_path)
    line2, events = line2_point(tmp_path, bin_dir)
    rules_link = first_point(events, "link", mod.RULES_REL)
    home = make_home(tmp_path)
    record = at(home, mod.RECORD_REL)
    foreign = b'{"record": "someone else"}\n'
    action = f"Path({str(record)!r}).write_bytes({foreign!r})"
    done = apply_all(home, bin_dir, hooks={"raise_at": [line2],
                                           "act_after": {str(rules_link): action}})
    line = one_refusal(done)
    assert line.endswith(f"; kept for a manual check: {record}"), line
    assert record.read_bytes() == foreign
    assert at(home, mod.SETTINGS_REL).read_bytes() == PRE_SETTINGS
    assert not at(home, mod.RULES_REL).exists()


def test_an_unexpected_exception_is_one_line_after_rollback(tmp_path):
    bin_dir = install(tmp_path)
    template = make_home(tmp_path, name="template")
    traced = apply_all(template, bin_dir)
    replace = first_point(traced.events, "replace", mod.SETTINGS_REL)
    home = make_home(tmp_path)
    before = snap(home)
    done = apply_all(home, bin_dir, hooks={"raise_at": [replace], "raise_type": "ValueError"})
    assert one_refusal(done) == f"setup: unexpected error: ValueError: injected; {CONFIGURE_STAYS}"
    assert unchanged(before, snap(home))


# ---------------------------------------------------------------------------
# The lock.


def hold(home: Path) -> int:
    path = at(home, mod.LOCK_REL)
    path.parent.mkdir(exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd


@pytest.mark.parametrize("verb", ["apply", "remove"])
def test_a_held_lock_refuses_and_touches_nothing(tmp_path, verb):
    bin_dir = install(tmp_path)
    home = make_home(tmp_path)
    if verb == "remove":
        assert apply_all(home, bin_dir).rc == 0
    fd = hold(home)
    try:
        inode = at(home, mod.LOCK_REL).stat().st_ino
        before = snap(home)
        done = run(verb, home, *(["--approve-all"] if verb == "apply" else []), bin_dir=bin_dir)
        assert one_refusal(done) == (
            f"setup: another harness setup run holds the lock {at(home, mod.LOCK_REL)}; "
            "nothing was changed")
        assert snap(home) == before
        assert at(home, mod.LOCK_REL).stat().st_ino == inode
    finally:
        os.close(fd)


def test_the_lock_file_keeps_its_inode_on_every_exit(tmp_path):
    bin_dir = install(tmp_path)
    home = make_home(tmp_path)
    lock = at(home, mod.LOCK_REL)
    line2, _events = line2_point(tmp_path, bin_dir)
    assert apply_all(home, bin_dir).rc == 0
    inode = lock.stat().st_ino
    one_refusal(apply_all(home, bin_dir))
    assert lock.stat().st_ino == inode, "after a refusal"
    assert run("remove", home).rc == 0
    assert lock.stat().st_ino == inode, "after a completed remove"
    one_refusal(apply_all(home, bin_dir, hooks={"raise_at": [line2]}))
    assert lock.stat().st_ino == inode, "after an exception and rollback"
    killed = apply_all(home, bin_dir, hooks={"kill_after": line2})
    assert killed.rc == -signal.SIGKILL
    assert lock.stat().st_ino == inode, "after a SIGKILL"


def test_a_replaced_lock_path_refuses(tmp_path):
    bin_dir = install(tmp_path)
    home = make_home(tmp_path)
    traced = apply_all(make_home(tmp_path, name="template"), bin_dir)
    lock_create = first_point(traced.events, "create", mod.LOCK_REL)
    lock = at(home, mod.LOCK_REL)
    (bin_dir / "claude-argv").unlink()
    action = (f"os.rename({str(lock)!r}, {str(lock) + '.old'!r}); "
              f"Path({str(lock)!r}).write_bytes(b'')")
    done = apply_all(home, bin_dir, hooks={"act_after": {str(lock_create): action}})
    assert one_refusal(done) == (
        f"setup: cannot take the setup lock: {lock} was replaced while it was taken")
    assert not at(home, mod.SETUP_DIR_REL).exists()
    assert not (bin_dir / "claude-argv").exists()
    assert Path(str(lock) + ".old").exists() and lock.exists()


def test_a_symlink_at_the_lock_path_refuses_and_is_not_followed(tmp_path):
    bin_dir = install(tmp_path)
    home = make_home(tmp_path)
    outside = tmp_path / "outside-lock"
    at(home, mod.LOCK_REL).symlink_to(outside)
    done = apply_all(home, bin_dir)
    assert one_refusal(done) == f"setup: {at(home, mod.LOCK_REL)} is a symlink"
    assert not outside.exists()


def test_the_lock_file_is_made_0600(tmp_path):
    bin_dir = install(tmp_path)
    home = make_home(tmp_path)
    lock = at(home, mod.LOCK_REL)
    lock.write_bytes(b"")
    lock.chmod(0o644)
    assert apply_all(home, bin_dir).rc == 0
    assert stat.S_IMODE(lock.stat().st_mode) == 0o600


def test_configure_holds_the_lock_after_setup_is_killed(tmp_path):
    home = make_home(tmp_path)
    proc, marker = start_and_wait_for_configure(tmp_path, home)
    try:
        proc.kill()
        proc.communicate(timeout=10)
        (tmp_path / "ok").mkdir()
        ok_bin = install(tmp_path / "ok")
        locked = apply_all(home, ok_bin)
        assert one_refusal(locked).startswith("setup: another harness setup run holds the lock")
    finally:
        stop_fake(marker)
    deadline = time.monotonic() + 10
    while True:
        again = apply_all(home, ok_bin)
        if again.rc == 0 or time.monotonic() > deadline:
            break
        time.sleep(0.05)
    assert again.rc == 0, again.err


def test_no_other_child_inherits_the_lock_descriptor(tmp_path):
    home = make_home(tmp_path)
    tree = mod.Tree(home)
    try:
        fd = mod.take_lock(tree)
        probe = subprocess.run([sys.executable, "-c", f"import os; os.fstat({fd})"],
                               close_fds=False, capture_output=True, text=True, timeout=30)
        assert probe.returncode != 0
        assert "Bad file descriptor" in probe.stderr
        os.close(fd)
    finally:
        tree.close()


# ---------------------------------------------------------------------------
# Files and modes.


@pytest.mark.parametrize("umask", [0o022, 0o777], ids=["umask-022", "umask-777"])
def test_every_file_setup_creates_is_born_0600(tmp_path, umask):
    born_0600_case(tmp_path, umask)


def restore_modes(root: Path) -> None:
    """Make every folder under root 0700 and every file 0600, never through a
    symlink, so pytest can remove root whatever a run left in it. Under an
    inherited umask of 0777, a setup without its own umask call makes mode-000
    folders, and pytest's shared temp root could not remove them."""
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            path = os.path.join(dirpath, name)
            info = os.lstat(path)
            if stat.S_ISDIR(info.st_mode):
                os.chmod(path, 0o700)
            elif stat.S_ISREG(info.st_mode):
                os.chmod(path, 0o600)


def born_0600_case(folder: Path, umask: int) -> None:
    bin_dir = install(folder, "#!/bin/sh\ncat > /dev/null\n")
    home = make_home(folder, None)
    try:
        done = apply_all(home, bin_dir, hooks={"umask": umask})
        assert done.rc == 0, done.err
        creates = [e for e in done.events if e["op"] == "create" and e.get("path")]
        assert len(creates) >= 5
        assert {(e["path"], e["mode"]) for e in creates if e["mode"] != 0o600} == set()
        for rel in (mod.RULES_DIR_REL, *mod.RECORD_DIRS):
            assert stat.S_IMODE(at(home, rel).stat().st_mode) == 0o700, rel
    finally:
        restore_modes(folder)


def test_the_umask_777_case_leaves_its_folder_removable(tmp_path):
    # Only removability is checked here: born_0600_case's own failures are
    # reported by test_every_file_setup_creates_is_born_0600.
    case = tmp_path / "case"
    case.mkdir()
    try:
        born_0600_case(case, 0o777)
    except AssertionError:
        pass
    shutil.rmtree(case)
    assert not case.exists()


def test_settings_keep_their_mode_through_apply_rollback_and_remove(tmp_path):
    bin_dir = install(tmp_path)
    home = make_home(tmp_path)
    settings = at(home, mod.SETTINGS_REL)
    assert apply_all(home, bin_dir).rc == 0
    assert stat.S_IMODE(settings.stat().st_mode) == 0o644
    assert run("remove", home).rc == 0
    assert stat.S_IMODE(settings.stat().st_mode) == 0o644
    traced = apply_all(make_home(tmp_path, name="template"), bin_dir)
    line2 = [e["n"] for e in traced.events
             if e["op"] == "write" and (e["path"] or "").endswith(mod.RECORD_REL)][-1]
    one_refusal(apply_all(home, bin_dir, hooks={"raise_at": [line2]}))
    assert settings.read_bytes() == PRE_SETTINGS
    assert stat.S_IMODE(settings.stat().st_mode) == 0o644
    fresh = make_home(tmp_path, None, name="fresh")
    assert apply_all(fresh, bin_dir).rc == 0
    assert stat.S_IMODE(at(fresh, mod.SETTINGS_REL).stat().st_mode) == 0o600


@pytest.mark.parametrize("rel", ["rules", "override"])
def test_a_target_that_appears_during_apply_is_never_overwritten(tmp_path, rel):
    bin_dir = install(tmp_path)
    target_rel = mod.RULES_REL if rel == "rules" else mod.OVERRIDE_REL
    traced = apply_all(make_home(tmp_path, name="template"), bin_dir)
    temp_create = temp_points(traced.events, target_rel.rsplit("/", 1)[1])[0]
    home = make_home(tmp_path)
    target = at(home, target_rel)
    action = f"Path({str(target)!r}).write_bytes(b'person')"
    done = apply_all(home, bin_dir, hooks={"act_after": {str(temp_create): action}})
    line = one_refusal(done)
    assert line.startswith(f"setup: {target} appeared while apply was running; it was not written")
    assert target.read_bytes() == b"person"
    assert at(home, mod.SETTINGS_REL).read_bytes() == PRE_SETTINGS
    assert not at(home, mod.RECORD_REL).exists()


def test_temp_names_are_unique_so_a_persons_file_survives(tmp_path):
    bin_dir = install(tmp_path)
    home = make_home(tmp_path)
    mine = at(home, ".claude/.settings.json.harness-x.tmp")
    mine.write_bytes(b"mine")
    rules_mine = at(home, ".claude/rules/.harness.md.harness-x.tmp")
    rules_mine.parent.mkdir()
    rules_mine.write_bytes(b"mine too")
    names = {"harness.md": [".harness.md.harness-x.tmp"],
             "settings.json": [".settings.json.harness-x.tmp"]}
    done = apply_all(home, bin_dir, hooks={"temp_names": names})
    assert done.rc == 0, done.err
    assert mine.exists() and rules_mine.exists(), "a look-alike file was lost"
    assert mine.read_bytes() == b"mine" and rules_mine.read_bytes() == b"mine too"
    temps = [e["path"].rsplit("/", 1)[1] for e in done.events if e["op"] == "create"
             and ".harness-" in (e.get("path") or "") and "error" not in e]
    assert len(temps) == len(set(temps))


def fsync_problems(events: list[dict], home: Path) -> list[str]:
    inside = [e for e in events if (e.get("path") or "").startswith(str(home))
              and "error" not in e and "raised" not in e]
    problems = []
    for i, event in enumerate(inside):
        rest = inside[i + 1:]
        if event["op"] in FOLDER_OPS:
            folder = event["path"].rsplit("/", 1)[0]
            window = []
            for later in rest:
                if later["op"] in MUTATING:
                    break
                window.append(later)
            if not any(e["op"] == "fsync" and e["path"] == folder for e in window):
                problems.append(f"{event['op']} {event['path']} (n={event['n']}) "
                                "not followed by an fsync of its folder")
        if event["op"] == "write":
            window = []
            for later in rest:
                if later["op"] in MUTATING and not (
                        later["op"] == "write" and later["path"] == event["path"]):
                    break
                window.append(later)
            if not any(e["op"] == "fsync" and e["path"] == event["path"] for e in window):
                problems.append(f"write {event['path']} (n={event['n']}) not fsynced "
                                "before the next step")
    return problems


def test_every_file_and_folder_change_is_fsynced_before_the_next_step(tmp_path):
    bin_dir = install(tmp_path)
    home = make_home(tmp_path, None)
    applied = apply_all(home, bin_dir)
    assert applied.rc == 0
    assert fsync_problems(applied.events, home) == []
    ops = {e["op"] for e in applied.events}
    assert {"mkdir", "link", "replace", "unlink", "write"} <= ops
    removed = run("remove", home)
    assert removed.rc == 0
    assert fsync_problems(removed.events, home) == []
    assert "rmdir" in {e["op"] for e in removed.events}
    template = make_home(tmp_path, name="template")
    traced = apply_all(template, bin_dir)
    line2 = [e["n"] for e in traced.events
             if e["op"] == "write" and (e["path"] or "").endswith(mod.RECORD_REL)][-1]
    other = make_home(tmp_path, name="other")
    rolled = apply_all(other, bin_dir, hooks={"raise_at": [line2]})
    one_refusal(rolled)
    assert fsync_problems(rolled.events, other) == []


def test_full_fsync_is_used_where_defined_and_skipped_where_not(tmp_path, monkeypatch):
    calls = []
    path = tmp_path / "f"
    path.write_bytes(b"x")
    fd = os.open(path, os.O_RDONLY)
    try:
        with_full = SimpleNamespace(F_FULLFSYNC=12345,
                                    fcntl=lambda f, op: calls.append((f, op)))
        monkeypatch.setattr(mod, "fcntl", with_full)
        mod.sync(fd)
        assert calls == [(fd, 12345)]
        without = SimpleNamespace(fcntl=lambda f, op: calls.append((f, op)))
        monkeypatch.setattr(mod, "fcntl", without)
        mod.sync(fd)
        assert calls == [(fd, 12345)]
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------
# I2: never knowingly overwrite or delete bytes setup did not write.


def line2_point(tmp_path: Path, bin_dir: Path, pre: bytes | None = PRE_SETTINGS) -> tuple[int, list]:
    traced = apply_all(make_home(tmp_path, pre, name="template"), bin_dir)
    assert traced.rc == 0, traced.err
    return [e["n"] for e in traced.events
            if e["op"] == "write" and (e["path"] or "").endswith(mod.RECORD_REL)][-1], traced.events


def test_an_edit_to_a_setup_file_survives_rollback_and_is_named(tmp_path):
    bin_dir = install(tmp_path)
    line2, events = line2_point(tmp_path, bin_dir)
    override_link = first_point(events, "link", mod.OVERRIDE_REL)
    home = make_home(tmp_path)
    override = at(home, mod.OVERRIDE_REL)
    action = f"Path({str(override)!r}).write_bytes(b'# edited by the person\\n')"
    done = apply_all(home, bin_dir, hooks={"raise_at": [line2],
                                           "act_after": {str(override_link + 1): action}})
    line = one_refusal(done)
    assert line.endswith(f"; kept for a manual check: {override}")
    assert override.read_bytes() == b"# edited by the person\n"
    assert not at(home, mod.RULES_REL).exists()


def test_an_edit_to_a_setup_file_survives_remove_and_is_named(tmp_path):
    bin_dir = install(tmp_path)
    home = make_home(tmp_path)
    assert apply_all(home, bin_dir).rc == 0
    override = at(home, mod.OVERRIDE_REL)
    override.write_bytes(b"# edited\n")
    done = run("remove", home)
    assert done.rc == 0, done.err
    assert f"kept for a manual check: {override}\n" in done.out
    assert override.read_bytes() == b"# edited\n"
    assert not at(home, mod.RULES_REL).exists()
    assert not at(home, mod.RECORD_REL).exists()


def test_settings_changed_before_the_replace_refuse_unwritten_on_apply(tmp_path):
    bin_dir = install(tmp_path)
    _line2, events = line2_point(tmp_path, bin_dir)
    temp = temp_points(events, "settings.json")[0]
    home = make_home(tmp_path)
    settings = at(home, mod.SETTINGS_REL)
    action = f"Path({str(settings)!r}).write_bytes(b'{{\"model\": \"person\"}}\\n')"
    done = apply_all(home, bin_dir, hooks={"act_after": {str(temp): action}})
    line = one_refusal(done)
    assert line.startswith(
        "setup: .claude/settings.json changed while apply was running; it was not written")
    assert settings.read_bytes() == b'{"model": "person"}\n'
    assert not at(home, mod.RULES_REL).exists()
    assert not at(home, mod.RECORD_REL).exists()
    assert no_temps(home) == []


def test_settings_changed_before_the_replace_refuse_unwritten_on_remove(tmp_path):
    bin_dir = install(tmp_path)
    home = make_home(tmp_path)
    assert apply_all(home, bin_dir).rc == 0
    traced_home = make_home(tmp_path, name="template")
    assert apply_all(traced_home, bin_dir).rc == 0
    traced = run("remove", traced_home)
    temp = temp_points(traced.events, "settings.json")[0]
    settings = at(home, mod.SETTINGS_REL)
    record = record_bytes(home)
    action = f"Path({str(settings)!r}).write_bytes(b'{{\"model\": \"person\"}}\\n')"
    done = run("remove", home, hooks={"act_after": {str(temp): action}})
    assert one_refusal(done) == (
        "setup: .claude/settings.json changed while remove was running; it was not written")
    assert settings.read_bytes() == b'{"model": "person"}\n'
    assert record_bytes(home) == record
    assert no_temps(home) == []


def test_remove_refuses_unwritten_when_a_settings_file_apply_created_changed(tmp_path,
                                                                            monkeypatch):
    # No setup write falls between remove's read of a settings.json apply
    # created and its compare before the unlink, so the outside edit runs
    # in-process, right after the items are taken out in memory.
    bin_dir = install(tmp_path)
    home = make_home(tmp_path, None)
    assert apply_all(home, bin_dir).rc == 0
    settings = at(home, mod.SETTINGS_REL)
    record = record_bytes(home)
    real = mod.take_out

    def take_out_then_edit(*args, **kwargs):
        real(*args, **kwargs)
        settings.write_bytes(b'{"person": true}\n')

    monkeypatch.setattr(mod, "take_out", take_out_then_edit)
    with pytest.raises(mod.SetupError, match=r"^\.claude/settings\.json changed while remove "
                                             r"was running; it was not written$"):
        mod.remove_verb(home)
    assert settings.read_bytes() == b'{"person": true}\n'
    assert record_bytes(home) == record


def test_apply_keeps_and_names_a_backup_changed_after_line_2(tmp_path):
    bin_dir = install(tmp_path)
    _line2, events = line2_point(tmp_path, bin_dir)
    line2_sync = [e["n"] for e in events
                  if e["op"] == "fsync" and (e["path"] or "").endswith(mod.RECORD_REL)][-1]
    home = make_home(tmp_path)
    backup = at(home, mod.BACKUP_REL)
    action = f"Path({str(backup)!r}).write_bytes(b'changed')"
    done = apply_all(home, bin_dir, hooks={"act_after": {str(line2_sync): action}})
    assert done.rc == 0, done.err
    assert done.err == f"setup: warning: kept for a manual check: {backup}\n"
    assert backup.read_bytes() == b"changed"


def test_remove_keeps_and_names_a_backup_whose_bytes_changed(tmp_path):
    bin_dir = install(tmp_path)
    _line2, events = line2_point(tmp_path, bin_dir)
    line2_sync = [e["n"] for e in events
                  if e["op"] == "fsync" and (e["path"] or "").endswith(mod.RECORD_REL)][-1]
    home = make_home(tmp_path)
    assert apply_all(home, bin_dir, hooks={"kill_after": line2_sync}).rc == -signal.SIGKILL
    backup = at(home, mod.BACKUP_REL)
    backup.write_bytes(b"changed")
    done = run("remove", home)
    assert done.rc == 0, done.err
    assert f"kept for a manual check: {backup}\n" in done.out
    assert backup.read_bytes() == b"changed"
    assert not at(home, mod.RECORD_REL).exists()


def test_settings_rollback_with_changed_bytes_takes_out_only_apply_items(tmp_path):
    bin_dir = install(tmp_path)
    line2, events = line2_point(tmp_path, bin_dir)
    replace = first_point(events, "replace", mod.SETTINGS_REL)
    home = make_home(tmp_path)
    settings = at(home, mod.SETTINGS_REL)
    action = ("import json\n"
              f"p = Path({str(settings)!r})\n"
              "d = json.loads(p.read_bytes())\n"
              "d['model'] = 'changed during apply'\n"
              "p.write_text(json.dumps(d, indent=2) + '\\n')\n")
    done = apply_all(home, bin_dir, hooks={"raise_at": [line2],
                                           "act_after": {str(replace + 1): action}})
    line = one_refusal(done)
    assert line.endswith(f"; kept for a manual check: {settings}")
    data = json.loads(settings.read_bytes())
    # A new key after the sandbox key would change its recorded neighbour and
    # keep it, so the person's edit here changes a value instead.
    assert data == {"model": "changed during apply",
                    "permissions": {"deny": ["Read(seeded)"]}}


# ---------------------------------------------------------------------------
# I3: exact, re-runnable remove.


def test_remove_leaves_other_settings_content_when_settings_changed_elsewhere(tmp_path):
    bin_dir = install(tmp_path)
    home = make_home(tmp_path)
    assert apply_all(home, bin_dir).rc == 0
    settings = at(home, mod.SETTINGS_REL)
    data = json.loads(settings.read_bytes())
    data["model"] = "changed by the person"
    settings.write_text(json.dumps(data, indent=2) + "\n")
    done = run("remove", home)
    assert done.rc == 0, done.err
    assert json.loads(settings.read_bytes()) == {
        "model": "changed by the person", "permissions": {"deny": ["Read(seeded)"]}}


@pytest.mark.parametrize("how", ["moved", "duplicated"])
def test_remove_keeps_and_names_moved_and_duplicated_rules(tmp_path, how):
    bin_dir = install(tmp_path)
    home = make_home(tmp_path)
    assert apply_all(home, bin_dir).rc == 0
    settings = at(home, mod.SETTINGS_REL)
    data = json.loads(settings.read_bytes())
    deny = data["permissions"]["deny"]
    # A duplicate of a rule other than the last keeps that rule at its
    # recorded place with its recorded neighbours.
    rule = mod.DENY_RULES[-1] if how == "moved" else mod.DENY_RULES[2]
    if how == "moved":
        deny.remove(rule)
        deny.insert(0, rule)
    else:
        deny.append(rule)
    settings.write_text(json.dumps(data, indent=2) + "\n")
    done = run("remove", home)
    assert done.rc == 0, done.err
    assert (f"kept for a manual check: {settings}: permissions.deny rule {rule!r} "
            "(moved or duplicated)") in done.out
    left = json.loads(settings.read_bytes())["permissions"]["deny"]
    assert rule in left and "Read(seeded)" in left
    assert "sandbox" not in json.loads(settings.read_bytes())


@pytest.mark.parametrize("side", ["before", "after"])
def test_remove_keeps_and_names_a_rule_whose_recorded_neighbour_changed(tmp_path, side):
    bin_dir = install(tmp_path)
    home = make_home(tmp_path)
    assert apply_all(home, bin_dir).rc == 0
    settings = at(home, mod.SETTINGS_REL)
    data = json.loads(settings.read_bytes())
    deny = data["permissions"]["deny"]
    if side == "before":
        # The seeded rule just before setup's first rule is replaced.
        deny[0] = "Read(replaced)"
        rule, left = mod.DENY_RULES[0], ["Read(replaced)", mod.DENY_RULES[0]]
    else:
        # A person's rule now follows setup's last rule.
        deny.append("Read(person)")
        rule, left = mod.DENY_RULES[-1], ["Read(seeded)", mod.DENY_RULES[-1], "Read(person)"]
    settings.write_text(json.dumps(data, indent=2) + "\n")
    done = run("remove", home)
    assert done.rc == 0, done.err
    assert (f"kept for a manual check: {settings}: permissions.deny rule {rule!r} "
            "(moved or duplicated)") in done.out
    assert json.loads(settings.read_bytes())["permissions"]["deny"] == left


@pytest.mark.parametrize("now", [1, 1.0], ids=["one", "one-point-zero"])
def test_remove_keeps_a_rule_whose_recorded_neighbour_changed_json_type(tmp_path, now):
    # A neighbour recorded as `true` that is now `1` or `1.0` has changed:
    # the compare is on canonical JSON text, never Python's `==`.
    bin_dir = install(tmp_path)
    home = make_home(tmp_path, b'{\n  "permissions": {\n    "deny": [true]\n  }\n}\n')
    rule = mod.DENY_RULES[0]
    assert run("apply", home, "--approve", f"deny:{rule}", bin_dir=bin_dir).rc == 0
    settings = at(home, mod.SETTINGS_REL)
    data = json.loads(settings.read_bytes())
    assert mod.canonical(data["permissions"]["deny"]) == mod.canonical([True, rule])
    data["permissions"]["deny"][0] = now
    settings.write_text(json.dumps(data, indent=2) + "\n")
    done = run("remove", home)
    assert done.rc == 0, done.err
    assert (f"kept for a manual check: {settings}: permissions.deny rule {rule!r} "
            "(moved or duplicated)") in done.out
    assert mod.canonical(json.loads(settings.read_bytes())) == mod.canonical(data)


def test_the_sandbox_key_is_recorded_at_its_place_in_the_final_settings(tmp_path):
    bin_dir = install(tmp_path)
    home = make_home(tmp_path, b'{\n  "model": "kept"\n}\n')
    assert apply_all(home, bin_dir).rc == 0
    _head, body = mod.parse_record(record_bytes(home))
    key = [item for item in body["settings"]["items"] if item["kind"] == "key"]
    assert key == [{"kind": "key", "name": "sandbox", "value": {"enabled": True}, "index": 1,
                    "before": "model", "after": "permissions"}]
    assert list(json.loads(at(home, mod.SETTINGS_REL).read_bytes())) == [
        "model", "sandbox", "permissions"]


@pytest.mark.parametrize("how", ["moved", "shifted", "neighbour-before", "neighbour-after",
                                 "changed", "one", "one-point-zero"])
def test_remove_keeps_and_names_a_moved_or_changed_sandbox_key(tmp_path, how):
    bin_dir = install(tmp_path)
    home = make_home(tmp_path, b'{\n  "model": "kept"\n}\n')
    assert run("apply", home, "--approve", "sandbox", bin_dir=bin_dir).rc == 0
    settings = at(home, mod.SETTINGS_REL)
    data = json.loads(settings.read_bytes())
    assert list(data) == ["model", "sandbox"]
    sandbox = data["sandbox"]
    data = {
        # Another index and other neighbours.
        "moved": {"sandbox": sandbox, "model": "kept"},
        # Another index, the same neighbours.
        "shifted": {"first": 1, "model": "kept", "sandbox": sandbox},
        # The same index, another neighbour before or after.
        "neighbour-before": {"other": "kept", "sandbox": sandbox},
        "neighbour-after": {"model": "kept", "sandbox": sandbox, "later": 1},
        # The same place, another value.
        "changed": {"model": "kept", "sandbox": {"enabled": True, "person": 1}},
        # Another JSON type: `1` and `1.0` are not the `true` setup wrote.
        "one": {"model": "kept", "sandbox": {"enabled": 1}},
        "one-point-zero": {"model": "kept", "sandbox": {"enabled": 1.0}},
    }[how]
    settings.write_text(json.dumps(data, indent=2) + "\n")
    done = run("remove", home)
    assert done.rc == 0, done.err
    assert f"kept for a manual check: {settings}: sandbox (moved or changed)\n" in done.out
    assert json.loads(settings.read_bytes()) == data
    assert mod.canonical(json.loads(settings.read_bytes())) == mod.canonical(data)
    assert list(json.loads(settings.read_bytes())) == list(data)


def applied_template(tmp_path: Path, bin_dir: Path) -> tuple[Path, dict]:
    template = make_home(tmp_path, name="applied")
    before = snap(template)
    assert apply_all(template, bin_dir).rc == 0
    return template, before


def copy_home(template: Path, dest: Path) -> Path:
    shutil.copytree(template, dest, symlinks=True)
    return dest.resolve()


def test_a_sigkill_at_every_remove_point_then_a_rerun_finishes(tmp_path):
    bin_dir = install(tmp_path)
    template, before = applied_template(tmp_path, bin_dir)
    record = record_bytes(template)
    probe = copy_home(template, tmp_path / "probe" / "home")
    traced = run("remove", probe)
    assert traced.rc == 0
    record_unlink = first_point(traced.events, "unlink", mod.RECORD_REL)
    saw_gone = False
    for m in range(1, record_unlink + 1):
        home = copy_home(template, tmp_path / f"m{m}" / "home")
        killed = run("remove", home, hooks={"kill_after": m})
        assert killed.rc == -signal.SIGKILL, m
        now = record_bytes(home)
        assert now in (record, None), m
        again = run("remove", home)
        after = snap(home)
        # A hard stop may leave one temp file beside a target; setup cannot
        # tell it from a person's file of a like name, so it stays. It is
        # 0600, or for settings.json the mode settings.json already has.
        temps = [rel for rel in after if ".harness-" in rel]
        assert len(temps) <= 1, (m, temps)
        for rel in temps:
            allowed = {0o600, 0o644} if "/.settings.json." in "/" + rel else {0o600}
            assert after.pop(rel)[1] in allowed, (m, rel)
        if now is None:
            # Remove completed when the record went: only empty folders remain.
            assert again.err == f"setup: no record at {at(home, mod.RECORD_REL)}\n", m
            leftover = {rel: v for rel, v in after.items() if rel in mod.RECORD_DIRS}
            assert all(v[0] == "dir" for v in leftover.values()), m
            after = {rel: v for rel, v in after.items() if rel not in leftover}
        else:
            assert again.rc == 0, (m, again.err)
            saw_gone = saw_gone or "already gone: " in again.out
        assert unchanged(before, after), m
    assert saw_gone


def test_remove_writes_nothing_to_the_record(tmp_path):
    bin_dir = install(tmp_path)
    template, _before = applied_template(tmp_path, bin_dir)
    done = run("remove", template)
    assert done.rc == 0
    touching = [e for e in done.events if (e.get("path") or "").endswith(mod.RECORD_REL)]
    assert [e["op"] for e in touching] == ["unlink"]


def test_an_exception_at_every_remove_point_keeps_the_record_and_a_rerun_finishes(tmp_path):
    bin_dir = install(tmp_path)
    template, before = applied_template(tmp_path, bin_dir)
    record = record_bytes(template)
    probe = copy_home(template, tmp_path / "probe" / "home")
    record_unlink = first_point(run("remove", probe).events, "unlink", mod.RECORD_REL)
    for k in range(1, record_unlink + 1):
        home = copy_home(template, tmp_path / f"x{k}" / "home")
        done = run("remove", home, hooks={"raise_at": [k]})
        one_refusal(done)
        assert record_bytes(home) == record, k
        assert no_temps(home) == [], k
        again = run("remove", home)
        assert again.rc == 0, (k, again.err)
        assert unchanged(before, snap(home)), k


def test_a_pruning_failure_after_the_record_is_deleted_is_a_warning(tmp_path):
    bin_dir = install(tmp_path)
    template, _before = applied_template(tmp_path, bin_dir)
    probe = copy_home(template, tmp_path / "probe" / "home")
    events = run("remove", probe).events
    record_unlink = first_point(events, "unlink", mod.RECORD_REL)
    rmdir = [e["n"] for e in events if e["op"] == "rmdir" and e["n"] > record_unlink][0]
    home = copy_home(template, tmp_path / "x" / "home")
    done = run("remove", home, hooks={"raise_at": [rmdir]})
    assert done.rc == 0, done.err
    assert re.fullmatch(r"setup: warning: could not remove the empty folder \S+ "
                        r"\(OSError: \[Errno 5\] injected\)\n", done.err), done.err
    assert not at(home, mod.RECORD_REL).exists()


def test_a_sync_failure_after_the_record_is_deleted_is_a_warning(tmp_path):
    bin_dir = install(tmp_path)
    template, before = applied_template(tmp_path, bin_dir)
    probe = copy_home(template, tmp_path / "probe" / "home")
    events = run("remove", probe).events
    record_unlink = first_point(events, "unlink", mod.RECORD_REL)
    folder_sync = [e for e in events if e["n"] == record_unlink + 1][0]
    assert folder_sync["op"] == "fsync"
    assert folder_sync["path"] == str(at(probe, mod.SETUP_DIR_REL))
    home = copy_home(template, tmp_path / "x" / "home")
    done = run("remove", home, hooks={"raise_at": [record_unlink + 1]})
    assert done.rc == 0, done.err
    assert done.err == (
        f"setup: warning: could not sync the folder {at(home, mod.SETUP_DIR_REL)} after "
        "deleting the record (OSError: [Errno 5] injected)\n")
    assert f"removed: {at(home, mod.RECORD_REL)}\n" in done.out
    assert not at(home, mod.RECORD_REL).exists()
    assert unchanged(before, snap(home))
    again = run("remove", home)
    assert again.err == f"setup: no record at {at(home, mod.RECORD_REL)}\n"


def test_the_record_is_the_last_file_deleted_and_its_folders_are_pruned_after_it(tmp_path):
    bin_dir = install(tmp_path)
    template, _before = applied_template(tmp_path, bin_dir)
    events = run("remove", template).events
    unlinks = [e for e in events if e["op"] == "unlink" and ".harness-" not in e["path"]]
    assert unlinks[-1]["path"].endswith(mod.RECORD_REL)
    record_n = unlinks[-1]["n"]
    # Every rmdir attempt, failed ones included.
    rmdirs = [(e["path"][len(str(template)) + 1:], e["n"]) for e in events if e["op"] == "rmdir"]
    assert {rel for rel, _n in rmdirs} == {mod.RULES_DIR_REL, *mod.RECORD_DIRS}
    for rel, n in rmdirs:
        if rel in mod.RECORD_DIRS:
            assert n > record_n, (rel, n, record_n)
        else:
            assert n < record_n, (rel, n, record_n)


def test_a_created_folder_with_a_persons_file_stays_and_claude_stays(tmp_path):
    bin_dir = install(tmp_path)
    home = make_home(tmp_path, None)
    assert apply_all(home, bin_dir).rc == 0
    mine = at(home, ".claude/rules/mine.md")
    mine.write_bytes(b"mine")
    done = run("remove", home)
    assert done.rc == 0, done.err
    assert mine.exists(), "the person's file in a created folder was lost"
    assert mine.read_bytes() == b"mine"
    assert not at(home, mod.RULES_REL).exists()
    assert at(home, ".claude").is_dir()
    assert not at(home, ".claude/plugins").exists()


def test_remove_refuses_non_utf8_settings_by_name(tmp_path):
    bin_dir = install(tmp_path)
    home = make_home(tmp_path)
    assert apply_all(home, bin_dir).rc == 0
    at(home, mod.SETTINGS_REL).write_bytes(b"\xff\xfe{}")
    before = snap(home)
    done = run("remove", home)
    assert one_refusal(done).startswith(
        f"setup: {at(home, mod.SETTINGS_REL)} is not valid UTF-8")
    assert snap(home) == before


def test_remove_refuses_a_record_changed_while_it_ran(tmp_path):
    bin_dir = install(tmp_path)
    template, _before = applied_template(tmp_path, bin_dir)
    probe = copy_home(template, tmp_path / "probe" / "home")
    events = run("remove", probe).events
    backup_or_rules = [e["n"] for e in events if e["op"] == "unlink"
                       and (e["path"] or "").endswith(mod.RULES_REL)][0]
    home = copy_home(template, tmp_path / "x" / "home")
    record = at(home, mod.RECORD_REL)
    action = f"p = Path({str(record)!r}); p.write_bytes(p.read_bytes() + b'x')"
    done = run("remove", home, hooks={"act_after": {str(backup_or_rules): action}})
    assert one_refusal(done) == f"setup: {record} changed while remove was running"
    assert record.exists()


# ---------------------------------------------------------------------------
# Symlinks.


@pytest.mark.parametrize("rel", [
    ".claude", ".claude/rules", ".claude/plugins", ".claude/plugins/harness-core",
    ".claude/plugins/harness-core/harness", mod.RULES_REL, mod.OVERRIDE_REL,
    mod.SETTINGS_REL, mod.RECORD_REL, mod.BACKUP_REL])
@pytest.mark.parametrize("verb", ["apply", "remove"])
def test_a_symlink_at_any_component_refuses_and_nothing_outside_is_written(tmp_path, rel, verb):
    bin_dir = install(tmp_path)
    home = make_home(tmp_path, None)
    if verb == "remove" and rel != ".claude":
        assert apply_all(home, bin_dir).rc == 0
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "settings.json").write_bytes(b"{}\n")
    target = at(home, rel)
    if target.is_dir():
        shutil.rmtree(target)
    elif target.exists():
        target.unlink()
    target.parent.mkdir(parents=True, exist_ok=True)
    is_folder = rel in (".claude", ".claude/rules") or rel in mod.RECORD_DIRS
    target.symlink_to(outside if is_folder else outside / "settings.json")
    before = snap(outside)
    done = run(verb, home, *(["--approve-all"] if verb == "apply" else []), bin_dir=bin_dir)
    assert one_refusal(done) == f"setup: {target} is a symlink"
    assert snap(outside) == before
    assert target.is_symlink()


def test_a_symlink_at_a_temp_path_refuses(tmp_path):
    bin_dir = install(tmp_path)
    home = make_home(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    planted = at(home, ".claude/.settings.json.harness-planted.tmp")
    planted.symlink_to(outside / "written")
    done = apply_all(home, bin_dir, hooks={"temp_names": {
        "settings.json": [".settings.json.harness-planted.tmp"]}})
    assert one_refusal(done).startswith(f"setup: {planted} is a symlink")
    assert list(outside.iterdir()) == []
    assert at(home, mod.SETTINGS_REL).read_bytes() == PRE_SETTINGS
    assert not at(home, mod.RULES_REL).exists()


def test_a_folder_swapped_for_a_symlink_after_it_was_opened_is_not_followed(tmp_path):
    bin_dir = install(tmp_path)
    traced = apply_all(make_home(tmp_path, name="template"), bin_dir)
    temp_create = temp_points(traced.events, "harness.md")[0]
    home = make_home(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    rules = at(home, mod.RULES_DIR_REL)
    action = (f"os.rename({str(rules)!r}, {str(rules) + '-moved'!r}); "
              f"os.symlink({str(outside)!r}, {str(rules)!r})")
    done = apply_all(home, bin_dir, hooks={"act_after": {str(temp_create): action}})
    assert list(outside.iterdir()) == []
    assert (Path(str(rules) + "-moved") / "harness.md").read_bytes() == RULES_SOURCE.read_bytes()
    assert done.rc == 0, done.err


# ---------------------------------------------------------------------------
# The remaining named refusals.


def test_a_missing_plugin_file_refuses_by_name(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "RULES_SOURCE", tmp_path / "harness.md")
    with pytest.raises(mod.SetupError,
                       match="^harness.md is missing from the plugin files folder$"):
        mod.check_proposals()


def test_a_temp_name_that_keeps_colliding_refuses_by_name(tmp_path):
    bin_dir = install(tmp_path)
    home = make_home(tmp_path)
    rules_dir = at(home, mod.RULES_DIR_REL)
    rules_dir.mkdir()
    (rules_dir / ".harness.md.harness-x.tmp").write_bytes(b"mine")
    before = snap(home)
    done = apply_all(home, bin_dir, hooks={"temp_names": {
        "harness.md": [".harness.md.harness-x.tmp"] * 100}})
    assert one_refusal(done).startswith(f"setup: cannot make a temp file in {rules_dir}")
    assert unchanged(before, snap(home))


def test_an_error_taking_the_lock_refuses_by_name(tmp_path):
    bin_dir = install(tmp_path)
    home = make_home(tmp_path)
    done = apply_all(home, bin_dir, hooks={"raise_at": [1]})
    assert done.events[0]["op"] == "create" and done.events[0]["path"].endswith(mod.LOCK_REL)
    assert one_refusal(done) == "setup: cannot take the setup lock: [Errno 5] injected"
