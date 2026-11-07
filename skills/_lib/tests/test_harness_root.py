"""harness_root suite, organised by seam (CS2, D1).

Four seams are under test:

  S1  root resolution      `resolve(config)` -> `HarnessPaths.root`. Adapter
                            that varies: `HARNESS_ROOT` env set/unset x a
                            config `harness_root.root` key set/unset x
                            neither (refused with `HarnessRootAbsent`).
                            Precedence: env > config key > pinned checkout
                            carrying the root marker.
  S2  subkey resolution    `resolve(config)` -> `.roadmaps/.standards/
                            .decisions/.topics`. Adapter: a subkey override
                            present in config vs absent (falls back to
                            `<root>/<name>`).
  S3  override resolution  `resolve(config)` -> `.memory_index` / `.delivery`.
                            Adapter: an override present vs absent (memory_index
                            is derived from the resolved root under the
                            running user's HOME; delivery falls back to
                            `"local"`).
  S4  config-source seam   `resolve(...)` accepts a dict, a path to a JSON
                            file, or `None`; a missing/unreadable file
                            degrades to `{}`, so the root must come from the
                            environment or the refusal is raised.
  S5  standard resolution  covered by `test_standards_resolve_once.py`: the
                            instance/core ordered search and named refusals.

Nothing here is a tautology: every expected path is COMPUTED from `tmp_path`
or `os.environ`/`HARNESS_ROOT` inside the test. There is no default root, so
the "nothing set" cases assert the refusal.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import harness_root as hr  # noqa: E402


@pytest.mark.parametrize("already_exists", [False, True])
def test_init_root_creates_only_the_starter_layout(tmp_path, monkeypatch, already_exists):
    root = tmp_path / "new-root"
    unrelated = tmp_path / "other-root"
    monkeypatch.setenv("HARNESS_ROOT", str(unrelated))
    if already_exists:
        root.mkdir()

    created = hr.init_root(root)

    assert created == root
    assert not unrelated.exists()
    assert {entry.name for entry in root.iterdir()} == {
        "roadmaps", "standards", "decisions", "topics", hr.ROOT_MARKER
    }
    assert (root / hr.ROOT_MARKER).read_bytes() == b""
    for name in ("roadmaps", "standards", "decisions", "topics"):
        assert (root / name).is_dir()
    assert [entry.name for entry in (root / "roadmaps").iterdir()] == ["wave-current.md"]
    assert (root / "roadmaps" / "wave-current.md").read_bytes() == b""
    for name in ("standards", "decisions", "topics"):
        assert list((root / name).iterdir()) == []


@pytest.mark.parametrize("entry_name", ["note.txt", ".hidden"])
def test_init_root_refuses_an_occupied_target(tmp_path, entry_name):
    root = tmp_path / "occupied"
    root.mkdir()
    (root / entry_name).touch()

    with pytest.raises(FileExistsError, match="target is occupied"):
        hr.init_root(root)

    assert {entry.name for entry in root.iterdir()} == {entry_name}


def test_init_root_refuses_a_second_call(tmp_path):
    root = tmp_path / "new-root"
    hr.init_root(root)

    with pytest.raises(FileExistsError, match="target is occupied"):
        hr.init_root(root)

    assert (root / "roadmaps" / "wave-current.md").read_bytes() == b""


def test_init_root_refuses_a_missing_parent_without_creating_it(tmp_path):
    parent = tmp_path / "missing-parent"
    root = parent / "new-root"

    with pytest.raises(FileNotFoundError):
        hr.init_root(root)

    assert not parent.exists()


@pytest.mark.parametrize("kind", ["file", "symlink"])
def test_init_root_refuses_a_target_that_is_not_a_plain_directory(tmp_path, kind):
    root = tmp_path / "target"
    if kind == "file":
        root.touch()
    else:
        destination = tmp_path / "destination"
        destination.mkdir()
        root.symlink_to(destination, target_is_directory=True)

    with pytest.raises(FileExistsError, match="target is occupied"):
        hr.init_root(root)


def test_init_root_does_not_change_strict_resolution(tmp_path, monkeypatch):
    root = hr.init_root(tmp_path / "new-root")
    monkeypatch.delenv("HARNESS_ROOT", raising=False)

    with pytest.raises(hr.HarnessRootAbsent):
        hr.resolve({}, cwd=tmp_path)
    assert hr.resolve({"harness_root": {"root": str(root)}}).root == root


# --- S1: root resolution ----------------------------------------------------- #


def test_unset_root_raises_with_the_fix_text(monkeypatch, tmp_path):
    monkeypatch.delenv("HARNESS_ROOT", raising=False)
    with pytest.raises(hr.HarnessRootAbsent) as excinfo:
        hr.resolve({}, cwd=tmp_path)
    assert str(excinfo.value) == hr.ROOT_ABSENT_FIX


def test_root_reads_config_key_when_env_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("HARNESS_ROOT", raising=False)
    paths = hr.resolve({"harness_root": {"root": str(tmp_path)}})
    assert paths.root == tmp_path


def test_root_env_beats_config_key(monkeypatch, tmp_path):
    env_root = tmp_path / "env-root"
    config_root = tmp_path / "config-root"
    monkeypatch.setenv("HARNESS_ROOT", str(env_root))
    paths = hr.resolve({"harness_root": {"root": str(config_root)}})
    assert paths.root == env_root
    assert paths.root != config_root


def test_root_env_alone_resolves(monkeypatch, tmp_path):
    env_root = tmp_path / "env-root"
    monkeypatch.setenv("HARNESS_ROOT", str(env_root))
    paths = hr.resolve({})
    assert paths.root == env_root


# --- S1b: a relative HARNESS_ROOT is rejected, never resolved against cwd
# --- (CS2 fix-forward F3/D12) ------------------------------------------------- #


def test_relative_env_root_is_rejected_and_the_root_is_absent(monkeypatch, tmp_path):
    monkeypatch.setenv("HARNESS_ROOT", "relative/dir")
    with pytest.raises(hr.HarnessRootAbsent):
        hr.resolve({}, cwd=tmp_path)


def test_relative_env_root_is_rejected_falls_through_to_config_key(monkeypatch, tmp_path):
    monkeypatch.setenv("HARNESS_ROOT", "relative/dir")
    config_root = tmp_path / "config-root"
    paths = hr.resolve({"harness_root": {"root": str(config_root)}})
    assert paths.root == config_root


def test_relative_env_root_prints_one_stderr_line_naming_it(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("HARNESS_ROOT", "relative/dir")
    with pytest.raises(hr.HarnessRootAbsent):
        hr.resolve({}, cwd=tmp_path)
    captured = capsys.readouterr()
    assert "relative/dir" in captured.err
    assert "HARNESS_ROOT" in captured.err


def test_absolute_env_root_is_accepted_unaffected_by_f3(monkeypatch, tmp_path):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    paths = hr.resolve({})
    assert paths.root == tmp_path


def test_tilde_env_root_expands_and_is_accepted(monkeypatch):
    monkeypatch.setenv("HARNESS_ROOT", "~/example-root")
    paths = hr.resolve({})
    assert paths.root == Path("~/example-root").expanduser()


# --- S1c: an absent root is a named refusal, and `require_root` changes
# --- nothing ------------------------------------------------------------------- #


def test_strict_resolve_raises_a_named_error_when_nothing_names_a_root(monkeypatch, tmp_path):
    monkeypatch.delenv("HARNESS_ROOT", raising=False)
    with pytest.raises(hr.HarnessRootAbsent):
        hr.resolve({}, require_root=True, cwd=tmp_path)


def test_strict_resolve_error_carries_the_one_line_fix(monkeypatch, tmp_path):
    monkeypatch.delenv("HARNESS_ROOT", raising=False)
    with pytest.raises(hr.HarnessRootAbsent) as excinfo:
        hr.resolve({"harness_root": {"root": None}}, require_root=True, cwd=tmp_path)
    message = str(excinfo.value)
    assert "HARNESS_ROOT=" in message
    assert "harness_root.root" in message


def test_strict_resolve_accepts_an_env_root(monkeypatch, tmp_path):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    assert hr.resolve({}, require_root=True).root == tmp_path


def test_strict_resolve_accepts_a_config_root(monkeypatch, tmp_path):
    monkeypatch.delenv("HARNESS_ROOT", raising=False)
    paths = hr.resolve({"harness_root": {"root": str(tmp_path)}}, require_root=True)
    assert paths.root == tmp_path


def test_strict_resolve_treats_a_rejected_relative_env_root_as_absent(monkeypatch, tmp_path):
    """A relative HARNESS_ROOT is already rejected (F3); the rejection must
    not fall through to a guessed root either."""
    monkeypatch.setenv("HARNESS_ROOT", "relative/dir")
    with pytest.raises(hr.HarnessRootAbsent):
        hr.resolve({}, require_root=True, cwd=tmp_path)


def test_strict_resolve_absent_root_is_a_lookup_error_naming_both_remedies_in_order(
    monkeypatch,
    tmp_path,
):
    """F6 (fix-forward r2): replaces a subclass-only pin
    (`issubclass(hr.HarnessRootAbsent, LookupError)` alone, never exercising
    a raise) with the observable a caller actually reads — `str(exc)` must
    NAME both ways to supply a root, env before config, in precedence order,
    so the message alone is the fix; the class hierarchy stays covered too,
    on the real instance this time. The CLI form of this same refusal
    (`harness_paths.py --require-root`, exit 2, nothing on stdout) is proven
    at `skills/session-handoff/tests/test_close_surfaces_from_harness_root.py
    ::test_the_strict_print_refuses_without_a_root_and_prints_no_path` — not
    duplicated here, since this module has no CLI of its own."""
    monkeypatch.delenv("HARNESS_ROOT", raising=False)
    with pytest.raises(hr.HarnessRootAbsent) as excinfo:
        hr.resolve({}, require_root=True, cwd=tmp_path)
    assert isinstance(excinfo.value, LookupError)
    message = str(excinfo.value)
    assert "export HARNESS_ROOT=" in message
    assert "harness_root.root" in message
    assert message.index("HARNESS_ROOT=") < message.index("harness_root.root")


def test_resolve_without_the_flag_refuses_too(monkeypatch, tmp_path):
    """The refusal is the default behaviour; `require_root` is a no-op kept
    for callers that still pass it."""
    monkeypatch.delenv("HARNESS_ROOT", raising=False)
    with pytest.raises(hr.HarnessRootAbsent):
        hr.resolve({}, cwd=tmp_path)
    with pytest.raises(hr.HarnessRootAbsent):
        hr.resolve({}, require_root=False, cwd=tmp_path)


# --- S2: subkey resolution ---------------------------------------------------- #


@pytest.mark.parametrize("subkey", ["roadmaps", "standards", "decisions", "topics"])
def test_subkey_defaults_to_root_slash_name(monkeypatch, tmp_path, subkey):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    paths = hr.resolve({}, cwd=tmp_path)
    assert getattr(paths, subkey) == tmp_path / subkey


@pytest.mark.parametrize("subkey", ["roadmaps", "standards", "decisions", "topics"])
def test_subkey_override_beats_root_slash_name(monkeypatch, tmp_path, subkey):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path / "root"))
    override = tmp_path / f"custom-{subkey}"
    paths = hr.resolve({"harness_root": {subkey: str(override)}})
    assert getattr(paths, subkey) == override


def test_subkeys_follow_a_redirected_root(monkeypatch, tmp_path):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    paths = hr.resolve({})
    assert paths.roadmaps == tmp_path / "roadmaps"
    assert paths.standards == tmp_path / "standards"
    assert paths.decisions == tmp_path / "decisions"
    assert paths.topics == tmp_path / "topics"


# --- S3: memory_index / delivery override ------------------------------------ #


def test_memory_index_uses_the_running_users_home(tmp_path):
    """The derivation reads HOME when it runs: a subprocess with its own HOME
    gets an index under that HOME, and no person's home appears anywhere."""
    home = tmp_path / "some-user"
    root = tmp_path / "cfg-root"
    probe = (
        "import sys; sys.path.insert(0, sys.argv[1]); import harness_root as hr; "
        "print(hr.resolve({}).memory_index)"
    )
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "HARNESS_ROOT": str(root)}
    proc = subprocess.run(
        [sys.executable, "-c", probe, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))],
        env=env, capture_output=True, text=True, check=True,
    )
    slug = str(root).replace("/", "-")
    assert proc.stdout.strip() == str(home / ".claude" / "projects" / slug / "memory" / "MEMORY.md")


