"""Pin the shipped pool and window tables for the Fable 5.2 registration."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "skills" / "_lib"
POOL_DEFAULTS = LIB / "pool-defaults.json"
MODEL_WINDOWS = LIB / "model-windows.tsv"
FABLE_52 = "claude-fable-5-2"
CLAUDE_POOL = "claude"
FABLE_52_WINDOW = 1000000


def _pool_config_module():
    path = LIB / "pool_config.py"
    spec = importlib.util.spec_from_file_location("core_model_registry_pool_config", path)
    if spec is None or spec.loader is None:
        raise AssertionError("cannot load pool_config from %s" % path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _shipped_config():
    assert POOL_DEFAULTS.is_file(), "missing shipped pool defaults: %s" % POOL_DEFAULTS
    return _pool_config_module().load_pool_config(None, defaults_path=POOL_DEFAULTS)


def _model_rows():
    """Parse model-windows.tsv: # comments skipped; model<TAB>id<TAB>window or suffix<TAB>suffix<TAB>window."""
    assert MODEL_WINDOWS.is_file(), "missing shipped model windows: %s" % MODEL_WINDOWS
    rows = {}
    for number, line in enumerate(MODEL_WINDOWS.read_text(encoding="utf-8").splitlines(), start=1):
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != 3:
            raise AssertionError("%s:%s: unparsable row %r" % (MODEL_WINDOWS, number, line))
        kind, key, window = fields
        if kind not in ("model", "suffix") or not window.isdigit():
            raise AssertionError("%s:%s: unparsable row %r" % (MODEL_WINDOWS, number, line))
        if kind != "model":
            continue
        if not key:
            raise AssertionError("%s:%s: missing id in row %r" % (MODEL_WINDOWS, number, line))
        if key in rows:
            raise AssertionError("%s:%s: duplicate model %r" % (MODEL_WINDOWS, number, key))
        rows[key] = int(window)
    return rows


def test_shipped_pool_declares_fable_52_in_the_claude_pool():
    config = _shipped_config()
    assert FABLE_52 in config.models_by_id, "%s is not a declared model" % FABLE_52
    assert config.models_by_id[FABLE_52].pool == CLAUDE_POOL, (
        "%s pool is %r, not %r" % (FABLE_52, config.models_by_id[FABLE_52].pool, CLAUDE_POOL)
    )


def test_shipped_windows_give_fable_52_a_million_token_window():
    rows = _model_rows()
    assert FABLE_52 in rows, "%s has no model row in model-windows.tsv" % FABLE_52
    assert rows[FABLE_52] == FABLE_52_WINDOW, (
        "%s window is %s, not %s" % (FABLE_52, rows[FABLE_52], FABLE_52_WINDOW)
    )


def test_every_claude_pool_model_has_a_window_row():
    config = _shipped_config()
    rows = _model_rows()
    missing = [model.id for model in config.models if model.pool == CLAUDE_POOL and model.id not in rows]
    assert missing == [], "claude-pool models with no window row: %s" % missing
