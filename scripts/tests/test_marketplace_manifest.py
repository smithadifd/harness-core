"""The marketplace manifest, the harness plugin's manifest, and the CI job that validates them.

The invariant every tag of this repository keeps: the marketplace lists each
plugin once; the in-repo `harness` plugin is a folder here at the same tag; each
external plugin is the one expected source for its name, fixed to a full 40-hex
commit on a release tag; nothing resolves to a branch or a range. The checks
live in scripts/check-marketplace.py (its docstring holds the grammar, the one
table of expected external sources and the rules of --source); this file
imports that script and drives it. Nothing here reaches a network: the
resolver is given a fake fetch, and the default fetch is driven through
urllib's own opener with its transport replaced by a local handler that
answers from a table, so this runs offline on the runner's own python3.

The pytest job runs with GITHUB_ACTIONS set. A test that expects the local
behaviour (no tripwire, no anchor required) deletes it; a test of the CI
behaviour sets it.

The CI job `plugin-validate` in `.github/workflows/ci.yml` is read as text:
Claude Code is installed at one exact version, both validate commands run
strict in a fresh HOME, and the pins are checked against their tags by a step
that runs `python3 -I`, passes the owner anchor and holds no token.
"""
from __future__ import annotations

import copy
import email.message
import http.client
import importlib.util
import io
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
import urllib.response
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check-marketplace.py"
_spec = importlib.util.spec_from_file_location("check_marketplace_under_test", SCRIPT)
cm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cm)

MARKETPLACE = cm.MARKETPLACE
PLUGIN_MANIFEST = cm.PLUGIN_MANIFEST
CI = ".github/workflows/ci.yml"
load_json = cm.load_json
problems = cm.problems


@pytest.fixture(autouse=True)
def no_proxy_or_ca_variable(monkeypatch):
    """No test inherits a proxy or CA-override variable from the shell that runs it."""
    for name in list(os.environ):
        if name.casefold() in {"https_proxy", "http_proxy", "all_proxy", "no_proxy",
                               "ssl_cert_file", "ssl_cert_dir"}:
            monkeypatch.delenv(name)


@pytest.fixture
def local(monkeypatch):
    """A local run: GITHUB_ACTIONS deleted (the pytest job runs with it set)."""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)


@pytest.fixture
def in_ci(monkeypatch):
    """A run in CI: GITHUB_ACTIONS set as GitHub sets it."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")


# The CI job, read as text.

SOURCE_RUN = 'python3 -I scripts/check-marketplace.py --source --expect-owner "$EXPECT_OWNER"'
ANCHOR_ENV = "EXPECT_OWNER: ${{ github.repository_owner }}"
# A token or secret named in an env block: the token variables, the secrets
# context in any form, the token in dot or index form, a whole context dumped.
TOKEN_REACH = re.compile(
    r"GITHUB_TOKEN|GH_TOKEN|\bsecrets\b|\bgithub\s*\.\s*token\b"
    r"|\bgithub\s*\[\s*['\"]token['\"]\s*\]|toJSON\s*\(\s*github\s*\)",
    re.IGNORECASE)


def ci_job(text: str) -> str | None:
    match = re.search(r"^  plugin-validate:\n((?:    .*\n|\n)+)", text, re.MULTILINE)
    return match.group(1) if match else None


def job_steps(job: str) -> list[list[str]]:
    """The job's steps, each a list of its lines (the first starts '      - ')."""
    found: list[list[str]] = []
    for line in job.split("\n"):
        if line.lstrip().startswith("#"):
            continue
        if line.startswith("      - "):
            found.append([line])
        elif found and (line.startswith("        ") or line == ""):
            found[-1].append(line)
    return found


def env_text(lines: list[str]) -> str:
    """Every env: block in the lines (its inline value and every deeper line)."""
    out: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        body = line.lstrip(" ")
        indent = len(line) - len(body)
        if body.startswith("- "):
            body, indent = body[2:], indent + 2
        if re.match(r"env\s*:", body):
            out.append(body)
            index += 1
            while index < len(lines) and (
                    lines[index].strip() == "" or len(lines[index]) - len(lines[index].lstrip(" ")) > indent):
                out.append(lines[index])
                index += 1
            continue
        index += 1
    return "\n".join(out)


def step_run(step: list[str]) -> str | None:
    for line in step:
        stripped = line.strip().removeprefix("- ")
        if stripped.startswith("run: "):
            return stripped[len("run: "):]
    return None


def ci_problems(text: str) -> list[str]:
    job = ci_job(text)
    if job is None:
        return ["ci.yml: the plugin-validate job is missing"]
    found: list[str] = []
    runner = "runs-on: ${{ fromJSON(vars.CORE_CI_RUNNER || '[\"ubuntu-latest\"]') }}"
    if runner not in job:
        found.append("plugin-validate: does not run on the CORE_CI_RUNNER variable with the hosted default")
    installs = re.findall(r"npm install\b.*", job)
    pinned = [line for line in installs
              if re.search(r"@anthropic-ai/claude-code@[0-9]+\.[0-9]+\.[0-9]+[\"']?(\s|$)", line)]
    if len(installs) != 1 or len(pinned) != 1:
        found.append("plugin-validate: Claude Code is not installed at one exact version")
    homes = re.findall(r"^\s*home=\"\$\(mktemp -d .*\)\"$", job, re.MULTILINE)
    commands = [line.strip() for line in job.split("\n") if "plugin validate" in line]
    for target, name in ((".", "marketplace"), ("plugins/harness", "harness plugin")):
        wanted = [line for line in commands if re.fullmatch(
            r"HOME=\"\$home\" \"\$claude\" plugin validate --strict " + re.escape(target), line)]
        if len(wanted) != 1:
            found.append(f"plugin-validate: the {name} is not validated strict in a fresh HOME")
    if len(homes) != 1:
        found.append("plugin-validate: the fresh HOME is not made with mktemp -d")
    steps = job_steps(job)
    # The --source step is chosen by its command text, never by its name.
    sources = [step for step in steps
               if any("check-marketplace.py" in line and "--source" in line for line in step)]
    if len(sources) != 1:
        found.append("plugin-validate: the pins are not checked against their tags (check-marketplace.py --source)")
    else:
        run = step_run(sources[0]) or ""
        if not run.startswith("python3 -I scripts/check-marketplace.py "):
            found.append("plugin-validate: the --source step does not run under python3 -I")
        arguments = run.split("scripts/check-marketplace.py", 1)[-1]
        anchor = [line for line in sources[0] if line.strip() == ANCHOR_ENV]
        named = [line for line in env_text(sources[0]).split("\n") if "EXPECT_OWNER" in line]
        if arguments != SOURCE_RUN.split("scripts/check-marketplace.py", 1)[1] or len(anchor) != 1 or named != anchor:
            found.append("plugin-validate: the --source step does not pass --expect-owner from github.repository_owner")
    head = text.split("\njobs:", 1)[0].split("\n")
    if TOKEN_REACH.search(env_text(head)) or TOKEN_REACH.search(env_text(job.split("\n"))):
        found.append("plugin-validate: a token or secret reaches the job through an env block")
    checkouts = [step for step in steps if any("uses: actions/checkout@" in line for line in step)]
    if len(checkouts) != 1 or not any(line.strip() == "persist-credentials: false" for line in checkouts[0]):
        found.append("plugin-validate: the checkout does not set persist-credentials: false")
    if "|| true" in job or "continue-on-error" in job:
        found.append("plugin-validate: a failure is allowed")
    return found


# The real files.

def test_the_marketplace_and_the_harness_plugin_manifests_hold_the_invariant():
    assert problems(ROOT) == []


def test_the_script_is_clean_offline_on_this_repository():
    assert cm.main([]) == 0


def test_the_script_refuses_an_unknown_option_with_the_usage_exit():
    assert cm.main(["--bogus"]) == 2


def test_expect_owner_without_source_is_a_usage_error():
    assert cm.main(["--expect-owner", "someone"]) == 2


def test_the_marketplace_lists_harness_and_both_external_plugins():
    names = [entry["name"] for entry in load_json(ROOT / MARKETPLACE)["plugins"]]
    assert {"harness", "sdlc", "ledger-hooks"} <= set(names)
    assert len(names) == len(set(names))


def test_harness_depends_only_on_entries_of_the_marketplace():
    names = {entry["name"] for entry in load_json(ROOT / MARKETPLACE)["plugins"]}
    manifest = load_json(ROOT / "plugins" / "harness" / PLUGIN_MANIFEST)
    assert manifest["dependencies"], "harness brings the set through its dependencies"
    assert set(manifest["dependencies"]) <= names


def test_the_ci_job_validates_both_manifests_strict_at_a_pinned_claude_code():
    """Also: the --source step runs python3 -I, passes --expect-owner from
    github.repository_owner, and no token or secret reaches the job."""
    assert ci_problems((ROOT / CI).read_text(encoding="utf-8")) == []


# Near misses: each is a good scratch tree with one defect, and the check names it.

