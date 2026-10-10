# Security policy

AegisOps reads untrusted telemetry, calls LLMs, and can change a running system after a human approves. Security reports are taken seriously and welcomed.

## Reporting a vulnerability

**Do not open a public issue.** Report privately through GitHub: the repository's **Security** tab, then **Report a vulnerability** ([direct link](https://github.com/lokeshbothra21/aegisops/security/advisories/new)).

Please include what you found, how to reproduce it (ideally against a local setup), and the impact you think it has.

This is a project maintained by one person. You can expect:

- an acknowledgement within **7 days**;
- an assessment and a plan within **14 days**;
- credit in the advisory and the changelog once fixed, unless you prefer otherwise.

## Supported versions

Only `main` and the current live deployment. There are no maintained release branches yet.

## In scope

Most interesting are the breaks of the safety model ([`CONTRIBUTING.md`](CONTRIBUTING.md#the-safety-model-is-not-negotiable)):

- **Reaching an action without approval**: any path to `execute` that skips `approval`, or an action running in public mode.
- **Prompt injection that works**: telemetry content (a log line, span attribute, flag description…) that makes the agent propose an action, cite fabricated evidence, or leak its prompt or configuration.
- **Action parameter injection**: anything that escapes the allowlist or reaches a shell.
- **Bypassing the public demo's admission limits**: the per-visitor limit or the daily cap ([ADR-018](docs/adr/ADR-018-public-run-admission-in-postgres.md)).
- **Admin routes** reachable without the admin token.
- **Secrets** exposed in logs, traces, responses, images or the repository.

## Out of scope

- Vulnerabilities in the OpenTelemetry Demo itself (report them [upstream](https://github.com/open-telemetry/opentelemetry-demo)).
- Rate limits and outages of the free-tier model providers.
- Denial of service by volume against the live demo, and findings that need a compromised maintainer account or machine.

## Testing rules

Test against **your own local setup** (`make db-up`, `make api`). Against the public deployment, stay within normal use: no load testing, no automated scanning, no attempts to reach data that is not yours.

The threat model is in [`docs/PROJECT.md` §11](docs/PROJECT.md).
