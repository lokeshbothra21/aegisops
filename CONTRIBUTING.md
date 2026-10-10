# Contributing to AegisOps

Thanks for looking. AegisOps is an incident-response agent that investigates real OpenTelemetry data, checks every claim it makes, and asks a human before it changes anything. Contributions of every size are welcome: a typo, a clearer error message, a new alert rule, a new fault scenario, a guide for connecting another system.

New here? Start with an issue labelled [`good first issue`](https://github.com/lokeshbothra21/aegisops/labels/good%20first%20issue). Larger ideas are tagged [`help wanted`](https://github.com/lokeshbothra21/aegisops/labels/help%20wanted).

## The safety model is not negotiable

A PR that weakens any of these will not be merged, however useful it is otherwise:

1. **Detection and verification are code, not the model.** The LLM proposes; deterministic code decides an incident exists and checks every cited trace, log, metric and change ([ADR-008](docs/adr/ADR-008-deterministic-alerting-and-verification.md)).
2. **Telemetry is untrusted input.** Tool results stay inside the `<telemetry untrusted="true">` envelope; nothing from a log line is ever treated as an instruction.
3. **No path to an action without approval.** Actions are reachable only from the `execute` node after `approval`; a graph-structure test enforces it ([ADR-007](docs/adr/ADR-007-mcp-reads-only.md)). Action parameters are allowlisted and nothing reaches a shell.
4. **Public mode never executes.**

## Set up

You need Python 3.13, [uv](https://docs.astral.sh/uv/) and Docker (OrbStack or Docker Desktop on macOS).

```bash
make install                      # uv sync + git hooks (ruff, mypy, detect-secrets)
make db-up && make db-migrate     # Postgres 17 + pgvector on :5433
make check                        # lint, types and the full test suite, exactly as CI runs it
```

That is enough for most changes. The tests need only the local Postgres: model calls are replayed from recorded cassettes, so **no API keys and no network**.

Only if your change touches live behaviour:

- `make demo-up` starts the pinned OpenTelemetry Demo with our collector layer (about 2.5 GB RAM).
- `AEGIS_GEMINI_API_KEY` / `AEGIS_GROQ_API_KEY` in a local `.env` (gitignored) enable real model calls. Never commit keys; `detect-secrets` will stop you.

`make help` lists every target. [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) explains how the pieces fit; [`docs/adr/`](docs/adr/README.md) explains why.

## Making a change

1. **Open or claim an issue first** for anything bigger than a small fix, so we agree on the approach before you spend time on it.
2. **Branch** from `main`: `feat/…`, `fix/…`, `docs/…`.
3. **Keep the PR to one change**, with tests at the right level: unit for pure logic, integration (against Postgres) for anything that reads or writes the database.
4. **Commit with [Conventional Commits](https://www.conventionalcommits.org/)** and sign off (below): `fix(ingest): explain 415 for protobuf bodies`.
5. **Run `make check`** before pushing. CI runs the same thing, plus CodeQL, dependency review and the sign-off check.
6. **Write an ADR** in `docs/adr/` if you made a design decision someone might later question: context, decision, alternatives, consequences, one page.
7. **Update docs** that your change makes wrong: README, `docs/ARCHITECTURE.md`, `docs/RUNBOOK.md`.

PRs are squash-merged, so the PR title becomes the commit on `main`.

`docs/learning/` is the maintainer's day-by-day teaching log; you do not need to edit it.

## Sign your commits (DCO)

Every commit must carry a `Signed-off-by` line. It certifies the [Developer Certificate of Origin](https://developercertificate.org/): that you wrote the change, or otherwise have the right to submit it under the project's Apache-2.0 licence. It is a statement, not a cryptographic signature, and there is nothing to sign up for.

```bash
git commit -s -m "fix(ingest): explain 415 for protobuf bodies"
# adds:  Signed-off-by: Your Name <you@example.com>   (from git config user.name / user.email)
```

Forgot on earlier commits? Add it to every commit on your branch, then force-push:

```bash
git rebase --signoff origin/main
git push --force-with-lease
```

Why DCO rather than a CLA: [ADR-020](docs/adr/ADR-020-dco-not-cla.md).

## Code style

- Python 3.13, `ruff` (lint and format), `mypy --strict`, Pydantic v2 at every boundary, `structlog` for logs.
- Match the surrounding code: its naming, comment density and idiom.
- Secrets never go in code, tests, logs or fixtures. Use obvious placeholders in tests.

## Reporting bugs and security issues

- **Bugs and ideas:** [open an issue](https://github.com/lokeshbothra21/aegisops/issues/new/choose).
- **Security vulnerabilities:** do **not** open a public issue. Follow [`SECURITY.md`](SECURITY.md).

Everyone taking part is expected to follow the [Code of Conduct](CODE_OF_CONDUCT.md).