SDLC_SHA = "b8e013f470f2470630f33664bd1ca9b351b6de7c"
LEDGER_SHA = "346cb3595180fcea702ebdd37b32ff8c36325ea5"
SKILLS_URL = "https://github.com/someone/skills.git"
LEDGER_URL = "https://github.com/someone/ledger.git"
GOOD_PIN = "# header\ntag v0.1.0\nrepo someone/ledger\nsha " + "0" * 40 + "\n"
GOOD_MARKETPLACE = {
    "name": "harness-core",
    "owner": {"name": "someone"},
    "plugins": [
        {"name": "harness", "source": "./plugins/harness"},
        {"name": "sdlc", "source": {
            "source": "url", "url": SKILLS_URL, "ref": "v1.3.0", "sha": SDLC_SHA}},
        {"name": "ledger-hooks", "source": {
            "source": "git-subdir", "url": LEDGER_URL, "path": "plugins/ledger-hooks",
            "ref": "v0.3.1", "sha": LEDGER_SHA}},
    ],
}
GOOD_PLUGIN = {"name": "harness", "version": "0.1.0", "dependencies": ["sdlc", "ledger-hooks"]}


def write_tree(root: Path, market, plugin, pin: bool = True) -> None:
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (root / MARKETPLACE).write_text(json.dumps(market), encoding="utf-8")
    (root / "plugins" / "harness" / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (root / "plugins" / "harness" / PLUGIN_MANIFEST).write_text(json.dumps(plugin), encoding="utf-8")
    if pin:
        (root / cm.PIN).write_text(GOOD_PIN, encoding="utf-8")


def external(market, name):
    return next(entry["source"] for entry in market["plugins"] if entry["name"] == name)


def entry_of(market, name):
    return next(entry for entry in market["plugins"] if entry["name"] == name)


def set_key(target, key, value):
    target[key] = value


def drop_key(target, key):
    del target[key]


def remove_folder(root: Path):
    shutil.rmtree(root / "plugins" / "harness")


def remove_manifest(root: Path):
    (root / "plugins" / "harness" / PLUGIN_MANIFEST).unlink()


def remove_pin(root: Path):
    (root / cm.PIN).unlink()


def write_pin(root: Path, text: str):
    (root / cm.PIN).write_text(text, encoding="utf-8")


def external_entry(name: str, url: str = SKILLS_URL) -> dict:
    return {"name": name, "source": {
        "source": "url", "url": url, "ref": "v1.0.0", "sha": SDLC_SHA}}


NEAR_MISSES = [
    # (id, change to the market dict, the plugin dict, or the tree, the named problem)
    ("pin-short", lambda m, p, r: set_key(external(m, "sdlc"), "sha", "b8e013f470f2470630f33664bd1ca9b351b6de7"),
     "plugin sdlc: pin is not a full 40-hex commit id"),
    ("pin-uppercase", lambda m, p, r: set_key(external(m, "sdlc"), "sha", "B8E013F470F2470630F33664BD1CA9B351B6DE7C"),
     "plugin sdlc: pin is not a full 40-hex commit id"),
    ("pin-trailing-newline", lambda m, p, r: set_key(external(m, "ledger-hooks"), "sha", "346cb3595180fcea702ebdd37b32ff8c36325ea5\n"),
     "plugin ledger-hooks: pin is not a full 40-hex commit id"),
    ("pin-not-a-string", lambda m, p, r: set_key(external(m, "ledger-hooks"), "sha", 346),
     "plugin ledger-hooks: pin is not a full 40-hex commit id"),
    ("pin-is-a-tag-name", lambda m, p, r: set_key(external(m, "sdlc"), "sha", "v1.3.0"),
     "plugin sdlc: pin is not a full 40-hex commit id"),
    ("pin-missing", lambda m, p, r: drop_key(external(m, "sdlc"), "sha"),
     "plugin sdlc: no pin (sha missing)"),
    ("ref-branch", lambda m, p, r: set_key(external(m, "sdlc"), "ref", "main"),
     "plugin sdlc: ref is not a release tag vX.Y.Z"),
    ("ref-range", lambda m, p, r: set_key(external(m, "sdlc"), "ref", "^1.3.0"),
     "plugin sdlc: ref is not a release tag vX.Y.Z"),
    ("ref-without-v", lambda m, p, r: set_key(external(m, "ledger-hooks"), "ref", "0.3.1"),
     "plugin ledger-hooks: ref is not a release tag vX.Y.Z"),
    ("ref-missing", lambda m, p, r: drop_key(external(m, "sdlc"), "ref"),
     "plugin sdlc: no ref"),
    ("source-type-npm", lambda m, p, r: set_key(entry_of(m, "sdlc"), "source", {"source": "npm", "package": "sdlc"}),
     "plugin sdlc: source type 'npm' is not allowed (url or git-subdir only)"),
    ("source-type-github-shorthand", lambda m, p, r: set_key(entry_of(m, "sdlc"), "source", {
        "source": "github", "repo": "someone/skills", "ref": "v1.3.0", "sha": SDLC_SHA}),
     "plugin sdlc: source type 'github' is not allowed (url or git-subdir only)"),
    ("source-unknown-key", lambda m, p, r: set_key(external(m, "sdlc"), "branch", "main"),
     "plugin sdlc: unknown source key 'branch'"),
    ("source-field-missing", lambda m, p, r: drop_key(external(m, "ledger-hooks"), "path"),
     "plugin ledger-hooks: source field 'path' is missing"),
    ("source-url-not-https", lambda m, p, r: set_key(external(m, "sdlc"), "url", "http://example.test/skills.git"),
     "plugin sdlc: source url is not https"),
    ("source-neither-path-nor-object", lambda m, p, r: set_key(entry_of(m, "sdlc"), "source", 3),
     "plugin sdlc: source is neither a ./ path nor an object"),
    # Each external plugin name is bound to its one expected source.
    ("sdlc-pointed-at-the-ledger-repository", lambda m, p, r: set_key(external(m, "sdlc"), "url", LEDGER_URL),
     "plugin sdlc: source url is not the expected skills repository"),
    ("ledger-hooks-pointed-at-the-skills-repository", lambda m, p, r: set_key(external(m, "ledger-hooks"), "url", SKILLS_URL),
     "plugin ledger-hooks: source url is not the expected ledger repository"),
    ("sdlc-at-another-owner", lambda m, p, r: set_key(external(m, "sdlc"), "url", "https://github.com/other/skills.git"),
     "plugin sdlc: source url is not the expected skills repository"),
    ("sdlc-url-without-the-git-suffix", lambda m, p, r: set_key(external(m, "sdlc"), "url", "https://github.com/someone/skills"),
     "plugin sdlc: source url is not the expected skills repository"),
    ("ledger-hooks-with-another-path", lambda m, p, r: set_key(external(m, "ledger-hooks"), "path", "plugins/other"),
     "plugin ledger-hooks: source path is not 'plugins/ledger-hooks'"),
    ("sdlc-with-a-subdirectory", lambda m, p, r: set_key(external(m, "sdlc"), "path", "plugins/ledger-hooks"),
     "plugin sdlc: unknown source key 'path'"),
    ("sdlc-as-git-subdir", lambda m, p, r: set_key(entry_of(m, "sdlc"), "source", {
        "source": "git-subdir", "url": SKILLS_URL, "path": "x", "ref": "v1.3.0", "sha": SDLC_SHA}),
     "plugin sdlc: source type 'git-subdir' is not the expected 'url'"),
    ("ledger-hooks-as-url", lambda m, p, r: set_key(entry_of(m, "ledger-hooks"), "source", {
        "source": "url", "url": LEDGER_URL, "ref": "v0.3.1", "sha": LEDGER_SHA}),
     "plugin ledger-hooks: source type 'url' is not the expected 'git-subdir'"),
    ("unexpected-external-entry", lambda m, p, r: m["plugins"].append(external_entry("rails")),
     "plugin rails: is not an expected external plugin (sdlc, ledger-hooks)"),
    ("sdlc-as-a-folder", lambda m, p, r: set_key(entry_of(m, "sdlc"), "source", "./plugins/harness"),
     "plugin sdlc: source is not the expected external source"),
    ("owner-pin-missing", lambda m, p, r: remove_pin(r),
     "ledger-vendor.pin: cannot be read to give the owner (FileNotFoundError)"),
    ("owner-pin-with-two-repo-lines", lambda m, p, r: write_pin(r, GOOD_PIN + "repo other/ledger\n"),
     "ledger-vendor.pin: has no single repo line of owner/name to give the owner"),
    ("owner-pin-repo-not-owner-name", lambda m, p, r: write_pin(r, "repo ledger\n"),
     "ledger-vendor.pin: has no single repo line of owner/name to give the owner"),
    ("folder-missing", lambda m, p, r: remove_folder(r),
     "plugin harness: folder ./plugins/harness is missing"),
    ("manifest-missing", lambda m, p, r: remove_manifest(r),
     "plugin harness: manifest ./plugins/harness/.claude-plugin/plugin.json is missing"),
    ("source-with-dotdot", lambda m, p, r: set_key(entry_of(m, "harness"), "source", "./plugins/../plugins/harness"),
     "plugin harness: source is not a ./ folder path inside the repository"),
    ("source-without-dot-slash", lambda m, p, r: set_key(entry_of(m, "harness"), "source", "plugins/harness"),
     "plugin harness: source is not a ./ folder path inside the repository"),
    ("source-absolute", lambda m, p, r: set_key(entry_of(m, "harness"), "source", "/plugins/harness"),
     "plugin harness: source is not a ./ folder path inside the repository"),
    ("manifest-name-differs", lambda m, p, r: set_key(p, "name", "harness2"),
     "plugin harness: manifest name 'harness2' differs from the entry name"),
    ("manifest-version-missing", lambda m, p, r: drop_key(p, "version"),
     "plugin harness: manifest version is missing or not X.Y.Z"),
    ("manifest-version-not-semver", lambda m, p, r: set_key(p, "version", "latest"),
     "plugin harness: manifest version is missing or not X.Y.Z"),
    ("version-in-entry-and-manifest", lambda m, p, r: set_key(entry_of(m, "harness"), "version", "0.1.0"),
     "plugin harness: version is set in the entry and in the manifest"),
    ("dependency-not-in-marketplace", lambda m, p, r: set_key(p, "dependencies", ["sdlc", "rails"]),
     "plugin harness: dependency 'rails' is not an entry of this marketplace"),
    ("dependency-with-marketplace-suffix", lambda m, p, r: set_key(p, "dependencies", ["sdlc@harness-core"]),
     "plugin harness: dependency 'sdlc@harness-core' is not an entry of this marketplace"),
    ("dependency-range-object", lambda m, p, r: set_key(p, "dependencies", [{"name": "sdlc", "version": "^1"}]),
     "plugin harness: dependencies must be a list of plain plugin names, no range"),
    ("dependency-on-itself", lambda m, p, r: set_key(p, "dependencies", ["harness"]),
     "plugin harness: depends on itself"),
    ("harness-without-an-external-dependency", lambda m, p, r: set_key(p, "dependencies", ["sdlc"]),
     "plugin harness: does not depend on external plugin 'ledger-hooks'"),
    ("harness-with-an-external-dependency-twice", lambda m, p, r: set_key(p, "dependencies", ["sdlc", "ledger-hooks", "sdlc"]),
     "plugin harness: depends on external plugin 'sdlc' more than once"),
    ("plugin-listed-twice", lambda m, p, r: m["plugins"].append(copy.deepcopy(entry_of(m, "sdlc"))),
     "plugin sdlc: is listed twice"),
    ("plugin-name-unknown", lambda m, p, r: m["plugins"].append({"name": "extra", "source": "./plugins/harness"}),
     "plugins[3]: plugin name 'extra' is not one of harness, sdlc, ledger-hooks, rails"),
    ("harness-not-listed", lambda m, p, r: m["plugins"].pop(0),
     "plugin harness: is not listed"),
    ("harness-is-external", lambda m, p, r: set_key(entry_of(m, "harness"), "source", copy.deepcopy(external(m, "sdlc"))),
     "plugin harness: source is not a folder in this repository"),
    ("marketplace-name-wrong", lambda m, p, r: set_key(m, "name", "other"),
     ".claude-plugin/marketplace.json: name is not 'harness-core'"),
    ("owner-missing", lambda m, p, r: drop_key(m, "owner"),
     ".claude-plugin/marketplace.json: owner.name is missing"),
    ("plugins-empty", lambda m, p, r: set_key(m, "plugins", []),
     ".claude-plugin/marketplace.json: plugins is not a non-empty list"),
]


@pytest.mark.parametrize("change,expected", [(case[1], case[2]) for case in NEAR_MISSES],
                         ids=[case[0] for case in NEAR_MISSES])
def test_a_near_miss_is_refused_by_name(tmp_path, change, expected):
    market, plugin = copy.deepcopy(GOOD_MARKETPLACE), copy.deepcopy(GOOD_PLUGIN)
    write_tree(tmp_path, market, plugin)
    assert problems(tmp_path) == [], "the scratch tree is good before the change"
    change(market, plugin, tmp_path)
    if (tmp_path / "plugins" / "harness").exists() and (
            tmp_path / "plugins" / "harness" / PLUGIN_MANIFEST).exists():
        write_tree(tmp_path, market, plugin, pin=False)
    else:
        (tmp_path / MARKETPLACE).write_text(json.dumps(market), encoding="utf-8")
    assert expected in problems(tmp_path)


# harness brings the set: its dependencies name each EXTERNAL_PLUGINS entry
# exactly once. Each case is the only refusal of an otherwise good tree.

@pytest.mark.parametrize("dependencies,expected", [
    (["sdlc"], ["plugin harness: does not depend on external plugin 'ledger-hooks'"]),
    (["ledger-hooks"], ["plugin harness: does not depend on external plugin 'sdlc'"]),
    ([], ["plugin harness: does not depend on external plugin 'sdlc'",
          "plugin harness: does not depend on external plugin 'ledger-hooks'"]),
    (None, ["plugin harness: does not depend on external plugin 'sdlc'",
            "plugin harness: does not depend on external plugin 'ledger-hooks'"]),
], ids=["no-ledger-hooks", "no-sdlc", "empty-list", "no-dependencies-key"])
def test_a_missing_external_dependency_of_harness_is_refused_by_name(tmp_path, dependencies, expected):
    plugin = copy.deepcopy(GOOD_PLUGIN)
    if dependencies is None:
        del plugin["dependencies"]
    else:
        plugin["dependencies"] = dependencies
    write_tree(tmp_path, GOOD_MARKETPLACE, plugin)
    assert problems(tmp_path) == expected


@pytest.mark.parametrize("dependencies,expected", [
    (["sdlc", "ledger-hooks", "sdlc"],
     ["plugin harness: depends on external plugin 'sdlc' more than once"]),
    (["ledger-hooks", "sdlc", "ledger-hooks", "ledger-hooks"],
     ["plugin harness: depends on external plugin 'ledger-hooks' more than once"]),
], ids=["sdlc-twice", "ledger-hooks-three-times"])
def test_a_duplicate_external_dependency_of_harness_is_refused_by_name(tmp_path, dependencies, expected):
    plugin = copy.deepcopy(GOOD_PLUGIN)
    plugin["dependencies"] = dependencies
    write_tree(tmp_path, GOOD_MARKETPLACE, plugin)
    assert problems(tmp_path) == expected


def test_the_expected_external_dependencies_are_read_from_the_table(tmp_path, monkeypatch):
    """A name added to EXTERNAL_PLUGINS is required of harness with no second list."""
    write_tree(tmp_path, GOOD_MARKETPLACE, GOOD_PLUGIN)
    monkeypatch.setitem(cm.EXTERNAL_PLUGINS, "rails",
                        {"source": "url", "repository": "rails", "path": None})
    assert problems(tmp_path) == ["plugin harness: does not depend on external plugin 'rails'"]


def test_a_duplicate_json_key_is_refused_by_name(tmp_path):
    write_tree(tmp_path, GOOD_MARKETPLACE, GOOD_PLUGIN)
    text = (tmp_path / MARKETPLACE).read_text(encoding="utf-8")
    (tmp_path / MARKETPLACE).write_text(text.replace('"name": "harness-core"',
                                                     '"name": "harness-core", "name": "other"', 1),
                                        encoding="utf-8")
    assert problems(tmp_path) == [
        ".claude-plugin/marketplace.json: is not valid JSON (duplicate key 'name')"]


def test_invalid_json_and_a_missing_file_are_refused_by_name(tmp_path):
    assert problems(tmp_path) == [".claude-plugin/marketplace.json: is missing"]
    write_tree(tmp_path, GOOD_MARKETPLACE, GOOD_PLUGIN)
    (tmp_path / MARKETPLACE).write_text("{", encoding="utf-8")
    assert problems(tmp_path)[0].startswith(".claude-plugin/marketplace.json: is not valid JSON (")
    (tmp_path / MARKETPLACE).write_text("[]", encoding="utf-8")
    assert problems(tmp_path) == [".claude-plugin/marketplace.json: top level is not an object"]


def test_a_plugin_manifest_with_a_duplicate_key_is_refused_by_name(tmp_path):
    write_tree(tmp_path, GOOD_MARKETPLACE, GOOD_PLUGIN)
    manifest = tmp_path / "plugins" / "harness" / PLUGIN_MANIFEST
    manifest.write_text('{"name": "harness", "name": "harness", "version": "0.1.0"}', encoding="utf-8")
    assert problems(tmp_path) == [
        "plugin harness: manifest is not valid JSON (duplicate key 'name')"]


def test_a_plugin_manifest_that_is_not_an_object_is_refused_by_name(tmp_path):
    write_tree(tmp_path, GOOD_MARKETPLACE, GOOD_PLUGIN)
    (tmp_path / "plugins" / "harness" / PLUGIN_MANIFEST).write_text("[]", encoding="utf-8")
    assert problems(tmp_path) == ["plugin harness: manifest is not a JSON object"]


def test_a_symlinked_folder_that_leaves_the_repository_is_refused_by_name(tmp_path):
    outside = tmp_path / "outside"
    (outside / ".claude-plugin").mkdir(parents=True)
    (outside / PLUGIN_MANIFEST).write_text(json.dumps(GOOD_PLUGIN), encoding="utf-8")
    root = tmp_path / "root"
    write_tree(root, GOOD_MARKETPLACE, GOOD_PLUGIN)
    shutil.rmtree(root / "plugins" / "harness")
    (root / "plugins" / "harness").symlink_to(outside)
    assert "plugin harness: source resolves outside the repository" in problems(root)


# --source: the commit each external entry's tag names, read from the GitHub
# REST API, is the commit its pin names. The resolver is given a fake fetch
# here; nothing reaches a network.

API_REPOS = "https://api.github.com/repos/"
SKILLS_GIT = "https://api.github.com/repos/someone/skills/git"
LEDGER_GIT = "https://api.github.com/repos/someone/ledger/git"
SDLC_REF = f"{SKILLS_GIT}/ref/tags/v1.3.0"
LEDGER_REF = f"{LEDGER_GIT}/ref/tags/v0.3.1"
OTHER_COMMIT = "7" * 40
# Tag objects, in chain order.
TAG_OBJECTS = ["a" * 40, "b" * 40, "c" * 40, "d" * 40, "e" * 40, "f" * 40]
NOT_FOUND = (404, {}, b'{"message": "Not Found"}')


def as_bytes(value) -> bytes:
    return json.dumps(value).encode("utf-8")


def ref_body(kind: str, sha, ref: str = "refs/tags/v1.3.0") -> bytes:
    return as_bytes({"ref": ref, "node_id": "n", "url": "u", "object": {"type": kind, "sha": sha, "url": "u"}})


def tag_body(own: str, kind: str, sha) -> bytes:
    return as_bytes({"sha": own, "tag": "v1.3.0", "object": {"type": kind, "sha": sha, "url": "u"}})


def ok(data: bytes) -> tuple:
    return 200, {}, data


class FakeApi:
    """A stand-in for the default fetch: url -> its answer, every call recorded.
    A url that is not in the table answers 404."""

    def __init__(self, answers: dict):
        self.answers = answers
        self.calls: list[str] = []

    def __call__(self, url: str):
        self.calls.append(url)
        answer = self.answers.get(url, NOT_FOUND)
        if isinstance(answer, BaseException):
            raise answer
        return answer


def chain(hops: int, end: str = SDLC_SHA) -> dict:
    """The sdlc tag as `hops` tag objects in a row, the last naming `end`."""
    if hops == 0:
        return {SDLC_REF: ok(ref_body("commit", end))}
    answers = {SDLC_REF: ok(ref_body("tag", TAG_OBJECTS[0]))}
    for index in range(hops):
        kind, target = ("tag", TAG_OBJECTS[index + 1]) if index + 1 < hops else ("commit", end)
        answers[f"{SKILLS_GIT}/tags/{TAG_OBJECTS[index]}"] = ok(tag_body(TAG_OBJECTS[index], kind, target))
    return answers


def api(sdlc: dict) -> FakeApi:
    """The ledger tag lightweight at its pin; the sdlc tag as given."""
    return FakeApi({LEDGER_REF: ok(ref_body("commit", LEDGER_SHA, "refs/tags/v0.3.1")), **sdlc})


def resolve(root: Path, fetch, expect_owner: str | None = None) -> list[str]:
    return cm.source_problems(root, expect_owner, fetch)


NOT_UTF8 = (b'{"ref": "refs/tags/v1.3.0", "note": "\xff", "object": {"type": "commit", "sha": "'
            + SDLC_SHA.encode() + b'", "url": "u"}}')
SOURCE_CASES = [
    # (id, the sdlc answers, the refusals expected)
    ("lightweight-tag-at-the-pin", chain(0), []),
    ("annotated-tag-one-hop", chain(1), []),
    ("tag-of-a-tag-two-hops", chain(2), []),
    ("exactly-four-tag-objects", chain(4), []),
    ("five-tag-objects-refused", chain(5), ["plugin sdlc: more than 4 tag objects before a commit"]),
    ("tag-to-a-blob", {SDLC_REF: ok(ref_body("blob", OTHER_COMMIT))},
     ["plugin sdlc: the tag names a 'blob' object, not a commit"]),
    ("tag-to-a-tree", {SDLC_REF: ok(ref_body("tree", OTHER_COMMIT))},
     ["plugin sdlc: the tag names a 'tree' object, not a commit"]),
    ("tag-object-to-a-blob", {SDLC_REF: ok(ref_body("tag", TAG_OBJECTS[0])),
                              f"{SKILLS_GIT}/tags/{TAG_OBJECTS[0]}": ok(tag_body(TAG_OBJECTS[0], "blob", OTHER_COMMIT))},
     ["plugin sdlc: the tag names a 'blob' object, not a commit"]),
    ("tag-to-an-unknown-type", {SDLC_REF: ok(ref_body("Commit\n", SDLC_SHA))},
     ["plugin sdlc: the tag names an object of an unknown type, not a commit"]),
    ("ref-prefix-match", {SDLC_REF: ok(ref_body("commit", SDLC_SHA, "refs/tags/v1.3.0-rc1"))},
     ["plugin sdlc: the answer's ref is not refs/tags/v1.3.0"]),
    ("ref-missing", {SDLC_REF: ok(as_bytes({"object": {"type": "commit", "sha": SDLC_SHA}}))},
     ["plugin sdlc: the answer's ref is not refs/tags/v1.3.0"]),
    ("status-404", {}, ["plugin sdlc: status 404"]),
    ("status-500", {SDLC_REF: (500, {}, b"")}, ["plugin sdlc: status 500"]),
    ("status-204", {SDLC_REF: (204, {}, ref_body("commit", SDLC_SHA))}, ["plugin sdlc: status 204"]),
    ("status-301", {SDLC_REF: (301, {"Location": "https://evil.example/x"}, b"")},
     ["plugin sdlc: redirect 301 (renamed or transferred repository?)"]),
    ("rate-limit-403", {SDLC_REF: (403, {"X-RateLimit-Remaining": "0"}, b"{}")},
     ["plugin sdlc: rate limited; rerun"]),
    ("rate-limit-429", {SDLC_REF: (429, {"x-ratelimit-remaining": "0"}, b"{}")},
     ["plugin sdlc: rate limited; rerun"]),
    ("forbidden-with-requests-left", {SDLC_REF: (403, {"X-RateLimit-Remaining": "5"}, b"{}")},
     ["plugin sdlc: status 403"]),
    ("forbidden-with-no-rate-header", {SDLC_REF: (403, {}, b"{}")}, ["plugin sdlc: status 403"]),
    ("not-json", {SDLC_REF: ok(b"not json")}, ["plugin sdlc: the answer is not JSON"]),
    ("a-list", {SDLC_REF: ok(b"[]")}, ["plugin sdlc: the answer is not a JSON object"]),
    ("object-missing", {SDLC_REF: ok(as_bytes({"ref": "refs/tags/v1.3.0"}))}, ["plugin sdlc: the answer has no object"]),
    ("object-not-an-object", {SDLC_REF: ok(as_bytes({"ref": "refs/tags/v1.3.0", "object": "x"}))},
     ["plugin sdlc: the answer has no object"]),
    ("object-sha-wrong-type", {SDLC_REF: ok(ref_body("commit", 7))},
     ["plugin sdlc: the answer's object has no string type and sha"]),
    ("object-type-missing", {SDLC_REF: ok(as_bytes({"ref": "refs/tags/v1.3.0", "object": {"sha": SDLC_SHA}}))},
     ["plugin sdlc: the answer's object has no string type and sha"]),
    ("not-utf-8", {SDLC_REF: ok(NOT_UTF8)}, ["plugin sdlc: the answer is not UTF-8"]),
    ("body-over-the-cap", {SDLC_REF: ok(b" " * (256 * 1024 + 1))}, ["plugin sdlc: the answer body is over 256 KiB"]),
    ("commit-sha-uppercase", {SDLC_REF: ok(ref_body("commit", SDLC_SHA.upper()))},
     ["plugin sdlc: the answer's object sha is not 40 lowercase hex"]),
    ("lightweight-tag-at-another-commit", chain(0, OTHER_COMMIT),
     [f"plugin sdlc: tag v1.3.0 resolves to {OTHER_COMMIT}, not the pin {SDLC_SHA}"]),
    ("annotated-tag-at-another-commit", chain(1, OTHER_COMMIT),
     [f"plugin sdlc: tag v1.3.0 resolves to {OTHER_COMMIT}, not the pin {SDLC_SHA}"]),
    ("annotated-tag-whose-tag-object-is-the-pin", {SDLC_REF: ok(ref_body("tag", SDLC_SHA)),
                                                   f"{SKILLS_GIT}/tags/{SDLC_SHA}": ok(tag_body(SDLC_SHA, "commit", OTHER_COMMIT))},
     [f"plugin sdlc: tag v1.3.0 resolves to {OTHER_COMMIT}, not the pin {SDLC_SHA}"]),
    ("fetch-failed", {SDLC_REF: cm.FetchFailed("request failed (TimeoutError)")},
     ["plugin sdlc: request failed (TimeoutError)"]),
]


@pytest.mark.parametrize("answers,expected", [(case[1], case[2]) for case in SOURCE_CASES],
                         ids=[case[0] for case in SOURCE_CASES])
def test_a_tag_is_compared_with_its_pin_by_name(tmp_path, local, answers, expected):
    write_tree(tmp_path, GOOD_MARKETPLACE, GOOD_PLUGIN)
    fake = api(answers)
    assert resolve(tmp_path, fake) == expected
    # No retry: every url is asked for once, refused or not.
    assert len(fake.calls) == len(set(fake.calls)), fake.calls


def test_a_recursion_error_while_decoding_is_the_not_json_refusal(tmp_path, local, monkeypatch):
    """Deeply nested JSON raises RecursionError in the decoder; the depth at which
    it does depends on the platform's stack, so the decoder is made to raise it
    for one marked body."""
    real = json.loads

    def loads(text, *args, **kwargs):
        if text == "[[[deep":
            raise RecursionError("maximum recursion depth exceeded while decoding a JSON array")
        return real(text, *args, **kwargs)
    monkeypatch.setattr(cm.json, "loads", loads)
    write_tree(tmp_path, GOOD_MARKETPLACE, GOOD_PLUGIN)
    assert resolve(tmp_path, api({SDLC_REF: ok(b"[[[deep")})) == ["plugin sdlc: the answer is not JSON"]


def test_the_hop_bound_stops_before_a_fifth_tag_object(tmp_path, local):
    write_tree(tmp_path, GOOD_MARKETPLACE, GOOD_PLUGIN)
    fake = api(chain(5))
    assert resolve(tmp_path, fake) == ["plugin sdlc: more than 4 tag objects before a commit"]
    assert [url for url in fake.calls if url.startswith(SKILLS_GIT)] == [
        SDLC_REF, *(f"{SKILLS_GIT}/tags/{sha}" for sha in TAG_OBJECTS[:4])], "the 5th tag object is never asked for"


@pytest.mark.parametrize("sha", ["abc", SDLC_SHA.upper(), "../x", "a" * 39 + "/", "a" * 41],
                         ids=["short", "uppercase", "dot-dot-path", "slash", "long"])
def test_a_hop_sha_off_the_grammar_is_refused_before_the_next_request(tmp_path, local, sha):
    write_tree(tmp_path, GOOD_MARKETPLACE, GOOD_PLUGIN)
    fake = api({SDLC_REF: ok(ref_body("tag", sha))})
    assert resolve(tmp_path, fake) == ["plugin sdlc: the answer's object sha is not 40 lowercase hex"]
    assert [url for url in fake.calls if url.startswith(SKILLS_GIT)] == [SDLC_REF], "no hop was requested"


def test_a_failed_request_is_asked_for_once(tmp_path, local):
    write_tree(tmp_path, GOOD_MARKETPLACE, GOOD_PLUGIN)
    fake = api({SDLC_REF: (500, {}, b"")})
    assert resolve(tmp_path, fake) == ["plugin sdlc: status 500"]
    assert fake.calls.count(SDLC_REF) == 1


def test_every_url_is_the_fixed_host_and_the_singular_ref_endpoint(tmp_path, local, monkeypatch):
    monkeypatch.setenv("GITHUB_API_URL", "https://evil.example/api")
    write_tree(tmp_path, GOOD_MARKETPLACE, GOOD_PLUGIN)
    fake = api(chain(1))
    assert resolve(tmp_path, fake) == []
    assert fake.calls == [SDLC_REF, f"{SKILLS_GIT}/tags/{TAG_OBJECTS[0]}", LEDGER_REF]
    for url in fake.calls:
        assert url.startswith(API_REPOS), url
        assert "/git/refs/" not in url, url
    assert "/git/ref/tags/" in fake.calls[0]


def test_each_entry_is_resolved_against_the_tables_repository_not_the_manifests_url(tmp_path, local, monkeypatch):
    market = copy.deepcopy(GOOD_MARKETPLACE)
    set_key(external(market, "sdlc"), "url", "https://example.test/elsewhere.git")
    set_key(external(market, "ledger-hooks"), "url", "https://example.test/other.git")
    write_tree(tmp_path, market, GOOD_PLUGIN)
    # An offline check that let both entries through, to prove the resolver
    # does not lean on it for the repository.
    monkeypatch.setattr(cm, "external_problems", lambda *args: [])
    fake = api(chain(0))
    assert resolve(tmp_path, fake) == []
    assert fake.calls == [SDLC_REF, LEDGER_REF]


def test_a_wrong_ledger_hooks_pin_is_refused_by_its_own_name(tmp_path, local):
    market = copy.deepcopy(GOOD_MARKETPLACE)
    set_key(external(market, "ledger-hooks"), "sha", OTHER_COMMIT)
    write_tree(tmp_path, market, GOOD_PLUGIN)
    assert resolve(tmp_path, api(chain(0))) == [
        f"plugin ledger-hooks: tag v0.3.1 resolves to {LEDGER_SHA}, not the pin {OTHER_COMMIT}"]


def test_source_mode_with_no_readable_marketplace_leaves_the_naming_to_the_offline_check(tmp_path, local):
    fake = api({})
    assert resolve(tmp_path, fake) == []
    assert fake.calls == []
    assert problems(tmp_path) == [".claude-plugin/marketplace.json: is missing"]


# The grammars: a segment off its grammar is refused and nothing is fetched.

@pytest.mark.parametrize("owner", ["..", "a/b", "a.b", "%2e%2e", "-a", "", "a" * 40, "é", "a_b", "a\n"],
                         ids=["dot-dot", "slash", "dot", "percent", "leading-dash", "empty", "too-long",
                              "non-ascii", "underscore", "newline"])
def test_an_owner_off_the_grammar_is_refused_and_nothing_is_fetched(owner):
    fake = api({})
    assert cm.resolve_commit(owner, "skills", "v1.3.0", fake) == (
        None, "not resolved, the owner is not a GitHub owner name")
    assert fake.calls == []


def test_owner_grammar_accepts_the_longest_owner_name():
    fake = api({f"{API_REPOS}{'a' * 39}/skills/git/ref/tags/v1.3.0": ok(ref_body("commit", SDLC_SHA))})
    assert cm.resolve_commit("a" * 39, "skills", "v1.3.0", fake) == (SDLC_SHA, None)


@pytest.mark.parametrize("repository", ["..", ".", "a/b", "a b", "", "a" * 101, "a%2f"],
                         ids=["dot-dot", "dot", "slash", "space", "empty", "too-long", "percent"])
def test_a_repository_off_the_grammar_is_refused_and_nothing_is_fetched(repository):
    fake = api({})
    assert cm.resolve_commit("someone", repository, "v1.3.0", fake) == (
        None, "not resolved, the repository is not a GitHub repository name")
    assert fake.calls == []


@pytest.mark.parametrize("tag", ["v1.3.0/x", "v1.3.0/../x", "v1.3.0?x", "v1.3.0#x", "v1.3.0%2f", "main"],
                         ids=["slash", "dot-dot", "query", "fragment", "percent", "branch"])
def test_a_tag_off_the_grammar_is_refused_and_nothing_is_fetched(tag):
    fake = api({})
    assert cm.resolve_commit("someone", "skills", tag, fake) == (
        None, "not resolved, the tag is not a release tag vX.Y.Z")
    assert fake.calls == []


def test_a_table_repository_off_the_grammar_is_not_resolved(tmp_path, local, monkeypatch):
    write_tree(tmp_path, GOOD_MARKETPLACE, GOOD_PLUGIN)
    monkeypatch.setitem(cm.EXTERNAL_PLUGINS, "sdlc", {**cm.EXTERNAL_PLUGINS["sdlc"], "repository": ".."})
    monkeypatch.setattr(cm, "external_problems", lambda *args: [])
    fake = api({})
    assert resolve(tmp_path, fake) == [
        "plugin sdlc: not resolved, the repository is not a GitHub repository name"]
    assert fake.calls == [LEDGER_REF], "only the entry with a clean repository was fetched"


def test_a_pin_owner_off_the_owner_grammar_is_not_resolved(tmp_path, local):
    market = copy.deepcopy(GOOD_MARKETPLACE)
    set_key(external(market, "sdlc"), "url", "https://github.com/a.b/skills.git")
    set_key(external(market, "ledger-hooks"), "url", "https://github.com/a.b/ledger.git")
    write_tree(tmp_path, market, GOOD_PLUGIN)
    write_pin(tmp_path, "repo a.b/ledger\n")
    fake = api({})
    assert resolve(tmp_path, fake) == [
        "plugin sdlc: not resolved, the owner is not a GitHub owner name",
        "plugin ledger-hooks: not resolved, the owner is not a GitHub owner name"]
    assert fake.calls == []


# Only an entry that passed every offline check is resolved.

OFFLINE_FAILURES = [
    # (id, change to the market dict, the plugin that must not be resolved)
    ("short-pin", lambda m: set_key(external(m, "sdlc"), "sha", SDLC_SHA[:39]), "sdlc"),
    ("branch-ref", lambda m: set_key(external(m, "ledger-hooks"), "ref", "main"), "ledger-hooks"),
    ("other-repository", lambda m: set_key(external(m, "sdlc"), "url", LEDGER_URL), "sdlc"),
    ("other-path", lambda m: set_key(external(m, "ledger-hooks"), "path", "plugins/other"), "ledger-hooks"),
    ("unknown-key", lambda m: set_key(external(m, "sdlc"), "branch", "main"), "sdlc"),
    ("listed-twice", lambda m: m["plugins"].append(copy.deepcopy(entry_of(m, "ledger-hooks"))), "ledger-hooks"),
    ("folder-source", lambda m: set_key(entry_of(m, "sdlc"), "source", "./plugins/harness"), "sdlc"),
    ("an-option-as-url", lambda m: set_key(external(m, "sdlc"), "url", "--upload-pack=touch /x"), "sdlc"),
]


@pytest.mark.parametrize("change,name", [(case[1], case[2]) for case in OFFLINE_FAILURES],
                         ids=[case[0] for case in OFFLINE_FAILURES])
def test_an_entry_that_failed_the_offline_check_is_not_resolved(tmp_path, local, change, name):
    market = copy.deepcopy(GOOD_MARKETPLACE)
    change(market)
    write_tree(tmp_path, market, GOOD_PLUGIN)
    assert problems(tmp_path), "the offline check refuses the change"
    fake = api(chain(0))
    assert resolve(tmp_path, fake) == [f"plugin {name}: not resolved, it failed the offline check"]
    other = {"sdlc": SDLC_REF, "ledger-hooks": LEDGER_REF}
    del other[name]
    assert fake.calls == list(other.values()), "only the clean entry was fetched"


# The owner anchor.

def test_the_anchor_is_compared_case_insensitively(tmp_path, local):
    write_tree(tmp_path, GOOD_MARKETPLACE, GOOD_PLUGIN)
    fake = api(chain(0))
    assert resolve(tmp_path, fake, "SomeOne") == []
    assert fake.calls == [SDLC_REF, LEDGER_REF]


@pytest.mark.parametrize("ci", [False, True], ids=["local", "in-ci"])
def test_an_anchor_that_differs_from_the_pin_owner_is_refused_before_any_request(tmp_path, monkeypatch, ci):
    if ci:
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
    else:
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    write_tree(tmp_path, GOOD_MARKETPLACE, GOOD_PLUGIN)
    fake = api(chain(0))
    assert resolve(tmp_path, fake, "other") == [
        "owner anchor: the owner in ledger-vendor.pin is not --expect-owner; refused before any request"]
    assert fake.calls == []


@pytest.mark.parametrize("anchor", ["a/b", "..", "some one"], ids=["slash", "dot-dot", "space"])
def test_an_anchor_off_the_owner_grammar_is_refused_before_any_request(tmp_path, local, anchor):
    write_tree(tmp_path, GOOD_MARKETPLACE, GOOD_PLUGIN)
    fake = api(chain(0))
    assert resolve(tmp_path, fake, anchor) == [
        "owner anchor: --expect-owner is not a GitHub owner name; refused before any request"]
    assert fake.calls == []


@pytest.mark.parametrize("anchor", [None, ""], ids=["missing", "empty"])
def test_in_ci_a_missing_or_empty_anchor_is_refused_before_any_request(tmp_path, in_ci, anchor):
    write_tree(tmp_path, GOOD_MARKETPLACE, GOOD_PLUGIN)
    fake = api(chain(0))
    assert resolve(tmp_path, fake, anchor) == [
        "no owner anchor in CI (--expect-owner is missing or empty); refused before any request"]
    assert fake.calls == []


def test_in_ci_the_anchor_lets_the_check_run(tmp_path, in_ci):
    write_tree(tmp_path, GOOD_MARKETPLACE, GOOD_PLUGIN)
    fake = api(chain(0))
    assert resolve(tmp_path, fake, "someone") == []
    assert fake.calls == [SDLC_REF, LEDGER_REF]


def test_a_local_run_without_an_anchor_says_so_in_one_line(local, monkeypatch, capsys):
    seen: list = []
    monkeypatch.setattr(cm, "source_problems", lambda root, owner: seen.append(owner) or [])
    assert cm.main(["--source"]) == 0
    printed = capsys.readouterr().out.splitlines()
    assert printed.count(cm.NO_ANCHOR) == 1
    assert cm.NO_ANCHOR.startswith("marketplace: no owner anchor: ")
    assert seen == [None]
    assert cm.main(["--source", "--expect-owner", "someone"]) == 0
    assert cm.NO_ANCHOR not in capsys.readouterr().out, "an anchored run prints no such line"


def test_in_ci_a_run_without_an_anchor_fails_and_prints_no_local_line(in_ci, capsys):
    assert cm.main(["--source"]) == 1
    printed = capsys.readouterr().out
    assert cm.NO_ANCHOR not in printed
    assert ("marketplace: refused: no owner anchor in CI (--expect-owner is missing or empty); "
            "refused before any request") in printed.splitlines()


# The CI tripwire: in CI, a proxy or CA-override variable refuses --source
# before any request. GITHUB_ACTIONS present with any value is CI.

TRIPPED = ["HTTPS_PROXY", "https_proxy", "Https_Proxy", "hTTPS_proxy", "ALL_PROXY", "all_proxy",
           "SSL_CERT_FILE", "ssl_cert_dir", "Ssl_Cert_File"]


@pytest.mark.parametrize("ci_value", ["true", "1", ""], ids=["true", "one", "empty"])
@pytest.mark.parametrize("variable", TRIPPED)
def test_in_ci_a_proxy_or_ca_variable_is_refused_by_name_before_any_request(
        tmp_path, monkeypatch, variable, ci_value):
    monkeypatch.setenv("GITHUB_ACTIONS", ci_value)
    monkeypatch.setenv(variable, "http://proxy.example:3128")
    write_tree(tmp_path, GOOD_MARKETPLACE, GOOD_PLUGIN)
    fake = api(chain(0))
    assert resolve(tmp_path, fake, "someone") == [
        f"proxy or CA override set in CI ({variable}); refused before any request"]
    assert fake.calls == []


@pytest.mark.parametrize("variable,value", [("HTTPS_PROXY", ""), ("SSL_CERT_FILE", ""),
                                            ("HTTPS_PROXY_", "http://proxy.example:3128"),
                                            ("HTTP_PROXY", "http://proxy.example:3128"),
                                            ("MY_SSL_CERT_DIR", "/x")],
                         ids=["empty-proxy", "empty-ca", "near-miss-name", "http-proxy", "prefixed-name"])
def test_in_ci_an_empty_or_other_variable_does_not_trip(tmp_path, in_ci, monkeypatch, variable, value):
    monkeypatch.setenv(variable, value)
    write_tree(tmp_path, GOOD_MARKETPLACE, GOOD_PLUGIN)
    fake = api(chain(0))
    assert resolve(tmp_path, fake, "someone") == []
    assert fake.calls == [SDLC_REF, LEDGER_REF]


@pytest.mark.parametrize("variable", TRIPPED)
def test_outside_ci_a_proxy_or_ca_variable_does_not_trip(tmp_path, local, monkeypatch, variable):
    monkeypatch.setenv(variable, "http://proxy.example:3128")
    write_tree(tmp_path, GOOD_MARKETPLACE, GOOD_PLUGIN)
    fake = api(chain(0))
    assert resolve(tmp_path, fake) == []
    assert fake.calls == [SDLC_REF, LEDGER_REF]


# Any exception the resolver raises is a named refusal, never a traceback.

def test_an_exception_in_the_resolver_is_a_named_refusal(tmp_path, local):
    write_tree(tmp_path, GOOD_MARKETPLACE, GOOD_PLUGIN)
    fake = api({SDLC_REF: RecursionError("maximum recursion depth exceeded")})
    assert resolve(tmp_path, fake) == ["the resolver raised RecursionError; nothing is attested"]


def test_an_exception_anywhere_in_the_check_fails_the_run_by_name(local, monkeypatch, capsys):
    def boom(*args):
        raise RecursionError("deep")
    monkeypatch.setattr(cm, "problems", boom)
    assert cm.main(["--source", "--expect-owner", "someone"]) == 1
    printed = capsys.readouterr().out.splitlines()
    assert "marketplace: refused: the check raised RecursionError; nothing is attested" in printed
    assert printed[-1] == "marketplace: FAIL: 1 refusal(s)"


def test_source_mode_refusals_fail_the_run_and_are_printed(local, monkeypatch, capsys):
    refusal = "plugin sdlc: status 404"
    monkeypatch.setattr(cm, "source_problems", lambda root, owner: [refusal])
    assert cm.main(["--source", "--expect-owner", "someone"]) == 1
    printed = capsys.readouterr().out
    assert f"marketplace: refused: {refusal}" in printed
    assert "marketplace: FAIL: 1 refusal(s)" in printed
    assert cm.main([]) == 0, "offline mode never reads the network"


# The default fetch, driven directly with no network: urllib's own opener
# (make_opener) with its transport replaced by a local handler that answers
# from a table.

CAP = 256 * 1024
MISSING = object()


class Body:
    """A response body; `fail` makes a read raise it (a truncated read)."""

    def __init__(self, data: bytes = b"", fail: BaseException | None = None):
        self.stream = io.BytesIO(data)
        self.fail = fail
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        if self.fail is not None:
            raise self.fail
        return self.stream.read(size)

    def readline(self, size: int = -1) -> bytes:
        return self.stream.readline(size)

    def close(self) -> None:
        self.closed = True


def message(headers: dict) -> email.message.Message:
    found = email.message.Message()
    for name, value in headers.items():
        found[name] = value
    return found


def http_error(url: str, status: int, headers: dict, data: bytes = b"{}") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, status, "answer", message(headers), Body(data))


