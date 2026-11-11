# Vendoring

## What is vendored

`.core-manifest` at the root declares it, and is read at a pinned commit. Its
grammar: UTF-8 text, LF line endings, ending in a newline, no empty or comment
lines. First zero or more `tree <folder>/` lines, then one or more paths, one
per line, relative to the repository root, sorted by byte value, with no
duplicates; every `tree` folder holds at least one path. Every listed path is
a regular, non-executable file (git mode 100644) here.

A consumer holds each listed path at the same relative path, with the same
bytes. A `tree` folder is one the core owns entirely in the consumer: every
file there must be listed. Any other listed path sits in a folder the consumer
shares with its own files, and only the listed file is held.

The folders this repository owns entirely:

```text
tree skills/_lib/ledger/
tree skills/_lib/recall/
tree skills/_lib/standards/
```

## The drift rule

A vendored file is never edited in a consumer. A change lands here first and
is tagged; the consumer then moves its pin to the tag in one pull request that
re-copies the files from the tagged commit's blobs and rewrites the pin. The
consumer's drift check fails on any difference, offline (files against the
pin) and against a checkout of this repository (the pin against
`.core-manifest` and the tree at the pinned commit, the tag and the main
branch).

`skills/_lib/ledger/` is vendored here from the public ledger repository under
the same rule, pinned by `ledger-vendor.pin` and checked by
`scripts/check-ledger-vendor.py`.

## Still to add

- A closure check here: no vendored module reaches a file outside the
  vendored set. Until it lands, the consumer's layer check runs it at every
  pin move.

## Repository-only files

Every tracked file that is not vendored is listed here, and only here; a file
listed in neither `.core-manifest` nor this table fails
`scripts/check-core-manifest.py`. None of these files is copied into a
consumer. `scripts/no-person-allowance.tsv` holds the few deny-list matches
allowed in place, each by its path and the sha256 of the matched text, never
the text itself.

| Path | What it is |
|---|---|
| `.core-manifest` | the list of vendored files: the folders this repository owns entirely, then every vendored path |
| `.github/workflows/ci.yml` | CI: the stand-alone tests, the manifest check, the no-person check and the ledger drift check |
| `.github/workflows/commit-identity.yml` | CI: the commit identity check on every pull request |
| `.gitignore` | keeps the bytecode Python writes out of the tree |
| `CHANGELOG.md` | the changes of each tag |
| `LICENSE` | the license: MIT, the same as the vendored ledger's |
| `README.md` | what this repository is and how it is vendored |
| `SECURITY.md` | how to report a vulnerability |
| `VENDORING.md` | this file |
| `ledger-vendor.pin` | the vendored ledger's pin: repository, tag, commit and one blob id per file |
| `scripts/check-core-manifest.py` | checks that every tracked file is vendored or repository-only |
| `scripts/check-ledger-vendor.py` | the vendored ledger's drift check |
| `scripts/check-no-person.py` | checks that no tracked file names a person, a machine, a home path or a private host |
| `scripts/ci/check-commit-identity.sh` | the commit identity check the workflow runs |
| `scripts/ledger-vendor-bump.py` | moves the vendored ledger to a new tag |
| `scripts/no-person-allowance.tsv` | the deny-list matches allowed in place, by path and hash |
| `scripts/tests/test_check_core_manifest.py` | tests of the manifest check |
| `scripts/tests/test_check_ledger_vendor.py` | tests of the ledger drift check |
| `scripts/tests/test_check_no_person.py` | tests of the no-person check |
| `scripts/tests/test_ci_workflow.py` | tests of the CI workflows' runners, triggers and gates |

## Carve record

This repository was carved, without history, from a private toolkit at commit
`5d2c033e6ddd86d4ea61f7e97dd69a8b4618923c`.

- 158 vendored files were copied unchanged from their blobs at that
  commit: 27 core files, 33 tests and fixtures of the
  core files, and 98 ledger files.
- Each vendored file is written mode 0644, whatever its mode at the source.
- The repository-only files (`ledger-vendor.pin`, the two ledger vendoring
  scripts and their test, and the commit identity workflow and script) were
  copied from the same commit; this file, `README.md`, `SECURITY.md` and
  `CHANGELOG.md` were written from templates at that commit.
