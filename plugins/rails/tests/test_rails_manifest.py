"""The rails plugin's manifest and hooks file.

The manifest declares the git guard's three options, each a string with a
default (ask, ask, off): push_to_main and force_push pick from ask (the floor)
and deny, commit_style from off, ask and deny; no value anywhere in the
manifest is allow. hooks.json holds one PreToolUse Bash group with exactly
three command hooks in order: the destructive-command guard; the git guard's
floor, whose arguments are the literal defaults, which references no option
and blocks on a failure; and the git guard's stored-options hook, whose
arguments are --tighten-only and the three option references, with no
onFailure key. None carries a test seam. Claude Code's plugin validator does
not check a ${user_config.KEY} reference against userConfig, so the keys are
pinned here. The destructive guard's arguments are pinned whole and reference
no option: Claude Code does not run a hook while an option it references is
unstored, so an option there would turn the guard off on a plain install (its
rm-roots file is read from the account's home folder instead).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

TESTS = Path(__file__).resolve().parent
PLUGIN = TESTS.parent
OPTION_KEYS = ["push_to_main", "force_push", "commit_style"]
GUARD_PREFIX = ["-u", "BASH_ENV", "-u", "ENV", "/bin/bash", "--noprofile", "--norc", "-p",
                "${CLAUDE_PLUGIN_ROOT}/hooks/git-guard.sh"]
# The floor's literal arguments: the plugin's defaults.
FLOOR_OPTIONS = ["--push-to-main", "ask", "--force-push", "ask", "--commit-style", "off"]

USER_CONFIG = {
    "push_to_main": {
        "type": "string",
        "title": "Push to main",
        "description": "What the git guard does before a push that updates main or master: ask (the floor) or deny.",
        "options": ["ask", "deny"],
        "default": "ask",
    },
    "force_push": {
        "type": "string",
        "title": "Force-push",
        "description": "What the git guard does before a force, delete, mirror or prune push: ask (the floor) or deny.",
        "options": ["ask", "deny"],
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
        "version": "0.2.0",
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
        assert option["default"] in option["options"], key
    assert [config[key]["default"] for key in OPTION_KEYS] == ["ask", "ask", "off"]
    # push_to_main and force_push cannot be off: the floor hook asks whatever
    # is stored, so off would be an inert choice.
    assert config["push_to_main"]["options"] == ["ask", "deny"]
    assert config["force_push"]["options"] == ["ask", "deny"]
    assert config["commit_style"]["options"] == ["off", "ask", "deny"]


def test_no_value_in_the_manifest_is_allow():
    manifest = load(".claude-plugin", "plugin.json")
    assert not [s for s in strings(manifest) if s.lower() == "allow"]


def test_hooks_file():
    hooks = load("hooks", "hooks.json")
    assert sorted(hooks) == ["description", "hooks"]
    assert sorted(hooks["hooks"]) == ["PreToolUse"]
    [group] = hooks["hooks"]["PreToolUse"]
    assert sorted(group) == ["hooks", "matcher"]
    assert group["matcher"] == "Bash"
    destructive, floor, stored = group["hooks"]
    for entry in (destructive, floor, stored):
        assert entry["type"] == "command"
        assert entry["command"] == "/usr/bin/env"
        assert entry["timeout"] == 10
        for word in entry["args"]:
            assert not word.startswith("--adapter-policy") and not word.startswith("--timeout-runner"), word
            assert not word.startswith("--scratchpad") and not word.startswith("--authorized-worktree"), word
            assert "permissionDecision" not in word
    for entry in (destructive, floor):
        assert sorted(entry) == ["args", "command", "onFailure", "statusMessage", "timeout", "type"]
        assert entry["onFailure"] == "block"
    assert sorted(stored) == ["args", "command", "statusMessage", "timeout", "type"]
    assert destructive["args"][-1] == "${CLAUDE_PLUGIN_ROOT}/hooks/destructive-command-guard.sh"
    assert destructive["statusMessage"] == "Checking destructive command guardrails..."
    assert floor["args"] == GUARD_PREFIX + FLOOR_OPTIONS
    assert floor["statusMessage"] == "Checking git guardrails..."
    assert stored["args"] == GUARD_PREFIX + [
        "--tighten-only",
        "--push-to-main", "${user_config.push_to_main}",
        "--force-push", "${user_config.force_push}",
        "--commit-style", "${user_config.commit_style}",
    ]
    assert stored["statusMessage"] == "Checking stored git guard options..."
    referenced = re.findall(r"\$\{user_config\.([A-Za-z0-9_]+)\}", " ".join(stored["args"]))
    assert referenced == OPTION_KEYS
    assert set(referenced) == set(load(".claude-plugin", "plugin.json")["userConfig"])


DESTRUCTIVE_ARGS = ["-u", "BASH_ENV", "-u", "ENV", "/bin/bash", "--noprofile", "--norc", "-p",
                    "${CLAUDE_PLUGIN_ROOT}/hooks/destructive-command-guard.sh"]


def test_the_destructive_guard_references_no_option():
    hooks = load("hooks", "hooks.json")
    entries = [entry for group in hooks["hooks"]["PreToolUse"] for entry in group["hooks"]
               if any(word.endswith("/hooks/destructive-command-guard.sh") for word in entry.get("args", []))]
    assert len(entries) == 1
    [entry] = entries
    assert entry["args"] == DESTRUCTIVE_ARGS
    assert "user_config" not in json.dumps(entry)
    assert "rm_roots" not in json.dumps(load(".claude-plugin", "plugin.json"))


def git_guard_entries():
    hooks = load("hooks", "hooks.json")
    return [entry for group in hooks["hooks"]["PreToolUse"] for entry in group["hooks"]
            if any(word.endswith("/hooks/git-guard.sh") for word in entry.get("args", []))]


def test_the_floor_references_no_option_and_blocks_on_a_failure():
    """Claude Code skips a hook while an option it references is unstored, so
    the floor must reference none to run on a plain install."""
    floor, _ = git_guard_entries()
    assert "${user_config." not in json.dumps(floor)
    assert "--tighten-only" not in floor["args"]
    assert floor["onFailure"] == "block"
    assert floor["args"][-6:] == FLOOR_OPTIONS
    defaults = [load(".claude-plugin", "plugin.json")["userConfig"][key]["default"] for key in OPTION_KEYS]
    assert floor["args"][-5::2] == defaults


def test_the_stored_options_hook_only_tightens_and_never_blocks():
    """Unstored, Claude Code fails this hook interactively; without onFailure
    it cannot block a call, and --tighten-only keeps it from repeating the
    floor's asks."""
    _, stored = git_guard_entries()
    assert stored["args"].count("--tighten-only") == 1
    assert "onFailure" not in stored
    referenced = re.findall(r"\$\{user_config\.([A-Za-z0-9_]+)\}", json.dumps(stored))
    assert referenced == OPTION_KEYS


def test_the_git_guard_reads_no_option_variable():
    text = (PLUGIN / "hooks" / "git-guard.sh").read_text(encoding="utf-8")
    code = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    assert not [line for line in code if "CLAUDE_PLUGIN_OPTION_" in line]
