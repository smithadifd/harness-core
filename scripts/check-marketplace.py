#!/usr/bin/env python3
"""Fail when the marketplace or the harness plugin's manifest breaks the invariant of a tag.

The invariant every tag of this repository keeps: the marketplace lists each
plugin once; the in-repo `harness` plugin is a folder here at the same tag; each
external plugin is the one expected source for its name, fixed to a full 40-hex
commit on a release tag; nothing resolves to a branch or a range.

Usage: check-marketplace.py [--source [--expect-owner OWNER]]

Run it as ``python3 -I`` (CI does): the isolated interpreter leaves this
script's folder and the working folder off the import path and ignores every
PYTHON* variable and the user site, so a module a pull request adds beside the
script (``scripts/json.py``) cannot shadow a standard-library import. The
script imports nothing from this repository.

The check always reads the repository that holds this script (the folder above
``scripts/``); there is no option to point it at another tree.

Offline (no option) reads the two manifests as JSON and fetches nothing. It
runs in the pytest job through scripts/tests/test_marketplace_manifest.py and
on the command line. Its grammar (anything else is refused, each by the name
shown in ``problems``):

- ``.claude-plugin/marketplace.json``: one JSON object with no duplicate key,
  with ``name`` equal to ``harness-core``, ``owner.name``, and a non-empty
  ``plugins`` list.
- Each plugin entry: an object whose ``name`` is one of ``harness``, ``sdlc``,
  ``ledger-hooks``, ``rails``, listed once, and whose ``source`` is either
  - a ``./<folder>`` path inside this repository (no ``..``, no backslash, no
    symlink that leaves the repository), whose folder exists and holds
    ``.claude-plugin/plugin.json``; or
  - an object of exactly one of two shapes, each with a ``ref`` that is a
    release tag ``vX.Y.Z`` and a ``sha`` that is a full lowercase 40-hex commit
    id: ``{"source": "url", "url": "https://..."}`` or
    ``{"source": "git-subdir", "url": "https://...", "path": "<folder>"}``.
    Every other source type (github, npm, archive, command) and every other key
    is refused.
- An external plugin is bound to its one expected source by name
  (``EXTERNAL_PLUGINS`` below, the only place that table is written): ``sdlc``
  is source type ``url`` at the skills repository with no subdirectory, and
  ``ledger-hooks`` is source type ``git-subdir`` at the ledger repository with
  path ``plugins/ledger-hooks``. The repository URL is
  ``https://github.com/<owner>/<repository>.git``, where the owner is the owner
  in the ``repo`` line of ledger-vendor.pin (the skills and ledger repositories
  share one owner), so this file never names the owner. Any other type, URL or
  path, an external entry for any other name, and a folder source for one of
  these two names, is refused.
- ``harness`` is listed and is a folder in this repository, and its manifest's
  ``dependencies`` name each external plugin (each name of
  ``EXTERNAL_PLUGINS``) exactly once: a missing one and a repeated one are
  each refused by name. That is how installing ``harness`` brings the set.
- A folder plugin's manifest: one JSON object with no duplicate key, whose
  ``name`` equals its entry's ``name``, with a ``version`` that is ``X.Y.Z`` set
  in the manifest only (not also in the entry), and ``dependencies`` (when
  present) a list of plain names, each another entry of this marketplace (no
  version range, no ``name@marketplace``, no object form, not itself).

``--source`` also asks the GitHub REST API, at use time, which commit the
release tag of each external entry names now, and refuses unless it is the
entry's ``sha``. Only an entry that passed every offline check is resolved; any
other is reported as not resolved. Its rules:

- Host. The literal ``https://api.github.com``. No environment variable
  (``GITHUB_API_URL`` included) and no option moves it. The default fetch
  refuses any url whose scheme is not ``https`` or whose network location is
  not exactly ``api.github.com`` (no user, no port).
- Path. The repository is the table's (``EXTERNAL_PLUGINS``), never the url in
  the manifest; the owner is the pin's. Each segment is checked with
  ``re.fullmatch`` (ASCII classes) before any url is built: owner
  ``[A-Za-z0-9][A-Za-z0-9-]{0,38}``; repository ``[A-Za-z0-9._-]{1,100}`` and
  never ``.`` or ``..``; tag ``v[0-9]+.[0-9]+.[0-9]+``; object sha
  ``[0-9a-f]{40}``. A url is the fixed host, fixed path words and checked
  segments only.
- Requests. ``GET /repos/<owner>/<repository>/git/ref/tags/<tag>``: the
  singular endpoint, which answers 404 on a miss (the plural ``git/refs/``
  prefix-matches and is never used). The answer's ``ref`` must be exactly
  ``refs/tags/<tag>``. An object of type ``commit`` is the tag's commit. An
  object of type ``tag`` is followed with
  ``GET /repos/<owner>/<repository>/git/tags/<sha>``, at most 4 tag objects; a
  5th is refused. Any other type (``blob``, ``tree``, anything unknown) is
  refused by name. Every sha in an answer is checked before it is used in a
  url or compared. The commit must equal the entry's ``sha``.
- Each request carries ``Accept: application/vnd.github+json``,
  ``X-GitHub-Api-Version: 2022-11-28``, a fixed ``User-Agent`` and
  ``Cache-Control: no-cache`` (so a shared cache does not answer with a moved
  tag's old commit), each unredirected. No credential: no token, no
  Authorization header, no credential helper.
- Fail closed. No redirect is followed (a 3xx is refused by its status). A
  status other than 200, a socket operation slower than 20 s, a body over
  256 KiB, a body that is not UTF-8 or not JSON, a JSON value of the wrong
  shape, and any exception are each a refusal naming what failed; nothing
  retries, one request per url. A 403 or 429 whose ``x-ratelimit-remaining``
  is ``0`` is the refusal ``rate limited; rerun``. A slow drip of bytes, each
  within 20 s, is bounded only by the CI job's ``timeout-minutes``.
- In CI. The script is in CI when the variable ``GITHUB_ACTIONS`` is present,
  with any value. In CI, ``--source`` refuses before any request when a
  variable whose name casefolds to ``https_proxy``, ``all_proxy``,
  ``ssl_cert_file`` or ``ssl_cert_dir`` holds a value (``proxy or CA override
  set in CI``, naming the variable), and when ``--expect-owner`` is missing or
  empty (``no owner anchor in CI``). Outside CI, proxy and CA settings are
  honoured the way urllib and ssl honour them by default.
- Owner anchor. ``--expect-owner`` (CI passes ``github.repository_owner``) must
  be an owner name equal to the pin's owner, compared case-insensitively;
  otherwise no request is made. A local run without it prints one line saying
  the run has no owner anchor.
- The step attests only for an unmodified workflow and script: a pull request
  that edits either can change what runs (KNOWN-LIMITS.md).

Exit 0 clean; 1 on any refusal, one line per refusal; 2 on usage.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
PREFIX = "marketplace:"
MARKETPLACE = ".claude-plugin/marketplace.json"
PLUGIN_MANIFEST = ".claude-plugin/plugin.json"
PIN = "ledger-vendor.pin"
MARKETPLACE_NAME = "harness-core"
PLUGIN_NAMES = ("harness", "sdlc", "ledger-hooks", "rails")
FULL_COMMIT = re.compile(r"[0-9a-f]{40}", re.ASCII)
RELEASE_TAG = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+", re.ASCII)
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+", re.ASCII)
LOCAL_SOURCE = re.compile(r"\./[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*", re.ASCII)
OWNER_REPO = re.compile(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", re.ASCII)
# The grammars of the segments of an API url (section "Path" above).
OWNER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", re.ASCII)
REPOSITORY = re.compile(r"[A-Za-z0-9._-]{1,100}", re.ASCII)
OBJECT_SHA = re.compile(r"[0-9a-f]{40}", re.ASCII)
# The fields each external source type needs besides source, ref and sha.
EXTERNAL_SOURCES = {
    "url": ("url",),
    "git-subdir": ("url", "path"),
}
# The one expected source of each external plugin name: its source type, the
# repository's name (the owner comes from ledger-vendor.pin) and the folder in
# that repository (None for a plugin at the repository root).
EXTERNAL_PLUGINS = {
    "sdlc": {"source": "url", "repository": "skills", "path": None},
    "ledger-hooks": {"source": "git-subdir", "repository": "ledger", "path": "plugins/ledger-hooks"},
}

# --source: the one host, the request's headers and the bounds of an answer.
API = "https://api.github.com"
API_HOST = "api.github.com"
REQUEST_HEADERS = (
    ("Accept", "application/vnd.github+json"),
    ("X-GitHub-Api-Version", "2022-11-28"),
    ("User-Agent", "harness-core-check-marketplace"),
    ("Cache-Control", "no-cache"),
)
TIMEOUT_SECONDS = 20
BODY_CAP = 256 * 1024
MAX_TAG_OBJECTS = 4
# In CI, a variable whose name casefolds to one of these refuses --source.
CI_TRIPWIRE = ("https_proxy", "all_proxy", "ssl_cert_file", "ssl_cert_dir")
RATE_LIMITED = "rate limited; rerun"
NO_ANCHOR = (f"{PREFIX} no owner anchor: --expect-owner was not given, so the pin's owner "
             "is not checked against the repository owner (a local run, not an attestation)")

# url -> (status, headers, body). The one boundary to the network.
Fetch = Callable[[str], "tuple[int, Mapping[str, str], bytes]"]


def expected_url(name: str, owner: str) -> str:
    """The one repository url of an external plugin name: the table's, never the manifest's."""
    return f"https://github.com/{owner}/{EXTERNAL_PLUGINS[name]['repository']}.git"


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


def pin_owner(root: Path) -> tuple[str | None, str | None]:
    """(owner, None) from the pin's repo line, or (None, the problem)."""
    try:
        text = (root / PIN).read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        return None, f"{PIN}: cannot be read to give the owner ({exc.__class__.__name__})"
    repos = [line[len("repo "):] for line in text.split("\n") if line.startswith("repo ")]
    if len(repos) != 1 or not OWNER_REPO.fullmatch(repos[0]):
        return None, f"{PIN}: has no single repo line of owner/name to give the owner"
    return repos[0].split("/")[0], None