- Allowed by the scan: the public ledger repository's owner/name slug, in `ledger-vendor.pin`, `scripts/ledger-vendor-bump.py` and `scripts/tests/test_check_ledger_vendor.py` only.
- Allowed by the scan: the person and system words under `skills/_lib/ledger/`, the public ledger tree vendored unchanged.
- Allowed by the scan: two no-person shapes in files under `skills/_lib/ledger/`, "private home folder" (a home-folder path to a dot folder or a user folder) and "decision number"; every other path, token and private-key pattern still applies there.
- Rewritten from its source: `.github/workflows/commit-identity.yml` carries the generic `self-hosted` runner label in place of the source's machine-named label, reads the `CORE_CI_RUNNER` repository variable in place of the source's two runner variables, and sets `persist-credentials: false` on its checkout.

Each line below is one `cmp` of the file here against
`git show 5d2c033e6ddd86d4ea61f7e97dd69a8b4618923c:<path>`, with the file's git blob id; every one showed no
difference.

```text
cmp ok 93536d5b78093fb79291b02dafe34127ad2f1417 install-profiles/linear.txt
cmp ok 59652c2c023629b968bc8230122b20dfc23b237f scripts/omp-harness-rules.py
cmp ok 7975b03db7c81bec3c18a7f1ac2c98f1536cea5f scripts/tests/test_core_standards_names.py
cmp ok f700efa6b662b917e10e37d0072fb99e37bbd8b9 scripts/tests/test_omp_harness_rules.py
cmp ok fdb0fe801450206f1bd4ee620e0a40c75fc2210e scripts/tests/test_worktree_sweep.py
cmp ok 23d7b840f92ba8dabbad1bd9c6cb0c1867c0867c scripts/worktree-sweep.py
cmp ok daae6f08a280b0c011d59a477bbc250f2bdc4264 skills/_lib/config_path.py
cmp ok 00232ee926cd54efa5c5ce2abc370f4aabfa9114 skills/_lib/harness_root.py
cmp ok dafb6ec5d1d9a2fd99e85a0d2aecd51a1e01664a skills/_lib/ledger/__init__.py
cmp ok 3d2c7ebc6f4050ab400c9623bc020c70ca969bb3 skills/_lib/ledger/__main__.py
cmp ok 1c67907220eaa2de990e053c10d7455237a7f858 skills/_lib/ledger/backup.py
cmp ok df7261593c8afc8a5c8eac2573a74cf41105223d skills/_lib/ledger/card.py
cmp ok 976a6c63dd68c26c4eb7d2fbf26f61afc1fe8d10 skills/_lib/ledger/cli.py
cmp ok 8a89bab7d6a377c8536567cfc92c0afdddae98a3 skills/_lib/ledger/decisions.py
cmp ok 7f4cdc5def0db58a267b95ecd2f5a05e18844287 skills/_lib/ledger/drill.py
cmp ok ff85e569496b8779b4402b99c4c565d4585efc39 skills/_lib/ledger/export.py
cmp ok cd82656852e4d2365a1e8eab6b21f64907815174 skills/_lib/ledger/gen_verb_registry.py
cmp ok fa95c38973803d98093c69e1d689c557654d3bf2 skills/_lib/ledger/guidance.py
cmp ok c9cf188416e0c509d0529710035f27bbad78082e skills/_lib/ledger/lease.py
cmp ok 3be24a887ecf0d2de4031d83bb37178a8845ccc8 skills/_lib/ledger/loop.py
cmp ok 821ff56dde950c950ef4ffb32f132240efdfb4ed skills/_lib/ledger/migrations/0001_initial.sql
cmp ok 93f656e1bac96c75d36f261929f40f4781f3e384 skills/_lib/ledger/migrations/0002_open_decisions_ask_link.sql
cmp ok bdf769b6118df1f35d71e810b4412d3227a592cd skills/_lib/ledger/migrations/0003_loop_writer_identity.sql
cmp ok db0b9e7e46ade4d730780dbeb7f8ed87d960db4e skills/_lib/ledger/migrations/0004_open_decisions_held.sql
cmp ok 58c0f73c5f0552e4413d58a0d8b66fc73c028fe8 skills/_lib/ledger/migrations/0005_decision_records.sql
cmp ok 3d0449557a06d41692ef604d1ee8c231a37b6542 skills/_lib/ledger/migrations/0006_claim_lease.sql
cmp ok 35ef05da1b4e44f1268d886001270dcfc5304f10 skills/_lib/ledger/migrations/0007_actor_operator.sql
cmp ok 08fc3f8e02cdd443c8c1ca0fa52467e44b8bff36 skills/_lib/ledger/migrations/0008_item_spec_version.sql
cmp ok 1292ffdfd689c4ec43aaa7ed74cd61c7f39e6a24 skills/_lib/ledger/migrations/0009_entities.sql
cmp ok 94b959aa29d36ead9e73df7f1e6cb758a4735de5 skills/_lib/ledger/paths.py
cmp ok 6e7fa99d5f41fce2f2ae90883c44996bfefa7a19 skills/_lib/ledger/pointers.py
cmp ok 305e4ab5bd50e295bd0c595bba1a11d8786cf99f skills/_lib/ledger/restore.py
cmp ok 9cbe1c4beed94cddf888ad1fc7394d4c316cb1d8 skills/_lib/ledger/schema.py
cmp ok b5e4c64a3f906e5a6bcf147d896e3cf235878196 skills/_lib/ledger/search.py
cmp ok da795223d91186f7cce4cc462ecf50e8048a4fbe skills/_lib/ledger/store.py
cmp ok f8fd17c208651c625c4cedc6b6edc215b1745c15 skills/_lib/ledger/tests/conftest.py
cmp ok 1edda3b5a69a01fdba7cc4c28131b81b1394af3c skills/_lib/ledger/tests/fixtures/decisions/ticket.json
cmp ok 516de401fd51eef600737ec667785c683f78de63 skills/_lib/ledger/tests/test_append_many.py
cmp ok a3232def49b9eb01976034de1aae634779764997 skills/_lib/ledger/tests/test_card.py
cmp ok b365a776cc9ac24876709c9407b61858136bdc39 skills/_lib/ledger/tests/test_claim_lease.py
cmp ok c73a33be0177c6fda49cc97c745fa6efc30f0cb6 skills/_lib/ledger/tests/test_cli.py
cmp ok 646202126710925c0fd49dedd7a254c6aeb6427e skills/_lib/ledger/tests/test_concurrency.py
cmp ok 60b9dc41a9c6f4a1fdb520b9ed7ad06c817bead2 skills/_lib/ledger/tests/test_context.py
cmp ok c6396601d19d217ab585811840ba989f6403672e skills/_lib/ledger/tests/test_decisions.py
cmp ok e61e17407f5ece3ce2f62dc2d9ded7d27df0a86b skills/_lib/ledger/tests/test_doctor_summary.py
cmp ok 27b18bbcf0c3aae9c620d7d55f3617aa1cc4a002 skills/_lib/ledger/tests/test_elect.py
cmp ok aa9cc001eb816fd05666ff17f14600e18dae7320 skills/_lib/ledger/tests/test_entities.py
cmp ok a4f95a4ca707d8357e43a44c16c67fd028f16c4a skills/_lib/ledger/tests/test_events_verb.py
cmp ok f4bde6b101813491da7bf2f60c293f8463c604f5 skills/_lib/ledger/tests/test_expect_revision.py
cmp ok d30c639561e993365116e49bb4b277da3556042d skills/_lib/ledger/tests/test_export_backup_restore.py
cmp ok 42391df1f0af1095f98bc8e0c579cc5531cd05ae skills/_lib/ledger/tests/test_first_try_verbs.py
cmp ok deec23f7e6a398576a7347d03d4c75a8bb3313a9 skills/_lib/ledger/tests/test_grants.py
cmp ok a9fb77a601e6faad655113b10e99951e8d3293cf skills/_lib/ledger/tests/test_guidance.py
cmp ok 0deaf6118351751937e69a2acf1ef99dfcd97303 skills/_lib/ledger/tests/test_idempotency_keys.py
cmp ok 93ef379f76382dfb7adb70a5371bef85bb9cfc18 skills/_lib/ledger/tests/test_item_spec.py
cmp ok 699f6cea62233cf77e050866312921d1029b8577 skills/_lib/ledger/tests/test_ledger.py
cmp ok 3a6424b163007996e36a660f2dc1157be123d072 skills/_lib/ledger/tests/test_link_duplicate_of.py
cmp ok 7d6d681416a0719bac07202e3ed574788a33a6e5 skills/_lib/ledger/tests/test_loop_writer_identity.py
cmp ok 03b4bc9e31d50d867c806e55c5323e35cbbe6bd2 skills/_lib/ledger/tests/test_newer_schema_refused.py
cmp ok cc35f0b4c30ca8a8401ba6246cbff8ad47ea02b6 skills/_lib/ledger/tests/test_operator_field.py
cmp ok 87ff513b8f79f210fd88712c3e60ceb02ae28fe9 skills/_lib/ledger/tests/test_paths.py
cmp ok c1772165371e17994ede82f6b0ec81286d50f55a skills/_lib/ledger/tests/test_questions_verb.py
cmp ok ea418662d67671e9d04be9c18c16bcc5b9478912 skills/_lib/ledger/tests/test_readonly_open.py
cmp ok 0912e26e367134bad8bae5d4abb3b8a928b5ce2b skills/_lib/ledger/tests/test_resume.py
cmp ok a94c684f909e20761f28a1b9b50f67ef8d07f129 skills/_lib/ledger/tests/test_search.py
cmp ok dd0ed8cb6e92191cd5c07bd1b6b12b151cb01297 skills/_lib/ledger/tests/test_search_filters.py
cmp ok 3b7f0acf44ee279b4f95f8e5b61414ba697f3d8f skills/_lib/ledger/tests/test_search_round2.py
cmp ok b7b66c788767a5ea4c609fe498b9eda146c92a8b skills/_lib/ledger/tests/test_source_locator.py
cmp ok 4caf06889664258c0c5eec3af85fff3bd9df39bb skills/_lib/ledger/tests/test_utterance_origin.py
cmp ok 893e06e697c29cd7f21444af54f3ada18e24e8f0 skills/_lib/ledger/tests/test_verb_discovery.py
cmp ok 5f1b755731127c414138b4922e8297d4950534b6 skills/_lib/ledger/tests/test_verb_plugins.py
cmp ok e36ef658610109a5dc03f26c187c430439cc0976 skills/_lib/ledger/tests/test_verb_registry_generator.py
cmp ok 30adfa304f4c022d2def95e23b75357b00e2103a skills/_lib/ledger/tests/test_wave_slot_keys.py
cmp ok 6b2777ddcfea8882646f03e483dda0d54b6b0712 skills/_lib/ledger/verbs/__init__.py
cmp ok 43aadf5f53d89a47d3ce6bd15e2ff890015723dd skills/_lib/ledger/verbs/_registry.py
cmp ok 9df5b8746bea718a5b69954568d03de026e1cc67 skills/_lib/ledger/verbs/append.py
cmp ok 7b9cb041be28b4e02ee17caf3f3baaa079b57679 skills/_lib/ledger/verbs/append_many.py
cmp ok 8aedb371a89985c130008c0cb45adee871739b5c skills/_lib/ledger/verbs/backup.py
cmp ok b474d124048d087024a12ac05ba49eaaeb4b400d skills/_lib/ledger/verbs/card.py
cmp ok 342321ec2607478906b42941b0c080d534e31262 skills/_lib/ledger/verbs/claim.py
cmp ok e4403e5eb81fd48473ad5fd7cd6361ebaf12f3ea skills/_lib/ledger/verbs/context.py
cmp ok ebca1ca72e6e8ef599f498978469dd85abfe5bbd skills/_lib/ledger/verbs/decisions.py
cmp ok f0f30af571c73e1b3114117fb346f8aafaa30bd2 skills/_lib/ledger/verbs/doctor.py
cmp ok 07396c18da22238ec9cb75b377511282fe3bdef5 skills/_lib/ledger/verbs/elect.py
cmp ok 937aafdaed47d7213e0c6c3c69863c6437d854b2 skills/_lib/ledger/verbs/entities.py
cmp ok 3a8bf848e3b8608fc5698d82d5fd038306028ae0 skills/_lib/ledger/verbs/entity.py
cmp ok 2649d2fdbd7b78d48b6defd4d155023ed7e925e0 skills/_lib/ledger/verbs/events.py
cmp ok 7ac6fe64fc69e35ba7722b0c18e1f773d2df8541 skills/_lib/ledger/verbs/export.py
cmp ok 775bcf57ea5bde50ada2a497a4e81199f4a7930d skills/_lib/ledger/verbs/export_item.py
cmp ok 024d84fc0db1f81dff26b214582b673192ff0ef3 skills/_lib/ledger/verbs/grants.py
cmp ok 8b40787e9e62f7cee1fa42cabb540bb3a3b09271 skills/_lib/ledger/verbs/guidance.py
cmp ok cc8299cad5f98498ba20e8549c925318009351ae skills/_lib/ledger/verbs/index.py
cmp ok 36049fac5b924e4a4ff2b5ba7dbde34d01f06a3f skills/_lib/ledger/verbs/link.py
cmp ok 009aad1cb87d367871eb6480e8e168144935badf skills/_lib/ledger/verbs/lint_quotes.py
cmp ok 9fa7416eff5c1908aa71761c86e0f70ec84a0101 skills/_lib/ledger/verbs/new.py
cmp ok 09f155c98d92e5a3fee3075e13fc332e201aa0c0 skills/_lib/ledger/verbs/note.py
cmp ok 8046cbf11656e7d921afb42b4bd8847a5aaea1da skills/_lib/ledger/verbs/pick.py
cmp ok e7f15f30932254c25c6c014359ee8724f0f3f98f skills/_lib/ledger/verbs/query.py
cmp ok 1a1e219686270ace37f23603817789b39b1c9d6b skills/_lib/ledger/verbs/questions.py
cmp ok 7dc88b7e9bb51102298508c21a660ba5bf0e0dbb skills/_lib/ledger/verbs/ready.py
cmp ok a8ef9bea2ce122cd83c4c87a91d5281e0280d599 skills/_lib/ledger/verbs/restore.py
cmp ok b13febab9957069c2c8ccc850195e82733a4d806 skills/_lib/ledger/verbs/resume.py
cmp ok 20cc1b1e355f2e646737dc76be0495689a04f6d2 skills/_lib/ledger/verbs/search.py
cmp ok 25829bc61af32cf0dc543051a99dd99bd2f4e936 skills/_lib/ledger/verbs/spec.py
cmp ok 016ba83ec5c2c89e0bab71ee3667328759f30df2 skills/_lib/ledger/verbs/state.py
cmp ok 9728093755f2f24742810dace76d358e21533849 skills/_lib/ledger/verbs/verify.py
cmp ok 42941104e253986d5e485abec574e130f6ab246d skills/_lib/model-windows.tsv
cmp ok 3a6145d12f975e7e07af2d50faad72b12de2f005 skills/_lib/onboarding_friction.py
cmp ok 96c16fa9daa46a3c8a7506e4296850eb69fdf83d skills/_lib/onboarding_plan.py
cmp ok 46ad8e4e2c638d4d35f73d23072ceb5305515213 skills/_lib/overrides.py
cmp ok ad0a7cde5eb5b1e0f568892befbe43c5c8c6204d skills/_lib/pool-defaults.json
cmp ok 36e61c763eed0762dae2bda04ea5e6dbbf6ee332 skills/_lib/pool_config.py
cmp ok a095e458d78be3bceb9a6873ae765e9ca262f461 skills/_lib/recall/__init__.py
cmp ok 1fa095a36f0c7dbc06ad633b15a3604a58cc9522 skills/_lib/recall/classify.py
cmp ok 8ebe803b631f90ec0619e5c004345d4b03cefd8e skills/_lib/recall/extract.py
cmp ok e88ee4631361d41479acc2e9b4807dc1b94c2287 skills/_lib/recall/guard.py
cmp ok 1450b2bc040160db4677dc4930244ebb03bda2be skills/_lib/recall/index.py
cmp ok 417effb704bb6ee74a376bc2837c70b90ea79278 skills/_lib/recall/ledger_verb.py
cmp ok 6ae532e44d9db6b7e35022f5c56b853cac9e0968 skills/_lib/recall/tests/conftest.py
cmp ok 56db5612385d6c8e2f731923f1ca3e67aec6a70a skills/_lib/recall/tests/recall_fixtures.py
cmp ok 7aaf8b4e8672c628d4e3e43817c8aa200a116edc skills/_lib/recall/tests/test_recall_admission.py
cmp ok 4b82058dc2c000cfe00b0f86ec773dc26e74d4a1 skills/_lib/recall/tests/test_recall_classify.py
cmp ok 2d47f8cc04e980bb145d76c21fc0080c06b40f63 skills/_lib/recall/tests/test_recall_cli.py
cmp ok 64242ccf541a4c2518cd19a8a002ae0f6b639401 skills/_lib/recall/tests/test_recall_guard.py
cmp ok c5000c051eb5296b4b93b9c56bdab2361373b8d9 skills/_lib/recall/tests/test_recall_hardening.py
cmp ok a3ad566878dec74f64fe3ff90c42d8092d48a8c4 skills/_lib/recall/tests/test_recall_index.py
cmp ok 4ffd0e81b54f0f44a5cb64c4299b6e7de195dc4d skills/_lib/recall/tests/test_recall_open.py
cmp ok 7fee573d941216af88ea8348184eba5ba8072536 skills/_lib/standards/README.md
cmp ok e81c52a5ecd6539a5e5a0032d3d7b18e01d514b6 skills/_lib/standards/ci.md
cmp ok 0db174c28829ca277352171bd70551a158d7b1b9 skills/_lib/standards/deploy.md
cmp ok ccef60ad26dc21113dcdc137ca361feae3d6684a skills/_lib/standards/harness-definition.md
cmp ok f0b91738a3f1ef1295b97e281da97bbaf74e26e1 skills/_lib/standards/licensing.md
cmp ok 8207c2469ac9b38f5c721a3bd3a24885a56957ae skills/_lib/standards/portable-core.md
cmp ok 2a3a00c586cb8a3a83d3f865017b5e2d3318f6a9 skills/_lib/standards/prompting.md
cmp ok 92e4a2863807d3d81083a76887afbe9ff34a6076 skills/_lib/standards/security.md
cmp ok fd3f8f7d4294a3004105955d0056946c73ebe0bc skills/_lib/standards/trust-ladder.md
cmp ok 4a3c9e514166d085f137cf807696eb8b8af122ae skills/_lib/tests/fixtures/model_promotion/overlay.json
cmp ok 461a6a917f13d917f998132ade44be8bd8609f2e skills/_lib/tests/fixtures/pool_config/claude-only.json
cmp ok d593ce172720ded430d324b4814cb154a51d37b0 skills/_lib/tests/fixtures/pool_config/codex-only.json
cmp ok f449470a537aa128a5ad04715b99e7a230364017 skills/_lib/tests/fixtures/pool_config/defaults.json
cmp ok b91bcf879b7d8f957b844af3218399142cfc631a skills/_lib/tests/fixtures/pool_config/invalid-model.json
cmp ok dcc4a5853694b9d13fe0d1bbab6af854eb605197 skills/_lib/tests/fixtures/pool_config/missing-field.json
cmp ok b70af430d74f45f1de83183cbd7efc58fbceda50 skills/_lib/tests/fixtures/pool_config/one-api-pool-overlay.json
cmp ok 4487698f3618084080f95f690d3044dacd89c102 skills/_lib/tests/fixtures/pool_config/one-api-pool.json
cmp ok 80b38f144951d2bdf49f1a97c7aef69189de1aaa skills/_lib/tests/fixtures/pool_config/override.json
cmp ok b398d6f22d06ede792af84d7d7a59fac9ecf18ad skills/_lib/tests/fixtures/pool_config/promote-grok.json
cmp ok 7723549a5802fc03b1dbea468e09b2e96cd58324 skills/_lib/tests/test_config_path.py
cmp ok 5e4ed7dcae75c3c47f64362244ea7a337108829d skills/_lib/tests/test_harness_root.py
cmp ok 5d09ae1ee89da043c20f86133e8c83d8843b550c skills/_lib/tests/test_onboarding_friction.py
cmp ok 5e77b342c5e4761107122662059b7ef4af807f84 skills/_lib/tests/test_onboarding_plan.py
cmp ok 517d8950d17eaf525c7acf7447886fc22061d74c skills/_lib/tests/test_overrides.py
cmp ok 6331b40151c2b2abae9446d526623bf65f4750ce skills/_lib/tests/test_pool_config.py
cmp ok 1c3110025d2ab1bb369446583e6ba1fef29cd093 skills/_lib/tests/test_pool_config_assurance.py
cmp ok 996df94b2b45da405a87224aca83adf926b693a8 skills/_lib/tests/test_pool_config_billing.py
cmp ok bca075859d04bc7b5b488baac785ddb54739a3f8 skills/_lib/tests/test_standards_resolve_once.py
cmp ok 8fbc5a907c635701e16869033c2a50f9e7fbde97 skills/_lib/tests/test_ticket_source.py
cmp ok 9ef274f2d55e77c0f220b7946803896eeb7872b6 skills/_lib/tests/test_trust_keys_documented_resolution.py
cmp ok f3e28b0b2c76d88eb0fa7665e298a6f45fda0ae5 skills/_lib/ticket_source.py
```

## Pin moves

None yet.
