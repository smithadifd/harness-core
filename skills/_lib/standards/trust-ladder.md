# Trust-ladder standard

ONE promotion state machine for every place a system earns autonomy. This file defines the machine (§ 1) and the merge classes an orchestrator may act on (§ 4); an instance may add instantiations for its own systems under the same machine (§§ 2–3). Who holds which rung today is a grant, and grants live in the instance, not here: this file never records one.

## 1. The machine

Four rungs. One-way promotions are earned; demotions are immediate.

| Rung | What executes | Who watches |
|---|---|---|
| **shadow** | Nothing. Outputs are recorded against ground truth or a yardstick; zero blast radius. (An older name for this rung, *observe*, is the same rung; use one name.) | The ledger only |
| **recommend** | Nothing. Outputs surface to a human queue (UI panel, draft PR, digest); a human executes or declines. | The human, per item |
| **gated-apply** | The system executes, but only when the class's **gate predicate** passes. The predicate is per-class: a human approval click, or a mechanical evidence bundle (merges: CI green + fresh-review clean + the second-family review the override names). Either way it can fail closed. | The gate, per item; humans async via ledger |
| **bounded-auto** | The system executes with no per-item gate, inside standing **bounds**: rate/budget caps, scope allowlist, mandatory post-verification, an **armed breaker**. | Digests + standing audit |

**Unit of trust.** Trust attaches to a **capability class** — (executor, action class, target domain) — never to a model identity, never to an agent instance. Each instantiation defines its class grain and its diversity dimension. Sub-agents and unattended runners never inherit a grant.

**Collapsed rungs.** An instantiation may lack a surface for some rung (an automated judge has no recommend queue). It declares the collapse in its table; demotion lands on the nearest *operable* lower rung.

### Promotion (earning)

- One rung at a time, never skipped.
- Evidence = **N clean outcomes across ≥M diverse contexts** within a rolling 90-day window. Defaults, per risk tier: **Low 10/3 · Medium 25/5 · High: no bounded-auto exists**. recommend → gated-apply is cheaper: **5 verified-clean outcomes at recommend** plus countersign.
- **Clean** = independently verified: the verifier is never the executor. Self-reported success counts for nothing. Each instantiation names its verifier.
- **Evidence matures before it counts.** An outcome enters the count only when its verification window closes: a merge at day 7 (its revert window), an act at its `verified` event, a judge window when the audit that scores it runs. Immature outcomes count toward nothing, and the rolling 90-day window keys on the **maturation** timestamp — "no revert *yet*" is not evidence.
- **Human countersign chokepoints:** the *first* gated-apply grant for any class, and *every* bounded-auto grant, require the grant owner's recorded sign-off with the evidence presented. The countersign may cite pre-ladder evidence **once, explicitly, at first grant** — after that only ledger evidence counts.
- **Evidence binds to the gate-bundle version — and so does standing.** The bundle includes the gate predicate, bounds, executor, and verifier. **Weakening** any of them (any relaxation) drops a class at gated-apply+ **one rung immediately** and restarts its count under the new bundle — you cannot earn trust under strong gates and then quietly remove them, and you cannot keep operating on the old bundle's standing either. **Tightening** keeps standing (stricter is always allowed). Weaken-vs-tighten calls go through Adjudication (below) when disputed.
- **No evidence farming.** Items whose primary value is ladder evidence may not be charted; diversity must come from organic work. A promotion built on a farm-batch is void.
- **Nomination bar — resolved-without-action.** An instantiation whose recommendations can close without action defines a **resolved-without-action rate** (recommendations that closed because the condition cleared on its own ÷ closed recommendations). A class is not **nominated** for bounded-auto while that rate exceeds the instance's threshold, assessed only once the class has a minimum count of closed recommendations over the same rolling window as the evidence count. Below the minimum the class is **not yet assessable**: nomination stays blocked for an honest reason (no evidence yet), never waved through by default. **Machine mute-closes are excluded from BOTH terms:** a recommendation closed because its job was silenced, not because the condition resolved, says nothing about the recommendation's quality; counting it would push a class over the assessability minimum on rows that carry no evidence about the class, *and* dilute the rate, so the two effects compound. Where a job is muted **and** its cited evidence genuinely cleared, the real self-resolution still counts. The bar gates **nomination only** — it neither reopens nor demotes standing a class already holds — and it composes with (never substitutes for) the evidence floor above: both must clear. Rationale: a class whose recommendations mostly self-clear is exactly the class whose promotion to bounded-auto would automate noise. An instantiation comes under this bar once, and only once, it defines its own resolved-without-action analog; until then it is out of the bar's scope, neither blocked from nomination nor counted as vacuously clearing it.