def external_problems(label: str, name: str, source: dict, owner: str | None) -> list[str]:
    kind = source.get("source")
    if kind not in EXTERNAL_SOURCES:
        return [f"{label}: source type {kind!r} is not allowed (url or git-subdir only)"]
    found: list[str] = []
    expected = EXTERNAL_PLUGINS.get(name)
    if expected is None:
        found.append(f"{label}: is not an expected external plugin ({', '.join(EXTERNAL_PLUGINS)})")
    elif kind != expected["source"]:
        found.append(f"{label}: source type {kind!r} is not the expected {expected['source']!r}")
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
    if expected is not None and owner is not None and isinstance(url, str) and url.startswith("https://"):
        wanted = expected_url(name, owner)
        if url != wanted:
            found.append(f"{label}: source url is not the expected {expected['repository']} repository")
    path = source.get("path")
    if expected is not None and expected["path"] is not None and isinstance(path, str):
        if path != expected["path"]:
            found.append(f"{label}: source path is not {expected['path']!r}")
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


def folder_problems(root: Path, label: str, entry: dict, source: str) -> tuple[list[str], list | None]:
    """(problems, dependencies) of a plugin that is a folder in this repository.
    The dependencies are None when the manifest or its list could not be read
    (that is already refused, so nothing more is said about them)."""
    if not LOCAL_SOURCE.fullmatch(source) or ".." in source.split("/"):
        return [f"{label}: source is not a ./ folder path inside the repository"], None
    folder = root / source[2:]
    if not folder.resolve().is_relative_to(root.resolve()):
        return [f"{label}: source resolves outside the repository"], None
    if not folder.is_dir():
        return [f"{label}: folder {source} is missing"], None
    manifest = folder / PLUGIN_MANIFEST
    if not manifest.is_file():
        return [f"{label}: manifest {source}/{PLUGIN_MANIFEST} is missing"], None
    try:
        data = load_json(manifest)
    except ValueError as exc:
        return [f"{label}: manifest is not valid JSON ({exc})"], None
    if not isinstance(data, dict):
        return [f"{label}: manifest is not a JSON object"], None
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
        return found, None
    return found, dependencies


