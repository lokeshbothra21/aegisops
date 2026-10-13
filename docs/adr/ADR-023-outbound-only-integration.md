# ADR-023: Outside the laptop, every integration is outbound from the customer's side

**Status:** Accepted as the design, 13 Oct 2026. Not built yet: v1 runs against one target on the same machine (§1.4). The build items are in PROJECT.md §25.

## Context
Everything AegisOps does today works because AegisOps and the OpenTelemetry Demo run on one machine:
- **Telemetry:** the demo's Collector posts to our `/ingest` on the host.
- **Changes:** the flag watcher reads `demo.flagd.json` from disk; the container watcher reads the local Docker socket.
- **Actions:** a flag revert edits that same file; a restart calls the same Docker socket.
- **Notifications:** none. A human sees an incident by opening the web UI or calling the API.

A company that wanted to use AegisOps would not give a third party a route into its network, its Docker or Kubernetes API, or long-lived credentials to its systems. Each one is a way in for an attacker who compromises AegisOps, and a lot of trust to ask of an unknown vendor. The question is what shape the integrations take when AegisOps and the target are in different places, and whether today's code already fits that shape.

## Decision
**Every connection is opened from the customer's side, sends only what the customer chooses, and can be revoked by the customer. AegisOps never connects into a customer network and never stores credentials that reach into it.**

| Need | How it works | Who holds the key |
|---|---|---|
| Telemetry in | The customer's own OTel Collector exports to `/ingest` (OTLP/HTTP, the same contract as today) with an ingest token in a header. Filtering and PII redaction happen in their Collector before anything leaves. | The customer has the ingest token; AegisOps stores only its hash. |
| Changes in | **Pushed** as `change_events`: a step at the end of their CI/CD pipeline posts "service X deployed version Y"; flag tools (LaunchDarkly, Unleash, flagd) call a webhook on every change; Kubernetes events reach us through their Collector (`k8sobjects` receiver). | The same token, or one per source. |
| Notifications out | AegisOps posts to endpoints **the customer creates**: a Slack incoming webhook, a PagerDuty integration key, an email address. Each one can post to a single channel or service and nothing more, and the customer can delete it at any time. Approve and reject buttons in Slack call back to our API, which checks Slack's request signature and the approver's identity. | The customer creates the URL or key and can revoke it. |
| Actions | A small **runner inside the customer's network** polls AegisOps over HTTPS ("is anything approved for me?"), runs only allowlisted actions using credentials that never leave their network, and reports the result. This is how self-hosted CI runners and Kubernetes GitOps agents (Argo CD, Flux) work. | The customer. The runner's credentials are scoped to the four actions. |
| Code changes (pull requests) | **Not a v1 goal** (§1.4, ADR-005: remediation is operational). If it is ever added: a GitHub App the customer installs on chosen repositories with `contents` and `pull_requests` write. AegisOps opens a PR (for example a revert of the deploy that the change correlation blamed), and the customer's CI and reviewers decide whether it merges. | The customer installs, scopes and uninstalls the app. |

**Self-hosting is the first-class option.** AegisOps is Apache-2.0 open source, so a company can run the whole thing inside its own network, and its data never leaves. The outbound-only shape still applies inside one company: security teams segment networks there too.

## Why this fits the code we already have
The agent and the verifier never see where a row came from. They read `spans`, `logs`, `metric_points` and `change_events`. Moving from laptop watchers to pushed events changes only who writes `change_events`, not who reads them. Actions are already an in-process module behind `approval` (ADR-007). Moving execution into a runner changes where `execute` runs, not who may reach it.

## Alternatives considered
- **Pull: AegisOps queries the customer's observability backend** (their Prometheus, Loki, Datadog API) with read credentials they give us. This is how many AIOps products start, because there's nothing to install. Rejected as the default: we would hold long-lived credentials into every customer, and we would need one adapter per backend instead of one OTLP contract. Could come later as an import path for a customer who cannot change their Collector.
- **Inbound access** (VPN, an opened port, a kubeconfig handed to us). The biggest attack surface, and a non-starter for any security review.
- **Notify by email only.** Simplest, but approvals by email need signed links and are slow during an incident. Kept as a fallback channel, not the main one.

## Consequences
- **`/ingest` needs an ingest token before any real target uses it.** Today it trusts the network, which was fine for a laptop where the only producer was our own Collector (ADR-013). This is the first item to build.
- **A change-event endpoint** (`POST /api/v1/changes`, token-authenticated) replaces the two watchers for remote targets. The watchers stay for the local demo.
- **Approvals gain a second client** (Slack), so `decide` must accept identities other than the admin token and keep recording who approved in the audit log.
- **The runner is a second deployable**, versioned with the API, and the action protocol between them (claim, execute, report) needs idempotency so a retried poll never runs an action twice.
- **The honest limit:** none of this makes a company trust an unknown vendor with production data. That takes security certifications, contracts and time. That's why self-hosting is first-class.
