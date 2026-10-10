# ADR-022: Web UI stack: Next.js 16, client-rendered pages, npm, Tailwind tokens without a component kit

**Status:** Accepted, 10 Oct 2026

## Context
The plan (§5.2, §5.6) named Next.js 15, pnpm, Tailwind and shadcn/ui for a four-page UI on Vercel. Starting E10 on 10 Oct 2026 surfaced facts the plan could not know:
- **Next.js 15 fails our own dependency review.** 15.5.x bundles a PostCSS version with high-severity advisories (GHSA-qx2v-qp2m-jg93 and three related ones); the fix ships only in 16.x. The first 16.3 releases then had critical advisories (RCE in image optimisation and `next/og`, cache poisoning), fixed in **16.3.8** (30 Sep, a security release).
- **pnpm is no longer one command away.** Node 25 stopped bundling corepack, so pnpm means an extra global install on every machine and CI runner. npm ships with Node.
- **The pages are live views of one API**, which scales to zero; every page's data changes while you look at it (a run streams for minutes).

## Decision
- **Next.js 16.3.8** (App Router, Turbopack), React 19.2, TypeScript strict. The npm audit of runtime dependencies is clean.
- **Client-rendered data.** Pages are static shells; components fetch from the API in the browser and subscribe to the run's SSE stream (ADR-021 says why the browser talks to the API directly). No server-side data fetching, so a cold API never blocks the first paint: the page shows and then fills.
- **npm**, with `package-lock.json` committed and `npm ci` in CI.
- **Tailwind v4 with our own colour tokens** (CSS variables, light and dark) and about five small components of our own (`Card`, `Badge`, `Button`, `Notice`, `Spinner`). No shadcn/ui yet.
- **The rules live in pure functions** (`lib/timeline.ts`: ordering, dedupe, step states, the evidence view), tested with Vitest; components stay thin.
- **Tests:** Vitest + Testing Library for the reducer, the stream hook (with a fake `EventSource`), the API client and two components; **Playwright smoke tests** on a production build at desktop and phone size (Pixel 7), with the API mocked at the network layer, asserting the page never scrolls sideways.
- **`agentRules: false`**: `next dev` would otherwise write `AGENTS.md`/`CLAUDE.md` into `apps/web`.

## Alternatives considered
- **Stay on Next.js 15 with an npm `overrides` entry forcing a patched PostCSS.** Works today, but it pins us to a branch that only gets backports, starting a new app on an old major.
- **Next.js 16.4.0** (the latest, 6 Oct). Four days old when we started; 16.3.8 carries the same security fixes and has had time to settle.
- **Server components fetching from the API.** Better for SEO, which this demo does not need, and every render would wait on a scale-from-zero API.
- **shadcn/ui.** Good components, but each one brings Radix packages and generated files; the UI needs a handful of static pieces and one popover. Revisit when a real dialog, menu or toast appears.
- **pnpm via a global install or `pnpm/action-setup`.** One more tool and one more third-party action for no benefit at this size.

## Consequences
- The plan's tech table (§5.2) now says Next.js 16 and npm.
- Playwright adds about a minute to CI (browser install and a production build).
- Dependabot watches `apps/web` (npm), and CodeQL now scans TypeScript.
- Dev-only advisory accepted: `braces`, reached through ESLint's Next plugin, has no fixed release; it never ships to the browser and dependency review fails only on runtime dependencies.