### Demotion

Two distinct paths — do not blur them:

- **Fault (discrete outcomes — acts, merges):** one confirmed harmful outcome (reverted act, reverted/hotfixed merge, verification miss with damage) → **down one rung immediately**. Severe — security, data loss, secrets, money, external send — → **straight to the class's lowest operable rung + breaker trip** (shadow where shadow is operable, e.g. automated judges; recommend for acts and merges, whose observation/PR paths never close — so re-entry evidence can always accrue). Re-earning starts only after a root-cause note is in the ledger, requires a fresh count at the demoted rung, and a severe demotion additionally requires a fresh countersign at the next promotion.
- **Drift (statistical evidence — e.g. an agreement rate):** two-bar hysteresis, described in § 2. Cheap in, cheap out: re-entry is one clean window, not a fresh N-count.

### Decay (staleness, not fault)

90 days at gated-apply or above with no qualifying clean outcome → drop one rung, marked **stale**. Re-entry: 3 clean outcomes at the lower rung. No root-cause note needed — nothing went wrong; the evidence just went cold.

### Breaker

Per-domain circuit breaker, orthogonal to rungs. Trip conditions are per-instantiation (budget ceiling, recommendation storm, severe incident, model-drift tripwire, manual). While tripped:

- every class in the domain executes at **recommend or below** (proposals keep flowing — visibility never trips off);
- promotions and evidence accrual freeze; the decay clock pauses;
- reset requires a human ack with the cause recorded.