def test_memory_index_base_is_person_neutral():
    assert hr.CLAUDE_PROJECTS_DIR == "~" + "/.claude/projects"
    assert not hasattr(hr, "DEFAULT_MEMORY_INDEX")
    assert not hasattr(hr, "DEFAULT_ROOT")


def test_memory_index_override_beats_resolved_root(monkeypatch, tmp_path):
    root = tmp_path / "root"
    override = tmp_path / "MEMORY.md"
    monkeypatch.setenv("HARNESS_ROOT", str(root))
    paths = hr.resolve({"harness_root": {"memory_index": str(override)}})
    assert paths.memory_index == override


def test_memory_index_is_derived_from_resolved_root(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "root.with_under-score"
    monkeypatch.setenv("HARNESS_ROOT", str(root))
    paths = hr.resolve({})
    slug = str(root).replace("/", "-")
    assert paths.memory_index == (
        tmp_path / "home" / ".claude" / "projects" / slug / "memory" / "MEMORY.md"
    )


def test_memory_index_is_derived_from_config_root_when_env_unset(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("HARNESS_ROOT", raising=False)
    root = tmp_path / "config-repo"
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"harness_root": {"root": str(root)}}),
        encoding="utf-8",
    )

    paths = hr.resolve(config_path)

    root_slug = str(root.absolute()).replace("/", "-")
    assert paths.memory_index == (
        Path.home() / ".claude" / "projects" / root_slug / "memory" / "MEMORY.md"
    )


