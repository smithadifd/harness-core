"""Tests for `skills/_lib/pool_config.py` — the portable pool inventory and
work-kind fit table.

The one observable these tests exist to hold: **the same validated
configuration resolves one deterministic eligible-model order from the same
work kind, on any machine.** Everything else here is a guard on that — the
shipped defaults reproduce a measured subscription set, an override changes
exactly what it names, and an invalid or missing field fails with a message
that names the field instead of quietly choosing a pool.

Every negative case goes through `assert_named_error`, which fails with a
message naming the condition under test when the error does not arrive or does
not name its field. A guard that stops guarding therefore says which guard it
was, not just that something did not raise.

Stdlib + pytest only. Offline by construction: nothing here reads a quota
meter, a clock, or the network.
"""

import ast
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest

LIB_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = LIB_DIR / "pool_config.py"
SHIPPED_DEFAULTS = LIB_DIR / "pool-defaults.json"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pool_config"


def _load_module():
    """Load by explicit path, the same shape the other modules in this
    directory are loaded with — `skills/_lib/` is never on `sys.path`."""
    spec = importlib.util.spec_from_file_location("pool_config_under_test", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


pool_config = _load_module()
PoolConfigError = pool_config.PoolConfigError


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def assert_named_error(call, *, names, condition):
    """Run `call`, require a `PoolConfigError` whose message contains `names`.

    `condition` is prose describing the guard being exercised; it is what the
    failure message leads with, so a guard that stops guarding is identifiable
    from the assertion text alone.
    """
    try:
        call()
    except PoolConfigError as exc:
        message = str(exc)
        assert names in message, (
            f"{condition}: the error did not name the field — expected "
            f"{names!r} in the message, got {message!r}"
        )
        return message
    raise AssertionError(
        f"{condition}: expected PoolConfigError naming {names!r}, but the "
        "configuration was accepted and a pool would have been chosen anyway"
    )


def base_document():
    """A small, self-contained valid document. Negative cases mutate a copy of
    this rather than the shipped defaults, so a defaults edit cannot quietly
    change what a validation test is asserting."""
    return {
        "version": 1,
        "pools": [
            {"id": "big-a", "tier": "primary", "billing": "quota", "windows": ["5h", "7d"]},
            {"id": "big-b", "tier": "primary", "billing": "quota", "windows": ["7d"]},
            {"id": "small-a", "tier": "secondary", "billing": "quota", "windows": ["7d"]},
            {"id": "small-b", "tier": "secondary", "billing": "quota", "windows": ["5h", "monthly"]},
        ],
        "models": [
            {"id": "model-big-a", "pool": "big-a"},
            {"id": "model-big-b", "pool": "big-b"},
            {"id": "model-small-a", "pool": "small-a"},
            {"id": "model-small-b", "pool": "small-b"},
        ],
        "work_kinds": [
            {"id": "build", "models": ["model-small-a", "model-big-b", "model-big-a"]},
            {"id": "review", "models": ["model-big-a", "model-big-b"]},
        ],
    }


def validate(document):
    return pool_config.validate_document(document)


def write_json(path, document):
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# the shipped defaults reproduce the measured subscription set
# ---------------------------------------------------------------------------


def test_shipped_defaults_file_validates():
    config = pool_config.load_pool_config(None)
    assert config.version == pool_config.SUPPORTED_VERSION


def test_policy_defaults_follow_pool_tier():
    config = validate(base_document())
    assert config.pools_by_id["big-a"].policy == "pace", "primary policy must default to pace"
    assert config.pools_by_id["small-a"].policy == "spend-down", "secondary policy must default to spend-down"


def test_pool_config_reads_no_clock():
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    clock_calls = [
        ast.unparse(node.func) for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and (node.func.value.id, node.func.attr) in {
            ("datetime", "now"), ("date", "today"), ("time", "time")
        }
    ]
    assert not clock_calls, f"pool_config must read no clock; found {clock_calls}"


def test_invalid_pool_policy_fails_named():
    document = base_document()
    document["pools"][0]["policy"] = "unlimited"
    assert_named_error(lambda: validate(document), names="policy",
                       condition="unknown pool policy must fail named")


def test_dated_policy_override_applies_through_until_and_reports_expiration():
    document = base_document()
    document["pools"][0]["policy"] = "protect"
    document["overrides"] = [{"pool": "big-a", "policy": "spend-down",
                              "until": "2026-10-02", "source": "quota review"}]
    config = validate(document)
    on_date = pool_config.effective_policy(config, "big-a", date(2026, 10, 2))
    after = pool_config.effective_policy(config, "big-a", date(2026, 10, 3))
    assert on_date.policy == "spend-down", "override must apply through its until date"
    assert on_date.expired_overrides == (), "active override must not be reported expired"
    assert after.policy == "protect", "expired override must restore the pool policy"
    assert after.expired_overrides == config.overrides, "expired override must be reported separately"


def test_override_does_not_change_another_pool():
    document = base_document()
    document["overrides"] = [{"pool": "big-a", "policy": "protect",
                              "until": "2026-10-02", "source": "review"}]
    config = validate(document)
    result = pool_config.effective_policy(config, "big-b", date(2026, 10, 2))
    assert result.policy == "pace", "an override for big-a must not change big-b"
    assert result.expired_overrides == (), "another pool's override must not be reported"


def test_last_active_override_for_same_pool_wins():
    document = base_document()
    document["overrides"] = [
        {"pool": "big-a", "policy": "protect", "until": "2026-10-03", "source": "first"},
        {"pool": "big-a", "policy": "spend-down", "until": "2026-10-02", "source": "last"},
    ]
    config = validate(document)
    result = pool_config.effective_policy(config, "big-a", date(2026, 10, 2))
    assert result.policy == "spend-down", "last active override must win"


def test_override_until_requires_canonical_iso_date():
    document = base_document()
    document["overrides"] = [{"pool": "big-a", "policy": "protect",
                              "until": "20261002", "source": "review"}]
    assert_named_error(lambda: validate(document), names="until",
                       condition="non-canonical override date must fail named")


@pytest.mark.parametrize("field,value", [
    ("pool", "missing"), ("policy", "unlimited"), ("until", "bad-date"),
    ("until", None), ("source", ""),
])
def test_invalid_override_fails_named(field, value):
    document = base_document()
    document["overrides"] = [{"pool": "big-a", "policy": "protect",
                              "until": "2026-10-02", "source": "review"}]
    document["overrides"][0][field] = value
    assert_named_error(lambda: validate(document), names=field,
                       condition=f"invalid override {field} must fail named")


def test_missing_until_and_unknown_effective_pool_fail_named():
    document = base_document()
    document["overrides"] = [{"pool": "big-a", "policy": "protect", "source": "review"}]
    assert_named_error(lambda: validate(document), names="until",
                       condition="missing override until must fail named")
    config = validate(base_document())
    assert_named_error(lambda: pool_config.effective_policy(config, "absent", date(2026, 10, 2)),
                       names="unknown pool", condition="unknown effective pool must fail named")


def test_override_array_and_fields_fail_named():
    document = base_document()
    document["overrides"] = {"pool": "big-a"}
    assert_named_error(lambda: validate(document), names="overrides",
                       condition="overrides must be an array")
    document["overrides"] = [{"pool": "big-a", "policy": "protect",
                              "until": "2026-10-02", "source": "review", "extra": True}]
    assert_named_error(lambda: validate(document), names="extra",
                       condition="unknown override fields must fail named")


def test_shipped_defaults_pin_pool_policies():
    config = pool_config.load_pool_config(None)
    assert {pool.id: pool.policy for pool in config.pools} == {
        "claude": "pace",
        "openai-codex": "pace",
        "xai-grok": "spend-down",
        "google-antigravity-gemini": "spend-down",
        "google-antigravity-shared": "spend-down",
    }, "shipped pool policies changed"


def test_shipped_defaults_declare_the_measured_pools_and_windows():
    config = pool_config.load_pool_config(None)
    declared = {
        pool.id: (pool.tier, tuple(pool.windows)) for pool in config.pools
    }
    assert declared == {
        "claude": ("primary", ("5h", "7d", "fable-weekly")),
        "openai-codex": ("primary", ("7d",)),
        "xai-grok": ("secondary", ("7d",)),
        "google-antigravity-gemini": ("secondary", ("5h", "7d")),
        "google-antigravity-shared": ("secondary", ("5h", "7d")),
    }, "the shipped defaults no longer reproduce the measured subscription set"


def test_shipped_defaults_set_no_protected_headroom_anywhere():
    config = pool_config.load_pool_config(None)
    with_protection = [
        pool.id for pool in config.pools if pool.protected_headroom_percent is not None
    ]
    assert with_protection == [], (
        "a protected amount exists only where a user's own configuration sets "
        f"one, but the shipped defaults protect {with_protection}"
    )


def test_shipped_defaults_resolve_one_known_order_per_work_kind():
    config = pool_config.load_pool_config(None)
    resolved = {
        kind.id: pool_config.resolve_eligible_models(config, kind.id)
        for kind in config.work_kinds
    }
    assert resolved == {
        "orchestration": ("claude-opus-5-5", "openai-codex/gpt-6.1-sol"),
        "hard_build": ("claude-opus-5-5", "openai-codex/gpt-6.1-sol"),
        "mechanical_build": (
            "xai-oauth/grok-4.6",
            "openai-codex/gpt-6.1-sol",
            "claude-sonnet-5-5",
        ),
        "docs": (
            "google-antigravity/gemini-3.8-flash",
            "openai-codex/gpt-6.1-sol",
            "claude-sonnet-5-5",
        ),
        "review": ("claude-sonnet-5-5", "openai-codex/gpt-6.1-sol"),
        "second_review": (
            "openai-codex/gpt-6.1-sol",
            "xai-oauth/grok-4.6",
            "claude-sonnet-5-5",
        ),
        "lint": ("claude-sonnet-5-5", "openai-codex/gpt-6.1-sol"),
        "research": ("openai-codex/gpt-6.1-sol", "claude-sonnet-5-5"),
    }, "the shipped defaults resolve a different order than the recorded one"


def test_every_shipped_work_kind_keeps_a_primary_lane():
    config = pool_config.load_pool_config(None)
    for kind in config.work_kinds:
        order = pool_config.resolve_eligible_models(config, kind.id)
        primaries = [m for m in order if config.pool_of(m).is_primary]
        assert primaries, (
            f"work kind {kind.id!r} resolves to {order} with no primary-pool "
            "model, so a secondary pool would have nothing to fall back to"
        )


# ---------------------------------------------------------------------------
# retired pools: OpenCode Go is gone, and a config that names it is refused by
# name rather than silently ignored
# ---------------------------------------------------------------------------

RETIRED_POOL_REFUSAL = "pool_config_retired_pool"


def _retired_override(tmp_path, document):
    path = tmp_path / "override.json"
    path.write_text(json.dumps({"version": 1, **document}), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "document",
    [
        {"pools": [{"id": "opencode-go", "tier": "secondary", "billing": "quota",
                    "windows": ["5h"]}]},
        {"models": [{"id": "gateway/glm-5.3-flash", "pool": "opencode-go"}]},
        {"disabled_pools": ["opencode-go"]},
        {"overrides": [{"pool": "opencode-go", "policy": "protect",
                        "until": "2026-12-01", "source": "x"}]},
    ],
    ids=["pool-id", "model-pool", "disabled-pools", "overrides-pool"],
)
def test_a_config_naming_the_retired_opencode_go_pool_is_refused_by_name(tmp_path, document):
    override = _retired_override(tmp_path, document)
    assert_named_error(
        lambda: pool_config.load_pool_config(override),
        names=RETIRED_POOL_REFUSAL,
        condition="a configuration that names the retired opencode-go pool",
    )


@pytest.mark.parametrize(
    "document",
    [
        {"models": [{"id": "opencode-go/glm-5.3-flash", "pool": "xai-grok"}]},
        {"work_kinds": [{"id": "docs",
                         "models": ["opencode-go/kimi-k2.7-code", "claude-sonnet-5-5"]}]},
    ],
    ids=["placement", "work-kind-list"],
)
def test_a_placement_naming_an_opencode_go_model_is_refused_by_name(tmp_path, document):
    override = _retired_override(tmp_path, document)
    assert_named_error(
        lambda: pool_config.load_pool_config(override),
        names=RETIRED_POOL_REFUSAL,
        condition="a model placement that names an opencode-go/* model",
    )


# ---------------------------------------------------------------------------
# the four required fixture configurations
# ---------------------------------------------------------------------------


def test_defaults_fixture_resolves_exactly_as_the_shipped_defaults():
    shipped = pool_config.load_pool_config(None)
    accepted = pool_config.load_pool_config(FIXTURES / "defaults.json")
    for kind in shipped.work_kinds:
        assert pool_config.resolve_eligible_models(
            accepted, kind.id
        ) == pool_config.resolve_eligible_models(shipped, kind.id), (
            f"accepting every default changed the order for work kind {kind.id!r}"
        )
    assert [p.id for p in accepted.pools] == [p.id for p in shipped.pools]
    assert [m.id for m in accepted.models] == [m.id for m in shipped.models]


def test_override_fixture_replaces_in_place_and_appends_new_entries():
    config = pool_config.load_pool_config(FIXTURES / "override.json")

    assert [pool.id for pool in config.pools] == [
        "claude",
        "openai-codex",
        "xai-grok",
        "google-antigravity-gemini",
        "google-antigravity-shared",
        "local-ollama",
    ], "an overriding entry must replace its shipped entry in place, and a new id must append"

    assert config.pools_by_id["claude"].protected_headroom_percent == 15, (
        "a user's protected amount on a primary pool must survive the merge"
    )
    assert config.disabled_pools == ("xai-grok",)

    assert pool_config.resolve_eligible_models(config, "docs") == (
        "openai-codex/gpt-5.6-sol",
        "claude-sonnet-5-5",
    ), "the overridden docs entry must replace the shipped one whole"

    assert pool_config.resolve_eligible_models(config, "triage") == (
        "qwen3.5:32b",
        "claude-sonnet-5-5",
    ), "a work kind the user added must resolve from the user's own models"


def test_override_fixture_drops_every_model_of_a_disabled_pool():
    config = pool_config.load_pool_config(FIXTURES / "override.json")
    for kind in config.work_kinds:
        order = pool_config.resolve_eligible_models(config, kind.id)
        assert "xai-oauth/grok-4.6" not in order, (
            f"work kind {kind.id!r} still resolves a model from the disabled "
            f"pool 'xai-grok': {order}"
        )
    assert pool_config.resolve_eligible_models(config, "mechanical_build") == (
        "openai-codex/gpt-6.1-sol",
        "claude-sonnet-5-5",
    ), "disabling a secondary pool must fall back to the primary pools, in declared order"


def test_missing_field_fixture_fails_named():
    message = assert_named_error(
        lambda: pool_config.load_pool_config(FIXTURES / "missing-field.json"),
        names="is missing required field 'tier'",
        condition="a pool entry with no tier must fail named",
    )
    assert "pools[0]" in message and "claude" in message, (
        f"the error must name which pool entry is incomplete, got {message!r}"
    )


def test_invalid_model_fixture_fails_named():
    message = assert_named_error(
        lambda: pool_config.load_pool_config(FIXTURES / "invalid-model.json"),
        names="names the undeclared model 'google-antigravity/gemini-3.9-ultra'",
        condition="a work kind naming a model that is not declared must fail named",
    )
    assert "work_kinds" in message and "docs" in message, (
        f"the error must name which work kind points at the undeclared model, "
        f"got {message!r}"
    )


# ---------------------------------------------------------------------------
# determinism and purity — the observable
# ---------------------------------------------------------------------------


def test_same_config_and_work_kind_resolve_identically_every_time():
    config = pool_config.load_pool_config(None)
    orders = {pool_config.resolve_eligible_models(config, "docs") for _ in range(50)}
    assert len(orders) == 1, f"resolution is not deterministic: {orders}"


def test_two_independent_loads_of_one_document_agree():
    first = pool_config.load_pool_config(None)
    second = pool_config.load_pool_config(None)
    assert [
        pool_config.resolve_eligible_models(first, kind.id) for kind in first.work_kinds
    ] == [
        pool_config.resolve_eligible_models(second, kind.id)
        for kind in second.work_kinds
    ], "two loads of the same document resolved differently"


def test_ordering_does_not_move_with_the_environment(monkeypatch):
    config = pool_config.load_pool_config(None)
    before = pool_config.resolve_eligible_models(config, "mechanical_build")
    for name in ("HOME", "TZ", "HARNESS_ROOT", "POOL_CONFIG", "LANG", "USER"):
        monkeypatch.setenv(name, "a-value-the-resolver-must-not-read")
    after = pool_config.resolve_eligible_models(config, "mechanical_build")
    assert before == after, (
        "the eligible order changed with the environment, so it is not "
        f"portable: {before} then {after}"
    )


def test_resolution_takes_a_config_and_a_work_kind_and_nothing_else():
    import inspect

    parameters = list(
        inspect.signature(pool_config.resolve_eligible_models).parameters
    )
    assert parameters == ["config", "work_kind"], (
        "resolution must take no further input — a schedule, job or meter "
        f"parameter would couple it to one user's own setup, got {parameters}"
    )


def test_work_kind_that_is_not_declared_fails_named_and_chooses_nothing():
    config = pool_config.load_pool_config(None)
    assert_named_error(
        lambda: pool_config.resolve_eligible_models(config, "deployment"),
        names="work kind 'deployment' is not declared in work_kinds",
        condition="an undeclared work kind must fail named rather than resolve a default pool",
    )


# ---------------------------------------------------------------------------
# reading a document
# ---------------------------------------------------------------------------


def test_unreadable_configuration_file_fails_named(tmp_path):
    missing = tmp_path / "absent.json"
    assert_named_error(
        lambda: pool_config.load_pool_config(missing),
        names=str(missing),
        condition="a configuration file that does not exist must fail named",
    )


def test_malformed_json_fails_named(tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text('{"version": 1,', encoding="utf-8")
    assert_named_error(
        lambda: pool_config.load_pool_config(broken),
        names="is not valid JSON",
        condition="a configuration file that is not valid JSON must fail named",
    )


def test_json_that_is_not_an_object_fails_named(tmp_path):
    listed = write_json(tmp_path / "listed.json", ["pools"])
    assert_named_error(
        lambda: pool_config.load_pool_config(listed),
        names="did not parse to a JSON object",
        condition="a configuration document that is not a JSON object must fail named",
    )


# ---------------------------------------------------------------------------
# document-level validation
# ---------------------------------------------------------------------------


def test_document_that_is_not_a_mapping_fails_named():
    assert_named_error(
        lambda: validate(["not", "a", "document"]),
        names="the configuration document must be a JSON object",
        condition="a non-object document must fail named",
    )


def test_missing_version_fails_named():
    document = base_document()
    del document["version"]
    assert_named_error(
        lambda: validate(document),
        names="is missing required field 'version'",
        condition="a document with no version must fail named",
    )


def test_non_integer_version_fails_named():
    document = base_document()
    document["version"] = True
    assert_named_error(
        lambda: validate(document),
        names="version must be an integer",
        condition="a non-integer version must fail named (a JSON true is an int in Python)",
    )


def test_unsupported_version_fails_named():
    document = base_document()
    document["version"] = 2
    assert_named_error(
        lambda: validate(document),
        names="version 2 is not supported",
        condition="a version this reader does not understand must fail named",
    )


def test_unknown_top_level_field_fails_named():
    document = base_document()
    document["work_kind"] = []
    assert_named_error(
        lambda: validate(document),
        names="has unknown field(s) ['work_kind']",
        condition="a mistyped top-level section must fail named rather than silently leave the defaults in place",
    )


def test_non_string_note_fails_named():
    document = base_document()
    document["note"] = 17
    assert_named_error(
        lambda: validate(document),
        names="note must be a string",
        condition="a non-string note must fail named",
    )


# ---------------------------------------------------------------------------
# pools
# ---------------------------------------------------------------------------


def test_missing_pools_section_fails_named():
    document = base_document()
    del document["pools"]
    assert_named_error(
        lambda: validate(document),
        names="is missing required field 'pools'",
        condition="a document with no pools section must fail named",
    )


def test_pools_section_of_the_wrong_type_fails_named():
    document = base_document()
    document["pools"] = {"big-a": "primary"}
    assert_named_error(
        lambda: validate(document),
        names="pools must be a JSON array",
        condition="a pools section that is not an array must fail named",
    )


def test_empty_pools_section_fails_named():
    document = base_document()
    document["pools"] = []
    assert_named_error(
        lambda: validate(document),
        names="pools must declare at least one entry",
        condition="an empty pools section must fail named",
    )


def test_pool_entry_that_is_not_an_object_fails_named():
    document = base_document()
    document["pools"][1] = "big-b"
    assert_named_error(
        lambda: validate(document),
        names="pools[1] must be a JSON object",
        condition="a pool entry that is not an object must fail named",
    )


def test_pool_with_an_empty_id_fails_named():
    document = base_document()
    document["pools"][0]["id"] = "   "
    assert_named_error(
        lambda: validate(document),
        names="pools[0].id must be a non-empty string",
        condition="a pool with a blank id must fail named",
    )


def test_pool_with_an_unknown_field_fails_named():
    document = base_document()
    document["pools"][0]["protected_headroom"] = 10
    assert_named_error(
        lambda: validate(document),
        names="has unknown field(s) ['protected_headroom']",
        condition="a mistyped pool field must fail named rather than be dropped",
    )


def test_duplicate_pool_id_fails_named():
    document = base_document()
    document["pools"].append({"id": "big-a", "tier": "secondary", "windows": ["7d"]})
    assert_named_error(
        lambda: validate(document),
        names="declares pool id 'big-a' a second time",
        condition="a pool id declared twice must fail named rather than let one entry win silently",
    )


def test_unknown_pool_tier_fails_named():
    document = base_document()
    document["pools"][2]["tier"] = "tertiary"
    assert_named_error(
        lambda: validate(document),
        names="tier must be one of ['primary', 'secondary']",
        condition="a tier outside primary/secondary must fail named",
    )


def test_pool_with_no_windows_field_fails_named():
    document = base_document()
    del document["pools"][0]["windows"]
    assert_named_error(
        lambda: validate(document),
        names="is missing required field 'windows'",
        condition="a pool that declares no windows must fail named",
    )


def test_pool_with_an_empty_windows_list_fails_named():
    document = base_document()
    document["pools"][0]["windows"] = []
    assert_named_error(
        lambda: validate(document),
        names="windows must declare at least one entry",
        condition="a pool with an empty windows list must fail named",
    )


def test_non_string_window_fails_named():
    document = base_document()
    document["pools"][0]["windows"] = ["5h", 7]
    assert_named_error(
        lambda: validate(document),
        names="windows[1] must be a non-empty string",
        condition="a window that is not a string must fail named",
    )


def test_duplicate_window_fails_named():
    document = base_document()
    document["pools"][0]["windows"] = ["7d", "7d"]
    assert_named_error(
        lambda: validate(document),
        names="declares window '7d' a second time",
        condition="a window declared twice must fail named",
    )


def test_no_primary_pool_at_all_fails_named():
    document = base_document()
    for pool in document["pools"]:
        pool["tier"] = "secondary"
    assert_named_error(
        lambda: validate(document),
        names="pools declares no primary pool",
        condition="a configuration with only secondary pools must fail named",
    )


# ---------------------------------------------------------------------------
# protected primary headroom
# ---------------------------------------------------------------------------


def test_protected_headroom_is_carried_on_a_primary_pool():
    document = base_document()
    document["pools"][0]["protected_headroom_percent"] = 20
    config = validate(document)
    assert config.pools_by_id["big-a"].protected_headroom_percent == 20


def test_protected_headroom_with_protect_policy_is_valid():
    document = base_document()
    document["pools"][0].update(policy="protect", protected_headroom_percent=20)
    assert validate(document).pools_by_id["big-a"].protected_headroom_percent == 20


@pytest.mark.parametrize("pool_index,policy", [(0, "spend-down"), (2, None)])
def test_protected_headroom_with_spend_down_fails_named(pool_index, policy):
    document = base_document()
    document["pools"][pool_index]["protected_headroom_percent"] = 10
    if policy is not None:
        document["pools"][pool_index]["policy"] = policy
    assert_named_error(
        lambda: validate(document), names="protected_headroom_percent requires policy",
        condition="spend-down policy must refuse protected headroom",
    )


def test_protected_headroom_of_the_wrong_type_fails_named():
    document = base_document()
    document["pools"][0]["protected_headroom_percent"] = "20%"
    assert_named_error(
        lambda: validate(document),
        names="protected_headroom_percent must be an integer percentage",
        condition="a non-integer protected amount must fail named",
    )


def test_protected_headroom_out_of_range_fails_named():
    document = base_document()
    document["pools"][0]["protected_headroom_percent"] = 140
    assert_named_error(
        lambda: validate(document),
        names="protected_headroom_percent must be between 0 and 100",
        condition="a protected amount outside 0-100 must fail named",
    )


def test_protected_headroom_on_a_secondary_pool_fails_named():
    document = base_document()
    document["pools"][2]["protected_headroom_percent"] = 10
    document["pools"][2]["policy"] = "protect"
    assert_named_error(
        lambda: validate(document),
        names="a protected amount may only be declared on a primary pool",
        condition="a protected amount on a secondary pool must fail named",
    )


# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------


def test_missing_models_section_fails_named():
    document = base_document()
    del document["models"]
    assert_named_error(
        lambda: validate(document),
        names="is missing required field 'models'",
        condition="a document with no models section must fail named",
    )


def test_model_entry_that_is_not_an_object_fails_named():
    document = base_document()
    document["models"][0] = "model-big-a"
    assert_named_error(
        lambda: validate(document),
        names="models[0] must be a JSON object",
        condition="a model entry that is not an object must fail named",
    )


def test_model_with_an_unknown_field_fails_named():
    document = base_document()
    document["models"][0]["provider"] = "big-a"
    assert_named_error(
        lambda: validate(document),
        names="has unknown field(s) ['provider']",
        condition="a mistyped model field must fail named",
    )


def test_duplicate_model_id_fails_named():
    document = base_document()
    document["models"].append({"id": "model-big-a", "pool": "big-b"})
    assert_named_error(
        lambda: validate(document),
        names="declares model id 'model-big-a' a second time",
        condition="a model id declared twice must fail named rather than resolve to whichever pool wins",
    )


def test_model_in_an_undeclared_pool_fails_named():
    document = base_document()
    document["models"][0]["pool"] = "big-c"
    assert_named_error(
        lambda: validate(document),
        names="names the undeclared pool 'big-c'",
        condition="a model pointing at a pool that is not declared must fail named",
    )


def test_model_with_no_pool_fails_named():
    document = base_document()
    del document["models"][0]["pool"]
    assert_named_error(
        lambda: validate(document),
        names="is missing required field 'pool'",
        condition="a model that names no pool must fail named rather than be assigned one",
    )


# ---------------------------------------------------------------------------
# work kinds
# ---------------------------------------------------------------------------


def test_missing_work_kinds_section_fails_named():
    document = base_document()
    del document["work_kinds"]
    assert_named_error(
        lambda: validate(document),
        names="is missing required field 'work_kinds'",
        condition="a document with no work_kinds section must fail named",
    )


def test_work_kind_entry_that_is_not_an_object_fails_named():
    document = base_document()
    document["work_kinds"][0] = "build"
    assert_named_error(
        lambda: validate(document),
        names="work_kinds[0] must be a JSON object",
        condition="a work kind entry that is not an object must fail named",
    )


def test_work_kind_with_an_unknown_field_fails_named():
    document = base_document()
    document["work_kinds"][0]["preferred"] = ["model-big-a"]
    assert_named_error(
        lambda: validate(document),
        names="has unknown field(s) ['preferred']",
        condition="a mistyped work kind field must fail named",
    )


def test_duplicate_work_kind_fails_named():
    document = base_document()
    document["work_kinds"].append({"id": "build", "models": ["model-big-a"]})
    assert_named_error(
        lambda: validate(document),
        names="declares work kind 'build' a second time",
        condition="a work kind declared twice must fail named rather than resolve to whichever entry wins",
    )


def test_work_kind_with_no_models_field_fails_named():
    document = base_document()
    del document["work_kinds"][0]["models"]
    assert_named_error(
        lambda: validate(document),
        names="is missing required field 'models'",
        condition="a work kind that names no models must fail named",
    )


def test_work_kind_with_an_empty_model_list_fails_named():
    document = base_document()
    document["work_kinds"][0]["models"] = []
    assert_named_error(
        lambda: validate(document),
        names="models must declare at least one entry",
        condition="a work kind with an empty model list must fail named",
    )


def test_non_string_model_reference_fails_named():
    document = base_document()
    document["work_kinds"][0]["models"] = [None, "model-big-a"]
    assert_named_error(
        lambda: validate(document),
        names="models[0] must be a non-empty string",
        condition="a work kind model reference that is not a string must fail named",
    )


def test_model_listed_twice_in_one_work_kind_fails_named():
    document = base_document()
    document["work_kinds"][1]["models"] = ["model-big-a", "model-big-a"]
    assert_named_error(
        lambda: validate(document),
        names="declares model 'model-big-a' a second time",
        condition="a model listed twice in one work kind must fail named",
    )


# ---------------------------------------------------------------------------
# disabled pools
# ---------------------------------------------------------------------------


def test_disabled_pool_removes_its_models_from_every_order():
    document = base_document()
    document["disabled_pools"] = ["small-a"]
    config = validate(document)
    assert pool_config.resolve_eligible_models(config, "build") == (
        "model-big-b",
        "model-big-a",
    ), "a disabled pool's models must drop out while the declared order of the rest holds"


def test_disabled_pools_of_the_wrong_type_fails_named():
    document = base_document()
    document["disabled_pools"] = "small-a"
    assert_named_error(
        lambda: validate(document),
        names="disabled_pools must be a JSON array",
        condition="a disabled_pools value that is not an array must fail named",
    )


def test_non_string_disabled_pool_fails_named():
    document = base_document()
    document["disabled_pools"] = [3]
    assert_named_error(
        lambda: validate(document),
        names="disabled_pools[0] must be a non-empty string",
        condition="a disabled pool that is not a string must fail named",
    )


def test_disabling_an_undeclared_pool_fails_named():
    document = base_document()
    document["disabled_pools"] = ["small-c"]
    assert_named_error(
        lambda: validate(document),
        names="names the undeclared pool 'small-c'",
        condition="disabling a pool that is not declared must fail named rather than be ignored",
    )


def test_pool_disabled_twice_fails_named():
    document = base_document()
    document["disabled_pools"] = ["small-a", "small-a"]
    assert_named_error(
        lambda: validate(document),
        names="declares pool id 'small-a' a second time",
        condition="a pool disabled twice must fail named",
    )


# ---------------------------------------------------------------------------
# the fallback-direction rules
# ---------------------------------------------------------------------------


def test_work_kind_drawing_on_two_secondary_pools_fails_named():
    document = base_document()
    document["work_kinds"][0]["models"] = [
        "model-small-a",
        "model-small-b",
        "model-big-a",
    ]
    assert_named_error(
        lambda: validate(document),
        names="a secondary pool never covers for another secondary pool",
        condition="a fallback chain running from one secondary pool into another must fail named",
    )


def test_two_models_of_the_same_secondary_pool_are_allowed():
    document = base_document()
    document["models"].append({"id": "model-small-a-2", "pool": "small-a"})
    document["work_kinds"][0]["models"] = [
        "model-small-a",
        "model-small-a-2",
        "model-big-a",
    ]
    config = validate(document)
    assert pool_config.resolve_eligible_models(config, "build") == (
        "model-small-a",
        "model-small-a-2",
        "model-big-a",
    ), "two models of ONE secondary pool are one pool, not a small-to-small fallback"


def test_work_kind_with_no_primary_lane_fails_named():
    document = base_document()
    document["work_kinds"][0]["models"] = ["model-small-a"]
    assert_named_error(
        lambda: validate(document),
        names="with no primary-pool model",
        condition="a work kind with no primary-pool lane must fail named",
    )


def test_work_kind_left_with_no_primary_lane_after_disabling_fails_named():
    document = base_document()
    document["work_kinds"][0]["models"] = ["model-small-a", "model-big-a"]
    document["disabled_pools"] = ["big-a"]
    assert_named_error(
        lambda: validate(document),
        names="with no primary-pool model",
        condition="disabling a pool that was a work kind's only primary lane must fail named",
    )


def test_work_kind_emptied_by_disabling_fails_named():
    document = base_document()
    document["work_kinds"][0]["models"] = ["model-small-a"]
    document["work_kinds"][0]["models"] = ["model-small-a", "model-big-a"]
    document["disabled_pools"] = ["small-a", "big-a"]
    document["work_kinds"][1]["models"] = ["model-big-b"]
    assert_named_error(
        lambda: validate(document),
        names="has no model left once disabled_pools",
        condition="a work kind every model of which sits in a disabled pool must fail named",
    )


# ---------------------------------------------------------------------------
# merge semantics
# ---------------------------------------------------------------------------


def test_merge_replaces_a_matching_entry_whole_and_keeps_its_position():
    base = base_document()
    merged = pool_config.merge_documents(
        base,
        {"pools": [{"id": "big-b", "tier": "secondary", "windows": ["monthly"]}]},
    )
    assert [pool["id"] for pool in merged["pools"]] == [
        "big-a",
        "big-b",
        "small-a",
        "small-b",
    ], "an overriding entry must keep the position of the entry it replaces"
    assert merged["pools"][1] == {
        "id": "big-b",
        "tier": "secondary",
        "windows": ["monthly"],
    }, "an overriding entry replaces the shipped entry whole, never field by field"


@pytest.mark.parametrize(
    ("section", "entries", "error_name"),
    [
        (
            "pools",
            [
                {"id": "big-b", "tier": "primary", "windows": ["5h"]},
                {"id": "big-b", "tier": "primary", "windows": ["monthly"]},
            ],
            "declares pool id 'big-b' a second time",
        ),
        (
            "models",
            [
                {"id": "model-big-b", "pool": "big-a"},
                {"id": "model-big-b", "pool": "big-b"},
            ],
            "declares model id 'model-big-b' a second time",
        ),
        (
            "work_kinds",
            [
                {"id": "review", "models": ["model-big-b"]},
                {"id": "review", "models": ["model-big-a"]},
            ],
            "declares work kind 'review' a second time",
        ),
    ],
)
def test_merge_with_duplicate_override_ids_fails_named(
    tmp_path, section, entries, error_name
):
    defaults_path = write_json(tmp_path / "defaults.json", base_document())
    override_path = write_json(tmp_path / "override.json", {section: entries})
    assert_named_error(
        lambda: pool_config.load_pool_config(
            override_path, defaults_path=defaults_path
        ),
        names=error_name,
        condition=(
            f"duplicate ids in the {section} override array must fail named "
            "rather than let one declaration win silently"
        ),
    )


def test_merge_appends_an_entry_with_a_new_id():
    base = base_document()
    merged = pool_config.merge_documents(
        base, {"pools": [{"id": "small-c", "tier": "secondary", "windows": ["7d"]}]}
    )
    assert [pool["id"] for pool in merged["pools"]][-1] == "small-c", (
        "an entry with an id the defaults do not carry must append"
    )


def test_merge_replaces_a_non_section_key_outright():
    base = base_document()
    base["disabled_pools"] = ["small-a"]
    merged = pool_config.merge_documents(base, {"disabled_pools": ["small-b"]})
    assert merged["disabled_pools"] == ["small-b"], (
        "a top-level key that is not an entry section is replaced, not merged"
    )


def test_merge_leaves_untouched_sections_alone():
    base = base_document()
    merged = pool_config.merge_documents(base, {"disabled_pools": []})
    assert merged["pools"] == base["pools"]
    assert merged["models"] == base["models"]
    assert merged["work_kinds"] == base["work_kinds"]


# ---------------------------------------------------------------------------
# the command line
# ---------------------------------------------------------------------------


def test_cli_prints_the_eligible_order_one_per_line(capsys):
    exit_code = pool_config.main(["--work-kind", "mechanical_build"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out.splitlines() == [
        "xai-oauth/grok-4.6",
        "openai-codex/gpt-6.1-sol",
        "claude-sonnet-5-5",
    ], f"the command line must print the eligible order, most preferred first: {captured.out!r}"


def test_cli_applies_a_user_configuration(capsys):
    exit_code = pool_config.main(
        ["--work-kind", "mechanical_build", "--config", str(FIXTURES / "override.json")]
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out.splitlines() == [
        "openai-codex/gpt-6.1-sol",
        "claude-sonnet-5-5",
    ], f"--config must be merged over the shipped defaults: {captured.out!r}"


def test_cli_refuses_an_invalid_configuration_with_a_named_error(capsys):
    exit_code = pool_config.main(
        ["--work-kind", "docs", "--config", str(FIXTURES / "missing-field.json")]
    )
    captured = capsys.readouterr()
    assert exit_code == 2, (
        "an invalid configuration must exit non-zero rather than print an order"
    )
    assert "is missing required field 'tier'" in captured.err, (
        f"the command line must pass the named error through: {captured.err!r}"
    )
    assert captured.out == "", (
        f"nothing may be resolved from an invalid configuration: {captured.out!r}"
    )


def test_cli_refuses_an_undeclared_work_kind(capsys):
    exit_code = pool_config.main(["--work-kind", "deployment"])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "is not declared in work_kinds" in captured.err
    assert captured.out == ""


def test_cli_requires_a_work_kind():
    with pytest.raises(SystemExit) as raised:
        pool_config.main([])
    assert raised.value.code == 2


def test_a_retired_pool_is_refused_before_an_unknown_top_level_field(tmp_path):
    override = _retired_override(
        tmp_path, {"disabled_pools": ["opencode-go"], "unrelated_field": True}
    )
    assert_named_error(
        lambda: pool_config.load_pool_config(override),
        names=RETIRED_POOL_REFUSAL,
        condition="a configuration with both a retired pool and an unknown top-level field",
    )
