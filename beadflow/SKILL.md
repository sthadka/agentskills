---
name: beadflow
description: Autonomous task planning and execution using Beads (bd). Use when working on multi-step projects, breaking down PRDs or specs into tasks, managing complex implementations, or tracking progress on development work. Creates and manages issues in the Beads issue graph.
allowed-tools:
  - Read
  - Write
  - Edit
  - "Bash(bd:*)"
  - "Bash(python3:*)"
  - "Bash(git:*)"
  - "Bash(grep:*)"
  - Agent
---

# BeadFlow — Autonomous Planning & Execution with Beads

You are an autonomous agent using **Beads** (`bd`) as the system of record. You both plan AND implement work directly. For `[parallel]` task groups, you dispatch sub-agents. For everything else, you do the work yourself.

## Rules

1. **Beads is the state** — if not in Beads, it doesn't exist. After compaction or restart, `bd ready` and `bd list --status=in_progress` are all you need to recover.
2. **Always update** — every action = bead update. Done = close via `bf.py close`. Blocked = mark blocked.
3. **Small units** — tasks must be completable in one session. Decompose anything larger.
4. **Quality at close** — always close via `python3 .beads/bf.py close` which validates uncommitted changes, dead-code markers, and commit messages before closing.
5. **Batch-first** — prefer `bd create -f` for multiple issues. Chain commands with `&&`. Use `--json | jq -c` for compact output.
6. **Durable issues** — write so another agent can resume without conversation context.
7. **Phase gates** — run `python3 .beads/bf.py smoke-test` between phases.

## Entry Protocol

### 1. Initialize bf.py

```bash
python3 ~/.claude/skills/beadflow/bf.py init --bd-path "$(which bd 2>/dev/null || echo bd)"
```
This copies `bf.py` to `.beads/` and stores the `bd` path. Idempotent — safe to re-run.

### 2. Detect Mode

```bash
python3 .beads/bf.py ready
```

**Mode A — Ready work exists:** proceed to [Execution Loop](SKILL-EXECUTION.md).

**Mode B — No ready work:**
```bash
bd blocked --json | jq -c && bd list --status=open --json | jq -c
```
- Blocked issues → analyze and resolve blockers
- In-progress issues → resume or close stale items
- No open issues → work complete, report to user
- Empty state → proceed to Planning Mode

**Mode C — No beads repository:**
- `bd init` if user wants to start tracking
- Then proceed to Planning Mode

**Mode D — Sculptor import:**
If input contains `plan.md` + `spec.md` + `idea.md`, follow [SCULPTOR-IMPORT.md](SCULPTOR-IMPORT.md).

### 3. Scope Detection

If the user's request is purely about creating, listing, updating, or closing beads — not implementing anything — use `bd` commands directly. Don't initialize `bf.py` or enter the execution loop.

## Planning Mode

### From Goal/PRD

#### 1. Analyze the Goal
Read and understand the PRD/goal. Identify the epic, features, and tasks.

#### 2. Write the Plan File
Use the Write tool to create `.beads/plan.md` in [bd create -f format](PLAN-FORMAT.md).

**Planning principles:**
- Epic = "Goal: X", describes end state
- Features = user-facing capabilities
- Tasks = concrete, actionable work (specific files, endpoints, functions)
- Name by WHAT (deliverable), not WHEN (timeline)
- Each task = 1 focused session max
- **Always include a Setup task** as the first task
- **Mark parallel groups** — add `[parallel]` for tasks with no cross-dependencies
- **Flag TDD candidates** — add `[TDD]` for data-heavy or edge-case-heavy tasks
- **Include file paths** — every task should include a `Files:` line listing target files. Enables `bf.py conflict-check` for parallel dispatch.

**Good task description:**
> "Create `internal/workflow/oom_report.go`: OOMReportWorkflow(ctx) error — runs weekly. Files (new): `internal/workflow/oom_report.go`, `internal/workflow/oom_report_test.go`. AC: function called in weekly schedule, produces report output."

**Bad task descriptions:** "Implement backend", "Handle auth", "Do the database stuff"

#### 3. Batch Create
```bash
bd create -f .beads/plan.md --json | jq -c
```

#### 4. Add Dependencies
```bash
# Blocking deps (blocker first, reads naturally):
bd dep <task-a> --blocks <task-b> && bd dep <task-b> --blocks <task-c>

# Or use bf.py for idempotent deps:
python3 .beads/bf.py dep <blocker> <blocked>

# Parent-child hierarchy:
bd dep add <child> <parent> -t parent-child
```

> **NEVER use `bd dep add A B` for blocking deps** — argument order is reversed from intuition.

#### 5. Validate
```bash
python3 .beads/bf.py ready
```
Should show at least one actionable task.

### Sculptor Import

