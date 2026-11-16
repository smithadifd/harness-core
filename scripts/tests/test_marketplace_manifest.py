"""The marketplace manifest, the harness plugin's manifest, and the CI job that validates them.

The invariant every tag of this repository keeps: the marketplace lists each
plugin once; the in-repo `harness` plugin is a folder here at the same tag; each
external plugin is fixed to a full 40-hex commit on a release tag; nothing
resolves to a branch or a range. The two manifests are read as JSON and nothing
is fetched, so this runs offline on the runner's own python3.

Grammar (anything else is refused, each by the name shown in `problems`):

- `.claude-plugin/marketplace.json`: one JSON object with no duplicate key, with
  `name` equal to `harness-core`, `owner.name`, and a non-empty `plugins` list.
- Each plugin entry: an object whose `name` is one of `harness`, `sdlc`,
  `ledger-hooks`, `rails`, listed once, and whose `source` is either
  - a `./<folder>` path inside this repository (no `..`, no backslash, no
    symlink that leaves the repository), whose folder exists and holds
    `.claude-plugin/plugin.json`; or
  - an object of exactly one of three shapes, each with a `ref` that is a
    release tag `vX.Y.Z` and a `sha` that is a full lowercase 40-hex commit id:
    `{"source": "github", "repo": "owner/name"}`,
    `{"source": "url", "url": "https://..."}` or
    `{"source": "git-subdir", "url": "https://...", "path": "<folder>"}`.
    Every other source type (npm, archive, command) and every other key is refused.
- `harness` is listed and is a folder in this repository.
- A folder plugin's manifest: one JSON object with no duplicate key, whose `name`
  equals its entry's `name`, with a `version` that is `X.Y.Z` set in the manifest
  only (not also in the entry), and `dependencies` (when present) a list of plain
  names, each another entry of this marketplace (no version range, no
  `name@marketplace`, no object form, not itself).

The CI job `plugin-validate` in `.github/workflows/ci.yml` is read as text:
Claude Code is installed at one exact version and both validate commands run
strict in a fresh HOME.
"""
from __future__ import annotations

import copy
import json
import re
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MARKETPLACE = ".claude-plugin/marketplace.json"
PLUGIN_MANIFEST = ".claude-plugin/plugin.json"
CI = ".github/workflows/ci.yml"
MARKETPLACE_NAME = "harness-core"
PLUGIN_NAMES = ("harness", "sdlc", "ledger-hooks", "rails")
FULL_COMMIT = re.compile(r"[0-9a-f]{40}")
RELEASE_TAG = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+")
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
LOCAL_SOURCE = re.compile(r"\./[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*")
OWNER_REPO = re.compile(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+")
# The fields each external source type needs besides source, ref and sha.
EXTERNAL_SOURCES = {
    "github": ("repo",),
    "url": ("url",),
    "git-subdir": ("url", "path"),
}


def load_json(path: Path):
    """The file's JSON, refusing a duplicate key (json.loads would keep the last)."""
    def pairs(items):
        found = {}
        for key, value in items:
            if key in found:
                raise ValueError(f"duplicate key {key!r}")
            found[key] = value
        return found
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs)


def external_problems(label: str, source: dict) -> list[str]:
    kind = source.get("source")
    if kind not in EXTERNAL_SOURCES:
        return [f"{label}: source type {kind!r} is not allowed (github, url or git-subdir only)"]
    found: list[str] = []
    needed = EXTERNAL_SOURCES[kind]
    unknown = sorted(set(source) - {"source", "ref", "sha", *needed})
    if unknown:
        found.append(f"{label}: unknown source key {unknown[0]!r}")
    for key in needed:
        if not isinstance(source.get(key), str) or not source[key]:
            found.append(f"{label}: source field {key!r} is missing")
    url = source.get("url")
    if isinstance(url, str) and not url.startswith("https://"):
        found.append(f"{label}: source url is not https")
    repo = source.get("repo")
    if isinstance(repo, str) and not OWNER_REPO.fullmatch(repo):
        found.append(f"{label}: source repo is not owner/name")
    ref = source.get("ref")
    if ref is None:
        found.append(f"{label}: no ref")
    elif not isinstance(ref, str) or not RELEASE_TAG.fullmatch(ref):
        found.append(f"{label}: ref is not a release tag vX.Y.Z")
    sha = source.get("sha")
    if sha is None:
        found.append(f"{label}: no pin (sha missing)")
    elif not isinstance(sha, str) or not FULL_COMMIT.fullmatch(sha):
        found.append(f"{label}: pin is not a full 40-hex commit id")
    return found