def problems(root: Path, external: dict | None = None) -> list[str]:
    """The offline refusals. When ``external`` is given it is filled with each
    plugin name whose source is an object: its source when that entry passed
    every offline check, else None (the entry --source must not resolve)."""
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
    owner_entry = data.get("owner")
    if not (isinstance(owner_entry, dict) and isinstance(owner_entry.get("name"), str) and owner_entry["name"]):
        found.append(f"{MARKETPLACE}: owner.name is missing")
    plugins = data.get("plugins")
    if not isinstance(plugins, list) or not plugins:
        return [*found, f"{MARKETPLACE}: plugins is not a non-empty list"]
    owner, owner_problem = pin_owner(root)
    if owner_problem is not None:
        found.append(owner_problem)
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
            if external is not None and name in external:
                external[name] = None
            continue
        entries[name] = entry
        source = entry.get("source")
        if isinstance(source, str):
            if name in EXTERNAL_PLUGINS:
                found.append(f"{label}: source is not the expected external source")
                if external is not None:
                    external[name] = None
                continue
            more, listed = folder_problems(root, label, entry, source)
            found.extend(more)
            if listed is not None:
                dependencies[name] = listed
        elif isinstance(source, dict):
            more = external_problems(label, name, source, owner)
            found.extend(more)
            if external is not None:
                clean = not more and owner is not None and name in EXTERNAL_PLUGINS
                external[name] = source if clean else None
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
    # harness brings the set: its dependencies name each external plugin once.
    # The expected names are EXTERNAL_PLUGINS's, never a second list.
    if "harness" in dependencies:
        for external_name in EXTERNAL_PLUGINS:
            times = dependencies["harness"].count(external_name)
            if times == 0:
                found.append(f"plugin harness: does not depend on external plugin {external_name!r}")
            elif times > 1:
                found.append(f"plugin harness: depends on external plugin {external_name!r} more than once")
    return found