def test_delivery_defaults_to_local(monkeypatch, tmp_path):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    paths = hr.resolve({})
    assert paths.delivery == "local"


def test_delivery_is_a_pass_through_override(monkeypatch, tmp_path):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    paths = hr.resolve({"harness_root": {"delivery": ["local", "desktop"]}})
    assert paths.delivery == ["local", "desktop"]


# --- S4: config-source seam --------------------------------------------------- #


def test_resolve_accepts_a_dict(monkeypatch):
    monkeypatch.delenv("HARNESS_ROOT", raising=False)
    paths = hr.resolve({"harness_root": {"root": "/tmp/example-root"}})
    assert paths.root == Path("/tmp/example-root")


def test_resolve_accepts_none(monkeypatch, tmp_path):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    paths = hr.resolve(None, cwd=tmp_path)
    assert paths.root == tmp_path


def test_resolve_accepts_a_json_file_path(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_ROOT", raising=False)
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"harness_root": {"root": str(tmp_path / "r")}}))
    paths = hr.resolve(config_path)
    assert paths.root == tmp_path / "r"


def test_resolve_degrades_on_missing_config_file(tmp_path, monkeypatch):
    """A missing config degrades to `{}`: the env root still resolves, and
    with no env root the refusal is raised (not an OSError)."""
    missing = tmp_path / "does-not-exist.json"
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path / "env-root"))
    assert hr.resolve(missing, cwd=tmp_path).root == tmp_path / "env-root"
    monkeypatch.delenv("HARNESS_ROOT")
    with pytest.raises(hr.HarnessRootAbsent):
        hr.resolve(missing, cwd=tmp_path)


