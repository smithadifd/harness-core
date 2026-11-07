# docker-compose deploy standard

How a self-hosted app ships to its Docker host. Like the notifier contract, this is a **convention plus a copy-in template — not a runtime-sourced shared library**. Each repo keeps its own small deploy script (by convention in its scripts folder), but they all converge on the contract below and start from one canonical template. A single shared deployer would have to encode every repo's quirks (a database backup here, bind-mounted config seeding there, boot-time migrations elsewhere) and would couple every repo's deploy path to one file's release cadence. The portable-core standard says copy the core and parameterize it locally instead.

The instance names its copy-in template (a parameterized, env-overridable script capturing the common shape below) and its reference implementations, the scripts the template was distilled from. The fullest shape is a git-pull-and-build app deploy: preflight → SSH clone-or-pull → build → `up -d` → health poll. A production deploy with a database adds a schema-aware migration step. Its rollback restores code and containers; schema changes need separate review. A fleet deployer that copies compose files across many hosts honors the same compose contract but is a fleet tool, not a single-app deployer, and is not a consumer of the template.

## What the deploy contract is

A deploy is: **on a remote Docker host, get the target commit's code into the compose dir, (re)build or pull the image, `up -d`, then verify health and report the result.** Every implementation MUST honor:

### Remote execution
- Reach the box over an **SSH alias** (`ssh <alias>`, keys configured) — never a raw password or an inline host+user string.
- Hosts differ in compose generation. A host running compose v1 takes `docker-compose` (hyphen), not `docker compose`, and may need its `PATH` exported first inside the non-interactive SSH session; a compose v2 host takes `docker compose`. The template's `DEPLOY_COMPOSE_CMD` + `DEPLOY_REMOTE_PREAMBLE` parameters encode which is which. The instance records each host's generation and preamble.
- Pass the env file explicitly: `--env-file <file>`. Never bake secrets into the compose file or the script.

### Idempotency + safety
- **Clone-or-pull, not clone.** A re-deploy against an existing checkout fetches + `reset --hard origin/<branch>`; a first deploy clones. Running it twice in a row is a no-op past `up -d`.
- **Preflight is local and refuses to guess.** Deploy from the deploy branch (default `main`) only, with a clean tree; fetching and resolving `origin/<branch>` must succeed, and a divergence prompts rather than proceeds.
- **Fail on a missing env file** on the box — never `up -d` a half-configured stack.
- **Prod-touching side effects are explicit and documented.** A pre-deploy database backup, bind-mounted config seeding, or boot-time migration belongs in the repo's `AGENTS.md` gotchas and, in the template, in the `pre_up_hook` / `post_up_hook` extension points — never smuggled into an unrelated change. Applying a deploy recreates containers; it is a deliberate act, never cleanup.
- **Restore the last-known-good remote commit on failure.** Capture the remote HEAD before changing it. If build, start, or health verification fails, reset to that commit, rebuild or pull its image, restart, and verify health. For pull-only deployments, require digest-pinned images in both the previous and target compose configurations; pulling a mutable tag after reset may fetch the failed image again. If no prior checkout exists or restoration fails, report that manual recovery is required. Code and image rollback does not reverse data or schema migrations; document and review migration recovery separately.

### Health + reporting
- **Poll a health endpoint** after `up -d` (retry with backoff, cap the attempts) and surface the URL + a log-tail command on failure. A repo with no HTTP surface may skip via `DEPLOY_SKIP_HEALTHCHECK=1`, but the default is to verify.
- **Report the result** through the notifier adapter the instance names: green on a healthy deploy, red on a failed health check. The adapter's own contract says how the webhook is resolved and kept secret, and a notify failure never fails the deploy.

## Parameters