def folder_problems(root: Path, label: str, entry: dict, source: str) -> tuple[list[str], list]:
    """(problems, dependencies) of a plugin that is a folder in this repository."""
    if not LOCAL_SOURCE.fullmatch(source) or ".." in source.split("/"):
        return [f"{label}: source is not a ./ folder path inside the repository"], []
    folder = root / source[2:]
    if not folder.resolve().is_relative_to(root.resolve()):
        return [f"{label}: source resolves outside the repository"], []
    if not folder.is_dir():
        return [f"{label}: folder {source} is missing"], []
    manifest = folder / PLUGIN_MANIFEST
    if not manifest.is_file():
        return [f"{label}: manifest {source}/{PLUGIN_MANIFEST} is missing"], []
    try:
        data = load_json(manifest)
    except ValueError as exc:
        return [f"{label}: manifest is not valid JSON ({exc})"], []
    if not isinstance(data, dict):
        return [f"{label}: manifest is not a JSON object"], []
    found: list[str] = []
    if data.get("name") != entry.get("name"):
        found.append(f"{label}: manifest name {data.get('name')!r} differs from the entry name")
    version = data.get("version")
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        found.append(f"{label}: manifest version is missing or not X.Y.Z")
    if "version" in entry:
        found.append(f"{label}: version is set in the entry and in the manifest")
    dependencies = data.get("dependencies", [])
    if not isinstance(dependencies, list) or not all(isinstance(d, str) for d in dependencies):
        found.append(f"{label}: dependencies must be a list of plain plugin names, no range")
        dependencies = []
    return found, dependencies


def problems(root: Path) -> list[str]:
    path = root / MARKETPLACE
    if not path.is_file():
        return [f"{MARKETPLACE}: is missing"]
    try:
        data = load_json(path)
    except ValueError as exc:
        return [f"{MARKETPLACE}: is not valid JSON ({exc})"]
    if not isinstance(data, dict):
        return [f"{MARKETPLACE}: top level is not an object"]
    found: list[str] = []
    if data.get("name") != MARKETPLACE_NAME:
        found.append(f"{MARKETPLACE}: name is not {MARKETPLACE_NAME!r}")
    owner = data.get("owner")
    if not (isinstance(owner, dict) and isinstance(owner.get("name"), str) and owner["name"]):
        found.append(f"{MARKETPLACE}: owner.name is missing")
    plugins = data.get("plugins")
    if not isinstance(plugins, list) or not plugins:
        return [*found, f"{MARKETPLACE}: plugins is not a non-empty list"]
    entries: dict[str, dict] = {}
    dependencies: dict[str, list] = {}
    for index, entry in enumerate(plugins):
        label = f"plugins[{index}]"
        if not isinstance(entry, dict):
            found.append(f"{label}: is not an object")
            continue
        name = entry.get("name")
        if not isinstance(name, str) or name not in PLUGIN_NAMES:
            found.append(f"{label}: plugin name {name!r} is not one of {', '.join(PLUGIN_NAMES)}")
            continue
        label = f"plugin {name}"
        if name in entries:
            found.append(f"{label}: is listed twice")
            continue
        entries[name] = entry
        source = entry.get("source")
        if isinstance(source, str):
            more, dependencies[name] = folder_problems(root, label, entry, source)
            found.extend(more)
        elif isinstance(source, dict):
            found.extend(external_problems(label, source))
        else:
            found.append(f"{label}: source is neither a ./ path nor an object")
    if "harness" not in entries:
        found.append("plugin harness: is not listed")
    elif not isinstance(entries["harness"].get("source"), str):
        found.append("plugin harness: source is not a folder in this repository")
    for name, wanted in dependencies.items():
        for dependency in wanted:
            if dependency == name:
                found.append(f"plugin {name}: depends on itself")
            elif dependency not in entries:
                found.append(f"plugin {name}: dependency {dependency!r} is not an entry of this marketplace")
    return found


