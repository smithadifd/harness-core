# Release standard

How a repository ships and how it says so. The goal is one small, predictable shape: a visitor to any repository, public or private, can tell in one line whether it has versions, where the history of changes lives, and why when the answer differs from the norm. A release is outward-facing, so a person cuts it; an agent may prepare it.

## Release class

Every repository declares exactly one class, in one line of its `AGENTS.md` near the top, next to its license and backup lines:

    Release class: versioned
    Release class: continuous
    Release class: none
    Release class: versioned — <one-line reason it departs from this standard>

| Class | What it is | Records |
|---|---|---|
| `versioned` | Something other people or other repositories install, pin, vendor or depend on: a library, a command-line tool, a plugin or skill set, a vendored core. | A semver tag, a GitHub Release per tag, a `CHANGELOG.md`. |
| `continuous` | Something that runs from its main branch: a deployed app, a scheduled job, a static site, a starter template, a toolkit installed from a checkout. Main is the release. | The deploy record (below). No tags, no Releases, no required changelog. |
| `none` | Documentation, configuration and inventory repositories that nothing installs and nothing deploys. | Git history only. |

A starter template is `continuous`: consumers copy main, and a version number on a copy-in template promises something it cannot keep. A repository becomes `versioned` the day something else pins it (a consumer's pin file, a vendored copy, an installed tag) or the day it goes public for others to install or self-host; change the line in that same pull request. A deployed app that outside people self-host may be `versioned` while it still deploys from main: the tags mark releases for them and the deploy is unchanged.

## Versioned

- **One version source.** The manifest the ecosystem already reads (`pyproject.toml`, `package.json`, `plugin.json`). A second copy, such as a `__version__`, is changed in the same commit and checked by a test. The tag is never the only record of the version.
- **Semver, tag `vX.Y.Z`.** Pre-1.0, a fix is a patch and anything that adds or changes a command, flag, hook, schema or file format is a minor. A repository holding several independently versioned artifacts tags `<name>--vX.Y.Z` and names the rule in its `AGENTS.md`. A pre-release suffix (`-rc`) is allowed on a tag and is marked pre-release on its Release.
- **Conventional commits drive the release.** Squash-merge titles follow conventional commits (`feat:`, `fix:`, `feat!:` or a `BREAKING CHANGE` footer), because release-please reads them to choose the version bump and to write the notes.
- **Automated with release-please.** The release-please action (`googleapis/release-please-action`) runs on pushes to main and keeps one open release pull request that holds the version bump in the version source and the generated `CHANGELOG.md` section (Keep a Changelog shape: newest first, one dated section per version). Reviewing that pull request is reviewing the release. **Merging it is the human go:** the action then creates the tag `vX.Y.Z` and the GitHub Release, whose notes are that changelog section. Nobody tags by hand and no release is cut as a side effect of another merge.
- **Cost.** Actions minutes are free on public repositories. On a private versioned repository the action runs only on pushes to main, so it costs one short run per merge and nothing on pull requests.
- **A repository may cut by hand** when it states why on its class line (for example a repository whose tags carry a build step release-please cannot run). Hand-cut shape: a `chore(release): vX.Y.Z` pull request that changes only the version source and the changelog, an annotated tag on its squash commit, and `gh release create vX.Y.Z --verify-tag --title vX.Y.Z --notes-file <the section>`. Notes are the changelog section, not text written at release time.
- **Invariant.** One Release per tag, titled with the tag; the newest stable version is marked Latest; a pre-release suffix (`-rc`) is marked pre-release. A tag with no Release, or a Release with no tag, is a defect.
- **Backfill.** A repository that adopts the standard with existing tags gets one Release, from its newest existing tag only, marked Latest, with notes from that tag's changelog section (a one-line body and a compare link if the section is missing, never invented notes). Older tags stay bare; the invariant applies to tags made after adoption.
- **A release is not a deploy.** Cutting a versioned release never deploys anything by itself.

## Continuous

- **Main is the release.** Merge, then deploy by the repository's own deploy script under the deploy standard.
- **Last known good** is the commit the deploy script captured on the box before it changed anything and restores on failure. That commit is the rollback target; no tag is needed to name it. A continuous repository does not tag deploys.
- **A changelog is optional.** Keep one only if users of a public repository would read it. Never keep an `Unreleased` section that is never closed.
- **Say it on the front page.** A public continuous repository carries one sentence in its README, such as "Released continuously from `main`; there are no versioned releases." An empty Releases tab with that sentence looks chosen; the same tab without it looks abandoned.

## None

No tags, releases or changelog. The `Release class` line says so and nothing more is needed.

## Deviations

A repository may depart from its class only with a reason on the same line as the class, in plain words and one line. Allowed shapes:

- A fork follows the upstream's tags and changelog and cuts no Releases of its own.
- A repository with several artifacts uses the `<name>--vX.Y.Z` tag form.
- A repository ships a version in a manifest for a tool's cache or install logic that is not a release number (`continuous — plugin.json version is a manifest field, not a release`).

A deviation with no reason, or a missing class line, is a gap, not a deviation.

## Who cuts a release

A person gives the go for each release by merging the release pull request (or, for a hand-cut repository, by saying go on the exact tag and Release commands). An agent may review that pull request and may prepare a hand-cut one. An agent never merges the release pull request, pushes a tag or creates a Release without that go.

## What can be checked

Mechanically, from the repository and the hosting API: the class line exists and holds one known value; a `versioned` repository has a changelog whose top dated section matches its newest tag, a Release for every tag made since adoption, and a version source equal to the newest tag; a `continuous` or `none` repository has no tags unless its line gives a deviation reason. Repository settings that keep history tidy (head branches deleted on merge, squash-only merging) are checked alongside, since they decide what a squash commit and a release pull request look like.

The instance records the census: each repository, its visibility, its class, its current state and the gap to the standard.
