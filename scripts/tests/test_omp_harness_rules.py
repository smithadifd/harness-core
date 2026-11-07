"""omp-harness-rules.py, exercised against temporary folders and a stub omp."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "omp-harness-rules.py"

SHARED_TEXT = "# Harness agreements — every session, every machine\n\n- shared rule\n"
LAB_TEXT = "# Harness agreements — lab\n\n- lab rule\n"
EXTRA_TEXT = "# Harness agreements — extra\n\n- extra rule\n"


def run(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, env=env, timeout=60,
    )


@pytest.fixture
def source(tmp_path: Path) -> Path:
    src = tmp_path / "rules"
    src.mkdir()
    (src / "harness.md").write_text(SHARED_TEXT, encoding="utf-8")
    (src / "harness-lab.md").write_text(LAB_TEXT, encoding="utf-8")
    return src


@pytest.fixture
def target(tmp_path: Path) -> Path:
    return tmp_path / "omp" / "agent" / "rules"


def install(source: Path, target: Path) -> subprocess.CompletedProcess:
    return run("--source", str(source), "--target", str(target))


def test_writes_shared_rules_and_an_arbitrary_overlay(source, target):
    result = install(source, target)
    assert result.returncode == 0, result.stderr
    for name, body in (("harness.md", SHARED_TEXT), ("harness-lab.md", LAB_TEXT)):
        text = (target / name).read_text(encoding="utf-8")
        head, _, rest = text.partition("\n---\n")
        assert head.startswith("---\n")
        assert "\nalwaysApply: true" in head
        assert "omp-harness-rules.py" in head
        assert rest == body
        assert not (target / name).is_symlink()
    assert not (target / "harness-extra.md").exists()


def test_second_run_changes_nothing(source, target):
    assert install(source, target).returncode == 0
    before = {p.name: p.stat().st_mtime_ns for p in target.iterdir()}
    result = install(source, target)
    assert result.returncode == 0
    assert result.stdout.count("unchanged:") == 2
    assert {p.name: p.stat().st_mtime_ns for p in target.iterdir()} == before


def test_updates_its_own_copy_when_the_source_changes(source, target):
    assert install(source, target).returncode == 0
    (source / "harness.md").write_text(SHARED_TEXT + "- new rule\n", encoding="utf-8")
    result = install(source, target)
    assert result.returncode == 0
    assert "update:" in result.stdout
    assert (target / "harness.md").read_text(encoding="utf-8").endswith("- new rule\n")


def test_refuses_to_overwrite_a_file_it_did_not_write(source, target):
    target.mkdir(parents=True)
    (target / "harness.md").write_text("someone else's rules\n", encoding="utf-8")
    result = install(source, target)
    assert result.returncode == 1
    assert "not written by this script" in result.stderr
    assert (target / "harness.md").read_text(encoding="utf-8") == "someone else's rules\n"
    assert not (target / "harness-lab.md").exists()


def test_refuses_a_symlinked_target_file(source, target, tmp_path):
    target.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere.md"
    elsewhere.write_text("x\n", encoding="utf-8")
    (target / "harness.md").symlink_to(elsewhere)
    result = install(source, target)
    assert result.returncode == 1
    assert "is a symlink; OMP rules" in result.stderr
    assert elsewhere.read_text(encoding="utf-8") == "x\n"


def test_refuses_a_symlink_even_to_a_file_it_wrote(source, target, tmp_path):
    other = tmp_path / "other-rules"
    assert install(source, other).returncode == 0
    target.mkdir(parents=True)
    (target / "harness.md").symlink_to(other / "harness.md")
    before = (other / "harness.md").read_bytes()
    result = install(source, target)
    assert result.returncode == 1
    assert "is a symlink; OMP rules" in result.stderr
    assert (other / "harness.md").read_bytes() == before


@pytest.mark.parametrize(
    "content",
    [
        b"---\ndescription: mine\nalwaysApply: true\n---\n# My rules\n",
        b"---\ndescription: mine\n---\nby claude-toolkit scripts/omp-harness-rules.py\n",
        b"by claude-toolkit scripts/omp-harness-rules.py\n",
        b"notes\nby claude-toolkit scripts/omp-harness-rules.py\n---\nbody\n",
        b"---\ndescription: by claude-toolkit scripts/omp-harness-rules.py \xff\n---\n",
    ],
    ids=["frontmatter-without-marker", "marker-in-body", "marker-without-frontmatter", "marker-before-a-rule-line", "not-utf8"],
)
def test_refuses_foreign_files_that_look_close_to_its_own(source, target, content):
    target.mkdir(parents=True)
    (target / "harness.md").write_bytes(content)
    result = install(source, target)
    assert result.returncode == 1
    assert "was not written by this script" in result.stderr
    assert (target / "harness.md").read_bytes() == content
    assert sorted(p.name for p in target.iterdir()) == ["harness.md"]


def test_refuses_a_directory_where_a_rules_file_goes(source, target):
    (target / "harness.md").mkdir(parents=True)
    result = install(source, target)
    assert result.returncode == 1
    assert "is not a regular file" in result.stderr
    assert sorted(p.name for p in target.iterdir()) == ["harness.md"]


def test_target_that_is_a_file_cannot_be_used(source, tmp_path):
    not_a_dir = tmp_path / "rules-file"
    not_a_dir.write_text("x\n", encoding="utf-8")
    for extra in ([], ["--check"]):
        result = run("--source", str(source), "--target", str(not_a_dir), *extra)
        assert result.returncode == 2
        assert "is not a folder" in result.stderr
    assert not_a_dir.read_text(encoding="utf-8") == "x\n"


def test_a_target_that_cannot_be_created_cannot_be_used(source, tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("x\n", encoding="utf-8")
    result = run("--source", str(source), "--target", str(blocker / "rules"))
    assert result.returncode == 2
    assert "cannot create" in result.stderr
    assert blocker.read_text(encoding="utf-8") == "x\n"


def test_refuses_a_source_that_already_has_frontmatter(source, target):
    (source / "harness.md").write_text("---\nx: 1\n---\n" + SHARED_TEXT, encoding="utf-8")
    result = install(source, target)
    assert result.returncode == 2
    assert "already starts with frontmatter" in result.stderr


def test_refuses_when_another_overlay_is_in_omp(source, target):
    target.mkdir(parents=True)
    (target / "harness-extra.md").write_text("extra\n", encoding="utf-8")
    result = install(source, target)
    assert result.returncode == 1
    assert "harness-extra.md" in result.stderr
    assert not (target / "harness.md").exists()


def test_refuses_a_source_with_two_overlays(source, target):
    (source / "harness-extra.md").write_text(EXTRA_TEXT, encoding="utf-8")
    result = install(source, target)
    assert result.returncode == 2
    assert "exactly one" in result.stderr
    assert not target.exists()


def test_refuses_a_source_with_no_overlay(source, target):
    (source / "harness-lab.md").unlink()
    result = install(source, target)
    assert result.returncode == 2
    assert not target.exists()


def test_refuses_a_source_without_harness_md(source, target):
    (source / "harness.md").unlink()
    result = install(source, target)
    assert result.returncode == 2
    assert "harness.md" in result.stderr


def test_another_arbitrary_overlay_is_copied(source, target):
    (source / "harness-lab.md").unlink()
    (source / "harness-extra.md").write_text(EXTRA_TEXT, encoding="utf-8")
    assert install(source, target).returncode == 0
    assert (target / "harness-extra.md").exists()
    assert not (target / "harness-lab.md").exists()


def test_check_reports_without_writing(source, target):
    result = run("--source", str(source), "--target", str(target), "--check")
    assert result.returncode == 1
    assert "missing" in result.stdout
    assert not target.exists()
    assert install(source, target).returncode == 0
    assert run("--source", str(source), "--target", str(target), "--check").returncode == 0
    (source / "harness-lab.md").write_text(LAB_TEXT + "- changed\n", encoding="utf-8")
    result = run("--source", str(source), "--target", str(target), "--check")
    assert result.returncode == 1
    assert "out of date" in result.stdout
    assert not (target / "harness-lab.md").read_text(encoding="utf-8").endswith("- changed\n")


def stub_omp(tmp_path: Path, body: str) -> dict:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "omp"
    stub.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    stub.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    return env


def test_default_target_comes_from_omp_config_path(source, tmp_path):
    agent = tmp_path / "profile" / "agent"
    env = stub_omp(tmp_path, f'[ "$1 $2" = "config path" ] && echo "{agent}" && exit 0\nexit 9\n')
    result = run("--source", str(source), env=env)
    assert result.returncode == 0, result.stderr
    assert (agent / "rules" / "harness.md").exists()


def test_probe_passes_when_omp_quotes_both_headings(source, tmp_path):
    log = tmp_path / "args.txt"
    env = stub_omp(
        tmp_path,
        f'printf "%s\\n" "$@" > "{log}"\n'
        "echo '# Harness agreements — every session, every machine'\n"
        "echo '# Harness agreements — lab'\n",
    )
    result = run("--source", str(source), "--probe", "--model", "some/model", env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("PASS") == 2
    args = log.read_text(encoding="utf-8").splitlines()
    assert "--no-tools" in args and "-p" in args
    assert args[args.index("--model") + 1] == "some/model"


def test_probe_reports_a_gap_when_a_heading_is_missing(source, tmp_path):
    env = stub_omp(tmp_path, "echo '# Harness agreements — every session, every machine'\n")
    result = run("--source", str(source), "--probe", env=env)
    assert result.returncode == 1
    assert "GAP" in result.stdout and "lab" in result.stdout


def test_probe_needs_whole_lines_not_substrings(source, tmp_path):
    env = stub_omp(
        tmp_path,
        "echo 'I see # Harness agreements — every session, every machine and more'\n"
        "echo '# Harness agreements — lab (copied)'\n",
    )
    result = run("--source", str(source), "--probe", env=env)
    assert result.returncode == 1
    assert result.stdout.count("GAP") == 2


def test_probe_cannot_verify_when_omp_fails(source, tmp_path):
    env = stub_omp(tmp_path, "echo boom >&2\nexit 3\n")
    result = run("--source", str(source), "--probe", env=env)
    assert result.returncode == 2
    assert "could not verify" in result.stderr


def test_probe_cannot_verify_without_omp(source, tmp_path):
    env = dict(os.environ)
    env["PATH"] = str(tmp_path / "empty-bin")
    result = run("--source", str(source), "--probe", env=env)
    assert result.returncode == 2


def load_module():
    spec = importlib.util.spec_from_file_location("omp_harness_rules", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_failure_while_staging_changes_nothing(source, target, monkeypatch):
    module = load_module()
    assert module.main(["--source", str(source), "--target", str(target)]) == 0
    (source / "harness.md").write_text(SHARED_TEXT + "- one\n", encoding="utf-8")
    (source / "harness-lab.md").write_text(LAB_TEXT + "- two\n", encoding="utf-8")
    before = {p.name: p.read_bytes() for p in target.iterdir()}
    real_mkstemp = module.tempfile.mkstemp
    calls = []

    def failing_mkstemp(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise OSError(28, "No space left on device")
        return real_mkstemp(*args, **kwargs)

    monkeypatch.setattr(module.tempfile, "mkstemp", failing_mkstemp)
    assert module.main(["--source", str(source), "--target", str(target)]) == 2
    assert {p.name: p.read_bytes() for p in target.iterdir()} == before


def test_a_failed_link_leaves_nothing_behind(source, target, monkeypatch):
    module = load_module()
    real_link = module.os.link
    calls = []

    def failing_link(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise OSError(13, "Permission denied")
        return real_link(*args, **kwargs)

    monkeypatch.setattr(module.os, "link", failing_link)
    assert module.main(["--source", str(source), "--target", str(target)]) == 2
    assert list(target.iterdir()) == []


def test_a_failed_second_replace_rolls_back_the_first(source, target, monkeypatch):
    module = load_module()
    assert module.main(["--source", str(source), "--target", str(target)]) == 0
    before = {p.name: p.read_bytes() for p in target.iterdir()}
    (source / "harness.md").write_text(SHARED_TEXT + "- one\n", encoding="utf-8")
    (source / "harness-lab.md").write_text(LAB_TEXT + "- two\n", encoding="utf-8")
    real_replace = module.os.replace
    calls = []

    def failing_replace(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise OSError(28, "No space left on device")
        return real_replace(*args, **kwargs)

    monkeypatch.setattr(module.os, "replace", failing_replace)
    assert module.main(["--source", str(source), "--target", str(target)]) == 2
    assert len(calls) == 2
    assert {p.name: p.read_bytes() for p in target.iterdir()} == before


def test_a_file_that_appears_after_planning_is_not_replaced(source, target, monkeypatch):
    module = load_module()
    real_plan = module.plan

    def plan_then_intrude(*args, **kwargs):
        actions = real_plan(*args, **kwargs)
        (target / "harness-lab.md").write_text("someone else's\n", encoding="utf-8")
        return actions

    monkeypatch.setattr(module, "plan", plan_then_intrude)
    assert module.main(["--source", str(source), "--target", str(target)]) == 1
    assert (target / "harness-lab.md").read_text(encoding="utf-8") == "someone else's\n"
    assert sorted(p.name for p in target.iterdir()) == ["harness-lab.md"]


def test_an_owned_file_swapped_after_planning_is_not_replaced(source, target, monkeypatch):
    module = load_module()
    assert module.main(["--source", str(source), "--target", str(target)]) == 0
    (source / "harness.md").write_text(SHARED_TEXT + "- one\n", encoding="utf-8")
    real_plan = module.plan

    def plan_then_swap(*args, **kwargs):
        actions = real_plan(*args, **kwargs)
        (target / "harness.md").write_text("someone else's\n", encoding="utf-8")
        return actions

    monkeypatch.setattr(module, "plan", plan_then_swap)
    assert module.main(["--source", str(source), "--target", str(target)]) == 1
    assert (target / "harness.md").read_text(encoding="utf-8") == "someone else's\n"


def test_refuses_a_dangling_symlink_for_another_overlay(source, target, tmp_path):
    target.mkdir(parents=True)
    (target / "harness-extra.md").symlink_to(tmp_path / "missing.md")
    result = install(source, target)
    assert result.returncode == 1
    assert "harness-extra.md is present" in result.stderr
    assert not (target / "harness.md").exists()


def test_a_source_that_is_not_utf8_cannot_be_used(source, target):
    (source / "harness-lab.md").write_bytes(b"# Harness agreements \xff\n")
    result = install(source, target)
    assert result.returncode == 2
    assert "is not UTF-8" in result.stderr


def test_probe_refuses_files_that_share_a_heading(source, tmp_path):
    (source / "harness-lab.md").write_text(SHARED_TEXT, encoding="utf-8")
    env = stub_omp(tmp_path, "echo '# Harness agreements — every session, every machine'\n")
    result = run("--source", str(source), "--probe", env=env)
    assert result.returncode == 2
    assert "share a heading" in result.stderr


def test_rollback_leaves_a_file_replaced_after_placement_alone(source, target, monkeypatch):
    module = load_module()
    real_link = module.os.link
    calls = []

    def link_then_intrude(src, dst, *args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            first = target / "harness.md"
            first.unlink()
            first.write_text("someone else's\n", encoding="utf-8")
            raise OSError(13, "Permission denied")
        return real_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(module.os, "link", link_then_intrude)
    assert module.main(["--source", str(source), "--target", str(target)]) == 2
    assert (target / "harness.md").read_text(encoding="utf-8") == "someone else's\n"
    assert sorted(p.name for p in target.iterdir()) == ["harness.md"]


def test_a_staging_file_that_cannot_be_removed_does_not_undo_a_good_install(source, target, monkeypatch):
    module = load_module()
    real_unlink = module.Path.unlink

    def failing_unlink(self, *args, **kwargs):
        if self.name.startswith(".harness"):
            raise OSError(1, "Operation not permitted")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(module.Path, "unlink", failing_unlink)
    assert module.main(["--source", str(source), "--target", str(target)]) == 0
    assert (target / "harness.md").exists() and (target / "harness-lab.md").exists()


def test_probe_removes_its_working_folder(source, tmp_path):
    record = tmp_path / "cwd.txt"
    env = stub_omp(
        tmp_path,
        f'pwd > "{record}"\n'
        "echo '# Harness agreements — every session, every machine'\n"
        "echo '# Harness agreements — lab'\n",
    )
    assert run("--source", str(source), "--probe", env=env).returncode == 0
    used = Path(record.read_text(encoding="utf-8").strip())
    assert "omp-rules-probe-" in used.name
    assert not used.exists()


# --- --check names the file that drifted ------------------------------------


@pytest.mark.parametrize("drifted", ["harness.md", "harness-lab.md"])
def test_check_names_only_the_file_that_drifted(source, target, drifted):
    assert install(source, target).returncode == 0
    (source / drifted).write_text((source / drifted).read_text(encoding="utf-8") + "- edited\n", encoding="utf-8")
    result = run("--source", str(source), "--target", str(target), "--check")
    assert result.returncode == 1
    states = dict(line.rsplit(": ", 1) for line in result.stdout.splitlines())
    assert states == {
        str(target / name): ("out of date" if name == drifted else "up to date")
        for name in ("harness.md", "harness-lab.md")
    }


def test_check_names_a_missing_file(source, target):
    assert install(source, target).returncode == 0
    (target / "harness-lab.md").unlink()
    result = run("--source", str(source), "--target", str(target), "--check")
    assert result.returncode == 1
    states = dict(line.rsplit(": ", 1) for line in result.stdout.splitlines())
    assert states == {str(target / "harness.md"): "up to date", str(target / "harness-lab.md"): "missing"}


def test_the_rules_folder_is_named_in_the_heading_the_refusal_and_the_help(source, target):
    rules_home = "~" + "/.claude/rules"
    assert install(source, target).returncode == 0
    head = (target / "harness.md").read_text(encoding="utf-8").partition("\n---\n")[0]
    assert f"(copied from {rules_home}/harness.md by claude-toolkit" in head
    (source / "harness.md").unlink()
    result = install(source, target)
    assert result.returncode == 2
    assert f"copy the rules into {rules_home}/ first" in result.stderr
    usage = run("--help")
    assert usage.returncode == 0
    assert f"(default {rules_home})" in " ".join(usage.stdout.split())
