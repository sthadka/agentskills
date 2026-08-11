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

You plan AND implement work directly using **Beads** (`bd`) as the system of record. For `[parallel]` task groups, dispatch sub-agents. For everything else, do the work yourself.

## Rules

1. **Beads is the state** — after compaction or restart, `bd ready` + `bd list --status=in_progress` recovers everything.
2. **Quality at close** — always close via `python3 .beads/bf.py close` (validates uncommitted changes, dead-code markers, commit messages).
3. **Batch-first** — `bd create -f` for multiple issues. Chain with `&&`. Use `--json | jq -c`.
4. **Phase gates** — `python3 .beads/bf.py smoke-test` between phases.

## Entry Protocol

```bash
python3 ~/.claude/skills/beadflow/bf.py init --bd-path "$(which bd 2>/dev/null || echo bd)"
python3 .beads/bf.py ready
```

**Ready work exists →** [Execution Loop](SKILL-EXECUTION.md)
**No ready work →** `bd blocked --json | jq -c && bd list --status=open --json | jq -c` to assess state
**No beads repository →** `bd init`, then plan
**Sculptor import →** [SCULPTOR-IMPORT.md](SCULPTOR-IMPORT.md)

**Scope check:** If the request is only about creating/listing/closing beads — not implementing — use `bd` commands directly. Skip bf.py and the execution loop.

## Planning Mode

### From Goal/PRD

1. **Analyze** the goal. Identify epic, features, tasks.
2. **Write** `.beads/plan.md` in [bd create -f format](PLAN-FORMAT.md).
3. **Create:** `bd create -f .beads/plan.md --json | jq -c`
4. **Wire deps:** `python3 .beads/bf.py dep <blocker> <blocked>` (idempotent). For hierarchy: `bd dep add <child> <parent> -t parent-child`
5. **Validate:** `python3 .beads/bf.py ready` — should show at least one task.

> **NEVER use `bd dep add A B` for blocking deps** — argument order is reversed. Use `bd dep <blocker> --blocks <blocked>`.

**Planning principles:**
- Each task = 1 focused session max. Decompose anything larger.
- Include `Files:` lines — enables `bf.py conflict-check` for parallel dispatch.
- Mark `[parallel]` for independent tasks, `[TDD]` for data-heavy tasks.
- Always include a Setup task as the first task.

### Sculptor Import

```bash
python3 .beads/bf.py import-graph .beads/beads-graph.jsonl
```
Or follow [SCULPTOR-IMPORT.md](SCULPTOR-IMPORT.md) for manual conversion.

## Execution Loop

See [SKILL-EXECUTION.md](SKILL-EXECUTION.md). Summary:

1. `python3 .beads/bf.py ready` — find work
2. `bd update <id> --status in_progress --json | jq -c` — claim
3. Execute the task
4. `git add <files> && git commit -m "..."` — commit
5. `python3 .beads/bf.py close <id> --files "f1,f2" --summary "AC: pass. ..."` — validate + close
6. Loop

**Parallel:** `bf.py conflict-check --beads id1,id2` → dispatch `safe` beads to sub-agents via Agent tool.

**Phase transitions:** `bf.py smoke-test --build-cmd "cmd" --beads id1,id2` — proceed only on `"pass"`.

## Compaction Recovery

```bash
python3 .beads/bf.py ready && bd list --status=in_progress --json | jq -c
```
Beads IS the state. Resume in-progress beads or pick up ready ones.

## Reference

**bf.py commands:** See [COMMANDS.md](COMMANDS.md)
**bd commands:** See [COMMANDS.md](COMMANDS.md)
**Plan format:** See [PLAN-FORMAT.md](PLAN-FORMAT.md)

## Session End

```bash
git pull --rebase && git push
```
Work is NOT complete until `git push` succeeds.

## Anti-Patterns

- Closing via `bd close` instead of `bf.py close` (skips quality checks)
- Creating issues without executing them (plan paralysis)
- Working without claiming the bead first
- Creating mega-tasks that span sessions
- Using `bd dep add A B` for blocking deps (reversed argument order)
- Implementing `[parallel]` tasks sequentially when sub-agents could run them

---

**Beads is the state. Close via bf.py. Build between phases. Push before done.**
