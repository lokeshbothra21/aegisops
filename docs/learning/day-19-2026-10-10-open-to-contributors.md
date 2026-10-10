# Day 19 · 10 Oct 2026 · Opening the repo to contributors (PR #43, E12.8)

## Why
The repository has been public since day one, but it was public the way a house with an unlocked door is open: nothing told a visitor how to come in. A developer who finds the repo through a post asks four questions within a minute: *What is this? Can I run it? How do I help? Is it safe to report a problem?* Until today we only answered the first.

Promotion (posts, launch) only pays off if the repo converts visitors into users, stars and contributors. That conversion is today's work; the posts come after.

## What we did
- **`CONTRIBUTING.md`:** the safety model as four non-negotiable rules, a setup that needs only Postgres (tests replay recorded model answers, so no keys and no network), the PR workflow, DCO sign-off, and code style.
- **`SECURITY.md`:** private reporting through GitHub, response times a one-person project can keep (7 days to acknowledge), and a scope list built from *our* threat model: reaching an action without approval, prompt injection that works, parameter injection, bypassing the demo's admission limits, secrets in logs or traces.
- **`CODE_OF_CONDUCT.md`:** Contributor Covenant 2.1, with reports going privately to the maintainer.
- **Issue forms** (`.github/ISSUE_TEMPLATE/`): a bug form with required reproduction steps and an area dropdown, a feature form with a safety-model checkbox and "would you like to build it?", and links that send vulnerabilities to the private form instead.
- **DCO check** (`.github/workflows/dco.yml`): reads the PR's commits through the API and fails if one lacks a `Signed-off-by:` line. No checkout, no third-party action, read-only token. Tested against PR #39 (correctly flagged: unsigned) and PR #38 (Dependabot signs off by itself).
- **PR template** gains a sign-off checkbox.
- **Repository settings:** topics, private vulnerability reporting enabled.
- **Starter issues** labelled `good first issue` and `help wanted`, each with the files to touch and a definition of done.
- **ADR-020:** DCO, not a CLA.

## The decision: DCO or CLA?
When someone contributes code, two questions arise: *did they have the right to give it?* and *on what terms do we hold it?*

- **Apache-2.0 §5** already answers the second: a contribution is under Apache-2.0 unless the contributor says otherwise. This is called **inbound = outbound**.
- **The DCO** answers the first, per commit: `git commit -s` adds `Signed-off-by: Name <email>`, which certifies the four short clauses of the Developer Certificate of Origin ("I wrote it, or I have the right to submit it under this licence"). It is used by the Linux kernel, GitLab and the CNCF projects.
- **A CLA** goes further: the contributor signs an agreement granting the maintainer broad rights, which lets the maintainer relicense the project later.

We chose the DCO. A CLA is a legal step before a first PR, exactly when newcomers are most likely to give up, and it tells people "this might become closed source one day". The thing a CLA would protect, an open-core business, does not need it: Apache-2.0 lets the maintainer build paid features in separate code anyway. The cost we accept: relicensing the whole project later would need every contributor's consent.

## How the sign-off check works
```
pull_request event
  └─ gh api repos/<repo>/pulls/<n>/commits        (read-only token)
       └─ jq: commits whose message has no line  ^Signed-off-by: Name <email>$
            ├─ none  → pass
            └─ some  → fail, list them, print the fix:
                       git rebase --signoff origin/main && git push --force-with-lease
```
Why not the popular DCO GitHub App? It is a third party with access to the repository, for a check that is a dozen lines of shell. Same reasoning as pinning actions by SHA: keep what can touch the repo small.

## Terms introduced

**Community health files.** The files GitHub looks for and links from the repo's sidebar and its "community standards" page: `README`, `LICENSE`, `CONTRIBUTING`, `CODE_OF_CONDUCT`, `SECURITY`, issue and PR templates. Newcomers check for them before investing time.

**Code of conduct.** Published rules for how people behave in issues and PRs, and how reports are handled. Most projects adopt the Contributor Covenant rather than writing their own.

**Coordinated disclosure.** Someone who finds a vulnerability tells the maintainer privately; the fix ships; then the details are published. A public issue would hand the exploit to everyone before the fix exists.

**Private vulnerability reporting.** A GitHub feature that adds "Report a vulnerability" to the Security tab. The report becomes a draft security advisory visible only to maintainers.

**DCO (Developer Certificate of Origin).** A per-commit `Signed-off-by:` line certifying that the contributor has the right to submit the change under the project's licence.

**CLA (Contributor Licence Agreement).** A signed agreement giving the maintainer rights over contributions, typically including the right to relicense. Safer for the owner, heavier for contributors.

**Inbound = outbound.** Contributions come in under the same licence the project is distributed under.

**Issue forms.** Issue templates written in YAML with typed fields (text areas, dropdowns, checkboxes, required flags), so a report arrives with reproduction steps instead of "doesn't work".

**Good first issue.** A label GitHub recognises and lists for newcomers. It works only if the issue is genuinely small and says which files to touch and what "done" means.

**Repository topics.** Tags on a repository. They put it on GitHub's topic pages (for example `github.com/topics/opentelemetry`) and in search filters: free, permanent discovery.

**Open core.** A business model: the core is open source; a paid edition adds hosting or enterprise features (SSO, multi-tenancy, audit exports). GitLab and Grafana work this way.

**Permissive licence.** A licence that allows any use, including closed-source and commercial, as long as the notice is kept (Apache-2.0, MIT). The opposite end is copyleft (GPL), which requires derivatives to stay open.

## Honest gaps
- **No one-command way to try it.** Replaying a captured scenario needs the fixture, which is published as a release asset, and there is no release yet (E11.7). This is the biggest barrier between a visitor and a star; it comes with the first release.
- **No web UI yet** (E10). The public demo is an API; a visitor cannot click anything.
- **README has no GIF or results yet.** Slots are marked "not yet" until the UI and the benchmark exist.
- GitHub Discussions is not enabled; questions go to issues for now.

## Interview questions
1. *How did you make the project ready for outside contributors?* Community health files, a setup that needs only Postgres because model calls are replayed from cassettes, issue forms that force reproduction steps, labelled starter issues with a definition of done, and a sign-off check.
2. *DCO or CLA, and why?* DCO: certifies provenance per commit at almost no friction; inbound = outbound under Apache-2.0. A CLA would allow relicensing, but adds a legal step at the point newcomers drop off, and open core does not need it.
3. *How do you handle security reports for a public project?* Private vulnerability reporting, response times you can actually keep, and a scope written from your own threat model, so researchers aim at what matters (here: approval bypass and prompt injection).
4. *How would you commercialise an Apache-2.0 project?* Open core or hosting, built in separate code you own; know that anyone else may also host it, which is the price of the trust a permissive licence earns.
5. *Why write your own DCO check instead of installing the app?* Least privilege: a third-party app gets repository access; a short workflow with a read-only token does the same job.
