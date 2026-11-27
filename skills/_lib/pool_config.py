#!/usr/bin/env python3
"""pool_config.py — the portable provider-pool inventory and work-kind fit
table, with shipped defaults.

WHY THIS EXISTS
---------------
Model choice used to be re-derived in prose, from memory, once per run. That
is expensive, it drifts, and it is not portable: the answer depended on who
was reading which document that day. This module makes it a typed, validated
configuration document instead, so the question "which models may take this
kind of work, in which order?" is answered by data the user owns and by one
deterministic function.

The configuration declares model fit, pool tiers, spend policy, and optional
protected headroom:

  * **the models a user actually has** (`models[]`, each naming its pool),
  * **which pools are primary and which are secondary** (`pools[].tier`),
  * **how each pool is spent** (`pools[].policy`, with dated `overrides[]`),
  * **an optional protected amount of a primary pool's quota**
    (`pools[].protected_headroom_percent`), and
  * **work-kind preferences** (`work_kinds[]`, an ordered list of model ids
    per kind of work).

`pool-defaults.json` beside this file ships defaults for all four.  With no
explicit path, `load_pool_config()` also reads the optional per-user
`pool-config.json` location defined by `overrides.py` at run time.  An absent
file leaves every shipped default in place; every present entry is
overridable.

WHAT THIS MODULE DELIBERATELY IS NOT
------------------------------------
* **It never reads a live quota meter.** Resolution is a pure function of the
  document and the work kind — no clock, no network, no environment. Capacity
  is a later, separate input; this file is the eligibility surface it is
  applied to, and the ordering here does not move when a meter does.
* **It never models scheduled or unattended jobs.** No job, schedule or
  calendar value is an input anywhere in this file, by construction: the one
  resolution entry point takes a configuration and a work kind and nothing
  else. Coupling a user's own background jobs into a configuration other
  people are meant to install was ruled out explicitly.
* **It never degrades to a default on bad input.** This is the one place
  where this module's posture is the OPPOSITE of `skills/_lib/config_path.py`
  next door. That helper reads a single optional path key and degrades to a
  documented literal when the file is missing or malformed, because "no
  override" and "a broken override" resolve to the same safe value there.
  Here they do not: silently falling back to a shipped default would choose a
  PROVIDER POOL the user never asked for, and spend real money in it. So every
  malformed, missing or unknown field raises `PoolConfigError` with a message
  that names the field, and nothing is resolved.

THE ORDERING RULES (material — read before editing)
---------------------------------------------------
1. **Fit before capacity.** A work kind's list is the set of models that fit
   that work. A model not named for the kind is never eligible for it, no
   matter how much quota its pool has. Quota may reorder or skip entries
   further down the line; it may never add one.
2. **The declared order is the order.** `resolve_eligible_models` returns the
   work kind's models in the order the document declares them, minus any whose
   pool the user disabled. Same document plus same work kind gives the same
   tuple on every machine, every time.
3. **A secondary pool never covers for another secondary pool.** A work kind's
   eligible order may draw on at most ONE secondary pool; falling back from
   one small pool into another small pool drains both faster and buys nothing.
   The list is read as a fallback chain, so two secondary pools in one chain
   IS one covering for the other.
4. **Every work kind keeps a primary-pool lane.** After the user's disabled
   pools are applied, each work kind must still resolve to at least one model
   in a primary pool — that is where a small pool falls back to, and it is why
   a big pool is never left idle by rule.

Rules 3 and 4 are enforced at load time, against the ENABLED set (what
actually resolves), and each failure names the work kind and the pools.

DOCUMENT SHAPE
--------------
```json
{
  "version": 1,
  "pools": [
    {"id": "claude", "tier": "primary", "billing": "quota",
     "windows": ["5h", "7d"], "policy": "pace",
     "protected_headroom_percent": 10, "note": "optional prose"},
    {"id": "work-api", "tier": "primary", "billing": "api", "ceiling": 12}
  ],
  "overrides": [{"pool": "claude", "policy": "protect",
                 "until": "2026-10-02", "source": "quota review"}],
  "models": [{"id": "claude-opus-5-5", "pool": "claude"},
             {"id": "api-model", "pool": "work-api",
              "price": {"usd_per_mtok": 9.5, "as_of": "2026-10-21"}}],
  "work_kinds": [{"id": "hard_build", "models": ["claude-opus-5-5"]}],
  "disabled_pools": ["xai-grok"]
}
```

`protected_headroom_percent` is optional, is only accepted on a PRIMARY pool,
and is NOT set by the shipped defaults: a protected amount exists only where a
user's own configuration sets one. This module carries it as typed data and
does not itself spend or withhold anything.

`policy` defaults to `pace` for primary pools and `spend-down` for secondary
pools. Protected headroom requires `pace` or `protect`. An override applies
through its ISO `until` date, inclusive. Resolution
requires an explicit `as_of` date and reports expired overrides separately.

BILLING SHAPES
--------------
Every pool declares `billing`, which is required and is either `quota` or
`api`. The two shapes are first-class and exclusive:

* A **`quota`** pool is what this module has always modelled: `windows`,
  `policy`, optional `protected_headroom_percent`. It carries no `ceiling`.
* An **`api`** pool is metered in dollars and has no quota. It carries NO
  `windows`, `policy` or `protected_headroom_percent` (each is refused by
  name), and an optional `ceiling`: the highest `price` it will pay for one
  model, in the same unit as `price`. Every model in an `api` pool declares a
  dated `price`, `{"usd_per_mtok": <number >= 0>, "as_of": "YYYY-MM-DD"}`
  (a blended USD price per million tokens, read on that date); a model in a
  `quota` pool carries none. `tier` is still required on both shapes, so the
  ordering rules below hold unchanged.

The pick on an `api` pool is `cheapest_within_ceiling`: of the models a work
kind lists in that pool, those priced at or under the ceiling, cheapest first,
a tie going to the work kind's declared order. This module only supplies that
ordering; the work-kind model picker applies it.

OVERRIDE SEMANTICS
------------------
`load_pool_config()` discovers the well-known optional override at read time;
`load_pool_config(path)` reads an explicit file, and
`load_pool_config(None)` deliberately reads shipped defaults only.

Either override merges over the shipped defaults **per entry, by id**: an
entry whose `id` matches a shipped entry replaces that entry WHOLE (never a
field-level merge, which would leave a half-specified pool), keeping its
position; an entry with a new id is appended in the order the user wrote it.
Top-level keys that are not one of the three entry sections (`version`,
`note`, `disabled_pools`) are replaced outright. A user who does not have one
of the shipped pools names it in `disabled_pools` rather than rewriting the
sections that mention it.

One field is inherited across that whole-entry replacement: a pool entry that
names a shipped pool and does not repeat `billing` takes the shipped pool's
`billing` (`load_pool_config` does this after merging). A user therefore never
has to restate the billing shape to tweak a shipped pool, while a NEW pool, or
a pool that exists in no shipped default, must declare `billing` itself: the
merged document is what is refused, never a partial override.

RETIRED POOLS
-------------
A pool the user no longer has is retired: the OpenCode Go account was
discontinued, so `opencode-go` is no longer a pool and `opencode-go/*` is no
longer a model route. A document that still names either, anywhere (a pool, a
model id or its pool, a work kind's model list, `disabled_pools`, or an
override), is refused by name with `pool_config_retired_pool`. It is never
silently ignored, because a stale entry would otherwise look like a live lane.

Python 3 stdlib only. No network, no subprocess, no third-party imports.
"""

