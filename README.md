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

## License

Apache-2.0
