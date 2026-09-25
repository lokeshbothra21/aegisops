# Day 15 · 25 Sep 2026 (second PR) · Executing approved fixes, safely (PR #28, E5.3, E5.5, E9.2, E9.3, E9.6)

## What we did
The last link in the loop: an approved proposal now changes the system.
- **Actions module (E5.3):** `toggle_flag`, `restart_service`, `scale_service`, `rollback_deployment`. A plain Python module, **not** exposed over MCP (ADR-007). Two backends: **LiveBackend** edits the flag file atomically (write to a temp file, rename into place) and restarts containers through fixed Docker Engine endpoints over the Unix socket, with no shell; **ReplayBackend** changes nothing and returns the recorded outcome for captured scenarios. Scale and rollback are refused by the live backend because the demo pins container names and ships one image tag.
- **`execute` node:** reachable only through a conditional edge from `approval` when the decision is `approved` or `auto` and the action is not `none`. The node re-checks the decision itself and raises "graph bypass" otherwise. Public mode records "execution disabled".
- **Parameter validation (E9.3):** allowlists from `config/policy.yaml`, strict patterns for names, variants and versions, replicas in 1..3, unknown keys dropped. Rejected requests never reach a backend.
- **Audit log (E9.6):** migration `0006` `audit_log` (ts, run, node, tool, args, duration, ok, actor). Every tool call (`agent`), every human decision (`admin:<name>`), every action attempt (`admin:<name>` or `policy:auto`), accepted or rejected.
- **Post-action verification (E5.5):** after `verify_delay_s` (90 s) the rule that opened the incident is re-evaluated over data **starting 15 s after the action** (counter-flush grace). If the rule has too little traffic to produce a reading, the exact fallback is used: error server spans since the action (every error trace is kept). Cleared → incident `resolved` ("resolved by toggle_flag"); still breaching → `failed`; the outcome records the value and the basis. Replayed actions use the recorded result.
- **Graph-structure test (E9.2):** walks the compiled graph and asserts the only edge into `execute` comes from `approval`, and that it is conditional. NFR-07 ("no action reachable without approval") is now a test that fails CI.
- 163 tests.

## Live result
Week 6 exit criterion **met live** ("S1 fixed end to end via flag toggle after approval"), on the fourth drill: `paymentFailure=100%` at 16:56:14 → incident 795 at 16:57:05 → three runs, the first two verified at 0.06 and 0.0 and correctly proposed nothing; the third (partial after a budget breach, correlation still ran) proposed `toggle_flag paymentFailure → off` → approved by `shreyas` → flag written 17:02:10 → charge errors stop → 90 s later the rule re-evaluated over post-grace data: error rate **0.0** (`basis: error_rate`) → incident **resolved**. Audit trail: 14 agent tool calls, `approval` and `execute` as `admin:shreyas`.

## How the safety layers stack
1. Graph topology: `execute` has one incoming edge, from `approval`, conditional on the decision (structure test).
2. The node re-checks the decision (bypass guard).
3. Policy: public mode never executes; autonomy level × risk × verified confidence for auto.
4. Validation: allowlists and strict patterns before any backend call.
5. Backend: fixed endpoints, no shell, atomic file writes.
6. Audit: every attempt, with its actor.
7. Verification: did it actually work? If not, the incident is marked failed, not quietly left "remediating".

## Terms introduced

**Atomic file replace.** Write the new content to a temp file in the same directory, then `rename` over the original. Readers (flagd) see either the old file or the new one, never half of each. flagd reloaded it within ~2 s (verified from span timestamps, after I had wrongly blamed it).

**Fixed argv / no shell.** Actions call specific API endpoints with typed parameters; nothing is passed through a shell, so `payment; rm -rf /` is just an invalid service name.

**Allowlist validation.** Accept only known-good values (named flags, named services, bounded integers) instead of trying to block known-bad ones. The test feeds injection strings and path traversal and expects rejections.

**Defence in depth.** Several independent guards, each sufficient alone for the common case, so one bug does not become an incident: topology, re-check, policy, validation, backend, audit, verification.

