# TreeFlow Feedback — ACS Customer Health Intelligence Build

Session: 2026-06-09. Epic: 50 beads (49 tasks + 1 epic). All closed.

## What went well

### Phased dispatch worked cleanly
Four phases (setup → core → CLI/skill → integration) kept the dependency graph sane. No wasted work from dispatching tasks whose prerequisites weren't done yet.

### Batching small tasks was the right call
Phase 0 had 8 trivially small setup tasks. Batching them into a single worker saved 7 spawn cycles (~20s overhead each). The worker completed all 8 in 3 minutes — spawning 8 separate workers would have been absurd.

### Parallel compute engines were the sweet spot
The 5 compute engines (health, demand, support, hygiene, engineering) write to completely separate files. Dispatching them across 2 workers (health+demand on one, support+hygiene+engineering on the other) gave genuine parallelism with zero merge conflicts. Wall-clock time for all 5: ~7 minutes. Serial would have been ~25 minutes.

### Worker context template saved time
The pre-populated `worker-context.md` meant every worker had the cross-cutting invariants (schema-qualified SQL, customer_map as truth, stateless compute engines) without me repeating them in each prompt. No worker violated these invariants.

### tf.py ready vs bd ready
`tf.py ready` catching the `capped: true` case was valuable. `bd ready` silently capped at some limit; `tf.py ready` supplemented from `bd list --status=open` and returned all 49 ready tasks. Without this, I'd have missed tasks.

## What could be better

### Workers don't reliably call worker-close
This was the most consistent issue. Out of 9 workers, only 3 (setup-1, skill-1, and templates-snap-1 on reuse) properly called `tf.py worker-close` to close their beads. The other 6 completed their implementation work correctly but left beads in `in_progress`. The orchestrator had to `bd close --force` each one.

**Root cause**: The worker prompt includes `worker-close` instructions, but workers with many sub-tasks seem to lose track of the close step for individual beads, especially when they batch commits. The `worker-prompt` template says "claim, implement, commit, close" per task, but workers optimize by committing multiple tasks together and then forget the per-task close calls.

**Suggestion**: Either:
1. Make `worker-close` the only way to mark work done (block worker completion until all assigned beads are closed)
2. Add a "did you close all beads?" check to the worker's final step
3. Have the orchestrator auto-close beads when a worker notification arrives and the work is verified done (current workaround, but shouldn't be necessary)

### bd close blocked by outgoing dependencies
`bd close` refused to close beads that had `blocks` relationships to still-open tasks. For example, closing `compute/support.py` failed because integration test beads depended on it. This is backwards — the dependency means the *integration test* can't start until support.py is done, not that support.py can't be closed.

Required `--force` on every close. Every single one. This made the `blocks` relationship effectively unusable without `--force`, which defeats the purpose of having dependency tracking.

**Suggestion**: `bd close` should only block when `blocked_by` tasks are still open (can't close X if X depends on unfinished Y). It should never block based on `blocks` (X being done doesn't prevent closing X just because Z depends on X).

### No formal dependency tracking in the plan
All 49 tasks had zero `blocked_by` constraints despite clear sequential dependencies (can't install deps before `uv init`, can't run compute engines before db.py exists). The plan was created without deps, so `bd ready` returned everything as ready simultaneously.

This worked because the orchestrator manually sequenced the phases, but it means the dependency graph was entirely in my head, not in beads. If I'd been interrupted or context-compressed, I'd have lost the sequencing logic.

**Suggestion**: During planning, require explicit `blocked_by` relationships for any task that imports from or depends on another task's output. `tf.py validate-plan` should warn when tasks reference files created by other tasks but have no dependency.

### Worker reuse had a narrow window
`tf.py sync` auto-retires workers idle >4 min, and workers became non-addressable within 2-5 min of completing. In practice, by the time I processed one notification, ran sync, and built a reuse prompt, the window had often closed. Only 1 out of 9 workers was successfully reused (templates-snap-1 for the demand template).

The reuse worked well when it worked — the SendMessage resumed the agent with full context and it completed a follow-up task efficiently. But the timing constraint makes reuse unreliable for orchestrators that process multiple notifications sequentially.

**Suggestion**: Consider extending the addressable window to 8-10 min, or provide a way to "hold" a worker addressable while the orchestrator is processing notifications.

### Conflict detection misses implicit file collisions
`conflict-check` only detects conflicts from explicit `Files:` lines in bead descriptions. But many tasks implicitly touch the same file — all 13 CLI commands modify `cli.py`, yet `conflict-check` reported no conflicts because the descriptions didn't include `Files: src/acs_health/cli.py`.

The orchestrator had to manually recognize that all CLI command tasks touch `cli.py` and serialize them into one worker. If I'd dispatched them in parallel, they'd have clobbered each other.

**Suggestion**: Infer likely files from task descriptions. If a task says "add X command to cli.py", infer `cli.py` as a target. Or require every task to have a `Files:` line (the validator warns but doesn't enforce).

## Metrics

| Metric | Value |
|--------|-------|
| Total beads | 50 (49 tasks + 1 epic) |
| Beads closed | 50/50 |
| Workers spawned | 9 (setup-1, core-1, data-pipeline-1, compute-health-demand-1, compute-ops-1, templates-snap-1, cli-commands-1, skill-1, integration-1) |
| Workers reused | 1 (templates-snap-1 for demand template) |
| Worker:task ratio | 0.18 (9 workers / 50 beads) |
| Phases | 4 (setup → core impl → CLI/skill → integration) |
| Max concurrent workers | 5 (Phase 2) |
| Total test count | 292 |
| Beads requiring manual close | ~30 (orchestrator had to `bd close --force`) |
| Total commits | 34 |
| Wall-clock time (approx) | ~35 min |

## Worker utilization

| Worker | Beads | Context% | Duration | Reused | Closed own beads? |
|--------|-------|----------|----------|--------|-------------------|
| setup-1 | 8 | 35% | 3.3 min | No | Yes |
| core-1 | 8 | 55% | 5.7 min | No (retired idle) | No |
| data-pipeline-1 | 13 | 80% | 7.8 min | No (retired idle) | No |
| compute-health-demand-1 | 8 | 70% | 5.0 min | No (retired idle) | No |
| compute-ops-1 | 5 | 75% | 7.1 min | No (retired idle) | No |
| templates-snap-1 | 7+1 | 78% | 7.2+1.5 min | Yes (demand template) | Partial |
| cli-commands-1 | 13 | 75% | 4.5 min | No | No |
| skill-1 | 8 | 78% | 7.1 min | No | Yes |
| integration-1 | 8 | 72% | 7.4 min | No | Partial |

## Recommendations for treeflow skill improvements

1. **Fix worker-close reliability** — this is the #1 pain point. Every session will hit this.
2. **Fix bd close semantics** — `blocks` shouldn't prevent closing the blocker.
3. **Enforce Files: in task descriptions** — make `validate-plan` error (not warn) on missing file targets.
4. **Add implicit file inference to conflict-check** — parse "add X to cli.py" → `cli.py`.
5. **Consider auto-closing beads on worker notification** — if the orchestrator confirms work is done, close without requiring the worker to have called worker-close.
6. **Extend worker addressable window** — 4 min is too tight for orchestrators processing multiple notifications.