class Transport(urllib.request.BaseHandler):
    """Answers https requests from a table in place of the network, recording
    each Request. An answer is (status, headers, body bytes or Body), or an
    exception to raise, or a callable that makes one of those afresh for each
    request. A url not in the table raises KeyError."""

    def __init__(self, table: dict, order: int):
        self.table = table
        self.handler_order = order
        self.seen: list[urllib.request.Request] = []
        self.timeouts: list = []

    def https_open(self, request):
        self.seen.append(request)
        answer = self.table[request.full_url]
        if callable(answer):
            answer = answer()
        if isinstance(answer, BaseException):
            raise answer
        status, headers, data = answer
        body = data if isinstance(data, Body) else Body(data)
        response = urllib.response.addinfourl(body, message(headers), request.full_url, code=status)
        response.msg = "answer"
        return response


@pytest.fixture
def transport(monkeypatch):
    """install(table, order=50) puts a Transport into every opener make_opener
    builds and records the timeout each open is given. Order 50 answers before
    urllib's proxy handler (100); order 150 answers after it."""
    real = cm.make_opener

    def install(table: dict, order: int = 50) -> Transport:
        handler = Transport(table, order)

        def opener():
            built = real()
            built.add_handler(handler)
            original = built.open

            def spy(request, data=None, timeout=MISSING):
                handler.timeouts.append(timeout)
                if timeout is MISSING:
                    return original(request, data)
                return original(request, data, timeout)
            built.open = spy
            return built
        monkeypatch.setattr(cm, "make_opener", opener)
        return handler
    return install


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_the_redirect_handler_follows_no_redirect(code):
    request = urllib.request.Request(SDLC_REF)
    headers = message({"Location": "https://evil.example/x"})
    assert cm.NoRedirect().redirect_request(request, Body(), code, "moved", headers, "https://evil.example/x") is None


