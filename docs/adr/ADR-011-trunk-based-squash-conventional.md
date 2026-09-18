# ADR-011: Trunk-based development, squash merges, Conventional Commits

**Status:** Accepted, 13 Sep 2026

## Context
Solo project, daily commits, a changelog to generate, and a public repo that will be read by interviewers.

## Decision
One long-lived branch (`main`) that is always deployable. Short-lived feature branches named `feat/E3.1-graph-skeleton`, `fix/...`, `docs/...`. Every PR is squash-merged and its branch deleted. Commit messages follow Conventional Commits (`feat(agent): add verify_evidence node (E4.1)`) and carry the feature ID.

## Alternatives considered
- **Git flow (develop/release branches).** Ceremony with no benefit for one developer and continuous deployment.
- **Merge commits.** History becomes a web of "Merge branch..." commits that obscure the one-feature-per-commit story.

## Consequences
- `git log --oneline` reads as the changelog; release notes (E11.7) can be generated from it.
- Merged to main means deployed (ADR-012 + deploy workflow).
- Lesson learned 16 Sep: never stack a PR on an unmerged branch; GitHub closes it (unrecoverably) when the base branch is deleted at merge. Rebase and open a fresh PR instead.
