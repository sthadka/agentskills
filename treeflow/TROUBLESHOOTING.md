# Treeflow — Error Handling & Anti-Patterns

## Error Handling

**`bd` command fails with "not found":** Run `bd doctor`, inform user.

**"no repository found":** Run `bd init` if user wants to start tracking.

**Worker spawn fails:** Retry once. If still fails, notify user.

**Duplicate dispatch (same worker name used twice):** The second spawn creates a new agent — the first is orphaned. Always check `tf.py registry` before dispatching to avoid name collisions.

**SendMessage to dead worker:** If the agent no longer exists, spawn fresh.

**Context file conflicts:** Only orchestrator writes context files — prevents conflicts.

**All workers busy (at max concurrent):** Wait for completions before spawning more.

**Dependency graph has cycles:** Detect via `bd graph --all`, report to user.

## Anti-Patterns

**Orchestrator behavior:**
- Reading/writing project source code (delegate to workers always)
- Running `git add`/`git commit` on source files (only `.beads/` files)
- Running `git stash -u` or `git stash --include-untracked` — stashes `.beads/context-*/` files, breaking all state tracking
- Accumulating full `<task-notification>` results in context (extract summary, discard rest)
- Editing `registry.json` manually (always use `tf.py`)
- Spawning workers for trivial tasks (batch them, or close directly with `tf.py close` for pure verification tasks like version checks or infrastructure confirmation)

**Worker management:**
- Spawning workers without `name` parameter (can't reuse unnamed workers)
- Spawning more workers than independent ready tasks
- Killing workers — let them complete or self-report
- **Resuming a session without running `tf.py sync` first** — see [Entry Protocol → Sync on Resume](SKILL.md#sync-on-resume).
- Reusing workers when remaining context is too small (sync handles this automatically)
- Spawning N workers for N near-identical small tasks (batch into one worker)
- **Routing a discovered fix worker-to-worker via `SendMessage` instead of through the orchestrator** — if a worker finds a bug and messages a peer to fix it while the orchestrator independently files+dispatches the same fix, both converge on duplicate work. Prefer reporting discovered fixes back to the orchestrator, which owns dedup and dispatch.

**Planning:**
- Tasks without target file paths in descriptions
- Ignoring file conflicts when parallelizing
- Not marking `[parallel]` groups during planning

**Commands:**
- Using `--json` without `| jq -c` for `bd` commands (wastes tokens)
- Using `bd dep add A B` for blocking deps (reversed argument order) — use `tf.py dep A B` instead (idempotent)
- Making separate Bash calls for related operations (chain with `&&`)
- Dispatching integration before `tf.py phase-gate` returns `pass: true`
- Validating `bd_path` with `Path.exists()` or `shutil.which()` — macOS sandbox blocks `stat()` on agent subprocess paths even when `execve` works. Trust the stored path.
- Skipping the pre-dispatch smoke test (`tf.py bd-path`) — catch infrastructure bugs before workers hit them
- Using bare `bd list --json` without `--limit 500` — bd defaults to 50 results, silently truncating large graphs. `tf.py` handles this internally; only matters when calling `bd` directly.