def test_a_301_is_refused_by_its_status_and_the_location_is_never_requested(tmp_path, local, transport):
    write_tree(tmp_path, GOOD_MARKETPLACE, GOOD_PLUGIN)
    handler = transport({
        SDLC_REF: (301, {"Location": "https://evil.example/x"}, b""),
        LEDGER_REF: ok(ref_body("commit", LEDGER_SHA, "refs/tags/v0.3.1")),
    })
    assert cm.api_fetch(SDLC_REF)[0] == 301
    assert resolve(tmp_path, cm.api_fetch) == ["plugin sdlc: redirect 301 (renamed or transferred repository?)"]
    assert [request.full_url for request in handler.seen] == [SDLC_REF, SDLC_REF, LEDGER_REF]


@pytest.mark.parametrize("status", [403, 429])
def test_a_rate_limit_raised_as_http_error_is_the_named_refusal(tmp_path, local, transport, status):
    write_tree(tmp_path, GOOD_MARKETPLACE, GOOD_PLUGIN)
    transport({
        SDLC_REF: lambda: http_error(SDLC_REF, status, {"X-RateLimit-Remaining": "0"},
                                     b'{"message": "API rate limit exceeded"}'),
        LEDGER_REF: ok(ref_body("commit", LEDGER_SHA, "refs/tags/v0.3.1")),
    })
    answer_status, headers, body = cm.api_fetch(SDLC_REF)
    assert answer_status == status
    assert cm.header_value(headers, "x-ratelimit-remaining") == "0"
    assert body == b'{"message": "API rate limit exceeded"}'
    assert resolve(tmp_path, cm.api_fetch) == ["plugin sdlc: rate limited; rerun"]


