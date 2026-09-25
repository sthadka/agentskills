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

### Sync on Resume

When resuming a session (workers may already exist) or after any anomaly, run `python3 .beads/tf.py sync` before dispatching — it auto-retires stale workers and surfaces reuse candidates. A brand-new plan with no prior workers/registry skips this: a first-ever dispatch needs no sync.

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

## `tf.py` Core Commands

The happy-path commands the core loop uses (all output compact JSON):

```bash
python3 .beads/tf.py init {plan-name} [--bd-path PATH] [--worker-model MODEL] [--idle-timeout N]  # Create context dir + registry + gitignore
python3 .beads/tf.py ready                                             # Dispatchable tasks (filtered epics, supplemented from bd list)
python3 .beads/tf.py dispatch {worker} {bead-id}[,bead-id2] --skill {domain} [--output-file path] [--agent-id ID]  # Record dispatch
python3 .beads/tf.py notify {worker} [bead] --context-pct N [--auto] [--summary "..."]  # Record completion
python3 .beads/tf.py phase-complete --epic {id} [--build-cmd "cmd"] [--phase-num N]  # Gate + smoke test + summary
python3 .beads/tf.py worker-close {bead_id} --context-pct N --files f1,f2 --summary "..." [--force]  # Worker validates + closes
```

Full command reference: [COMMANDS.md](COMMANDS.md).

## Graph Import Format

For sculptor-generated plans, use `beads-graph.jsonl` (produced by `/sculptor export-beads`):
```bash
python3 .beads/tf.py import-graph .beads/beads-graph.jsonl
```
This calls `bd create --graph` which handles issues, parent-child hierarchy, and blocking deps atomically.

After import, always validate and correct the graph before dispatching. Sculptor emits a conservative serial chain whenever the plan's tasks lack `[parallel]` markers, so the graph usually needs edges pruned:

1. Run the detector: `python3 .beads/tf.py validate-graph --plan plan.md`. It flags over-linearization (serial chains of ≥3 beads where each link has exactly one blocker and one dependent) and, with `--plan`, reports `[parallel]` markers the graph doesn't reflect.
2. Break each false edge: `python3 .beads/tf.py dep <blocker> <blocked> --remove` (emits JSON; `bd dep remove` does not).
3. Add any genuinely-required edge: `python3 .beads/tf.py dep <blocker> <blocked>` (idempotent). Keep real ordering (e.g. `go mod init` before `go build`); only remove edges between independent tasks.

For manual batch creation, use `bd create -f plan.md --json` directly.

## Planning Mode

### From Goal/PRD

Follow sculptor's planning process or write a plan file directly, then import via `bd create --graph` or `bd create -f`.

**Additional treeflow requirements for task descriptions (rules that fire on every plan):**

1. **Include target file paths** — every task MUST include a `Files:` line listing all files it will create or modify. Use `Files (new):` and `Files (modifies):` to distinguish. Without it, `conflict-check` cannot detect file-level parallelism conflicts.
2. **Mark parallel groups** — add `[parallel]` for tasks within a phase that have no cross-dependencies.
9. **Acceptance criteria** — every task must include acceptance criteria stating observable, testable behavior from the spec's perspective. "Function exists" is not acceptance; "function is called in the pipeline and produces observable result" is.
12. **Spec-section references** — each task should cite the spec section it implements (e.g., `Spec: spec.md §3 — VAD preprocessing`). After all tasks are created, verify coverage: every spec section should map to at least one task.

Full planning rules (batching, soft-deps, verbatim identifiers, consumer naming, etc.): [PLANNING.md](PLANNING.md).

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
- Archive by **byte size** (not line count): run `tf.py archive-context` when a context file exceeds ~48 KB — completion summaries are few but huge, so a line trigger never fires while the file balloons and gets inlined into every later worker prompt
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

## Troubleshooting

Error handling and the full anti-pattern list: [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

---

**Remember: You are the orchestrator. Plan, dispatch, track, aggregate. Never write code. Workers do the work. `tf.py` manages the state.**