A trip **demotes only the class whose acts caused it** (that's the fault path); every *other* class in the domain freezes but keeps its standing. Collective punishment turns one bad actor into a fleet-wide re-earning slog and teaches nothing.

A trip pauses the decay clock for **at most 14 days**; a longer outage does not preserve standing — decay resumes on day 15. Frozen standing is protection from someone else's incident, not a cryo-chamber for stale evidence.

**Bounded-auto precondition:** no class enters bounded-auto in a domain whose breaker is unarmed or alert-only.

### Adjudication — who decides

The words "confirmed harmful," "attributable," "causal class," and "farm-batch" all need an owner or they become whoever-benefits words. The rule: **the interactive orchestrator proposes the call in-session and stamps it in the ledger; the grant owner ratifies or overturns it at the next periodic review; a disputed call freezes the class (no promotion, execution one rung down) until ratified.** Unattended runners never adjudicate their own outcomes — an unattended lane that hits an ambiguous outcome parks it for the next interactive session. Merge-class labels get the same treatment: the FRESH-context reviewer verifies the class label as part of review (a mislabel is a review finding → upgrade + re-gate); a mislabel discovered post-merge voids that item's evidence and stamps the outcome dirty.

### Ledger

Every promotion, demotion, freeze, trip, countersign, and adjudication is recorded. Be honest about *where*: a record kind with no machine-readable home yet is kept in the paper ledger, which is authoritative until one exists, and nothing pretends otherwise. Where each event is recorded:

| Event | System of record |
|---|---|
| promotion / demotion / audit-pass for an instantiation's own systems | that system's event log |
| merge outcomes (class + clean/dirty) | the session ledger |
| freezes, breaker trips, adjudications | the session ledger + the breaker's own alert log |
| countersigns / grants | the instance's grant ledger. **The commit stores the countersign; it does not constitute it**: sessions commit docs, so an agent-authored commit must never read as the owner's signature. Each grant edit cites the owner's directive or exact structured answer plus a durable session or transcript reference |

## 2. Instantiation I — statistical-agreement classes (instance-defined)

This toolkit ships no instantiation for automated judges; an instance that has them defines the class grain, verifier, bars and numbers. The class half of the machine is fixed. It subsumes any routing standard's promotion recipe: that recipe's steps map onto the rungs as below.

- **Class grain is (model identity, policy hash, channel).** Changing any component (a model swap, a policy edit, a different channel) starts a **new class** at shadow with a fresh calibration; standing never survives any of them (a silent model upgrade invalidates a trust gate — the same instinct, now structural). Diversity dimension: items within the channel (a window sample).
- **Verifier:** an independent yardstick, always; never the judge itself. Other voices in a panel are report-only bias probes and never decide a verdict.

| Recipe step | Rung |
|---|---|
| Shadow-run | shadow |
| A/B judge | recommend (collapsed surface: the A/B report is the "queue"; the yardstick still decides live) |
| Promote at threshold | **bounded-auto** — the automated judge decides unattended, inside standing bounds: fail-safe to the full path, an unsure verdict escalates to the yardstick (never cached), reversible tags, a pinned policy hash, a periodic standing audit |
| Standing audit | the bounded-auto audit obligation |
| Regression → demote | the drift path below |

gated-apply is collapsed for judges (no per-item gate exists between A/B and promotion); demotion from bounded-auto therefore lands at **shadow**.

**Two-bar hysteresis** per class, replacing a single bar. The bars and the minimum window size are numbers an instance sets; the rule is fixed:

- **Promote / re-enter** at or above the upper bar over at least the minimum number of judged items. A smaller window is inconclusive and carries forward — it neither promotes nor demotes.
- **Hold band** between the bars: HOLD. No demotion, and no event on the wire; the gate tracks the band internally and the periodic digest carries the flag.
- **Demote** below the lower bar, or after two consecutive windows in the hold band: a demotion event, and the channel goes back to shadow (the yardstick takes the job).
- **Destructive or irreversible action classes keep the single bar:** any window under the upper bar demotes (a higher bar for destructive classes is the same instinct as the band).
- **Wire mechanics honor a "HOLD emits nothing" contract.** A band window emits no event: the consecutive-band counter is gate-internal state and band status surfaces through the digest. A demotion emits the standard single-window scorecard exactly as schema'd; the prior band window's numbers live in the report artifact the scorecard points to. No schema change.

Why a band and not one bar: audit windows are small enough that a single reading just under the upper bar is within sampling noise; the quality risk inside the band is already bounded by the fail-safe, the escalation and the reversible tags; and a demotion is not free (attention plus a recalibration run). The band-with-deadline caps drift exposure at about two audit cycles. Demote-on-first-touch optimizes the cheap risk and spends the expensive resource, the owner's attention, to do it. Reinstatement is cheap by design: one clean window at or above the upper bar re-promotes.

The same instinct sets every ladder: hard bar to earn, tolerance band to hold *statistical* standing, immediate demotion on *discrete* harm, cheap structured re-entry. Action classes and merges inherit the shape; only the numbers differ.

## 3. Instantiation II — action classes (instance-defined)

This toolkit ships no action-class table of its own; an instance that has one defines its classes, ceilings and numbers. The class half of the machine is fixed. Class grain: **action class per target domain**. Diversity dimension: **distinct targets, at least two evidence kinds — of which `manual` never counts toward the floor** (a manual kind with a free-form reference, alternated with one real kind, would satisfy the letter of "at least two" while adding nothing independent).

- **Verifier:** re-observation through an independent telemetry path — the next model-bearing run record or heartbeat, produced by the target itself and evaluated by the monitor, never by the actuator's say-so. The actuator's `verified` stage may *stamp* that observation; it cannot self-declare success. If the next run record contradicts the stamp, the observation wins and the act is `failed`.
- **Executor per rung:** recommend = an operator applies by hand; gated-apply = the actuator applies upon the operator's approval (the approval click is the gate predicate); bounded-auto = the actuator applies within bounds, with no per-act click.
- **Bounded-auto scope is always an explicit allowlist, never "the fleet":** the countersigned bounds enumerate the eligible targets, and a per-target ceiling (for example "gated: has external side effects") overrides any class rung — the ladder never out-ranks a target's own ceiling.

A reference action-class set, with ceilings. An instance sets its own standing per class; every class enters **recommend** at the start by construction (recommendations are the product):

| Class | Action | Tier | Ceiling |
|---|---|---|---|
| A1 | Force-run a job | Low | bounded-auto eventually |
| A2 | Mute/unmute alerts | Low | bounded-auto eventually |
| A3 | Model pin swap | Medium | gated-apply; auto only after a long clean record |
| A4 | Pause/resume a job | Medium | gated-apply |
| A5 | Fallback-chain edit | High | gated-apply only |
| A6 | Quarantine | High | gated-apply only |
| — | Secrets, deletions, migrations | — | **never** (out of scope permanently) |

- **shadow for acts** is the observation sources themselves (drift tripwires, heartbeat evaluators), which are already live.
- **recommend → gated-apply:** 5 verified-clean approved recommendations for the class (approved in the UI, applied by hand, `verified` clean) across at least 3 distinct targets, plus the countersign (§ 1). The countersign may cite the observation sources' pre-ladder history once, at first grant.
- **gated-apply → bounded-auto:** the § 1 defaults — 10 clean gated applies across at least 3 targets (Low), 25 across 5 (Medium). High tier: no bounded-auto.
- **Demotion:** the fault path (§ 1). A `reverted` act, or a `failed` act **that mutated anything** (partial apply, verification miss after apply), demotes the class one rung; a breaker trip demotes the causal class only. A **fail-closed refusal** — a stale-state abort, the gate predicate refusing, nothing mutated — is the gate *working*: logged, never a demotion (a wire format may fold pre-apply refusals into `failed`; the ladder distinguishes by whether a mutation occurred). Sustained abort churn is breaker territory, not a demotion lever. Acts are discrete, so the hysteresis band does not apply to them.
- **Phase gates:** a phase that adds gated-apply requires the instance's act-lifecycle decisions promoted and its wire schema tightened; the phase that adds bounded-auto requires the first bounded-auto countersign plus an armed breaker (already required by § 1). The instance names the prerequisites.

## 4. Instantiation III — /orchestrate merge classes

This table defines what an orchestrator may merge on its own evidence, what needs the full ceremony, and what never leaves a human's hands. The charter is raising the trust ceiling per ticket (verification loops, eval gates, review-agent stages) so that small, well-specified tickets approach merge-without-deep-review. It codifies live practice rather than inventing new permissions.

Class grain: **merge class, held portfolio-wide, activated per-repo** — the rung is earned by the *process* across ≥3 repos (that's what the diversity floor proves), but gated-apply/bounded-auto operates in a given repo only after (a) the repo passes the eligibility precondition below and (b) the class has **≥3 clean gated merges in that repo** — pooled evidence never authorizes a repo the class has no history in. Diversity dimension: **distinct repos**. Verifier: CI + FRESH-context reviewer + the second-family review the override names, at merge time; **clean** = no revert/hotfix/rollback attributable within 7 days (the outcome matures on day 7, § 1) and no flag from the weekly standing audit. Executor per rung: recommend = PR opened, a human merges; gated-apply = the interactive `/orchestrate` session merges when the gate bundle is green; bounded-auto = an unattended runner merges within caps.

Two clarifications against the loop standard, stated so they read as one policy and not a quiet divergence:

- **"Builders land draft PRs" stands; who merges them is what the rungs define.** In an authorized run, the *orchestrator* merging a gate-green PR is the gated-apply executor. Unattended (bounded-auto) merge authority exists only where this table grants it.
- **MC-4 and the split-ticket rule compose, not conflict:** a live-sensitive ticket splits into a code-PR half and a supervised apply half. The **apply/arm/deploy half is MC-4** — it never enters an automated queue — with one named exception: eligible app-deploy *execution* runs under § Deploy lane below. The **code-diff half may be charted** as a PR-only item, but it is charted at **MC-3-with-recommend-ceiling**: full ceremony, PR opened, **a human merges**. "MC-4" in a queue row is a charting error; the row should say MC-3/recommend with the apply split out.

**Repo eligibility is a precondition, not a class property:** a repo qualifies for gated-apply at all only when **merge ≠ deploy is verified** — merging to main must not change any live system without a separate supervised deploy step. Verified, not assumed. Eligibility is re-probed at charting time: compare against the *remote's* main HEAD, never a possibly-stale local remote-tracking ref, and carry an `observed_at`; a stale observation reads as *unknown*, never as zero-delta.

**Per-repo activation bootstraps through recommend:** the first ≥3 in-repo outcomes are earned as **recommend-rung merges** — the item runs the full gate bundle, a human merges, and those count (see the evidence rule below). No circularity: recommend needs no prior in-repo history; gated-apply needs recommend's receipts.

| Class | Scope | Gate bundle (the predicate at gated-apply) | Ceiling |
|---|---|---|---|
| **MC-0** | Docs/roadmaps/memory/config-comments in non-runtime repos (notes, READMEs of undeployed repos) | batch-PR; ONE fresh-review over the batch diff; ONE second-family review the override names; secret-scan clean; conventional commit | bounded-auto |
| **MC-1** | Mechanical code: dependency patch/minor with green CI, lockfiles, format/lint, comment-only, generated files | green CI + batch second-family review (fresh-review optional for dep-bots) | bounded-auto |
| **MC-2** | S-fix: ≤~75 changed LOC behavioral fix **with a regression test**, no schema/auth/API/dependency surface | own PR; regression test present + failing-on-main; CI green; FRESH-context review clean; second-family review the override names | bounded-auto |
| **MC-3** | Logic/feature change, schema, API surface, auth-adjacent, migrations-adjacent | full ceremony (MC-2 bundle; regression test where testable) + item charted `full` | **gated-apply — hard ceiling** |
| **MC-4** | Anything with live reach: wrappers, plists, secrets references, deploy scripts, CI workflows that deploy, money, external sends — everything the instance's supervised categories list | n/a — no machine gate exists for this class | **recommend — hard ceiling** |

Cross-cutting merge rules:

- **Authority-bearing scope carve-out.** An MC-0-tagged standards edit that **alters authority** — any of four named categories: **merge rights** (who may execute at which rung, § 4 above), **verdict authority** (whose review verdict binds), **supervised categories** (the instance's list), or **session policy** (which session or model executes an action) — keeps its MC-0 **ceremony** (light lane: batch-PR eligible, ONE fresh-review over the batch diff, ONE second-family review the override names, secret-scan clean, conventional commit) but **lands at the recommend rung regardless of the MC-0 tag**: PR opened, a human merges. **This changes the rung only, not the ceremony** — an MC-0 edit outside these four named categories is unaffected and stays gated-apply-eligible in the light lane wherever the class is activated; a reader must not read this carve-out as "MC-0 now needs full ceremony." Rationale: MC-0's gate checks the *diff*, not what the diff *means*, so an edit can pass a clean lint with a plausible rationale and still over-reach. The carve-out is scoped to these four categories only: a reader decides "does this edit alter authority?" from the list above, never from a fifth category or a "feels important" read; it is a floor on MC-0's existing scope, not a new scope.
- Bounded-auto lanes (now or future) carry per-run caps (≤3 merges/run, ≤10/week/class) and a lane breaker: **any post-merge revert trips the lane** — the class demotes one rung (fault path) and the lane pauses until human ack. **The per-run caps bind machine executors only — bounded-auto explicitly, gated-apply by extension; they never cap a human's own recommend-rung merges.**
- Class assignment happens at charting (the ceremony tag: `light` ⇔ MC-0/MC-1, `full` ⇔ MC-2/MC-3; MC-4 *applies* never enter an automated queue; the code-diff half of a live-sensitive ticket charts at MC-3/recommend per the clarification above). The label is **verified, not trusted**: the FRESH-context reviewer confirms the class as part of review; a mislabel is a review finding (upgrade + re-gate), and a mislabel found post-merge voids the item's evidence and stamps it dirty (§ 1 Adjudication). The orchestrator may upgrade an item's class mid-flight on surprise; it may never downgrade one.
- A PR that mixes classes is judged as its highest class. Splitting a PR to dodge a class boundary and merging the pieces separately is the same act at the same class — the ladder reads intent, and the session ledger records the split.
- Evidence accrues only from ledger-stamped sessions (the session handoff stamps class + outcome per row; a row is `pending` until its 7-day window matures at a later handoff). Merges outside the loop (a human's own ad-hoc merges) don't count toward machine promotion — with one principled exception: **at the recommend rung, human-merged items that ran the full gate bundle count as evidence** — for a demoted class re-earning *and* for per-repo activation. At recommend, the human-as-merger is the rung's definition, so the gate bundle is the only thing the evidence can measure; what never counts is a merge that skipped the bundle.

### Ceremony cuts — decision 2026-10-25

**Pinned merge after a CI-only update.** Let `A` be the last head both families approved, `H` the updated head, and `M` main's tip. This path is allowed only when all six conditions hold:

1. Main's commits since `A` touched none of the PR's files.
2. `H` is a merge commit whose first parent is `A` and whose second parent is `M`.
3. The PR's patch-id at `H` equals its patch-id at `A`.
4. Reconstructing the merge from `A` and `M` produces exactly `H`'s tree: no conflict edits and nothing added beyond the merge.
5. `A`'s receipts pass every ordinary merge-gate check except exact-head equality: both families reviewed `A` at the tier the PR's files need; every open BUG has its required waiver; every current or carried harden finding not later confirmed fixed has its own follow-up ticket, never this pull request itself.
6. On a run created after the update, read per job, every job the review runner requires is green; every other job is green or skipped. When the required set cannot be read, every job must be green.

After any owner go already required by the rung, use the repository's receipt-gated merge path. Its unrelated-update proof must establish all six conditions and pin the expected head; no body-line exception bypasses the gate. If any condition fails, run a real update round.

**Record-only docs.** The one-family path is a fixed machine allow-list, never a merge-time judgment. An admitted pull request must remain docs-tier and may use only one fresh review, from the family the override names as the docs reviewer; a receipt from only the other family still needs both families. Unmatched docs and every behavior-bearing instruction keep both families. The merge gate owns an append-only required-set log at a canonical path, independent of equivalent path spelling. At close, one batch lane, from the family the override names for that lane, must complete successfully, approve, and name every logged repository, pull request and full head token. Its verdict is its output's first non-blank line, bare (`VERDICT: APPROVE` or `VERDICT: CHANGES`), and any later verdict-shaped line or `[BUG]` bullet (`- [BUG]` or `* [BUG]`) refuses; the batch brief must tell the lane to open its output with that bare line. The close rehashes its envelope and output and refuses unknown models, malformed evidence, missing tokens, non-approval or any required-set mismatch.

**Train.** A train lands 2 to 6 approved pull requests from one CI run on their predicted final tree. It is opt-in per merge and formed only by the merging step (the person who merges, or a merge session); a run session never forms one, and a merge without `--train` is the ordinary path. It changes which CI run certifies a member, not who merges: the rung and every receipt requirement are unchanged. The train certifies the final tree with one run; it never replaces a member's own evidence:

1. Each member would merge alone today except for main having moved: its head is the reviewed head, and both families' receipts pass the merge gate (through the unrelated-update proof, conditions (a)-(d), when the receipts are on an earlier head).
2. The members' file lists are pairwise disjoint, and main since each member's merge-base touches none of them. Merge-path and CI-machinery paths ride alone (the instance's `TRAIN_SOLO_PATTERNS`: the merge, receipt, train and landing tools, the CI workflows and the CI suite runner). A guard-class member need not ride alone.
3. Each predicted tree is a clean `git merge-tree --write-tree` whose diff from the one before has exactly the member's paths and its reviewed verbatim patch-id.
4. One run on the train head, created after READY, on a head whose tree equals the predicted final tree, has every required job green, read per job, from every workflow the members' paths select. `gh run rerun --failed` may certify a red run of the train pull request only; the train pull request never merges.

What stays: each member keeps its own post-READY green on its reviewed head and its two-family receipts; each merges in train order through the merge command's train mode, pinned with `--match-head-commit`; and the L8 landed-tree check after each merge, which halts the train by name on a mismatch. `land` also reads the main run of every earlier landed member, job by job, before it prepares the next one, but that read only refuses (`train_prefix_run_red`) on an observed completed run with a failed job; an absent, queued, in-progress or cancelled run does not refuse, so it is a tripwire and not proof that each member's main run was green. What certifies the landed result is the train run on the predicted final tree together with the L8 landed-tree check. A train that refuses or halts lands nothing more: `abort`, then re-form. No `Train:` trailer is added to a squash message; `trains.log` and the train pull request's closing comment are the record. The first three real trains run attended by the person who merges, never overnight.

Branch deletes (owner decision, 2026-11-02, reading confirmed 2026-11-03): the train tool deletes only a `train/*` branch its own record named before it was pushed, after that train landed or aborted; a train that fails during formation is recorded as aborted, so its recorded branch may be deleted. Every other remote delete still prompts the owner. Until the instance's delete guard enforces this rule, the owner answers that prompt in the first three attended trains and no session answers it.

### Deploy lane — app deploys in interactive sessions

App deploys executed through the `deploy` skill's contract (the repo's deploy contract · health poll · auto-rollback to last-known-good · a notifier report) are **gated-apply in interactive sessions**: the session deploys when the pre-apply gate is green and reports after, instead of readying and asking.

- **Class grain:** (root interactive orchestrator, deploy-contract version, app). Evidence binds to the contract version per § 1 — a contract rewrite restarts the count. **Activation** is checked at use-time, per app: before the lane's first deploy of an app, the session verifies a prior clean supervised deploy exists for that app under the same contract (deploy log / ledger / notifier history) — that verified history is the pre-ladder evidence the countersign cites (once, explicitly, per § 1). No such history → this deploy runs **announce-first** (post intent + gate evidence, state the health-check plan, proceed — visibility, not a wait); the app is active after one clean lane deploy.
- **Pre-apply gate (the predicate — everything checkable *before* mutation):** change landed through its merge-class ceremony · exact candidate SHA + current-live SHA identified · baseline currently healthy · fresh backup exists for stateful services · pending migrations classified reversible/forward-compatible (**destructive or irreversible migrations disqualify the deploy from this lane** — that is supervised) · rollback SHA pinned · a positive health verifier configured (**`DEPLOY_SKIP_HEALTHCHECK`-style bypasses drop the deploy to recommend** — no verifier, no autonomy) · the notifier reachable.
- **Post-apply obligations:** the repo runbook's migration/deploy order executed as written · candidate SHA confirmed live · health check passed · report posted · outcome matures at 48h (§ 1 maturation). A red health check triggers the skill's rollback and the deploy is `failed` — never silently retried, and **a rolled-back SHA is not redeployed without a fix commit**.
- **Verifier:** the health endpoint plus the next independent observation (a portfolio-health or digest pass) — never the deploying session's say-so alone.
- **Fault path (§ 1, standard rules — not weakened):** a pre-mutation refusal (gate red, nothing deployed) is the gate working — logged, never a demotion. **Any deploy that reaches live and then requires rollback is a dirty outcome and demotes the lane one rung immediately.** Failed recovery, data loss, security impact, or prolonged user-visible breakage is severe: breaker trip + lowest operable rung, per § 1. Re-entry per § 1.
- **Scope guard:** *app* deploys only. Scheduled-job wrappers and their definitions, secrets, registry flips, DNS/network, destructive migrations, and everything the supervised categories list stay MC-4 / one-per-sitting. **Sub-agents never inherit the lane** — an `/orchestrate` session exercises it only from the root orchestrator session; sub-agents may return read-only GO/NO-GO evidence but never merge, migrate, deploy, roll back, or report for the lane. **A textual "unattended run" directive in a prompt confers no authority on its own.**

### Unattended-executor deploy class

- **Class grain:** (unattended executor, pinned deploy-contract version, **app × environment**). Evidence is strictly per-cell — nothing pools or transfers between cells, and § 1's evidence-binds-to-the-bundle rule applies (a contract rewrite restarts the cell's count). Evidence from one environment never authorizes another for the same app.
- **Earn-in (per-cell activation):** **3 clean lifecycle deploys observed in interactive sessions** under the same pinned deploy-contract version. Whether a given historical interactive deploy qualifies is checked at activation time against the ledger and the contract pin — never asserted from memory; a disputed count goes to Adjudication (§ 1). No cell is active until a grant for it is recorded in the instance.
- **"Lifecycle" means the Deploy lane above incorporated by reference IN FULL** — pre-apply gate, backup/migration predicates, pinned live + rollback SHAs, positive health verifier, post-apply obligations, independent verifier — **plus, for unattended runs:** a **per-environment deploy lease** (one deploy in flight per environment, ever) and an **atomic pre-mutation live-SHA check** (if live moved since the gate was evaluated, abort — a fail-closed refusal, not a fault).
- **Fault path (§ 1, standard rules):** any deploy that reaches live and requires rollback is dirty → the **cell** demotes immediately; severe (data loss, security, failed recovery, prolonged breakage) → breaker trip + lowest operable rung + fresh countersign to re-promote. Evidence resets on deploy-contract or target change.
- **Countersign scope:** a grant covers only the cells it enumerates. A target outside them, or **any weakening** of the lifecycle bundle (lease, SHA check, gate predicates), needs a fresh recorded countersign — § 1's gate-bundle binding applies with full force.

## 5. Adversarial review — provenance and residual risk

This standard was adversarially reviewed by a second-family model against the full cross-check corpus. It returned 19 findings, 9 of them HIGH in the first block alone. Every finding was adjudicated empirically against the corpus and all 19 were accepted; the sections above already contain the fixes. The record, compactly:

| # | Sev | Finding (gist) | Adjudication → fix |
|---|---|---|---|
| 1 | H | Act verifier = the actuator that also applies | ACCEPTED — verifier redefined as the monitor's independent telemetry observation; the actuator only stamps it (§ 3) |
| 2 | H | Judge grain (policy, channel) lets trust survive a model swap | ACCEPTED — grain now (model identity, policy hash, channel); changing any component starts a new class (§ 2) |
| 3 | H | Gate-weakening reset the count but not standing | ACCEPTED — weakening drops a rung immediately; tightening keeps standing (§ 1) |
| 4 | H | Per-repo grain vs cross-repo diversity incoherent; pooling authorizes untested repos | ACCEPTED — class held portfolio-wide, activated per-repo after ≥3 local cleans (§ 4) |
| 5 | H | 3–5 safe jobs could grant fleet-wide act autonomy | ACCEPTED — bounds spec = explicit target allowlist; a per-target ceiling outranks any class rung (§ 3) |
| 6 | H | Judges sit at bounded-auto with an alert-only breaker — a day-one self-violation | ACCEPTED — an instance's existing grants get a bounded compliance window to arm the breaker, else the judge classes drop to shadow; new bounded-auto grants get no window (the bounded-auto precondition, § 1) |
| 7 | H | Severe → shadow is unrecoverable for acts and merges | ACCEPTED — severe lands at the lowest *operable* rung; human-merged full-bundle items count as recommend-rung re-earning evidence (§ 1, § 4) |
| 8 | H | Freeze/trip/countersign events unrepresentable in current schemas | ACCEPTED — honest ledger table, paper ledger named authoritative (§ 1) |
| 9 | H | Hold-band "audit-pass" contradicts the "HOLD emits nothing" contract | ACCEPTED — band windows emit nothing; the counter is gate-internal; the digest surfaces band status; the demotion event carries the scorecard (§ 2) |
| 10 | M | Benign fail-closed refusals (`failed`, including stale-state aborts) would demote | ACCEPTED — demotion keys on mutation-occurred; refusals are the gate working (§ 3) |
| 11 | H | "Clean" countable before the revert window closes | ACCEPTED — evidence-maturation rule; the window keys on the maturation timestamp (§ 1) |
| 12 | H | Merge-class assignment self-selected, no adjudicator | ACCEPTED (partial by design) — the reviewer verifies the label; a post-merge mislabel voids evidence and stamps dirty; residual judgment goes through Adjudication rather than a classifier we do not have (§ 1, § 4) |
| 13 | H | "MC-4 never enters an automated queue" vs the split-ticket rule | ACCEPTED — the apply half is MC-4; the code half charts at MC-3/recommend; stated as composition (§ 4) |
| 14 | H | Orchestrator/unattended merge authority vs draft-PR and never-auto-push | ACCEPTED — clarified: gated-apply names the orchestrator-merge practice inside an authorized run; never-auto-push binds outside the gates (§ 4) |
| 15 | H | A "grandfathered" mechanical class bypasses every earning rule | ACCEPTED — an instance may seal an enumerated exception with a countersign, and the category is closed once sealed (§ 4) |
| 16 | M | An "N/N through this exact gate" claim overstates the ledger | ACCEPTED — claims are softened to ceremony-precedent; a bounded-auto count starts from zero under the exact bundle (§ 4) |
| 17 | M | Harm/attribution/farming words have no owner | ACCEPTED — the Adjudication section: the orchestrator proposes, the grant owner ratifies, disputes freeze, unattended lanes never self-adjudicate (§ 1) |
| 18 | M | Breaker trips pause decay indefinitely | ACCEPTED — the pause is capped at 14 days (§ 1) |
| 19 | M | `manual` evidence alternation games the ≥2-kinds floor | ACCEPTED — `manual` never satisfies the diversity floor (§ 3) |

**Second pass.** A later review of the loop standard's amendments caught five further defects in this document: per-repo activation was circular (fixed: bootstrap through recommend-rung merges); two leftover "audit-pass on HOLD" phrases contradicted the corrected wire mechanics (fixed: HOLD emits nothing, everywhere); a "both windows' numbers" phrase overloaded a typed single-window field (fixed: a schema'd scorecard plus a report artifact); the eligibility hand-off lacked freshness semantics (fixed: remote-HEAD comparison plus `observed_at`, stale = unknown); and merge-evidence rows needed an explicit `pending → matured` path (fixed: the ledger rule in § 4). All accepted; the adjudication table lives with the loop standard.

**Residual accepted risk.** Merge-class boundaries (mechanical vs behavioral, the ~75 LOC line) remain human-judged. The controls are the reviewer's label check, post-merge voiding, and Adjudication — a machine classifier is deliberately not a dependency of this standard.
