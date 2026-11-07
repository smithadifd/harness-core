"""A malformed or missing skill config degrades with one diagnostic."""

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "config_path.py"
spec = importlib.util.spec_from_file_location("config_path_under_test", MODULE_PATH)
assert spec is not None and spec.loader is not None
config_path = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config_path)


def assert_degrades_once(path, capsys, reason):
    assert config_path.load_config(path) == {}, f"{reason}: config must degrade to {{}}"
    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == 1, f"{reason}: expected one stderr line, got {lines!r}"
    assert str(path) in lines[0], f"{reason}: diagnostic must name the config path"
    assert reason in lines[0], f"{reason}: diagnostic must explain the failure"


def test_invalid_utf8_degrades_once(tmp_path, capsys):
    path = tmp_path / "config.json"
    path.write_bytes(b"\xff")
    assert_degrades_once(path, capsys, "could not read")


def test_missing_file_degrades_once(tmp_path, capsys):
    assert_degrades_once(tmp_path / "missing.json", capsys, "could not read")


def test_malformed_json_degrades_once(tmp_path, capsys):
    path = tmp_path / "config.json"
    path.write_text("{", encoding="utf-8")
    assert_degrades_once(path, capsys, "not valid JSON")