def test_a_forbidden_answer_with_requests_left_is_the_generic_refusal(tmp_path, local, transport):
    write_tree(tmp_path, GOOD_MARKETPLACE, GOOD_PLUGIN)
    transport({
        SDLC_REF: http_error(SDLC_REF, 403, {"X-RateLimit-Remaining": "5"}),
        LEDGER_REF: ok(ref_body("commit", LEDGER_SHA, "refs/tags/v0.3.1")),
    })
    assert resolve(tmp_path, cm.api_fetch) == ["plugin sdlc: status 403"]


def test_a_rate_limit_status_through_the_opener_is_the_named_refusal(tmp_path, local, transport):
    """A 429 the transport answers (not raises) goes through urllib's error processor."""
    write_tree(tmp_path, GOOD_MARKETPLACE, GOOD_PLUGIN)
    transport({
        SDLC_REF: (429, {"x-ratelimit-remaining": "0"}, b"{}"),
        LEDGER_REF: ok(ref_body("commit", LEDGER_SHA, "refs/tags/v0.3.1")),
    })
    assert resolve(tmp_path, cm.api_fetch) == ["plugin sdlc: rate limited; rerun"]


RAISED = [
    ("timeout", TimeoutError("timed out"), "plugin sdlc: request failed (TimeoutError)"),
    ("dns", urllib.error.URLError("nodename nor servname provided"), "plugin sdlc: request failed (URLError)"),
    ("tls", ssl.SSLError("certificate verify failed"), "plugin sdlc: request failed (SSLError)"),
    ("truncated-read", (200, {}, Body(fail=http.client.IncompleteRead(b"{"))),
     "plugin sdlc: request failed (IncompleteRead)"),
    ("http-500", None, "plugin sdlc: status 500"),
]


