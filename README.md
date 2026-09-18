# AegisOps

Autonomous incident-response agent over real OpenTelemetry data.

> Work in progress. Master plan: [`docs/PROJECT.md`](docs/PROJECT.md). Code freeze 22 Dec 2026.

## Develop

```bash
make install   # uv sync + git hooks
make check     # lint, typecheck, tests (same as CI)
make api       # http://localhost:8000/docs
make db-up     # local Postgres 17 on :5433
make demo-up   # pinned OpenTelemetry Demo (../opentelemetry-demo) + our collector layer
make flag name=paymentFailure variant=100%   # inject a fault; variant=off clears it
```

## Deploy

Every push to `main` builds the image, deploys a no-traffic Cloud Run revision, probes it, then shifts traffic (`.github/workflows/deploy-api.yml`). Keyless via Workload Identity Federation. Runbook: [`docs/RUNBOOK.md`](docs/RUNBOOK.md).

## License

Apache-2.0
