# Day 21 · 13 Oct 2026 · How AegisOps would plug into a real company (ADR-023, docs only)

## Why
LinkedIn comments and my own questions kept arriving at the same place: *everything works on my laptop, but how would a real company use it?* Do they need an account? Do they have to open their systems to us? How would we notify them, or open a pull request, without access? These are the first questions an interviewer asks after "what does it do?", so today we answer them in writing. No code changed; one gap turned up (the ingest token) and is now a planned item.

## The one rule
**Every connection is opened from the company's side.** They send us what they choose; we never reach into their network; they can cut any connection at any time. The four things we need follow from that rule.

### 1. Getting their telemetry: no account, no password
OpenTelemetry is not a company. It is an **open standard** (a shared format for logs, metrics and traces) plus free software that speaks it, mainly the **Collector**. There is nothing to sign up for.

Today, on the laptop, the demo's Collector has one extra exporter we added (`infra/otel-demo/otelcol-config-extras.yml`): "also send a copy to `AEGISOPS_INGEST_ENDPOINT`". Data is **pushed** to us; we never log in anywhere to pull it.

A company does the same: add one exporter block to the Collector they already run, pointing at our `/ingest` URL with a **token in a header**. Their Collector can drop fields and redact personal data *before* anything leaves. This is exactly how hosted observability vendors onboard customers.

**Gap found while answering this:** our `/ingest` has no authentication today. It was designed for one producer on the same machine (ADR-013). Before any other producer uses it, it needs an ingest token: the Collector sends it, the API stores only its hash and answers 401 without it. Now planned as **E9.8**.

### 2. Knowing what changed: they tell us
Today's watchers only work because everything is on one machine:
- the **flag watcher** checks a file on disk (`demo.flagd.json`) for edits;
- the **container watcher** asks the local Docker engine through its socket what's running.

Asking a company for remote access to its Docker or Kubernetes API would be a security nightmare, and nobody would agree. So in a company we flip it to **push**:
- **Deploys:** a final step in their CI/CD pipeline posts "payment v2.3 deployed".
- **Feature flags:** flag tools (LaunchDarkly, Unleash, flagd) already offer **webhooks**: "call this URL whenever a flag changes".
- **Restarts and scaling:** their Collector can watch Kubernetes events and forward them along with the rest of the telemetry.

All of these land in the same `change_events` table, so the agent and the change correlation don't change at all.

### 3. Telling a human: endpoints *they* create
To notify someone, we need somewhere to post, and the company creates it:
- a **Slack incoming webhook**: a secret URL that can post into one channel and nothing else; deleting it cuts us off;
- a **PagerDuty integration key**, which pages the on-call person;
- or an email address.

For approvals, the Slack message carries Approve and Reject buttons. A click calls our API. We check Slack's request signature (so nobody can fake a click) and record who approved in the audit log, just as the web UI's operator token does today.

### 4. Taking an action, or opening a pull request, without access
**Actions** (revert a flag, restart, scale, roll back) need credentials *inside* their network. We never hold those. Instead, a small **runner** runs inside their network and asks us over HTTPS: "is anything approved for me?" If something is, it runs that one allowlisted action with its local credentials and reports the result. The connection is still outbound from their side. GitHub's self-hosted CI runners and GitOps agents like Argo CD work the same way.

**Pull requests:** AegisOps does **not** write code or open PRs. That's a deliberate non-goal (ADR-005: remediation is operational, because a wrong code patch during an incident is far riskier than a revert). If it were ever added, the standard way is a **GitHub App**. The company installs it on the repositories it chooses, with only "write contents" and "write pull requests". AegisOps opens a PR, for example "revert the deploy that change correlation blamed", and their CI and reviewers decide whether it merges. They can uninstall it at any time.

### And the simplest answer: self-host
AegisOps is Apache-2.0. A company can run the whole system inside its own network, and nothing leaves. For an open-source project without a security certification, that's the realistic way in. Grafana and Sentry were adopted the same way.

