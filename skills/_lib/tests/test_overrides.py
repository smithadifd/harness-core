"""Behavioral tests for the shared read-time JSON overrides loader."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parents[1]
OVERRIDES_PATH = LIB_DIR / "overrides.py"
POOL_CONFIG_PATH = LIB_DIR / "pool_config.py"


def _env(home: Path, *, xdg: Path | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env["HOME"] = str(home)
    if xdg is None:
        env.pop("XDG_CONFIG_HOME", None)
    else:
        env["XDG_CONFIG_HOME"] = str(xdg)
    return env


def _run_pool(
    home: Path, *, xdg: Path | None = None, env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(POOL_CONFIG_PATH), "--work-kind", "mechanical_build"],
        capture_output=True,
        text=True,
        env=env if env is not None else _env(home, xdg=xdg),
        cwd=cwd,
        timeout=30,
    )


def _run_skill_loader(home: Path, name: str, defaults: dict) -> subprocess.CompletedProcess[str]:
    code = (
        "import importlib.util,json,sys;"
        "spec=importlib.util.spec_from_file_location('overrides_runtime',sys.argv[1]);"
        "module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);"
        "print(json.dumps(module.load_skill_overrides(sys.argv[2],json.loads(sys.argv[3])),sort_keys=True))"
    )
    return subprocess.run(
        [sys.executable, "-c", code, str(OVERRIDES_PATH), name, json.dumps(defaults)],
        capture_output=True,
        text=True,
        env=_env(home),
        timeout=30,
    )


def test_missing_well_known_pool_override_uses_shipped_default(tmp_path):
    result = _run_pool(tmp_path / "home")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == "xai-oauth/grok-4.7", (
        "an absent optional override must leave the shipped model order unchanged"
    )


def test_well_known_pool_override_is_read_at_run_time(tmp_path):
    home = tmp_path / "home"
    before = _run_pool(home)
    config_dir = home / ".config" / "claude-toolkit"
    config_dir.mkdir(parents=True)
    (config_dir / "pool-config.json").write_text(
        json.dumps({"disabled_pools": ["xai-grok"]}), encoding="utf-8"
    )
    after = _run_pool(home)

    assert before.returncode == after.returncode == 0, before.stderr + after.stderr
    assert before.stdout.splitlines()[0] == "xai-oauth/grok-4.7"
    assert after.stdout.splitlines()[0] == "openai-codex/gpt-6.1-sol", (
        "the override created between runs was not read from the current HOME"
    )


def test_xdg_pool_override_takes_precedence_over_home_fallback(tmp_path):
    home = tmp_path / "home"
    xdg = tmp_path / "xdg"
    home_config = home / ".config" / "claude-toolkit"
    xdg_config = xdg / "claude-toolkit"
    home_config.mkdir(parents=True)
    xdg_config.mkdir(parents=True)
    (home_config / "pool-config.json").write_text(
        json.dumps({"disabled_pools": ["xai-grok"]}), encoding="utf-8"
    )
    (xdg_config / "pool-config.json").write_text(
        json.dumps({"disabled_pools": ["google-antigravity-gemini"]}), encoding="utf-8"
    )

    result = _run_pool(home, xdg=xdg)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == "xai-oauth/grok-4.7", (
        "the HOME fallback won even though XDG_CONFIG_HOME was set"
    )


def test_relative_xdg_config_home_is_ignored_from_any_directory(tmp_path):
    home = tmp_path / "home"
    config_dir = home / ".config" / "claude-toolkit"
    config_dir.mkdir(parents=True)
    (config_dir / "pool-config.json").write_text(
        json.dumps({"disabled_pools": ["xai-grok"]}), encoding="utf-8"
    )
    first_cwd = tmp_path / "cwd-a"
    second_cwd = tmp_path / "cwd-b"
    first_cwd.mkdir()
    second_cwd.mkdir()
    env = _env(home)
    env["XDG_CONFIG_HOME"] = "relative-config"

    first = _run_pool(home, env=env, cwd=first_cwd)
    second = _run_pool(home, env=env, cwd=second_cwd)

    assert first.returncode == second.returncode == 0, first.stderr + second.stderr
    assert first.stdout.splitlines()[0] == second.stdout.splitlines()[0] == (
        "openai-codex/gpt-6.1-sol"
    ), "a relative XDG_CONFIG_HOME was not ignored in favour of the HOME fallback"


def test_nested_skill_override_keeps_default_sibling(tmp_path):
    home = tmp_path / "home"
    path = home / ".claude" / "skill-config" / "example.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"paths": {"primary": "custom"}}), encoding="utf-8")

    result = _run_skill_loader(
        home, "example", {"paths": {"primary": "default", "secondary": "kept"}}
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "paths": {"primary": "custom", "secondary": "kept"}
    }, "overriding one nested key discarded its shipped sibling"


def test_malformed_well_known_override_fails_loudly(tmp_path):
    home = tmp_path / "home"
    path = home / ".config" / "claude-toolkit" / "pool-config.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"disabled_pools": [', encoding="utf-8")

    result = _run_pool(home)
    assert result.returncode == 2
    assert str(path) in result.stderr and "not valid JSON" in result.stderr, (
        "a malformed override did not fail with a named path and parse error"
    )
    assert result.stdout == ""