from __future__ import annotations

import argparse
from datetime import date
import importlib.util
import math
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union

SHIPPED_DEFAULTS_PATH = Path(__file__).resolve().parent / "pool-defaults.json"
_DISCOVER_OVERRIDE = object()

_overrides_spec = importlib.util.spec_from_file_location(
    "pool_config_overrides", Path(__file__).with_name("overrides.py")
)
if _overrides_spec is None or _overrides_spec.loader is None:
    raise RuntimeError("cannot load overrides reader")
overrides = importlib.util.module_from_spec(_overrides_spec)
_overrides_spec.loader.exec_module(overrides)
SUPPORTED_VERSION = 1

PRIMARY = "primary"
SECONDARY = "secondary"
POOL_TIERS = (PRIMARY, SECONDARY)

QUOTA = "quota"
API = "api"
BILLINGS = (QUOTA, API)
# The policy an `api` pool reports: it has no quota to pace, spend or protect.
API_POLICY = "none"

ENTRY_SECTIONS = ("pools", "models", "work_kinds")
TOP_LEVEL_KEYS = ("version", "note", "pools", "models", "work_kinds", "disabled_pools", "overrides")
POOL_KEYS = (
    "id", "tier", "billing", "windows", "protected_headroom_percent", "policy",
    "ceiling", "note",
)
QUOTA_ONLY_POOL_KEYS = ("windows", "policy", "protected_headroom_percent")
POLICIES = ("spend-down", "pace", "protect")
DEFAULT_POLICIES = {PRIMARY: "pace", SECONDARY: "spend-down"}
OVERRIDE_KEYS = ("pool", "policy", "until", "source")
MODEL_KEYS = ("id", "pool", "price", "note")
PRICE_KEYS = ("usd_per_mtok", "as_of")
# The largest integer a float holds exactly; a price or ceiling given as a
# bigger int is refused rather than rounded.
_MAX_EXACT_PRICE_INT = 2**53
WORK_KIND_KEYS = ("id", "models", "note")

_PREFIX = "pool config: "

# The stable refusal token for a document that names a retired pool.
RETIRED_POOL_REFUSAL = "pool_config_retired_pool"
RETIRED_POOLS = ("opencode-go",)


class PoolConfigError(ValueError):
    """Raised for every invalid or missing field. The message always names the
    field that failed. Never raised for a condition that could be resolved by
    picking a pool — this module refuses instead of choosing."""


class ModelSelectionGuardUnsetError(PoolConfigError):
    """The caller did not supply the model-selection script path."""


class WorkKindUndeclaredError(PoolConfigError):
    """Raised by `resolve_eligible_models` when the work kind is not declared.
    A subclass, so every `except PoolConfigError` still catches it; a caller
    that must tell an undeclared work kind from a broken configuration keys
    off this type, never off the message text."""


@dataclass(frozen=True)
class Pool:
    id: str
    tier: str
    windows: Tuple[str, ...]
    protected_headroom_percent: Optional[int] = None
    note: Optional[str] = None
    policy: str = "pace"
    billing: str = QUOTA
    ceiling: Optional[float] = None

    @property
    def is_api(self) -> bool:
        return self.billing == API

    @property
    def is_primary(self) -> bool:
        return self.tier == PRIMARY


@dataclass(frozen=True)
class Price:
    """A dated price: blended USD per million tokens, read on `as_of`."""

    usd_per_mtok: float
    as_of: date


@dataclass(frozen=True)
class Model:
    id: str
    pool: str
    note: Optional[str] = None
    price: Optional[Price] = None


@dataclass(frozen=True)
class WorkKind:
    id: str
    models: Tuple[str, ...]
    note: Optional[str] = None


