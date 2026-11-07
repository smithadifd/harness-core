#!/usr/bin/env python3
"""config_path.py — shared single-key path resolver for STANDALONE-WITH-CONFIG
skills (CT4, `harness-reconstruction.md` § 3 R0 row 4, "Registry path key for
the STANDALONE-WITH-CONFIG skills").

WHY THIS EXISTS
----------------
`migrate-launchd-job`, `new-automation` and `topic` each carry exactly one
home-rooted literal that names a real, varying destination (the automations
registry file; the topics write directory) — never a normative standard
citation (those stay hardcoded prose in each skill, e.g. `model-routing.md`,
`security.md` § Live-automation retrofit rules, `topics/README.md`'s own
convention citation — out of this row's scope: the tier table's "one or two
literals" names the registry/write-dir class specifically, not every
citation a skill happens to make).

All three want the exact same shape: one bare key in the skill's own
`config.json`, `null`/absent means "use today's literal", present means "use
this instead", `~` expanded, never existence-checked. The sibling
`harness_root.py` (CS2, D1) is the wrong tool for this: it resolves
LOOP-BOUND skills onto a shared multi-subkey root, and the
STANDALONE-WITH-CONFIG tier is explicitly out of D1's scope (`harness-
reconstruction.md` § 1's own tier split — these skills stay standalone, they
do not gain a `harness_root` key). This module is the one small shared
implementation each skill's own resolver script (`migrate-launchd-job/
registry_path.py`, `new-automation/registry_path.py`, and `topic`'s
`topics_dir.py` until topic moved to the pinned skills upstream, whose copy
carries its own fallback) calls, so "read one key, default to a literal" is written once rather than
three times (a D-record in the CT4 PR body: a shared helper was the honest
call the moment a third near-identical copy would have been pasted).

CONTRACT
--------
`load_config(path)`: read a skill's `config.json`. A missing, unreadable, or
malformed file degrades to `{}` so a caller can still resolve the default
even when the file is absent — never raises on a caller-supplied path.
UNLIKE `skills/_lib/harness_root.py::_load_config` (which degrades
silently), every degrade path here prints ONE stderr line naming why (D6,
CT4 fix-forward review) — "degrade-and-report", not just "degrade": a
malformed config that silently resolves to every default looks, to an
agent, identical to a config that was never touched, which is exactly the
kind of silent-wrong-root class of bug `harness_root.py`'s own
`_env_root()` refuses to allow for `HARNESS_ROOT`. A genuinely-unexpected
parsed type (valid JSON, but not a JSON object — e.g. a bare list) also
degrades to `{}` the same way.

`resolve_configured_path(config, key, default)`:
  - `config.get(key)` is a non-empty string -> that value, `~`-expanded,
    returned UNCHANGED otherwise (an override always wins; never joined
    onto anything else — there is no subkey to join onto, unlike
    `harness_root.py`'s `resolve_relative`).
  - `config.get(key)` missing, `None`, empty, or present but NOT a string
    (a JSON array/object/bool/number — `Path(str(value))` would otherwise
    turn e.g. `true` into the bogus relative path `"True"`, D6) -> `default`,
    `~`-expanded. The wrong-type case prints one stderr line naming the
    rejected value before falling back, same posture as the config-file
    degrade above.
  - Never existence-checked; never raises on the value itself.

Python 3 stdlib only. No network, no subprocess, no third-party imports.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Mapping, Union


def load_config(config_path: Union[str, Path]) -> Mapping[str, Any]:
    """Degrade to `{}` on a missing, unreadable, or malformed config file —
    never raise on a caller-supplied path. Each degrade prints one stderr
    line naming why (D6) rather than failing silently."""
    path = Path(config_path)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        sys.stderr.write(
            f"config_path: could not read {path} ({exc}) — degrading to "
            "{}; every key falls back to its documented default\n"
        )
        return {}
    try:
        data = json.loads(text)
    except ValueError as exc:
        sys.stderr.write(
            f"config_path: {path} is not valid JSON ({exc}) — degrading to "
            "{}; every key falls back to its documented default\n"
        )
        return {}
    if not isinstance(data, Mapping):
        sys.stderr.write(
            f"config_path: {path} did not parse to a JSON object (got "
            f"{type(data).__name__}) — degrading to {{}}; every key falls "
            "back to its documented default\n"
        )
        return {}
    return data


def resolve_configured_path(config: Mapping[str, Any], key: str, default: str) -> Path:
    """The one seam this module exposes: `key` present in `config` as a
    non-empty STRING -> that value, expanded; `key` absent/`None`/empty/a
    non-string value (array, object, bool, number) -> `default`, expanded.
    A non-string value prints one stderr line naming the rejected value
    before falling back (D6). Never existence-checked."""
    value = config.get(key) if isinstance(config, Mapping) else None
    if value is not None and not isinstance(value, str):
        sys.stderr.write(
            f"config_path: {key!r} is a {type(value).__name__}, not a "
            f"string ({value!r}) — ignoring it; the default applies "
            "instead\n"
        )
        value = None
    if not value:
        value = default
    return Path(value).expanduser()
