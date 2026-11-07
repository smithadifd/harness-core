# Licensing and funding standard

## License policy

- **MIT by default** for anything public. Add the license file at repository creation (it is in the templates), not at "release time".
- **Deliberately unlicensed** repositories must say why in `AGENTS.md`: for example a public repository kept public for CI convenience, with a personal-use posture or scraping-adjacent access. An explicit "no license means all rights reserved" note beats silence.
- Never publish anything with personal data, a credentials history, or terms-of-service-fragile access patterns, unless the posture is documented. A documented ethics section in the repository is the model.

## Funding and donations

- Every public repository intended for others gets a funding file in the hosting platform's format, listing the maintainer's sponsorship handles. The handles are instance configuration; the instance records them.
- Set up the sponsorship profile and the tip account once, then reuse them for every repository.
- Honest framing: donations on niche self-hosted tools are beer money. The point is rails, credibility and discoverability, not revenue.

## Selling things (when that day comes)

- **Use a merchant of record** for anything actually sold (licenses, subscriptions, boilerplate). It handles global tax, which is worth the fee at solo scale. Do not hand-roll a payment processor plus tax handling. The instance names its chosen provider.
- **Open-core seams**: templates should keep pro-tier-shaped boundaries clean: plugin and config injection points, single-tenant assumptions isolated, the auth and user model swappable. Design the seam; do not build the paid side speculatively.
- Pro-tier selection heuristic: features that are must-haves for teams and production but nice-to-haves for hobbyists (single sign-on, multi-user, hosted operations, service levels, priority support).
- **Managed hosting** is the natural paid tier for self-hosted apps: the open-source version is free forever, and users pay not to run it themselves. Commit only after demonstrated demand.

## Per-repo current state

The instance's portfolio inventory (its license and funding columns) is the source of truth for each repository; this document is policy only.