@dataclass(frozen=True)
class PolicyOverride:
    pool: str
    policy: str
    until: date
    source: str


@dataclass(frozen=True)
class EffectivePolicy:
    policy: str
    expired_overrides: Tuple[PolicyOverride, ...]


@dataclass(frozen=True)
class PoolConfig:
    """A validated configuration. Every invariant this module enforces already
    held when this object was constructed."""

    version: int
    pools: Tuple[Pool, ...]
    models: Tuple[Model, ...]
    work_kinds: Tuple[WorkKind, ...]
    disabled_pools: Tuple[str, ...] = ()
    note: Optional[str] = None
    overrides: Tuple[PolicyOverride, ...] = ()

    @property
    def pools_by_id(self) -> Mapping[str, Pool]:
        return MappingProxyType({pool.id: pool for pool in self.pools})

    @property
    def models_by_id(self) -> Mapping[str, Model]:
        return MappingProxyType({model.id: model for model in self.models})

    @property
    def work_kinds_by_id(self) -> Mapping[str, WorkKind]:
        return MappingProxyType({kind.id: kind for kind in self.work_kinds})

    def pool_of(self, model_id: str) -> Pool:
        """The pool a declared model belongs to. Both lookups are total on a
        validated configuration."""
        return self.pools_by_id[self.models_by_id[model_id].pool]

    def is_enabled(self, model_id: str) -> bool:
        return self.models_by_id[model_id].pool not in self.disabled_pools


# --------------------------------------------------------------------------
# reading and merging
# --------------------------------------------------------------------------


def read_document(path: Union[str, Path]) -> Dict[str, Any]:
    """Read one required JSON object through the shared overrides reader."""
    try:
        data = overrides.read_json_object(path)
    except overrides.OverrideError as exc:
        raise PoolConfigError(f"{_PREFIX}{exc}") from exc
    assert data is not None
    return data


def merge_documents(
    base: Mapping[str, Any], override: Mapping[str, Any]
) -> Dict[str, Any]:
    """Merge a user document over the shipped defaults. Entry sections merge
    by `id` (whole-entry replacement in place, new ids appended); every other
    top-level key is replaced outright."""
    merged: Dict[str, Any] = dict(base)
    for key, value in override.items():
        if key in ENTRY_SECTIONS and isinstance(merged.get(key), list) and isinstance(value, list):
            merged[key] = _merge_entries(merged[key], value)
        else:
            merged[key] = value
    return merged


def _merge_entries(base_entries: List[Any], override_entries: List[Any]) -> List[Any]:
    merged = list(base_entries)
    positions: Dict[str, int] = {}
    for position, entry in enumerate(merged):
        if isinstance(entry, dict) and isinstance(entry.get("id"), str):
            positions.setdefault(entry["id"], position)
    for entry in override_entries:
        entry_id = entry.get("id") if isinstance(entry, dict) else None
        if isinstance(entry_id, str) and entry_id in positions:
            merged[positions[entry_id]] = entry
            del positions[entry_id]
        else:
            merged.append(entry)
    return merged


# --------------------------------------------------------------------------
# field-level helpers — every one of them names the field it rejects
# --------------------------------------------------------------------------


def _require_mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise PoolConfigError(
            f"{_PREFIX}{where} must be a JSON object, got {type(value).__name__}"
        )
    return value


def _require_non_empty_list(value: Any, where: str) -> List[Any]:
    if not isinstance(value, list):
        raise PoolConfigError(
            f"{_PREFIX}{where} must be a JSON array, got {type(value).__name__}"
        )
    if not value:
        raise PoolConfigError(f"{_PREFIX}{where} must declare at least one entry")
    return value


def _require_string(mapping: Mapping[str, Any], key: str, where: str) -> str:
    if key not in mapping:
        raise PoolConfigError(f"{_PREFIX}{where} is missing required field '{key}'")
    value = mapping[key]
    if not isinstance(value, str) or not value.strip():
        raise PoolConfigError(
            f"{_PREFIX}{where}.{key} must be a non-empty string, got {value!r}"
        )
    return value


def _reject_unknown_keys(mapping: Mapping[str, Any], allowed: Tuple[str, ...], where: str) -> None:
    unknown = sorted(key for key in mapping if key not in allowed)
    if unknown:
        raise PoolConfigError(
            f"{_PREFIX}{where} has unknown field(s) {unknown} — allowed fields "
            f"are {sorted(allowed)}"
        )


def _optional_note(mapping: Mapping[str, Any], where: str) -> Optional[str]:
    if "note" not in mapping:
        return None
    note = mapping["note"]
    if not isinstance(note, str):
        raise PoolConfigError(
            f"{_PREFIX}{where}.note must be a string, got {type(note).__name__}"
        )
    return note


def _reject_duplicate(seen: Dict[str, int], value: str, where: str, what: str) -> None:
    if value in seen:
        raise PoolConfigError(
            f"{_PREFIX}{where} declares {what} {value!r} a second time — it is "
            f"already declared at index {seen[value]}"
        )


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------


