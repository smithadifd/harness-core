"""Put ``skills/_lib`` on ``sys.path`` and provide the shared recall fixtures.

Every transcript here is invented. Nothing reads the real home directory: the
tests point ``HOME`` at a temporary folder, and library calls take ``home``
explicitly.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parents[2]
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from recall_fixtures import Recorder  # noqa: E402


@pytest.fixture
def recorder(monkeypatch) -> Recorder:
    return Recorder(monkeypatch)


@pytest.fixture
def home(tmp_path) -> Path:
    path = tmp_path / "home"
    (path / ".claude" / "projects").mkdir(parents=True)
    (path / ".omp" / "agent" / "sessions").mkdir(parents=True)
    (path / "workspaces" / "home").mkdir(parents=True)
    (path / "code" / "demo").mkdir(parents=True)
    return path