# --source: each external entry's tag, read from the GitHub REST API.

class FetchFailed(Exception):
    """A request that produced no answer to read; its message is the refusal."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Follows no redirect: urllib then raises HTTPError with the 3xx status."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def make_opener() -> urllib.request.OpenerDirector:
    """urllib's default opener (its proxy and CA handling included) with no redirect."""
    return urllib.request.build_opener(NoRedirect())


def read_capped(stream) -> bytes:
    """At most BODY_CAP bytes of the stream; one byte more is a refusal."""
    chunks: list[bytes] = []
    total = 0
    while total <= BODY_CAP:
        chunk = stream.read(BODY_CAP + 1 - total)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    if total > BODY_CAP:
        raise FetchFailed("the answer body is over 256 KiB")
    return b"".join(chunks)


def error_answer(error: urllib.error.HTTPError) -> tuple[int, Mapping[str, str], bytes]:
    """(status, headers, body) of an HTTP error answer, its body read through the cap."""
    try:
        return error.code, error.headers, read_capped(error)
    except FetchFailed:
        raise
    except Exception as exc:
        raise FetchFailed(f"request failed ({exc.__class__.__name__}) after status {error.code}") from None
    finally:
        try:
            error.close()
        except Exception:
            pass


def api_fetch(url: str) -> tuple[int, Mapping[str, str], bytes]:
    """GET the url from api.github.com: (status, headers, body), or FetchFailed.

    The only code that reaches the network. An HTTP error status (a refused
    redirect, a 403, a 429, a 500) comes back as its status, headers and capped
    body; every other exception becomes FetchFailed naming its class."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        raise FetchFailed(f"request refused: the url is not on {API}") from None
    if parts.scheme != "https" or parts.netloc != API_HOST:
        raise FetchFailed(f"request refused: the url is not on {API}")
    request = urllib.request.Request(url, method="GET")
    for name, value in REQUEST_HEADERS:
        request.add_unredirected_header(name, value)
    try:
        with make_opener().open(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status, response.headers, read_capped(response)
    except urllib.error.HTTPError as error:
        return error_answer(error)
    except FetchFailed:
        raise
    except Exception as exc:
        raise FetchFailed(f"request failed ({exc.__class__.__name__})") from None


def header_value(headers, name: str) -> str | None:
    """The value of a header, its name compared case-insensitively."""
    for key, value in headers.items():
        if isinstance(key, str) and key.casefold() == name:
            return value
    return None


def read_answer(fetch: Fetch, url: str) -> tuple[dict | None, str | None]:
    """(the JSON object of a 200 answer, None), or (None, the refusal)."""
    try:
        status, headers, body = fetch(url)
    except FetchFailed as exc:
        return None, str(exc)
    if len(body) > BODY_CAP:
        return None, "the answer body is over 256 KiB"
    if status in (403, 429) and str(header_value(headers, "x-ratelimit-remaining")).strip() == "0":
        return None, RATE_LIMITED
    if 300 <= status < 400:
        return None, f"redirect {status} (renamed or transferred repository?)"
    if status != 200:
        return None, f"status {status}"
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return None, "the answer is not UTF-8"
    try:
        value = json.loads(text)
    except (ValueError, RecursionError):
        return None, "the answer is not JSON"
    if not isinstance(value, dict):
        return None, "the answer is not a JSON object"
    return value, None


def object_of(answer: dict) -> tuple[str | None, str | None, str | None]:
    """(type, sha, None) of the answer's object, its sha checked; or (None, None, the refusal)."""
    target = answer.get("object")
    if not isinstance(target, dict):
        return None, None, "the answer has no object"
    kind, sha = target.get("type"), target.get("sha")
    if not isinstance(kind, str) or not isinstance(sha, str):
        return None, None, "the answer's object has no string type and sha"
    if not OBJECT_SHA.fullmatch(sha):
        return None, None, "the answer's object sha is not 40 lowercase hex"
    return kind, sha, None


