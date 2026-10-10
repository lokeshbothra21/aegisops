# ADR-020: Outside contributions are accepted under the DCO, not a CLA

**Status:** Accepted, 10 Oct 2026

## Context
The repository is about to invite outside contributors (CONTRIBUTING.md, starter issues, posts). AegisOps is Apache-2.0. Before the first outside commit lands, we have to decide on what terms contributions come in, because changing that afterwards means asking every past contributor. One future option on the table is open core: the Apache-2.0 project stays free, and a hosted or paid edition adds features such as multi-tenancy, SSO and integrations.

Apache-2.0 §5 already says a contribution submitted to the project is under the licence unless the contributor says otherwise ("inbound = outbound"). What it does not give is an explicit, per-commit statement that the contributor had the right to submit the code.

## Decision
- **Inbound = outbound:** contributions come in under Apache-2.0, the licence they go out under.
- **Every commit carries a DCO sign-off** (`git commit -s`, a `Signed-off-by:` line certifying the Developer Certificate of Origin 1.1).
- **Enforced by our own workflow** (`.github/workflows/dco.yml`): it reads the PR's commits through the API with a read-only token and fails if any commit (other than Dependabot's) lacks a sign-off.
- **No CLA.**

## Alternatives considered
- **A CLA (for example via CLA Assistant).** Contributors grant the maintainer broad rights, which would allow relicensing later. Rejected: it adds a legal step before a first PR, the moment newcomers drop off, and it signals that the project may close its source, which reduces trust for a project that is just starting to build a community. It is not needed for open core (see Consequences).
- **Nothing beyond Apache-2.0 §5.** Works in most cases, but leaves no explicit record of provenance per commit.
- **The DCO GitHub App instead of a workflow.** A third-party app with access to the repository, for a check that is twenty lines of shell over the API.

## Consequences
- **Relicensing the whole project later** (for example to a source-available licence) would need every contributor's consent or a rewrite of their code. Accepted.
- **Open core stays possible:** Apache-2.0 lets anyone, the maintainer included, build paid features on top. They would live in separate code the maintainer owns, not in contributed files.
- Equally, anyone else may sell or host AegisOps. That is the trade of a permissive licence, and part of why people trust it.
- Maintainer and assistant commits are signed off too. The check runs on PR commits, not on the squash commit GitHub creates on `main`.