The template reads everything from env vars (overridable inline or via a gitignored `.deploy.env` — looked up at the repo root first, i.e. one directory up from the script when it lives in the repo's scripts folder, then falling back to next to the script). No per-repo value is hardcoded.

| Parameter | Default | Purpose |
|---|---|---|
| `DEPLOY_REMOTE` | *(required)* | SSH alias of the Docker host |
| `DEPLOY_REMOTE_PATH` | *(required)* | Compose dir on the box (holds the `.git`) |
| `DEPLOY_REPO_URL` | `git remote get-url origin` | Git URL the box clones/pulls |
| `DEPLOY_BRANCH` | `main` | Branch to deploy |
| `DEPLOY_COMPOSE_FILE` | `docker-compose.prod.yml` | Compose file (relative to `REMOTE_PATH`) |
| `DEPLOY_ENV_FILE` | `.env.production` | `--env-file` passed to compose |
| `DEPLOY_COMPOSE_CMD` | `docker-compose` | `docker-compose` (v1) or `docker compose` (v2) |
| `DEPLOY_REMOTE_PREAMBLE` | the instance's host `PATH` export | Shell run at the top of the remote session |
| `DEPLOY_BUILD` | `1` | Build the image on the box (`0` = pull only; requires digest-pinned images) |
| `DEPLOY_APP_PORT` | *(unset → skip health)* | Port the health check hits |
| `DEPLOY_HEALTH_PATH` | `/api/health` | Path appended to the health URL |
| `DEPLOY_HEALTH_HOST` | resolved via `ssh -G` | Host for the health probe |
| `DEPLOY_SKIP_HEALTHCHECK` | `0` | Skip the health poll |

The template also carries one optional result-notify webhook parameter, named for the notifier adapter the instance chose. It falls back to that adapter's standard webhook variable.

Repo-specific extra steps hook in without forking the file: redefine `pre_up_hook` / `post_up_hook` in `.deploy.env` (it is `source`d, so the template preserves functions defined there), or — for a step that must run *on the box* — add it to the remote heredoc of the copied deploy script.

## How a repo adopts the template

Per the portable-core standard: **copy the core, parameterize locally.** Do NOT `source` the template file at runtime.

1. Copy the template → the repo's deploy script (by convention in its scripts folder; or keep the repo's existing filename).
2. Set the repo's parameters — either a small config block near the top of the copied script, or a gitignored `.deploy.env` (recommended). Gitignore `.deploy.env`; never commit real hosts/paths if the repo is public.
3. If the repo has prod-touching steps (database backup, migrations, config seeding), move them into `pre_up_hook` / `post_up_hook` (local) or the remote heredoc (on-box), and add a one-line gotcha to the repo's `AGENTS.md`.
4. Point the repo's local context file's end-of-session workflow at the repo's deploy script.
5. Verify: `bash -n` on the deploy script (and `shellcheck` if available), then a real deploy of an unchanged commit — it should reach `up -d` and pass the health poll idempotently.

## Tracked deploy scripts

The instance keeps its census of tracked deploy scripts and adoption status. A script's presence in the census does not certify full contract conformance; template adoption and fixes to live deployments remain reviewed per-repo work. A script whose shape is outside the contract (a retired path, a demo with a host-wide container stop, a fleet tool) is listed as such, not as an adoption target.

## Checklist for a new deploy script

- [ ] Copied from the template; parameters set via `.deploy.env` (gitignored), not hardcoded.
- [ ] SSH alias, not raw host/password. A compose v1 host uses `docker-compose` + its `PATH` export.
- [ ] `--env-file` passed explicitly; no secrets in the script or compose file.
- [ ] Clone-or-pull (idempotent); preflight refuses a dirty tree / wrong branch.
- [ ] Fails on a missing env file on the box.
- [ ] Health poll after `up -d`, with bounded backoff, failed URL, and a log-tail pointer on failure.
- [ ] Last-known-good remote commit captured before mutation; rollback restores code and image, restarts, and verifies health. Migration recovery is documented separately.
- [ ] Result reported (notifier adapter); notify failure never fails the deploy.
- [ ] Prod-touching steps (backup/migrations/seeding) live in a hook + an `AGENTS.md` gotcha.