def validate_document(document: Mapping[str, Any]) -> PoolConfig:
    """Turn a merged document into a `PoolConfig`, or raise `PoolConfigError`
    naming the first field that fails."""
    _require_mapping(document, "the configuration document")
    _reject_retired_pools(document)
    _reject_unknown_keys(document, TOP_LEVEL_KEYS, "the configuration document")

    if "version" not in document:
        raise PoolConfigError(
            f"{_PREFIX}the configuration document is missing required field 'version'"
        )
    version = document["version"]
    if isinstance(version, bool) or not isinstance(version, int):
        raise PoolConfigError(
            f"{_PREFIX}version must be an integer, got {version!r}"
        )
    if version != SUPPORTED_VERSION:
        raise PoolConfigError(
            f"{_PREFIX}version {version} is not supported — this reader "
            f"understands version {SUPPORTED_VERSION}"
        )

    note = _optional_note(document, "the configuration document")
    pools = _validate_pools(document)
    models = _validate_models(document, pools)
    work_kinds = _validate_work_kinds(document, models)
    disabled = _validate_disabled_pools(document, pools)
    overrides = _validate_overrides(document, pools)

    config = PoolConfig(
        version=version,
        pools=pools,
        models=models,
        work_kinds=work_kinds,
        disabled_pools=disabled,
        note=note,
        overrides=overrides,
    )
    _validate_fallback_direction(config)
    return config


def _names_retired_pool(value: Any) -> bool:
    return isinstance(value, str) and any(
        value == retired or value.startswith(f"{retired}/") for retired in RETIRED_POOLS
    )


def _reject_retired_pools(document: Mapping[str, Any]) -> None:
    """Refuse, by name, any reference to a retired pool or one of its models."""

    def refuse(where: str, value: Any) -> None:
        raise PoolConfigError(
            f"{_PREFIX}{RETIRED_POOL_REFUSAL}: {where} names {value!r}, which is a "
            f"retired pool (retired pools are {list(RETIRED_POOLS)}) — remove it"
        )

    for section, field in (
        ("pools", "id"), ("models", "id"), ("models", "pool"), ("overrides", "pool"),
    ):
        entries = document.get(section)
        if not isinstance(entries, list):
            continue
        for index, entry in enumerate(entries):
            if isinstance(entry, dict) and _names_retired_pool(entry.get(field)):
                refuse(f"{section}[{index}].{field}", entry[field])
    kinds = document.get("work_kinds")
    for index, entry in enumerate(kinds if isinstance(kinds, list) else ()):
        listed = entry.get("models") if isinstance(entry, dict) else None
        for position, model_id in enumerate(listed if isinstance(listed, list) else ()):
            if _names_retired_pool(model_id):
                refuse(f"work_kinds[{index}].models[{position}]", model_id)
    disabled = document.get("disabled_pools")
    for index, pool_id in enumerate(disabled if isinstance(disabled, list) else ()):
        if _names_retired_pool(pool_id):
            refuse(f"disabled_pools[{index}]", pool_id)


def _validate_pools(document: Mapping[str, Any]) -> Tuple[Pool, ...]:
    if "pools" not in document:
        raise PoolConfigError(
            f"{_PREFIX}the configuration document is missing required field 'pools'"
        )
    entries = _require_non_empty_list(document["pools"], "pools")
    pools: List[Pool] = []
    seen: Dict[str, int] = {}
    for index, raw in enumerate(entries):
        where = f"pools[{index}]"
        entry = _require_mapping(raw, where)
        _reject_unknown_keys(entry, POOL_KEYS, where)
        pool_id = _require_string(entry, "id", where)
        where = f"pools[{index}] ({pool_id!r})"
        _reject_duplicate(seen, pool_id, where, "pool id")
        seen[pool_id] = index

        tier = _require_string(entry, "tier", where)
        if tier not in POOL_TIERS:
            raise PoolConfigError(
                f"{_PREFIX}{where}.tier must be one of {list(POOL_TIERS)}, got {tier!r}"
            )

        billing = _validate_billing(entry, where)
        if billing == API:
            ceiling = _validate_api_pool(entry, where)
            pools.append(
                Pool(
                    id=pool_id,
                    tier=tier,
                    windows=(),
                    note=_optional_note(entry, where),
                    policy=API_POLICY,
                    billing=API,
                    ceiling=ceiling,
                )
            )
            continue
        if "ceiling" in entry:
            raise PoolConfigError(
                f"{_PREFIX}{where}.ceiling is an api-pool field but billing is "
                f"{QUOTA!r} — a quota pool is limited by its windows, not a price"
            )

        windows = _validate_windows(entry, where)
        policy = entry.get("policy", DEFAULT_POLICIES[tier])
        if not isinstance(policy, str) or policy not in POLICIES:
            raise PoolConfigError(
                f"{_PREFIX}{where}.policy must be one of {list(POLICIES)}, got {policy!r}"
            )
        protected = _validate_protected_headroom(entry, where, tier, pool_id, policy)
        pools.append(
            Pool(
                id=pool_id,
                tier=tier,
                windows=windows,
                protected_headroom_percent=protected,
                note=_optional_note(entry, where),
                policy=policy,
            )
        )

    if not any(pool.is_primary for pool in pools):
        raise PoolConfigError(
            f"{_PREFIX}pools declares no primary pool — a secondary pool has "
            "nothing to fall back to"
        )
    return tuple(pools)


def _validate_billing(entry: Mapping[str, Any], where: str) -> str:
    billing = _require_string(entry, "billing", where)
    if billing not in BILLINGS:
        raise PoolConfigError(
            f"{_PREFIX}{where}.billing must be one of {list(BILLINGS)}, got {billing!r}"
        )
    return billing


def _validate_api_pool(entry: Mapping[str, Any], where: str) -> Optional[float]:
    """An `api` pool has no quota: refuse every quota-only field by name, and
    read the optional ceiling."""
    for key in QUOTA_ONLY_POOL_KEYS:
        if key in entry:
            raise PoolConfigError(
                f"{_PREFIX}{where}.{key} is a quota-pool field but billing is "
                f"{API!r} — an api pool carries no windows, policy or protected "
                "headroom"
            )
    if "ceiling" not in entry:
        return None
    return _require_price_number(entry["ceiling"], f"{where}.ceiling")