**Audit log with actor.** Who did what, when, with which arguments, and whether it worked; `agent`, `admin:<name>` or `policy:auto`. Answers "why did this change?" after the fact.

**Post-action verification.** Close the loop: re-measure the symptom after the fix, over post-fix data only, allowing for the metrics pipeline's flush lag; fall back to an exact signal when traffic is too thin for a rate. Measuring over a window that still contains pre-fix errors reports a working fix as failed; treating "no data" as "cleared" reports anything as fixed.

**Flush lag.** Aggregated metrics arrive in batches (every 10 s here); the first batch after an event can still describe the time before it. Any before/after comparison on metrics needs a grace period.

**Graph-structure test.** A test over the compiled graph's edges, not its behaviour; it protects the architecture from a well-meaning future edit.

**Background task hygiene.** A task started with `asyncio.create_task` that raises disappears silently unless wrapped; the verification task logs `run.verification_failed` instead.

## Three live drills, one wrong diagnosis
1. **Drill 1:** approve → flag file flipped `100% → off` in 5 s → verification 90 s later said **still breaching** (error rate 0.29) → incident `failed`. I diagnosed it as flagd missing the change because the atomic rename swapped the file's inode under its watcher, and switched to an in-place write.
2. **Drill 2** (in-place write): incident `resolved`. But `value_after` was empty: too few payment calls in 90 s for a rate (tail sampling keeps only 15 % of healthy spans), and "no reading" had been coded as "cleared". Right answer, wrong reason. My drill script also reported ~880 "error spans after the fix": it forgot the scenario filter and counted test fixture rows with future timestamps.
3. **Checking the data** overturned the diagnosis. In drill 1 the last charge error was at 16:30:38 and the flag write at 16:30:40: flagd *had* reloaded; the fault stopped within seconds. The real cause of the false "failed" was **counter-flush lag**: span-metrics counters flush every 10 s, so the first counter sample after the action still carried errors from just before it (2 of 7 calls). The atomic write was restored.
4. **Fix:** the post-action window starts 15 s after the action; when the rule yields no reading, the exact error-span count since the action decides; the outcome records `basis`.

5. **Drill 3** failed differently: the model said `app_bug`, the §10.1 table maps that to `toggle_flag`, but no flag was named, so validation rejected it at execute. The run was partial (budget breach after the follow-up round), and a breach routed straight to `root_cause`, **skipping correlation**, so the flag-revert override never fired. Fixes: a budget breach now routes through `correlate_changes` (deterministic, free); `propose` never emits `toggle_flag` without a flag (falls through to the next fully specified action, or report only).
6. **Drill 4:** all of the above held; resolved on `error_rate = 0.0` over post-action data.

Lesson: a plausible mechanism ("inodes and file watchers") is not evidence. The timestamps were in the database the whole time.

## Process lesson: silent text replacements
Four edits in this PR "succeeded" but changed nothing: `str.replace` on code that `ruff format` had already rewrapped simply found no match. Symptoms were a verification that never ran, a missing error logger, a test fixture without its short delay, and a stale assertion. Fix: every scripted edit now asserts it matched exactly once, and after a batch of edits every intended change is grepped for. Tests that passed were not evidence the code had changed.

## Interview questions
1. *How do you guarantee an agent cannot act without approval?* Topology (one conditional edge from approval), a re-check in the node, a structure test in CI, policy, allowlisted parameters, and an audit log.
2. *How do you make a config change safe for a process that watches the file?* Atomic temp-file-then-rename.
3. *How do you know the fix worked?* Re-evaluate the triggering rule after a delay, over data after the action only; resolve or fail the incident accordingly.
4. *Why a separate replay backend?* The public demo and the benchmark must never change anything, yet exercise the same code path; the backend is the only difference.
5. *Tell me about a time you misdiagnosed something.* A fix was judged failed; I blamed file-watcher inode semantics and changed the write method. Span timestamps showed the fault had stopped two seconds after the write; the real cause was metrics flush lag in the verification window. Restored the original write, added a grace period and an exact fallback.
6. *What would you audit and why?* Every tool call, decision and action, with actor and outcome, so any change can be explained later.
