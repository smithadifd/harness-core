"""Tests of the instance folder: the template in the harness plugin and the
instance-plan and instance-create verbs of plugins/harness/setup.py.

The instance folder is optional and the person's own. These tests hold: the
template is the folder's files and nothing else; the plan writes nothing; the
create makes exactly the plan's files and a git repository, only in a new or
empty folder, and never through a symlink; a failure at any point rolls back
what it made; and setup's record, doctor and remove never treat the folder as
a placed file. Runs are child processes against fixture homes with stub
commands on a PATH that holds nothing else; the rollback tests call the script
in this process with its `os` wrapped, as test_harness_setup.py's driver does
for a child.
"""
from __future__ import annotations

import errno
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import importlib.util

import pytest

from harness_fixtures import (
    README, ROOT, SKILL, SCRIPT, child_env, load_setup, make_bin, make_home, put_stub,
    setup_run, setup_run_traced, show_stored, snap,
)

setup = load_setup()
INSTANCE_FILES = setup.INSTANCE_FILES
TEMPLATE = ROOT / "plugins" / "harness" / "files" / "instance"
GIT_REFUSAL = "setup: git is not on PATH"
NO_PERSON_SCRIPT = ROOT / "scripts" / "check-no-person.py"


def load_no_person():
    """scripts/check-no-person.py as a module, so the test runs its own shape rules."""
    spec = importlib.util.spec_from_file_location("no_person_for_instance_tests",
                                                  NO_PERSON_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def world(tmp_path, *, git=True, name="case"):
    folder = (tmp_path / name)
    folder.mkdir()
    folder = folder.resolve()
    home = make_home(folder, settings=None)
    work = folder / "work"
    work.mkdir()
    return SimpleNamespace(folder=folder, homedir=home, work=work,
                           bin=make_bin(folder, git=git))


def run(verb, w, path, **kw):
    return setup_run(verb, w.homedir, "--path", str(path), bin_dir=kw.pop("bin_dir", w.bin), **kw)


def template_files():
    return sorted(path.relative_to(TEMPLATE).as_posix()
                  for path in TEMPLATE.rglob("*") if path.is_file())


def made_files(folder: Path):
    """The files under folder, outside .git, as paths inside it."""
    return sorted(path.relative_to(folder).as_posix() for path in folder.rglob("*")
                  if path.is_file() and ".git" not in path.relative_to(folder).parts)


def git(folder, *args):
    return subprocess.run(["git", "-C", str(folder), *args], capture_output=True, text=True,
                          env={**os.environ, "HOME": str(folder.parent),
                               "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"})


# ---------------------------------------------------------------------------
# The template


def test_the_script_lists_exactly_the_files_of_the_template_folder():
    # A file added to the template folder later is caught here, not skipped.
    assert sorted(INSTANCE_FILES) == template_files()
    assert len(set(INSTANCE_FILES)) == len(INSTANCE_FILES)


def test_the_template_holds_markdown_and_a_gitignore_only():
    for rel in INSTANCE_FILES:
        assert rel == ".gitignore" or rel.endswith(".md"), rel
    assert {rel.split("/")[0] for rel in INSTANCE_FILES if "/" in rel} == {
        "roadmaps", "decisions", "standards"}


def test_the_template_text_keeps_the_database_out_of_the_instance_folder():
    text = (TEMPLATE / "README.md").read_text(encoding="utf-8")
    for gone in ("settings.json", "additionalDirectories", "LEDGER_DB", "<workspace>",
                 "<instance>", "lives in `.ledger/`", "Create an instance"):
        assert gone not in text, gone
    assert "Not here: this folder holds markdown only" in text
    assert ".local/share/ledger/ledger.db" in text
    ignore = (TEMPLATE / ".gitignore").read_text(encoding="utf-8")
    assert [line for line in ignore.splitlines() if line and not line.startswith("#")] == [
        ".ledger/"]


def test_no_template_file_names_a_private_system_or_a_home_path():
    # The no-person script's own shape rules (home paths, private hosts, tokens, decision
    # numbers), imported and run over each template file as it would be tracked; the
    # deny list is the script's job in CI and is not available here.
    no_person = load_no_person()
    for rel in INSTANCE_FILES:
        data = (TEMPLATE / rel).read_bytes()
        tracked = f"plugins/harness/files/instance/{rel}"
        assert no_person.scan_text(tracked, data, None, {}, set()) == [], rel
        text = data.decode("utf-8")
        assert "/Users/" not in text and "/home/" not in text, rel


# ---------------------------------------------------------------------------
# The plan


def test_the_plan_writes_nothing_and_lists_every_file_with_its_text(tmp_path):
    w = world(tmp_path)
    target = w.work / "inst"
    before = snap(w.folder)
    plan = run("instance-plan", w, target)
    assert plan.rc == 0, plan.err
    assert snap(w.folder) == before, "the plan wrote something"
    lines = plan.out.splitlines()
    assert lines[0] == f"INSTANCE FOLDER {target}"
    assert "a new folder, made now" in lines[1]
    listed = [line[len("INSTANCE FILE "):] for line in lines if line.startswith("INSTANCE FILE ")]
    assert listed == list(INSTANCE_FILES)
    for rel in INSTANCE_FILES:
        text = (TEMPLATE / rel).read_text(encoding="utf-8").rstrip("\n")
        assert f"INSTANCE FILE {rel}\n----\n{text}\n----\n" in plan.out, rel
    assert f"-C {target} init -q" in plan.out
    assert "NOT DONE: no commit, no remote and no setting is made" in plan.out


def test_the_plan_for_an_existing_empty_folder_says_it_stays(tmp_path):
    w = world(tmp_path)
    target = w.work / "inst"
    target.mkdir()
    plan = run("instance-plan", w, target)
    assert plan.rc == 0, plan.err
    assert "an existing empty folder, which stays" in plan.out


# ---------------------------------------------------------------------------
# Create


def test_create_makes_exactly_the_plans_files_and_a_git_repository_with_no_commit(tmp_path):
    w = world(tmp_path)
    target = w.work / "inst"
    plan = run("instance-plan", w, target)
    done = run("instance-create", w, target)
    assert done.rc == 0, done.err
    assert done.err == ""
    planned = [line[len("INSTANCE FILE "):] for line in plan.out.splitlines()
               if line.startswith("INSTANCE FILE ")]
    assert made_files(target) == sorted(planned)
    for rel in planned:
        assert (target / rel).read_bytes() == (TEMPLATE / rel).read_bytes(), rel
    out = done.out.splitlines()
    assert out[0] == f"created: {target} (a new folder)"
    assert out[1:1 + len(planned)] == [f"created: {target / rel}" for rel in planned]
    assert f"created: git repository at {target / '.git'}" in out
    assert git(target, "rev-parse", "--is-inside-work-tree").stdout.strip() == "true"
    assert git(target, "rev-parse", "--show-toplevel").stdout.strip() == str(target)
    assert git(target, "rev-parse", "--verify", "HEAD").returncode != 0, "a commit was made"
    assert git(target, "remote").stdout == ""
    assert git(target, "status", "--short").stdout.splitlines() == sorted(
        f"?? {name}" for name in {rel.split("/")[0] + ("/" if "/" in rel else "")
                                  for rel in planned})


def test_create_into_an_existing_empty_folder_keeps_the_folder(tmp_path):
    w = world(tmp_path)
    target = w.work / "inst"
    target.mkdir()
    inode = target.stat().st_ino
    done = run("instance-create", w, target)
    assert done.rc == 0, done.err
    assert done.out.splitlines()[0] == f"created: {target} (the empty folder)"
    assert target.stat().st_ino == inode
    assert made_files(target) == sorted(INSTANCE_FILES)


def test_create_makes_private_files_and_folders(tmp_path):
    w = world(tmp_path)
    target = w.work / "inst"
    assert run("instance-create", w, target).rc == 0
    for path in [target, *target.rglob("*")]:
        if ".git" in path.relative_to(target).parts:
            continue
        mode = stat.S_IMODE(path.lstat().st_mode)
        assert mode == (0o700 if path.is_dir() else 0o600), (path, oct(mode))


@pytest.mark.parametrize("before", [0o755, 0o777, 0o750], ids=["0755", "0777", "0750"])
def test_create_makes_an_existing_empty_folder_private_whatever_mode_it_had(tmp_path, before):
    w = world(tmp_path)
    target = w.work / "inst"
    target.mkdir()
    os.chmod(target, before)
    assert stat.S_IMODE(target.stat().st_mode) == before
    done = run("instance-create", w, target)
    assert done.rc == 0, done.err
    mode = stat.S_IMODE(target.stat().st_mode)
    assert mode == 0o700, f"existing empty folder left at {oct(mode)}, not 0700"


def test_create_follows_a_symlink_above_the_folder_and_names_the_real_path(tmp_path):
    w = world(tmp_path)
    real = w.work / "real"
    real.mkdir()
    (w.work / "link").symlink_to(real)
    done = run("instance-create", w, w.work / "link" / "inst")
    assert done.rc == 0, done.err
    assert done.out.splitlines()[0] == f"created: {real / 'inst'} (a new folder)"
    assert made_files(real / "inst") == sorted(INSTANCE_FILES)


def test_create_writes_only_inside_the_folder_and_nothing_under_the_claude_folder(tmp_path):
    w = world(tmp_path)
    target = w.work / "inst"
    before = snap(w.folder)
    done = setup_run_traced("instance-create", w.homedir, "--path", str(target), bin_dir=w.bin)
    assert done.rc == 0, done.err
    assert done.events, "the trace is empty, so it proves nothing"
    for event in done.events:
        if event["path"] == os.devnull:  # subprocess opens it for git's stdin
            continue
        assert event["path"] == str(target) or event["path"].startswith(f"{target}/"), event
    after = snap(w.folder)
    own = {"trace_driver.py", "trace-0.jsonl"}  # the trace driver's own files
    assert {name for name in after if name not in before and name not in own
            and not name.startswith("work/inst")} == set(), "something outside the folder"
    assert not (w.homedir / ".claude").exists(), "setup's lock or record was made"


def test_git_does_not_follow_a_git_dir_from_the_environment(tmp_path):
    w = world(tmp_path)
    target = w.work / "inst"
    elsewhere = w.folder / "elsewhere.git"
    env = child_env(w.homedir, w.bin)
    env["GIT_DIR"] = str(elsewhere)
    env["GIT_WORK_TREE"] = str(w.folder)
    done = subprocess.run([sys.executable, str(SCRIPT), "instance-create", "--home", str(w.homedir),
                           "--path", str(target)], capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr
    assert (target / ".git").is_dir()
    assert not elsewhere.exists()


# ---------------------------------------------------------------------------
# Refusals: the same for the plan and the create, each before any write


def build(case, w):
    """(path to pass, expected text in the refusal, extra run arguments)."""
    inst = w.work / "inst"
    if case == "a file in it":
        inst.mkdir()
        (inst / "notes.md").write_text("mine\n", encoding="utf-8")
        return inst, "is not empty; the instance folder must be new or empty", {}
    if case == "a hidden file in it":
        inst.mkdir()
        (inst / ".keep").write_text("", encoding="utf-8")
        return inst, "is not empty; the instance folder must be new or empty", {}
    if case == "an empty subfolder in it":
        (inst / "sub").mkdir(parents=True)
        return inst, "is not empty; the instance folder must be new or empty", {}
    if case == "a symlink to an empty folder":
        (w.work / "empty").mkdir()
        inst.symlink_to(w.work / "empty")
        return inst, f"{inst} is a symlink; choose a new folder", {}
    if case == "a dangling symlink":
        inst.symlink_to(w.work / "nowhere")
        return inst, f"{inst} is a symlink; choose a new folder", {}
    if case == "a file":
        inst.write_text("mine\n", encoding="utf-8")
        return inst, f"{inst} exists and is not a folder", {}
    if case == "a relative path":
        return "work/inst", "the instance folder must be an absolute path", {}
    if case == "a dotdot part":
        return f"{w.work}/a/../inst", "the instance folder path must not hold a .. part", {}
    if case == "a missing parent":
        return (w.work / "nope" / "inst",
                f"the folder that should hold the instance folder does not exist: {w.work / 'nope'}",
                {})
    if case == "the root":
        return "/", "the instance folder cannot be the root folder", {}
    if case == "inside the claude folder":
        (w.homedir / ".claude").mkdir()
        return (w.homedir / ".claude" / "inst",
                f"the instance folder cannot be inside {w.homedir / '.claude'}", {})
    if case == "the claude folder itself":
        (w.homedir / ".claude").mkdir()
        return w.homedir / ".claude", f"the instance folder cannot be inside {w.homedir / '.claude'}", {}
    if case == "inside a claude folder that is a symlink":
        away = w.folder / "away"
        away.mkdir()
        (w.homedir / ".claude").symlink_to(away)
        return away / "inst", f"the instance folder cannot be inside {away}", {}
    if case == "no git on the path":
        return inst, GIT_REFUSAL, {"bin_dir": make_bin(w.folder / "work", git=False)}
    raise AssertionError(case)


CASES = ["a file in it", "a hidden file in it", "an empty subfolder in it",
         "a symlink to an empty folder", "a dangling symlink", "a file", "a relative path",
         "a dotdot part", "a missing parent", "the root", "inside the claude folder",
         "the claude folder itself", "inside a claude folder that is a symlink",
         "no git on the path"]


@pytest.mark.parametrize("verb", ["instance-plan", "instance-create"])
@pytest.mark.parametrize("case", CASES)
def test_a_refused_folder_stops_both_verbs_with_one_line_and_nothing_written(tmp_path, case, verb):
    w = world(tmp_path)
    path, expected, extra = build(case, w)
    before = snap(w.folder)
    done = run(verb, w, path, **extra)
    assert done.rc == 1, (done.out, done.err)
    assert done.out == ""
    assert done.err.count("\n") == 1 and done.err.startswith("setup: "), done.err
    assert expected in done.err, done.err
    assert snap(w.folder) == before, "a refused run changed something"


def test_a_missing_path_argument_is_refused_by_name(tmp_path):
    w = world(tmp_path)
    for verb in ("instance-plan", "instance-create"):
        done = setup_run(verb, w.homedir, bin_dir=w.bin)
        assert done.rc == 1
        assert done.err == "setup: --path is required for instance-plan and instance-create\n"


def test_a_refused_non_empty_folder_keeps_the_persons_files_byte_for_byte(tmp_path):
    w = world(tmp_path)
    inst = w.work / "inst"
    (inst / "decisions").mkdir(parents=True)
    (inst / "README.md").write_text("my own readme\n", encoding="utf-8")
    (inst / "decisions" / "0001-x.md").write_text("mine\n", encoding="utf-8")
    before = snap(w.folder)
    done = run("instance-create", w, inst)
    assert done.rc == 1
    assert snap(w.folder) == before
    assert (inst / "README.md").read_text(encoding="utf-8") == "my own readme\n"


def test_a_missing_template_file_refuses_before_any_write(tmp_path, monkeypatch):
    w = world(tmp_path)
    empty = w.folder / "no-template"
    empty.mkdir()
    monkeypatch.setattr(setup, "INSTANCE_SOURCE", empty)
    monkeypatch.setenv("PATH", str(w.bin))
    before = snap(w.folder)
    for verb in ("instance-plan", "instance-create"):
        with pytest.raises(setup.SetupError) as raised:
            setup.run([verb, "--home", str(w.homedir), "--path", str(w.work / "inst")])
        assert str(raised.value) == (
            f"instance/{INSTANCE_FILES[0]} is missing from the plugin files folder")
        assert snap(w.folder) == before


def test_an_unreadable_existing_folder_is_refused_by_name(tmp_path, monkeypatch):
    w = world(tmp_path)
    inst = w.work / "inst"
    inst.mkdir()

    class ListdirDenied:
        def listdir(self, path="."):
            if str(path) == str(inst):
                raise OSError(errno.EACCES, "denied")
            return os.listdir(path)

        def __getattr__(self, name):
            return getattr(os, name)

    monkeypatch.setattr(setup, "os", ListdirDenied())
    monkeypatch.setenv("PATH", str(w.bin))
    with pytest.raises(setup.SetupError) as raised:
        setup.run(["instance-plan", "--home", str(w.homedir), "--path", str(inst)])
    assert str(raised.value) == f"cannot read {inst}: [Errno 13] denied"


# ---------------------------------------------------------------------------
# A folder that changes between the check and the write


class Race:
    """Run `act` once, when setup asks where git is: after the folder was
    judged and before anything is written."""

    def __init__(self, monkeypatch, act):
        real = setup.which_git

        def which_git():
            found = real()
            act()
            return found

        monkeypatch.setattr(setup, "which_git", which_git)


def in_process(monkeypatch, w, target, *, verb="instance-create"):
    monkeypatch.setenv("PATH", str(w.bin))
    monkeypatch.setenv("HOME", str(w.homedir))
    old = os.umask(0o077)
    try:
        return setup.run([verb, "--home", str(w.homedir), "--path", str(target)])
    finally:
        os.umask(old)


def test_a_file_that_appears_in_the_empty_folder_is_never_overwritten_or_deleted(
        tmp_path, monkeypatch):
    w = world(tmp_path)
    inst = w.work / "inst"
    inst.mkdir()
    Race(monkeypatch, lambda: (inst / "README.md").write_text("mine\n", encoding="utf-8"))
    before = snap(w.folder)
    with pytest.raises(setup.SetupError) as raised:
        in_process(monkeypatch, w, inst)
    assert str(raised.value) == f"{inst} is not empty; the instance folder must be new or empty"
    after = snap(w.folder)
    assert after.pop("work/inst/README.md")[0] == "file", "the person's file was deleted"
    assert after == before
    assert (inst / "README.md").read_text(encoding="utf-8") == "mine\n"


def test_a_folder_that_appears_where_a_new_one_was_to_be_made_is_left_alone(
        tmp_path, monkeypatch):
    w = world(tmp_path)
    inst = w.work / "inst"

    def appear():
        inst.mkdir()
        (inst / "mine.md").write_text("mine\n", encoding="utf-8")

    Race(monkeypatch, appear)
    with pytest.raises(setup.SetupError) as raised:
        in_process(monkeypatch, w, inst)
    assert str(raised.value) == f"{inst} appeared while setup was running; nothing was written"
    assert sorted(path.name for path in inst.iterdir()) == ["mine.md"]


def test_a_file_created_after_the_last_emptiness_check_is_never_overwritten(
        tmp_path, monkeypatch):
    w = world(tmp_path)
    inst = w.work / "inst"
    inst.mkdir()
    (inst / ".gitignore").write_text("mine\n", encoding="utf-8")

    class StaleListing:
        """os, whose listing of the instance folder still says it is empty, as
        it was when checked, before the person's file arrived."""

        def listdir(self, path="."):
            return [] if isinstance(path, int) or str(path) == str(inst) else os.listdir(path)

        def __getattr__(self, name):
            return getattr(os, name)

    monkeypatch.setattr(setup, "os", StaleListing())
    before = snap(w.folder)
    with pytest.raises(setup.SetupError) as raised:
        in_process(monkeypatch, w, inst)
    assert str(raised.value).startswith("unexpected error: FileExistsError: ")
    assert snap(w.folder) == before
    assert (inst / ".gitignore").read_text(encoding="utf-8") == "mine\n"


def test_an_empty_folder_swapped_for_a_symlink_is_not_followed(tmp_path, monkeypatch):
    w = world(tmp_path)
    inst = w.work / "inst"
    inst.mkdir()
    away = w.folder / "away"
    away.mkdir()

    def swap():
        inst.rmdir()
        inst.symlink_to(away)

    Race(monkeypatch, swap)
    with pytest.raises(setup.SetupError) as raised:
        in_process(monkeypatch, w, inst)
    assert str(raised.value).startswith(f"cannot open {inst}: ")
    assert list(away.iterdir()) == [], "setup wrote through the symlink"


# ---------------------------------------------------------------------------
# A failure rolls back what this run made


class FlakyOs:
    """setup.py's `os`, counting every call that can change the disk (a
    create, mkdir, write, fsync, unlink and rmdir) and raising an I/O error at
    the numbered ones."""

    CHANGING = ("mkdir", "write", "fsync", "unlink", "rmdir")

    def __init__(self, fail_at=()):
        self.fail_at = set(fail_at)
        self.points: list[str] = []

    def hit(self, label):
        self.points.append(label)
        if len(self.points) in self.fail_at:
            raise OSError(errno.EIO, "injected")

    def __getattr__(self, name):
        real = getattr(os, name)
        if name == "open":
            def opener(path, flags, *args, **kwargs):
                if flags & os.O_CREAT:
                    self.hit(f"create {path}")
                return real(path, flags, *args, **kwargs)
            return opener
        if name in self.CHANGING:
            def changer(*args, **kwargs):
                self.hit(f"{name} {args[0] if args else ''}")
                return real(*args, **kwargs)
            return changer
        return real


def attempt(monkeypatch, tmp_path, name, fail_at, *, existing, git=True):
    w = world(tmp_path, name=name, git=git)
    inst = w.work / "inst"
    if existing:
        inst.mkdir()
    flaky = FlakyOs(fail_at)
    monkeypatch.setattr(setup, "os", flaky)
    before = snap(w.folder)
    try:
        in_process(monkeypatch, w, inst)
        error = None
    except setup.SetupError as exc:
        error = str(exc)
    monkeypatch.setattr(setup, "os", os)
    return SimpleNamespace(w=w, inst=inst, flaky=flaky, error=error, before=before)


@pytest.mark.parametrize("existing", [False, True], ids=["new-folder", "empty-folder"])
def test_an_error_at_every_point_rolls_back_to_the_state_before(tmp_path, monkeypatch, existing):
    clean = attempt(monkeypatch, tmp_path, "clean", (), existing=existing)
    assert clean.error is None
    points = len(clean.flaky.points)
    assert points >= 18, clean.flaky.points
    for k in range(1, points + 1):
        run_k = attempt(monkeypatch, tmp_path, f"k{k}", (k,), existing=existing)
        label = clean.flaky.points[k - 1].split(" ")[0]
        cause = "unexpected error: OSError: [Errno 5] injected"
        # Point 1 fails before anything is made, so there is nothing to roll back.
        expected = cause if k == 1 else f"{cause}; rolled back, nothing was left"
        assert run_k.error == expected, (k, label, run_k.error)
        assert snap(run_k.w.folder) == run_k.before, (k, label)


class ChmodOs(FlakyOs):
    """setup.py's `os` with every fchmod counted and an I/O error raised at the
    numbered ones; the folder's mode is changed for real otherwise."""

    def __init__(self, fail_chmod=()):
        super().__init__()
        self.fail_chmod = set(fail_chmod)
        self.chmods: list[int] = []

    def fchmod(self, fd, mode):
        self.chmods.append(mode)
        if len(self.chmods) in self.fail_chmod:
            raise OSError(errno.EIO, "injected")
        return os.fchmod(fd, mode)


def fail_git_init(monkeypatch):
    def broken(git, target):
        raise setup.SetupError("git init failed: boom")

    monkeypatch.setattr(setup, "instance_git_init", broken)


@pytest.mark.parametrize("fail", ["the-mode-change", "git-init-after-it"])
@pytest.mark.parametrize("before", [0o755, 0o750], ids=["0755", "0750"])
def test_a_failure_after_the_mode_change_leaves_the_folder_with_its_mode_and_no_git_folder(
        tmp_path, monkeypatch, before, fail):
    w = world(tmp_path)
    inst = w.work / "inst"
    inst.mkdir()
    os.chmod(inst, before)
    flaky = ChmodOs(fail_chmod=(1,) if fail == "the-mode-change" else ())
    monkeypatch.setattr(setup, "os", flaky)
    if fail == "git-init-after-it":
        fail_git_init(monkeypatch)
    with pytest.raises(setup.SetupError) as raised:
        in_process(monkeypatch, w, inst)
    monkeypatch.setattr(setup, "os", os)
    if fail == "the-mode-change":
        assert str(raised.value) == "unexpected error: OSError: [Errno 5] injected"
        assert flaky.chmods == [0o700]
    else:
        assert str(raised.value) == "git init failed: boom; rolled back, nothing was left"
        assert flaky.chmods == [0o700, before], "the mode was not put back"
    assert stat.S_IMODE(inst.stat().st_mode) == before
    assert not (inst / ".git").exists(), "a git folder was left behind"
    assert os.listdir(inst) == []


def test_a_mode_that_cannot_be_put_back_is_kept_and_named(tmp_path, monkeypatch):
    w = world(tmp_path)
    inst = w.work / "inst"
    inst.mkdir()
    os.chmod(inst, 0o755)
    monkeypatch.setattr(setup, "os", ChmodOs(fail_chmod=(2,)))
    fail_git_init(monkeypatch)
    with pytest.raises(setup.SetupError) as raised:
        in_process(monkeypatch, w, inst)
    monkeypatch.setattr(setup, "os", os)
    assert str(raised.value) == (
        f"git init failed: boom; rolled back, except for what is kept: {inst}")
    assert os.listdir(inst) == []


def test_a_rollback_step_that_fails_keeps_and_names_what_it_could_not_remove(
        tmp_path, monkeypatch):
    clean = attempt(monkeypatch, tmp_path, "clean", (), existing=False)
    last_write = max(index for index, label in enumerate(clean.flaky.points, 1)
                     if label.startswith("write "))
    run_k = attempt(monkeypatch, tmp_path, "k", (last_write, last_write + 1), existing=False)
    last = run_k.inst / INSTANCE_FILES[-1]
    assert run_k.error == (
        "unexpected error: OSError: [Errno 5] injected; rolled back, except for what is kept: "
        f"{last}, {last.parent}, {run_k.inst}")
    left = sorted(path.relative_to(run_k.w.folder).as_posix()
                  for path in (run_k.w.folder / "work").rglob("*"))
    assert left == ["work/inst", "work/inst/standards", "work/inst/standards/README.md"]


def stub_git(w, body):
    return put_stub(w.bin, "git", f"#!/bin/sh\n{body}\n")


def test_a_failed_git_init_rolls_back_and_names_its_error(tmp_path):
    w = world(tmp_path)
    stub_git(w, 'echo "fatal: boom" >&2\nexit 1')
    target = w.work / "inst"
    before = snap(w.folder)
    done = run("instance-create", w, target)
    assert done.rc == 1
    assert done.err == "setup: git init failed: fatal: boom; rolled back, nothing was left\n"
    assert snap(w.folder) == before


def test_a_git_that_cannot_run_rolls_back(tmp_path):
    w = world(tmp_path)
    stub_git(w, "")
    (w.bin / "git").write_text("#!/nonexistent/interpreter\n", encoding="utf-8")
    before = snap(w.folder)
    done = run("instance-create", w, w.work / "inst")
    assert done.rc == 1
    assert done.err.startswith("setup: git init failed: [Errno 2] ")
    assert done.err.endswith("; rolled back, nothing was left\n")
    assert snap(w.folder) == before


def test_a_git_that_times_out_rolls_back(tmp_path, monkeypatch):
    w = world(tmp_path)
    real = subprocess.run

    def slow(command, *args, **kwargs):
        if command[1:2] == ["-C"]:
            raise subprocess.TimeoutExpired(command, 60)
        return real(command, *args, **kwargs)

    monkeypatch.setattr(setup.subprocess, "run", slow)
    before = snap(w.folder)
    with pytest.raises(setup.SetupError) as raised:
        in_process(monkeypatch, w, w.work / "inst")
    assert str(raised.value).startswith("git init failed: Command ")
    assert str(raised.value).endswith("; rolled back, nothing was left")
    assert snap(w.folder) == before


def test_a_git_init_that_makes_no_git_folder_rolls_back(tmp_path):
    w = world(tmp_path)
    stub_git(w, "exit 0")
    before = snap(w.folder)
    done = run("instance-create", w, w.work / "inst")
    assert done.rc == 1
    assert done.err == (
        "setup: git init reported success but made no .git folder; rolled back, "
        "nothing was left\n")
    assert snap(w.folder) == before


def test_a_git_that_fails_after_making_a_git_folder_keeps_and_names_it(tmp_path):
    w = world(tmp_path)
    stub_git(w, '/bin/mkdir "$2/.git"\necho partial > "$2/.git/partial"\n'
                'echo "fatal: disk" >&2\nexit 1')
    target = w.work / "inst"
    done = run("instance-create", w, target)
    assert done.rc == 1
    assert done.err == (
        "setup: git init failed: fatal: disk; rolled back, except for what is kept: "
        f"{target / '.git'}\n")
    assert sorted(path.relative_to(target).as_posix() for path in target.rglob("*")) == [
        ".git", ".git/partial"], "only git's own partial folder is left"
    again = run("instance-create", w, target)
    assert again.rc == 1 and "is not empty" in again.err


# ---------------------------------------------------------------------------
# The folder is the person's: never a placed file


def full_setup(w):
    applied = setup_run("apply", w.homedir, bin_dir=w.bin)
    assert applied.rc == 0, applied.err
    show_stored(w.bin)
    init = setup_run("ledger-init", w.homedir, bin_dir=w.bin)
    assert init.rc == 0, init.err


def test_setup_never_records_and_doctor_never_lists_an_instance_folder(tmp_path):
    w = world(tmp_path)
    full_setup(w)
    record = (w.homedir / setup.RECORD_REL).read_bytes()
    before = setup_run("doctor", w.homedir, bin_dir=w.bin)
    assert before.rc == 0, before.out
    target = w.homedir / "notes"
    made = run("instance-create", w, target)
    assert made.rc == 0, made.err
    after = setup_run("doctor", w.homedir, bin_dir=w.bin)
    assert after.out == before.out and after.rc == 0
    assert str(target) not in after.out and "notes" not in after.out
    assert (w.homedir / setup.RECORD_REL).read_bytes() == record
    assert b"notes" not in record
    assert not (w.homedir / ".claude" / "harness-setup.lock").read_bytes()


def test_uninstall_leaves_an_instance_folder_exactly_as_it_was(tmp_path):
    w = world(tmp_path)
    full_setup(w)
    target = w.homedir / "notes"
    assert run("instance-create", w, target).rc == 0
    (target / "roadmaps" / "plan.md").write_text("my plan\n", encoding="utf-8")
    (target / "README.md").write_text("edited by the person\n", encoding="utf-8")
    kept = snap(target)
    removed = setup_run("remove", w.homedir, bin_dir=w.bin)
    assert removed.rc == 0, removed.err
    assert "notes" not in removed.out and str(target) not in removed.out
    assert snap(target) == kept


def test_an_instance_folder_made_before_setup_is_untouched_by_apply_and_remove(tmp_path):
    w = world(tmp_path)
    target = w.homedir / "notes"
    assert run("instance-create", w, target).rc == 0
    kept = snap(target)
    applied = setup_run_traced("apply", w.homedir, bin_dir=w.bin)
    assert applied.rc == 0, applied.err
    touched = {event["path"] for event in applied.events}
    assert touched and not any(path == str(target) or path.startswith(f"{target}/")
                               for path in touched)
    show_stored(w.bin)
    assert setup_run("ledger-init", w.homedir, bin_dir=w.bin).rc == 0
    assert setup_run("remove", w.homedir, bin_dir=w.bin).rc == 0
    assert snap(target) == kept


def test_the_record_can_never_list_an_instance_file():
    # The record's file list admits the rules default and the override only.
    head = setup.header_line(None)
    for rel in INSTANCE_FILES + (".claude/rules/harness.md.instance",):
        body = {"complete": True, "files": [{"path": rel, "sha256": "0" * 64}],
                "settings": {"created": False, "sha256": None, "items": [],
                             "created_permissions": False, "created_deny": False},
                "dirs": [], "backup_sha256": None}
        assert not setup.valid_body(body, setup.parse_line(head)), rel


# ---------------------------------------------------------------------------
# The offer is optional, and the README and the skill match the script


def section(text, heading):
    lines = text.splitlines()
    start = lines.index(heading) + 1
    end = next((index for index in range(start, len(lines)) if lines[index].startswith("#")),
               len(lines))
    return lines[start:end]


def test_the_readme_lists_the_files_the_plan_lists(tmp_path):
    w = world(tmp_path)
    plan = run("instance-plan", w, w.work / "inst")
    planned = {line[len("INSTANCE FILE "):] for line in plan.out.splitlines()
               if line.startswith("INSTANCE FILE ")}
    lines = section(README.read_text(encoding="utf-8"), "### Make an instance folder (optional)")
    paths = {match.group(1) for line in lines if (match := re.match(r"\| `([^`]+)` \|", line))}
    assert paths == planned == set(INSTANCE_FILES)
    text = "\n".join(lines)
    assert "/harness:setup instance" in text
    assert "plugins/harness/files/instance/" in text


def test_the_skill_offers_the_instance_folder_once_and_never_without_a_yes():
    text = SKILL.read_text(encoding="utf-8")
    assert "disable-model-invocation: true" in text
    front = text.split("---", 2)[1]
    assert 'argument-hint: "[doctor | uninstall | instance]"' in front
    steps = {int(match.group(1)): match.group(0) for match in
             re.finditer(r"^(\d+)\. \*\*[^*]+\*\*", text.split("## Instance folder")[0],
                         re.MULTILINE)}
    offer = next(number for number, line in steps.items() if "Offer an instance folder" in line)
    end = next(number for number, line in steps.items() if "start a new session" in line)
    check = next(number for number, line in steps.items() if "Check the result" in line)
    assert check < offer < end
    offer_text = text.split("Offer an instance folder")[1].split("**End by telling")[0]
    assert "Ask once" in offer_text and "do not ask again" in offer_text
    assert "Never run it without a yes" in offer_text
    mode = text.split("## Instance folder")[1].split("## Doctor")[0]
    assert mode.index("instance-plan") < mode.index("Take the yes") < mode.index("instance-create")
    assert "Never choose or suggest a path" in mode
    assert "you never do it for them" in mode


def test_the_instance_commands_run_from_the_skill_text_through_bash(tmp_path):
    w = world(tmp_path)
    commands = [line.strip() for line in SKILL.read_text(encoding="utf-8").splitlines()
                if line.strip().startswith('python3 "${CLAUDE_SKILL_DIR}/../../setup.py" instance-')]
    assert [command.split()[2] for command in commands] == ["instance-plan", "instance-create"]
    target = w.work / "it's here"
    quoted = str(target).replace("'", "'\\''")
    env = child_env(w.homedir, w.bin)
    env["CLAUDE_SKILL_DIR"] = str(SKILL.parent)
    for command in commands:
        done = subprocess.run(["/bin/bash", "-c", command.replace("<path>", quoted)],
                              capture_output=True, text=True, env=env, timeout=120)
        assert done.returncode == 0, done.stderr
    assert made_files(target) == sorted(INSTANCE_FILES)