def _require_price_number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PoolConfigError(
            f"{_PREFIX}{where} must be a non-negative number, got {value!r}"
        )
    # An int past 2**53 does not survive float(): neighbours collapse onto one
    # float (a price over the ceiling could tie it) and a huge one overflows.
    # Refuse it by name before converting. The message never stringifies the
    # value: str() of an int past the decimal-conversion limit raises ValueError.
    if isinstance(value, int) and abs(value) > _MAX_EXACT_PRICE_INT:
        raise PoolConfigError(
            f"{_PREFIX}{where} must be a non-negative number no larger than "
            f"{_MAX_EXACT_PRICE_INT} (2**53), got an integer of {value.bit_length()} bits"
        )
    if not math.isfinite(value) or value < 0:
        raise PoolConfigError(
            f"{_PREFIX}{where} must be a non-negative number, got {value!r}"
        )
    return float(value)


def _validate_price(entry: Mapping[str, Any], where: str, pool: Pool) -> Optional[Price]:
    if "price" not in entry:
        if pool.is_api:
            raise PoolConfigError(
                f"{_PREFIX}{where} is missing required field 'price' — a model "
                f"in the api pool {pool.id!r} needs a dated price"
            )
        return None
    if not pool.is_api:
        raise PoolConfigError(
            f"{_PREFIX}{where}.price is set on a model in the {QUOTA} pool "
            f"{pool.id!r} — only a model in an api pool carries a price"
        )
    raw = _require_mapping(entry["price"], f"{where}.price")
    _reject_unknown_keys(raw, PRICE_KEYS, f"{where}.price")
    for key in PRICE_KEYS:
        if key not in raw:
            raise PoolConfigError(
                f"{_PREFIX}{where}.price is missing required field '{key}' — a "
                "price is a number and the date it was read"
            )
    amount = _require_price_number(raw["usd_per_mtok"], f"{where}.price.usd_per_mtok")
    as_of = _parse_date(raw["as_of"], f"{where}.price.as_of")
    return Price(usd_per_mtok=amount, as_of=as_of)


def _validate_windows(entry: Mapping[str, Any], where: str) -> Tuple[str, ...]:
    if "windows" not in entry:
        raise PoolConfigError(f"{_PREFIX}{where} is missing required field 'windows'")
    raw_windows = _require_non_empty_list(entry["windows"], f"{where}.windows")
    windows: List[str] = []
    seen: Dict[str, int] = {}
    for index, window in enumerate(raw_windows):
        position = f"{where}.windows[{index}]"
        if not isinstance(window, str) or not window.strip():
            raise PoolConfigError(
                f"{_PREFIX}{position} must be a non-empty string, got {window!r}"
            )
        _reject_duplicate(seen, window, position, "window")
        seen[window] = index
        windows.append(window)
    return tuple(windows)


def _validate_protected_headroom(
    entry: Mapping[str, Any], where: str, tier: str, pool_id: str, policy: str
) -> Optional[int]:
    if "protected_headroom_percent" not in entry:
        return None
    value = entry["protected_headroom_percent"]
    if isinstance(value, bool) or not isinstance(value, int):
        raise PoolConfigError(
            f"{_PREFIX}{where}.protected_headroom_percent must be an integer "
            f"percentage, got {value!r}"
        )
    if not 0 <= value <= 100:
        raise PoolConfigError(
            f"{_PREFIX}{where}.protected_headroom_percent must be between 0 and "
            f"100, got {value}"
        )
    if policy == "spend-down":
        raise PoolConfigError(
            f"{_PREFIX}{where}.protected_headroom_percent requires policy "
            f"'pace' or 'protect', got {policy!r}"
        )
    if tier != PRIMARY:
        raise PoolConfigError(
            f"{_PREFIX}{where}.protected_headroom_percent is set on the "
            f"{tier} pool {pool_id!r} — a protected amount may only be "
            "declared on a primary pool"
        )
    return value


def _validate_models(
    document: Mapping[str, Any], pools: Tuple[Pool, ...]
) -> Tuple[Model, ...]:
    if "models" not in document:
        raise PoolConfigError(
            f"{_PREFIX}the configuration document is missing required field 'models'"
        )
    entries = _require_non_empty_list(document["models"], "models")
    pool_ids = [pool.id for pool in pools]
    pools_by_id = {pool.id: pool for pool in pools}
    models: List[Model] = []
    seen: Dict[str, int] = {}
    for index, raw in enumerate(entries):
        where = f"models[{index}]"
        entry = _require_mapping(raw, where)
        _reject_unknown_keys(entry, MODEL_KEYS, where)
        model_id = _require_string(entry, "id", where)
        where = f"models[{index}] ({model_id!r})"
        _reject_duplicate(seen, model_id, where, "model id")
        seen[model_id] = index

        pool_id = _require_string(entry, "pool", where)
        if pool_id not in pool_ids:
            raise PoolConfigError(
                f"{_PREFIX}{where}.pool names the undeclared pool {pool_id!r} — "
                f"declared pools are {pool_ids}"
            )
        price = _validate_price(entry, where, pools_by_id[pool_id])
        models.append(
            Model(id=model_id, pool=pool_id, note=_optional_note(entry, where), price=price)
        )
    return tuple(models)


