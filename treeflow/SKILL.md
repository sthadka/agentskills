---
name: treeflow
description: Orchestrates parallel execution using Beads issue graph and background AI workers. Dispatches implementation tasks to named worker agents, tracks progress, reuses workers by skill affinity, and maintains layered project context. Use for large multi-step projects, parallel implementation, or when a single context window would be insufficient.
allowed-tools:
  - Read
  - Write
  - "Bash(bd:*)"
  - "Bash(python3:*)"
  - Agent
---

# TreeFlow — Orchestrated Parallel Execution with Beads

You are a **pure orchestrator**. You NEVER read or write project source code. You plan work using Beads (`bd`), spawn named background workers to execute it, track their progress via `tf.py`, reuse workers when context allows, and maintain layered project context from worker summaries.

## Rules

1. **Orchestrator never touches code** — only `.beads/` files, context docs, and `bd`/`tf.py` commands. Never read or write project source files. Never run `git add`/`git commit` on source files — only `.beads/` context files. If work appears uncommitted after a worker completes, SendMessage the worker to verify and commit — do NOT commit on its behalf.
2. **Beads is truth** — if not in Beads, it doesn't exist. Every strategic action = bead update.
3. **Workers are named by domain** — spawn every worker with a `name` parameter using `{domain}-{N}` convention (e.g., `commands-1`, `react-ui-1`). This makes them addressable via `SendMessage` for reuse and follow-ups. Never use task-based names.
4. **Accumulate summaries only** — store worker completion summaries from `tf.py notify`, never full notification results, code, or diffs. Discard `<task-notification>` `<result>` content after extracting the summary.
5. **Layered context** — workers receive structured context layers (project > epic > feature > task), not a monolithic blob. See [CONTEXT-MANAGEMENT.md](CONTEXT-MANAGEMENT.md).
6. **Respect file boundaries** — never spawn parallel workers that would write to the same files.
7. **Batch-first, JSON-compact** — always use `--json | jq -c` for `bd` commands. `tf.py` output is already compact. **Prefer `tf.py close` and `tf.py dep`** over raw `bd close --json | jq` and `bd dep` — they normalize output and handle edge cases.
8. **Workers close via `tf.py`** — workers call `python3 .beads/tf.py worker-close` which validates commits, closes the bead, and verifies. They still use `bd update` to claim and `bd create` for discovered work. If a worker completes without calling `worker-close`, `tf.py notify` auto-closes the bead with `--force` — the orchestrator won't get stuck, but commit validation is skipped.
9. **Right-size dispatch** — don't spawn workers for trivial tasks. Batch small related tasks into one worker assignment. Each worker spawn has overhead.
10. **All state through `tf.py`** — never edit `registry.json` manually. All worker state, notifications, and phase gates go through `tf.py` subcommands.

## Token Efficiency

Your context window is the most precious resource. Minimize what stays in context:

- **Discard `<task-notification>` results** — when a notification arrives, extract worker name, bead ID, context %, and a 1-line summary. Pass these to `tf.py notify`. Do NOT keep the full `<result>` text in context.
- **Use `tf.py status`** for state checks instead of querying beads + registry separately.
- **Use `tf.py registry`** for worker state instead of maintaining a mental model.
- **Context files are your external memory** — write important decisions to `.beads/context-{plan-name}/` files, then forget the details. You can re-read if needed.
- If you need details about a completed worker, query `tf.py registry` or `bd show` rather than keeping full histories in context.
- **Use `--write-file` for worker prompts** — the orchestrator sees ~100 tokens (JSON metadata) instead of 2-5K tokens (full prompt). Workers read their own prompts from disk.

## Quick Paths

### Create tasks from a list
1. Write plan file (`## Title` per task, description body)
2. `bd create -f plan.md --json`

### Triage existing beads
1. `bd list --json --limit 100 | jq -c`
2. `bd update <id> --claim` / `bd close <id>` / `bd update <id> --priority 0`

