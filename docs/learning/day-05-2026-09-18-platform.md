# Day 5 · 18 Sep 2026 · CI, security scanning, Dependabot, Docker, Cloud Run (PRs #6–#12)

## What we did
CI on every PR; CodeQL + dependency review; Dependabot (and its first PR merged); production Docker image; keyless deploy to Cloud Run with probe-before-traffic; three deploys to get it right; GCP project configured from the CLI with cost guardrails. The API is live.

## The CI/CD flow in one paragraph
PR opened → GitHub runs `ci.yml` (lint+types, tests vs Postgres container, hooks, docker build), `codeql.yml`, `dependency-review.yml` on fresh VMs → checks show on the PR → squash merge → `deploy-api.yml`: `make check` again → OIDC token → WIF exchange → build/push image → deploy no-traffic revision tagged `sha-xxxx` → probe `/livez` on the tag → shift 100 % traffic → job summary with rollback command.

## Terms introduced

**GitHub Actions.** GitHub's CI/CD: YAML *workflows* in `.github/workflows/`, triggered by *events* (`pull_request`, `push`, `schedule`, `workflow_dispatch`), made of *jobs* (each on a fresh VM, in parallel unless `needs`), made of *steps* (shell or reusable *actions*).

**Runner.** The VM (`ubuntu-latest`) a job runs on. Ephemeral: nothing survives between runs except explicit caches/artifacts.

**Service container.** A sidecar container started next to the job (`services: postgres: image: pgvector/pgvector:pg17`) with a health check; the integration tests talk to it on `localhost:5433`.

**Cache vs artifact.** *Cache* speeds up later runs (uv downloads keyed on `uv.lock`, pre-commit envs, Docker layers via `type=gha`). *Artifact* is an output you download (coverage.xml, 14 days).

**Pinning actions by SHA.** `uses: actions/checkout@3d3c42e...  # v7.0.1` instead of `@v7`. A tag can be moved to malicious code; a commit hash cannot. Dependabot keeps the pins fresh (its first PR bumped `actions/cache` 4.3.0 → 6.1.0 within minutes).

**Concurrency group.** `concurrency: {group: ci-<ref>, cancel-in-progress: true}` cancels a superseded CI run; the deploy group uses `cancel-in-progress: false` so a deploy is never killed halfway.

**Least-privilege `permissions`.** Workflows default to `contents: read`; a job asks for exactly what it needs (`id-token: write` for OIDC, `security-events: write` for CodeQL uploads).

**CodeQL.** GitHub's semantic code analysis. It builds a queryable database of the code and runs security queries (`security-extended`). We scan Python *and* the workflow files (`actions` language) on PRs, pushes and a Monday cron. Findings go to the Security tab. First scan: 0.

**Dependency review.** On a PR, diff the lockfile and fail if a new dependency has a high-severity advisory (from the GitHub Advisory Database) or a denied licence (GPL/AGPL/LGPL/SSPL for our Apache-2.0 project).

**Dependabot.** GitHub's bot: alerts on vulnerable dependencies, opens *security update* PRs, and opens *version update* PRs on a schedule from `.github/dependabot.yml`. Ours: weekly, Tuesday 06:00 IST, uv + github-actions + docker ecosystems, minor/patch grouped, majors grouped. Verified genuine by: author `app/dependabot`, the new SHA matched the upstream tag, all checks green.

**Secret scanning / push protection.** GitHub scans commits for credential patterns and blocks pushes containing them. Already on for public repos; `detect-secrets` in pre-commit is the local first line.

**Docker image, multi-stage build.** Stage 1 (`builder`): install uv, sync dependencies into `/app/.venv` (dependency layer cached separately from source). Stage 2 (`runtime`): `python:3.13-slim` + the venv + `curl`, non-root user `aegis`, `EXPOSE 8080`, `HEALTHCHECK`. Result 76 MB. `.dockerignore` keeps tests/docs/.git out of the context.

**Layer caching.** Each Dockerfile instruction is a layer; unchanged layers are reused. Copying `pyproject.toml`+`uv.lock` and syncing *before* copying source means a code change does not reinstall dependencies. `--mount=type=cache` keeps uv's download cache across builds.

**Non-root container.** `USER aegis` (uid 1001): if the app is compromised, the attacker is not root inside the container.