def _validate_work_kinds(
    document: Mapping[str, Any], models: Tuple[Model, ...]
) -> Tuple[WorkKind, ...]:
    if "work_kinds" not in document:
        raise PoolConfigError(
            f"{_PREFIX}the configuration document is missing required field "
            "'work_kinds'"
        )
    entries = _require_non_empty_list(document["work_kinds"], "work_kinds")
    model_ids = [model.id for model in models]
    work_kinds: List[WorkKind] = []
    seen: Dict[str, int] = {}
    for index, raw in enumerate(entries):
        where = f"work_kinds[{index}]"
        entry = _require_mapping(raw, where)
        _reject_unknown_keys(entry, WORK_KIND_KEYS, where)
        kind_id = _require_string(entry, "id", where)
        where = f"work_kinds[{index}] ({kind_id!r})"
        _reject_duplicate(seen, kind_id, where, "work kind")
        seen[kind_id] = index

        if "models" not in entry:
            raise PoolConfigError(
                f"{_PREFIX}{where} is missing required field 'models'"
            )
        raw_models = _require_non_empty_list(entry["models"], f"{where}.models")
        preference: List[str] = []
        seen_models: Dict[str, int] = {}
        for position, model_id in enumerate(raw_models):
            at = f"{where}.models[{position}]"
            if not isinstance(model_id, str) or not model_id.strip():
                raise PoolConfigError(
                    f"{_PREFIX}{at} must be a non-empty string, got {model_id!r}"
                )
            if model_id not in model_ids:
                raise PoolConfigError(
                    f"{_PREFIX}{at} names the undeclared model {model_id!r} — "
                    f"declared models are {model_ids}"
                )
            _reject_duplicate(seen_models, model_id, at, "model")
            seen_models[model_id] = position
            preference.append(model_id)
        work_kinds.append(
            WorkKind(
                id=kind_id,
                models=tuple(preference),
                note=_optional_note(entry, where),
            )
        )
    return tuple(work_kinds)


def _validate_disabled_pools(
    document: Mapping[str, Any], pools: Tuple[Pool, ...]
) -> Tuple[str, ...]:
    if "disabled_pools" not in document:
        return ()
    raw = document["disabled_pools"]
    if not isinstance(raw, list):
        raise PoolConfigError(
            f"{_PREFIX}disabled_pools must be a JSON array, got {type(raw).__name__}"
        )
    pool_ids = [pool.id for pool in pools]
    disabled: List[str] = []
    seen: Dict[str, int] = {}
    for index, pool_id in enumerate(raw):
        where = f"disabled_pools[{index}]"
        if not isinstance(pool_id, str) or not pool_id.strip():
            raise PoolConfigError(
                f"{_PREFIX}{where} must be a non-empty string, got {pool_id!r}"
            )
        if pool_id not in pool_ids:
            raise PoolConfigError(
                f"{_PREFIX}{where} names the undeclared pool {pool_id!r} — "
                f"declared pools are {pool_ids}"
            )
        _reject_duplicate(seen, pool_id, where, "pool id")
        seen[pool_id] = index
        disabled.append(pool_id)
    return tuple(disabled)


def _parse_date(value: Any, where: str) -> date:
    if not isinstance(value, str):
        raise PoolConfigError(f"{_PREFIX}{where} must be an ISO date, got {value!r}")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise PoolConfigError(f"{_PREFIX}{where} must be an ISO date, got {value!r}") from exc
    if parsed.isoformat() != value:
        raise PoolConfigError(f"{_PREFIX}{where} must be an ISO date, got {value!r}")
    return parsed


def _validate_overrides(
    document: Mapping[str, Any], pools: Tuple[Pool, ...]
) -> Tuple[PolicyOverride, ...]:
    raw = document.get("overrides", [])
    if not isinstance(raw, list):
        raise PoolConfigError(f"{_PREFIX}overrides must be a JSON array")
    pool_ids = {pool.id for pool in pools}
    api_ids = {pool.id for pool in pools if pool.is_api}
    overrides = []
    for index, item in enumerate(raw):
        where = f"overrides[{index}]"
        entry = _require_mapping(item, where)
        _reject_unknown_keys(entry, OVERRIDE_KEYS, where)
        pool = _require_string(entry, "pool", where)
        if pool not in pool_ids:
            raise PoolConfigError(f"{_PREFIX}{where}.pool names the undeclared pool {pool!r}")
        if pool in api_ids:
            raise PoolConfigError(
                f"{_PREFIX}{where}.pool names the api pool {pool!r} — a dated "
                "policy override applies to a quota pool only"
            )
        policy = _require_string(entry, "policy", where)
        if policy not in POLICIES:
            raise PoolConfigError(f"{_PREFIX}{where}.policy must be one of {list(POLICIES)}, got {policy!r}")
        if "until" not in entry:
            raise PoolConfigError(f"{_PREFIX}{where} is missing required field 'until'")
        until = _parse_date(entry["until"], f"{where}.until")
        source = _require_string(entry, "source", where)
        overrides.append(PolicyOverride(pool, policy, until, source))
    return tuple(overrides)


def _validate_fallback_direction(config: PoolConfig) -> None:
    """Rules 3 and 4 of the ordering rules, checked against what actually
    resolves after the user's disabled pools are applied."""
    for index, kind in enumerate(config.work_kinds):
        where = f"work_kinds[{index}] ({kind.id!r})"
        enabled = [model_id for model_id in kind.models if config.is_enabled(model_id)]
        if not enabled:
            raise PoolConfigError(
                f"{_PREFIX}{where} has no model left once disabled_pools "
                f"{list(config.disabled_pools)} is applied — every model it names "
                "belongs to a disabled pool"
            )
        if not any(config.pool_of(model_id).is_primary for model_id in enabled):
            raise PoolConfigError(
                f"{_PREFIX}{where} resolves to {enabled} with no primary-pool "
                "model — a work kind must keep a primary-pool lane to fall back to"
            )
        secondaries: List[str] = []
        for model_id in enabled:
            pool = config.pool_of(model_id)
            if not pool.is_primary and pool.id not in secondaries:
                secondaries.append(pool.id)
        if len(secondaries) > 1:
            raise PoolConfigError(
                f"{_PREFIX}{where} draws on more than one secondary pool "
                f"({secondaries}) — a secondary pool never covers for another "
                "secondary pool"
            )