@pytest.mark.parametrize("raised,expected", [(case[1], case[2]) for case in RAISED],
                         ids=[case[0] for case in RAISED])
def test_an_exception_of_the_default_fetch_is_a_named_refusal(tmp_path, local, transport, raised, expected):
    write_tree(tmp_path, GOOD_MARKETPLACE, GOOD_PLUGIN)
    answer = http_error(SDLC_REF, 500, {}) if raised is None else raised
    transport({SDLC_REF: answer, LEDGER_REF: ok(ref_body("commit", LEDGER_SHA, "refs/tags/v0.3.1"))})
    assert resolve(tmp_path, cm.api_fetch) == [expected]


def test_a_truncated_read_of_an_error_body_is_a_named_refusal(transport):
    transport({SDLC_REF: urllib.error.HTTPError(SDLC_REF, 403, "x", message({}),
                                                Body(fail=http.client.IncompleteRead(b"")))})
    with pytest.raises(cm.FetchFailed, match=r"^request failed \(IncompleteRead\) after status 403$"):
        cm.api_fetch(SDLC_REF)


def test_the_request_headers_are_unredirected_and_hold_no_credential(transport):
    handler = transport({SDLC_REF: ok(ref_body("commit", SDLC_SHA))})
    assert cm.api_fetch(SDLC_REF)[0] == 200
    (request,) = handler.seen
    unredirected = {name.casefold(): value for name, value in request.unredirected_hdrs.items()}
    assert unredirected["accept"] == "application/vnd.github+json"
    assert unredirected["x-github-api-version"] == "2022-11-28"
    assert unredirected["user-agent"] == "harness-core-check-marketplace"
    assert unredirected["cache-control"] == "no-cache", "a shared cache must not answer a moved tag's old commit"
    redirected = {name.casefold() for name in request.headers}
    assert not {"accept", "x-github-api-version", "user-agent", "cache-control"} & redirected
    for headers in (request.headers, request.unredirected_hdrs):
        assert "authorization" not in {name.casefold() for name in headers}
        assert "proxy-authorization" not in {name.casefold() for name in headers}
    assert request.get_method() == "GET"