def test_resolve_degrades_on_unreadable_json(tmp_path, monkeypatch):
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json")
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path / "env-root"))
    assert hr.resolve(bad, cwd=tmp_path).root == tmp_path / "env-root"
    monkeypatch.delenv("HARNESS_ROOT")
    with pytest.raises(hr.HarnessRootAbsent):
        hr.resolve(bad, cwd=tmp_path)


# --- HarnessPaths shape ------------------------------------------------------- #


def test_harness_paths_carries_exactly_the_declared_attributes(monkeypatch, tmp_path):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    paths = hr.resolve({})
    for attr in (
        "root",
        "roadmaps",
        "standards",
        "core_standards",
        "decisions",
        "topics",
        "memory_index",
        "delivery",
    ):
        assert hasattr(paths, attr)


# --- resolve_relative (the small joiner the four-skill sweep uses) ----------- #


def test_resolve_relative_joins_a_bare_filename_onto_a_subkey(monkeypatch):
    monkeypatch.delenv("HARNESS_ROOT", raising=False)
    paths = hr.resolve({"harness_root": {"root": "/tmp/example-root"}})
    joined = hr.resolve_relative(paths, "roadmaps", "wave-current.md")
    assert joined == Path("/tmp/example-root/roadmaps/wave-current.md")


