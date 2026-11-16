"""The marketplace manifest, the harness plugin's manifest, and the CI job that validates them.

The invariant every tag of this repository keeps: the marketplace lists each
plugin once; the in-repo `harness` plugin is a folder here at the same tag; each
external plugin is the one expected source for its name, fixed to a full 40-hex
commit on a release tag; nothing resolves to a branch or a range. The checks
live in scripts/check-marketplace.py (its docstring holds the grammar and the
one table of expected external sources); this file imports that script and
drives it. The two manifests are read as JSON and nothing is fetched here: the
tag-against-pin comparison is tested with a fake `git ls-remote` output, and
the real `git ls-remote` is run only against a repository made in a scratch
folder, so this runs offline on the runner's own python3.

The CI job `plugin-validate` in `.github/workflows/ci.yml` is read as text:
Claude Code is installed at one exact version, both validate commands run
strict in a fresh HOME, and the pins are checked against their tags.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import re
import shutil
import subprocess
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


# The CI job, read as text.

SOURCE_STEP = "python3 scripts/check-marketplace.py --source"


def ci_job(text: str) -> str | None:
    match = re.search(r"^  plugin-validate:\n((?:    .*\n|\n)+)", text, re.MULTILINE)
    return match.group(1) if match else None


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
    sources = [line for line in job.split("\n") if line.strip() == f"run: {SOURCE_STEP}"]
    if len(sources) != 1:
        found.append("plugin-validate: the pins are not checked against their tags (check-marketplace.py --source)")
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


# --source: the tag each external entry names is the commit its pin names.
# The resolver is given fake `git ls-remote` output; nothing here reaches a network.

TAG_OBJECT = "5" * 40
OTHER_COMMIT = "7" * 40


def tag_lines(*pairs: tuple[str, str]) -> str:
    return "".join(f"{commit}\t{ref}\n" for commit, ref in pairs)


LEDGER_LIGHTWEIGHT = tag_lines((LEDGER_SHA, "refs/tags/v0.3.1"))
SOURCE_CASES = [
    # (id, ls-remote exit code and output for sdlc, the refusals expected)
    ("lightweight-tag-at-the-pin", (0, tag_lines((SDLC_SHA, "refs/tags/v1.3.0"))), []),
    ("annotated-tag-peels-to-the-pin",
     (0, tag_lines((TAG_OBJECT, "refs/tags/v1.3.0"), (SDLC_SHA, "refs/tags/v1.3.0^{}"))), []),
    ("annotated-tag-peeled-line-first",
     (0, tag_lines((SDLC_SHA, "refs/tags/v1.3.0^{}"), (TAG_OBJECT, "refs/tags/v1.3.0"))), []),
    ("annotated-tag-whose-tag-object-is-the-pin-but-peels-elsewhere",
     (0, tag_lines((SDLC_SHA, "refs/tags/v1.3.0"), (OTHER_COMMIT, "refs/tags/v1.3.0^{}"))),
     [f"plugin sdlc: tag v1.3.0 resolves to {OTHER_COMMIT}, not the pin {SDLC_SHA}"]),
    ("lightweight-tag-at-another-commit",
     (0, tag_lines((OTHER_COMMIT, "refs/tags/v1.3.0"))),
     [f"plugin sdlc: tag v1.3.0 resolves to {OTHER_COMMIT}, not the pin {SDLC_SHA}"]),
    ("annotated-tag-peels-to-another-commit",
     (0, tag_lines((TAG_OBJECT, "refs/tags/v1.3.0"), (OTHER_COMMIT, "refs/tags/v1.3.0^{}"))),
     [f"plugin sdlc: tag v1.3.0 resolves to {OTHER_COMMIT}, not the pin {SDLC_SHA}"]),
    ("tag-missing", (0, ""),
     ["plugin sdlc: tag v1.3.0 is not in the source repository"]),
    ("ls-remote-fails", (128, ""),
     ["plugin sdlc: git ls-remote failed (exit 128)"]),
    ("ls-remote-cannot-run", (255, ""),
     ["plugin sdlc: git ls-remote failed (exit 255)"]),
    ("another-ref-in-the-output",
     (0, tag_lines((SDLC_SHA, "refs/tags/v1.3.0"), (SDLC_SHA, "refs/tags/v9.9.9"))),
     ["plugin sdlc: ls-remote output is not <40 hex><TAB>refs/tags/<tag> lines of the tag and its peeled form"]),
    ("the-tag-twice",
     (0, tag_lines((SDLC_SHA, "refs/tags/v1.3.0"), (SDLC_SHA, "refs/tags/v1.3.0"))),
     ["plugin sdlc: ls-remote output is not <40 hex><TAB>refs/tags/<tag> lines of the tag and its peeled form"]),
    ("a-line-off-the-grammar", (0, "not a ref line\n"),
     ["plugin sdlc: ls-remote output is not <40 hex><TAB>refs/tags/<tag> lines of the tag and its peeled form"]),
    ("a-short-id", (0, "abc\trefs/tags/v1.3.0\n"),
     ["plugin sdlc: ls-remote output is not <40 hex><TAB>refs/tags/<tag> lines of the tag and its peeled form"]),
]


def fake_remote(sdlc: tuple[int, str], calls: list | None = None):
    """A stand-in for `git ls-remote`: (url, tag) -> (exit code, output)."""
    answers = {(SKILLS_URL, "v1.3.0"): sdlc, (LEDGER_URL, "v0.3.1"): (0, LEDGER_LIGHTWEIGHT)}

    def run(url: str, tag: str) -> tuple[int, str]:
        if calls is not None:
            calls.append((url, tag))
        return answers[(url, tag)]
    return run


@pytest.mark.parametrize("answer,expected", [(case[1], case[2]) for case in SOURCE_CASES],
                         ids=[case[0] for case in SOURCE_CASES])
def test_a_tag_is_compared_with_its_pin_by_name(tmp_path, answer, expected):
    write_tree(tmp_path, GOOD_MARKETPLACE, GOOD_PLUGIN)
    assert cm.source_problems(tmp_path, fake_remote(answer)) == expected


def test_each_external_entry_is_resolved_against_the_repository_it_names(tmp_path):
    write_tree(tmp_path, GOOD_MARKETPLACE, GOOD_PLUGIN)
    calls: list = []
    answer = (0, tag_lines((SDLC_SHA, "refs/tags/v1.3.0")))
    assert cm.source_problems(tmp_path, fake_remote(answer, calls)) == []
    assert calls == [(SKILLS_URL, "v1.3.0"), (LEDGER_URL, "v0.3.1")]


def test_a_wrong_ledger_hooks_pin_is_refused_by_its_own_name(tmp_path):
    market = copy.deepcopy(GOOD_MARKETPLACE)
    set_key(external(market, "ledger-hooks"), "sha", OTHER_COMMIT)
    write_tree(tmp_path, market, GOOD_PLUGIN)
    answer = (0, tag_lines((SDLC_SHA, "refs/tags/v1.3.0")))
    assert cm.source_problems(tmp_path, fake_remote(answer)) == [
        f"plugin ledger-hooks: tag v0.3.1 resolves to {LEDGER_SHA}, not the pin {OTHER_COMMIT}"]


def test_source_mode_with_no_readable_marketplace_leaves_the_naming_to_the_offline_check(tmp_path):
    assert cm.source_problems(tmp_path, fake_remote((0, ""))) == []
    assert problems(tmp_path) == [".claude-plugin/marketplace.json: is missing"]


# The real `git ls-remote`, against a repository made in a scratch folder (no network).

def git(repo: Path, *args: str) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.test", "-c", "commit.gpgsign=false",
         "-c", "tag.gpgsign=false", *args],
        cwd=repo, env=env, check=True, capture_output=True, text=True)
    return done.stdout.strip()


def test_the_real_ls_remote_peels_an_annotated_tag_and_keeps_a_lightweight_one(tmp_path):
    repo = tmp_path / "remote"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "a").write_text("a", encoding="utf-8")
    git(repo, "add", "a")
    git(repo, "commit", "-q", "-m", "first")
    first = git(repo, "rev-parse", "HEAD")
    git(repo, "tag", "-a", "v1.0.0", "-m", "annotated")
    (repo / "a").write_text("b", encoding="utf-8")
    git(repo, "commit", "-q", "-am", "second")
    second = git(repo, "rev-parse", "HEAD")
    git(repo, "tag", "v2.0.0")
    tag_object = git(repo, "rev-parse", "refs/tags/v1.0.0")
    assert tag_object != first, "the annotated tag is its own object"

    code, output = cm.git_ls_remote(str(repo), "v1.0.0")
    assert code == 0
    assert cm.resolve_tag(output, "v1.0.0") == (first, None)
    code, output = cm.git_ls_remote(str(repo), "v2.0.0")
    assert code == 0
    assert cm.resolve_tag(output, "v2.0.0") == (second, None)
    code, output = cm.git_ls_remote(str(repo), "v3.0.0")
    assert code == 0
    assert cm.resolve_tag(output, "v3.0.0") == (None, "tag v3.0.0 is not in the source repository")
    code, _ = cm.git_ls_remote(str(tmp_path / "no-such-repository"), "v1.0.0")
    assert code != 0


def test_a_git_that_cannot_run_is_a_failure_code_never_a_pass(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    code, output = cm.git_ls_remote("https://example.test/skills.git", "v1.0.0")
    assert code == 255
    assert output == ""


def test_source_mode_refusals_fail_the_run_and_are_printed(monkeypatch, capsys):
    refusal = "plugin sdlc: tag v1.3.0 is not in the source repository"
    monkeypatch.setattr(cm, "source_problems", lambda root: [refusal])
    assert cm.main(["--source"]) == 1
    printed = capsys.readouterr().out
    assert f"marketplace: refused: {refusal}" in printed
    assert "marketplace: FAIL: 1 refusal(s)" in printed
    assert cm.main([]) == 0, "offline mode never reads the network"


# The CI job, read as text.

GOOD_CI = """\
jobs:
  other:
    runs-on: x
  plugin-validate:
    name: plugin validate
    runs-on: ${{ fromJSON(vars.CORE_CI_RUNNER || '["ubuntu-latest"]') }}
    steps:
      - name: The pins are the commits their tags name
        run: python3 scripts/check-marketplace.py --source
      - name: Install Claude Code
        run: npm install --prefix "$RUNNER_TEMP/claude-code" "@anthropic-ai/claude-code@2.1.325"
      - name: Validate
        run: |
          home="$(mktemp -d "$RUNNER_TEMP/validate-home.XXXXXX")"
          claude="$RUNNER_TEMP/claude-code/node_modules/.bin/claude"
          HOME="$home" "$claude" plugin validate --strict .
          HOME="$home" "$claude" plugin validate --strict plugins/harness
"""

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
    ("source-step-missing", lambda t: t.replace(
        "      - name: The pins are the commits their tags name\n"
        "        run: python3 scripts/check-marketplace.py --source\n", ""),
     "plugin-validate: the pins are not checked against their tags (check-marketplace.py --source)"),
]


def test_the_good_ci_text_is_clean():
    assert ci_problems(GOOD_CI) == []


@pytest.mark.parametrize("change,expected", [(case[1], case[2]) for case in CI_NEAR_MISSES],
                         ids=[case[0] for case in CI_NEAR_MISSES])
def test_a_ci_near_miss_is_refused_by_name(change, expected):
    changed = change(GOOD_CI)
    assert changed != GOOD_CI, "the change did something"
    assert expected in ci_problems(changed)
