# AegisOps

Autonomous incident-response agent over real OpenTelemetry data.

> Work in progress. Master plan: [`docs/PROJECT.md`](docs/PROJECT.md). Code freeze 22 Dec 2026.

## Develop

```bash
make install   # uv sync + git hooks
make check     # lint, typecheck, tests (same as CI)
make api       # http://localhost:8000/docs
make demo-up   # start the pinned OpenTelemetry Demo (../opentelemetry-demo)
```

## License

Apache-2.0
