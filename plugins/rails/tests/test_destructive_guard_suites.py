"""The rails plugin's destructive-command guard suites, run under pytest.

Each shell suite runs as ``/bin/bash <suite>`` with only a fresh HOME under
pytest's temporary folder, ``LC_ALL=C`` and the job's PATH in its
environment, and a 600-second timeout. A suite passes when it exits 0, prints
at least one ``PASS`` line and no line that starts with ``FAIL``. The suite
list is pinned here, so a suite that is deleted or renamed fails instead of
vanishing.

A second test reads the plugin's manifest and hooks file and checks the
fields the hook entry test (H1, in the dependencies suite) does not.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
PLUGIN = TESTS.parent
SUITES = [
    "destructive-command-guard.test.sh",
    "destructive-command-guard-cleanup-worktree.test.sh",
    "destructive-command-guard-dependencies.test.sh",
    "destructive-command-guard-dormant.test.sh",
    "destructive-command-guard-git-forms.test.sh",
    "destructive-command-guard-lane-remove.test.sh",
    "destructive-command-guard-linked-worktree.test.sh",
    "destructive-command-guard-merged-remove.test.sh",
    "destructive-command-guard-never-allow.test.sh",
    "destructive-command-guard-pins.test.sh",
    "destructive-command-guard-reasons.test.sh",
    "destructive-command-guard-redirect.test.sh",
    "destructive-command-guard-scope.test.sh",
    "destructive-command-guard-unavailable-temp.test.sh",
    "destructive-command-guard-workspace-cwd.test.sh",
    "destructive-command-guard-wrappers.test.sh",
]


def test_the_suite_list_is_exactly_the_suites_on_disk():
    found = sorted(path.name for path in TESTS.glob("destructive-command-guard*.test.sh"))
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


def test_plugin_manifest_and_hooks_fields():
    manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest == {
        "name": "rails",
        "version": "0.1.0",
        "description": "Guard hooks that deny destructive shell commands. Deny only: no hook in this plugin ever allows.",
        "license": "MIT",
        "keywords": ["guard", "hooks", "deny"],
    }
    for field in ("author", "homepage", "repository", "userConfig", "hooks"):
        assert field not in manifest, field
    hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    assert sorted(hooks) == ["description", "hooks"]
    assert sorted(hooks["hooks"]) == ["PreToolUse"]
    [group] = hooks["hooks"]["PreToolUse"]
    assert group["matcher"] == "Bash"
    [entry] = group["hooks"]
    assert sorted(entry) == ["args", "command", "onFailure", "statusMessage", "timeout", "type"]
    assert entry["statusMessage"] == "Checking destructive command guardrails..."
    for word in entry["args"]:
        assert not word.startswith("--scratchpad") and not word.startswith("--authorized-worktree"), word
        assert not word.startswith("--adapter-policy") and not word.startswith("--timeout-runner"), word
