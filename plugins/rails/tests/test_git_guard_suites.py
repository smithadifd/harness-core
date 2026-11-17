"""The rails plugin's git guard suites, run under pytest.

Each shell suite runs as ``/bin/bash <suite>`` with only a fresh HOME under
pytest's temporary folder, ``LC_ALL=C`` and the job's PATH in its
environment, and a 600-second timeout. A suite passes when it exits 0, prints
at least one ``PASS`` line and no line that starts with ``FAIL``. The suite
list is pinned here, so a suite that is deleted or renamed fails instead of
vanishing.

The plugin's manifest and hooks file are checked in test_rails_manifest.py.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
SUITES = [
    "git-guard-carriers.test.sh",
    "git-guard-commit.test.sh",
    "git-guard-dependencies.test.sh",
    "git-guard-never-allow.test.sh",
    "git-guard-options.test.sh",
    "git-guard-pins.test.sh",
    "git-guard-push.test.sh",
    "git-guard-reasons.test.sh",
    "git-guard-scope.test.sh",
    "git-guard-target.test.sh",
    "git-guard-unattended.test.sh",
]


def test_the_suite_list_is_exactly_the_suites_on_disk():
    found = sorted(path.name for path in TESTS.glob("git-guard*.test.sh"))
    assert found == sorted(SUITES)


@pytest.mark.parametrize("suite", SUITES)
def test_suite_passes(suite, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    env = {"HOME": str(home), "LC_ALL": "C", "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    done = subprocess.run(["/bin/bash", str(TESTS / suite)], cwd=tmp_path, env=env,
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, timeout=600)
    lines = done.stdout.splitlines()
    failed = [line for line in lines if line.startswith("FAIL")]
    passed = [line for line in lines if line.startswith("PASS")]
    tail = "\n".join(lines[-40:])
    assert done.returncode == 0, f"{suite} exited {done.returncode}\n{tail}"
    assert not failed, "\n".join(failed[:40])
    assert passed, f"{suite} printed no PASS line\n{tail}"
