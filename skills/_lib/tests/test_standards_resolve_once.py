"""The ordered standards search.

Its repository-wide consumption contract (every standard the skills
declare resolves once; no live file names a literal standards path)
is in test_standards_resolve_once_toolkit.py.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import harness_root as hr  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_standard_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HARNESS_ROOT", raising=False)
    monkeypatch.delenv("HARNESS_CORE_STANDARDS", raising=False)


def _resolver_tree(tmp_path: Path, *, override: object = None) -> tuple[hr.HarnessPaths, Path, Path]:
    root = tmp_path / "instance"
    instance = root / "standards"
    core = tmp_path / "core-standards"
    instance.mkdir(parents=True)
    core.mkdir()
    harness_config: dict[str, object] = {
        "root": str(root),
        "core_standards": str(core),
    }
    if override is not None:
        harness_config["standards_override"] = override
    return hr.resolve({"harness_root": harness_config}), instance, core


def test_only_instance_copy_resolves_to_instance(tmp_path: Path) -> None:
    paths, instance, _ = _resolver_tree(tmp_path)
    standard = instance / "delegation-loop.md"
    standard.write_text("instance\n", encoding="utf-8")

    assert hr.resolve_standard(paths, standard.name) == standard


def test_only_core_copy_resolves_to_core(tmp_path: Path) -> None:
    paths, _, core = _resolver_tree(tmp_path)
    standard = core / "delegation-loop.md"
    standard.write_text("core\n", encoding="utf-8")

    assert hr.resolve_standard(paths, standard.name) == standard


def test_both_places_refuse_without_declared_override(tmp_path: Path) -> None:
    paths, instance, core = _resolver_tree(tmp_path)
    name = "delegation-loop.md"
    (instance / name).write_text("instance\n", encoding="utf-8")
    (core / name).write_text("core\n", encoding="utf-8")

    try:
        hr.resolve_standard(paths, name)
    except hr.StandardAmbiguous as exc:
        message = str(exc)
        assert name in message
        assert str(instance / name) in message
        assert str(core / name) in message
    else:
        raise AssertionError("both-place standard must refuse without standards_override")


def test_both_places_with_override_selects_instance_first(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    name = "delegation-loop.md"
    paths, instance, core = _resolver_tree(tmp_path, override=[name])
    (instance / name).write_text("instance\n", encoding="utf-8")
    (core / name).write_text("core\n", encoding="utf-8")

    resolved = hr.resolve_standard(paths, name)

    assert resolved == instance / name, "declared override must select instance before core"
    notice = capsys.readouterr().err
    assert str(instance / name) in notice
    assert str(core / name) in notice


def test_neither_place_refuses_and_names_both_paths(tmp_path: Path) -> None:
    paths, instance, core = _resolver_tree(tmp_path)
    name = "missing.md"

    try:
        hr.resolve_standard(paths, name)
    except hr.StandardAbsent as exc:
        message = str(exc)
        assert name in message
        assert str(instance / name) in message
        assert str(core / name) in message
    else:
        raise AssertionError("neither-place standard must refuse instead of returning a path")


def test_malformed_standards_override_refuses_resolution(tmp_path: Path) -> None:
    paths, instance, _ = _resolver_tree(tmp_path, override="delegation-loop.md")
    name = "delegation-loop.md"
    (instance / name).write_text("instance\n", encoding="utf-8")

    with pytest.raises(hr.StandardConfigInvalid, match="standards_override"):
        hr.resolve_standard(paths, name)


def test_unreadable_config_refuses_resolution_instead_of_silently_picking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "instance"
    standard = root / "standards" / "delegation-loop.md"
    standard.parent.mkdir(parents=True)
    standard.write_text("instance\n", encoding="utf-8")
    monkeypatch.setenv("HARNESS_ROOT", str(root))
    paths = hr.resolve(tmp_path / "missing-config.json")

    with pytest.raises(hr.StandardConfigInvalid, match="config unreadable"):
        hr.resolve_standard(paths, standard.name)


def test_parent_relative_name_refuses_even_when_escaped_file_exists(tmp_path: Path) -> None:
    paths, instance, _ = _resolver_tree(tmp_path)
    escaped = instance.parent / "outside.md"
    escaped.write_text("secret\n", encoding="utf-8")

    with pytest.raises(hr.StandardNameInvalid, match="bare filename"):
        hr.resolve_standard(paths, "../outside.md")


def test_absolute_name_refuses(tmp_path: Path) -> None:
    paths, instance, _ = _resolver_tree(tmp_path)
    target = tmp_path / "elsewhere.md"
    target.write_text("secret\n", encoding="utf-8")

    with pytest.raises(hr.StandardNameInvalid, match="bare filename"):
        hr.resolve_standard(paths, str(target.resolve()))


def test_nested_dotdot_name_refuses(tmp_path: Path) -> None:
    paths, instance, _ = _resolver_tree(tmp_path)
    (instance / "x.md").write_text("ok\n", encoding="utf-8")
    (instance / "sub").mkdir()

    with pytest.raises(hr.StandardNameInvalid, match="bare filename"):
        hr.resolve_standard(paths, "sub/../x.md")


def test_dotdot_name_refuses(tmp_path: Path) -> None:
    paths, _, _ = _resolver_tree(tmp_path)

    with pytest.raises(hr.StandardNameInvalid, match="bare filename"):
        hr.resolve_standard(paths, "..")


def test_empty_name_refuses(tmp_path: Path) -> None:
    paths, _, _ = _resolver_tree(tmp_path)

    with pytest.raises(hr.StandardNameInvalid, match="bare filename"):
        hr.resolve_standard(paths, "")


def test_nul_name_refuses(tmp_path: Path) -> None:
    paths, _, _ = _resolver_tree(tmp_path)

    with pytest.raises(hr.StandardNameInvalid, match="bare filename"):
        hr.resolve_standard(paths, "security.md\0x")


def test_bare_filename_still_resolves(tmp_path: Path) -> None:
    paths, instance, _ = _resolver_tree(tmp_path)
    standard = instance / "security.md"
    standard.write_text("ok\n", encoding="utf-8")

    assert hr.resolve_standard(paths, "security.md") == standard
    assert hr.resolve_standard(paths, "security.md").is_file()
