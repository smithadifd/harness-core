"""harness_root.py — the shared harness-root resolver.

WHY THIS EXISTS
----------------
Every loop skill (`session-handoff` · `orchestrate` · `chart-wave` ·
`sharpen` · `seat-retro`; the agent `close-escalation-reviewer` reads
through its caller) used to hardcode one configuration repository and
its sub-paths. This module is the one shared resolver every loop skill's
own `config.json` is read through, so the same skills can run for a second
person or on a second machine without a fork.

PRECEDENCE (material — this is the contract)
---------------------------------------------
`root`            : `HARNESS_ROOT` env var > `config["harness_root"]["root"]`
                     > git toplevel of cwd (or `resolve(..., cwd=)`) when
                     that checkout contains the root marker file
                     (`ROOT_MARKER`, below) AND is a checkout of the
                     repository the config's `git_guard` pin names
                     (matching `origin` `host/owner/repo` identity). With no
                     pin there is nothing to compare against, so cwd is never
                     adopted. When none of these names a root, `resolve()`
                     raises `HarnessRootAbsent` — a `LookupError` whose
                     message IS the one-line fix (`ROOT_ABSENT_FIX`). There is
                     no default root: the shared core names no person and no
                     repository. `require_root` is still accepted but changes
                     nothing; refusing is the only behaviour.
                     A `HARNESS_ROOT` that is still RELATIVE after `~`
                     expansion is REJECTED, not silently resolved against the
                     caller's cwd (a silent-wrong-root class of bug): one
                     stderr line names the rejected value and this precedence
                     falls through to the config key / checkout as if the env
                     var had never been set. `resolve()` raises only on a
                     genuinely malformed `config_path_or_dict` type. Standard
                     resolution records config read/schema failures and
                     refuses at that boundary.
                     `init_root(path)` creates a new root only when the caller
                     supplies an empty target, and writes the root marker.
`roadmaps` /
`standards` /
`decisions` /
`topics`          : `config["harness_root"][<subkey>]` (a config-only
                     override — no per-subkey env var) >
                     `<root>/<subkey>`.
`memory_index`     : `config["harness_root"]["memory_index"]` > a Claude
                     project index derived from the resolved root by
                     replacing `/` in its absolute path with `-`, under
                     `CLAUDE_PROJECTS_DIR` (the Claude projects folder under
                     the home folder, `.claude/projects`, expanded
                     against the running user's `HOME` when resolved).
`delivery`         : `config["harness_root"]["delivery"]` > `"local"`. A
                     string or a list of adapter names (`local`, `desktop`,
                     `discord`, or another delivery adapter's name).
                     Session-handoff is the consumer;
                     this module only carries the value through.

`core_standards`   : `config["harness_root"]["core_standards"]` >
                     absolute `HARNESS_CORE_STANDARDS` > the one shipped
                     folder beside this module (`core_standards()`). A relative
                     env value is rejected. The default folder may be empty.
`standards_override`: `config["harness_root"]["standards_override"]`, a list
                     of names. It is used only to authorize an instance copy
                     when the same name also exists in the core.
`resolve_standard(paths, name)` searches the instance standards directory,
then the core directory. `name` must be a bare filename: empty, absolute,
`.`/`..`, NUL, or separator-bearing names are refused before either
candidate is built. It returns the sole file, refuses an absent name,
and refuses an ambiguous name unless `standards_override` declares it.

ROOT MARKER
-----------
A configuration repository carries an (empty) file named `ROOT_MARKER`
(`.harness-root`) at its top level. Checkout discovery adopts a cwd checkout
only when that file exists there. An existing configuration repository that
relied on discovery needs the file added once; `HARNESS_ROOT` or
`harness_root.root` works without it.

A missing or unreadable config (bad JSON, unreadable path) still DEGRADES to
`{}` for root/path resolution — the root must then come from the environment
— preserving the dry-run posture of the wave triage script's own
`load_config`. The failure is retained on `HarnessPaths`, so
`resolve_standard()` refuses instead of silently resolving without the
instance's override declaration. `resolve()` raises only on a genuinely
unexpected type for `config_path_or_dict`.

IMPORT STRATEGY (D2 — read before adding a new call site)
-----------------------------------------------------------
`install.sh` symlinks a skill's `SKILL.md` (and an agent's `.md`) PER FILE
into the Claude commands and agents folders under the home folder
(`.claude/commands/`, `.claude/agents/`) by default — the `.py`
siblings are never individually linked, so they keep running from the
physical checkout. `--omp` exports the whole `skills/<name>/` directory.
Either way, `Path(__file__).resolve()` inside a
sibling `.py` file always canonicalizes back through any symlink to the
PHYSICAL file, so `Path(__file__).resolve().parent.parent / "_lib" /
"harness_root.py"` finds this module regardless of install mode with no
extra wiring in `install.sh` — no separate symlink of `_lib/` is needed, and
none is added. This is the SAME mechanism the wave triage script's
`load_module()` already uses to reach the readiness gate
and `ai-coding-tasks/triage_taxonomy.py` (also true of
`ticket_lifecycle.py`'s `notion_tasks.py` load) — never `sys.path`, never
`$PWD`. A future skill call site should load this module the same way —
registered under a CALLER-PREFIXED name in `sys.modules` BEFORE
`exec_module`, never the bare `"harness_root"` and never skipped: this
module's own `HarnessPaths` is a `@dataclass` under `from __future__ import
annotations`, whose fields resolve lazily via `sys.modules[cls.__module__]`
— an unregistered module makes that lookup return `None` and crash with
`AttributeError: 'NoneType' object has no attribute '__dict__'` the first
time anything introspects the dataclass (observed on Python 3.14 when the
harness map's `sources.py` loaded this module without registering it; the
wave triage script's `load_module` already registers before `exec_module`,
which is why this snippet must match it exactly rather than being
simplified):

    import importlib.util
    import sys
    from pathlib import Path

    HERE = Path(__file__).resolve().parent
    SKILLS_DIR = HERE.parent
    _spec = importlib.util.spec_from_file_location(
        "your_skill_harness_root", SKILLS_DIR / "_lib" / "harness_root.py"
    )
    harness_root = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = harness_root  # BEFORE exec_module — see above
    _spec.loader.exec_module(harness_root)

`test_import_strategy.py` in this folder's tests proves this against a
symlinked-whole-directory layout (the shape `--omp` produces), the
way a linked skill would actually reach this file.

Python 3 stdlib only. No network, no third-party imports. Subprocesses:
`git rev-parse --show-toplevel`, `git rev-parse --path-format=absolute
--git-common-dir`, and `git worktree list --porcelain` to discover checkout
paths, and `git remote get-url --push --all origin` to compare repository
identity.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Union

#: The file a configuration repository carries at its top level. Checkout
#: discovery adopts a cwd checkout only when this file is present.
ROOT_MARKER = ".harness-root"

#: The one-line fix `HarnessRootAbsent` carries. Names both ways a root can
#: be supplied, in precedence order, so the message is the remedy.
ROOT_ABSENT_FIX = (
    "no harness root is set — export HARNESS_ROOT=/absolute/path/to/root, "
    "or set harness_root.root to that path in the skill's config.json"
)

#: Base of the per-root Claude project indexes. Expanded at run time against
#: the running user's `HOME`; never a literal home path.
#: Joined from two parts so this source holds no home-folder path literal;
#: the value is the same string.
CLAUDE_PROJECTS_DIR = "~" + "/.claude/projects"

#: `delivery`'s default — local write (the wake-up report beside the
#: ledger). Extra adapters are selected by the consumer, never from here.
DEFAULT_DELIVERY = "local"

#: The default core standards folder: shipped standards live here, found
#: relative to this file so it resolves the same from a checkout or export.
#: The folder is declared even while it contains no shipped standard.
CORE_STANDARDS = Path(__file__).resolve().parent / "standards"

#: Optional deployment-level redirect for the shipped standards folder.
CORE_STANDARDS_ENV = "HARNESS_CORE_STANDARDS"

#: The four instance subkeys, each defaulting to `<root>/<name>`.
SUBKEYS = ("roadmaps", "standards", "decisions", "topics")


def core_standards() -> Path:
    """The declared default folder for shipped standards.

    Existence is intentionally not checked here; `resolve_standard()` checks
    the named file at the point of use.
    """
    return CORE_STANDARDS


def init_root(path: Union[str, Path]) -> Path:
    """Create a root at the supplied empty path and return it.

    An existing entry, including a hidden one, makes the target occupied.
    This function does not consult the resolver's environment or defaults.
    A missing parent raises FileNotFoundError without creating anything.
    """
    root = Path(path).expanduser()
    if root.is_symlink():
        raise FileExistsError(f"target is occupied: {root}")
    if root.exists():
        if not root.is_dir() or any(root.iterdir()):
            raise FileExistsError(f"target is occupied: {root}")
    else:
        root.mkdir()

    for name in SUBKEYS:
        (root / name).mkdir()
    (root / ROOT_MARKER).touch(exist_ok=False)
    (root / "roadmaps" / "wave-current.md").touch(exist_ok=False)
    return root


class HarnessRootAbsent(LookupError):
    """Raised by `resolve()` when neither `HARNESS_ROOT` nor
    `config["harness_root"]["root"]` names a root and cwd is not a checkout of
    the pinned repository that contains `ROOT_MARKER`. `str(exc)` is the one-line fix
    (`ROOT_ABSENT_FIX`); a CLI prints it and exits, nothing more.
    Deliberately a `LookupError`, not a `ValueError`: the config is
    well-formed, the root is simply not there.
    """

    def __init__(self, message: str = ROOT_ABSENT_FIX) -> None:
        super().__init__(message)


class StandardResolutionError(LookupError):
    """Base class for named, fail-closed standard-resolution refusals."""


class StandardAbsent(StandardResolutionError):
    """Raised when a named standard exists in neither searched directory."""


class StandardAmbiguous(StandardResolutionError):
    """Raised when both searched directories contain an undeclared duplicate."""


class StandardConfigInvalid(StandardResolutionError):
    """Raised when config cannot safely authorize standard precedence."""


class StandardNameInvalid(StandardResolutionError):
    """Raised when a standard name is not a bare filename."""


@dataclass(frozen=True)
class HarnessPaths:
    """The resolver's output, including both standards search locations.

    Path attributes are expanded (`~` resolved), but not existence-checked.
    The private fields retain the validated override names and any config
    failure for `resolve_standard()`; consumers do not interpret them.
    """

    root: Path
    roadmaps: Path
    standards: Path
    core_standards: Path
    decisions: Path
    topics: Path
    memory_index: Path
    delivery: Optional[Any] = None
    _standards_override: frozenset[str] = field(
        default_factory=frozenset, repr=False
    )
    _standard_config_error: Optional[str] = field(
        default=None, repr=False, compare=False
    )


@dataclass(frozen=True)
class _ConfigLoad:
    config: Mapping[str, Any]
    standard_error: Optional[str] = None


def _load_config(
    config_path_or_dict: Union[str, Path, Mapping[str, Any], None]
) -> _ConfigLoad:
    """Load config while retaining failures needed by standard resolution.

    Root/path resolution keeps its established degrade-to-empty behavior.
    Standard resolution later fails closed on `standard_error`.
    """
    if config_path_or_dict is None:
        return _ConfigLoad({})
    if isinstance(config_path_or_dict, Mapping):
        return _ConfigLoad(config_path_or_dict)
    path = Path(config_path_or_dict)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return _ConfigLoad({}, f"config unreadable at {path}: {exc}")
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        return _ConfigLoad({}, f"config malformed at {path}: {exc}")
    if not isinstance(parsed, Mapping):
        return _ConfigLoad({}, f"config malformed at {path}: top level must be an object")
    return _ConfigLoad(parsed)


def _env_root() -> Optional[str]:
    """`HARNESS_ROOT`, or `None` if unset OR relative after `~` expansion.
    A relative env value resolves against
    whatever the CALLER's cwd happens to be at the moment `resolve()` runs
    — a silent-wrong-root bug class, not a redirect — so it is rejected
    with one loud stderr line rather than honoured. Returning `None` here
    makes the rejection fall through to the config key / default exactly
    as if the env var had never been set; this function never raises.
    """
    raw = os.environ.get("HARNESS_ROOT")
    if not raw:
        return None
    expanded = Path(raw).expanduser()
    if expanded.is_absolute():
        return str(expanded)
    sys.stderr.write(
        f"harness_root: HARNESS_ROOT={raw!r} is relative after ~ expansion "
        "(would resolve against the current working directory) — ignoring "
        "it; the config key or checkout applies instead\n"
    )
    return None


def _checkout_root(cwd: Union[str, Path, None] = None) -> Optional[str]:
    """Git toplevel of `cwd` (default: process cwd) when that checkout
    contains `ROOT_MARKER`. `None` on any failure — discovery
    never raises.
    """
    try:
        start = Path(cwd) if cwd is not None else Path.cwd()
        proc = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            return None
        top = proc.stdout.strip()
        if not top:
            return None
        if (Path(top) / ROOT_MARKER).is_file():
            return top
    except OSError:
        return None
    return None


def _normalize_origin_url(url: str) -> Optional[str]:
    """One origin url → canonical `host/owner/repo`, or None. Same forms the
    push-scope pin recognises (`https://`, `http://`, `ssh://git@`, `git@host:`);
    github.com only; trailing slashes stripped before a `.git` suffix.
    """
    url = url.strip()
    if not url:
        return None
    while url.endswith("/"):
        url = url[:-1]
    if url.endswith(".git"):
        url = url[:-4]
    if url.startswith(("https://", "http://")):
        rest = url.split("://", 1)[1]
        host, _, path = rest.partition("/")
    elif url.startswith("ssh://git@"):
        rest = url[len("ssh://git@"):]
        host, _, path = rest.partition("/")
    elif url.startswith("git@"):
        rest = url[len("git@"):]
        host, _, path = rest.partition(":")
    else:
        return None
    if host != "github.com" or not path:
        return None
    return f"{host}/{path}"


def origin_identities(root: Union[str, Path]) -> frozenset[str]:
    """`host/owner/repo` identities of `root`'s `origin` push remotes.
    Empty on any failure. Never raises.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "remote", "get-url", "--push", "--all", "origin"],
            capture_output=True,
            text=True,
        )
    except OSError:
        return frozenset()
    if proc.returncode != 0:
        return frozenset()
    identities: list[str] = []
    for line in proc.stdout.splitlines():
        ident = _normalize_origin_url(line.strip())
        if ident:
            identities.append(ident)
    return frozenset(identities)


