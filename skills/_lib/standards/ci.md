# CI standard

Baselines per project family, lifted from the strongest existing implementations. Starter templates carry these; retrofits converge on them.

## Family 1 — Next.js apps

Two workflows:

1. **`ci.yml`** — on PR + push to main: `npm ci` → `npm run lint` (`--max-warnings 0`) → `npm run typecheck` → `npm test` (vitest) → `npm run build`. Node from `.nvmrc` (`actions/setup-node` with `node-version-file`).
2. **`deploy.yml`** (or docs-deploy where applicable) — manual `workflow_dispatch` or main-merge triggered, per project.

Rules:
- Branch protection on main for public repos; merges gated on the CI verdict. A red merge slips through when gating is informal.
- Squash-merge, delete branch.
- Dependabot or Renovate enabled; updates swept in a routine pass.

## Family 2 — Python automation

One workflow, `ci.yml`: `pip install -e .[dev]` → `ruff check` → `ruff format --check` → `pytest`. Python pinned via `.python-version` / `actions/setup-python`.

A Python repo with no CI workflow is a gap. The retrofit adds this one as soon as the repo has a remote.

## Family 3 — Infra

CI optional. Where used: `terraform fmt -check` + `terraform validate`; compose-file lint (`docker compose config -q`) is enough for compose repos. Renovate is the main automation.

## Rule of thumb

CI must be able to run from a fresh clone with no secrets. Anything needing secrets (deploys) is a separate manually-triggered workflow or a local script.