def resolve_commit(owner: str, repository: str, tag: str, fetch: Fetch) -> tuple[str | None, str | None]:
    """(the commit the tag names, None), or (None, the refusal).

    Every segment is checked before a url is built; a url is the fixed host,
    fixed path words and checked segments."""
    if not isinstance(owner, str) or not OWNER.fullmatch(owner):
        return None, "not resolved, the owner is not a GitHub owner name"
    if not isinstance(repository, str) or repository in (".", "..") or not REPOSITORY.fullmatch(repository):
        return None, "not resolved, the repository is not a GitHub repository name"
    if not isinstance(tag, str) or not RELEASE_TAG.fullmatch(tag):
        return None, "not resolved, the tag is not a release tag vX.Y.Z"
    base = f"{API}/repos/{owner}/{repository}/git"
    answer, problem = read_answer(fetch, f"{base}/ref/tags/{tag}")
    if problem is not None:
        return None, problem
    if answer.get("ref") != f"refs/tags/{tag}":
        return None, f"the answer's ref is not refs/tags/{tag}"
    kind, sha, problem = object_of(answer)
    if problem is not None:
        return None, problem
    followed = 0
    while kind == "tag":
        if followed == MAX_TAG_OBJECTS:
            return None, f"more than {MAX_TAG_OBJECTS} tag objects before a commit"
        followed += 1
        answer, problem = read_answer(fetch, f"{base}/tags/{sha}")
        if problem is not None:
            return None, problem
        kind, sha, problem = object_of(answer)
        if problem is not None:
            return None, problem
    if kind != "commit":
        named = f"a {kind!r} object" if re.fullmatch(r"[a-z]{1,16}", kind, re.ASCII) else "an object of an unknown type"
        return None, f"the tag names {named}, not a commit"
    return sha, None


