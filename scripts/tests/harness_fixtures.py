"""Shared fixtures for the harness install tests and the doctor tests.

A fixture home is a pytest tmp_path folder. A run of plugins/harness/setup.py
is a child process whose PATH holds only a stub folder, so no real `claude`,
`ledger` or system Python is ever found. The stub `claude` records what
`claude plugin configure --values-stdin` is given and answers `--json` from a
file the test writes; the stub `ledger` runs the ledger vendored in this
repository (the real command, as a plugin would put on PATH). Nothing in
setup.py knows about any of it: the stubs are the real injection boundary, a
command on PATH.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "plugins" / "harness" / "setup.py"
SKILL = ROOT / "plugins" / "harness" / "skills" / "setup" / "SKILL.md"
README = ROOT / "README.md"
VENDORED_LIBS = ROOT / "skills" / "_lib"

OPERATOR = "operator"
HARNESS = "claude-code"
LEDGER_PLUGIN = "ledger-hooks@harness-core"
OPTION_KEYS = ("ledger_db", "ledger_operator", "ledger_harness")

# A settings file the person already had: it holds a deny list and grants of
# its own, which setup must leave exactly as they are.
SEEDED_SETTINGS = {
    "model": "kept",
    "permissions": {
        "allow": ["Bash(ls:*)"],
        "deny": ["Read(seeded-one)", "Read(seeded-two)"],
    },
    "additionalDirectories": ["/seeded/extra"],
}


def load_setup():
    spec = importlib.util.spec_from_file_location("harness_setup_for_install_tests", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def settings_bytes(data: dict) -> bytes:
    return (json.dumps(data, indent=2) + "\n").encode("utf-8")


CLAUDE_STUB = """#!/bin/sh
here=${0%/*}
case "$*" in
  *--values-stdin*)
    /bin/cat > "$here/claude-stdin"
    printf '%s\\n' "$@" > "$here/claude-argv"
    ;;
  *--json*)
    [ -f "$here/claude-show.json" ] || { echo "no stored options" >&2; exit 1; }
    /bin/cat "$here/claude-show.json"
    ;;
  *)
    echo "unexpected: $*" >&2
    exit 2
    ;;
esac
"""

LEDGER_STUB = """#!/bin/sh
here=${0%/*}
printf '%s\\n' "$@" > "$here/ledger-argv"
printf '%s\\n%s\\n%s\\n' "$LEDGER_DB" "$LEDGER_OPERATOR" "$LEDGER_HARNESS" > "$here/ledger-env"
exec PYTHON -I -c 'import runpy, sys; sys.path.insert(0, sys.argv.pop(1)); runpy.run_module("ledger", run_name="__main__", alter_sys=True)' LIBS "$@"
"""

PYTHON_STUB = """#!/bin/sh
exec PYTHON "$@"
"""


def put_stub(bin_dir: Path, name: str, body: str) -> Path:
    path = bin_dir / name
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def make_bin(folder: Path, *, claude: bool = True, ledger: bool = True,
             python: bool = True) -> Path:
    """The stub folder, the whole PATH of a run."""
    bin_dir = folder / "bin"
    bin_dir.mkdir(exist_ok=True)
    if claude:
        put_stub(bin_dir, "claude", CLAUDE_STUB)
    if ledger:
        put_stub(bin_dir, "ledger",
                 LEDGER_STUB.replace("PYTHON", sys.executable).replace("LIBS", str(VENDORED_LIBS)))
    if python:
        put_stub(bin_dir, "python3", PYTHON_STUB.replace("PYTHON", sys.executable))
    return bin_dir


def show_json(stored: dict | None, declared: tuple = OPTION_KEYS) -> str:
    """What `claude plugin configure <id> --json` prints, with `stored` set."""
    stored = stored or {}
    return json.dumps({
        "pluginId": LEDGER_PLUGIN,
        "schema": {key: {"type": "string"} for key in declared},
        "inputs": {key: stored.get(key, "") for key in declared},
        "configured": [key for key in declared if stored.get(key)],
        "unconfigured": [key for key in declared if not stored.get(key)],
    })


def show_stored(bin_dir: Path) -> dict:
    """Make the stub answer --json with the values the last configure was given."""
    stored = json.loads((bin_dir / "claude-stdin").read_text(encoding="utf-8"))
    (bin_dir / "claude-show.json").write_text(show_json(stored), encoding="utf-8")
    return stored


def make_home(tmp_path: Path, *, settings: dict | None = None, name: str = "home") -> Path:
    home = tmp_path / name
    home.mkdir()
    home = home.resolve()
    if settings is not None:
        (home / ".claude").mkdir()
        (home / ".claude" / "settings.json").write_bytes(settings_bytes(settings))
    return home


def at(home: Path, rel: str) -> Path:
    return home.joinpath(*rel.split("/"))


def snap(home: Path) -> dict[str, tuple]:
    """Every entry under the home: path -> type, mode and content hash."""
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


def changed_files(before: dict, after: dict) -> set[str]:
    """Files that were added, removed or whose bytes or mode differ."""
    names = set(before) | set(after)
    return {name for name in names
            if (before.get(name) or after.get(name))[0] == "file"
            and before.get(name) != after.get(name)}


def added_dirs(before: dict, after: dict) -> set[str]:
    return {name for name, value in after.items() if value[0] == "dir" and name not in before}


def ancestors(rel: str) -> set[str]:
    parts = rel.split("/")
    return {"/".join(parts[:index]) for index in range(1, len(parts))}


def child_env(home: Path, bin_dir: Path | None) -> dict[str, str]:
    return {"PATH": str(bin_dir) if bin_dir is not None else "",
            "HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1"}


def setup_run(verb: str, home: Path, *extra: str, bin_dir: Path | None,
              operator: str | None = OPERATOR, harness: str | None = HARNESS,
              timeout: float = 120) -> SimpleNamespace:
    argv = [sys.executable, str(SCRIPT), verb, "--home", str(home)]
    if operator is not None and verb in ("plan", "apply", "ledger-init"):
        argv += ["--operator", operator]
        if harness is not None:
            argv += ["--harness", harness]
    argv += list(extra)
    done = subprocess.run(argv, capture_output=True, text=True,
                          env=child_env(home, bin_dir), timeout=timeout)
    return SimpleNamespace(rc=done.returncode, out=done.stdout, err=done.stderr)


# A child that loads setup.py and logs every call that could change an
# existing entry: an open that can write, link, replace, rename, unlink,
# mkdir, rmdir and truncate, each with the full path it names.
TRACE_DRIVER = r'''
import importlib.util, json, os, sys
spec = json.loads(sys.argv[1])
R = {name: getattr(os, name) for name in (
    "open", "link", "replace", "rename", "unlink", "mkdir", "rmdir", "truncate", "write")}
log = R["open"](spec["trace"], os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
paths = {}
WRITING = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_TRUNC | os.O_CREAT


def full(dir_fd, name):
    if not isinstance(name, str):
        return None
    if os.path.isabs(name):
        return name
    base = paths.get(dir_fd) if dir_fd is not None else os.getcwd()
    return f"{base}/{name}" if base else None


def note(op, path):
    R["write"](log, (json.dumps({"op": op, "path": path}) + "\n").encode())


def w_open(path, flags, mode=0o777, *, dir_fd=None):
    fd = R["open"](path, flags, mode, dir_fd=dir_fd)
    paths[fd] = full(dir_fd, path)
    if flags & WRITING:
        note("open-write", paths[fd])
    return fd


def target(op, name_index, dir_key):
    def wrapper(*args, **kwargs):
        note(op, full(kwargs.get(dir_key), args[name_index]))
        return R[op](*args, **kwargs)
    return wrapper


loaded = importlib.util.spec_from_file_location("setup_under_trace", spec["script"])
mod = importlib.util.module_from_spec(loaded)
loaded.loader.exec_module(mod)
os.open = w_open
os.link = target("link", 1, "dst_dir_fd")
os.replace = target("replace", 1, "dst_dir_fd")
os.rename = target("rename", 1, "dst_dir_fd")
os.unlink = target("unlink", 0, "dir_fd")
os.mkdir = target("mkdir", 0, "dir_fd")
os.rmdir = target("rmdir", 0, "dir_fd")
os.truncate = target("truncate", 0, "dir_fd")
sys.exit(mod.main(spec["argv"]))
'''


def setup_run_traced(verb: str, home: Path, *extra: str, bin_dir: Path | None,
                     operator: str = OPERATOR, harness: str = HARNESS) -> SimpleNamespace:
    folder = home.parent
    driver = folder / "trace_driver.py"
    if not driver.exists():
        driver.write_text(TRACE_DRIVER, encoding="utf-8")
    trace = folder / f"trace-{len(list(folder.glob('trace-*.jsonl')))}.jsonl"
    argv = [verb, "--home", str(home)]
    if verb in ("plan", "apply", "ledger-init"):
        argv += ["--operator", operator, "--harness", harness]
    argv += list(extra)
    spec = {"script": str(SCRIPT), "argv": argv, "trace": str(trace)}
    done = subprocess.run([sys.executable, str(driver), json.dumps(spec)],
                          capture_output=True, text=True, env=child_env(home, bin_dir),
                          timeout=120)
    events = []
    if trace.exists():
        events = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    return SimpleNamespace(rc=done.returncode, out=done.stdout, err=done.stderr, events=events)


def plan_files(plan_out: str) -> list[str]:
    """The paths of the plan's FILE lines, in order."""
    return [line[len("FILE "):] for line in plan_out.splitlines() if line.startswith("FILE ")]


def plan_settings_path(plan_out: str) -> str:
    lines = [line[len("SETTINGS "):] for line in plan_out.splitlines()
             if line.startswith("SETTINGS ")]
    assert len(lines) == 1, plan_out
    return lines[0]


def plan_option_ids(plan_out: str) -> list[str]:
    return [line[len("OPTIONAL "):] for line in plan_out.splitlines()
            if line.startswith("OPTIONAL ")]


def plan_configure(plan_out: str) -> tuple[list[str], dict]:
    """The `claude plugin configure` argument list and the JSON values the plan lists."""
    lines = plan_out.splitlines()
    head = next(index for index, line in enumerate(lines)
                if line.startswith("claude plugin configure "))
    assert lines[head + 1] == "----"
    end = lines.index("----", head + 2)
    return lines[head].split()[1:], json.loads("\n".join(lines[head + 2:end]))


def approve_args(ids: list[str]) -> list[str]:
    args: list[str] = []
    for ident in ids:
        args += ["--approve", ident]
    return args