## Why the code is already shaped for this
The agent and the verifier read four tables: `spans`, `logs`, `metric_points` and `change_events`. They never know who wrote a row. Moving from laptop watchers to webhooks changes the *writers*, not the *readers*. Actions are already a separate module behind approval (ADR-007), so moving them into a runner changes *where* `execute` runs, not *who may reach it*.

## Bonus: how the error rate is computed (asked the same day)
```
error rate = failed requests ÷ total requests      (per service, last 2 minutes)
```
- **Source:** the Collector's `span_metrics` counters, which count *all* traffic before sampling. Our stored traces keep every error trace but only 15% of healthy ones, so a rate computed from them would look far worse than reality.
- **Counters only go up**, so "calls in the window" = latest value − last value before the window. A counter that went *down* means the service restarted, so its new value is the count.
- **Server spans only** (requests a service *received*), so one failure isn't counted at every hop.
- **At least 5 calls**, or no rate is reported: 1 failure out of 1 call would otherwise read as 100%.

The **alert evaluator** that uses this number is not an AI and not an agent tool. It's a background loop inside the API (`alerts/evaluator.py`) that runs every 15 seconds. It loads `config/alerts.yaml`, computes each rule for each service, counts consecutive breaches, opens an incident when a streak reaches `for_windows`, and auto-resolves after 3 healthy checks if nobody has investigated. The evaluator decides *that* something is wrong; the agent works out *why*.

## Terms introduced

**Push vs pull integration.** Push: the source sends data to you (their Collector exports to our URL). Pull: you fetch it from them, which needs credentials into their systems. Push keeps the customer in control.

**Ingest token.** A secret the sender includes with every request so the receiver accepts only data from known producers. Store a hash, not the token.

**Webhook.** "Call this URL when X happens." A tool sends an HTTP POST to an address you registered. Used for flag changes, deploys and Slack messages.

**Incoming webhook (Slack).** A secret URL that can post messages into one channel. Revoking it removes the integration.

**Request signature.** A hash of the request body made with a shared secret, sent in a header. The receiver recomputes it to prove the request really came from (for example) Slack and wasn't altered.

**Customer-side runner.** A small agent inside the customer's network that pulls approved work from a service over HTTPS and runs it with local credentials. The same pattern as self-hosted CI runners and GitOps agents.

**GitHub App.** An integration a repository owner installs, with permissions they choose, on repositories they choose. It acts as itself (not as a person) and can be uninstalled at any time.

**Self-hosting.** Running software on your own infrastructure instead of using the vendor's hosted service. Data stays in your network.

**Egress-only.** A network rule that allows connections *out* but not *in*. Outbound-only integrations work behind such rules without opening a port.

## Honest gaps
- **None of the company-side pieces exist yet:** no ingest token, no change endpoint, no Slack, no runner. They're in PROJECT.md §25 (the ingest token is scheduled as E9.8).
- **Trust can't be built in code.** A company sending production data to an unknown vendor needs security certifications (SOC 2), contracts and a track record. Until then, self-hosting is the realistic path.

## Interview questions
1. *How would a company connect its systems to AegisOps?* Their own OTel Collector exports to our ingest URL with a token; no account, no inbound access. Filtering and redaction happen in their Collector before data leaves.
2. *Your change watchers read a local file and the Docker socket. How does that work for a remote customer?* It doesn't, and shouldn't. Changes are pushed instead: CI/CD steps, flag-tool webhooks, Kubernetes events through their Collector, all into the same `change_events` table.
3. *How do you take an action without access to their network?* A runner inside their network polls for approved actions over HTTPS, runs only allowlisted ones with local credentials, and reports back. We never hold their credentials.
4. *Does it open pull requests?* No, by design: remediation is operational, because a wrong patch mid-incident is riskier than a revert. If ever added, through a GitHub App the customer installs and scopes, with their CI and reviewers deciding.
5. *Why would a company trust you with its telemetry?* At first it shouldn't have to: self-host the open-source version. A hosted version needs certifications and contracts, which is a business problem, not a code problem.