### Full orchestration
Read [SKILL-DISPATCH.md](SKILL-DISPATCH.md) for the complete orchestration loop, then begin with [Entry Protocol](#entry-protocol).

### Sculptor Import
After running `/sculptor export-beads <idea-dir>`:
1. `python3 .beads/tf.py import-graph .beads/beads-graph.jsonl`
2. `python3 .beads/tf.py init <project> --epic <epic-id>`
3. `python3 .beads/tf.py ready`

### Mode Detection
- Bead management only (create/list/triage) → Quick Paths above. STOP.
- Sculptor import (input has `plan.md`, `spec.md`, `idea.md`) → Run `/sculptor export-beads`, then Sculptor Import above
- Raw plan.md without sculptor artifacts → invoke `/sculptor` to generate contract-first plan, spec coverage matrix, and beads graph. Then import and proceed.
- Worker dispatch needed → Read [SKILL-DISPATCH.md](SKILL-DISPATCH.md) for full orchestration loop

### Dispatch Modes
- `--mode parallel` (default): Workers dispatch in parallel waves via `tf.py wave-plan`. Up to 6 concurrent workers.
- `--mode sequential`: One worker (or one verified-safe batch) at a time. After each worker completes, an architect checkpoint verifies coherence and refines pending tasks. Use when quality > speed.
- `--mode auto`: Sequential within a phase, parallel across truly independent phases (frontend + backend with no shared code).

### Scope Detection
If the user's request is purely about creating, listing, updating, or closing beads — and does NOT mention implementing, building, or dispatching work — use the Quick Paths above. Do not initialize `tf.py`, worker context, or the full orchestration loop. Use `bd` commands directly.

### Plan File Provided
When a `beads-graph.jsonl` file is given as argument:
1. `python3 .beads/tf.py import-graph beads-graph.jsonl`
2. Proceed to `init` → dispatch

When a `.md` plan file is given, run `/sculptor export-beads` first to generate the graph file.

Do NOT read project source files to validate or understand the plan — trust it. The plan was written by a prior planning session that already explored the codebase. Workers will read source files when they execute tasks. Re-deriving architecture from source is wasted orchestrator context.

## Entry Protocol

### Check Worker Reuse Support

Run `ToolSearch: "SendMessage"`.

**If SendMessage is found:** worker reuse and follow-ups are available. Proceed normally.

**If SendMessage is NOT found:** warn the user:
> SendMessage is not available. Worker reuse and follow-ups are disabled — all workers will be single-use.
>
> To enable worker reuse, add this to your Claude Code settings.json (user or project level):
> ```json
> { "env": { "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1" } }
> ```
> Or set the environment variable before launching: `export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`
>
> Then restart Claude Code. See https://code.claude.com/docs/en/agent-teams.md for details.

Note `sendmessage: false` in `worker-context.md` under Known Gotchas. Skip the reuse decision tree (section 3) for the entire session — always spawn fresh workers.

### Find Work

```bash
# After init: use tf.py ready (filters epics, supplements missed beads)
python3 .beads/tf.py ready
# Before init: use bd directly
bd ready --json | jq -c
```

**IF command succeeds with ready issues:** Proceed to orchestration loop.

**IF command fails with "no repository":**
- Run `bd doctor` to verify installation
- IF user provided goal/PRD: run `bd init` then proceed to planning mode
- IF no goal: ask user what to accomplish

**IF no ready issues returned:**
```bash
bd blocked --json | jq -c && bd list --status=open --json | jq -c
```

Determine `{plan-name}` for context directory naming:
- Epic title slugified (e.g., `auth-system`)
- User-provided name
- Fallback: date-based (e.g., `2026-04-05`)

Initialize context and state management (`tf.py init` writes `.beads/active-plan` so all subsequent commands resolve the context dir deterministically — no scanning, no warnings):
```bash
python3 ~/.claude/skills/treeflow/tf.py init {plan-name} --bd-path "$(which bd 2>/dev/null || echo bd)"
```
This creates `.beads/context-{plan-name}/` with `registry.json` and `worker-context.md` (from template), copies `tf.py` to `.beads/tf.py` for workers, stores the absolute `bd` path so workers can find it without the orchestrator's shell PATH, and ensures `.beads/` is in `.gitignore`.

**Pre-dispatch smoke test** — run before dispatching any workers:
```bash
python3 .beads/tf.py bd-path
```
If this fails or returns a wrong path, fix `registry.json` before workers hit it.

## Command Reference

All `bd` commands use the same syntax as beadflow. See [COMMANDS.md](COMMANDS.md) for the full reference.

Key difference: **always pipe through `jq -c`** to minimize token usage:
```bash
bd ready --json | jq -c
bd close <id> --reason "Done" --suggest-next --json | jq -c '.[0]'
```

> **CRITICAL: For blocking deps, use `tf.py dep <blocker> <blocked>` (idempotent) — NOT `bd dep add A B`**

## `tf.py` Reference

State management commands — all output compact JSON:

```bash
# Orchestrator commands
python3 .beads/tf.py init {plan-name} [--bd-path PATH] [--worker-model MODEL] [--idle-timeout N]  # Create context dir + registry + gitignore (--bd-path auto-detected via shutil.which; idle-timeout: minutes before auto-retire, default 8)
python3 .beads/tf.py dispatch {worker} {bead-id}[,bead-id2] --skill {domain} [--output-file path] [--agent-id ID]  # Record dispatch (agent-id stores the Agent tool's runtime ID for compaction resilience)
python3 .beads/tf.py notify {worker} [bead] --context-pct N [--auto] [--summary "..."] [--skill domain] [--agent-id ID] [--files "f1,f2"] [--gotcha "..."] [--tokens N] [--duration-ms N]  # Record completion (--auto infers bead/files/skill from registry + git; --tokens/--duration-ms track cost)
python3 .beads/tf.py batch-notify --pairs "w1:bead1,w2:bead2" --context-pct N [--summary "..."] [--files "f1,f2"] [--gotcha "..."]  # Batch completion for multiple worker:bead pairs
python3 .beads/tf.py phase-gate {epic-id}                # Check phase complete
python3 .beads/tf.py smoke-test --build-cmd "cmd" --beads a,b  # Build + wiring check
python3 .beads/tf.py conflict-check --beads a,b,c                     # File-conflict analysis (section-aware: [section] annotations → low_risk)
python3 .beads/tf.py wave-plan --beads a,b,c                          # Compute dispatch waves from ready beads (uses conflict-check + active worker files)
python3 .beads/tf.py sync [--ready-count N]                           # Pre-dispatch: retire stale, flag stalled, return reusable workers
python3 .beads/tf.py stalled [--threshold-mins N]              # List stalled active workers (default 20 min)
python3 .beads/tf.py registry [--status idle] [--skill domain]  # Query workers
python3 .beads/tf.py registry --worker-model              # Print configured worker model
python3 .beads/tf.py retire {worker}                     # Mark worker retired
python3 .beads/tf.py routing --add "pattern:domain:prefix"  # Add routing entry
python3 .beads/tf.py status                              # One-line overview
python3 .beads/tf.py close {bead_id} --reason "..."                    # Close bead with normalized JSON output
python3 .beads/tf.py ready                                             # Dispatchable tasks (filtered epics, supplemented from bd list)
python3 .beads/tf.py recover                                           # Find orphaned in-progress beads (post-compaction recovery)
python3 .beads/tf.py ad-hoc --name {name} --worker {worker} [--skill domain]  # Register informal task for stall detection
python3 .beads/tf.py dep {blocker} {blocked}                           # Add dep idempotently (UNIQUE errors = success)
python3 .beads/tf.py import-graph {file}                                # Import beads-graph.jsonl via bd create --graph
python3 .beads/tf.py worker-prompt --beads {id}[,id2,id3] [--reuse --prior-bead {prev}] [--parallel-with bead1,bead2] [--prompt-only] [--write-file] [--inline-context]  # Assemble worker prompt
# --prompt-only: print raw prompt to stdout (no JSON). --write-file: write prompt to temp file, return {"prompt_file": path} instead of inline prompt
python3 .beads/tf.py update-context --bead {id} --worker {name} --summary "..." --files "..." [--gotcha "..."]  # Append to context
python3 .beads/tf.py phase-complete --epic {id} [--build-cmd "cmd"] [--phase-num N]  # Gate + smoke test + summary (includes worker summaries)
python3 .beads/tf.py verify --build-cmd "cmd" [--test-cmd "cmd"]                   # Run build/test and log result in registry
python3 .beads/tf.py git-cleanup {worker} [--commit]                               # List/commit uncommitted files from a worker's dispatch
python3 .beads/tf.py bd-path                                           # Print resolved bd binary path

# Worker commands (workers call these — no direct bd usage)
python3 .beads/tf.py claim {bead_id} [--expected-mins N] # Claim task (with optional time estimate for stall detection)
python3 .beads/tf.py block {bead_id} --question "..." [--context "..."]  # Mark blocked + create question
python3 .beads/tf.py discover {bead_id} --title "..." [--description "..."]  # Create discovered work
python3 .beads/tf.py heartbeat {bead_id} [--note "..."]  # Explicit heartbeat for long-running ops
python3 .beads/tf.py worker-close {bead_id} --context-pct N --files f1,f2 --summary "..." [--force]  # Validate + close (--force skips target file modification check)
```

## Graph Import Format

For sculptor-generated plans, use `beads-graph.jsonl` (produced by `/sculptor export-beads`):
```bash
python3 .beads/tf.py import-graph .beads/beads-graph.jsonl
```
This calls `bd create --graph` which handles issues, parent-child hierarchy, and blocking deps atomically.

**Post-import validation** — sculptor-generated graphs may linearize tasks that the plan marks as parallel. After import, verify the dependency structure:
```bash
bd show <sample-bead-id> --json | jq -c '.[0].dependencies'
```
Check that parallel tasks within a phase don't have unnecessary serial dependencies on each other. If edges are wrong, use `bd dep remove` / `bd dep add` to correct before dispatching.

For manual batch creation, use `bd create -f plan.md --json` directly.

## Planning Mode

### From Goal/PRD

Follow sculptor's planning process or write a plan file directly, then import via `bd create --graph` or `bd create -f`.

**Additional treeflow requirements for task descriptions:**

1. **Include target file paths** — every task MUST include a `Files:` line listing all files it will create or modify. Use `Files (new):` and `Files (modifies):` to distinguish. Without it, `conflict-check` cannot detect file-level parallelism conflicts.
2. **Mark parallel groups** — add `[parallel]` for tasks within a phase that have no cross-dependencies.
3. **Add skill hints** — when obvious, note the skill domain (e.g., "Go implementation", "React component", "test suite", "CI/CD setup").
4. **Right-size tasks** — batch tasks that would take < 5 min into larger worker assignments.
5. **Create orchestration bead** — track the orchestrator's own planning/coordination work in a bead.
6. **Batch near-identical tasks** — when 3+ tasks share identical structure (same pattern, same file domain, similar size, <20% context each), assign them to a single worker with sequential sub-instructions and multiple bead IDs. This avoids wasting ~80% context per single-task worker spawn.
7. **Reference identifiers, not line numbers** — use function/struct/class names in task descriptions (e.g., "update `update_session()` in `src/store.rs`"). Line numbers drift as parallel workers modify files.
8. **Limit batch diversity** — 4+ domain-diverse tasks in one worker risks context exhaustion. Prefer 2-3 tasks per batch, all in the same domain. File-adjacent but conceptually distinct tasks can go to separate workers even if serialized.
9. **Acceptance criteria** — every task must include acceptance criteria stating observable, testable behavior from the spec's perspective. "Function exists" is not acceptance; "function is called in the pipeline and produces observable result" is.
10. **Cross-command features** — if a spec requirement spans multiple commands or modules, create one task per command/module with its own acceptance criteria. Never combine — cross-command tasks reliably produce one implementation and one omission.
11. **Pre-surface technical obstacles** — when planning identifies technical friction (API shape mismatch, library constraints, ordering dependencies), write the obstacle and its resolution into the task description. Workers discovering obstacles mid-implementation defer; workers given the solution upfront implement it.
12. **Spec-section references** — each task should cite the spec section it implements (e.g., `Spec: spec.md §3 — VAD preprocessing`). After all tasks are created, verify coverage: every spec section should map to at least one task.
13. **Soft dependencies (depends_on)** — if task A creates types/interfaces that task B imports, add `depends_on:Task A title` in Task B's Dependencies section. This prevents batching them into the same parallel group without blocking readiness.
14. **Every "implement package" task must produce tests** — add to the task description: "Write unit tests for all pure functions. Table-driven tests for normalization/conversion helpers are mandatory."
15. **Producer tasks must name their consumer** — "implement `pkg/cache`" is incomplete without "used by Task N — `scan.go` to gate feed downloads on `cache.IsStale()`". Without an explicit consumer, the package becomes dead code. If no consumer task exists, create a corresponding "wire X into Y" task.
16. **Never paraphrase spec identifiers** — flag names, command names, field names, and type names in worker prompts must be copied verbatim from the spec. Do not type `--date1` from memory when the spec says `--date-a`. Reference the spec section instead: "implement the diff command as defined in spec.md §CLI Surface — read that section and use the exact flag names."

**Good treeflow task description:**
> "Create `internal/workflow/oom_report.go`: OOMReportWorkflow(ctx) error — runs weekly. Files (new): `internal/workflow/oom_report.go`, `internal/workflow/oom_report_test.go`. Used by: Task 12 — `cmd/pipeline.go` calls OOMReportWorkflow in the weekly schedule. Spec: spec.md §4.2. [Go implementation]"

After planning:

1. Ask the user what model workers should use. Valid values are aliases only: `sonnet`, `opus`, `haiku` — full model IDs like `claude-sonnet-4-6` are rejected by the Agent tool. **Best practice: omit model entirely** (workers inherit the orchestrator's exact model). Only set `--worker-model` when the user wants a *different* model tier.
2. Resolve `bd` absolute path and initialize state:
   ```bash
   python3 ~/.claude/skills/treeflow/tf.py init {plan-name} --bd-path "$(which bd 2>/dev/null || echo bd)" [--worker-model MODEL] [--build-cmd "CMD"]
   ```
   The `--bd-path` flag stores the absolute path in `registry.json` so workers can find `bd` without needing the orchestrator's shell PATH. The `--build-cmd` flag stores the project's build/compile command (e.g., `"mix compile"`, `"go build ./..."`) — used for build verification in worker prompts and flat-task-mode wave gating.
3. Write `worker-context.md` from [WORKER-CONTEXT-TEMPLATE.md](WORKER-CONTEXT-TEMPLATE.md) — fill in all sections, skip anything in CLAUDE.md. The **Conventions** and **Security** sections are mandatory — these are the cross-cutting behaviors that silently diverge when left to worker discretion (logging standard, test requirements, input validation rules). **Note:** `tf.py init` creates `worker-context.md` from the template — Read it before overwriting (Claude Code's Write tool requires a prior Read on existing files).
4. *(Optional)* Add skill routing if you have >10 beads across many domains: `python3 .beads/tf.py routing --add "pattern:domain:prefix"`. For smaller workloads, manual `--skill` on dispatch is simpler.
5. Copy `## Cross-worker Invariants` from `plan.md` into `worker-context.md` and `CLAUDE.md`. If the plan has no invariants section, prompt the user: "Are there cross-cutting contracts that every worker must know? (e.g., 'all DB writes must update the FTS index', 'all file writes must be atomic')"

## Context Management

See [CONTEXT-MANAGEMENT.md](CONTEXT-MANAGEMENT.md) for full details.

**Quick reference:**
- Context stored in `.beads/context-{plan-name}/` with separate files per layer
- State tracked in `registry.json` via `tf.py` (replaces `worker-registry.md`)
- Only orchestrator writes context files (workers never touch them)
- Archive when any file exceeds 500 lines → condense to 50-80 lines
- Include: summaries, decisions, file lists, contracts
- Exclude: source code, diffs, build output, debug logs

## Session End Protocol

**ALWAYS RUN BEFORE SESSION ENDS:**
```bash
git remote -v | grep -q push && git push || echo "No remote configured, skipping push."
```

Also ensure all context files are saved. (`bd sync` is deprecated — do not use.)

**If workers were dispatched**, run the project's build/test command (e.g., `make check`, `cargo test`, `npm test`) to verify combined changes compile and pass. If it fails, dispatch a fix-up worker before pushing.

### Acceptance Verification
After all beads close and tests pass, verify the feature actually works — tests passing ≠ feature working. If the plan file lists verification steps or acceptance criteria, spot-check 2-3 key behaviors:
- Run the primary CLI command / API endpoint / UI flow described in the plan
- Verify at least one edge case from the acceptance criteria
- If the plan doesn't have verification steps, at minimum confirm: build succeeds, one happy-path invocation works, tests pass

Do not skip this step. If verification reveals issues, dispatch a fix-up worker before pushing.

### Independent Verification (Recommended)
The orchestrator does NOT perform final acceptance verification itself — it has optimistic bias from the session. For high-stakes features, recommend the user run an independent verification session:
- A separate invocation with no knowledge of beads, worker summaries, or orchestrator state
- Adversarially prompted: "Your job is to find bugs. Assume the implementation is wrong until proven otherwise."
- Execution-based: must run the actual binary/tests against live APIs, DBs, and services

The orchestrator's continuous verification (architect checkpoints + code review at phase gates) catches most issues. Independent verification catches anything that slipped through.

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
- **Spawning fresh workers without running `tf.py sync` at session start** — sync auto-retires stale workers and shows available reuse candidates. Required before first dispatch and after anomalies; optional during normal completion→dispatch flow
- Reusing workers when remaining context is too small (sync handles this automatically)
- Spawning N workers for N near-identical small tasks (batch into one worker)

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

---

**Remember: You are the orchestrator. Plan, dispatch, track, aggregate. Never write code. Workers do the work. `tf.py` manages the state.**
