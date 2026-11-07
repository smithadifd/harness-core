"""Read optional JSON overrides from stable user configuration locations.

Toolkit-wide overrides live under
``${XDG_CONFIG_HOME:-$HOME/.config}/claude-toolkit/``.  The pool inventory is
the reference consumer: ``pool_config.py`` reads ``pool-config.json`` there at
run time, then applies its existing entry-by-id merge rules.

Per-skill overrides retain their established location under
``$HOME/.claude/skill-config/<name>.json``.  Skills should call
:func:`load_skill_overrides` when they can depend on this library; pinned or
external skills that cannot do so may keep reading the same JSON object
without changing its format.

Both forms are sparse JSON objects: each key is one setting, an absent key
keeps the shipped default, and nested objects merge recursively so changing
one nested key does not discard its default siblings.  A missing optional
file means no override.  An unreadable, malformed, or non-object file raises
:class:`OverrideError` and names the path; bad input never silently falls back.
Paths are derived on every call, so a process observes the current HOME or
XDG_CONFIG_HOME rather than an import-time value.  A relative XDG_CONFIG_HOME is
ignored, as the XDG specification requires.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

TOOLKIT_CONFIG_DIRNAME = "claude-toolkit"
POOL_CONFIG_FILENAME = "pool-config.json"
MODEL_REGISTRY_FILENAME = "model-registry.json"
SKILL_CONFIG_PARTS = (".claude", "skill-config")


class OverrideError(ValueError):
    """An override file could not be read as a JSON object."""


def _home() -> Path:
    value = os.environ.get("HOME")
    return Path(value).expanduser() if value else Path.home()


def toolkit_config_dir() -> Path:
    """Return the toolkit configuration directory for the current process."""
    xdg_home = os.environ.get("XDG_CONFIG_HOME")
    # A relative XDG_CONFIG_HOME is invalid and would follow the working
    # directory, so it is ignored and the HOME fallback is used.
    if xdg_home and Path(xdg_home).is_absolute():
        base = Path(xdg_home)
    else:
        base = _home() / ".config"
    return base / TOOLKIT_CONFIG_DIRNAME


def toolkit_override_path(name: str) -> Path:
    """Return one toolkit-wide JSON override path."""
    return toolkit_config_dir() / _json_filename(name)


def pool_override_path() -> Path:
    """Return the pool inventory override path for the current process."""
    return toolkit_override_path(POOL_CONFIG_FILENAME)


def model_registry_override_path() -> Path:
    """Return the per-person model registry overlay path for the current process."""
    return toolkit_override_path(MODEL_REGISTRY_FILENAME)


def skill_override_path(name: str) -> Path:
    """Return one established per-skill JSON override path."""
    return _home().joinpath(*SKILL_CONFIG_PARTS, _json_filename(name))


def _json_filename(name: str) -> str:
    path = Path(name)
    if not name or path.name != name or name in {".", ".."}:
        raise ValueError(f"override name must be one file name, got {name!r}")
    return name if name.endswith(".json") else f"{name}.json"


def read_json_object(path: str | Path, *, missing_ok: bool = False) -> dict[str, Any] | None:
    """Read one JSON object, optionally treating an absent file as no override."""
    resolved = Path(path)
    try:
        text = resolved.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        if missing_ok:
            return None
        raise OverrideError(
            f"could not read the configuration file {resolved} ({exc})"
        ) from exc
    except OSError as exc:
        raise OverrideError(
            f"could not read the configuration file {resolved} ({exc})"
        ) from exc
    except UnicodeError as exc:
        raise OverrideError(f"{resolved} is not valid UTF-8 ({exc})") from exc
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise OverrideError(f"{resolved} is not valid JSON ({exc})") from exc
    if not isinstance(document, dict):
        raise OverrideError(
            f"{resolved} did not parse to a JSON object "
            f"(got {type(document).__name__})"
        )
    return document


def deep_merge(defaults: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Return ``defaults`` with nested mapping keys replaced by ``override``."""
    merged = dict(defaults)
    for key, value in override.items():
        existing = merged.get(key)
        if isinstance(existing, Mapping) and isinstance(value, Mapping):
            merged[key] = deep_merge(existing, value)
        else:
            merged[key] = value
    return merged


def load_json_overrides(
    defaults: Mapping[str, Any], path: str | Path, *, missing_ok: bool = True
) -> dict[str, Any]:
    """Read and deep-merge one sparse JSON override document."""
    override = read_json_object(path, missing_ok=missing_ok)
    return deep_merge(defaults, override or {})


def load_toolkit_overrides(name: str, defaults: Mapping[str, Any]) -> dict[str, Any]:
    """Load one optional toolkit-wide override from the current config base."""
    return load_json_overrides(defaults, toolkit_override_path(name))


def load_skill_overrides(name: str, defaults: Mapping[str, Any]) -> dict[str, Any]:
    """Load one optional per-skill override from its established location."""
    return load_json_overrides(defaults, skill_override_path(name))