def in_ci() -> bool:
    """In CI: GITHUB_ACTIONS is present, whatever its value."""
    return "GITHUB_ACTIONS" in os.environ


def tripwire_variables() -> list[str]:
    """The set proxy and CA-override variables, their names compared casefolded."""
    return sorted(name for name, value in os.environ.items()
                  if name.casefold() in CI_TRIPWIRE and value)


def resolve_sources(root: Path, expect_owner: str | None, fetch: Fetch) -> list[str]:
    """One refusal for every external entry whose tag does not resolve to its sha,
    or the refusals that stop every request (the CI tripwire, the owner anchor)."""
    if in_ci():
        tripped = tripwire_variables()
        if tripped:
            return [f"proxy or CA override set in CI ({name}); refused before any request"
                    for name in tripped]
        if not expect_owner:
            return ["no owner anchor in CI (--expect-owner is missing or empty); refused before any request"]
    external: dict[str, dict | None] = {}
    problems(root, external)
    owner, _ = pin_owner(root)
    if expect_owner:
        if not OWNER.fullmatch(expect_owner):
            return ["owner anchor: --expect-owner is not a GitHub owner name; refused before any request"]
        if owner is not None and owner.casefold() != expect_owner.casefold():
            return [f"owner anchor: the owner in {PIN} is not --expect-owner; refused before any request"]
    found: list[str] = []
    for name, source in external.items():
        label = f"plugin {name}"
        if source is None or owner is None:
            found.append(f"{label}: not resolved, it failed the offline check")
            continue
        repository, ref, sha = EXTERNAL_PLUGINS[name]["repository"], source["ref"], source["sha"]
        commit, problem = resolve_commit(owner, repository, ref, fetch)
        if problem is not None:
            found.append(f"{label}: {problem}")
        elif commit != sha:
            found.append(f"{label}: tag {ref} resolves to {commit}, not the pin {sha}")
    return found


def source_problems(root: Path, expect_owner: str | None = None, fetch: Fetch = api_fetch) -> list[str]:
    """``resolve_sources``, with any exception it raises turned into a refusal."""
    try:
        return resolve_sources(root, expect_owner, fetch)
    except Exception as exc:
        return [f"the resolver raised {exc.__class__.__name__}; nothing is attested"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the marketplace and the harness plugin's manifest.")
    parser.add_argument("--source", action="store_true",
                        help="also resolve each external entry's tag through the GitHub REST API "
                             "and compare it with the pin")
    parser.add_argument("--expect-owner", metavar="OWNER",
                        help="with --source: the owner the pin's owner must equal (CI passes "
                             "github.repository_owner)")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    if args.expect_owner is not None and not args.source:
        print(f"{PREFIX} usage: --expect-owner is read only with --source", file=sys.stderr)
        return 2
    try:
        found = problems(REPO_ROOT)
        if args.source:
            if not in_ci() and not args.expect_owner:
                print(NO_ANCHOR)
            found.extend(source_problems(REPO_ROOT, args.expect_owner))
    except Exception as exc:
        found = [f"the check raised {exc.__class__.__name__}; nothing is attested"]
    for line in found:
        print(f"{PREFIX} refused: {line}")
    if found:
        print(f"{PREFIX} FAIL: {len(found)} refusal(s)")
        return 1
    checked = " and each external tag against its repository" if args.source else ""
    print(f"{PREFIX} ok: the marketplace and the harness plugin hold the invariant{checked}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