def test_resolve_relative_leaves_an_absolute_value_alone(monkeypatch):
    monkeypatch.delenv("HARNESS_ROOT", raising=False)
    paths = hr.resolve({"harness_root": {"root": "/tmp/example-root"}})
    joined = hr.resolve_relative(paths, "roadmaps", "/elsewhere/x.md")
    assert joined == Path("/elsewhere/x.md")


def test_resolve_relative_expands_a_tilde_value(monkeypatch):
    monkeypatch.delenv("HARNESS_ROOT", raising=False)
    paths = hr.resolve({"harness_root": {"root": "/tmp/example-root"}})
    joined = hr.resolve_relative(paths, "roadmaps", "~/elsewhere/x.md")
    assert joined == Path("~/elsewhere/x.md").expanduser()


def test_resolve_relative_empty_value_is_the_subkey_itself(monkeypatch):
    monkeypatch.delenv("HARNESS_ROOT", raising=False)
    paths = hr.resolve({"harness_root": {"root": "/tmp/example-root"}})
    joined = hr.resolve_relative(paths, "roadmaps", "")
    assert joined == paths.roadmaps


# --- S1d: cwd checkout is adopted only for the pinned repository, found by the
# --- root marker ---------------------------------------------------------------- #

ORIGIN = "git@github.com:example/config-repo.git"
PIN = "github.com/example/config-repo"


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _repo(path: Path, origin: str, *, marker: bool = True, wave: bool = True) -> Path:
    path.mkdir(parents=True)
    (path / "roadmaps").mkdir()
    (path / "README").write_text("x\n", encoding="utf-8")
    if wave:
        (path / "roadmaps" / "wave-current.md").write_text("# SoT\n", encoding="utf-8")
    if marker:
        (path / hr.ROOT_MARKER).touch()
    _git(path, "init", "-q")
    _git(path, "config", "user.name", "test")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "commit.gpgsign", "false")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "init")
    _git(path, "remote", "add", "origin", origin)
    return path


