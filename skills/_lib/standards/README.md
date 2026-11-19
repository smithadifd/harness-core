# Core standards

This is the one folder for shipped standards. A standard that the toolkit
distributes lives here and nowhere else; there is no second standards folder
in this repository.

Rules:

- Consumers reach a standard through `resolve_standard()` in
  `../harness_root.py`, never by writing a literal path to either folder.
- No core-tagged file names a standards directory inside someone's own
  repository. The layer checker fails the build if one does, or if a second
  standards folder appears.
- A person's own standards live in their instance (the `standards` path the
  resolver returns for their root), not here.

`layers.json` tags this folder `core`, together with the rest of the shareable
core. The layer checker holds every core-tagged file to one more rule: it may
reference only other core files and the pinned upstream skills.

Standards in this folder:

- [`portable-core.md`](portable-core.md) — how every project carries context
  for agents and humans in a tool-neutral way: the required files, the
  `AGENTS.md` sections, the layering rules and the harness-layer budgets.
- [`security.md`](security.md) — secret-handling rules, live-automation
  retrofit rules, and backup posture.
- [`trust-ladder.md`](trust-ladder.md) — the promotion state machine and the
  merge classes. It records no grants; an instance keeps its own.
- [`ci.md`](ci.md) — baseline CI workflows per project family.
- [`deploy.md`](deploy.md) — the compose-deploy contract: remote
  execution, safety, rollback, health and result reporting, with the
  parameter table and the new-script checklist.
- [`harness-definition.md`](harness-definition.md) — what the harness is: its
  purpose, posture, principles, the core and where the rules live.
- [`licensing.md`](licensing.md) — license and funding policy, and the selling
  and open-core guidance. Funding handles belong to the instance.
- [`release.md`](release.md) — release classes (versioned, continuous, none),
  the one-line declaration, and the tag, changelog and GitHub Release rules.
- [`prompting.md`](prompting.md) — how to write sub-agent briefs and scope
  reviewers.