# --------------------------------------------------------------------------
# the public seam
# --------------------------------------------------------------------------


def pool_override_path() -> Path:
    """Return the optional per-user pool override path for the current process."""
    return overrides.pool_override_path()


def _inherit_pool_billing(
    defaults: Mapping[str, Any], document: Dict[str, Any]
) -> Dict[str, Any]:
    """A user's pool entry replaces the shipped entry whole, so one that names a
    shipped pool and omits `billing` would otherwise lose it. Copy `billing`
    from the shipped pool of the same id onto such an entry. Nothing else is
    inherited, and an entry with no shipped counterpart is left to the
    validator, which refuses it."""
    shipped = defaults.get("pools")
    merged = document.get("pools")
    if not isinstance(shipped, list) or not isinstance(merged, list):
        return document
    billing_by_id: Dict[str, Any] = {}
    for entry in shipped:
        if isinstance(entry, dict) and isinstance(entry.get("id"), str) and "billing" in entry:
            billing_by_id.setdefault(entry["id"], entry["billing"])
    pools: List[Any] = []
    for entry in merged:
        if (
            isinstance(entry, dict)
            and "billing" not in entry
            and isinstance(entry.get("id"), str)
            and entry["id"] in billing_by_id
        ):
            entry = {**entry, "billing": billing_by_id[entry["id"]]}
        pools.append(entry)
    return {**document, "pools": pools}


def load_pool_config(
    override_path: Optional[Union[str, Path]] | object = _DISCOVER_OVERRIDE,
    *,
    defaults_path: Union[str, Path] = SHIPPED_DEFAULTS_PATH,
) -> PoolConfig:
    """Load shipped defaults plus a discovered or explicit user document.

    With no argument, the optional well-known override is read at call time.
    An explicit ``None`` selects shipped defaults only.  Every malformed
    present document raises `PoolConfigError` naming the path.
    """
    defaults = read_document(defaults_path)
    document = defaults
    if override_path is _DISCOVER_OVERRIDE:
        try:
            override = overrides.read_json_object(pool_override_path(), missing_ok=True)
        except overrides.OverrideError as exc:
            raise PoolConfigError(f"{_PREFIX}{exc}") from exc
        if override is not None:
            document = _inherit_pool_billing(defaults, merge_documents(defaults, override))
    elif override_path is not None:
        document = _inherit_pool_billing(
            defaults, merge_documents(defaults, read_document(override_path))
        )
    return validate_document(document)


def resolve_eligible_models(config: PoolConfig, work_kind: str) -> Tuple[str, ...]:
    """The observable: one deterministic eligible-model order for one work
    kind. Pure — the configuration and the work kind are the only inputs, so
    the same pair gives the same tuple on any machine, at any time."""
    kind = config.work_kinds_by_id.get(work_kind)
    if kind is None:
        declared = [entry.id for entry in config.work_kinds]
        raise WorkKindUndeclaredError(
            f"{_PREFIX}work kind {work_kind!r} is not declared in work_kinds — "
            f"declared work kinds are {declared}; no pool is chosen for an "
            "undeclared work kind"
        )
    return tuple(model_id for model_id in kind.models if config.is_enabled(model_id))


def cheapest_within_ceiling(
    config: PoolConfig, pool_id: str, model_ids: Tuple[str, ...]
) -> Tuple[str, ...]:
    """The pick on an `api` pool: of ``model_ids`` (the models a work kind lists
    in that pool, in declared order), those priced at or under the pool's
    ceiling (all of them when it sets none), cheapest first. A tie keeps the
    order given. Pure: no clock, no network. An empty tuple means nothing in
    this pool qualifies, and the caller refuses by name rather than choosing
    something over the ceiling."""
    pool = config.pools_by_id.get(pool_id)
    if pool is None:
        raise PoolConfigError(f"{_PREFIX}unknown pool {pool_id!r}")
    if not pool.is_api:
        raise PoolConfigError(
            f"{_PREFIX}pool {pool_id!r} is a {pool.billing} pool — only an api "
            "pool is picked by price"
        )
    priced: List[Tuple[float, int, str]] = []
    for position, model_id in enumerate(model_ids):
        model = config.models_by_id.get(model_id)
        if model is None or model.pool != pool_id:
            raise PoolConfigError(
                f"{_PREFIX}model {model_id!r} is not declared in pool {pool_id!r}"
            )
        assert model.price is not None  # validated: every api-pool model is priced
        if pool.ceiling is not None and model.price.usd_per_mtok > pool.ceiling:
            continue
        priced.append((model.price.usd_per_mtok, position, model_id))
    return tuple(model_id for _price, _position, model_id in sorted(priced))


def effective_policy(config: PoolConfig, pool_id: str, as_of: date) -> EffectivePolicy:
    """Resolve policy on an explicit date; the last active override wins."""
    pool = config.pools_by_id.get(pool_id)
    if pool is None:
        raise PoolConfigError(f"{_PREFIX}unknown pool {pool_id!r}")
    if not isinstance(as_of, date):
        raise PoolConfigError(f"{_PREFIX}as_of must be a date")
    if pool.is_api:
        return EffectivePolicy(API_POLICY, ())
    policy = pool.policy
    expired = []
    for override in config.overrides:
        if override.pool != pool_id:
            continue
        if override.until < as_of:
            expired.append(override)
        else:
            policy = override.policy
    return EffectivePolicy(policy, tuple(expired))


