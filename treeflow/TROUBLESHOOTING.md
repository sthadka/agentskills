# Treeflow — Error Handling & Practices

## Error Handling

**`bd` command fails with "not found":** Run `bd doctor`, inform user.

**"no repository found":** Run `bd init` if user wants to start tracking.

**Worker spawn fails:** Retry once. If still fails, notify user.

**Duplicate dispatch (same worker name used twice):** The second spawn creates a new agent — the first is orphaned. Always check `tf.py registry` before dispatching to avoid name collisions.

**SendMessage to dead worker:** If the agent no longer exists, spawn fresh.

**Context file conflicts:** Only orchestrator writes context files — prevents conflicts.

**All workers busy (at max concurrent):** Wait for completions before spawning more.

**Dependency graph has cycles:** Detect via `bd graph --all`, report to user.

## Do This Instead

Positive targets for the behaviors that most often drift. Hard guardrails are stated as the target to hit, not the thing to avoid.

**Orchestrator behavior:**
- Delegate all source reading and writing to workers; you touch only `.beads/` files and context docs.
- Commit only `.beads/` files; leave source commits to the worker that made them.
- Stash with `git stash` (tracked only) or `git stash push <files>` — the `-u`/`--include-untracked` forms stash `.beads/context-*/` and break state tracking.
- Extract the summary, bead ID, and context% from each notification and discard the rest.
- Change worker state only through `tf.py`; treat `registry.json` as read-only.
- Close pure-verification tasks directly with `tf.py close`; reserve worker spawns for real implementation.

**Worker management:**
- Spawn every worker with a `{domain}-{N}` name so it stays reusable.
- Keep at most one worker per independent ready task; batch N near-identical small tasks into one worker.
- Let workers complete or self-report; a stuck worker is retired by `tf.py sync`, never killed.
- Sync on resume (see [Entry Protocol → Sync on Resume](SKILL.md#sync-on-resume)).
- Reuse a worker only while its remaining context is ample — `tf.py sync` flags when it isn't.
- Report discovered fixes back to the orchestrator, which owns dedup and dispatch, rather than routing worker-to-worker via `SendMessage` (two independent dispatches converge on duplicate work).

**Planning:**
- Give every task a `Files:` line and mark `[parallel]` groups; resolve file conflicts before parallelizing.

**Commands:**
- Pipe every `bd --json` through `| jq -c`.
- Use `tf.py dep A B` for blocking deps — idempotent, with the correct argument order.
- Chain related bash operations with `&&`.
- Gate integration on `tf.py phase-gate` returning `pass: true`.
- Trust the stored `bd_path` — the macOS sandbox blocks `stat()` on agent subprocess paths even when `execve` works, so `Path.exists()`/`shutil.which()` give false negatives.
- Run the pre-dispatch smoke test (`tf.py bd-path`) so infrastructure bugs surface before workers hit them.
- Call `bd list` with `--limit 500` when using `bd` directly (it defaults to 50 and silently truncates); `tf.py` handles this internally.
