# Portable-core context standard

The keystone standard: how every project carries context for AI agents and humans, in a tool-neutral way.

Rationale: `AGENTS.md` is an open convention, read natively by many agent tools (coding assistants, editors, command-line agents). Keeping the source of truth tool-neutral means a cheaper or different agent drops into any repository with no rework. Tool-specific behavior lives only in a thin shim file.

## Required files per project

| File | Committed | Role | Budget |
|------|-----------|------|--------|
| `AGENTS.md` | yes | Tool-neutral, self-contained source of truth | 150–300 lines |
| Tool shim (the file a given agent tool reads first) | yes | Thin: imports `AGENTS.md`, adds that tool's own behaviors only | < 40 lines |
| Local context file | **gitignored** | Per-machine and volatile: deploy targets, URLs, the end-of-session ship workflow, session log | trim to the last ~10 entries |
| Local context file example | yes | Shape of the local file so a fresh clone knows what to fill in | — |
| `README.md` | yes | Human-facing overview | — |
| `.editorconfig` | yes | Copy from the family template | — |
| `.nvmrc` or `.python-version` | yes | Runtime pin (the supported LTS for the language) | — |
| Tool settings file | yes | Permission allowlist and hooks — **never secrets** | — |
| Local tool settings file | **gitignored** | Machine overrides — **never secrets** (see `security.md` § Secrets rules) | — |
| a docs folder, a plans folder | yes | Depth material and AI plan history, read on demand | — |
| MCP config file | yes, if used | Env-var references only, never secret values | — |

The instance names the shim and local files its agent tools read and the template it copies from. It records those names in its own override of this standard.

## AGENTS.md required sections

In order:

1. **Header note** — "canonical, tool-agnostic, self-contained; the shim imports this; don't duplicate."
2. **What the project is** — purpose, deployment posture, hard non-goals.
3. **Tech stack** — table.
4. **Commands** — copy-pasteable block with one-line comments; flag anything that hits live services.
5. **Repo map** — annotated tree of `src/` (or the package), path aliases.
6. **Architecture in brief** — data flow, the components an agent must not bypass.
7. **Database / storage** (if any) — schema source of truth, migration flow.
8. **Conventions** — language and style rules that differ from defaults.
9. **Critical gotchas** — the highest value-per-token section. Every incident that cost a session goes here, stated as a rule.
10. **Environment** — env vars, where production env lives (never the values).

Style: tables and command lists over prose; no changelogs (that is git and the local file); link to a docs folder instead of inlining depth; nothing tool-specific.

In public repositories, keep internal process terms out of human-facing docs and source comments. Run the instance's public-posture check to find text that needs a plain-language rewrite.

## Shim shape

```markdown
# <shim file> — <Project>

@AGENTS.md

Everything about <project> lives in the imported AGENTS.md — read it first.
Personal/session config is in the gitignored local context file — read it first if it exists.

## Notes for this tool
- <review workflow, skill pointers, verification habits — tool-only>
- End-of-session ship steps live in the gitignored local context file.
```

**The end-of-session ship ritual lives in the local context file, not the committed shim.** A repo's ship sequence is deploy-specific (which deploy script, boot-versus-migrate model, "deploy only when asked", session-log hygiene) and personal — none of it belongs in a public, machine-neutral file. Two layers already hold the shared halves:

- The **generic** `branch → PR → CI → merge` sequence lives once in the user-level context file's end-of-session checklist (a universal workflow; see the layering table). Do not restate it per repo.
- The **deploy mechanics** (remote login, image pull and restart, health check, rollback) live in each repo's deploy script and the skill that drives it, not in prose.

So the local context file carries only the repo's *deltas* (deploy script name, migration model, any live-automation safety note); the example file shows that shape for a fresh clone. This keeps a committed shim from ever pointing at a private user-level file, which a public clone would not have.

**Symlink shim.** Agent tools increasingly read `AGENTS.md` natively. If a project's shim would carry no tool-specific content beyond the import, it **may** collapse to a symlink to `AGENTS.md` instead of the file form above. The conformance checker accepts either shape.

## Context layering rules (token efficiency)

**Each fact lives at exactly one layer.** Duplication wastes tokens in every session and drifts.

