"""Tests for the provider billing shape in `skills/_lib/pool_config.py`.

Every pool declares `billing`: `quota` (windows, policy, headroom: today's
shape) or `api` (dollars, no windows, an optional ceiling and, per model, a
dated price). The pick on an `api` pool (the cheapest qualified model at or
under the ceiling) is the toolkit's `scripts/work_kind_model.py`; its cases
are in test_pool_config_billing_toolkit.py.

Every document here is a scratch copy written under `tmp_path` and passed
through the existing `defaults_path` / `override_path` parameters; nothing
reads a per-user override file.

Stdlib + pytest only. Offline by construction.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

LIB_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = LIB_DIR / "pool_config.py"
SHIPPED_DEFAULTS = LIB_DIR / "pool-defaults.json"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


pool_config = _load("pool_config_billing_under_test", MODULE_PATH)
PoolConfigError = pool_config.PoolConfigError


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def price(amount, as_of="2026-10-21"):
    return {"usd_per_mtok": amount, "as_of": as_of}


def api_document():
    """One quota pool and one api pool (ceiling 10). Declared prices in the
    api pool: pricey 8, mid 5, cheap 3 — the declared order is NOT the price
    order, so a pick that takes the first model is distinguishable."""
    return {
        "version": 1,
        "pools": [
            {"id": "home", "tier": "primary", "billing": "quota", "windows": ["7d"]},
            {"id": "work-api", "tier": "primary", "billing": "api", "ceiling": 10},
        ],
        "models": [
            {"id": "claude-home", "pool": "home"},
            {"id": "claude-pricey", "pool": "work-api", "price": price(8)},
            {"id": "claude-mid", "pool": "work-api", "price": price(5)},
            {"id": "claude-cheap", "pool": "work-api", "price": price(3)},
        ],
        "work_kinds": [
            {"id": "api_job", "models": ["claude-pricey", "claude-mid", "claude-cheap"]},
            {"id": "quota_job", "models": ["claude-home", "claude-cheap"]},
            {"id": "mixed_job", "models": ["claude-pricey", "claude-home", "claude-cheap"]},
        ],
    }


def write_json(path, document):
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def validate(document):
    return pool_config.validate_document(document)


def assert_named_error(call, *, names, condition):
    try:
        call()
    except PoolConfigError as exc:
        message = str(exc)
        assert names in message, (
            f"{condition}: the error did not name {names!r}, got {message!r}"
        )
        return message
    raise AssertionError(
        f"{condition}: expected PoolConfigError naming {names!r}, but the "
        "configuration was accepted"
    )


# ---------------------------------------------------------------------------
# shipped defaults
# ---------------------------------------------------------------------------


def test_every_shipped_pool_declares_a_billing_shape():
    document = json.loads(SHIPPED_DEFAULTS.read_text(encoding="utf-8"))
    assert document["pools"], "the shipped defaults declare no pools"
    for pool in document["pools"]:
        assert pool.get("billing") in ("quota", "api"), (
            f"shipped pool {pool['id']!r} does not declare billing quota|api"
        )


def test_shipped_pools_are_all_quota_and_keep_their_windows():
    config = pool_config.load_pool_config(None)
    assert {pool.id: pool.billing for pool in config.pools} == {
        pool.id: "quota" for pool in config.pools
    }
    assert all(pool.windows for pool in config.pools)


# ---------------------------------------------------------------------------
# validator: billing
# ---------------------------------------------------------------------------


def test_pool_with_no_billing_is_refused_and_names_pool_and_field():
    document = api_document()
    del document["pools"][0]["billing"]
    message = assert_named_error(
        lambda: validate(document),
        names="missing required field 'billing'",
        condition="a pool with no billing field must be refused",
    )
    assert "'home'" in message, f"the refusal must name the pool: {message!r}"


def test_unknown_billing_is_refused_and_names_pool_and_field():
    document = api_document()
    document["pools"][0]["billing"] = "metered"
    message = assert_named_error(
        lambda: validate(document),
        names="billing must be one of",
        condition="a billing other than quota|api must be refused",
    )
    assert "'home'" in message and "'metered'" in message, message


def test_non_string_billing_is_refused():
    document = api_document()
    document["pools"][0]["billing"] = ["api"]
    assert_named_error(
        lambda: validate(document),
        names="billing must be a non-empty string",
        condition="a non-string billing must be refused",
    )


def test_api_pool_with_windows_is_refused_and_names_pool_and_field():
    document = api_document()
    document["pools"][1]["windows"] = ["7d"]
    message = assert_named_error(
        lambda: validate(document),
        names=".windows is a quota-pool field",
        condition="an api pool with windows must be refused",
    )
    assert "'work-api'" in message, f"the refusal must name the pool: {message!r}"


@pytest.mark.parametrize(
    ("field", "value"),
    [("policy", "pace"), ("protected_headroom_percent", 10)],
)
def test_api_pool_with_any_other_quota_only_field_is_refused(field, value):
    document = api_document()
    document["pools"][1][field] = value
    assert_named_error(
        lambda: validate(document),
        names=f".{field} is a quota-pool field",
        condition=f"an api pool carrying {field} must be refused",
    )


def test_quota_pool_with_a_ceiling_is_refused():
    document = api_document()
    document["pools"][0]["ceiling"] = 10
    message = assert_named_error(
        lambda: validate(document),
        names=".ceiling is an api-pool field",
        condition="a quota pool with a ceiling must be refused",
    )
    assert "'home'" in message, message


def test_valid_api_pool_carries_no_windows_and_a_ceiling():
    config = validate(api_document())
    pool = config.pools_by_id["work-api"]
    assert (pool.billing, pool.windows, pool.ceiling) == ("api", (), 10.0)
    assert config.pools_by_id["home"].billing == "quota"
    assert config.models_by_id["claude-cheap"].price.usd_per_mtok == 3.0
    assert config.models_by_id["claude-cheap"].price.as_of.isoformat() == "2026-10-21"


def test_api_pool_ceiling_is_optional():
    document = api_document()
    del document["pools"][1]["ceiling"]
    assert validate(document).pools_by_id["work-api"].ceiling is None


@pytest.mark.parametrize("value", [-1, -0.01, True, "10", None, float("inf")])
def test_invalid_ceiling_is_refused(value):
    document = api_document()
    document["pools"][1]["ceiling"] = value
    assert_named_error(
        lambda: validate(document),
        names="ceiling must be a non-negative number",
        condition=f"ceiling {value!r} must be refused",
    )


def test_api_pool_policy_override_is_refused():
    document = api_document()
    document["overrides"] = [
        {"pool": "work-api", "policy": "protect", "until": "2026-12-01", "source": "x"}
    ]
    assert_named_error(
        lambda: validate(document),
        names="names the api pool 'work-api'",
        condition="a dated policy override on an api pool must be refused",
    )


def test_effective_policy_of_an_api_pool_is_none():
    config = validate(api_document())
    from datetime import date

    assert pool_config.effective_policy(config, "work-api", date(2026, 10, 21)).policy == "none"


# ---------------------------------------------------------------------------
# validator: price
# ---------------------------------------------------------------------------


def _model_with_price(value):
    document = api_document()
    document["models"][3]["price"] = value
    return document


def test_price_without_a_date_is_refused():
    message = assert_named_error(
        lambda: validate(_model_with_price({"usd_per_mtok": 3})),
        names="price is missing required field 'as_of'",
        condition="an undated price must be refused",
    )
    assert "'claude-cheap'" in message, f"the refusal must name the model: {message!r}"


@pytest.mark.parametrize("as_of", ["", "2026-13-40", "21/10/2026", "2026-1-5", 20261021, None])
def test_price_with_a_malformed_date_is_refused(as_of):
    assert_named_error(
        lambda: validate(_model_with_price(price(3, as_of))),
        names="price.as_of must be an ISO date",
        condition=f"price date {as_of!r} must be refused",
    )


@pytest.mark.parametrize("amount", [-1, -0.5, "3", True, None, float("nan")])
def test_price_that_is_not_a_non_negative_number_is_refused(amount):
    assert_named_error(
        lambda: validate(_model_with_price(price(amount))),
        names="price.usd_per_mtok must be a non-negative number",
        condition=f"price amount {amount!r} must be refused",
    )


def test_price_of_zero_is_allowed():
    assert validate(_model_with_price(price(0))).models_by_id["claude-cheap"].price.usd_per_mtok == 0.0


@pytest.mark.parametrize("huge", [10**1000, -(10**1000)], ids=["positive", "negative"])
def test_huge_integer_price_raises_pool_config_error_not_overflow(huge):
    assert_named_error(
        lambda: validate(_model_with_price(price(huge))),
        names="price.usd_per_mtok must be a non-negative number",
        condition="a huge integer price must raise PoolConfigError, not OverflowError",
    )


@pytest.mark.parametrize("huge", [10**1000, -(10**1000)], ids=["positive", "negative"])
def test_huge_integer_ceiling_raises_pool_config_error_not_overflow(huge):
    document = api_document()
    document["pools"][1]["ceiling"] = huge
    assert_named_error(
        lambda: validate(document),
        names="ceiling must be a non-negative number",
        condition="a huge integer ceiling must raise PoolConfigError, not OverflowError",
    )


@pytest.mark.parametrize("huge", [10**5000, -(10**5000)], ids=["positive", "negative"])
def test_integer_past_the_decimal_conversion_limit_as_price_is_refused_by_name(huge):
    # 10**5000 has more digits than Python will convert to a str (4300 by
    # default); the refusal message must not stringify it.
    message = assert_named_error(
        lambda: validate(_model_with_price(price(huge))),
        names="price.usd_per_mtok must be a non-negative number",
        condition="an integer past the decimal-conversion limit as a price must raise PoolConfigError, not ValueError",
    )
    assert "'claude-cheap'" in message, f"the refusal must name the model: {message!r}"


@pytest.mark.parametrize("huge", [10**5000, -(10**5000)], ids=["positive", "negative"])
def test_integer_past_the_decimal_conversion_limit_as_ceiling_is_refused_by_name(huge):
    document = api_document()
    document["pools"][1]["ceiling"] = huge
    message = assert_named_error(
        lambda: validate(document),
        names="ceiling must be a non-negative number",
        condition="an integer past the decimal-conversion limit as a ceiling must raise PoolConfigError, not ValueError",
    )
    assert "'work-api'" in message, f"the refusal must name the pool: {message!r}"


def test_price_that_is_a_bare_number_is_refused():
    assert_named_error(
        lambda: validate(_model_with_price(3)),
        names="price must be a JSON object",
        condition="a price with no date object must be refused",
    )


def test_price_with_an_unknown_key_is_refused():
    assert_named_error(
        lambda: validate(_model_with_price({**price(3), "currency": "usd"})),
        names="unknown field",
        condition="an unknown price key must be refused",
    )


def test_api_model_with_no_price_is_refused():
    document = api_document()
    del document["models"][3]["price"]
    message = assert_named_error(
        lambda: validate(document),
        names="missing required field 'price'",
        condition="a model in an api pool with no price must be refused",
    )
    assert "'work-api'" in message and "'claude-cheap'" in message, message


def test_quota_model_with_a_price_is_refused():
    document = api_document()
    document["models"][0]["price"] = price(1)
    assert_named_error(
        lambda: validate(document),
        names="price is set on a model in the quota pool 'home'",
        condition="a price on a quota-pool model must be refused",
    )


# ---------------------------------------------------------------------------
# a per-user override that omits billing (compatibility)
# ---------------------------------------------------------------------------


def test_override_naming_a_default_pool_without_billing_still_validates(tmp_path):
    defaults = write_json(tmp_path / "defaults.json", api_document())
    override = write_json(
        tmp_path / "override.json",
        {"pools": [{"id": "home", "tier": "primary", "windows": ["7d", "5h"]}]},
    )
    config = pool_config.load_pool_config(override, defaults_path=defaults)
    home = config.pools_by_id["home"]
    assert home.billing == "quota", "the merged pool must inherit billing from the defaults"
    assert home.windows == ("7d", "5h"), "the override's own fields still replace the entry"


def test_override_naming_an_api_default_pool_without_billing_inherits_api(tmp_path):
    defaults = write_json(tmp_path / "defaults.json", api_document())
    override = write_json(
        tmp_path / "override.json",
        {"pools": [{"id": "work-api", "tier": "primary", "ceiling": 4}]},
    )
    config = pool_config.load_pool_config(override, defaults_path=defaults)
    pool = config.pools_by_id["work-api"]
    assert (pool.billing, pool.ceiling) == ("api", 4.0)


def test_inherited_api_billing_still_refuses_windows_in_the_override(tmp_path):
    defaults = write_json(tmp_path / "defaults.json", api_document())
    override = write_json(
        tmp_path / "override.json",
        {"pools": [{"id": "work-api", "tier": "primary", "windows": ["7d"]}]},
    )
    assert_named_error(
        lambda: pool_config.load_pool_config(override, defaults_path=defaults),
        names="'work-api'",
        condition="an override giving windows to an inherited api pool must be refused",
    )


def test_override_may_still_restate_billing_to_change_the_shape(tmp_path):
    defaults = write_json(tmp_path / "defaults.json", api_document())
    override = write_json(
        tmp_path / "override.json",
        {"pools": [{"id": "home", "tier": "primary", "billing": "quota", "windows": ["5h"]}]},
    )
    config = pool_config.load_pool_config(override, defaults_path=defaults)
    assert config.pools_by_id["home"].windows == ("5h",)


def test_new_pool_in_an_override_must_declare_billing(tmp_path):
    defaults = write_json(tmp_path / "defaults.json", api_document())
    override = write_json(
        tmp_path / "override.json",
        {"pools": [{"id": "brand-new", "tier": "secondary", "windows": ["7d"]}]},
    )
    message = assert_named_error(
        lambda: pool_config.load_pool_config(override, defaults_path=defaults),
        names="missing required field 'billing'",
        condition="a new pool with no billing in the merged document must be refused",
    )
    assert "'brand-new'" in message, message


def test_merged_document_with_a_pool_missing_billing_in_the_defaults_is_refused(tmp_path):
    document = api_document()
    del document["pools"][0]["billing"]
    defaults = write_json(tmp_path / "defaults.json", document)
    override = write_json(tmp_path / "override.json", {"version": 1})
    assert_named_error(
        lambda: pool_config.load_pool_config(override, defaults_path=defaults),
        names="missing required field 'billing'",
        condition="defaults with no billing must be refused after the merge",
    )


def test_merge_documents_itself_still_replaces_the_entry_whole():
    merged = pool_config.merge_documents(
        api_document(),
        {"pools": [{"id": "home", "tier": "primary", "windows": ["5h"]}]},
    )
    assert merged["pools"][0] == {"id": "home", "tier": "primary", "windows": ["5h"]}


# ---------------------------------------------------------------------------
# the pick on an api pool
# ---------------------------------------------------------------------------


# ---- the same pick, through the CLI --------------------------------------------


# ---- quota regression ----------------------------------------------------------
