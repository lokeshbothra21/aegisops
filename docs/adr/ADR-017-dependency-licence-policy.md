# ADR-017: Dependency licence policy — strong copyleft denied, weak copyleft allowed

**Status:** Accepted, 23 Sep 2026

## Context
AegisOps is Apache-2.0. The dependency-review check (E9.7) originally denied GPL, AGPL, LGPL and SSPL. Adding LangGraph's Postgres checkpointer pulled in `psycopg` (LGPL-3.0-only) and the check went red on PR #21.

## Decision
Deny **strong** copyleft (GPL-2.0, GPL-3.0, AGPL-3.0) and source-available licences that behave like it (SSPL-1.0). Allow **weak** copyleft (LGPL, MPL-2.0) for dependencies we use unmodified as libraries. Never vendor or modify a weak-copyleft library without revisiting this ADR.

## Alternatives considered
- **Write our own checkpointer over asyncpg.** A day of work to avoid a licence that does not restrict us; reinvents tested code.
- **Deny LGPL and drop Postgres checkpoints.** Loses durable interrupts, the core of ADR-006.
- **Allow everything.** AGPL/SSPL genuinely constrain how the service may be offered; keep them denied.

## Consequences
- `deny-licenses` in `.github/workflows/dependency-review.yml` lists GPL-2.0, GPL-3.0, AGPL-3.0, SSPL-1.0.
- Interview answer: LGPL lets permissive/proprietary code link to the library as long as the library itself stays LGPL and replaceable; GPL would require our whole program to be GPL; AGPL extends that to network use.
