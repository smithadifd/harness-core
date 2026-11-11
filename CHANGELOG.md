# Changelog

Each tag of this repository gets an entry: what changed in the vendored files,
and any pin move of the vendored ledger.

## Unreleased

- Alpha preparation, no vendored file changed: an MIT `LICENSE` at the root,
  the same license as the vendored ledger; the no-person check exempts
  exactly the MIT copyright line of that file from its deny list and nothing
  else; every CI job runs on the GitHub-hosted `ubuntu-latest` runner unless
  the repository variable `CORE_CI_RUNNER` names another; the README opens
  with an alpha status block.
- First tree: 158 vendored files (27 core files,
  33 tests and fixtures, 98 ledger files) carved
  without history from commit `5d2c033e6ddd86d4ea61f7e97dd69a8b4618923c`, with `.core-manifest`, the vendored
  ledger's pin and drift check, and the commit identity check. See
  `VENDORING.md` for the carve record.