**Entrypoint script.** Runs `alembic upgrade head` if `AEGIS_DATABASE_URL` is set, then `exec uvicorn ...`. `exec` replaces the shell so signals reach uvicorn. Cloud Run has no separate "pre-start" hook, so this is where migrations live.

**Cloud Run.** Google's serverless container platform: give it an image, it scales instances 0→N per request load and bills per 100 ms of CPU/memory while serving. Our flags: 1 vCPU, 1 GiB, timeout 900 s (long SSE), concurrency 10, **min 0** (no idle cost), max 3, cpu-boost (faster cold start), session affinity (SSE sticks to one instance).

**Revision.** An immutable deployment of a Cloud Run service (image + config). Traffic is split across revisions by percentage; each can have a *tag* giving it its own URL (`https://sha-a6352db---aegisops-api-…run.app`).

**Probe-before-traffic (blue/green-lite).** Deploy with `--no-traffic`, hit the tagged URL, then `update-traffic --to-latest`. A broken revision never serves users; rollback is one command to the previous revision.

**Artifact Registry.** GCP's container registry. Repo `aegisops`, image `api`, tags `<sha>` and `latest`. Cleanup policy: keep 5 most recent, delete > 30 days, so storage stays inside the free allowance.

**OIDC (OpenID Connect) token.** A signed JSON Web Token GitHub mints for a workflow run, stating `repository`, `ref`, `sha`. Short-lived (minutes). It is *proof of identity* that other clouds can trust.

**Workload Identity Federation (WIF).** Google's mechanism to trust an external identity provider. A *pool* (`github`) holds a *provider* (`aegisops-repo`) with the GitHub issuer URL, an attribute mapping, and a condition `assertion.repository == 'lokeshbothra21/aegisops'`. Google STS swaps the GitHub token for a short-lived Google access token as the deployer service account. No JSON keys anywhere (ADR-012).

**Service account and IAM roles.** A non-human Google identity. `github-deployer` has `run.admin` + `artifactregistry.writer` + `iam.serviceAccountUser` on the runtime SA. `aegisops-api` (runtime) has only `secretmanager.secretAccessor`. Least privilege: each identity can do only its job.

**GitHub variables vs secrets.** Project id, region, provider name, SA emails are not secret, so they are *variables* (visible, auditable). Secrets (later: the DB URL) are encrypted and masked in logs.

**Budget alert.** Billing budget "aegisops guardrail" ₹500 with emails at 50 %, 100 %, and forecast. The single best cost safety net.

**Google Frontend (GFE).** Google's edge proxy in front of `*.run.app`. It reserves the exact path `/healthz` and answers it itself with an HTML 404, so requests never reach the container. Diagnosed by comparing `Content-Type` (HTML vs our `problem+json`) and the `server: Google Frontend` header across paths. Fix: rename liveness to `/livez` (ADR-015).

**`--no-traffic` on first deploy.** gcloud refuses it when the service does not exist yet. The workflow checks `services describe` and drops the flag only the first time.

**gcloud configurations.** Named sets of account/project/region. `aegisops` config keeps this project separate from the machine's default (a service account for the ERP project).

## Interview questions
1. *Walk me through what happens when you merge to main.* (The one-paragraph flow above.)
2. *Why WIF instead of a service-account key in GitHub Secrets?* Keys are long-lived and leak; WIF issues minutes-long credentials only to workflows from this exact repository, verified cryptographically.
3. *How do you make a deploy safe?* New revision with zero traffic, probe it on its own URL, shift traffic only on success; rollback is a traffic switch, not a rebuild.
4. *What broke on your first deploys?* `--no-traffic` on service creation; then GFE swallowing `/healthz`. How found: read gcloud's error; compared response headers across paths to prove the 404 was Google's, not ours.
5. *Why pin actions by SHA?* Tags are mutable; a compromised action could exfiltrate the OIDC token. Dependabot keeps SHAs fresh so pinning does not mean stale.
6. *Why re-run `make check` inside the deploy workflow?* The merge result can differ from what the PR tested; also the deploy must not depend on another workflow's status.
7. *How do you keep this at ₹0?* min-instances 0, max 3, registry cleanup, budget alert, and no always-on components (ADR-009).