# The CI job, read as text.

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
    if "|| true" in job or "continue-on-error" in job:
        found.append("plugin-validate: a failure is allowed")
    return found


# The real files.

def test_the_marketplace_and_the_harness_plugin_manifests_hold_the_invariant():
    assert problems(ROOT) == []


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

GOOD_MARKETPLACE = {
    "name": "harness-core",
    "owner": {"name": "someone"},
    "plugins": [
        {"name": "harness", "source": "./plugins/harness"},
        {"name": "sdlc", "source": {
            "source": "url", "url": "https://example.test/skills.git",
            "ref": "v1.3.0", "sha": "b8e013f470f2470630f33664bd1ca9b351b6de7c"}},
        {"name": "ledger-hooks", "source": {
            "source": "git-subdir", "url": "https://example.test/ledger.git",
            "path": "plugins/ledger-hooks",
            "ref": "v0.3.1", "sha": "346cb3595180fcea702ebdd37b32ff8c36325ea5"}},
    ],
}
GOOD_PLUGIN = {"name": "harness", "version": "0.1.0", "dependencies": ["sdlc", "ledger-hooks"]}


def write_tree(root: Path, market, plugin) -> None:
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (root / MARKETPLACE).write_text(json.dumps(market), encoding="utf-8")
    (root / "plugins" / "harness" / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (root / "plugins" / "harness" / PLUGIN_MANIFEST).write_text(json.dumps(plugin), encoding="utf-8")


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
     "plugin sdlc: source type 'npm' is not allowed (github, url or git-subdir only)"),
    ("source-unknown-key", lambda m, p, r: set_key(external(m, "sdlc"), "branch", "main"),
     "plugin sdlc: unknown source key 'branch'"),
    ("source-field-missing", lambda m, p, r: drop_key(external(m, "ledger-hooks"), "path"),
     "plugin ledger-hooks: source field 'path' is missing"),
    ("source-url-not-https", lambda m, p, r: set_key(external(m, "sdlc"), "url", "http://example.test/skills.git"),
     "plugin sdlc: source url is not https"),
    ("source-repo-not-owner-name", lambda m, p, r: set_key(entry_of(m, "sdlc"), "source", {
        "source": "github", "repo": "one-part", "ref": "v1.3.0", "sha": "b8e013f470f2470630f33664bd1ca9b351b6de7c"}),
     "plugin sdlc: source repo is not owner/name"),
    ("source-neither-path-nor-object", lambda m, p, r: set_key(entry_of(m, "sdlc"), "source", 3),
     "plugin sdlc: source is neither a ./ path nor an object"),
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
        write_tree(tmp_path, market, plugin)
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


def test_a_symlinked_folder_that_leaves_the_repository_is_refused_by_name(tmp_path):
    outside = tmp_path / "outside"
    (outside / ".claude-plugin").mkdir(parents=True)
    (outside / PLUGIN_MANIFEST).write_text(json.dumps(GOOD_PLUGIN), encoding="utf-8")
    root = tmp_path / "root"
    write_tree(root, GOOD_MARKETPLACE, GOOD_PLUGIN)
    shutil.rmtree(root / "plugins" / "harness")
    (root / "plugins" / "harness").symlink_to(outside)
    assert "plugin harness: source resolves outside the repository" in problems(root)


GOOD_CI = """\
jobs:
  other:
    runs-on: x
  plugin-validate:
    name: plugin validate
    runs-on: ${{ fromJSON(vars.CORE_CI_RUNNER || '["ubuntu-latest"]') }}
    steps:
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
]


def test_the_good_ci_text_is_clean():
    assert ci_problems(GOOD_CI) == []


@pytest.mark.parametrize("change,expected", [(case[1], case[2]) for case in CI_NEAR_MISSES],
                         ids=[case[0] for case in CI_NEAR_MISSES])
def test_a_ci_near_miss_is_refused_by_name(change, expected):
    changed = change(GOOD_CI)
    assert changed != GOOD_CI, "the change did something"
    assert expected in ci_problems(changed)