If input is a sculptor session directory, follow [SCULPTOR-IMPORT.md](SCULPTOR-IMPORT.md). For graph files:
```bash
python3 .beads/bf.py import-graph .beads/beads-graph.jsonl
```

## Execution Loop

See [SKILL-EXECUTION.md](SKILL-EXECUTION.md) for the full execution loop. Summary:

1. **Find work:** `python3 .beads/bf.py ready`
2. **Claim:** `bd update <id> --status in_progress --json | jq -c`
3. **Execute:** do exactly what the issue describes
4. **Verify:** `python3 .beads/bf.py verify --files "f1,f2"` (optional mid-task check)
5. **Commit:** `git add <files> && git commit -m "..."`
6. **Close:** `python3 .beads/bf.py close <id> --files "f1,f2" --summary "AC: all pass. ..."`
7. **Next:** loop back to step 1

### Parallel Execution

When multiple ready issues are independent (marked `[parallel]`):

1. Run conflict-check: `python3 .beads/bf.py conflict-check --beads id1,id2,id3`
2. Dispatch `safe` beads to sub-agents via the `Agent` tool with `run_in_background: true`
3. Process completions as sub-agents finish
4. Close each via `bf.py close`

### Phase Transitions

Between phases, run smoke-test:
```bash
python3 .beads/bf.py smoke-test --build-cmd "npm test" --beads id1,id2
```
Only proceed to the next phase if `build: "pass"`.

## Compaction Recovery

After context compression or session restart, state reconstruction is one command:

```bash
python3 .beads/bf.py ready && bd list --status=in_progress --json | jq -c
```

Beads IS the state. No registry, no session log, no reconstruction protocol. If a bead is `in_progress`, resume or close it. If it's `open` and unblocked, it's ready for work.

## Type Selection

| Type | Use When | Priority Default |
|------|----------|------------------|
| `epic` | Top-level goal, major deliverable | P0 |
| `feature` | User-facing capability, delivers user value | P1 |
| `task` | Implementation work, concrete action | P2 |
| `bug` | Defect, something broken | P1 |
| `chore` | Refactor, cleanup, no user-facing change | P3 |

## Priority Scale

- `0` (P0) — Blocks everything, drop all other work
- `1` (P1) — Important features, major bugs
- `2` (P2) — Standard work
- `3` (P3) — Nice-to-have
- `4` (P4) — Future, not planned

## bf.py Reference

Quality layer commands — all output compact JSON:

```bash
# Setup
python3 .beads/bf.py init --bd-path "$(which bd)"

# Find work (filtered ready — no epics, supplements capped results)
python3 .beads/bf.py ready

# Quality checks
python3 .beads/bf.py verify --files "f1,f2"          # Mid-task validation
python3 .beads/bf.py close <id> --files "f1,f2" --summary "AC: pass. ..."  # Validate + close
python3 .beads/bf.py close <id> --force               # Skip validation checks
python3 .beads/bf.py smoke-test --build-cmd "cmd" --beads a,b  # Build + wiring check

# Parallelism
python3 .beads/bf.py conflict-check --beads a,b,c    # File-conflict analysis

# Dependencies
python3 .beads/bf.py dep <blocker> <blocked>          # Idempotent dep addition

# Import
python3 .beads/bf.py import-graph graph.jsonl         # Import sculptor graph
```

**Close validation checks:**
1. Uncommitted changes in working tree (error — blocks close)
2. Dead-code markers: TODO, FIXME, HACK, `@ts-ignore`, etc. (warning)
3. Commit message contains `Task N:` prefix (error — blocks close)
4. Summary missing `AC:` status (warning)

## Command Reference

See [COMMANDS.md](COMMANDS.md) for the full `bd` command reference.

## Session End Protocol

```bash
git pull --rebase && git push
```

Work is NOT complete until `git push` succeeds. Never say "ready to push when you are" — push it yourself.

## Error Handling

- **`bd` not found:** run `bd doctor`, inform user
- **No repository:** run `bd init` if user agrees
- **`bd create -f` format error:** validate with `--dry-run --json`
- **Dependency cycles:** detect via `bd graph --all`, ask user which dep to remove
- **Sub-agent failure:** check target files exist, retry with more explicit prompt

## Anti-Patterns

- Creating issues without executing them (plan paralysis)
- Working without claiming issue first
- Closing issues that aren't done
- Closing via `bd close` instead of `bf.py close` (skips quality checks)
- Creating mega-tasks that span sessions (decompose first)
- Using `bd dep add A B` for blocking deps (reversed argument order)
- Making separate Bash calls for chainable operations
- Implementing `[parallel]` tasks sequentially when sub-agents could run them
- Defining types without checking for existing ones first
- Writing call sites from memory without verifying signatures
- Trusting LSP diagnostics on recently-edited files over the build tool

---

**Remember: Beads is the state. Close via bf.py. Build between phases. Push before done.**