def test_discovery_adopts_a_pinned_checkout_carrying_the_marker(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_ROOT", raising=False)
    checkout = _repo(tmp_path / "checkout", ORIGIN)
    config = {"harness_root": {"git_guard": PIN}}
    assert hr.resolve(config, cwd=checkout).root == checkout
    monkeypatch.chdir(checkout)
    assert hr.resolve(config).root == checkout


def test_discovery_keys_on_the_marker_not_on_wave_current_alone(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_ROOT", raising=False)
    checkout = _repo(tmp_path / "checkout", ORIGIN, marker=False, wave=True)
    with pytest.raises(hr.HarnessRootAbsent):
        hr.resolve({"harness_root": {"git_guard": PIN}}, cwd=checkout)


def test_discovery_does_not_need_wave_current_when_the_marker_is_present(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_ROOT", raising=False)
    checkout = _repo(tmp_path / "checkout", ORIGIN, marker=True, wave=False)
    assert hr.resolve({"harness_root": {"git_guard": PIN}}, cwd=checkout).root == checkout


def test_same_repo_keys_on_the_pin_only(tmp_path, monkeypatch):
    """A clone under HOME with the same origin
    gives no reference: with no pin the checkout is refused; with a pin that
    names a different repository it is refused too."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("HARNESS_ROOT", raising=False)
    _repo(home / "config-clone", ORIGIN)
    checkout = _repo(tmp_path / "checkout", ORIGIN)
    assert hr._same_repo_as_configured(str(checkout), {}) is False
    assert hr._same_repo_as_configured(
        str(checkout), {"harness_root": {"git_guard": "github.com/example/other"}}
    ) is False
    assert hr._same_repo_as_configured(
        str(checkout), {"harness_root": {"git_guard": PIN}}
    ) is True
    assert hr._same_repo_as_configured(str(checkout), {"git_guard": [PIN]}) is True
    with pytest.raises(hr.HarnessRootAbsent):
        hr.resolve({}, cwd=checkout)


def test_discovery_adopts_a_pinned_worktree(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_ROOT", raising=False)
    primary = _repo(tmp_path / "primary", ORIGIN)
    worktree = tmp_path / "worktree"
    _git(primary, "worktree", "add", "--detach", str(worktree))
    independent = _git(worktree, "rev-parse", "--show-toplevel").stdout.strip()
    paths = hr.resolve({"harness_root": {"git_guard": PIN}}, cwd=worktree)
    assert paths.root == Path(independent)


def test_memory_index_uses_main_checkout_for_a_linked_worktree(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("HARNESS_ROOT", raising=False)
    primary = _repo(tmp_path / "primary", ORIGIN)
    checkout = tmp_path / "config-worktree"
    _git(primary, "worktree", "add", "--detach", str(checkout))

    paths = hr.resolve({"harness_root": {"git_guard": PIN}}, cwd=checkout)

    primary_slug = str(primary.absolute()).replace("/", "-")
    assert paths.memory_index == (
        home / ".claude" / "projects" / primary_slug / "memory" / "MEMORY.md"
    )


def test_memory_index_uses_plain_checkout_itself(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("HARNESS_ROOT", raising=False)
    primary = _repo(tmp_path / "primary", ORIGIN)

    paths = hr.resolve({"harness_root": {"git_guard": PIN}}, cwd=primary)

    primary_slug = str(primary.absolute()).replace("/", "-")
    assert paths.memory_index == (
        home / ".claude" / "projects" / primary_slug / "memory" / "MEMORY.md"
    )


def test_memory_index_falls_back_to_first_porcelain_worktree(monkeypatch, tmp_path):
    home = tmp_path / "home"
    root = tmp_path / "linked"
    main = tmp_path / "main"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("HARNESS_ROOT", str(root))

    def fake_run(args, **kwargs):
        if "--git-common-dir" in args:
            return subprocess.CompletedProcess(args, 1, "", "failed")
        return subprocess.CompletedProcess(
            args, 0, f"worktree {main}\nHEAD abc123\n\nworktree {root}\n", ""
        )

    monkeypatch.setattr(hr.subprocess, "run", fake_run)

    paths = hr.resolve({})

    main_slug = str(main.absolute()).replace("/", "-")
    assert paths.memory_index == (
        home / ".claude" / "projects" / main_slug / "memory" / "MEMORY.md"
    )


def test_memory_index_falls_back_to_root_when_git_lookups_fail(monkeypatch, tmp_path):
    home = tmp_path / "home"
    root = tmp_path / "not-a-checkout"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("HARNESS_ROOT", str(root))

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 1, "", "failed")

    monkeypatch.setattr(hr.subprocess, "run", fake_run)

    paths = hr.resolve({})

    root_slug = str(root.absolute()).replace("/", "-")
    assert paths.memory_index == (
        home / ".claude" / "projects" / root_slug / "memory" / "MEMORY.md"
    )


def test_memory_index_slug_uses_stubbed_main_checkout_path(monkeypatch):
    monkeypatch.setenv("HOME", "/tmp/home")
    monkeypatch.setenv("HARNESS_ROOT", "/example/work/roll-dm/close-dm")

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, "/example/code/config/.git\n", "")

    monkeypatch.setattr(hr.subprocess, "run", fake_run)

    paths = hr.resolve({})

    assert paths.memory_index == Path("/tmp/home") / (
        ".claude/projects/-example-code-config/memory/MEMORY.md"
    )


def test_a_foreign_origin_checkout_is_not_adopted(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_ROOT", raising=False)
    foreign = _repo(tmp_path / "other", "git@github.com:example/other-repo.git")
    with pytest.raises(hr.HarnessRootAbsent):
        hr.resolve({"harness_root": {"git_guard": PIN}}, cwd=foreign)