# --------------------------------------------------------------------------
# assurance
# --------------------------------------------------------------------------

REVIEW_KINDS = ("review", "second_review")
UNKNOWN_FAMILY = "unknown"
_FAMILY_PREFIXES = (
    ("claude-", "anthropic"),
    ("gpt-", "openai"),
    ("chatgpt-", "openai"),
    ("codex-", "openai"),
    ("grok-", "xai"),
    ("gemini-", "google"),
    ("glm-", "zhipu"),
    ("kimi-", "moonshot"),
    ("deepseek-", "deepseek"),
)


@dataclass(frozen=True)
class Assurance:
    """Whether a row keeps its full review assurance. `reduced` never blocks
    anything; it only carries the one-line reason to print."""

    reduced: bool
    reasons: Tuple[str, ...] = ()

    @property
    def reason(self) -> str:
        return "; ".join(self.reasons)


def model_family(model_id: str) -> str:
    """The family that trains a model, read from the model NAME after any
    provider prefix, so the route that serves it never changes the answer. A
    name that is not recognised is `unknown`, which never counts as an
    outside family."""
    name = model_id.rsplit("/", 1)[-1].lower()
    for candidate in name.split("."):
        for prefix, family in _FAMILY_PREFIXES:
            if candidate.startswith(prefix):
                return family
        if len(candidate) > 1 and candidate[0] == "o" and candidate[1].isdigit():
            return "openai"
    return UNKNOWN_FAMILY


def _promoted_models(config: PoolConfig, shipped: PoolConfig, work_kind: str) -> Tuple[str, ...]:
    """Models moved above their shipped rank, including by omitted entries."""
    user_kind = config.work_kinds_by_id.get(work_kind)
    shipped_kind = shipped.work_kinds_by_id.get(work_kind)
    if user_kind is None or shipped_kind is None:
        return ()
    shipped_order = shipped_kind.models
    user_order = user_kind.models
    promoted: List[str] = []
    for index, model_id in enumerate(user_order):
        if model_id in shipped_order:
            if index < shipped_order.index(model_id):
                promoted.append(model_id)
        elif index < len(shipped_order):
            promoted.append(model_id)
    return tuple(promoted)


def _review_model_can_run(model_id: str, guard: Path) -> bool:
    if not (
        re.fullmatch(r"claude-[a-z0-9][a-z0-9.-]*(?:\[1m\])?", model_id)
        or (
            model_id != "provider/model"
            and re.fullmatch(r"[a-z0-9][a-z0-9-]*/[A-Za-z0-9][A-Za-z0-9._:-]*", model_id)
        )
    ):
        return False
    result = subprocess.run(
        ["bash", "-c", 'source "$1"; model_selection_check "$2"', "review", str(guard), model_id],
        capture_output=True,
    )
    return result.returncode == 0


def row_assurance(
    config: PoolConfig,
    shipped: PoolConfig,
    work_kind: str,
    builder_model: str,
    unavailable_pools: Tuple[str, ...] = (),
    *,
    model_selection_guard: Union[str, Path, None] = None,
) -> Assurance:
    """Label one row. Reduced when no review or second_review model outside the
    builder's family can run, or when the user's configuration ranks a model
    above the shipped order for the row's work kind. Never raises on a lost
    assurance; an undeclared work kind still raises, and so does a missing
    ``model_selection_guard`` (the caller supplies the path to the
    model-selection script that decides whether a review model can run)."""
    if model_selection_guard is None:
        raise ModelSelectionGuardUnsetError(
            "model_selection_guard: the path to the model-selection script is required"
        )
    guard = Path(model_selection_guard)
    resolve_eligible_models(config, work_kind)
    reasons: List[str] = []
    builder_family = model_family(builder_model)
    outside: List[str] = []
    for kind in REVIEW_KINDS:
        if kind not in config.work_kinds_by_id:
            continue
        for model_id in resolve_eligible_models(config, kind):
            if config.models_by_id[model_id].pool in unavailable_pools or not _review_model_can_run(model_id, guard):
                continue
            family = model_family(model_id)
            if builder_family != UNKNOWN_FAMILY and family not in (builder_family, UNKNOWN_FAMILY):
                outside.append(model_id)
            break
    if not outside:
        if builder_family == UNKNOWN_FAMILY:
            safe_builder = re.sub(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]", " ", builder_model)
            reasons.append(f"builder {safe_builder} has an unknown model family")
        else:
            reasons.append(
                f"no review model outside the {builder_family} family can run"
            )
    promoted = _promoted_models(config, shipped, work_kind)
    if promoted:
        safe_ids = (re.sub(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]", " ", model_id) for model_id in promoted)
        reasons.append(
            f"{', '.join(safe_ids)} ranked above the shipped {work_kind} order"
        )
    return Assurance(bool(reasons), tuple(reasons))


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pool_config.py",
        description=(
            "Print the eligible model order for one work kind, most preferred "
            "first."
        ),
    )
    parser.add_argument(
        "--work-kind",
        required=True,
        help="the work kind to resolve, as declared in work_kinds[]",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="optional user configuration merged over the shipped defaults",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        config = (
            load_pool_config()
            if args.config is None
            else load_pool_config(args.config)
        )
        for model_id in resolve_eligible_models(config, args.work_kind):
            print(model_id)
    except PoolConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    sys.exit(main())
