"""Tests for the reduced-assurance label in `skills/_lib/pool_config.py`.

A row is labelled when no review model outside the builder's family can run,
or when the user's configuration ranks a model above the shipped order for the
row's work kind. The label never blocks: the row still resolves a builder.

The cases that judge reviewers through the toolkit's
`scripts/model-selection.sh`, or run `scripts/row_assurance.py` and
`scripts/work_kind_model.py`, are in test_pool_config_assurance_toolkit.py.
"""

import importlib.util
import shutil
import sys
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location("pool_config_assurance", LIB_DIR / "pool_config.py")
pool_config = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pool_config
spec.loader.exec_module(pool_config)

BUILDER = "claude-opus-5-5"


# ---------------------------------------------------------------------------
# family comes from the model name, never from the route that serves it
# ---------------------------------------------------------------------------

import pytest


@pytest.mark.parametrize(
    "model_id, family",
    [
        ("claude-opus-5-5", "anthropic"),
        ("openai-codex/gpt-5.6-sol", "openai"),
        ("gpt-5.6-sol", "openai"),
        ("o3-mini", "openai"),
        ("xai-oauth/grok-4.6", "xai"),
        ("google-antigravity/gemini-3.8-flash", "google"),
        ("gateway/glm-5.3-flash", "zhipu"),
        ("gateway/kimi-k2.7-code", "moonshot"),
        ("gateway/deepseek-v4-pro", "deepseek"),
        ("google-antigravity/claude-opus-5-5", "anthropic"),
        ("gateway/claude-sonnet-5-5", "anthropic"),
        ("Claude-Opus-5-5", "anthropic"),
        ("GPT-5.6", "openai"),
        ("O3", "openai"),
        ("us.anthropic.claude-opus-5-5", "anthropic"),
        ("bedrock/anthropic.claude-opus-5-5", "anthropic"),
        ("chatgpt-4o", "openai"),
        ("codex-mini", "openai"),
        ("qwen3.5:32b", "unknown"),
        ("ollama/mystery", "unknown"),
        ("omega-1", "unknown"),
    ],
)
def test_family_is_read_from_the_model_name(model_id, family):
    assert pool_config.model_family(model_id) == family, (
        f"{model_id} should belong to the {family} family"
    )


def _guard_script(tmp_path, name, returncode):
    script = tmp_path / name
    script.write_text(f"model_selection_check() {{ return {returncode}; }}\n", encoding="utf-8")
    return script


def test_row_assurance_runs_the_caller_supplied_guard(tmp_path):
    config = pool_config.load_pool_config(None)
    rejecting = _guard_script(tmp_path, "reject.sh", 2)
    accepting = _guard_script(tmp_path, "accept.sh", 0)

    refused = pool_config.row_assurance(
        config, config, "review", BUILDER, model_selection_guard=rejecting
    )
    allowed = pool_config.row_assurance(
        config, config, "review", BUILDER, model_selection_guard=accepting
    )

    assert refused.reduced and "no review model outside" in refused.reason, (
        "a guard that refuses every model must leave no outside reviewer"
    )
    assert not allowed.reduced, "a guard that accepts every model must leave an outside reviewer"


def test_row_assurance_requires_a_guard_path():
    config = pool_config.load_pool_config(None)
    with pytest.raises(pool_config.ModelSelectionGuardUnsetError, match="model_selection_guard"):
        pool_config.row_assurance(config, config, "review", BUILDER)


def test_pool_config_copied_without_a_toolkit_above_it_still_works(tmp_path):
    lib = tmp_path / "isolated" / "lib"
    lib.mkdir(parents=True)
    for name in ("pool_config.py", "overrides.py", "pool-defaults.json"):
        shutil.copy(LIB_DIR / name, lib / name)
    scratch_spec = importlib.util.spec_from_file_location(
        "pool_config_scratch_copy", lib / "pool_config.py"
    )
    scratch = importlib.util.module_from_spec(scratch_spec)
    sys.modules[scratch_spec.name] = scratch
    scratch_spec.loader.exec_module(scratch)
    config = scratch.load_pool_config(None)

    result = scratch.row_assurance(
        config, config, "review", BUILDER,
        model_selection_guard=_guard_script(tmp_path, "accept.sh", 0),
    )

    assert not result.reduced, "the copied module ignored the caller-supplied guard"
    with pytest.raises(scratch.ModelSelectionGuardUnsetError):
        scratch.row_assurance(config, config, "review", BUILDER)