| Layer | Contains | Must NOT contain |
|-------|----------|------------------|
| User-level context file (short, about 80 lines at most) | Cross-project preferences, shared infrastructure (hosts, ports), universal workflows (the generic end-of-session `branch → PR → CI → merge` sequence) | Anything about one project |
| Project `AGENTS.md` | Durable repo facts | Volatile state, personal data, tool-specific behavior |
| Project shim | Tool-only behavior | Repo facts (import, don't restate); deploy-specific or personal ship steps (those go in the local file) |
| Local context file | Deploy specifics, secrets *locations* (not values), the end-of-session ship workflow (repo deltas only), session log | Anything another machine or contributor needs; the generic ship sequence (that is user-level) |

Session-log hygiene: when an entry's lessons become durable, promote the lesson to `AGENTS.md` § Critical gotchas and delete the entry. Target: the local file readable in one screenful plus the recent log.

## Harness layer budgets

The table above governs the *repo* context layer. The *harness* layer (skills and memory) has its own limits, enforced by the agent runtime rather than by taste. **Exceeding them can fail silently: content is dropped with no error.** The instance measures the runtime it uses, records the numbers and the date it measured them in its own override, and re-measures when the runtime changes.

The surfaces that carry a limit:

| Surface | What the runtime limits | Failure mode when exceeded |
|---------|-------------------------|----------------------------|
| Skill `description` | A per-skill character cap, plus one shared budget for the whole listing | Over the cap the description truncates mid-sentence. When the shared budget saturates, whole descriptions drop to a bare name, never-used skills first |
| Memory file, including the memory index | A line cap and a byte cap | Loaded partially; everything past the cap is unreachable |
| Skill body | Nothing truncates: the whole file loads in one call | A very large body costs a large share of the context window in one call, so no seat of ordinary size can load it. The instance sets a split line (warn) and a hard line (gap) |

Rules that follow:

- **A `description` is routing text, not a spec.** It answers *when should this fire, and what is it not for*, and nothing more. The body is the spec; stating it twice pays for it in every session. Keep trigger phrases and negative routing ("NOT for X", "supersedes Y") early in the description so they survive truncation.
- **A skill body over the split line should split** into the main file plus a `references/` subdirectory, so its procedure loads progressively instead of all at once. A monolithic body is the same tokens with no option to defer. Over the hard line is a gap, not a warning: the shape that works is a table-of-contents file (what each step does, which chapter holds it, which script enforces it, when to skip it) plus one chapter per step, read only when the seat reaches that step, with chapter names that sort into reading order.
- **A lesson becomes a rail, a lint or a memory note, never skill prose.** A retro finding lands, in order of preference, as a script rail with a test, a conformance or CI check, or a memory note. Appending the finding as a paragraph to a skill body is how a rulebook grows past the point anyone can load it.
- **Memory is one fact per file.** A long document belongs under a docs folder inside the memory directory, with a short pointer memory keeping the **original basename**, so inbound links and index links keep resolving with no repair. Never relocate outside the memory directory: a sync tool that watches only the memory path reads any other target as a delete on the peer machine.
- **Memory is the personalization layer.** Where a thing goes is decided at write time. A gotcha anyone could use goes to the repo (`AGENTS.md`, docs, a rail or a lint) or to a standard, where a coworker's agent reads it. What is personal (a preference, a correction, how the person wants to be worked with) goes to memory, and only when no durable place already holds it; anything else is refused at write time and written to its home. Ideas and bespoke threads are ledger items, never memory notes. Long prose stays a markdown document that the item links to, in the repo it belongs to. Memory may point to a standard or a rule and never restates it; where they disagree, the standard wins and the session fixes the note in the same turn. The failure this prevents: memory is in context at birth and standards are read on demand, so a restated rule wins every disagreement by default, and a lane gets routed from a stale note after the standard has already denied it.
- **Keep a rule only when you can name the failure mode it prevents.** Apply the deletion test: prescriptive style rules an agent would infer from the surrounding code fail it and should go. Safety rules such as "force-push always confirms" and "confirm before destructive ops" **pass**: they name real consequences, and a mechanical backstop (a guard hook) covers only the destructive forms it was written for.

The global end-of-session checklist stays at the user level unless a skill owns every step of it. Before deleting it as duplication, check that no skill covers the steps it carries (watch CI to green, squash-merge, production migrations, memory update); removing it orphans every shim that defers to it. Revisiting it is a standards change with a migration, not a cleanup.

## Conformance

The instance's standards checker reports gaps against this standard. Run it before declaring a retrofit done, and in any new-project scaffold.