def test_the_opener_is_given_the_twenty_second_timeout(transport):
    handler = transport({SDLC_REF: ok(ref_body("commit", SDLC_SHA))})
    cm.api_fetch(SDLC_REF)
    assert handler.timeouts == [20]


def test_a_body_over_the_cap_is_refused_and_one_at_the_cap_is_read(transport):
    transport({SDLC_REF: ok(b"x" * (CAP + 1)), LEDGER_REF: ok(b"x" * CAP)})
    with pytest.raises(cm.FetchFailed, match=r"^the answer body is over 256 KiB$"):
        cm.api_fetch(SDLC_REF)
    status, _, body = cm.api_fetch(LEDGER_REF)
    assert (status, len(body)) == (200, CAP)


@pytest.mark.parametrize("raise_it", [True, False], ids=["raised-http-error", "answered-status"])
def test_an_error_body_over_the_cap_is_refused(transport, raise_it):
    """The cap holds on the error path too (the rate-limit answer is read there)."""
    data = b"x" * (CAP + 5)
    headers = {"X-RateLimit-Remaining": "0"}
    answer = http_error(SDLC_REF, 403, headers, data) if raise_it else (403, headers, data)
    transport({SDLC_REF: answer, LEDGER_REF: http_error(LEDGER_REF, 429, headers, b"x" * CAP)})
    with pytest.raises(cm.FetchFailed, match=r"^the answer body is over 256 KiB$"):
        cm.api_fetch(SDLC_REF)
    status, _, body = cm.api_fetch(LEDGER_REF)
    assert (status, len(body)) == (429, CAP)


LOOKALIKES = [
    "http://api.github.com/repos/someone/skills/git/ref/tags/v1.3.0",
    "https://api.github.com.evil.example/repos/someone/skills/git/ref/tags/v1.3.0",
    "https://api.github.com@evil.example/repos/someone/skills/git/ref/tags/v1.3.0",
    "https://evil.example/?api.github.com",
    "https://api.github.com:8443/repos/someone/skills/git/ref/tags/v1.3.0",
    "https://user@api.github.com/repos/someone/skills/git/ref/tags/v1.3.0",
    "https://API.github.com/repos/someone/skills/git/ref/tags/v1.3.0",
    "https://[::1/x",
]


@pytest.mark.parametrize("url", LOOKALIKES, ids=["http", "suffix-host", "userinfo-host", "query",
                                                 "port", "userinfo", "uppercase-host", "bad-ipv6"])
def test_the_default_fetch_refuses_any_url_off_the_fixed_host(transport, url):
    handler = transport({url: ok(ref_body("commit", SDLC_SHA))})
    with pytest.raises(cm.FetchFailed, match=r"^request refused: the url is not on https://api\.github\.com$"):
        cm.api_fetch(url)
    assert handler.seen == [], "nothing was requested"


def test_outside_ci_a_proxy_is_honoured(local, monkeypatch, transport):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:3128")
    handler = transport({SDLC_REF: ok(ref_body("commit", SDLC_SHA))}, order=150)
    assert cm.api_fetch(SDLC_REF)[0] == 200
    (request,) = handler.seen
    assert request.host == "proxy.example:3128", "urllib routed the request through the proxy"
    assert request.full_url == SDLC_REF


# The isolated interpreter: a module beside the script cannot shadow an import.

