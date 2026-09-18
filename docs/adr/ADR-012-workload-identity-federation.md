# ADR-012: Keyless deploys with Workload Identity Federation

**Status:** Accepted, 13 Sep 2026; implemented 18 Sep 2026

## Context
GitHub Actions must push images and deploy to Cloud Run. The classic way is to download a service-account JSON key and store it as a GitHub secret. Long-lived keys leak, never rotate, and are the number-one cloud-breach vector.

## Decision
Use Workload Identity Federation. GitHub issues the workflow a short-lived OIDC token asserting the repository and ref; Google's STS exchanges it for temporary credentials of the `github-deployer` service account. The provider's attribute condition is `assertion.repository == 'lokeshbothra21/aegisops'`, so no other repo (including forks) can obtain credentials. The deployer has only `run.admin`, `artifactregistry.writer` and `actAs` on the runtime service account.

## Alternatives considered
- **Service-account JSON key in GitHub Secrets.** Works, but is exactly the practice production teams are removing.
- **Cloud Build triggers.** Moves the pipeline off GitHub Actions; a second CI system to maintain.

## Consequences
- Zero stored cloud credentials anywhere.
- Non-secret identifiers (project id, provider name, SA emails) are GitHub *variables*, not secrets, so they are visible and auditable.
- Two production facts learned on first deploy: `--no-traffic` cannot be used when creating a service, and `/healthz` is reserved by Google Frontend (ADR-015).
