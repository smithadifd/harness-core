"""The rails plugin's manifest and hooks file.

The manifest declares the git guard's three options, each a string with a
picker of exactly ask, deny and off and a default (ask, ask, off); no value
anywhere in the manifest is allow. hooks.json holds one PreToolUse Bash group
with exactly two command hooks in order: the destructive-command guard, then
the git guard, whose arguments end with the three option pairs and carry no
test seam. Claude Code's plugin validator does not check a
${user_config.KEY} reference against userConfig, so the keys are pinned here.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

TESTS = Path(__file__).resolve().parent
PLUGIN = TESTS.parent
OPTION_KEYS = ["push_to_main", "force_push", "commit_style"]

USER_CONFIG = {
    "push_to_main": {
        "type": "string",
        "title": "Push to main",
        "description": "What the git guard does before a push that updates main or master: ask, deny, or off.",
        "options": ["ask", "deny", "off"],
        "default": "ask",
    },
    "force_push": {
        "type": "string",
        "title": "Force-push",
        "description": "What the git guard does before a force, delete, mirror or prune push: ask, deny, or off.",
        "options": ["ask", "deny", "off"],
        "default": "ask",
    },
    "commit_style": {
        "type": "string",
        "title": "Commit style",
        "description": "What the git guard does when a git commit -m subject is not conventional "
                       "(feat:, fix:, docs: and the rest): off, ask, or deny.",
        "options": ["off", "ask", "deny"],
        "default": "off",
    },
}


def load(*parts):
    return json.loads(PLUGIN.joinpath(*parts).read_text(encoding="utf-8"))


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def test_plugin_manifest():
    manifest = load(".claude-plugin", "plugin.json")
    assert manifest == {
        "name": "rails",
        "version": "0.1.1",
        "description": "Guard hooks for shell commands: deny destructive commands, ask before a push to main "
                       "or a force-push. No hook in this plugin ever allows.",
        "author": {"name": "harness-core maintainers"},
        "license": "MIT",
        "keywords": ["guard", "hooks", "deny", "ask"],
        "userConfig": USER_CONFIG,
    }
    for field in ("homepage", "repository", "hooks"):
        assert field not in manifest, field


def test_user_config_options():
    config = load(".claude-plugin", "plugin.json")["userConfig"]
    assert list(config) == OPTION_KEYS
    for key, option in config.items():
        assert sorted(option) == ["default", "description", "options", "title", "type"], key
        assert option["type"] == "string", key
        assert sorted(option["options"]) == ["ask", "deny", "off"], key
        assert option["default"] in option["options"], key
    assert [config[key]["default"] for key in OPTION_KEYS] == ["ask", "ask", "off"]
    assert config["commit_style"]["options"] == ["off", "ask", "deny"]


def test_no_value_in_the_manifest_is_allow():
    manifest = load(".claude-plugin", "plugin.json")
    assert not [s for s in strings(manifest) if s.lower() == "allow"]


def test_hooks_file():
    hooks = load("hooks", "hooks.json")
    assert sorted(hooks) == ["description", "hooks"]
    assert hooks["description"] == ("The destructive-command guard denies or stays silent; the git guard asks, "
                                    "denies or stays silent. Neither hook ever returns an allow.")
    assert sorted(hooks["hooks"]) == ["PreToolUse"]
    [group] = hooks["hooks"]["PreToolUse"]
    assert sorted(group) == ["hooks", "matcher"]
    assert group["matcher"] == "Bash"
    destructive, git = group["hooks"]
    for entry in (destructive, git):
        assert sorted(entry) == ["args", "command", "onFailure", "statusMessage", "timeout", "type"]
        assert entry["type"] == "command"
        assert entry["command"] == "/usr/bin/env"
        assert entry["timeout"] == 10
        assert entry["onFailure"] == "block"
        for word in entry["args"]:
            assert not word.startswith("--adapter-policy") and not word.startswith("--timeout-runner"), word
            assert not word.startswith("--scratchpad") and not word.startswith("--authorized-worktree"), word
            assert "permissionDecision" not in word
    assert destructive["args"][-1] == "${CLAUDE_PLUGIN_ROOT}/hooks/destructive-command-guard.sh"
    assert destructive["statusMessage"] == "Checking destructive command guardrails..."
    assert git["args"] == [
        "-u", "BASH_ENV", "-u", "ENV", "/bin/bash", "--noprofile", "--norc", "-p",
        "${CLAUDE_PLUGIN_ROOT}/hooks/git-guard.sh",
        "--push-to-main", "${user_config.push_to_main}",
        "--force-push", "${user_config.force_push}",
        "--commit-style", "${user_config.commit_style}",
    ]
    assert git["statusMessage"] == "Checking git guardrails..."
    referenced = re.findall(r"\$\{user_config\.([A-Za-z0-9_]+)\}", " ".join(git["args"]))
    assert referenced == OPTION_KEYS
    assert set(referenced) == set(load(".claude-plugin", "plugin.json")["userConfig"])


def test_the_git_guard_reads_no_option_variable():
    text = (PLUGIN / "hooks" / "git-guard.sh").read_text(encoding="utf-8")
    code = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    assert not [line for line in code if "CLAUDE_PLUGIN_OPTION_" in line]