def test_under_python_I_a_module_beside_the_script_is_not_imported(tmp_path):
    # The copy resolves its repository from its own path, so it gets a scripts/
    # folder. Fixture: GOOD_MARKETPLACE with sdlc's pin cut to 39 hex and
    # ledger-hooks's ref a branch, so no external entry is clean and --source
    # makes no request (both are "not resolved"); GITHUB_ACTIONS is deleted,
    # so the run is local and needs no anchor.
    market = copy.deepcopy(GOOD_MARKETPLACE)
    set_key(external(market, "sdlc"), "sha", SDLC_SHA[:39])
    set_key(external(market, "ledger-hooks"), "ref", "main")
    root = tmp_path / "repo"
    write_tree(root, market, GOOD_PLUGIN)
    (root / "scripts").mkdir()
    shutil.copyfile(SCRIPT, root / "scripts" / "check-marketplace.py")
    marker = tmp_path / "marker"
    (root / "scripts" / "json.py").write_text(
        f"import sys\nopen({str(marker)!r}, 'w').close()\nsys.exit(0)\n", encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if key != "GITHUB_ACTIONS"}
    done = subprocess.run([sys.executable, "-I", str(root / "scripts" / "check-marketplace.py"), "--source"],
                          cwd=root / "scripts", env=env, capture_output=True, text=True, timeout=60)
    assert not marker.exists(), "the planted json.py was imported"
    lines = done.stdout.splitlines()
    assert done.returncode == 1, done.stdout + done.stderr
    assert "marketplace: refused: plugin sdlc: not resolved, it failed the offline check" in lines
    assert "marketplace: refused: plugin ledger-hooks: not resolved, it failed the offline check" in lines
    assert lines[-1] == "marketplace: FAIL: 4 refusal(s)"


# The CI job, read as text.

GOOD_CI = """\
on: x
jobs:
  other:
    runs-on: x
  plugin-validate:
    name: plugin validate
    runs-on: ${{ fromJSON(vars.CORE_CI_RUNNER || '["ubuntu-latest"]') }}
    steps:
      - name: Check out the repository
        uses: actions/checkout@v7
        with:
          persist-credentials: false
      - name: The pins are the commits their tags name
        env:
          EXPECT_OWNER: ${{ github.repository_owner }}
        run: python3 -I scripts/check-marketplace.py --source --expect-owner "$EXPECT_OWNER"
      - name: Install Claude Code
        run: npm install --prefix "$RUNNER_TEMP/claude-code" "@anthropic-ai/claude-code@2.1.325"
      - name: Validate
        run: |
          home="$(mktemp -d "$RUNNER_TEMP/validate-home.XXXXXX")"
          claude="$RUNNER_TEMP/claude-code/node_modules/.bin/claude"
          HOME="$home" "$claude" plugin validate --strict .
          HOME="$home" "$claude" plugin validate --strict plugins/harness
"""
SOURCE_STEP_TEXT = (
    "      - name: The pins are the commits their tags name\n"
    "        env:\n"
    "          EXPECT_OWNER: ${{ github.repository_owner }}\n"
    "        run: python3 -I scripts/check-marketplace.py --source --expect-owner \"$EXPECT_OWNER\"\n")
STEP_ENV = "        env:\n          EXPECT_OWNER: ${{ github.repository_owner }}\n"
TOKEN = "plugin-validate: a token or secret reaches the job through an env block"
ANCHOR = "plugin-validate: the --source step does not pass --expect-owner from github.repository_owner"


def step_env_with(line: str):
    return lambda t: t.replace(STEP_ENV, STEP_ENV + f"          {line}\n")


CI_NEAR_MISSES = [
    ("job-missing", lambda t: t.replace("  plugin-validate:", "  plugin-check:"),
     "ci.yml: the plugin-validate job is missing"),
    ("runner-hardcoded", lambda t: t.replace("${{ fromJSON(vars.CORE_CI_RUNNER || '[\"ubuntu-latest\"]') }}", "ubuntu-latest"),
     "plugin-validate: does not run on the CORE_CI_RUNNER variable with the hosted default"),
    ("version-latest", lambda t: t.replace("claude-code@2.1.325", "claude-code@latest"),
     "plugin-validate: Claude Code is not installed at one exact version"),
    ("version-range", lambda t: t.replace("claude-code@2.1.325", "claude-code@^2.1.325"),
     "plugin-validate: Claude Code is not installed at one exact version"),
    ("version-absent", lambda t: t.replace("claude-code@2.1.325", "claude-code"),
     "plugin-validate: Claude Code is not installed at one exact version"),
    ("marketplace-not-strict", lambda t: t.replace("plugin validate --strict .", "plugin validate ."),
     "plugin-validate: the marketplace is not validated strict in a fresh HOME"),
    ("plugin-not-strict", lambda t: t.replace("plugin validate --strict plugins/harness", "plugin validate plugins/harness"),
     "plugin-validate: the harness plugin is not validated strict in a fresh HOME"),
    ("plugin-not-validated", lambda t: t.replace('          HOME="$home" "$claude" plugin validate --strict plugins/harness\n', ""),
     "plugin-validate: the harness plugin is not validated strict in a fresh HOME"),
    ("marketplace-without-fresh-home", lambda t: t.replace('HOME="$home" "$claude" plugin validate --strict .', '"$claude" plugin validate --strict .'),
     "plugin-validate: the marketplace is not validated strict in a fresh HOME"),
    ("home-not-made", lambda t: t.replace('home="$(mktemp -d "$RUNNER_TEMP/validate-home.XXXXXX")"', 'home="$HOME"'),
     "plugin-validate: the fresh HOME is not made with mktemp -d"),
    ("failure-allowed", lambda t: t.replace("plugins/harness\n", "plugins/harness || true\n"),
     "plugin-validate: the harness plugin is not validated strict in a fresh HOME"),
    ("continue-on-error", lambda t: t.replace("    steps:", "    continue-on-error: true\n    steps:"),
     "plugin-validate: a failure is allowed"),
    ("source-step-without-the-flag", lambda t: t.replace("check-marketplace.py --source", "check-marketplace.py"),
     "plugin-validate: the pins are not checked against their tags (check-marketplace.py --source)"),
    ("source-step-missing", lambda t: t.replace(SOURCE_STEP_TEXT, ""),
     "plugin-validate: the pins are not checked against their tags (check-marketplace.py --source)"),
    ("source-step-renamed-is-still-found", lambda t: t.replace(
        "The pins are the commits their tags name", "Something else").replace("python3 -I ", "python3 "),
     "plugin-validate: the --source step does not run under python3 -I"),
    ("source-step-without-I", lambda t: t.replace("python3 -I scripts/check-marketplace.py", "python3 scripts/check-marketplace.py"),
     "plugin-validate: the --source step does not run under python3 -I"),
    ("source-step-wrapped", lambda t: t.replace("run: python3 -I scripts/check-marketplace.py", "run: env python3 -I scripts/check-marketplace.py"),
     "plugin-validate: the --source step does not run under python3 -I"),
    ("anchor-flag-missing", lambda t: t.replace(' --expect-owner "$EXPECT_OWNER"', ""), ANCHOR),
    ("anchor-env-missing", lambda t: t.replace(STEP_ENV, ""), ANCHOR),
    ("anchor-from-a-literal", lambda t: t.replace("EXPECT_OWNER: ${{ github.repository_owner }}", "EXPECT_OWNER: someone"), ANCHOR),
    ("anchor-from-the-actor", lambda t: t.replace("github.repository_owner", "github.actor"), ANCHOR),
    ("anchor-set-twice", step_env_with("EXPECT_OWNER: someone"), ANCHOR),
    ("anchor-flag-unquoted", lambda t: t.replace('"$EXPECT_OWNER"', "$EXPECT_OWNER"), ANCHOR),
    ("anchor-plus-another-flag", lambda t: t.replace('"$EXPECT_OWNER"', '"$EXPECT_OWNER" --x'), ANCHOR),
    ("job-env-github-token", lambda t: t.replace("    steps:", "    env:\n      GH_TOKEN: ${{ github.token }}\n    steps:"), TOKEN),
    ("step-env-secrets-token", step_env_with("GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}"), TOKEN),
    ("step-env-any-secret", step_env_with("X: ${{ secrets.ANY }}"), TOKEN),
    ("step-env-secret-index", step_env_with("X: ${{ secrets['ANY'] }}"), TOKEN),
    ("step-env-whole-github", step_env_with("CONTEXT: ${{ toJSON(github) }}"), TOKEN),
    ("step-env-whole-github-spaced", step_env_with("CONTEXT: ${{ toJson( github ) }}"), TOKEN),
    ("step-env-token-single-index", step_env_with("X: ${{ github['token'] }}"), TOKEN),
    ("step-env-token-double-index", step_env_with('X: ${{ github["token"] }}'), TOKEN),
    ("step-env-token-dot-uppercase", step_env_with("X: ${{ GITHUB.TOKEN }}"), TOKEN),
    ("step-env-gh-token-name", step_env_with("GH_TOKEN: x"), TOKEN),
    ("workflow-env-token", lambda t: t.replace("jobs:\n", "env:\n  T: ${{ github.token }}\njobs:\n", 1), TOKEN),
    ("other-step-env-token", lambda t: t.replace(
        "      - name: Install Claude Code\n",
        "      - name: Install Claude Code\n        env:\n          GH_TOKEN: ${{ github.token }}\n"), TOKEN),
    ("persist-credentials-true", lambda t: t.replace("persist-credentials: false", "persist-credentials: true"),
     "plugin-validate: the checkout does not set persist-credentials: false"),
    ("checkout-without-the-input", lambda t: t.replace("        with:\n          persist-credentials: false\n", ""),
     "plugin-validate: the checkout does not set persist-credentials: false"),
]


def test_the_good_ci_text_is_clean():
    assert ci_problems(GOOD_CI) == []


@pytest.mark.parametrize("change,expected", [(case[1], case[2]) for case in CI_NEAR_MISSES],
                         ids=[case[0] for case in CI_NEAR_MISSES])
def test_a_ci_near_miss_is_refused_by_name(change, expected):
    changed = change(GOOD_CI)
    assert changed != GOOD_CI, "the change did something"
    assert expected in ci_problems(changed)