def _pin_identities(config: Mapping[str, Any]) -> frozenset[str]:
    """Optional git-guard pin from config (`harness_root.git_guard` or
    top-level `git_guard`): a comma list or a sequence of `host/owner/repo`
    entries. Empty when unset. Never raises.
    """
    raw = None
    hr = config.get("harness_root") if isinstance(config, Mapping) else None
    if isinstance(hr, Mapping):
        raw = hr.get("git_guard")
    if not raw and isinstance(config, Mapping):
        raw = config.get("git_guard")
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.split(",") if p.strip()]
    elif isinstance(raw, (list, tuple)):
        parts = [str(p).strip() for p in raw if str(p).strip()]
    else:
        return frozenset()
    return frozenset(parts)


def _same_repo_as_configured(checkout: str, config: Mapping[str, Any]) -> bool:
    """True iff `checkout`'s origin identity equals the config's `git_guard`
    pin. With no pin, or no readable origin, False: cwd is not adopted.
    """
    checkout_ids = origin_identities(checkout)
    if not checkout_ids:
        return False
    ref_ids = _pin_identities(config)
    return bool(ref_ids) and checkout_ids == ref_ids


def _main_worktree_root(root: Path) -> Path:
    """Return the main worktree for ``root``, or ``root`` when Git cannot."""
    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            ],
            capture_output=True,
            text=True,
        )
        common_dir = proc.stdout.strip()
        if proc.returncode == 0 and common_dir:
            common_path = Path(common_dir)
            return common_path.parent if common_path.name == ".git" else common_path

        proc = subprocess.run(
            ["git", "-C", str(root), "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                if line.startswith("worktree ") and line.removeprefix("worktree "):
                    return Path(line.removeprefix("worktree "))
    except OSError:
        pass
    return root


def resolve(
    config_path_or_dict: Union[str, Path, Mapping[str, Any], None] = None,
    *,
    require_root: bool = False,
    cwd: Union[str, Path, None] = None,
) -> HarnessPaths:
    """Resolve paths using `config_path_or_dict`. The argument is
    whichever of a skill's own `config.json` (a path) or an already-parsed
    config (a dict) the caller has to hand; `None` resolves purely off the
    environment and the documented defaults.

    When neither the env var nor the config key names a root and cwd is not a
    checkout of the pinned repository containing `ROOT_MARKER`,
    `HarnessRootAbsent` is raised with the one-line fix rather than a path
    being guessed. `require_root` is accepted for compatibility and has no
    effect. `cwd` (default: process cwd) is the directory whose git toplevel
    is considered for the checkout step.

    Precedence, per attribute, is documented in the module docstring above.
    """
    loaded = _load_config(config_path_or_dict)
    config = loaded.config
    hr_config_raw = config.get("harness_root")
    standard_config_error = loaded.standard_error
    if hr_config_raw is not None and not isinstance(hr_config_raw, Mapping):
        standard_config_error = standard_config_error or (
            "harness_root must be an object"
        )
    hr_config = hr_config_raw if isinstance(hr_config_raw, Mapping) else {}

    root_value = _env_root() or hr_config.get("root")
    if not root_value:
        checkout = _checkout_root(cwd)
        if checkout and _same_repo_as_configured(checkout, config):
            root_value = checkout
        else:
            raise HarnessRootAbsent()
    root = Path(root_value).expanduser()

    def _subkey(name: str) -> Path:
        override = hr_config.get(name)
        if override:
            return Path(override).expanduser()
        return root / name

    core_override = hr_config.get("core_standards")
    if core_override is not None and not isinstance(core_override, (str, Path)):
        standard_config_error = standard_config_error or (
            "harness_root.core_standards must be a path string or null"
        )
        core_override = None
    if core_override:
        resolved_core_standards = Path(core_override).expanduser()
    else:
        env_core = os.environ.get(CORE_STANDARDS_ENV)
        if env_core:
            candidate = Path(env_core).expanduser()
            if candidate.is_absolute():
                resolved_core_standards = candidate
            else:
                print(
                    f"ignoring relative {CORE_STANDARDS_ENV}={env_core!r}; "
                    "use an absolute path",
                    file=sys.stderr,
                )
                resolved_core_standards = core_standards()
        else:
            resolved_core_standards = core_standards()

    override_value = hr_config.get("standards_override")
    if override_value is None:
        standards_override: frozenset[str] = frozenset()
    elif (
        not isinstance(override_value, list)
        or any(not isinstance(name, str) or not name for name in override_value)
    ):
        standards_override = frozenset()
        standard_config_error = standard_config_error or (
            "harness_root.standards_override must be a list of non-empty strings"
        )
    else:
        standards_override = frozenset(override_value)

    memory_index_value = hr_config.get("memory_index")
    if not memory_index_value:
        root_slug = str(_main_worktree_root(root).absolute()).replace("/", "-")
        memory_index_value = (
            Path(CLAUDE_PROJECTS_DIR).expanduser() / root_slug / "memory" / "MEMORY.md"
        )
    delivery_value = hr_config.get("delivery") or DEFAULT_DELIVERY

    return HarnessPaths(
        root=root,
        roadmaps=_subkey("roadmaps"),
        standards=_subkey("standards"),
        core_standards=resolved_core_standards,
        decisions=_subkey("decisions"),
        topics=_subkey("topics"),
        memory_index=Path(memory_index_value).expanduser(),
        delivery=delivery_value,
        _standards_override=standards_override,
        _standard_config_error=standard_config_error,
    )


def _require_bare_standard_name(name: str) -> None:
    """Refuse names that would join outside either searched directory."""
    if (
        name == ""
        or os.path.isabs(name)
        or "/" in name
        or os.sep in name
        or name in (".", "..")
        or "\0" in name
    ):
        raise StandardNameInvalid(
            f"standard name {name!r} is not a bare filename"
        )


def resolve_standard(paths: HarnessPaths, name: str) -> Path:
    """Return the named standard from exactly one authorized location.

    Search order is instance, then core. A declared override authorizes the
    first candidate when both files exist; every other zero/two-result state is
    a named refusal that includes the standard and both searched paths.
    Names that are not a bare filename are refused before either candidate
    is built.
    """
    _require_bare_standard_name(name)

    instance_path = paths.standards / name
    core_path = paths.core_standards / name

    if paths._standard_config_error:
        raise StandardConfigInvalid(
            f"cannot resolve standard {name!r}: {paths._standard_config_error}"
        )

    candidates = (instance_path, core_path)
    found = tuple(path for path in candidates if path.is_file())
    if not found:
        raise StandardAbsent(
            f"standard {name!r} was not found; searched {instance_path} and {core_path}"
        )
    if len(found) == 2 and name not in paths._standards_override:
        raise StandardAmbiguous(
            f"standard {name!r} exists in both {instance_path} and {core_path}; "
            "add its name to harness_root.standards_override to let the instance win"
        )
    if len(found) == 2:
        print(
            f"standard {name!r}: instance override selects {instance_path} "
            f"over {core_path}",
            file=sys.stderr,
        )
    return found[0]


def resolve_relative(paths: HarnessPaths, subkey: str, value: str) -> Path:
    """Join a bare, skill-specific filename (or glob) onto one of
    `paths`'s four subkey directories — the helper a skill calls so
    `chart-wave`/`sharpen`/`seat-retro`'s own `config.json`
    keys (`sot_path`, `delegation_loop_standard`, `roadmaps_glob`, …) can
    carry a bare filename instead of repeating `harness_root`'s own default
    absolute prefix. Standards should use `resolve_standard()` instead so the
    ordered instance/core search and duplicate refusal apply.

    `value` already absolute, or `~`-prefixed, is returned expanded and
    UNCHANGED relative to `subkey` — an explicit override always wins, the
    same "explicit beats derived" rule `resolve()` itself uses. An empty
    string names the subkey directory itself (e.g. `sharpen`'s old
    `brief_dir`, which pointed at `harness_root.roadmaps` whole).
    """
    if value == "":
        return getattr(paths, subkey)
    candidate = Path(value)
    if candidate.is_absolute() or str(value).startswith("~"):
        return candidate.expanduser()
    return getattr(paths, subkey) / value
